"""Unit tests for the Slice 4 information-sweep module
(PROJECT_PLAN.md SG2 Slice 4). Pure-Python: no SUMO install, no TraCI, no
TensorFlow -- synthetic ``compute_metrics``-shaped dicts stand in for real
run pairs, following tests/test_metrics.py's pattern. Importing
``floodtwin.analysis.sweep`` at module scope must stay SUMO-free (it lazily
imports ``floodtwin.sim.runner`` only inside ``run_sweep_point``/``run_sweep``);
this file exercises exactly that boundary by never calling those two.
"""
import csv

import pytest

from floodtwin.analysis.sweep import (
    SWEEP_CSV_FIELDNAMES,
    aggregate_sweep_results,
    make_sweep_dir,
    plot_sweep_figure,
    seeds_for_sweep,
    sweep_point_row,
    write_sweep_csv,
)


# ---------------------------------------------------------------------------
# seeds_for_sweep (pure)
# ---------------------------------------------------------------------------


def test_seeds_for_sweep_default():
    assert seeds_for_sweep(3, 42) == [42, 43, 44]


def test_seeds_for_sweep_is_deterministic():
    assert seeds_for_sweep(5, 100) == seeds_for_sweep(5, 100)
    assert seeds_for_sweep(1, 7) == [7]


# ---------------------------------------------------------------------------
# sweep_point_row (pure)
# ---------------------------------------------------------------------------


def _metrics(mean_delta, p95_delta, pct_exposed, delta_arrived, baseline_valid=True, flooded_valid=True):
    return {
        "travel_time": {"mean_delta_s": mean_delta, "p95_delta_s": p95_delta, "n_matched": 10},
        "exposure": {
            "n_exposed_closed_edge": 3,
            "pct_exposed_closed_edge": pct_exposed,
            "n_exposed_wet_edge": 5,
            "pct_exposed_wet_edge": pct_exposed * 1.5 if pct_exposed is not None else None,
        },
        "throughput": {
            "baseline_arrived": 100,
            "flooded_arrived": 100 + (delta_arrived or 0),
            "delta_arrived": delta_arrived,
        },
        "run_health": {
            "baseline": {"teleports": 0},
            "flooded": {"teleports": 0},
        },
        "run_valid": {"baseline": baseline_valid, "flooded": flooded_valid},
    }


def test_sweep_point_row_extracts_headline_fields():
    metrics = _metrics(mean_delta=25.0, p95_delta=80.0, pct_exposed=12.5, delta_arrived=-5)
    row = sweep_point_row(50, 42, "runs/base_dir", "runs/flood_dir", metrics)
    assert row["rerouting_fraction_pct"] == 50
    assert row["seed"] == 42
    assert row["mean_travel_time_delta_s"] == pytest.approx(25.0)
    assert row["p95_travel_time_delta_s"] == pytest.approx(80.0)
    assert row["pct_exposed_closed_edge"] == pytest.approx(12.5)
    assert row["delta_arrived"] == -5
    assert row["baseline_teleports"] == 0
    assert row["flooded_teleports"] == 0
    assert row["baseline_run_valid"] is True
    assert row["flooded_run_valid"] is True
    assert row["baseline_run_dir"] == "runs/base_dir"
    assert row["flooded_run_dir"] == "runs/flood_dir"


def test_sweep_point_row_handles_missing_metrics_gracefully():
    # An empty/partial metrics dict shouldn't crash -- should produce None
    # fields, matching the rest of the codebase's "missing means missing,
    # not a silently-dropped 0" convention (see metrics.py's parse_tripinfo
    # docstring).
    row = sweep_point_row(0, 1, "b", "f", {})
    assert row["mean_travel_time_delta_s"] is None
    assert row["p95_travel_time_delta_s"] is None
    assert row["baseline_teleports"] is None
    assert row["baseline_run_valid"] is None


# ---------------------------------------------------------------------------
# aggregate_sweep_results (pure)
# ---------------------------------------------------------------------------


def test_aggregate_sweep_results_groups_and_computes_mean_std():
    rows = [
        sweep_point_row(0, 1, "b", "f", _metrics(100.0, 200.0, 50.0, -10)),
        sweep_point_row(0, 2, "b", "f", _metrics(120.0, 220.0, 55.0, -12)),
        sweep_point_row(0, 3, "b", "f", _metrics(110.0, 210.0, 52.0, -11)),
        sweep_point_row(100, 1, "b", "f", _metrics(10.0, 20.0, 5.0, -1)),
        sweep_point_row(100, 2, "b", "f", _metrics(12.0, 22.0, 6.0, -1)),
        sweep_point_row(100, 3, "b", "f", _metrics(11.0, 21.0, 5.5, -1)),
    ]
    agg = aggregate_sweep_results(rows)
    assert [a["rerouting_fraction_pct"] for a in agg] == [0, 100]

    at_0 = agg[0]
    assert at_0["n_seeds"] == 3
    assert at_0["seeds"] == [1, 2, 3]
    assert at_0["mean_travel_time_delta_s_mean"] == pytest.approx(110.0)
    # sample stdev of [100, 120, 110] = 10.0
    assert at_0["mean_travel_time_delta_s_std"] == pytest.approx(10.0)

    at_100 = agg[1]
    assert at_100["mean_travel_time_delta_s_mean"] == pytest.approx(11.0)
    # 100% rerouting shows a much smaller delta than 0% -- the headline
    # direction the sweep exists to measure.
    assert at_100["mean_travel_time_delta_s_mean"] < at_0["mean_travel_time_delta_s_mean"]


def test_aggregate_sweep_results_single_seed_reports_zero_std_not_none():
    rows = [sweep_point_row(25, 42, "b", "f", _metrics(50.0, 90.0, 20.0, -3))]
    agg = aggregate_sweep_results(rows)
    assert len(agg) == 1
    assert agg[0]["n_seeds"] == 1
    assert agg[0]["mean_travel_time_delta_s_mean"] == pytest.approx(50.0)
    assert agg[0]["mean_travel_time_delta_s_std"] == pytest.approx(0.0)


def test_aggregate_sweep_results_flags_invalid_runs():
    rows = [
        sweep_point_row(50, 1, "b", "f", _metrics(10.0, 20.0, 5.0, -1, baseline_valid=True, flooded_valid=False)),
        sweep_point_row(50, 2, "b", "f", _metrics(11.0, 21.0, 5.0, -1, baseline_valid=True, flooded_valid=True)),
    ]
    agg = aggregate_sweep_results(rows)
    assert agg[0]["n_invalid_runs"] == 1


def test_aggregate_sweep_results_all_none_returns_none_not_crash():
    rows = [sweep_point_row(75, 1, "b", "f", _metrics(None, None, None, None))]
    agg = aggregate_sweep_results(rows)
    assert agg[0]["mean_travel_time_delta_s_mean"] is None
    assert agg[0]["mean_travel_time_delta_s_std"] is None


def test_aggregate_sweep_results_ignores_none_values_in_mixed_group():
    rows = [
        sweep_point_row(10, 1, "b", "f", _metrics(None, None, None, None)),
        sweep_point_row(10, 2, "b", "f", _metrics(40.0, 60.0, 10.0, -2)),
    ]
    agg = aggregate_sweep_results(rows)
    # only the non-None observation should count toward mean/std
    assert agg[0]["mean_travel_time_delta_s_mean"] == pytest.approx(40.0)
    assert agg[0]["mean_travel_time_delta_s_std"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# write_sweep_csv (I/O, pure filesystem -- no SUMO)
# ---------------------------------------------------------------------------


def test_write_sweep_csv_round_trip(tmp_path):
    rows = [
        sweep_point_row(0, 1, "b1", "f1", _metrics(100.0, 200.0, 50.0, -10)),
        sweep_point_row(100, 1, "b2", "f2", _metrics(10.0, 20.0, 5.0, -1)),
    ]
    path = write_sweep_csv(rows, tmp_path / "sweep_results.csv")
    assert path.exists()
    with open(path, newline="") as f:
        read_rows = list(csv.DictReader(f))
    assert len(read_rows) == 2
    assert set(read_rows[0].keys()) == set(SWEEP_CSV_FIELDNAMES)
    assert read_rows[0]["rerouting_fraction_pct"] == "0"
    assert read_rows[1]["rerouting_fraction_pct"] == "100"


# ---------------------------------------------------------------------------
# plot_sweep_figure (I/O -- writes a PNG; skipped if matplotlib unavailable)
# ---------------------------------------------------------------------------


def test_plot_sweep_figure_creates_png(tmp_path):
    pytest.importorskip("matplotlib")
    rows = [
        sweep_point_row(0, 1, "b", "f", _metrics(100.0, 200.0, 50.0, -10)),
        sweep_point_row(0, 2, "b", "f", _metrics(110.0, 210.0, 52.0, -11)),
        sweep_point_row(100, 1, "b", "f", _metrics(10.0, 20.0, 5.0, -1)),
        sweep_point_row(100, 2, "b", "f", _metrics(12.0, 22.0, 6.0, -1)),
    ]
    agg = aggregate_sweep_results(rows)
    out_path = plot_sweep_figure(rows, agg, tmp_path / "rerouting_sweep.png", title="test sweep")
    assert out_path.exists()
    assert out_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# make_sweep_dir (I/O)
# ---------------------------------------------------------------------------


def test_make_sweep_dir_creates_directory_with_label(monkeypatch, tmp_path):
    import floodtwin.analysis.sweep as sweep_mod

    monkeypatch.setattr(sweep_mod, "RUNS_DIR", tmp_path)
    d = make_sweep_dir("Some/Scenario")
    assert d.exists()
    assert d.parent == tmp_path
    assert "sweep_Some_Scenario" in d.name
