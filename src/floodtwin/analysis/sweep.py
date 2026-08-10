"""Slice 4 analysis: the information sweep -- "how much does traveler
information mitigate flood disruption?" (PROJECT_PLAN.md SG2 Slice 4).

Sweeps rerouting fraction (D5's "0/25/50/75/100%" set, the fraction of
vehicles carrying SUMO's rerouting device) crossed with N random seeds. For
every ``(fraction, seed)`` point this reuses Slice 2's
``floodtwin.sim.runner.run_baseline`` / ``run_flooded_multiframe`` to produce
a run pair, then Slice 3's ``floodtwin.analysis.metrics.compute_metrics`` to
score it -- this module does not reinvent either.

Baseline-handling decision (documented per the Slice 4 task -- a real
methodological choice, not just an implementation detail):

    The baseline is re-run at **every** ``(fraction, seed)`` point, not once
    per seed. Reasoning: SUMO's rerouting device (``--device.rerouting``)
    recomputes routes periodically based on *current* edge travel times,
    which it does regardless of whether any edge is flood-closed -- with
    more vehicles carrying the device, more of them shift off locally
    congested edges even in a dry network. That means rerouting fraction is
    not a no-op on the no-flood case, so a single fixed-fraction baseline
    shared across the sweep would let a baseline-only effect (rerouting
    device behavior under normal congestion) leak into the reported
    "flooded - baseline" delta at every fraction except the one the shared
    baseline happened to use. Re-running the baseline at each fraction holds
    it constant *within* each comparison pair, so the reported delta at each
    sweep point isolates the marginal effect of the flood closures at that
    information level -- exactly the quantity the headline figure claims to
    show. The cost is 2x the run count (a baseline + a flooded run per
    point, both fast per Slice 2/3 experience), which is affordable at
    district scale.

Pure logic (:func:`sweep_point_row`, :func:`aggregate_sweep_results`,
:func:`seeds_for_sweep`) is unit-testable without SUMO/TensorFlow, per
``tests/test_sweep.py``. Only :func:`run_sweep_point` / :func:`run_sweep`
(and the lazy ``floodtwin.sim.runner`` import inside them) touch SUMO --
importing this module at the top of a test file must stay CI-safe.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from floodtwin.analysis.metrics import compute_metrics
from floodtwin.sim.paths import RUNS_DIR

# D5: the sweep set called out in PROJECT_PLAN.md Slice 4.
DEFAULT_FRACTIONS_PCT = (0, 25, 50, 75, 100)

# N seeds per fraction (CLI-configurable, per the Slice 4 task -- "your call
# on N ... document your choice"). 3 is chosen as a reasonable default: it's
# enough to see whether a trend is a real signal or seed noise (a mean +/-
# std band with 1 seed is meaningless; 3 gives a first-order variance
# estimate) while keeping a 5-fraction sweep to 5*3*2 = 30 total SUMO runs,
# comfortably inside "a few minutes" at the seconds-per-run pace observed in
# Slices 2-3. Bump via --n-seeds for the real paper run once this is wired
# up and the per-run cost is confirmed at whatever scenario/net is final.
DEFAULT_N_SEEDS = 3
DEFAULT_BASE_SEED = 42


def seeds_for_sweep(n_seeds: int = DEFAULT_N_SEEDS, base_seed: int = DEFAULT_BASE_SEED) -> List[int]:
    """Deterministic seed list: ``[base_seed, base_seed+1, ..., base_seed+n-1]``.
    Deterministic (not random) so re-running the sweep with the same
    ``--n-seeds``/``--base-seed`` reproduces the exact same runs."""
    return [base_seed + i for i in range(n_seeds)]


# ---------------------------------------------------------------------------
# Pure: per-point row construction + cross-seed aggregation
# ---------------------------------------------------------------------------


def sweep_point_row(
    fraction_pct: float,
    seed: int,
    baseline_dir: "Path | str",
    flooded_dir: "Path | str",
    metrics: dict,
) -> dict:
    """Build one ``sweep_results.csv`` row from a single ``(fraction, seed)``
    run pair's already-computed Slice 3 metrics dict
    (``floodtwin.analysis.metrics.compute_metrics``'s first return value).

    Pure dict-reshaping -- no filesystem access -- so it's unit-testable
    against a synthetic ``metrics`` dict shaped like ``compute_metrics``'s
    real output (see ``tests/test_metrics.py``'s ``test_compute_metrics_end_to_end``
    for that shape).
    """
    tt = metrics.get("travel_time", {})
    exp = metrics.get("exposure", {})
    thr = metrics.get("throughput", {})
    rh = metrics.get("run_health", {})
    valid = metrics.get("run_valid", {})
    return {
        "rerouting_fraction_pct": fraction_pct,
        "seed": seed,
        "mean_travel_time_delta_s": tt.get("mean_delta_s"),
        "p95_travel_time_delta_s": tt.get("p95_delta_s"),
        "n_matched_trips": tt.get("n_matched"),
        "n_exposed_closed_edge": exp.get("n_exposed_closed_edge"),
        "pct_exposed_closed_edge": exp.get("pct_exposed_closed_edge"),
        "n_exposed_wet_edge": exp.get("n_exposed_wet_edge"),
        "pct_exposed_wet_edge": exp.get("pct_exposed_wet_edge"),
        "baseline_arrived": thr.get("baseline_arrived"),
        "flooded_arrived": thr.get("flooded_arrived"),
        "delta_arrived": thr.get("delta_arrived"),
        "baseline_teleports": (rh.get("baseline") or {}).get("teleports"),
        "flooded_teleports": (rh.get("flooded") or {}).get("teleports"),
        "baseline_run_valid": valid.get("baseline"),
        "flooded_run_valid": valid.get("flooded"),
        "baseline_run_dir": str(baseline_dir),
        "flooded_run_dir": str(flooded_dir),
    }


SWEEP_CSV_FIELDNAMES = [
    "rerouting_fraction_pct",
    "seed",
    "mean_travel_time_delta_s",
    "p95_travel_time_delta_s",
    "n_matched_trips",
    "n_exposed_closed_edge",
    "pct_exposed_closed_edge",
    "n_exposed_wet_edge",
    "pct_exposed_wet_edge",
    "baseline_arrived",
    "flooded_arrived",
    "delta_arrived",
    "baseline_teleports",
    "flooded_teleports",
    "baseline_run_valid",
    "flooded_run_valid",
    "baseline_run_dir",
    "flooded_run_dir",
]


def _mean_std(values: Sequence[Optional[float]]):
    """Mean/sample-stdev over non-``None`` values. ``(None, None)`` if
    nothing usable; ``(value, 0.0)`` for a single observation (a lone seed
    has zero *observed* spread -- it's just not a meaningful variance
    estimate, which is why ``n_seeds`` is reported alongside so a caller/
    figure can flag it rather than the function inventing a fake NaN)."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None, None
    if len(vals) == 1:
        return vals[0], 0.0
    return float(statistics.mean(vals)), float(statistics.stdev(vals))


def aggregate_sweep_results(rows: Sequence[dict]) -> List[dict]:
    """Group per-``(fraction, seed)`` rows (:func:`sweep_point_row` output)
    by rerouting fraction and compute mean +/- std across seeds for the
    headline metrics. This is the pure aggregation/summary-statistics
    function the Slice 4 task asks to unit test without running SUMO.

    Returns one dict per distinct ``rerouting_fraction_pct`` present in
    ``rows``, sorted ascending by fraction.
    """
    by_fraction: Dict[float, List[dict]] = {}
    for row in rows:
        by_fraction.setdefault(row["rerouting_fraction_pct"], []).append(row)

    out: List[dict] = []
    for fraction in sorted(by_fraction):
        pts = by_fraction[fraction]
        mean_delta_mean, mean_delta_std = _mean_std([p["mean_travel_time_delta_s"] for p in pts])
        p95_delta_mean, p95_delta_std = _mean_std([p["p95_travel_time_delta_s"] for p in pts])
        pct_exposed_mean, pct_exposed_std = _mean_std([p["pct_exposed_closed_edge"] for p in pts])
        delta_arrived_mean, delta_arrived_std = _mean_std([p["delta_arrived"] for p in pts])
        n_invalid = sum(
            1
            for p in pts
            if p.get("baseline_run_valid") is False or p.get("flooded_run_valid") is False
        )
        out.append(
            {
                "rerouting_fraction_pct": fraction,
                "n_seeds": len(pts),
                "seeds": sorted(p["seed"] for p in pts),
                "mean_travel_time_delta_s_mean": mean_delta_mean,
                "mean_travel_time_delta_s_std": mean_delta_std,
                "p95_travel_time_delta_s_mean": p95_delta_mean,
                "p95_travel_time_delta_s_std": p95_delta_std,
                "pct_exposed_closed_edge_mean": pct_exposed_mean,
                "pct_exposed_closed_edge_std": pct_exposed_std,
                "delta_arrived_mean": delta_arrived_mean,
                "delta_arrived_std": delta_arrived_std,
                "n_invalid_runs": n_invalid,
            }
        )
    return out


# ---------------------------------------------------------------------------
# I/O: run orchestration, CSV/JSON/figure output
# ---------------------------------------------------------------------------


def run_sweep_point(
    fraction_pct: float,
    seed: int,
    scenario_name: str,
    variant: str,
    run_name: str,
) -> dict:
    """Run one ``(fraction, seed)`` point: a matched baseline + flooded run
    at this rerouting fraction and seed (see the module docstring for why
    the baseline is re-run per point, not shared across the sweep), scored
    with Slice 3's ``compute_metrics``. Returns a :func:`sweep_point_row`.

    Lazily imports ``floodtwin.sim.runner`` (which imports ``sumolib`` at
    module load) so this module stays importable -- and its pure functions
    testable -- without a SUMO install.
    """
    from floodtwin.sim import runner  # lazy: sumolib import, see module docstring

    fraction = fraction_pct / 100.0
    baseline_dir = runner.run_baseline(seed=seed, rerouting_fraction=fraction)
    flooded_dir = runner.run_flooded_multiframe(
        scenario_name=scenario_name,
        seed=seed,
        variant=variant,
        run_name=run_name,
        rerouting_fraction=fraction,
    )
    metrics, _per_trip = compute_metrics(baseline_dir, flooded_dir)
    return sweep_point_row(fraction_pct, seed, baseline_dir, flooded_dir, metrics)


def write_sweep_csv(rows: List[dict], path: "Path | str") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SWEEP_CSV_FIELDNAMES)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in SWEEP_CSV_FIELDNAMES})
    return path


def plot_sweep_figure(
    rows: List[dict], agg: List[dict], out_path: "Path | str", title: Optional[str] = None
) -> Path:
    """The Slice 4 headline figure: disruption metric(s) vs information
    level (rerouting fraction), with seed-variance error bars.

    Left panel headlines **mean travel-time delta** (flooded - baseline) --
    picked as the primary metric because it's the plan's own framing ("how
    much does traveler information mitigate flood disruption") in the most
    directly interpretable unit (seconds of extra travel time saved per
    trip on average); right panel shows p95 delta (tail/worst-case
    disruption) as the secondary metric, since mean and tail effects of
    rerouting can diverge (rerouting can cut the average while leaving a
    smaller set of badly-detoured trips). Faint scatter points show every
    individual seed so the error bars can be sanity-checked against the raw
    spread, not just trusted blindly.
    """
    import matplotlib

    matplotlib.use("Agg")  # headless-safe (CI, no display)
    import matplotlib.pyplot as plt

    fractions = [a["rerouting_fraction_pct"] for a in agg]
    mean_vals = [a["mean_travel_time_delta_s_mean"] for a in agg]
    mean_stds = [a["mean_travel_time_delta_s_std"] or 0.0 for a in agg]
    p95_vals = [a["p95_travel_time_delta_s_mean"] for a in agg]
    p95_stds = [a["p95_travel_time_delta_s_std"] or 0.0 for a in agg]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

    def _scatter_by_fraction(ax, key, color):
        by_frac: Dict[float, List[float]] = {}
        for r in rows:
            v = r.get(key)
            if v is not None:
                by_frac.setdefault(r["rerouting_fraction_pct"], []).append(v)
        for frac, vals in by_frac.items():
            ax.scatter([frac] * len(vals), vals, color=color, alpha=0.35, s=28, zorder=1, label=None)

    ax1.errorbar(
        fractions, mean_vals, yerr=mean_stds, marker="o", capsize=4, color="#d62728",
        linewidth=2, zorder=2, label="mean +/- std across seeds",
    )
    _scatter_by_fraction(ax1, "mean_travel_time_delta_s", "#d62728")
    ax1.axhline(0, color="gray", linewidth=0.8, linestyle=":")
    ax1.set_xlabel("Rerouting fraction (% of vehicles with traveler information)")
    ax1.set_ylabel("Mean travel-time delta, flooded - baseline (s)")
    ax1.set_title("Headline: mean travel-time delta vs information level")
    ax1.set_xticks(list(fractions))
    ax1.legend()

    ax2.errorbar(
        fractions, p95_vals, yerr=p95_stds, marker="s", capsize=4, color="#1f77b4",
        linewidth=2, zorder=2, label="mean +/- std across seeds",
    )
    _scatter_by_fraction(ax2, "p95_travel_time_delta_s", "#1f77b4")
    ax2.axhline(0, color="gray", linewidth=0.8, linestyle=":")
    ax2.set_xlabel("Rerouting fraction (% of vehicles with traveler information)")
    ax2.set_ylabel("p95 travel-time delta, flooded - baseline (s)")
    ax2.set_title("Secondary: p95 (tail) travel-time delta")
    ax2.set_xticks(list(fractions))
    ax2.legend()

    fig.suptitle(title or "Information sweep: does traveler information mitigate flood disruption?")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def make_sweep_dir(label: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = label.replace("/", "_").replace("\\", "_")
    run_dir = RUNS_DIR / f"{ts}_sweep_{safe_label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def run_sweep(
    scenario_name: str,
    fractions_pct: Sequence[float] = DEFAULT_FRACTIONS_PCT,
    n_seeds: int = DEFAULT_N_SEEDS,
    base_seed: int = DEFAULT_BASE_SEED,
    seeds: Optional[Sequence[int]] = None,
    variant: Optional[str] = None,
    run_name: Optional[str] = None,
    out_dir: Optional["Path | str"] = None,
) -> Path:
    """Full Slice 4 pipeline: run the fraction x seed grid, aggregate across
    seeds, and write ``sweep_results.csv`` + ``sweep_summary.json`` +
    ``rerouting_sweep.png`` into a fresh ``runs/<ts>_sweep_<scenario>/`` dir
    (default) or ``out_dir``.
    """
    # Lazy: only need these to fill in flood_paths defaults without forcing
    # a SUMO import at module load (flood_paths itself is TF/SUMO-free, but
    # keep the pattern consistent with run_sweep_point's lazy runner import).
    from floodtwin.flood import paths as flood_paths

    variant = variant or flood_paths.DEFAULT_VARIANT
    run_name = run_name or flood_paths.DEFAULT_RUN_NAME
    seed_list = list(seeds) if seeds is not None else seeds_for_sweep(n_seeds, base_seed)

    t0 = time.time()
    rows: List[dict] = []
    total = len(fractions_pct) * len(seed_list)
    n = 0
    for fraction_pct in fractions_pct:
        for seed in seed_list:
            n += 1
            print(f"[{n}/{total}] fraction={fraction_pct}% seed={seed} ...")
            row = run_sweep_point(fraction_pct, seed, scenario_name, variant, run_name)
            rows.append(row)
            mean_d = row["mean_travel_time_delta_s"]
            p95_d = row["p95_travel_time_delta_s"]
            print(
                f"    mean_delta={mean_d:.1f}s" if mean_d is not None else "    mean_delta=n/a",
                f"p95_delta={p95_d:.1f}s" if p95_d is not None else "p95_delta=n/a",
            )
    wall_s = time.time() - t0

    agg = aggregate_sweep_results(rows)

    out_dir = Path(out_dir) if out_dir is not None else make_sweep_dir(scenario_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = write_sweep_csv(rows, out_dir / "sweep_results.csv")
    fig_path = plot_sweep_figure(
        rows, agg, out_dir / "rerouting_sweep.png",
        title=f"Information sweep: {scenario_name} (n_seeds={len(seed_list)})",
    )

    summary = {
        "generated_at": datetime.now().isoformat(),
        "scenario": scenario_name,
        "variant": variant,
        "run_name": run_name,
        "fractions_pct": list(fractions_pct),
        "n_seeds": len(seed_list),
        "seeds": seed_list,
        "n_points": len(rows),
        "wall_clock_s": wall_s,
        "per_point_results": rows,
        "aggregated_by_fraction": agg,
        "baseline_handling": (
            "Baseline re-run at every (fraction, seed) point (not shared across "
            "the sweep) so the reported flooded-baseline delta at each fraction "
            "isolates the flood's marginal effect at that information level, "
            "controlling for the rerouting device's effect on travel times even "
            "absent closures. See floodtwin.analysis.sweep module docstring."
        ),
        "outputs": {
            "sweep_results_csv": str(csv_path),
            "sweep_summary_json": str(out_dir / "sweep_summary.json"),
            "figure_png": str(fig_path),
        },
    }
    with open(out_dir / "sweep_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\nSweep complete in {wall_s:.1f}s ({total} points -> {out_dir})")
    print(f"{'fraction%':>10} {'n_seeds':>8} {'mean_delta_s (mean+/-std)':>28} {'p95_delta_s (mean+/-std)':>28}")
    for a in agg:
        md = a["mean_travel_time_delta_s_mean"]
        ms = a["mean_travel_time_delta_s_std"]
        pd_ = a["p95_travel_time_delta_s_mean"]
        ps = a["p95_travel_time_delta_s_std"]
        md_str = f"{md:.1f} +/- {ms:.1f}" if md is not None else "n/a"
        pd_str = f"{pd_:.1f} +/- {ps:.1f}" if pd_ is not None else "n/a"
        print(f"{a['rerouting_fraction_pct']:>10} {a['n_seeds']:>8} {md_str:>28} {pd_str:>28}")

    return out_dir


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Slice 4: information sweep -- rerouting fraction x N seeds. Runs a "
            "matched baseline+flooded pair at every (fraction, seed) point (reusing "
            "floodtwin.sim.runner), scores each with floodtwin.analysis.metrics, "
            "and writes sweep_results.csv + sweep_summary.json + rerouting_sweep.png "
            "into runs/<ts>_sweep_<scenario>/."
        )
    )
    parser.add_argument(
        "--scenario",
        default="Sep_30_2022_74.75",
        help="Storm scenario name (see floodtwin.sim.runner --scenario). Reuses the cached forecast NPZ across the whole sweep.",
    )
    parser.add_argument(
        "--fractions",
        default=",".join(str(p) for p in DEFAULT_FRACTIONS_PCT),
        help="Comma-separated rerouting fractions as percentages, e.g. '0,25,50,75,100'.",
    )
    parser.add_argument("--n-seeds", type=int, default=DEFAULT_N_SEEDS)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument(
        "--seeds",
        default=None,
        help="Explicit comma-separated seed list, overriding --n-seeds/--base-seed.",
    )
    parser.add_argument("--variant", default=None, help="Flood model variant (default: floodtwin.flood.paths.DEFAULT_VARIANT)")
    parser.add_argument("--run-name", default=None, help="Flood model run name (default: floodtwin.flood.paths.DEFAULT_RUN_NAME)")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    fractions_pct = [float(x) for x in args.fractions.split(",") if x.strip() != ""]
    seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else None

    run_sweep(
        scenario_name=args.scenario,
        fractions_pct=fractions_pct,
        n_seeds=args.n_seeds,
        base_seed=args.base_seed,
        seeds=seeds,
        variant=args.variant,
        run_name=args.run_name,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()
