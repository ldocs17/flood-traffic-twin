// Slice 5+6 frontend. Vanilla JS + MapLibre GL JS (CDN, no build step).
// Talks only to this same-origin FastAPI backend (floodtwin.api.app).
//
// Slice 5 built the replay view (run picker, time scrubber, flood overlay,
// edge coloring, animated FCD positions) for *already-completed* runs.
// Slice 6 adds two things on top, without rewriting that replay logic:
//   1. "Run from the browser": a scenario form (storm/rerouting/seed) plus
//      click-to-toggle manual edge closures on the map, POSTing to
//      POST /api/runs and polling GET /api/run_jobs/{job_id} until the run
//      artifact is ready, then loading it straight into the replay view.
//   2. Two side-by-side panels ("two runs compared side by side" -- the
//      Slice 6 demo criterion) -- the whole Slice 5 UI plus the new form is
//      factored into `createPanel(panelId)` and instantiated twice below,
//      each with its own independent MapLibre map/state/DOM, so a user can
//      run two different configurations and eyeball them next to each
//      other. No synchronized scrubbing between panels -- not needed for
//      the demo and would add real complexity for no clear payoff.

const API = "/api";

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}: ${await res.text().catch(() => "")}`);
  return res.json();
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : JSON.stringify(data);
    throw new Error(`${url} -> ${res.status}: ${detail}`);
  }
  return data;
}

function createPanel(panelId) {
  const root = document.getElementById(panelId);
  const $ = (sel) => root.querySelector(sel);

  const state = {
    runId: null,
    config: null,
    edgeStates: { marks_s: [], frames: [] },
    floodMeta: { available: false, frames: [] },
    fcd: { stride_s: 5, frames: [] },
    networkGeojson: null,
    floodLayerIds: [],
    playing: false,
    playTimer: null,
    currentTime: 0,
    closureMode: false,
    pendingClosures: new Set(), // manual edge closures being composed for a NEW run
    jobPollTimer: null,
  };

  function setStatus(msg) {
    $(".status").textContent = msg;
  }

  function setJobStatus(msg, cls) {
    const el = $(".job-status");
    el.textContent = msg;
    el.className = "job-status" + (cls ? ` ${cls}` : "");
  }

  // ---------------------------------------------------------------------
  // Map setup
  // ---------------------------------------------------------------------

  const map = new maplibregl.Map({
    container: $(".map"),
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

  map.on("error", (e) => console.error(`[${panelId}] maplibre error`, e && e.error));
  map.on("load", async () => {
    try {
      await loadNetworkLayer();
      await loadScenarios();
      await loadRunList();
    } catch (e) {
      console.error(`[${panelId}] init failed`, e);
      setStatus(`Init failed: ${e.message}`);
    }
  });

  // ---------------------------------------------------------------------
  // Network (shared across runs within this panel; loaded once)
  // ---------------------------------------------------------------------

  async function loadNetworkLayer() {
    setStatus("Loading road network...");
    const geojson = await getJSON(`${API}/network`);
    state.networkGeojson = geojson;
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

    // Slice 6: pending manual closures for a NEW run being composed --
    // rendered from a small separately-managed GeoJSON source (not
    // feature-state on "network") so it survives `clearEdgeColors()`
    // (replay's `map.removeFeatureState({source:"network"})` wipes ALL
    // feature-state for that source, which would otherwise also erase this).
    map.addSource("pending-closures", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    map.addLayer({
      id: "pending-closures-line",
      type: "line",
      source: "pending-closures",
      paint: { "line-color": "#ff00ff", "line-width": 5, "line-opacity": 0.85, "line-dasharray": [1, 1] },
    });

    map.on("click", "network-line", onEdgeClick);
    map.on("mouseenter", "network-line", () => {
      if (state.closureMode) map.getCanvas().style.cursor = "crosshair";
    });
    map.on("mouseleave", "network-line", () => {
      map.getCanvas().style.cursor = "";
    });

    // Fit the view to the network bounds once.
    const bounds = new maplibregl.LngLatBounds();
    for (const f of geojson.features) {
      for (const c of f.geometry.coordinates) bounds.extend(c);
    }
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 40, duration: 0 });

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
  // Slice 6: click-to-toggle manual edge closures
  // ---------------------------------------------------------------------

  function onEdgeClick(e) {
    if (!state.closureMode) return;
    if (!e.features || e.features.length === 0) return;
    const edgeId = e.features[0].properties.id;
    toggleClosure(edgeId);
  }

  function toggleClosure(edgeId) {
    if (state.pendingClosures.has(edgeId)) {
      state.pendingClosures.delete(edgeId);
    } else {
      state.pendingClosures.add(edgeId);
    }
    renderPendingClosures();
  }

  function renderPendingClosures() {
    const ids = Array.from(state.pendingClosures);
    if (state.networkGeojson) {
      const idSet = state.pendingClosures;
      const features = state.networkGeojson.features.filter((f) => idSet.has(f.properties.id));
      const src = map.getSource("pending-closures");
      if (src) src.setData({ type: "FeatureCollection", features });
    }
    const listEl = $(".closure-list");
    listEl.innerHTML = "";
    if (ids.length === 0) {
      listEl.innerHTML = '<span class="closureEmpty" style="color:#666;">none selected</span>';
      return;
    }
    for (const id of ids) {
      const chip = document.createElement("span");
      chip.className = "closure-chip";
      chip.textContent = id;
      chip.title = "Click to remove";
      chip.addEventListener("click", () => toggleClosure(id));
      listEl.appendChild(chip);
    }
  }

  $(".closureModeBtn").addEventListener("click", () => {
    state.closureMode = !state.closureMode;
    const btn = $(".closureModeBtn");
    btn.classList.toggle("active", state.closureMode);
    btn.textContent = state.closureMode
      ? "Click map edges to close them (ON — click again to stop)"
      : "Click map edges to close them";
    map.getCanvas().style.cursor = "";
  });

  // ---------------------------------------------------------------------
  // Slice 6: scenario dropdown + run-submission form
  // ---------------------------------------------------------------------

  async function loadScenarios() {
    const select = $(".scenarioSelect");
    try {
      const { scenarios } = await getJSON(`${API}/scenarios`);
      select.innerHTML = "";
      if (scenarios.length === 0) {
        select.innerHTML = "<option>No scenarios found</option>";
        return;
      }
      // The project's canonical/validated storm event (IMPLEMENTATION_CONTEXT.md
      // continuity note, data/scenarios/README.md) is pre-cached -- default to
      // it (or the first cached scenario, or just the first scenario) so the
      // "Run scenario" button works well out of the box.
      let defaultName = null;
      for (const s of scenarios) {
        const opt = document.createElement("option");
        opt.value = s.name;
        opt.textContent = s.cached ? `${s.name} (cached)` : s.name;
        select.appendChild(opt);
        if (s.name === "Sep_30_2022_74.75") defaultName = s.name;
        if (defaultName === null && s.cached) defaultName = s.name;
      }
      select.value = defaultName || scenarios[0].name;
    } catch (e) {
      console.error(`[${panelId}] loadScenarios failed`, e);
      select.innerHTML = "<option>Failed to load scenarios</option>";
    }
  }

  $(".reroutingInput").addEventListener("input", (e) => {
    $(".reroutingVal").textContent = `${e.target.value}%`;
  });

  async function submitNewRun() {
    const runBtn = $(".runBtn");
    const storm_scenario = $(".scenarioSelect").value;
    const rerouting_probability = parseFloat($(".reroutingInput").value) / 100;
    const seed = parseInt($(".seedInput").value, 10) || 0;
    const manual_closures = Array.from(state.pendingClosures);

    runBtn.disabled = true;
    setJobStatus(
      `Submitting run (storm=${storm_scenario}, rerouting=${Math.round(rerouting_probability * 100)}%, ` +
        `seed=${seed}, ${manual_closures.length} manual closure(s))...`,
      "running"
    );
    try {
      const { job_id } = await postJSON(`${API}/runs`, {
        storm_scenario,
        rerouting_probability,
        seed,
        manual_closures,
      });
      pollJob(job_id);
    } catch (e) {
      runBtn.disabled = false;
      setJobStatus(`Failed to submit: ${e.message}`, "error");
    }
  }

  function pollJob(jobId) {
    if (state.jobPollTimer) clearInterval(state.jobPollTimer);
    const startedAt = Date.now();
    state.jobPollTimer = setInterval(async () => {
      let job;
      try {
        job = await getJSON(`${API}/run_jobs/${jobId}`);
      } catch (e) {
        clearInterval(state.jobPollTimer);
        $(".runBtn").disabled = false;
        setJobStatus(`Lost track of job: ${e.message}`, "error");
        return;
      }
      const elapsed = ((Date.now() - startedAt) / 1000).toFixed(0);
      if (job.status === "running") {
        setJobStatus(`Running... (${elapsed}s elapsed; first use of a scenario can take ~30-60s for flood-model inference)`, "running");
        return;
      }
      clearInterval(state.jobPollTimer);
      $(".runBtn").disabled = false;
      if (job.status === "done") {
        setJobStatus(`Run complete (${elapsed}s): ${job.run_id}`, "done");
        state.pendingClosures.clear();
        renderPendingClosures();
        await loadRunList(job.run_id);
      } else {
        setJobStatus(`Run failed: ${job.error || "unknown error"}`, "error");
      }
    }, 1500);
  }

  $(".runBtn").addEventListener("click", submitNewRun);

  // ---------------------------------------------------------------------
  // Run list + selection (Slice 5, unchanged logic)
  // ---------------------------------------------------------------------

  async function loadRunList(preferredRunId) {
    const runs = await getJSON(`${API}/runs`);
    const select = $(".runSelect");
    select.innerHTML = "";
    if (runs.length === 0) {
      select.innerHTML = "<option>No runs found under runs/</option>";
      setStatus("No run artifacts found yet. Run a scenario above, or generate one with floodtwin.sim.runner.");
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
    select.removeEventListener("change", select._floodtwinHandler || (() => {}));
    const handler = () => selectRun(select.value);
    select._floodtwinHandler = handler;
    select.addEventListener("change", handler);

    const target = preferredRunId && runs.some((r) => r.id === preferredRunId) ? preferredRunId : runs[0].id;
    await selectRun(target);
  }

  async function selectRun(runId) {
    pause();
    state.runId = runId;
    setStatus(`Loading run ${runId}...`);
    $(".runSelect").value = runId;

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
    const slider = $(".timeSlider");
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
    const closures = config.manual_closures || [];
    const manualRow = closures.length
      ? `<div class="row"><span>manual closures</span><span>${closures.length}</span></div>`
      : "";
    $(".run-info").innerHTML = `
      <div class="row"><span>scenario</span><span>${config.scenario || "?"}</span></div>
      <div class="row"><span>storm</span><span>${config.storm_scenario || "-"}</span></div>
      <div class="row"><span>seed</span><span>${config.seed ?? "-"}</span></div>
      <div class="row"><span>rerouting %</span><span>${
        config.rerouting_probability != null ? Math.round(config.rerouting_probability * 100) + "%" : "-"
      }</span></div>
      <div class="row"><span>duration</span><span>${config.end_s ?? "?"}s</span></div>
      <div class="row"><span>arrived</span><span>${health.arrived ?? "-"}</span></div>
      <div class="row"><span>teleports</span><span class="${validClass}">${health.teleports ?? "-"}</span></div>
      ${manualRow}
      ${boundsWarning}
    `;
  }

  // ---------------------------------------------------------------------
  // Flood raster overlay
  // ---------------------------------------------------------------------

  function setupFloodLayers(floodMeta, runId) {
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
    const opacity = $(".opacitySlider").value / 100;
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
    $(".timeSlider").value = t;
    $(".timeLabel").textContent = formatTime(t);
    updateEdgeColorsAtTime(t);
    updateFloodVisibility(t);
    updateVehiclesAtTime(t);
  }

  function play() {
    if (state.playing) return;
    state.playing = true;
    $(".playBtn").innerHTML = "&#10074;&#10074;";
    const stepMs = 200;
    state.playTimer = setInterval(() => {
      const speed = parseFloat($(".speedSelect").value);
      const endS = parseFloat($(".timeSlider").max);
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
    $(".playBtn").innerHTML = "&#9654;";
    if (state.playTimer) {
      clearInterval(state.playTimer);
      state.playTimer = null;
    }
  }

  $(".playBtn").addEventListener("click", () => (state.playing ? pause() : play()));
  $(".timeSlider").addEventListener("input", (e) => {
    pause();
    renderAtTime(parseFloat(e.target.value));
  });
  $(".opacitySlider").addEventListener("input", (e) => {
    $(".opacityVal").textContent = `${e.target.value}%`;
    setFloodOpacity(e.target.value / 100);
  });

  return { map, state, loadRunList, selectRun };
}

window.panelA = createPanel("panelA");
window.panelB = createPanel("panelB");
