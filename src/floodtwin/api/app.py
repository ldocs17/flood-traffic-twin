"""FastAPI app: Slice 5 web replay backend (PROJECT_PLAN.md SG3 / Slice 5).

Serves completed run artifacts under ``runs/`` for the MapLibre frontend in
``web/`` to replay: run listing, config, pre-parsed FCD vehicle positions,
edge-state-colored network, and the flood raster overlay.

Run with (from the repo root, in the Python env that has ``sumolib``
reachable via SUMO_HOME -- see ``floodtwin.sumo_env`` -- and the ``api``
extra installed):

    python -m uvicorn floodtwin.api.app:app --reload --port 8000

Then open http://localhost:8000/ .

Slice 5 scope was replay of *already-completed* runs only. Slice 6 (below)
adds ``POST /api/runs``: submit a scenario config (storm, rerouting
fraction, seed, optional manual edge closures) from the browser and it
triggers a real SUMO+flood-coupling pipeline run in the background, polled
via ``GET /api/run_jobs/{job_id}`` until a run artifact exists to replay.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from floodtwin.api import edge_states as edge_states_mod
from floodtwin.api import fcd as fcd_mod
from floodtwin.api import flood_raster
from floodtwin.api import network as network_mod
from floodtwin.api import run_jobs
from floodtwin.api import runs as runs_mod
from floodtwin.api.run_request import InvalidRunRequestError, RunRequest, parse_run_request
from floodtwin.flood import paths as flood_paths
from floodtwin.sim import paths as sim_paths
from floodtwin.sim import runner as runner_mod

REPO_ROOT = sim_paths.REPO_ROOT
RUNS_DIR = sim_paths.RUNS_DIR
NET_FILE = sim_paths.NET_FILE
WEB_DIR = REPO_ROOT / "web"

app = FastAPI(title="floodtwin replay API")


def _get_run_dir(run_id: str) -> Path:
    try:
        return runs_mod.run_dir_for_id(RUNS_DIR, run_id)
    except runs_mod.InvalidRunIdError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except runs_mod.RunNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/runs")
def api_list_runs():
    return runs_mod.list_runs(RUNS_DIR)


@app.get("/api/runs/{run_id}/config")
def api_run_config(run_id: str):
    run_dir = _get_run_dir(run_id)
    return runs_mod.load_run_config(run_dir)


@app.get("/api/network")
def api_network():
    """District road network as GeoJSON (shared across all runs -- every
    run in this repo simulates on the same ``data/net/district.net.xml``)."""
    return network_mod.load_network_geojson(NET_FILE)


@app.get("/api/runs/{run_id}/fcd")
def api_run_fcd(run_id: str, stride: float = fcd_mod.DEFAULT_STRIDE_S):
    run_dir = _get_run_dir(run_id)
    fcd_path = run_dir / "fcd.xml"
    if not fcd_path.exists():
        raise HTTPException(status_code=404, detail=f"run {run_id!r} has no fcd.xml")
    convert = network_mod.lonlat_converter(NET_FILE)
    return fcd_mod.parse_fcd_frames(fcd_path, stride_s=stride, convert=convert)


@app.get("/api/runs/{run_id}/edge_states")
def api_run_edge_states(run_id: str):
    run_dir = _get_run_dir(run_id)
    csv_path = run_dir / "edge_states.csv"
    if not csv_path.exists():
        # Baseline (no-flood) runs have no edge-state table -- an empty
        # frame list (not a 404) so the frontend can render "all open"
        # without special-casing baseline runs.
        return {"marks_s": [], "frames": []}
    config = runs_mod.load_run_config(run_dir)
    default_mark_s = config.get("closure_time_s") or 0.0
    parsed = edge_states_mod.parse_edge_states_csv(csv_path, default_mark_s=default_mark_s)
    speed_limits = network_mod.speed_limits_ms(NET_FILE)
    return edge_states_mod.edge_states_to_json(parsed, speed_limits_ms=speed_limits)


@app.get("/api/runs/{run_id}/flood/frames")
def api_run_flood_frames(run_id: str):
    run_dir = _get_run_dir(run_id)
    config = runs_mod.load_run_config(run_dir)
    source = flood_raster.resolve_flood_source(config)
    if source is None:
        return {"available": False, "frames": []}
    n_frames = source.depth_stack.shape[2]
    global_max = float(source.depth_stack.max()) or 1.0
    frames = []
    for i in range(n_frames):
        mark_s = source.frame_marks_s[i] if i < len(source.frame_marks_s) else None
        label = source.frame_labels[i] if i < len(source.frame_labels) else f"frame {i}"
        frames.append({"index": i, "mark_s": mark_s, "label": label})
    return {
        "available": True,
        "bounds": {
            "north": source.transform.north,
            "south": source.transform.south,
            "east": source.transform.east,
            "west": source.transform.west,
        },
        "bounds_match_georef": source.bounds_match_georef,
        "global_max_depth_normalized": global_max,
        "frames": frames,
    }


@app.get("/api/runs/{run_id}/flood/{frame_index}.png")
def api_run_flood_png(run_id: str, frame_index: int):
    run_dir = _get_run_dir(run_id)
    config = runs_mod.load_run_config(run_dir)
    source = flood_raster.resolve_flood_source(config)
    if source is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} has no flood raster data")
    n_frames = source.depth_stack.shape[2]
    if not (0 <= frame_index < n_frames):
        raise HTTPException(
            status_code=404, detail=f"frame_index {frame_index} out of range (0..{n_frames - 1})"
        )
    global_max = float(source.depth_stack.max()) or 1.0
    rgba = flood_raster.frame_rgba(source.depth_stack, frame_index, global_max=global_max)
    png_bytes = flood_raster.rgba_to_png_bytes(rgba)
    return Response(content=png_bytes, media_type="image/png")


# ---------------------------------------------------------------------------
# Slice 6: "Run from the browser" -- POST a scenario config, poll for
# completion, then replay it with the same endpoints as any other run above.
# ---------------------------------------------------------------------------


@app.get("/api/scenarios")
def api_scenarios():
    """Storm scenarios available for the browser's scenario form (Slice 6),
    with a ``cached`` flag so the frontend can hint whether picking one
    means an instant run or a ~30s flood-model cold start."""
    return {"scenarios": flood_paths.list_available_scenarios()}


def _execute_run_job(job_id: str, req: RunRequest, manual_closures: list) -> None:
    run_jobs.run_job(
        job_id,
        lambda: runner_mod.run_flooded_multiframe(
            scenario_name=req.storm_scenario,
            seed=req.seed,
            rerouting_fraction=req.rerouting_probability,
            manual_closures=manual_closures,
        ),
    )


@app.post("/api/runs")
def api_create_run(body: dict, background_tasks: BackgroundTasks):
    """Slice 6: trigger a full flooded-multiframe pipeline run
    (``floodtwin.sim.runner.run_flooded_multiframe`` -- the same
    orchestration the CLI uses, not a duplicate) from a browser-submitted
    scenario config. Returns immediately with a ``job_id`` to poll at
    ``GET /api/run_jobs/{job_id}`` -- see ``floodtwin.api.run_jobs`` for why
    this is a background task rather than a blocking request.

    Cheap validation happens synchronously, before any job is created, so
    the client gets an immediate 400 instead of a job that's doomed to fail
    30s later: the body shape (``floodtwin.api.run_request``), manual-closure
    edge IDs against the real net, and the storm scenario name against the
    scenarios actually on disk (when that list is available at all).
    """
    try:
        req = parse_run_request(body)
    except InvalidRunRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))

    net = network_mod.load_net(sim_paths.NET_FILE)
    try:
        manual_closures = runner_mod.validate_manual_closures(net, req.manual_closures)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    available = flood_paths.list_available_scenarios()
    available_names = {s["name"] for s in available}
    if available_names and req.storm_scenario not in available_names:
        raise HTTPException(
            status_code=400,
            detail=f"unknown storm scenario {req.storm_scenario!r} (see GET /api/scenarios)",
        )

    job_id = run_jobs.create_job(dataclasses.asdict(req))
    background_tasks.add_task(_execute_run_job, job_id, req, manual_closures)
    return {"job_id": job_id, "status": "running"}


@app.get("/api/run_jobs/{job_id}")
def api_run_job_status(job_id: str):
    job = run_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
    return job


# Static frontend (web/) -- mounted last so it acts as a catch-all and
# never shadows the /api/* routes above. `html=True` serves web/index.html
# at "/".
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
