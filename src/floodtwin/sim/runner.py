"""Run orchestration.

Slice 1: a baseline run (no closures) and a flooded run (one precomputed
flood frame's edge closures applied via TraCI at t=15min). Kept below as
``run_flooded`` / ``compute_edge_states`` for backward compatibility.

Slice 2: the full 60-minute coupling -- ``run_flooded_multiframe`` runs (or
reuses a cached) ``flood_runner`` forecast, maps all four frames to per-edge
Pregnolato speeds/closures, and applies them via TraCI at all four 15-min
marks. This is what ``main()`` / the CLI now runs.

Every run is written as a run artifact directory per PROJECT_PLAN.md #2.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from floodtwin.sumo_env import ensure_sumo_tools_on_path, sumo_binary

ensure_sumo_tools_on_path()
import sumolib  # noqa: E402

from floodtwin.coupling import georef
from floodtwin.coupling.edge_mapper import (
    CLOSURE_THRESHOLD_MM,
    DEPTH_SCALE_M,
    closed_edges,
    sample_edge_depths,
    speeds_and_closures,
)
from floodtwin.flood import paths as flood_paths
from floodtwin.sim import artifact, paths
from floodtwin.sim.controller import run_with_closures, run_with_edge_states

CLOSURE_TIME_S = 900.0  # t = 15 min -- the model's first output frame (D3/D4)
DEFAULT_SEED = 42
FRAME_INDEX = 0  # index 0 of the (128,128,4) stack = t+15 min

# --- Slice 2: full 60-minute coupling -------------------------------------
FRAME_MARKS_S = (900.0, 1800.0, 2700.0, 3600.0)  # t+15/30/45/60 min
FRAME_LABELS = ("t+15min", "t+30min", "t+45min", "t+60min")
# Same Sep 30 2022 event Slice 1 used (continuity per Plan OQ2), but now the
# scenario *name* used to look up an INPUT frame for real inference -- not a
# path to a precomputed output frame (Slice 1's DEFAULT_SCENARIO).
DEFAULT_SCENARIO_NAME = "Sep_30_2022_74.75"


def _base_sumo_cmd(seed: int, extra_outputs: dict) -> List[str]:
    cmd = [
        sumo_binary("sumo"),
        "--net-file", str(paths.NET_FILE),
        "--route-files", str(paths.ROUTE_FILE),
        "--begin", "0",
        "--end", str(paths.SIM_END_S),
        "--seed", str(seed),
        "--time-to-teleport", "-1",
        "--device.rerouting.probability", "1.0",
        "--device.rerouting.period", "120",
        "--no-step-log", "true",
        # A small-district flood closure can isolate a pocket of edges with
        # no remaining path to a vehicle's destination (observed with this
        # scenario: 29/60 in-domain edges close at once). Without this flag
        # SUMO aborts the *entire* run on the first such vehicle; with it,
        # that one vehicle is discarded (recorded in tripinfo) and the run
        # continues -- the correct behavior for a district-scale closure
        # experiment. Identical flag on both runs for a fair comparison.
        "--ignore-route-errors", "true",
    ]
    for flag, out_path in extra_outputs.items():
        cmd += [flag, str(out_path)]
    return cmd


def compute_edge_states(net, scenario_npy: Path, frame_index: int = FRAME_INDEX):
    stack = np.load(scenario_npy)
    depth_grid = stack[:, :, frame_index]
    depths = sample_edge_depths(net, depth_grid)
    closed = closed_edges(depths)
    return depths, closed


def run_baseline(seed: int = DEFAULT_SEED) -> Path:
    """Plain (no-TraCI) baseline run: same net/demand/rerouting settings as
    the flooded run, but no edge closures."""
    run_dir = artifact.make_run_dir("baseline")
    outputs = {
        "--fcd-output": run_dir / "fcd.xml",
        "--tripinfo-output": run_dir / "tripinfo.xml",
        "--summary-output": run_dir / "summary.xml",
        "--vehroute-output": run_dir / "vehroutes.xml",
    }
    cmd = _base_sumo_cmd(seed, outputs)

    result = subprocess.run(cmd, capture_output=True, text=True)
    (run_dir / "sumo_stdout.log").write_text(result.stdout)
    (run_dir / "sumo_stderr.log").write_text(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"baseline sumo run failed (exit {result.returncode}); see {run_dir / 'sumo_stderr.log'}"
        )

    health = artifact.parse_run_health(outputs["--summary-output"])
    reroute = artifact.parse_reroute_stats(outputs["--tripinfo-output"])

    config = {
        "scenario": "baseline_no_flood",
        "net_file": str(paths.NET_FILE),
        "route_file": str(paths.ROUTE_FILE),
        "seed": seed,
        "begin_s": 0,
        "end_s": paths.SIM_END_S,
        "rerouting_probability": 1.0,
        "rerouting_period_s": 120,
        "time_to_teleport": -1,
        "closures": [],
        "closure_time_s": None,
        "run_health": health,
        "reroute_stats": reroute,
    }
    artifact.write_config(run_dir, config)
    return run_dir


def run_flooded(
    scenario_npy: Path = paths.DEFAULT_SCENARIO,
    seed: int = DEFAULT_SEED,
    frame_index: int = FRAME_INDEX,
    closure_time_s: float = CLOSURE_TIME_S,
) -> Path:
    """TraCI-controlled run: same net/demand/rerouting settings as the
    baseline, plus edges closed at ``closure_time_s`` per the one flood
    frame's depth map."""
    run_dir = artifact.make_run_dir("flooded")
    net = sumolib.net.readNet(str(paths.NET_FILE))
    depths, closed = compute_edge_states(net, scenario_npy, frame_index)
    artifact.write_edge_state_table(run_dir, net, depths, closed)

    outputs = {
        "--fcd-output": run_dir / "fcd.xml",
        "--tripinfo-output": run_dir / "tripinfo.xml",
        "--summary-output": run_dir / "summary.xml",
        "--vehroute-output": run_dir / "vehroutes.xml",
    }
    cmd = _base_sumo_cmd(seed, outputs)

    closure_result = run_with_closures(cmd, closed, closure_time_s, float(paths.SIM_END_S))

    health = artifact.parse_run_health(outputs["--summary-output"])
    reroute = artifact.parse_reroute_stats(outputs["--tripinfo-output"])

    config = {
        "scenario": "flooded",
        "scenario_npy": str(scenario_npy),
        "frame_index": frame_index,
        "frame_label": "t+15min",
        "net_file": str(paths.NET_FILE),
        "route_file": str(paths.ROUTE_FILE),
        "seed": seed,
        "begin_s": 0,
        "end_s": paths.SIM_END_S,
        "rerouting_probability": 1.0,
        "rerouting_period_s": 120,
        "time_to_teleport": -1,
        "depth_scale_m": DEPTH_SCALE_M,
        "closure_threshold_mm": CLOSURE_THRESHOLD_MM,
        "closure_time_s": closure_time_s,
        "closures": sorted(closed),
        "n_closed_edges": len(closed),
        "closure_result": closure_result,
        "run_health": health,
        "reroute_stats": reroute,
    }
    artifact.write_config(run_dir, config)
    return run_dir


# ---------------------------------------------------------------------------
# Slice 2: full 60-minute coupling
# ---------------------------------------------------------------------------

def get_or_build_forecast(
    scenario_name: str,
    variant: str = flood_paths.DEFAULT_VARIANT,
    run_name: str = flood_paths.DEFAULT_RUN_NAME,
) -> Path:
    """Return the path to ``scenario_name``'s forecast NPZ (flood_runner's
    output), building it via a subprocess call to a Keras-3 interpreter if
    it isn't already cached.

    Deviation/engineering call (documented in the Slice 2 report): the
    repo's usual Python 3.8 interpreter has TF 2.13 / Keras 2.13, which
    predates ``keras.ops`` -- it cannot import ``flood_pipeline.py``
    (IMPLEMENTATION_CONTEXT.md G2 says the model was trained under "Keras 3
    + TF" on the cluster). Rather than rebuild the whole repo's env, this
    shells out to a separate Keras-3 interpreter
    (``floodtwin.flood.paths.tf_python()``) just for the inference step, and
    caches the result by (scenario, run_name) so repeated CLI invocations
    for the same scenario/checkpoint don't re-run TF inference (~30s of
    model load + forward pass) every time. SUMO/TraCI/coupling code never
    imports TensorFlow.
    """
    scenario_stem = scenario_name[:-4] if scenario_name.endswith(".npy") else scenario_name
    cached = flood_paths.cached_forecast_path(scenario_stem, run_name)
    if cached.exists():
        print(f"Using cached forecast: {cached}")
        return cached

    print(
        f"No cached forecast for {scenario_stem!r} (run={run_name!r}); "
        f"running flood_runner inference via {flood_paths.tf_python()} ..."
    )
    cmd = [
        flood_paths.tf_python(), "-m", "floodtwin.flood.flood_runner",
        "--scenario", scenario_stem,
        "--variant", variant,
        "--run-name", run_name,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"flood_runner inference failed (exit {result.returncode}):\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    print(result.stdout.strip())
    if not cached.exists():
        raise RuntimeError(f"flood_runner reported success but {cached} was not written")
    return cached


def load_forecast(npz_path: Path) -> Tuple[np.ndarray, georef.GeoTransform, dict]:
    """Load a flood_runner forecast NPZ: the (128,128,4) depth stack, its
    georeferencing transform, and provenance metadata (PROJECT_PLAN.md
    Slice 2: "georeferencing metadata so downstream code doesn't hardcode
    the grid bounds")."""
    data = np.load(npz_path)
    depth_stack = data["depth_stack"]
    transform = georef.GeoTransform(
        north=float(data["north"]),
        south=float(data["south"]),
        east=float(data["east"]),
        west=float(data["west"]),
        grid_size=int(data["grid_size"]),
    )
    meta = {
        "scenario": str(data["scenario"]),
        "input_npy": str(data["input_npy"]),
        "variant": str(data["variant"]),
        "run_name": str(data["run_name"]),
        "weights_path": str(data["weights_path"]),
        "generated_at": str(data["generated_at"]),
    }
    return depth_stack, transform, meta


def compute_multiframe_edge_states(
    net, depth_stack: np.ndarray, transform: georef.GeoTransform
) -> Tuple[Dict[float, Dict[str, float]], Dict[float, Dict[str, Tuple[float, bool]]], Dict[str, float]]:
    """Map all four depth-stack frames to per-edge Pregnolato
    speeds/closures at their 15-min marks (D3/D4, full form)."""
    speed_limits_ms: Dict[str, float] = {e.getID(): e.getSpeed() for e in net.getEdges()}
    edge_depths_by_mark: Dict[float, Dict[str, float]] = {}
    edge_states_by_mark: Dict[float, Dict[str, Tuple[float, bool]]] = {}
    for mark, frame_idx in zip(FRAME_MARKS_S, range(4)):
        depth_grid = depth_stack[:, :, frame_idx]
        depths = sample_edge_depths(net, depth_grid, transform=transform)
        states = speeds_and_closures(depths, speed_limits_ms)
        edge_depths_by_mark[mark] = depths
        edge_states_by_mark[mark] = states
    return edge_depths_by_mark, edge_states_by_mark, speed_limits_ms


def _frame_counts(
    states: Dict[str, Tuple[float, bool]], speed_limits_ms: Dict[str, float]
) -> Tuple[int, int, int]:
    n_closed = sum(1 for _, closed in states.values() if closed)
    n_slowed = sum(
        1
        for eid, (v, closed) in states.items()
        if not closed and v < speed_limits_ms.get(eid, v) - 1e-6
    )
    n_full_speed = len(states) - n_closed - n_slowed
    return n_closed, n_slowed, n_full_speed


def print_demo_table(
    edge_depths_by_mark: Dict[float, Dict[str, float]],
    edge_states_by_mark: Dict[float, Dict[str, Tuple[float, bool]]],
    speed_limits_ms: Dict[str, float],
) -> None:
    """PROJECT_PLAN.md Slice 2 demo: "a printed table of edges slowed/closed
    per timestep." """
    print("\nEdge states per 15-min mark:")
    print(f"{'frame':>10} {'mark_s':>8} {'closed':>8} {'slowed':>8} {'full_speed':>11}")
    for mark, label in zip(FRAME_MARKS_S, FRAME_LABELS):
        n_closed, n_slowed, n_full = _frame_counts(edge_states_by_mark[mark], speed_limits_ms)
        print(f"{label:>10} {int(mark):>8} {n_closed:>8} {n_slowed:>8} {n_full:>11}")

    touched = set()
    for depths in edge_depths_by_mark.values():
        touched.update(eid for eid, d in depths.items() if d > 0)
    if touched:
        print(
            f"\nPer-edge status (n={len(touched)} edges with any depth > 0 at "
            f"any mark), columns = {', '.join(FRAME_LABELS)}:"
        )
        print(f"{'edge_id':<20}" + "".join(f"{lbl:>16}" for lbl in FRAME_LABELS))
        for eid in sorted(touched):
            row = f"{eid:<20}"
            for mark in FRAME_MARKS_S:
                v, closed = edge_states_by_mark[mark].get(
                    eid, (speed_limits_ms.get(eid, 0.0), False)
                )
                cell = "CLOSED" if closed else f"{v:.1f} m/s"
                row += f"{cell:>16}"
            print(row)


def run_flooded_multiframe(
    scenario_name: str = DEFAULT_SCENARIO_NAME,
    seed: int = DEFAULT_SEED,
    variant: str = flood_paths.DEFAULT_VARIANT,
    run_name: str = flood_paths.DEFAULT_RUN_NAME,
) -> Path:
    """Slice 2: TraCI-controlled run with the full Pregnolato speed curve
    applied at all four 15-min marks (900/1800/2700/3600s), using a real
    ``flood_runner`` forecast (cached or freshly run) for ``scenario_name``.
    """
    run_dir = artifact.make_run_dir("flooded_multiframe")
    net = sumolib.net.readNet(str(paths.NET_FILE))

    forecast_npz = get_or_build_forecast(scenario_name, variant=variant, run_name=run_name)
    depth_stack, transform, forecast_meta = load_forecast(forecast_npz)

    edge_depths_by_mark, edge_states_by_mark, speed_limits_ms = compute_multiframe_edge_states(
        net, depth_stack, transform
    )
    artifact.write_multiframe_edge_state_table(run_dir, net, edge_depths_by_mark, edge_states_by_mark)

    outputs = {
        "--fcd-output": run_dir / "fcd.xml",
        "--tripinfo-output": run_dir / "tripinfo.xml",
        "--summary-output": run_dir / "summary.xml",
        "--vehroute-output": run_dir / "vehroutes.xml",
    }
    cmd = _base_sumo_cmd(seed, outputs)
    apply_result = run_with_edge_states(cmd, edge_states_by_mark, float(paths.SIM_END_S))

    health = artifact.parse_run_health(outputs["--summary-output"])
    reroute = artifact.parse_reroute_stats(outputs["--tripinfo-output"])
    # Plan R4: teleport count is a first-class run-health metric; any
    # teleport flags the run invalid (a teleport silently deletes a stuck
    # vehicle from the network mid-jam, which would understate congestion).
    run_valid = health.get("teleports", 0) == 0

    per_frame_summary = []
    for mark, label in zip(FRAME_MARKS_S, FRAME_LABELS):
        n_closed, n_slowed, n_full = _frame_counts(edge_states_by_mark[mark], speed_limits_ms)
        per_frame_summary.append(
            {
                "mark_s": mark,
                "label": label,
                "n_closed": n_closed,
                "n_slowed": n_slowed,
                "n_full_speed": n_full,
            }
        )

    config = {
        "scenario": "flooded_multiframe",
        "storm_scenario": scenario_name,
        "forecast_npz": str(forecast_npz),
        "forecast_meta": forecast_meta,
        "net_file": str(paths.NET_FILE),
        "route_file": str(paths.ROUTE_FILE),
        "seed": seed,
        "begin_s": 0,
        "end_s": paths.SIM_END_S,
        "rerouting_probability": 1.0,
        "rerouting_period_s": 120,
        "time_to_teleport": -1,
        "depth_scale_m": DEPTH_SCALE_M,
        "closure_threshold_mm": CLOSURE_THRESHOLD_MM,
        "frame_marks_s": list(FRAME_MARKS_S),
        "frame_labels": list(FRAME_LABELS),
        "per_frame_summary": per_frame_summary,
        "apply_result": apply_result,
        "run_health": health,
        "run_valid": run_valid,
        "reroute_stats": reroute,
    }
    if not run_valid:
        config["INVALID_RUN_WARNING"] = (
            f"{health.get('teleports')} teleport(s) occurred during this run. Per "
            "PROJECT_PLAN.md R4 this run is flagged INVALID -- a teleport silently "
            "removes a stuck vehicle from the network, which understates congestion. "
            "Do not use this run's metrics without investigating (e.g. raise "
            "--time-to-teleport further, or check for an isolated pocket of edges)."
        )
    artifact.write_config(run_dir, config)

    print_demo_table(edge_depths_by_mark, edge_states_by_mark, speed_limits_ms)
    print()
    status = "VALID" if run_valid else "INVALID -- see config.json INVALID_RUN_WARNING"
    print(
        f"Run health: teleports={health.get('teleports')}, "
        f"collisions={health.get('collisions')}, arrived={health.get('arrived')} -> {status}"
    )

    return run_dir


def main():
    parser = argparse.ArgumentParser(
        description="Run Slice 2 baseline + full 60-min Pregnolato-coupled flooded SUMO scenarios."
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO_NAME,
        help=(
            "Storm scenario name (e.g. Sep_30_2022_74.75), resolved to an Example "
            "Dataset INPUT frame. flood_runner inference is run once per "
            "(scenario, run-name) and cached under data/scenarios/."
        ),
    )
    parser.add_argument("--variant", default=flood_paths.DEFAULT_VARIANT)
    parser.add_argument("--run-name", default=flood_paths.DEFAULT_RUN_NAME)
    parser.add_argument(
        "--metrics",
        action="store_true",
        help=(
            "Slice 3: after producing the baseline/flooded run pair, compute "
            "travel-time/exposure/throughput/closure-timeline metrics and write "
            "metrics.json + trip_metrics.csv + a travel-time-distribution figure "
            "into a new runs/<ts>_metrics_<scenario>/ dir (see floodtwin.analysis.metrics)."
        ),
    )
    args = parser.parse_args()

    print("Running baseline (no closures)...")
    baseline_dir = run_baseline(seed=args.seed)
    print(f"  -> {baseline_dir}")

    print("\nRunning flooded (Pregnolato speeds/closures at 4x 15-min marks)...")
    flooded_dir = run_flooded_multiframe(
        scenario_name=args.scenario, seed=args.seed, variant=args.variant, run_name=args.run_name
    )
    print(f"  -> {flooded_dir}")

    if args.metrics:
        # Lazy import: metrics.py pulls in matplotlib (an "analysis"/"test"
        # extra, not a core dependency), and nothing else in this module
        # should require it just to run a simulation.
        from floodtwin.analysis.metrics import compute_and_write

        print("\nComputing Slice 3 metrics (travel time / exposure / throughput / closure timeline)...")
        metrics_dir = compute_and_write(baseline_dir, flooded_dir)
        print(f"  -> {metrics_dir}")


if __name__ == "__main__":
    main()
