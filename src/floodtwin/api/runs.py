"""Run discovery + config loading (PROJECT_PLAN.md #2 "Run artifact"
contract). Pure stdlib -- scans ``runs/`` for directories that look like a
run artifact (have a ``config.json``) and reads their metadata.

A run's ``id`` is just its directory name (e.g.
``20260810_173500_flooded_multiframe``) -- timestamped and unique by
construction (``floodtwin.sim.artifact.make_run_dir``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class RunNotFoundError(Exception):
    pass


class InvalidRunIdError(Exception):
    """Raised for a run id that isn't a bare directory name (defends the
    file-serving endpoints against path traversal, e.g. ``..%2Fsecret``)."""


def _validate_run_id(run_id: str) -> None:
    if not run_id or run_id in (".", "..") or "/" in run_id or "\\" in run_id:
        raise InvalidRunIdError(f"invalid run id: {run_id!r}")


def run_dir_for_id(runs_dir: Path, run_id: str) -> Path:
    _validate_run_id(run_id)
    run_dir = runs_dir / run_id
    if not run_dir.is_dir() or not (run_dir / "config.json").exists():
        raise RunNotFoundError(f"no run artifact directory {run_id!r} under {runs_dir}")
    return run_dir


def load_run_config(run_dir: Path) -> Dict[str, Any]:
    with open(run_dir / "config.json") as f:
        return json.load(f)


def summarize_run(run_dir: Path) -> Optional[Dict[str, Any]]:
    """Build a list-view summary for one run directory, or ``None`` if it
    doesn't look like a valid run artifact (no ``config.json`` -- e.g. a
    stray file like ``demo_baseline_vs_flooded.html`` sitting in ``runs/``)."""
    config_path = run_dir / "config.json"
    if not config_path.is_file():
        return None
    try:
        config = load_run_config(run_dir)
    except (json.JSONDecodeError, OSError):
        return None

    has_fcd = (run_dir / "fcd.xml").exists()
    has_edge_states = (run_dir / "edge_states.csv").exists()
    has_flood_raster = bool(config.get("forecast_npz")) and Path(config["forecast_npz"]).exists()

    return {
        "id": run_dir.name,
        "scenario": config.get("scenario"),
        "storm_scenario": config.get("storm_scenario"),
        "seed": config.get("seed"),
        "begin_s": config.get("begin_s", 0),
        "end_s": config.get("end_s"),
        "rerouting_probability": config.get("rerouting_probability"),
        "run_valid": config.get("run_valid", config.get("run_health", {}).get("teleports", 0) == 0),
        "run_health": config.get("run_health"),
        "frame_marks_s": config.get("frame_marks_s"),
        "frame_labels": config.get("frame_labels"),
        "closure_time_s": config.get("closure_time_s"),
        "has_fcd": has_fcd,
        "has_edge_states": has_edge_states,
        "has_flood_raster": has_flood_raster,
    }


def list_runs(runs_dir: Path) -> List[Dict[str, Any]]:
    """List every valid run artifact directory under ``runs_dir``, newest
    first (run ids are ``YYYYMMDD_HHMMSS_<label>``, so a reverse lexical
    sort on the directory name is a reverse chronological sort)."""
    if not runs_dir.is_dir():
        return []
    summaries = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        summary = summarize_run(child)
        if summary is not None:
            summaries.append(summary)
    summaries.sort(key=lambda s: s["id"], reverse=True)
    return summaries
