"""Slice 8 analysis: sensitivity / robustness sweep (PROJECT_PLAN.md SG4
Slice 8, "does the headline finding survive varying this assumption").

Six axes, each varied one-at-a-time against the Slices 1-7 default (v1
checkpoint, 300mm closure threshold, max edge-depth aggregation, 120s
rerouting period, DEPTH_SCALE_M=1.0), holding the scenario
(``Sep_30_2022_74.75``) and rerouting fraction (100% -- the headline
convention used by Slice 3's first figure and Slice 4/7's sweep tables, see
``DEFAULT_REROUTING_FRACTION_PCT``) fixed:

    1. Closure threshold (200/300/400mm)          -- ``closure_threshold_mm``
    2. Edge-depth aggregation (max/p95)            -- ``aggregation``
    3. Rerouting period (60/120/300s)              -- ``rerouting_period_s``
    4. Seeds (42/43/44)                            -- ``seed``
    5. DEPTH_SCALE_M (0.5/1.0/2.0)                 -- ``depth_scale_m``
    6. Flood-model checkpoint (v1 vs v4)           -- ``variant``/``run_name``

Every axis parameter (1/2/3/5) is a keyword argument added directly to
``floodtwin.coupling.edge_mapper``/``floodtwin.sim.runner`` in this same
slice (default = the Slices 1-7 hardcoded value, so no other caller is
affected). Axis 6 (checkpoint) and the rerouting-fraction/demand-variant
knobs were already parameterized since Slice 2/4/7 -- this module is pure
orchestration + reporting on top of existing plumbing, per PROJECT_PLAN.md
Slice 8's framing ("this is a parameter sweep ... not the demand layer").

Design note -- pure vs I/O, following ``floodtwin.analysis.sweep``'s
pattern: :func:`closure_stats_from_per_frame`, :func:`sensitivity_row`,
:func:`default_points` are pure/unit-testable without SUMO/TF (see
``tests/test_sensitivity.py``). Only :func:`run_sensitivity_point` /
:func:`run_sensitivity` (and the lazy ``floodtwin.sim.runner`` import inside
them) touch SUMO -- importing this module at the top of a test file must
stay CI-safe.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from floodtwin.analysis.metrics import compute_metrics
from floodtwin.sim.paths import RUNS_DIR

# Scenario + headline rerouting fraction: PROJECT_PLAN.md Slice 8 says to use
# "the same scenario (Sep_30_2022_74.75) and a representative rerouting
# fraction (100%, matching Slice 3's headline comparison, or whatever the
# established 'headline' convention is)". Slice 3's first figure, Slice 4's
# sweep table, and Slice 7's calibrated-demand comparison all report/anchor
# on the 100% rerouting-fraction point (D5's original Slices-1-2 default,
# preserved as ``floodtwin.sim.runner.DEFAULT_REROUTING_FRACTION``) -- see
# PROGRESS.md's Slice 3/4/7 notes. Reused here as the fixed point this whole
# sweep holds constant.
DEFAULT_SCENARIO = "Sep_30_2022_74.75"
DEFAULT_REROUTING_FRACTION_PCT = 100
DEFAULT_DEMAND_VARIANT = "v1"  # Slices 1-7 default (floodtwin.sim.paths.DEFAULT_DEMAND_VARIANT)
DEFAULT_SEED = 42

# Slices 1-7 default settings for every Slice-8 axis -- the "everything else
# held at baseline" reference point PROJECT_PLAN.md Slice 8 asks for.
DEFAULT_VARIANT = "v1"
DEFAULT_RUN_NAME = "v1_random_s42"
DEFAULT_CLOSURE_THRESHOLD_MM = 300.0
DEFAULT_AGGREGATION = "max"
DEFAULT_REROUTING_PERIOD_S = 120
DEFAULT_DEPTH_SCALE_M = 1.0


@dataclass
class SensitivityPoint:
    """One sensitivity-sweep point: an ``axis`` name, a human-readable
    ``label``, and every parameter needed to reproduce the run pair. Every
    field defaults to the Slices 1-7 baseline value, so a point only needs to
    override the one axis it's testing."""

    axis: str
    label: str
    seed: int = DEFAULT_SEED
    variant: str = DEFAULT_VARIANT
    run_name: str = DEFAULT_RUN_NAME
    closure_threshold_mm: float = DEFAULT_CLOSURE_THRESHOLD_MM
    aggregation: str = DEFAULT_AGGREGATION
    rerouting_period_s: int = DEFAULT_REROUTING_PERIOD_S
    depth_scale_m: float = DEFAULT_DEPTH_SCALE_M
    is_baseline_row: bool = False


def default_points() -> List[SensitivityPoint]:
    """The Slice 8 sensitivity grid: one baseline (all-default) point, plus
    two points per axis (the axis's alternative values) except aggregation
    (max is already the baseline; only p95 is new) and checkpoint (v1 is
    already the baseline; only v4 is new). 11 points total -- see the module
    docstring's axis list."""
    return [
        SensitivityPoint(
            axis="baseline",
            label="baseline (v1, 300mm, max, 120s period, scale=1.0, seed=42)",
            is_baseline_row=True,
        ),
        # 1. Closure threshold (D3 sensitivity axis: 20/30/40 cm).
        SensitivityPoint(axis="closure_threshold", label="200mm (20cm)", closure_threshold_mm=200.0),
        SensitivityPoint(axis="closure_threshold", label="400mm (40cm)", closure_threshold_mm=400.0),
        # 2. Edge-depth aggregation (D4 sensitivity axis: max vs p95).
        SensitivityPoint(axis="aggregation", label="p95", aggregation="p95"),
        # 3. Rerouting period (IMPLEMENTATION_CONTEXT.md #3 sensitivity axis).
        SensitivityPoint(axis="rerouting_period", label="60s", rerouting_period_s=60),
        SensitivityPoint(axis="rerouting_period", label="300s", rerouting_period_s=300),
        # 4. Seeds -- reuses the baseline row's seed=42 point; adds 43/44 to
        # match Slice 4/7's 3-seed convention (seeds_for_sweep(3, 42)).
        SensitivityPoint(axis="seed", label="seed=43", seed=43),
        SensitivityPoint(axis="seed", label="seed=44", seed=44),
        # 5. DEPTH_SCALE_M (Risk R7 / Open Question Q2, still open).
        SensitivityPoint(axis="depth_scale", label="0.5", depth_scale_m=0.5),
        SensitivityPoint(axis="depth_scale", label="2.0", depth_scale_m=2.0),
        # 6. Flood-model checkpoint: v1 (recall-optimized, default) vs v4
        # (balanced) -- IMPLEMENTATION_CONTEXT.md asset inventory.
        SensitivityPoint(
            axis="checkpoint", label="v4 (balanced)", variant="v4", run_name="v4_random_s42"
        ),
    ]


# ---------------------------------------------------------------------------
# Pure: per-mark closure-rate summary + row construction
# ---------------------------------------------------------------------------


def closure_stats_from_per_frame(per_frame_summary: Sequence[dict]) -> dict:
    """Summarize a flooded run's ``per_frame_summary`` (4 entries, one per
    15-min mark, each carrying ``n_closed``/``n_slowed``/``n_full_speed`` --
    see ``floodtwin.sim.runner.run_flooded_multiframe``) into a single
    mean/max percent-of-edges-closed pair. Mean is the headline number (a
    run's typical closure extent over its hour); max is the worst single
    mark (peak disruption), since a mean can hide a sharp mid-run spike."""
    pct_per_mark: List[float] = []
    for frame in per_frame_summary:
        total = (frame.get("n_closed") or 0) + (frame.get("n_slowed") or 0) + (frame.get("n_full_speed") or 0)
        if total > 0:
            pct_per_mark.append(100.0 * frame.get("n_closed", 0) / total)
    if not pct_per_mark:
        return {"mean_pct_edges_closed": None, "max_pct_edges_closed": None, "n_marks": 0}
    return {
        "mean_pct_edges_closed": sum(pct_per_mark) / len(pct_per_mark),
        "max_pct_edges_closed": max(pct_per_mark),
        "n_marks": len(pct_per_mark),
    }


def sensitivity_row(
    point: SensitivityPoint,
    metrics: dict,
    baseline_dir: "Path | str",
    flooded_dir: "Path | str",
    rerouting_fraction_pct: float = DEFAULT_REROUTING_FRACTION_PCT,
    demand_variant: str = DEFAULT_DEMAND_VARIANT,
    scenario_name: str = DEFAULT_SCENARIO,
) -> dict:
    """Build one sensitivity-table row from a run pair's already-computed
    Slice 3 metrics dict (``floodtwin.analysis.metrics.compute_metrics``'s
    first return value) plus the ``SensitivityPoint`` that produced it. Pure
    dict-reshaping -- no filesystem access -- mirroring
    ``floodtwin.analysis.sweep.sweep_point_row``."""
    tt = metrics.get("travel_time", {})
    exp = metrics.get("exposure", {})
    thr = metrics.get("throughput", {})
    rh = metrics.get("run_health", {})
    valid = metrics.get("run_valid", {})
    closure = closure_stats_from_per_frame(metrics.get("closure_timeline") or [])

    baseline_health = rh.get("baseline") or {}
    flooded_health = rh.get("flooded") or {}
    baseline_valid = valid.get("baseline")
    flooded_valid = valid.get("flooded")
    run_pair_valid = bool(baseline_valid) and bool(flooded_valid)

    return {
        "axis": point.axis,
        "label": point.label,
        "is_baseline_row": point.is_baseline_row,
        "scenario": scenario_name,
        "rerouting_fraction_pct": rerouting_fraction_pct,
        "demand_variant": demand_variant,
        "seed": point.seed,
        "checkpoint_variant": point.variant,
        "checkpoint_run_name": point.run_name,
        "closure_threshold_mm": point.closure_threshold_mm,
        "aggregation": point.aggregation,
        "rerouting_period_s": point.rerouting_period_s,
        "depth_scale_m": point.depth_scale_m,
        "mean_travel_time_delta_s": tt.get("mean_delta_s"),
        "p95_travel_time_delta_s": tt.get("p95_delta_s"),
        "n_matched_trips": tt.get("n_matched"),
        "mean_pct_edges_closed": closure["mean_pct_edges_closed"],
        "max_pct_edges_closed": closure["max_pct_edges_closed"],
        "pct_exposed_closed_edge": exp.get("pct_exposed_closed_edge"),
        "baseline_arrived": thr.get("baseline_arrived"),
        "flooded_arrived": thr.get("flooded_arrived"),
        "delta_arrived": thr.get("delta_arrived"),
        "baseline_teleports": baseline_health.get("teleports"),
        "flooded_teleports": flooded_health.get("teleports"),
        "baseline_collisions": baseline_health.get("collisions"),
        "flooded_collisions": flooded_health.get("collisions"),
        "baseline_run_valid": baseline_valid,
        "flooded_run_valid": flooded_valid,
        "run_pair_valid": run_pair_valid,
        "baseline_run_dir": str(baseline_dir),
        "flooded_run_dir": str(flooded_dir),
        "error": None,
    }


def error_row(
    point: SensitivityPoint,
    error: str,
    rerouting_fraction_pct: float = DEFAULT_REROUTING_FRACTION_PCT,
    demand_variant: str = DEFAULT_DEMAND_VARIANT,
    scenario_name: str = DEFAULT_SCENARIO,
) -> dict:
    """Build a sensitivity-table row for a point whose run pair could not be
    produced at all -- e.g. an infrastructure crash unrelated to this
    slice's own code (see the module docstring's fault-tolerance note). Same
    field shape as :func:`sensitivity_row` so both kinds of row write to the
    same CSV/JSON/markdown, but every metric field is ``None`` (never a
    fabricated/interpolated number -- PROJECT_PLAN.md Slice 8's data-integrity
    standard, same as Slice 7's) and ``error`` carries the real exception
    message so the failure reason is visible in the table itself, not
    silently dropped or conflated with an ordinary "run was unhealthy"
    result (``run_pair_valid=False`` covers both cases; ``error`` set is what
    distinguishes 'never ran' from 'ran but was invalid')."""
    return {
        "axis": point.axis,
        "label": point.label,
        "is_baseline_row": point.is_baseline_row,
        "scenario": scenario_name,
        "rerouting_fraction_pct": rerouting_fraction_pct,
        "demand_variant": demand_variant,
        "seed": point.seed,
        "checkpoint_variant": point.variant,
        "checkpoint_run_name": point.run_name,
        "closure_threshold_mm": point.closure_threshold_mm,
        "aggregation": point.aggregation,
        "rerouting_period_s": point.rerouting_period_s,
        "depth_scale_m": point.depth_scale_m,
        "mean_travel_time_delta_s": None,
        "p95_travel_time_delta_s": None,
        "n_matched_trips": None,
        "mean_pct_edges_closed": None,
        "max_pct_edges_closed": None,
        "pct_exposed_closed_edge": None,
        "baseline_arrived": None,
        "flooded_arrived": None,
        "delta_arrived": None,
        "baseline_teleports": None,
        "flooded_teleports": None,
        "baseline_collisions": None,
        "flooded_collisions": None,
        "baseline_run_valid": None,
        "flooded_run_valid": None,
        "run_pair_valid": False,
        "baseline_run_dir": None,
        "flooded_run_dir": None,
        "error": error,
    }


SENSITIVITY_CSV_FIELDNAMES = [
    "axis",
    "label",
    "is_baseline_row",
    "scenario",
    "rerouting_fraction_pct",
    "demand_variant",
    "seed",
    "checkpoint_variant",
    "checkpoint_run_name",
    "closure_threshold_mm",
    "aggregation",
    "rerouting_period_s",
    "depth_scale_m",
    "mean_travel_time_delta_s",
    "p95_travel_time_delta_s",
    "n_matched_trips",
    "mean_pct_edges_closed",
    "max_pct_edges_closed",
    "pct_exposed_closed_edge",
    "baseline_arrived",
    "flooded_arrived",
    "delta_arrived",
    "baseline_teleports",
    "flooded_teleports",
    "baseline_collisions",
    "flooded_collisions",
    "baseline_run_valid",
    "flooded_run_valid",
    "run_pair_valid",
    "baseline_run_dir",
    "flooded_run_dir",
    "error",
]


# ---------------------------------------------------------------------------
# I/O: run orchestration, CSV/JSON/markdown output
# ---------------------------------------------------------------------------


def run_sensitivity_point(
    point: SensitivityPoint,
    scenario_name: str = DEFAULT_SCENARIO,
    rerouting_fraction_pct: float = DEFAULT_REROUTING_FRACTION_PCT,
    demand_variant: str = DEFAULT_DEMAND_VARIANT,
) -> dict:
    """Run one sensitivity point: a matched baseline + flooded run pair at
    this point's parameters, scored with Slice 3's ``compute_metrics``.
    Returns a :func:`sensitivity_row`.

    Lazily imports ``floodtwin.sim.runner`` (which imports ``sumolib`` at
    module load) so this module stays importable -- and its pure functions
    testable -- without a SUMO install, matching
    ``floodtwin.analysis.sweep.run_sweep_point``'s pattern.
    """
    from floodtwin.sim import runner  # lazy: sumolib import, see module docstring

    fraction = rerouting_fraction_pct / 100.0
    baseline_dir = runner.run_baseline(
        seed=point.seed,
        rerouting_fraction=fraction,
        demand_variant=demand_variant,
        rerouting_period_s=point.rerouting_period_s,
    )
    try:
        flooded_dir = runner.run_flooded_multiframe(
            scenario_name=scenario_name,
            seed=point.seed,
            variant=point.variant,
            run_name=point.run_name,
            rerouting_fraction=fraction,
            demand_variant=demand_variant,
            rerouting_period_s=point.rerouting_period_s,
            closure_threshold_mm=point.closure_threshold_mm,
            depth_scale_m=point.depth_scale_m,
            aggregation=point.aggregation,
        )
    except Exception as exc:
        # Preserve that the baseline half of this pair *did* succeed (its run
        # dir is still on disk) in the error message before re-raising --
        # run_sensitivity's caller only sees the exception text, not this
        # function's locals, so this is the one place that context can be
        # attached without restructuring the caller's try/except.
        raise RuntimeError(
            f"flooded run failed for axis={point.axis!r} label={point.label!r} "
            f"(baseline run at {baseline_dir} succeeded): {exc}"
        ) from exc
    metrics, _per_trip = compute_metrics(baseline_dir, flooded_dir)
    return sensitivity_row(
        point, metrics, baseline_dir, flooded_dir,
        rerouting_fraction_pct=rerouting_fraction_pct, demand_variant=demand_variant,
        scenario_name=scenario_name,
    )


def write_sensitivity_csv(rows: List[dict], path: "Path | str") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SENSITIVITY_CSV_FIELDNAMES)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in SENSITIVITY_CSV_FIELDNAMES})
    return path


def _fmt(v, spec: str = ".1f") -> str:
    if v is None:
        return "n/a"
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return str(v)


def _sanitize_cell(text: str, max_len: int = 200) -> str:
    """Make arbitrary text (e.g. a raw exception message) safe to embed in a
    single markdown table cell: collapse newlines/pipes (either would break
    the table's row structure) and cap length so one long stack trace can't
    blow out the whole table's readability."""
    flat = " ".join(str(text).split())  # collapses all whitespace incl. newlines
    flat = flat.replace("|", "/")
    if len(flat) > max_len:
        flat = flat[: max_len - 3] + "..."
    return flat


def render_sensitivity_markdown(rows: List[dict], title: Optional[str] = None) -> str:
    """One markdown table for the paper's limitations section (PROJECT_PLAN.md
    Slice 8 demo criterion), grouped by axis with the baseline row first.

    A point whose run pair couldn't be produced at all (:func:`error_row`,
    e.g. an infrastructure crash) is shown with every metric column as
    ``n/a`` and its real failure reason in the ``notes`` column -- never
    silently dropped and never a fabricated number (PROJECT_PLAN.md Slice 8's
    data-integrity standard). An ordinary run-health failure (teleports > 0,
    ``error`` unset) is flagged **NO** with no fabricated notes text.

    Pure string-building -- no filesystem access."""
    lines = []
    lines.append(f"# {title or 'Slice 8 sensitivity table'}")
    lines.append("")
    lines.append(
        "| axis | point | mean travel-time delta (s) | p95 travel-time delta (s) | "
        "mean % closed | max % closed | % exposed | teleports (b/f) | collisions (b/f) | valid | notes |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        error = r.get("error")
        if error:
            valid_mark = "**ERROR**"
            notes = _sanitize_cell(error)
        elif r.get("run_pair_valid"):
            valid_mark = "yes"
            notes = ""
        else:
            valid_mark = "**NO**"
            notes = "run completed but was flagged invalid (see teleports/collisions)"
        lines.append(
            f"| {r['axis']} | {r['label']} "
            f"| {_fmt(r.get('mean_travel_time_delta_s'))} "
            f"| {_fmt(r.get('p95_travel_time_delta_s'))} "
            f"| {_fmt(r.get('mean_pct_edges_closed'))} "
            f"| {_fmt(r.get('max_pct_edges_closed'))} "
            f"| {_fmt(r.get('pct_exposed_closed_edge'))} "
            f"| {r.get('baseline_teleports')}/{r.get('flooded_teleports')} "
            f"| {r.get('baseline_collisions')}/{r.get('flooded_collisions')} "
            f"| {valid_mark} "
            f"| {notes} |"
        )
    return "\n".join(lines) + "\n"


def write_sensitivity_markdown(rows: List[dict], path: "Path | str", title: Optional[str] = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Explicit utf-8 (not the platform default -- Windows' cp1252 default
    # can't encode every character a future writeup might use here).
    path.write_text(render_sensitivity_markdown(rows, title=title), encoding="utf-8")
    return path


def make_sensitivity_dir(label: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = label.replace("/", "_").replace("\\", "_")
    run_dir = RUNS_DIR / "sensitivity" / f"{ts}_{safe_label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def run_sensitivity(
    scenario_name: str = DEFAULT_SCENARIO,
    points: Optional[Sequence[SensitivityPoint]] = None,
    rerouting_fraction_pct: float = DEFAULT_REROUTING_FRACTION_PCT,
    demand_variant: str = DEFAULT_DEMAND_VARIANT,
    out_dir: Optional["Path | str"] = None,
) -> Path:
    """Full Slice 8 pipeline: run every sensitivity point (:func:`default_points`
    by default), and write ``sensitivity_results.csv`` + ``sensitivity_summary.json``
    + ``sensitivity_table.md`` into a fresh ``runs/sensitivity/<ts>_<scenario>/``
    dir (default) or ``out_dir``.

    Fault-tolerant per point (this is a *robustness* analysis tool; a single
    point's failure taking down the whole table would itself be a robustness
    bug -- discovered in practice when the checkpoint axis hit a real,
    diagnosed infrastructure crash unrelated to this slice's own code, a
    Python-3.13/numpy native access violation in the TF inference subprocess,
    while every other point in the same sweep ran cleanly). Each point's
    :func:`run_sensitivity_point` call is wrapped in a try/except: on
    failure, an :func:`error_row` is recorded (axis/label intact, the real
    exception message, every metric ``None`` -- never fabricated) and the
    sweep continues to the next point rather than losing every already-
    completed row. The CSV/JSON/markdown are also rewritten after *every*
    point (not just at the end), so a run killed externally mid-sweep (e.g.
    a session boundary -- see PROGRESS.md's Slice 4/7 notes on long-running
    batch steps) still leaves every completed point's results on disk.
    """
    points = list(points) if points is not None else default_points()
    out_dir = Path(out_dir) if out_dir is not None else make_sensitivity_dir(scenario_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    title = f"Slice 8 sensitivity table: {scenario_name} (rerouting={rerouting_fraction_pct}%, demand={demand_variant})"

    rows: List[dict] = []
    for i, point in enumerate(points, 1):
        print(f"[{i}/{len(points)}] axis={point.axis} label={point.label!r} ...")
        try:
            row = run_sensitivity_point(
                point, scenario_name=scenario_name,
                rerouting_fraction_pct=rerouting_fraction_pct, demand_variant=demand_variant,
            )
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: a
            # single point's infrastructure failure (subprocess crash, missing
            # weights file, etc.) must not take down the whole sensitivity
            # table -- see the docstring above.
            error_msg = f"{type(exc).__name__}: {exc}"
            print(f"    ERRORED: {error_msg}")
            row = error_row(
                point, error_msg, rerouting_fraction_pct=rerouting_fraction_pct,
                demand_variant=demand_variant, scenario_name=scenario_name,
            )
        else:
            print(
                f"    mean_delta={_fmt(row['mean_travel_time_delta_s'])}s "
                f"p95_delta={_fmt(row['p95_travel_time_delta_s'])}s "
                f"mean_pct_closed={_fmt(row['mean_pct_edges_closed'])}% "
                f"valid={row['run_pair_valid']}"
            )
        rows.append(row)
        # Rewrite after every point (not just at the end) so a mid-sweep
        # kill still leaves completed points on disk.
        write_sensitivity_csv(rows, out_dir / "sensitivity_results.csv")
        write_sensitivity_markdown(rows, out_dir / "sensitivity_table.md", title=title)

    csv_path = write_sensitivity_csv(rows, out_dir / "sensitivity_results.csv")
    md_path = write_sensitivity_markdown(rows, out_dir / "sensitivity_table.md", title=title)

    summary = {
        "generated_at": datetime.now().isoformat(),
        "scenario": scenario_name,
        "rerouting_fraction_pct": rerouting_fraction_pct,
        "demand_variant": demand_variant,
        "n_points": len(rows),
        "n_errored": sum(1 for r in rows if r.get("error")),
        "n_valid": sum(1 for r in rows if r.get("run_pair_valid")),
        "points": rows,
        "axes": (
            "closure_threshold_mm {200,300,400}; aggregation {max,p95}; "
            "rerouting_period_s {60,120,300}; seed {42,43,44}; "
            "depth_scale_m {0.5,1.0,2.0}; checkpoint {v1_random_s42,v4_random_s42}"
        ),
        "outputs": {
            "sensitivity_results_csv": str(csv_path),
            "sensitivity_summary_json": str(out_dir / "sensitivity_summary.json"),
            "sensitivity_table_md": str(md_path),
        },
    }
    with open(out_dir / "sensitivity_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\nSensitivity sweep complete ({len(rows)} points) -> {out_dir}")
    return out_dir


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Slice 8: sensitivity/robustness sweep over closure threshold, edge-depth "
            "aggregation, rerouting period, seeds, DEPTH_SCALE_M, and flood-model "
            "checkpoint, holding scenario + rerouting fraction fixed at the headline "
            "point. Writes sensitivity_results.csv + sensitivity_summary.json + "
            "sensitivity_table.md into runs/sensitivity/<ts>_<scenario>/."
        )
    )
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--rerouting-fraction-pct", type=float, default=DEFAULT_REROUTING_FRACTION_PCT)
    parser.add_argument("--demand", default=DEFAULT_DEMAND_VARIANT, choices=["v1", "calibrated_v2"])
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    run_sensitivity(
        scenario_name=args.scenario,
        rerouting_fraction_pct=args.rerouting_fraction_pct,
        demand_variant=args.demand,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()
