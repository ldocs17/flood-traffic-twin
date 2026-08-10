"""Slice 3 analysis: baseline-vs-flooded metrics (PROJECT_PLAN.md SG2 Slice 3).

Given a baseline run artifact dir and a flooded run artifact dir produced from
the *same* demand + seed (flood off / flood on -- e.g. the pair
``floodtwin.sim.runner.run_baseline()`` / ``run_flooded_multiframe()``
produces from one ``--scenario``/``--seed`` invocation), compute:

  - travel-time delta per trip (mean/p95), matched by vehicle ID
  - exposure: vehicles whose route touched an edge that becomes
    flooded/closed at any point in the flooded run
  - throughput: vehicles arrived, baseline vs flooded
  - closure timeline: edges closed/slowed per 15-min mark, carried straight
    from the flooded run's ``config.json``/``edge_states.csv`` (Slice 2
    output) -- not recomputed here.

Design note -- pure vs I/O: every function that does real computation takes
already-parsed dicts/lists and is unit-testable without a SUMO install or a
real run directory (see ``tests/test_metrics.py``). Only the ``parse_*`` and
``compute_and_write`` functions touch the filesystem.

Output location (Slice 3 task left this to engineering judgment): a **new**
``runs/<ts>_metrics_<scenario>/`` directory, not written into either run
dir. Rationale: a run artifact (PROJECT_PLAN.md #2) is byte-for-byte
reproducible from (net, routes, seed, edge states) and should stay exactly
what SUMO produced; metrics are *derived* and may need recomputing (e.g. a
different exposure definition) without mutating the runs that fed them.
Traceability is kept explicit instead: ``metrics.json`` always records the
two source run directories verbatim under ``source_runs``, so a metrics dir
alone is enough to answer "which two runs made this figure" -- no metrics
without a run artifact, per the Run artifact contract.
"""
from __future__ import annotations

import argparse
import csv
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from floodtwin.sim import artifact
from floodtwin.sim.paths import RUNS_DIR

# ---------------------------------------------------------------------------
# Parsing (I/O)
# ---------------------------------------------------------------------------


def parse_tripinfo(path: Path) -> Dict[str, dict]:
    """Parse a SUMO ``tripinfo.xml`` into ``{vehicle_id: {duration, depart,
    arrival, routeLength, waitingTime, timeLoss, rerouteNo}}``.

    Only vehicles that actually arrived (or were otherwise removed) appear
    here -- SUMO's tripinfo output only records completed trips, so a
    vehicle still en route when the simulation ends is simply absent, not
    zero/None. Callers must treat "missing" as "no completed trip in this
    run", never silently as "delta = 0".
    """
    tree = ET.parse(path)
    out: Dict[str, dict] = {}
    for t in tree.getroot().findall("tripinfo"):
        vid = t.get("id")
        out[vid] = {
            "depart": float(t.get("depart", 0.0)),
            "arrival": float(t.get("arrival", 0.0)),
            "duration": float(t.get("duration", 0.0)),
            "routeLength": float(t.get("routeLength", 0.0)),
            "waitingTime": float(t.get("waitingTime", 0.0)),
            "timeLoss": float(t.get("timeLoss", 0.0)),
            "rerouteNo": int(t.get("rerouteNo", 0)),
        }
    return out


def parse_edge_states_csv(path: Path) -> List[dict]:
    """Parse ``edge_states.csv`` (Slice 1 single-frame or Slice 2 multiframe
    schema) into a list of row dicts with numeric/bool types coerced.
    Multiframe rows carry an int ``frame_min``; single-frame rows omit it."""
    rows: List[dict] = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            row = dict(r)
            row["max_depth_m"] = float(row["max_depth_m"])
            row["v_max_ms"] = float(row["v_max_ms"])
            row["closed"] = row["closed"].strip() in ("1", "True", "true")
            if "frame_min" in row and row["frame_min"] not in (None, ""):
                row["frame_min"] = int(row["frame_min"])
            rows.append(row)
    return rows


def load_config(run_dir: Path) -> dict:
    with open(Path(run_dir) / "config.json") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> Optional[float]:
    return float(np.mean(values)) if len(values) else None


def _p95(values: Sequence[float]) -> Optional[float]:
    return float(np.percentile(values, 95)) if len(values) else None


def travel_time_comparison(
    baseline_trips: Dict[str, dict], flooded_trips: Dict[str, dict]
) -> dict:
    """Match trips by vehicle ID (the "same demand + seed" assumption --
    verified against real run pairs: vehicle IDs come straight from the
    shared route file, so a flooded run's completed-trip set is a *subset*
    of the baseline's; no ID ever appeared in flooded-but-not-baseline in
    verification runs, but this is checked, not assumed, every time).

    Returns per-trip rows for every vehicle ID seen in *either* run (so
    unmatched vehicles are reported, not silently dropped) plus summary
    stats computed only over the matched subset (a delta needs both sides).
    """
    base_ids = set(baseline_trips)
    flood_ids = set(flooded_trips)
    matched_ids = sorted(base_ids & flood_ids)
    baseline_only = sorted(base_ids - flood_ids)
    flooded_only = sorted(flood_ids - base_ids)

    per_trip: List[dict] = []
    for vid in matched_ids:
        b = baseline_trips[vid]["duration"]
        f = flooded_trips[vid]["duration"]
        per_trip.append(
            {
                "vehicle_id": vid,
                "baseline_travel_time_s": b,
                "flooded_travel_time_s": f,
                "delta_s": f - b,
            }
        )
    for vid in baseline_only:
        per_trip.append(
            {
                "vehicle_id": vid,
                "baseline_travel_time_s": baseline_trips[vid]["duration"],
                "flooded_travel_time_s": None,
                "delta_s": None,
            }
        )
    for vid in flooded_only:
        per_trip.append(
            {
                "vehicle_id": vid,
                "baseline_travel_time_s": None,
                "flooded_travel_time_s": flooded_trips[vid]["duration"],
                "delta_s": None,
            }
        )
    per_trip.sort(key=lambda r: r["vehicle_id"])

    baseline_matched = [baseline_trips[v]["duration"] for v in matched_ids]
    flooded_matched = [flooded_trips[v]["duration"] for v in matched_ids]
    deltas = [f - b for b, f in zip(baseline_matched, flooded_matched)]

    summary = {
        "n_baseline_trips": len(base_ids),
        "n_flooded_trips": len(flood_ids),
        "n_matched": len(matched_ids),
        "n_baseline_only": len(baseline_only),
        "n_flooded_only": len(flooded_only),
        "baseline_only_ids_sample": baseline_only[:20],
        "flooded_only_ids_sample": flooded_only[:20],
        "baseline_mean_travel_time_s": _mean(baseline_matched),
        "baseline_p95_travel_time_s": _p95(baseline_matched),
        "flooded_mean_travel_time_s": _mean(flooded_matched),
        "flooded_p95_travel_time_s": _p95(flooded_matched),
        "mean_delta_s": _mean(deltas),
        "p95_delta_s": _p95(deltas),
    }
    if flooded_only:
        summary["WARNING"] = (
            f"{len(flooded_only)} vehicle ID(s) arrived in the flooded run but never "
            "appeared in the baseline run's tripinfo -- this breaks the 'same demand "
            "+ seed' ID-matching assumption (expected direction is the opposite: "
            "flooded arrivals should always be a subset of baseline arrivals). "
            "Investigate before trusting the delta numbers: check the two runs' "
            "route_file/seed in config.json actually match."
        )
    return {
        "summary": summary,
        "per_trip": per_trip,
        "baseline_only_ids": baseline_only,
        "flooded_only_ids": flooded_only,
        "matched_ids": matched_ids,
    }


def flooded_edge_sets(edge_state_rows: List[dict]) -> Tuple[Set[str], Set[str]]:
    """From parsed ``edge_states.csv`` rows, the set of edges that were
    ``closed`` at any recorded mark, and the (superset) set of edges with any
    nonzero sampled depth at any mark ("wet", whether or not they closed)."""
    closed = {r["edge_id"] for r in edge_state_rows if r["closed"]}
    wet = {r["edge_id"] for r in edge_state_rows if r["max_depth_m"] > 0.0}
    return closed, wet


def exposure_summary(
    vehicle_edges: Dict[str, Set[str]], closed_edges: Set[str], wet_edges: Set[str]
) -> dict:
    """Vehicles whose driven route (``vehicle_edges``, from the flooded run's
    ``vehroutes.xml`` -- the union of every route leg a vehicle carried,
    including pre-reroute legs, so a vehicle that successfully detoured
    *before* reaching a since-closed edge still counts as exposed) touches an
    edge that was ever closed, or ever wet, during the flooded run.

    ``vehicle_edges`` population caveat: SUMO's default ``--vehroute-output``
    only records vehicles once they leave the simulation (arrive), so this
    is the flooded run's *completed-trip* population, not the full demand --
    vehicles still en route at the sim's end have no recorded route to check
    (see the ``exposure`` block's ``population_note`` in the output).
    """
    total = len(vehicle_edges)
    exposed_closed = {vid for vid, edges in vehicle_edges.items() if edges & closed_edges}
    exposed_wet = {vid for vid, edges in vehicle_edges.items() if edges & wet_edges}
    return {
        "population_note": (
            "Denominator is vehicles with a recorded route in the flooded run's "
            "vehroutes.xml, which (under default SUMO output settings, no "
            "--vehroutes.write-unfinished) only covers vehicles that arrived/were "
            "removed -- i.e. the flooded run's completed-trip population, not the "
            "full demand. Vehicles still running at simulation end are excluded."
        ),
        "n_closed_edges_ever": len(closed_edges),
        "n_wet_edges_ever": len(wet_edges),
        "total_vehicles_with_recorded_route": total,
        "n_exposed_closed_edge": len(exposed_closed),
        "pct_exposed_closed_edge": (100.0 * len(exposed_closed) / total) if total else None,
        "n_exposed_wet_edge": len(exposed_wet),
        "pct_exposed_wet_edge": (100.0 * len(exposed_wet) / total) if total else None,
        "exposed_closed_edge_vehicle_ids": sorted(exposed_closed),
    }


def throughput_summary(baseline_health: dict, flooded_health: dict) -> dict:
    """Arrived-vehicle throughput, baseline vs flooded, straight from each
    run's ``run_health`` (already parsed from ``summary.xml`` by
    ``artifact.parse_run_health`` at run time -- not recomputed here)."""
    b_arrived = baseline_health.get("arrived")
    f_arrived = flooded_health.get("arrived")
    delta = None
    if b_arrived is not None and f_arrived is not None:
        delta = f_arrived - b_arrived
    return {
        "baseline_arrived": b_arrived,
        "flooded_arrived": f_arrived,
        "delta_arrived": delta,
        "baseline_loaded": baseline_health.get("loaded"),
        "flooded_loaded": flooded_health.get("loaded"),
        "baseline_inserted": baseline_health.get("inserted"),
        "flooded_inserted": flooded_health.get("inserted"),
        "baseline_running_at_end": baseline_health.get("running"),
        "flooded_running_at_end": flooded_health.get("running"),
        "flooded_discarded_before_insertion": (
            (flooded_health.get("loaded") - flooded_health.get("inserted"))
            if flooded_health.get("loaded") is not None and flooded_health.get("inserted") is not None
            else None
        ),
    }


def closure_timeline(flooded_config: dict, edge_state_rows: List[dict]) -> List[dict]:
    """Edges closed/slowed per 15-min mark. Per the Slice 3 task, this is
    *summarized/carried*, not recomputed: ``run_flooded_multiframe`` already
    writes an accurate ``n_closed``/``n_slowed``/``n_full_speed`` count per
    mark into the flooded run's ``config.json`` (it has each edge's real
    speed limit at hand; ``edge_states.csv`` alone does not carry the speed
    limit column, so re-deriving "slowed" from the CSV would be an
    approximation). When available, that ``per_frame_summary`` is returned
    verbatim. Only for older/Slice-1-style single-frame runs without it does
    this fall back to a coarser count derived from ``edge_states.csv``
    directly (closed-edge count only; "slowed" is left ``None`` since the
    speed limit isn't in the CSV)."""
    if flooded_config.get("per_frame_summary"):
        return flooded_config["per_frame_summary"]

    if not edge_state_rows:
        return []
    if "frame_min" not in edge_state_rows[0]:
        n_closed = sum(1 for r in edge_state_rows if r["closed"])
        return [
            {
                "mark_s": flooded_config.get("closure_time_s"),
                "label": None,
                "n_closed": n_closed,
                "n_slowed": None,
                "n_full_speed": None,
                "note": "single-frame run (no per_frame_summary in config.json); slowed count unavailable",
            }
        ]
    marks = sorted({r["frame_min"] for r in edge_state_rows})
    out = []
    for m in marks:
        rows_m = [r for r in edge_state_rows if r["frame_min"] == m]
        n_closed = sum(1 for r in rows_m if r["closed"])
        n_wet_open = sum(1 for r in rows_m if not r["closed"] and r["max_depth_m"] > 0.0)
        out.append(
            {
                "mark_s": m * 60,
                "label": f"t+{m}min",
                "n_closed": n_closed,
                "n_slowed_approx": n_wet_open,
                "n_total_edges": len(rows_m),
                "note": "approximate: 'slowed' = any nonzero depth while open, no speed-limit column in edge_states.csv",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Orchestration (I/O)
# ---------------------------------------------------------------------------


def compute_metrics(baseline_dir: Path, flooded_dir: Path) -> Tuple[dict, List[dict]]:
    """Load a baseline/flooded run pair and compute the full Slice 3 metrics
    dict, plus the per-trip rows for the CSV export. Returns
    ``(metrics, per_trip_rows)``."""
    baseline_dir = Path(baseline_dir)
    flooded_dir = Path(flooded_dir)
    baseline_config = load_config(baseline_dir)
    flooded_config = load_config(flooded_dir)

    same_seed = baseline_config.get("seed") == flooded_config.get("seed")
    same_route_file = baseline_config.get("route_file") == flooded_config.get("route_file")
    same_net_file = baseline_config.get("net_file") == flooded_config.get("net_file")

    baseline_trips = parse_tripinfo(baseline_dir / "tripinfo.xml")
    flooded_trips = parse_tripinfo(flooded_dir / "tripinfo.xml")
    tt = travel_time_comparison(baseline_trips, flooded_trips)

    vehicle_edges = artifact.edges_used(flooded_dir / "vehroutes.xml")
    edge_rows = parse_edge_states_csv(flooded_dir / "edge_states.csv")
    closed_edges_set, wet_edges_set = flooded_edge_sets(edge_rows)
    exposure = exposure_summary(vehicle_edges, closed_edges_set, wet_edges_set)

    throughput = throughput_summary(
        baseline_config.get("run_health", {}), flooded_config.get("run_health", {})
    )

    timeline = closure_timeline(flooded_config, edge_rows)

    matching_warnings = []
    if not same_seed:
        matching_warnings.append(
            f"seed mismatch: baseline seed={baseline_config.get('seed')!r} vs "
            f"flooded seed={flooded_config.get('seed')!r} -- the 'same demand+seed' "
            "vehicle-ID-matching assumption does not hold for this pair."
        )
    if not same_route_file:
        matching_warnings.append(
            f"route_file mismatch: baseline={baseline_config.get('route_file')!r} vs "
            f"flooded={flooded_config.get('route_file')!r}."
        )
    if not same_net_file:
        matching_warnings.append(
            f"net_file mismatch: baseline={baseline_config.get('net_file')!r} vs "
            f"flooded={flooded_config.get('net_file')!r}."
        )
    if "WARNING" in tt["summary"]:
        matching_warnings.append(tt["summary"]["WARNING"])

    metrics = {
        "generated_at": datetime.now().isoformat(),
        "source_runs": {
            "baseline_dir": str(baseline_dir),
            "flooded_dir": str(flooded_dir),
        },
        "scenario": {
            "baseline_scenario": baseline_config.get("scenario"),
            "flooded_scenario": flooded_config.get("scenario"),
            "storm_scenario": flooded_config.get("storm_scenario"),
            "baseline_seed": baseline_config.get("seed"),
            "flooded_seed": flooded_config.get("seed"),
        },
        "vehicle_id_matching": {
            "same_seed": same_seed,
            "same_route_file": same_route_file,
            "same_net_file": same_net_file,
            "n_matched": tt["summary"]["n_matched"],
            "n_baseline_only": tt["summary"]["n_baseline_only"],
            "n_flooded_only": tt["summary"]["n_flooded_only"],
            "warnings": matching_warnings,
        },
        "travel_time": tt["summary"],
        "exposure": exposure,
        "throughput": throughput,
        "closure_timeline": timeline,
        "run_health": {
            "baseline": baseline_config.get("run_health"),
            "flooded": flooded_config.get("run_health"),
        },
        "run_valid": {
            "baseline": baseline_config.get("run_health", {}).get("teleports") == 0,
            "flooded": flooded_config.get("run_valid", flooded_config.get("run_health", {}).get("teleports") == 0),
        },
    }

    exposed_closed_ids = set(exposure["exposed_closed_edge_vehicle_ids"])
    per_trip = []
    for row in tt["per_trip"]:
        vid = row["vehicle_id"]
        exposed = None
        if vid in vehicle_edges:
            exposed = vid in exposed_closed_ids
        per_trip.append({**row, "exposed": exposed})

    return metrics, per_trip


def write_trip_csv(per_trip: List[dict], path: Path) -> Path:
    """Per-trip CSV export: vehicle_id, baseline_travel_time, flooded_travel_time,
    delta, exposed. Blank cells mean "not applicable" (e.g. no completed trip
    in that run, or exposure unknown because the vehicle has no recorded
    route in the flooded run) -- never a silently-dropped 0."""
    path = Path(path)
    fieldnames = [
        "vehicle_id",
        "baseline_travel_time_s",
        "flooded_travel_time_s",
        "delta_s",
        "exposed",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in per_trip:
            w.writerow(
                {
                    "vehicle_id": row["vehicle_id"],
                    "baseline_travel_time_s": "" if row["baseline_travel_time_s"] is None else row["baseline_travel_time_s"],
                    "flooded_travel_time_s": "" if row["flooded_travel_time_s"] is None else row["flooded_travel_time_s"],
                    "delta_s": "" if row["delta_s"] is None else row["delta_s"],
                    "exposed": "" if row["exposed"] is None else row["exposed"],
                }
            )
    return path


def plot_travel_time_distribution(
    per_trip: List[dict], out_path: Path, title: Optional[str] = None
) -> Path:
    """Slice 3's first research figure: overlaid travel-time distributions,
    baseline vs flooded, over each run's own full completed-trip population
    (not just the matched subset -- the point of this figure is "how did the
    distribution shift", which the unmatched tail is part of)."""
    import matplotlib

    matplotlib.use("Agg")  # headless-safe (CI, no display)
    import matplotlib.pyplot as plt

    baseline_durations = [r["baseline_travel_time_s"] for r in per_trip if r["baseline_travel_time_s"] is not None]
    flooded_durations = [r["flooded_travel_time_s"] for r in per_trip if r["flooded_travel_time_s"] is not None]

    fig, ax = plt.subplots(figsize=(8, 5))
    bins = 40
    if baseline_durations:
        ax.hist(
            baseline_durations, bins=bins, alpha=0.55, density=True, label=f"Baseline (n={len(baseline_durations)})",
            color="#1f77b4",
        )
    if flooded_durations:
        ax.hist(
            flooded_durations, bins=bins, alpha=0.55, density=True, label=f"Flooded (n={len(flooded_durations)})",
            color="#d62728",
        )
    if baseline_durations:
        ax.axvline(float(np.mean(baseline_durations)), color="#1f77b4", linestyle="--", linewidth=1.5)
    if flooded_durations:
        ax.axvline(float(np.mean(flooded_durations)), color="#d62728", linestyle="--", linewidth=1.5)

    ax.set_xlabel("Trip travel time (s)")
    ax.set_ylabel("Density")
    ax.set_title(title or "Travel-time distribution: baseline vs flooded")
    ax.legend()
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def make_metrics_dir(label: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = label.replace("/", "_").replace("\\", "_")
    run_dir = RUNS_DIR / f"{ts}_metrics_{safe_label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def compute_and_write(baseline_dir: Path, flooded_dir: Path, out_dir: Optional[Path] = None) -> Path:
    """Full Slice 3 pipeline: compute metrics for a run pair and write
    ``metrics.json`` + ``trip_metrics.csv`` + ``travel_time_distribution.png``
    into ``out_dir`` (a fresh ``runs/<ts>_metrics_<scenario>/`` dir by
    default)."""
    baseline_dir = Path(baseline_dir)
    flooded_dir = Path(flooded_dir)
    metrics, per_trip = compute_metrics(baseline_dir, flooded_dir)

    if out_dir is None:
        label = metrics["scenario"].get("storm_scenario") or metrics["scenario"].get("flooded_scenario") or "scenario"
        out_dir = make_metrics_dir(label)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fig_path = plot_travel_time_distribution(
        per_trip,
        out_dir / "travel_time_distribution.png",
        title=f"Travel-time distribution: {metrics['scenario'].get('storm_scenario') or 'baseline vs flooded'}",
    )
    csv_path = write_trip_csv(per_trip, out_dir / "trip_metrics.csv")
    metrics["outputs"] = {
        "metrics_json": str(out_dir / "metrics.json"),
        "trip_metrics_csv": str(csv_path),
        "travel_time_distribution_png": str(fig_path),
    }
    with open(out_dir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    return out_dir


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Slice 3: compute baseline-vs-flooded metrics (travel-time delta, "
            "exposure, throughput, closure timeline) from an existing run pair "
            "produced by floodtwin.sim.runner, and write metrics.json + "
            "trip_metrics.csv + a travel-time-distribution figure."
        )
    )
    parser.add_argument("--baseline-dir", type=Path, required=True, help="baseline run artifact directory")
    parser.add_argument("--flooded-dir", type=Path, required=True, help="flooded run artifact directory (same demand+seed)")
    parser.add_argument("--out-dir", type=Path, default=None, help="output dir (default: runs/<ts>_metrics_<scenario>/)")
    args = parser.parse_args()

    out_dir = compute_and_write(args.baseline_dir, args.flooded_dir, out_dir=args.out_dir)
    metrics = json.load(open(out_dir / "metrics.json"))

    tt = metrics["travel_time"]
    exp = metrics["exposure"]
    thr = metrics["throughput"]
    print(f"Metrics written to {out_dir}")
    print(
        f"Travel time: baseline mean={tt['baseline_mean_travel_time_s']:.1f}s p95={tt['baseline_p95_travel_time_s']:.1f}s | "
        f"flooded mean={tt['flooded_mean_travel_time_s']:.1f}s p95={tt['flooded_p95_travel_time_s']:.1f}s | "
        f"delta mean={tt['mean_delta_s']:.1f}s p95={tt['p95_delta_s']:.1f}s (n_matched={tt['n_matched']})"
    )
    print(
        f"Exposure: {exp['n_exposed_closed_edge']}/{exp['total_vehicles_with_recorded_route']} "
        f"({exp['pct_exposed_closed_edge']:.1f}%) vehicles touched a closed edge"
    )
    print(f"Throughput: baseline arrived={thr['baseline_arrived']}, flooded arrived={thr['flooded_arrived']}")
    for w in metrics["vehicle_id_matching"]["warnings"]:
        print(f"WARNING: {w}")


if __name__ == "__main__":
    main()
