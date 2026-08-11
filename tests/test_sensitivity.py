"""Unit tests for the Slice 8 sensitivity-sweep module (PROJECT_PLAN.md SG4
Slice 8). Pure-Python: no SUMO install, no TraCI, no TensorFlow -- synthetic
``compute_metrics``-shaped dicts stand in for real run pairs, following
``tests/test_sweep.py``'s pattern. Importing ``floodtwin.analysis.sensitivity``
at module scope must stay SUMO-free (it lazily imports ``floodtwin.sim.runner``
only inside ``run_sensitivity_point``/``run_sensitivity``); this file exercises
exactly that boundary by never calling those two.
"""
import csv
import json

import pytest

from floodtwin.analysis.sensitivity import (
    SENSITIVITY_CSV_FIELDNAMES,
    DEFAULT_AGGREGATION,
    DEFAULT_CLOSURE_THRESHOLD_MM,
    DEFAULT_DEPTH_SCALE_M,
    DEFAULT_REROUTING_PERIOD_S,
    DEFAULT_RUN_NAME,
    DEFAULT_SEED,
    DEFAULT_VARIANT,
    SensitivityPoint,
    closure_stats_from_per_frame,
    default_points,
    error_row,
    make_sensitivity_dir,
    render_sensitivity_markdown,
    sensitivity_row,
    write_sensitivity_csv,
    write_sensitivity_markdown,
)


# ---------------------------------------------------------------------------
# default_points (pure)
# ---------------------------------------------------------------------------


def test_default_points_has_one_baseline_row():
    points = default_points()
    baseline_rows = [p for p in points if p.is_baseline_row]
    assert len(baseline_rows) == 1
    b = baseline_rows[0]
    assert b.seed == DEFAULT_SEED
    assert b.variant == DEFAULT_VARIANT
    assert b.run_name == DEFAULT_RUN_NAME
    assert b.closure_threshold_mm == DEFAULT_CLOSURE_THRESHOLD_MM
    assert b.aggregation == DEFAULT_AGGREGATION
    assert b.rerouting_period_s == DEFAULT_REROUTING_PERIOD_S
    assert b.depth_scale_m == DEFAULT_DEPTH_SCALE_M


def test_default_points_covers_all_six_axes():
    axes = {p.axis for p in default_points()}
    assert axes == {
        "baseline",
        "closure_threshold",
        "aggregation",
        "rerouting_period",
        "seed",
        "depth_scale",
        "checkpoint",
    }


def test_default_points_non_baseline_rows_vary_exactly_one_axis_from_default():
    baseline = SensitivityPoint(axis="baseline", label="x", is_baseline_row=True)
    for p in default_points():
        if p.is_baseline_row:
            continue
        diffs = []
        for field_name in ("seed", "variant", "run_name", "closure_threshold_mm", "aggregation",
                            "rerouting_period_s", "depth_scale_m"):
            if getattr(p, field_name) != getattr(baseline, field_name):
                diffs.append(field_name)
        # checkpoint axis changes both variant AND run_name together (one
        # conceptual axis, two fields) -- every other axis changes exactly
        # one field.
        if p.axis == "checkpoint":
            assert set(diffs) == {"variant", "run_name"}
        else:
            assert len(diffs) == 1, f"{p.axis}/{p.label} changed {diffs}, expected exactly 1"


def test_default_points_checkpoint_axis_uses_v4():
    checkpoint_points = [p for p in default_points() if p.axis == "checkpoint"]
    assert len(checkpoint_points) == 1
    assert checkpoint_points[0].variant == "v4"
    assert checkpoint_points[0].run_name == "v4_random_s42"


# ---------------------------------------------------------------------------
# closure_stats_from_per_frame (pure)
# ---------------------------------------------------------------------------


def test_closure_stats_from_per_frame_mean_and_max():
    per_frame = [
        {"n_closed": 10, "n_slowed": 10, "n_full_speed": 80},  # 10%
        {"n_closed": 30, "n_slowed": 10, "n_full_speed": 60},  # 30%
        {"n_closed": 20, "n_slowed": 10, "n_full_speed": 70},  # 20%
        {"n_closed": 40, "n_slowed": 10, "n_full_speed": 50},  # 40%
    ]
    stats = closure_stats_from_per_frame(per_frame)
    assert stats["mean_pct_edges_closed"] == pytest.approx(25.0)
    assert stats["max_pct_edges_closed"] == pytest.approx(40.0)
    assert stats["n_marks"] == 4


def test_closure_stats_from_per_frame_empty_returns_none():
    stats = closure_stats_from_per_frame([])
    assert stats["mean_pct_edges_closed"] is None
    assert stats["max_pct_edges_closed"] is None
    assert stats["n_marks"] == 0


def test_closure_stats_from_per_frame_skips_zero_total_marks():
    per_frame = [
        {"n_closed": 0, "n_slowed": 0, "n_full_speed": 0},
        {"n_closed": 5, "n_slowed": 5, "n_full_speed": 90},  # 5%
    ]
    stats = closure_stats_from_per_frame(per_frame)
    assert stats["n_marks"] == 1
    assert stats["mean_pct_edges_closed"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# sensitivity_row (pure)
# ---------------------------------------------------------------------------


def _metrics(mean_delta, p95_delta, pct_exposed, delta_arrived, per_frame=None,
             baseline_valid=True, flooded_valid=True, b_teleports=0, f_teleports=0,
             b_collisions=0, f_collisions=0):
    return {
        "travel_time": {"mean_delta_s": mean_delta, "p95_delta_s": p95_delta, "n_matched": 10},
        "exposure": {
            "n_exposed_closed_edge": 3,
            "pct_exposed_closed_edge": pct_exposed,
        },
        "throughput": {
            "baseline_arrived": 100,
            "flooded_arrived": 100 + (delta_arrived or 0),
            "delta_arrived": delta_arrived,
        },
        "run_health": {
            "baseline": {"teleports": b_teleports, "collisions": b_collisions},
            "flooded": {"teleports": f_teleports, "collisions": f_collisions},
        },
        "run_valid": {"baseline": baseline_valid, "flooded": flooded_valid},
        "closure_timeline": per_frame or [],
    }


def test_sensitivity_row_extracts_headline_fields():
    point = SensitivityPoint(axis="closure_threshold", label="200mm (20cm)", closure_threshold_mm=200.0)
    per_frame = [{"n_closed": 20, "n_slowed": 10, "n_full_speed": 70}]
    metrics = _metrics(mean_delta=25.0, p95_delta=80.0, pct_exposed=12.5, delta_arrived=-5, per_frame=per_frame)
    row = sensitivity_row(point, metrics, "runs/base_dir", "runs/flood_dir")
    assert row["axis"] == "closure_threshold"
    assert row["label"] == "200mm (20cm)"
    assert row["closure_threshold_mm"] == 200.0
    assert row["mean_travel_time_delta_s"] == pytest.approx(25.0)
    assert row["p95_travel_time_delta_s"] == pytest.approx(80.0)
    assert row["mean_pct_edges_closed"] == pytest.approx(20.0)
    assert row["max_pct_edges_closed"] == pytest.approx(20.0)
    assert row["pct_exposed_closed_edge"] == pytest.approx(12.5)
    assert row["baseline_teleports"] == 0
    assert row["flooded_teleports"] == 0
    assert row["baseline_collisions"] == 0
    assert row["flooded_collisions"] == 0
    assert row["run_pair_valid"] is True
    assert row["baseline_run_dir"] == "runs/base_dir"
    assert row["flooded_run_dir"] == "runs/flood_dir"


def test_sensitivity_row_flags_invalid_when_either_run_invalid():
    point = SensitivityPoint(axis="baseline", label="baseline", is_baseline_row=True)
    metrics = _metrics(25.0, 80.0, 12.5, -5, flooded_valid=False, f_teleports=2)
    row = sensitivity_row(point, metrics, "b", "f")
    assert row["flooded_run_valid"] is False
    assert row["run_pair_valid"] is False
    assert row["flooded_teleports"] == 2


def test_sensitivity_row_handles_missing_metrics_gracefully():
    point = SensitivityPoint(axis="seed", label="seed=43", seed=43)
    row = sensitivity_row(point, {}, "b", "f")
    assert row["mean_travel_time_delta_s"] is None
    assert row["mean_pct_edges_closed"] is None
    assert row["run_pair_valid"] is False  # {} -> valid.get(...) is None -> bool(None) is False


def test_sensitivity_row_error_field_is_none_on_success():
    point = SensitivityPoint(axis="baseline", label="baseline", is_baseline_row=True)
    row = sensitivity_row(point, _metrics(25.0, 80.0, 12.5, -5), "b", "f")
    assert row["error"] is None


# ---------------------------------------------------------------------------
# error_row (pure) -- Slice 8 fault-tolerance: a point whose run pair could
# not be produced at all (e.g. the checkpoint axis's real, diagnosed
# Python-3.13/numpy native-crash infrastructure blocker) gets an honestly
# errored row, not a fabricated number and not a silently-dropped point.
# ---------------------------------------------------------------------------


def test_error_row_keeps_axis_and_label_and_params_intact():
    point = SensitivityPoint(axis="checkpoint", label="v4 (balanced)", variant="v4", run_name="v4_random_s42")
    row = error_row(point, "RuntimeError: flood_runner inference failed (exit 3221225477)")
    assert row["axis"] == "checkpoint"
    assert row["label"] == "v4 (balanced)"
    assert row["checkpoint_variant"] == "v4"
    assert row["checkpoint_run_name"] == "v4_random_s42"
    assert row["error"] == "RuntimeError: flood_runner inference failed (exit 3221225477)"


def test_error_row_every_metric_field_is_none_not_zero_or_fabricated():
    point = SensitivityPoint(axis="checkpoint", label="v4 (balanced)", variant="v4", run_name="v4_random_s42")
    row = error_row(point, "boom")
    for key in (
        "mean_travel_time_delta_s", "p95_travel_time_delta_s", "n_matched_trips",
        "mean_pct_edges_closed", "max_pct_edges_closed", "pct_exposed_closed_edge",
        "baseline_arrived", "flooded_arrived", "delta_arrived",
        "baseline_teleports", "flooded_teleports", "baseline_collisions", "flooded_collisions",
        "baseline_run_valid", "flooded_run_valid", "baseline_run_dir", "flooded_run_dir",
    ):
        assert row[key] is None, f"{key} should be None for an errored point, got {row[key]!r}"
    assert row["run_pair_valid"] is False


def test_error_row_has_same_csv_shape_as_sensitivity_row():
    point = SensitivityPoint(axis="checkpoint", label="v4 (balanced)", variant="v4", run_name="v4_random_s42")
    err = error_row(point, "boom")
    ok = sensitivity_row(
        SensitivityPoint(axis="baseline", label="baseline", is_baseline_row=True),
        _metrics(1.0, 2.0, 3.0, 4), "b", "f",
    )
    assert set(err.keys()) == set(ok.keys()) == set(SENSITIVITY_CSV_FIELDNAMES)


def test_render_sensitivity_markdown_shows_error_reason_not_just_no():
    valid_point = SensitivityPoint(axis="baseline", label="baseline", is_baseline_row=True)
    checkpoint_point = SensitivityPoint(axis="checkpoint", label="v4 (balanced)", variant="v4", run_name="v4_random_s42")
    rows = [
        sensitivity_row(valid_point, _metrics(100.0, 200.0, 50.0, -10), "b1", "f1"),
        error_row(checkpoint_point, "RuntimeError: flood_runner inference failed (exit 3221225477): numpy segfault"),
    ]
    md = render_sensitivity_markdown(rows, title="Test Table")
    assert "**ERROR**" in md
    assert "flood_runner inference failed" in md
    assert "numpy segfault" in md
    # The ordinary-invalid case (no error, just unhealthy) must NOT claim an
    # infrastructure error happened -- distinct failure modes, distinct text.
    assert "**NO**" not in md  # neither row here is that case; sanity-check the other test covers it


def test_render_sensitivity_markdown_sanitizes_pipes_and_newlines_in_error():
    point = SensitivityPoint(axis="checkpoint", label="v4", variant="v4", run_name="v4_random_s42")
    nasty = "line one\nline two | with a pipe | that would break | the table"
    rows = [error_row(point, nasty)]
    md = render_sensitivity_markdown(rows)
    # Every data row must still be exactly one line with the right column count.
    data_lines = [l for l in md.splitlines() if l.startswith("| checkpoint")]
    assert len(data_lines) == 1
    assert data_lines[0].count("|") == md.splitlines()[2].count("|")  # matches header's column count
    assert "\n" not in data_lines[0]


# ---------------------------------------------------------------------------
# write_sensitivity_csv / render+write markdown (I/O, pure filesystem)
# ---------------------------------------------------------------------------


def test_write_sensitivity_csv_round_trip(tmp_path):
    p1 = SensitivityPoint(axis="baseline", label="baseline", is_baseline_row=True)
    p2 = SensitivityPoint(axis="closure_threshold", label="400mm (40cm)", closure_threshold_mm=400.0)
    rows = [
        sensitivity_row(p1, _metrics(100.0, 200.0, 50.0, -10), "b1", "f1"),
        sensitivity_row(p2, _metrics(90.0, 180.0, 45.0, -8), "b2", "f2"),
    ]
    path = write_sensitivity_csv(rows, tmp_path / "sensitivity_results.csv")
    assert path.exists()
    with open(path, newline="") as f:
        read_rows = list(csv.DictReader(f))
    assert len(read_rows) == 2
    assert set(read_rows[0].keys()) == set(SENSITIVITY_CSV_FIELDNAMES)
    assert read_rows[0]["axis"] == "baseline"
    assert read_rows[1]["closure_threshold_mm"] == "400.0"


def test_render_sensitivity_markdown_contains_every_row_and_flags_invalid():
    p1 = SensitivityPoint(axis="baseline", label="baseline", is_baseline_row=True)
    p2 = SensitivityPoint(axis="checkpoint", label="v4 (balanced)", variant="v4", run_name="v4_random_s42")
    rows = [
        sensitivity_row(p1, _metrics(100.0, 200.0, 50.0, -10), "b1", "f1"),
        sensitivity_row(p2, _metrics(90.0, 180.0, 45.0, -8, flooded_valid=False, f_teleports=1), "b2", "f2"),
    ]
    md = render_sensitivity_markdown(rows, title="Test Table")
    assert "Test Table" in md
    assert "baseline" in md
    assert "v4 (balanced)" in md
    assert "**NO**" in md  # the invalid checkpoint row must be visibly flagged


def test_write_sensitivity_markdown_writes_file(tmp_path):
    p1 = SensitivityPoint(axis="baseline", label="baseline", is_baseline_row=True)
    rows = [sensitivity_row(p1, _metrics(100.0, 200.0, 50.0, -10), "b1", "f1")]
    path = write_sensitivity_markdown(rows, tmp_path / "sensitivity_table.md", title="T")
    assert path.exists()
    assert "T" in path.read_text()


# ---------------------------------------------------------------------------
# make_sensitivity_dir (I/O)
# ---------------------------------------------------------------------------


def test_make_sensitivity_dir_creates_directory_under_sensitivity_subdir(monkeypatch, tmp_path):
    import floodtwin.analysis.sensitivity as sens_mod

    monkeypatch.setattr(sens_mod, "RUNS_DIR", tmp_path)
    d = make_sensitivity_dir("Sep_30_2022_74.75")
    assert d.exists()
    assert d.parent == tmp_path / "sensitivity"
    assert "Sep_30_2022_74.75" in d.name
