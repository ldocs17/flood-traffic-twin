// Slice 5 replay frontend. Vanilla JS + MapLibre GL JS (CDN, no build step
// per PROJECT_PLAN.md Slice 5 — "static HTML/JS page, no bundler needed").
// Talks only to this same-origin FastAPI backend (floodtwin.api.app); no
// "run from the browser" here — replay of an already-completed run only
// (Slice 6 scope).

const API = "/api";

const state = {
  runId: null,
  config: null,
  edgeStates: { marks_s: [], frames: [] },
  floodMeta: { available: false, frames: [] },
  fcd: { stride_s: 5, frames: [] },
  networkLoaded: false,
  floodLayerIds: [],
  playing: false,
  playTimer: null,
  currentTime: 0,
};

const el = (id) => document.getElementById(id);

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function setStatus(msg) {
  el("status").textContent = msg;
}

// ---------------------------------------------------------------------
// Map setup
// ---------------------------------------------------------------------

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      basemap: {
        type: "raster",
        tiles: [
          "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
          "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
          "https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        ],
        tileSize: 256,
        attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
      },
    },
    layers: [{ id: "basemap", type: "raster", source: "basemap" }],
  },
  // District net centroid (IMPLEMENTATION_CONTEXT.md georeferencing / net origBoundary).
  center: [-76.29, 36.9],
  zoom: 14,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");

window.__map = map; // exposed for debugging via devtools console
map.on("error", (e) => console.error("maplibre error", e && e.error));
map.on("load", async () => {
  try {
    await loadNetworkLayer();
    await loadRunList();
  } catch (e) {
    console.error("init failed", e);
    setStatus(`Init failed: ${e.message}`);
  }
});

// ---------------------------------------------------------------------
// Network (shared across runs; loaded once)
// ---------------------------------------------------------------------

async function loadNetworkLayer() {
  setStatus("Loading road network...");
  const geojson = await getJSON(`${API}/network`);
  map.addSource("network", {
    type: "geojson",
    data: geojson,
    promoteId: "id",
  });
  map.addLayer({
    id: "network-line",
    type: "line",
    source: "network",
    paint: {
      "line-color": ["coalesce", ["feature-state", "color"], "#7a7a8a"],
      "line-width": [
        "case",
        ["==", ["coalesce", ["feature-state", "color"], ""], "#e74c3c"],
        3.5,
        1.6,
      ],
      "line-opacity": 0.9,
    },
  });

  // Fit the view to the network bounds once.
  const bounds = new maplibregl.LngLatBounds();
  for (const f of geojson.features) {
    for (const c of f.geometry.coordinates) bounds.extend(c);
  }
  if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 40, duration: 0 });

  state.networkLoaded = true;
  setStatus(`Network loaded: ${geojson.features.length} edges.`);
}

function clearEdgeColors() {
  map.removeFeatureState({ source: "network" });
}

function applyEdgeColors(edgesObj) {
  clearEdgeColors();
  for (const [edgeId, s] of Object.entries(edgesObj)) {
    map.setFeatureState({ source: "network", id: edgeId }, { color: s.color });
  }
}

// ---------------------------------------------------------------------
// Run list + selection
// ---------------------------------------------------------------------

async function loadRunList() {
  const runs = await getJSON(`${API}/runs`);
  const select = el("runSelect");
  select.innerHTML = "";
  if (runs.length === 0) {
    select.innerHTML = "<option>No runs found under runs/</option>";
    setStatus("No run artifacts found. Generate one with floodtwin.sim.runner first.");
    return;
  }
  for (const r of runs) {
    const opt = document.createElement("option");
    opt.value = r.id;
    const flags = [];
    if (r.has_flood_raster) flags.push("flood");
    if (r.run_valid === false) flags.push("INVALID");
    opt.textContent = `${r.id}${flags.length ? " [" + flags.join(", ") + "]" : ""}`;
    select.appendChild(opt);
  }
  select.addEventListener("change", () => selectRun(select.value));
  // Most recent run first (list_runs returns newest-first already).
  await selectRun(runs[0].id);
}

async function selectRun(runId) {
  pause();
  state.runId = runId;
  setStatus(`Loading run ${runId}...`);
  el("runSelect").value = runId;

  const [config, edgeStates, floodMeta] = await Promise.all([
    getJSON(`${API}/runs/${runId}/config`),
    getJSON(`${API}/runs/${runId}/edge_states`),
    getJSON(`${API}/runs/${runId}/flood/frames`),
  ]);
  state.config = config;
  state.edgeStates = edgeStates;
  state.floodMeta = floodMeta;

  renderRunInfo(config, floodMeta);
  setupFloodLayers(floodMeta, runId);

  const endS = config.end_s || 3700;
  const slider = el("timeSlider");
  slider.min = 0;
  slider.max = endS;
  slider.step = 1;
  slider.value = 0;
  state.currentTime = 0;
  renderAtTime(0);

  setStatus(`Loading vehicle positions (fcd, stride=5s)...`);
  state.fcd = await getJSON(`${API}/runs/${runId}/fcd?stride=5`);
  setupVehicleLayer();
  renderAtTime(0);
  setStatus(
    `Run ${runId} ready. ${state.fcd.frames.length} vehicle-position frames ` +
      `(stride=${state.fcd.stride_s}s), ${edgeStates.frames.length} edge-state frame(s), ` +
      `flood raster ${floodMeta.available ? "available" : "not available"}.`
  );
}

function renderRunInfo(config, floodMeta) {
  const health = config.run_health || {};
  const validClass =
    config.run_valid === false ? "bad" : config.run_valid === true ? "ok" : "";
  let boundsWarning = "";
  if (floodMeta.available && floodMeta.bounds_match_georef === false) {
    boundsWarning =
      '<div class="warn">⚠ flood raster bounds differ from georef.DEFAULT_TRANSFORM — check alignment</div>';
  }
  el("runInfo").innerHTML = `
    <div class="row"><span>scenario</span><span>${config.scenario || "?"}</span></div>
    <div class="row"><span>storm</span><span>${config.storm_scenario || "-"}</span></div>
    <div class="row"><span>seed</span><span>${config.seed ?? "-"}</span></div>
    <div class="row"><span>rerouting %</span><span>${
      config.rerouting_probability != null ? Math.round(config.rerouting_probability * 100) + "%" : "-"
    }</span></div>
    <div class="row"><span>duration</span><span>${config.end_s ?? "?"}s</span></div>
    <div class="row"><span>arrived</span><span>${health.arrived ?? "-"}</span></div>
    <div class="row"><span>teleports</span><span class="${validClass}">${health.teleports ?? "-"}</span></div>
    ${boundsWarning}
  `;
}

// ---------------------------------------------------------------------
// Flood raster overlay
// ---------------------------------------------------------------------

function setupFloodLayers(floodMeta, runId) {
  // Remove the previous run's flood layers/sources. Tracked in `state`
  // (rather than queried from map.getStyle()) so this doesn't depend on
  // the style object being fully materialized -- getStyle() can return
  // undefined very early in the map's lifecycle.
  for (const { layerId, srcId } of state.floodLayerIds || []) {
    if (map.getLayer(layerId)) map.removeLayer(layerId);
    if (map.getSource(srcId)) map.removeSource(srcId);
  }
  state.floodLayerIds = [];
  if (!floodMeta.available) return;

  const { north, south, east, west } = floodMeta.bounds;
  const coordinates = [
    [west, north],
    [east, north],
    [east, south],
    [west, south],
  ];
  const opacity = el("opacitySlider").value / 100;
  for (const frame of floodMeta.frames) {
    const srcId = `flood-src-${frame.index}`;
    const layerId = `flood-layer-${frame.index}`;
    map.addSource(srcId, {
      type: "image",
      url: `${API}/runs/${runId}/flood/${frame.index}.png`,
      coordinates,
    });
    map.addLayer({
      id: layerId,
      type: "raster",
      source: srcId,
      paint: { "raster-opacity": opacity },
      layout: { visibility: "none" },
    });
    state.floodLayerIds.push({ layerId, srcId });
  }
}

function setFloodOpacity(opacity) {
  if (!state.floodMeta.available) return;
  for (const frame of state.floodMeta.frames) {
    const layerId = `flood-layer-${frame.index}`;
    if (map.getLayer(layerId)) map.setPaintProperty(layerId, "raster-opacity", opacity);
  }
}

function updateFloodVisibility(t) {
  if (!state.floodMeta.available) return;
  const frames = state.floodMeta.frames;
  // Step function: show the latest frame whose mark_s <= t; none before the
  // first mark (matches how frame closures were step-applied in the sim,
  // PROJECT_PLAN.md open question #3).
  let activeIndex = -1;
  for (const f of frames) {
    if (f.mark_s != null && f.mark_s <= t) activeIndex = f.index;
  }
  for (const f of frames) {
    const layerId = `flood-layer-${f.index}`;
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", f.index === activeIndex ? "visible" : "none");
    }
  }
}

// ---------------------------------------------------------------------
// Edge coloring by time
// ---------------------------------------------------------------------

function updateEdgeColorsAtTime(t) {
  const frames = state.edgeStates.frames;
  if (!frames || frames.length === 0) {
    clearEdgeColors();
    return;
  }
  let active = null;
  for (const f of frames) {
    if (f.mark_s <= t) active = f;
  }
  if (active === null) {
    clearEdgeColors();
    return;
  }
  applyEdgeColors(active.edges);
}

// ---------------------------------------------------------------------
// Vehicles (FCD)
// ---------------------------------------------------------------------

function setupVehicleLayer() {
  const empty = { type: "FeatureCollection", features: [] };
  if (map.getSource("vehicles")) {
    map.getSource("vehicles").setData(empty);
    return;
  }
  map.addSource("vehicles", { type: "geojson", data: empty });
  map.addLayer({
    id: "vehicles-circle",
    type: "circle",
    source: "vehicles",
    paint: {
      "circle-radius": 3.5,
      "circle-color": [
        "interpolate",
        ["linear"],
        ["get", "speed"],
        0, "#ff4444",
        6.7, "#ffff00",
        13.4, "#00e676",
      ],
      "circle-opacity": 0.9,
      "circle-stroke-width": 0,
    },
  });
}

function nearestFcdFrame(t) {
  const frames = state.fcd.frames;
  if (!frames || frames.length === 0) return null;
  let best = frames[0];
  let bestDiff = Math.abs(frames[0].t - t);
  for (const f of frames) {
    const diff = Math.abs(f.t - t);
    if (diff < bestDiff) {
      best = f;
      bestDiff = diff;
    }
    if (f.t > t) break; // frames are time-ordered; no need to scan further
  }
  return best;
}

function updateVehiclesAtTime(t) {
  if (!map.getSource("vehicles")) return;
  const frame = nearestFcdFrame(t);
  if (!frame) return;
  const features = frame.v.map(([id, lon, lat, speed]) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [lon, lat] },
    properties: { id, speed },
  }));
  map.getSource("vehicles").setData({ type: "FeatureCollection", features });
}

// ---------------------------------------------------------------------
// Time scrubber / playback
// ---------------------------------------------------------------------

function formatTime(t) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `t = ${t.toFixed(0)}s (${m}m${s.toString().padStart(2, "0")}s)`;
}

function renderAtTime(t) {
  state.currentTime = t;
  el("timeSlider").value = t;
  el("timeLabel").textContent = formatTime(t);
  updateEdgeColorsAtTime(t);
  updateFloodVisibility(t);
  updateVehiclesAtTime(t);
}

function play() {
  if (state.playing) return;
  state.playing = true;
  el("playBtn").innerHTML = "&#10074;&#10074;";
  const stepMs = 200;
  state.playTimer = setInterval(() => {
    const speed = parseFloat(el("speedSelect").value);
    const endS = parseFloat(el("timeSlider").max);
    let t = state.currentTime + speed * (stepMs / 1000);
    if (t >= endS) {
      t = endS;
      pause();
    }
    renderAtTime(t);
  }, stepMs);
}

function pause() {
  state.playing = false;
  el("playBtn").innerHTML = "&#9654;";
  if (state.playTimer) {
    clearInterval(state.playTimer);
    state.playTimer = null;
  }
}

el("playBtn").addEventListener("click", () => (state.playing ? pause() : play()));
el("timeSlider").addEventListener("input", (e) => {
  pause();
  renderAtTime(parseFloat(e.target.value));
});
el("opacitySlider").addEventListener("input", (e) => {
  el("opacityVal").textContent = `${e.target.value}%`;
  setFloodOpacity(e.target.value / 100);
});
