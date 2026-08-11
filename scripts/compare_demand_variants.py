"""Slice 7 demo criterion (PROJECT_PLAN.md SG4): "calibrated vs random demand
comparison". Reads a pair of Slice 3 ``metrics.json`` (baseline-vs-flooded)
outputs -- one run with ``v1`` (random/illustrative) demand, one with
``calibrated_v2`` (VDOT-calibrated) demand, same scenario/seed/rerouting
fraction -- and a pair of Slice 4 ``sweep_summary.json`` outputs, and writes
a side-by-side comparison table (CSV + a small bar figure) into
``runs/<ts>_demand_comparison/``.

Not exercised by ``pytest`` (reads real run artifacts under ``runs/``,
which is gitignored/ephemeral) -- verified manually, see the Slice 7 PR for
the actual numbers produced.

Usage::

    python scripts/compare_demand_variants.py \\
        --v1-metrics runs/<ts>_metrics_.../metrics.json \\
        --calibrated-metrics runs/<ts>_metrics_.../metrics.json \\
        --v1-sweep runs/sweep_v1/sweep_summary.json \\
        --calibrated-sweep runs/sweep_calibrated_v2/sweep_summary.json \\
        --out-dir runs/demand_comparison
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text())


def build_headline_rows(v1_metrics: dict, cal_metrics: dict) -> list:
    """Slice 3-style baseline-vs-flooded headline numbers, side by side."""
    rows = []

    def add(label, v1_val, cal_val, fmt="{:.1f}"):
        rows.append(
            {
                "metric": label,
                "v1_random": fmt.format(v1_val) if isinstance(v1_val, (int, float)) else v1_val,
                "calibrated_v2": fmt.format(cal_val) if isinstance(cal_val, (int, float)) else cal_val,
            }
        )

    v1_tt, cal_tt = v1_metrics["travel_time"], cal_metrics["travel_time"]
    v1_exp, cal_exp = v1_metrics["exposure"], cal_metrics["exposure"]
    v1_thr, cal_thr = v1_metrics["throughput"], cal_metrics["throughput"]
    v1_rh, cal_rh = v1_metrics["run_health"], cal_metrics["run_health"]

    add("total demand (loaded vehicles)", v1_rh["baseline"]["loaded"], cal_rh["baseline"]["loaded"], "{:.0f}")
    add("baseline mean travel time (s)", v1_tt["baseline_mean_travel_time_s"], cal_tt["baseline_mean_travel_time_s"])
    add("flooded mean travel time (s)", v1_tt["flooded_mean_travel_time_s"], cal_tt["flooded_mean_travel_time_s"])
    add("mean travel-time delta, flooded-baseline (s)", v1_tt["mean_delta_s"], cal_tt["mean_delta_s"])
    add("p95 travel-time delta, flooded-baseline (s)", v1_tt["p95_delta_s"], cal_tt["p95_delta_s"])
    add("% trips exposed to a closed edge", v1_exp["pct_exposed_closed_edge"], cal_exp["pct_exposed_closed_edge"])
    add("% trips exposed to any wet edge", v1_exp["pct_exposed_wet_edge"], cal_exp["pct_exposed_wet_edge"])
    add("throughput delta (flooded - baseline arrived)", v1_thr["delta_arrived"], cal_thr["delta_arrived"], "{:.0f}")
    add("baseline teleports", v1_rh["baseline"]["teleports"], cal_rh["baseline"]["teleports"], "{:.0f}")
    add("flooded teleports", v1_rh["flooded"]["teleports"], cal_rh["flooded"]["teleports"], "{:.0f}")

    return rows


def build_sweep_rows(v1_sweep: dict, cal_sweep: dict) -> list:
    """Slice 4-style rerouting-fraction sweep, side by side at each
    fraction."""
    v1_by_frac = {a["rerouting_fraction_pct"]: a for a in v1_sweep["aggregated_by_fraction"]}
    cal_by_frac = {a["rerouting_fraction_pct"]: a for a in cal_sweep["aggregated_by_fraction"]}
    rows = []
    for frac in sorted(set(v1_by_frac) | set(cal_by_frac)):
        v1a = v1_by_frac.get(frac, {})
        cala = cal_by_frac.get(frac, {})
        rows.append(
            {
                "rerouting_fraction_pct": frac,
                "v1_mean_delta_s": v1a.get("mean_travel_time_delta_s_mean"),
                "v1_mean_delta_s_std": v1a.get("mean_travel_time_delta_s_std"),
                "calibrated_v2_mean_delta_s": cala.get("mean_travel_time_delta_s_mean"),
                "calibrated_v2_mean_delta_s_std": cala.get("mean_travel_time_delta_s_std"),
                "v1_p95_delta_s": v1a.get("p95_travel_time_delta_s_mean"),
                "calibrated_v2_p95_delta_s": cala.get("p95_travel_time_delta_s_mean"),
            }
        )
    return rows


def write_csv(rows, path, fieldnames):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def plot_comparison(headline_rows, sweep_rows, out_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

    def _val(row, key):
        try:
            return float(row[key])
        except (TypeError, ValueError):
            return None

    labels = ["mean travel-time\ndelta (s)", "p95 travel-time\ndelta (s)"]
    mean_row = next(r for r in headline_rows if r["metric"] == "mean travel-time delta, flooded-baseline (s)")
    p95_row = next(r for r in headline_rows if r["metric"] == "p95 travel-time delta, flooded-baseline (s)")
    v1_vals = [_val(mean_row, "v1_random"), _val(p95_row, "v1_random")]
    cal_vals = [_val(mean_row, "calibrated_v2"), _val(p95_row, "calibrated_v2")]

    x = range(len(labels))
    width = 0.35
    ax1.bar([i - width / 2 for i in x], v1_vals, width, label="v1 (random, illustrative)", color="#7f7f7f")
    ax1.bar([i + width / 2 for i in x], cal_vals, width, label="calibrated_v2 (VDOT-calibrated)", color="#d62728")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels)
    ax1.set_ylabel("Delta, flooded - baseline (s)")
    ax1.set_title(f"Flood disruption headline\n(scenario baseline_seed=42, rerouting=100%)")
    ax1.legend()
    ax1.axhline(0, color="gray", linewidth=0.8, linestyle=":")

    fractions = [r["rerouting_fraction_pct"] for r in sweep_rows]
    v1_means = [r["v1_mean_delta_s"] for r in sweep_rows]
    cal_means = [r["calibrated_v2_mean_delta_s"] for r in sweep_rows]
    ax2.plot(fractions, v1_means, marker="o", color="#7f7f7f", label="v1 (random, illustrative)")
    ax2.plot(fractions, cal_means, marker="s", color="#d62728", label="calibrated_v2 (VDOT-calibrated)")
    ax2.axhline(0, color="gray", linewidth=0.8, linestyle=":")
    ax2.set_xlabel("Rerouting fraction (%)")
    ax2.set_ylabel("Mean travel-time delta, flooded - baseline (s)")
    ax2.set_title("Information sweep: calibrated vs random demand")
    ax2.legend()

    fig.suptitle("Slice 7 demo: calibrated (VDOT) vs random (illustrative) demand")
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-metrics", required=True)
    parser.add_argument("--calibrated-metrics", required=True)
    parser.add_argument("--v1-sweep", required=True)
    parser.add_argument("--calibrated-sweep", required=True)
    parser.add_argument("--out-dir", default="runs/demand_comparison")
    args = parser.parse_args()

    v1_metrics = load(args.v1_metrics)
    cal_metrics = load(args.calibrated_metrics)
    v1_sweep = load(args.v1_sweep)
    cal_sweep = load(args.calibrated_sweep)

    headline_rows = build_headline_rows(v1_metrics, cal_metrics)
    sweep_rows = build_sweep_rows(v1_sweep, cal_sweep)

    out_dir = Path(args.out_dir)
    headline_csv = write_csv(headline_rows, out_dir / "headline_comparison.csv", ["metric", "v1_random", "calibrated_v2"])
    sweep_csv = write_csv(
        sweep_rows,
        out_dir / "sweep_comparison.csv",
        [
            "rerouting_fraction_pct",
            "v1_mean_delta_s",
            "v1_mean_delta_s_std",
            "calibrated_v2_mean_delta_s",
            "calibrated_v2_mean_delta_s_std",
            "v1_p95_delta_s",
            "calibrated_v2_p95_delta_s",
        ],
    )
    fig_path = plot_comparison(headline_rows, sweep_rows, out_dir / "demand_comparison.png")

    print(f"Headline comparison table -> {headline_csv}")
    print(f"Sweep comparison table -> {sweep_csv}")
    print(f"Figure -> {fig_path}")
    print()
    for r in headline_rows:
        print(f"  {r['metric']:<50s} v1={r['v1_random']:>10}   calibrated_v2={r['calibrated_v2']:>10}")


if __name__ == "__main__":
    main()
