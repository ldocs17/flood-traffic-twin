"""Unit tests for the Slice 5 web-replay API's pure logic (PROJECT_PLAN.md
SG3 / Slice 5). Pure-Python: no SUMO install, no sumolib, no FastAPI
TestClient -- synthetic fcd.xml / edge_states.csv / config.json fixtures
stand in for real run artifacts, following tests/test_metrics.py's pattern.

``floodtwin.api.network`` (sumolib-dependent) and ``floodtwin.api.app``
(FastAPI route wiring) are deliberately NOT imported here -- CI has no
SUMO_HOME and doesn't install the ``api`` extra (see pyproject.toml).
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from floodtwin.api.edge_states import (
    COLOR_CLOSED,
    COLOR_OPEN,
    COLOR_SLOWED,
    edge_state_color,
    edge_states_to_json,
    parse_edge_states_csv,
)
from floodtwin.api.fcd import parse_fcd_frames
from floodtwin.api.flood_raster import (
    DEFAULT_FRAME_LABELS,
    DEFAULT_FRAME_MARKS_S,
    depth_to_rgba,
    resolve_flood_source,
    verify_bounds_match_georef,
)
from floodtwin.api.runs import (
    InvalidRunIdError,
    RunNotFoundError,
    list_runs,
    load_run_config,
    run_dir_for_id,
    summarize_run,
)
from floodtwin.coupling import georef


# ---------------------------------------------------------------------------
# fcd.py
# ---------------------------------------------------------------------------

FCD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fcd-export>
    <timestep time="0.00">
        <vehicle id="v0" x="100.0" y="200.0" speed="0.0"/>
    </timestep>
    <timestep time="1.00">
        <vehicle id="v0" x="101.0" y="200.0" speed="1.0"/>
    </timestep>
    <timestep time="2.00">
        <vehicle id="v0" x="102.0" y="200.0" speed="2.0"/>
        <vehicle id="v1" x="50.0" y="50.0" speed="0.5"/>
    </timestep>
    <timestep time="3.00">
        <vehicle id="v0" x="103.0" y="200.0" speed="3.0"/>
    </timestep>
    <timestep time="4.00">
        <vehicle id="v0" x="104.0" y="200.0" speed="4.0"/>
    </timestep>
</fcd-export>
"""


def _write_fcd(tmp_path):
    p = tmp_path / "fcd.xml"
    p.write_text(FCD_XML)
    return p


def test_parse_fcd_frames_no_stride_keeps_every_timestep(tmp_path):
    p = _write_fcd(tmp_path)
    result = parse_fcd_frames(p, stride_s=0)
    assert result["stride_s"] == 0
    assert [f["t"] for f in result["frames"]] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert len(result["frames"][2]["v"]) == 2  # v0 + v1 at t=2


def test_parse_fcd_frames_stride_decimates(tmp_path):
    p = _write_fcd(tmp_path)
    result = parse_fcd_frames(p, stride_s=2.0)
    # first timestep (t=0) always kept, then t=2, then t=4
    assert [f["t"] for f in result["frames"]] == [0.0, 2.0, 4.0]


def test_parse_fcd_frames_applies_convert_and_rounds(tmp_path):
    p = _write_fcd(tmp_path)

    def convert(x, y):
        return x / 10.0, y / 10.0

    result = parse_fcd_frames(p, stride_s=0, convert=convert)
    vid, lon, lat, speed = result["frames"][0]["v"][0]
    assert vid == "v0"
    assert lon == pytest.approx(10.0)
    assert lat == pytest.approx(20.0)
    assert speed == pytest.approx(0.0)


def test_parse_fcd_frames_identity_convert_by_default(tmp_path):
    p = _write_fcd(tmp_path)
    result = parse_fcd_frames(p, stride_s=0)
    vid, lon, lat, speed = result["frames"][1]["v"][0]
    assert (lon, lat) == pytest.approx((101.0, 200.0))


# ---------------------------------------------------------------------------
# edge_states.py
# ---------------------------------------------------------------------------

SINGLE_FRAME_CSV = """edge_id,max_depth_m,v_max_ms,closed
e1,0.4819,0.000,1
e2,0.0000,5.560,0
e3,0.1000,4.000,0
"""

MULTIFRAME_CSV = """frame_min,edge_id,max_depth_m,v_max_ms,closed
15,e1,0.4819,0.000,1
15,e2,0.0000,5.560,0
30,e1,0.5000,0.000,1
30,e2,0.0000,5.560,0
"""


def test_parse_edge_states_csv_single_frame_uses_default_mark(tmp_path):
    p = tmp_path / "edge_states.csv"
    p.write_text(SINGLE_FRAME_CSV)
    parsed = parse_edge_states_csv(p, default_mark_s=900.0)
    assert set(parsed.keys()) == {900.0}
    depth_m, v_max_ms, closed = parsed[900.0]["e1"]
    assert depth_m == pytest.approx(0.4819)
    assert closed is True
    assert parsed[900.0]["e2"][2] is False


def test_parse_edge_states_csv_multiframe_groups_by_mark(tmp_path):
    p = tmp_path / "edge_states.csv"
    p.write_text(MULTIFRAME_CSV)
    parsed = parse_edge_states_csv(p)
    assert set(parsed.keys()) == {900.0, 1800.0}
    assert parsed[1800.0]["e1"][0] == pytest.approx(0.5000)


def test_edge_state_color_closed_wins():
    assert edge_state_color(closed=True, v_max_ms=0.0, speed_limit_ms=13.0) == COLOR_CLOSED


def test_edge_state_color_slowed_when_below_speed_limit():
    assert edge_state_color(closed=False, v_max_ms=5.0, speed_limit_ms=13.9) == COLOR_SLOWED


def test_edge_state_color_open_at_full_speed():
    assert edge_state_color(closed=False, v_max_ms=13.9, speed_limit_ms=13.9) == COLOR_OPEN


def test_edge_state_color_open_without_speed_limit_falls_back():
    # no speed_limit_ms known -> can't tell slowed from open, never guesses
    assert edge_state_color(closed=False, v_max_ms=1.0, speed_limit_ms=None) == COLOR_OPEN


def test_edge_states_to_json_shape(tmp_path):
    p = tmp_path / "edge_states.csv"
    p.write_text(MULTIFRAME_CSV)
    parsed = parse_edge_states_csv(p)
    out = edge_states_to_json(parsed, speed_limits_ms={"e1": 13.9, "e2": 5.56})
    assert out["marks_s"] == [900.0, 1800.0]
    frame0 = out["frames"][0]
    assert frame0["mark_s"] == 900.0
    assert frame0["edges"]["e1"]["closed"] is True
    assert frame0["edges"]["e1"]["color"] == COLOR_CLOSED
    assert frame0["edges"]["e2"]["color"] == COLOR_OPEN  # 5.56 == speed limit, not slowed


# ---------------------------------------------------------------------------
# flood_raster.py
# ---------------------------------------------------------------------------

def test_depth_to_rgba_dry_pixels_fully_transparent():
    grid = np.zeros((4, 4))
    rgba = depth_to_rgba(grid, max_depth=1.0)
    assert (rgba[:, :, 3] == 0).all()


def test_depth_to_rgba_wet_pixel_has_alpha_and_blue():
    grid = np.zeros((2, 2))
    grid[0, 0] = 0.5  # above WET_THRESHOLD
    rgba = depth_to_rgba(grid, max_depth=1.0)
    assert rgba[0, 0, 3] > 0  # alpha channel set
    assert rgba[0, 0, 2] > 0  # blue channel set
    assert rgba[1, 1, 3] == 0  # untouched pixel stays transparent


def test_depth_to_rgba_deeper_is_more_opaque():
    grid = np.array([[0.05, 0.9]])
    rgba = depth_to_rgba(grid, max_depth=1.0)
    assert rgba[0, 1, 3] > rgba[0, 0, 3]


def test_depth_to_rgba_handles_zero_max_depth_without_dividing_by_zero():
    grid = np.zeros((2, 2))
    rgba = depth_to_rgba(grid, max_depth=0.0)  # would divide by zero if unguarded
    assert rgba.shape == (2, 2, 4)


def test_verify_bounds_match_georef_true_for_default_transform():
    assert verify_bounds_match_georef(georef.DEFAULT_TRANSFORM) is True


def test_verify_bounds_match_georef_false_for_shifted_transform():
    shifted = georef.GeoTransform(
        north=georef.DEFAULT_TRANSFORM.north + 1.0,
        south=georef.DEFAULT_TRANSFORM.south,
        east=georef.DEFAULT_TRANSFORM.east,
        west=georef.DEFAULT_TRANSFORM.west,
    )
    assert verify_bounds_match_georef(shifted) is False


def test_resolve_flood_source_from_forecast_npz(tmp_path):
    npz_path = tmp_path / "forecast.npz"
    depth_stack = np.random.rand(4, 4, 4).astype(np.float32)
    np.savez(
        npz_path,
        depth_stack=depth_stack,
        north=1.0, south=0.0, east=1.0, west=0.0, grid_size=4,
        scenario="x", input_npy="x", variant="v1", run_name="r", generated_at="t",
    )
    config = {
        "forecast_npz": str(npz_path),
        "frame_marks_s": [900.0, 1800.0, 2700.0, 3600.0],
        "frame_labels": ["a", "b", "c", "d"],
    }
    source = resolve_flood_source(config)
    assert source is not None
    assert source.depth_stack.shape == (4, 4, 4)
    assert source.transform.north == 1.0
    assert source.bounds_match_georef is False  # tiny fake grid, not the real domain
    assert source.frame_labels == ["a", "b", "c", "d"]


def test_resolve_flood_source_from_scenario_npy_falls_back_to_default_transform(tmp_path):
    npy_path = tmp_path / "output.npy"
    np.save(npy_path, np.random.rand(128, 128, 4).astype(np.float32))
    config = {"scenario_npy": str(npy_path)}
    source = resolve_flood_source(config)
    assert source is not None
    assert source.transform == georef.DEFAULT_TRANSFORM
    assert source.frame_marks_s == DEFAULT_FRAME_MARKS_S
    assert source.frame_labels == DEFAULT_FRAME_LABELS


def test_resolve_flood_source_none_for_baseline_config():
    assert resolve_flood_source({"scenario": "baseline_no_flood"}) is None


# ---------------------------------------------------------------------------
# runs.py
# ---------------------------------------------------------------------------

def _make_run_dir(tmp_path, name, config):
    run_dir = tmp_path / name
    run_dir.mkdir()
    (run_dir / "config.json").write_text(json.dumps(config))
    return run_dir


def test_run_dir_for_id_rejects_path_traversal(tmp_path):
    with pytest.raises(InvalidRunIdError):
        run_dir_for_id(tmp_path, "../secret")
    with pytest.raises(InvalidRunIdError):
        run_dir_for_id(tmp_path, "a/b")


def test_run_dir_for_id_missing_run_raises(tmp_path):
    with pytest.raises(RunNotFoundError):
        run_dir_for_id(tmp_path, "does_not_exist")


def test_run_dir_for_id_finds_valid_run(tmp_path):
    _make_run_dir(tmp_path, "20260101_000000_baseline", {"scenario": "baseline_no_flood"})
    run_dir = run_dir_for_id(tmp_path, "20260101_000000_baseline")
    assert run_dir.name == "20260101_000000_baseline"


def test_summarize_run_reports_flags(tmp_path):
    run_dir = _make_run_dir(
        tmp_path,
        "20260101_000000_flooded_multiframe",
        {
            "scenario": "flooded_multiframe",
            "run_health": {"teleports": 0},
            "run_valid": True,
        },
    )
    (run_dir / "fcd.xml").write_text("<fcd-export/>")
    (run_dir / "edge_states.csv").write_text("edge_id,max_depth_m,v_max_ms,closed\n")
    summary = summarize_run(run_dir)
    assert summary["id"] == "20260101_000000_flooded_multiframe"
    assert summary["has_fcd"] is True
    assert summary["has_edge_states"] is True
    assert summary["has_flood_raster"] is False  # no forecast_npz in this config
    assert summary["run_valid"] is True


def test_summarize_run_returns_none_without_config(tmp_path):
    stray = tmp_path / "not_a_run"
    stray.mkdir()
    assert summarize_run(stray) is None


def test_list_runs_sorted_newest_first_and_skips_non_runs(tmp_path):
    _make_run_dir(tmp_path, "20260101_000000_baseline", {"scenario": "baseline_no_flood"})
    _make_run_dir(tmp_path, "20260102_000000_baseline", {"scenario": "baseline_no_flood"})
    (tmp_path / "demo_baseline_vs_flooded.html").write_text("<html></html>")
    results = list_runs(tmp_path)
    assert [r["id"] for r in results] == [
        "20260102_000000_baseline",
        "20260101_000000_baseline",
    ]


def test_list_runs_empty_dir_returns_empty_list(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert list_runs(missing) == []
