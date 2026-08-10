"""Run artifact helpers (PROJECT_PLAN.md #2 "Run artifact" contract): every
run lands in ``runs/<timestamp>_<label>/`` with a ``config.json``, SUMO's
native outputs, and an edge-state table.
"""
from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

from floodtwin.coupling.edge_mapper import depth_to_mm

from .paths import RUNS_DIR


def make_run_dir(label: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / f"{ts}_{label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_config(run_dir: Path, config: dict) -> Path:
    path = run_dir / "config.json"
    with open(path, "w") as f:
        json.dump(config, f, indent=2, default=str)
    return path


def write_edge_state_table(
    run_dir: Path,
    net,
    edge_depths_normalized: Dict[str, float],
    closed: Set[str],
) -> Path:
    """Edge-state table (PROJECT_PLAN.md #2): edge_id, max_depth_m, v_max_ms,
    closed. Written for every edge in the net -- edges with no flood-grid
    overlap get max_depth_m = 0.0 / closed = 0, which is the correct "dry"
    state, not a missing value."""
    path = run_dir / "edge_states.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["edge_id", "max_depth_m", "v_max_ms", "closed"])
        for edge in net.getEdges():
            eid = edge.getID()
            depth_norm = edge_depths_normalized.get(eid, 0.0)
            depth_m = depth_to_mm(depth_norm) / 1000.0
            is_closed = eid in closed
            v_max = 0.0 if is_closed else edge.getSpeed()
            w.writerow([eid, f"{depth_m:.4f}", f"{v_max:.3f}", int(is_closed)])
    return path


def write_multiframe_edge_state_table(
    run_dir: Path,
    net,
    edge_depths_by_mark: Dict[float, Dict[str, float]],
    edge_states_by_mark: Dict[float, Dict[str, "tuple"]],
) -> Path:
    """Slice 2 edge-state table: extends Slice 1's single-frame
    ``edge_states.csv`` with a ``frame_min`` column so all four 15-min marks
    are logged (PROJECT_PLAN.md Slice 2). Columns:
    ``frame_min, edge_id, max_depth_m, v_max_ms, closed``.

    Written for every edge in the net, at every mark -- edges with no
    flood-grid overlap at a given mark get ``max_depth_m = 0.0`` / their
    unmodified speed limit / ``closed = 0`` (the correct "dry" state, not a
    missing value), matching Slice 1's per-edge coverage guarantee.
    """
    path = run_dir / "edge_states.csv"
    marks = sorted(edge_states_by_mark.keys())
    edges = list(net.getEdges())
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame_min", "edge_id", "max_depth_m", "v_max_ms", "closed"])
        for mark in marks:
            frame_min = int(round(mark / 60.0))
            states = edge_states_by_mark[mark]
            depths = edge_depths_by_mark.get(mark, {})
            for edge in edges:
                eid = edge.getID()
                depth_norm = depths.get(eid, 0.0)
                depth_m = depth_to_mm(depth_norm) / 1000.0
                v_max, is_closed = states.get(eid, (edge.getSpeed(), False))
                w.writerow([frame_min, eid, f"{depth_m:.4f}", f"{v_max:.3f}", int(is_closed)])
    return path


def parse_run_health(summary_xml_path: Path) -> dict:
    """Cumulative teleport/collision counts + final arrival stats from a
    SUMO ``--summary-output`` file (Plan R4: any teleport flags a run
    invalid). Uses the last <step> element, which carries cumulative
    totals."""
    tree = ET.parse(summary_xml_path)
    steps = tree.getroot().findall("step")
    if not steps:
        return {"teleports": None, "collisions": None, "arrived": None, "running": None}
    last = steps[-1]
    return {
        "teleports": int(last.get("teleports", 0)),
        "collisions": int(last.get("collisions", 0)),
        "arrived": int(last.get("arrived", 0)),
        "running": int(last.get("running", 0)),
        "loaded": int(last.get("loaded", 0)),
        "inserted": int(last.get("inserted", 0)),
        "final_time": float(last.get("time", 0)),
    }


def parse_reroute_stats(tripinfo_xml_path: Path) -> dict:
    """Aggregate ``rerouteNo`` across all vehicles in a tripinfo file --
    evidence of rerouting activity (device.rerouting recomputes routes
    periodically; a flooded run should show more/different reroutes than
    baseline for vehicles whose path crossed a closed edge)."""
    tree = ET.parse(tripinfo_xml_path)
    trips = tree.getroot().findall("tripinfo")
    reroute_counts = [int(t.get("rerouteNo", 0)) for t in trips]
    n_rerouted = sum(1 for r in reroute_counts if r > 0)
    return {
        "n_vehicles": len(trips),
        "n_vehicles_rerouted": n_rerouted,
        "total_reroutes": sum(reroute_counts),
    }


def edges_used(vehroute_xml_path: Path) -> Dict[str, Set[str]]:
    """Map vehicle id -> set of edge IDs actually driven, from a SUMO
    ``--vehroute-output`` file. Used to check whether any vehicle actually
    drove across a closed edge after the closure time (it shouldn't, once
    rerouting has a chance to act -- vehicles already committed to the edge
    when it closes are the expected exception, per D5)."""
    tree = ET.parse(vehroute_xml_path)
    result: Dict[str, Set[str]] = {}
    for vehicle in tree.getroot().findall("vehicle"):
        vid = vehicle.get("id")
        edge_ids: Set[str] = set()
        # A vehicle may have multiple <route> children if it was rerouted;
        # the last one (or the only "exitTimes"-bearing one) is what it
        # actually drove -- but the safe conservative check is the union of
        # every route element's edges, since replaced routes were partially
        # driven too before the reroute.
        for route in vehicle.findall("route"):
            edges_attr = route.get("edges", "")
            edge_ids.update(edges_attr.split())
        result[vid] = edge_ids
    return result
