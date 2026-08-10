"""Unit tests for the Slice 3 baseline-vs-flooded metrics module
(PROJECT_PLAN.md SG2 Slice 3). Pure-Python: no SUMO install, no TraCI, no
TensorFlow -- synthetic tripinfo/vehroutes/edge_states files and parsed
dicts stand in for real run artifacts, following tests/test_coupling.py's
pattern.
"""
import json

import pytest

from floodtwin.analysis.metrics import (
    closure_timeline,
    compute_and_write,
    compute_metrics,
    exposure_summary,
    flooded_edge_sets,
    parse_edge_states_csv,
    parse_tripinfo,
    throughput_summary,
    travel_time_comparison,
    write_trip_csv,
)


# ---------------------------------------------------------------------------
# parse_tripinfo (I/O)
# ---------------------------------------------------------------------------

TRIPINFO_XML = """<?xml version="1.0"?>
<tripinfos>
    <tripinfo id="v1" depart="10.00" arrival="60.00" duration="50.00" routeLength="500.00"
              waitingTime="5.00" timeLoss="8.00" rerouteNo="0"/>
    <tripinfo id="v2" depart="20.00" arrival="140.00" duration="120.00" routeLength="900.00"
              waitingTime="30.00" timeLoss="45.00" rerouteNo="2"/>
</tripinfos>
"""


def test_parse_tripinfo(tmp_path):
    path = tmp_path / "tripinfo.xml"
    path.write_text(TRIPINFO_XML)
    trips = parse_tripinfo(path)
    assert set(trips) == {"v1", "v2"}
    assert trips["v1"]["duration"] == pytest.approx(50.0)
    assert trips["v2"]["rerouteNo"] == 2
    assert trips["v2"]["waitingTime"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# travel_time_comparison (pure)
# ---------------------------------------------------------------------------

def test_travel_time_comparison_matches_by_vehicle_id():
    baseline = {"v1": {"duration": 50.0}, "v2": {"duration": 100.0}}
    flooded = {"v1": {"duration": 80.0}, "v2": {"duration": 100.0}}
    result = travel_time_comparison(baseline, flooded)
    summary = result["summary"]
    assert summary["n_matched"] == 2
    assert summary["n_baseline_only"] == 0
    assert summary["n_flooded_only"] == 0
    assert summary["baseline_mean_travel_time_s"] == pytest.approx(75.0)
    assert summary["flooded_mean_travel_time_s"] == pytest.approx(90.0)
    assert summary["mean_delta_s"] == pytest.approx(15.0)
    # deltas: v1 +30, v2 +0
    deltas = {r["vehicle_id"]: r["delta_s"] for r in result["per_trip"]}
    assert deltas["v1"] == pytest.approx(30.0)
    assert deltas["v2"] == pytest.approx(0.0)


def test_travel_time_comparison_handles_unmatched_honestly():
    # Real-run behavior verified against Slice 2 run pairs: flooded arrivals
    # are a strict subset of baseline arrivals (vehicles still en route past
    # the horizon, or discarded because their destination edge closed, drop
    # out of the flooded tripinfo -- never the other way around).
    baseline = {"v1": {"duration": 50.0}, "v2": {"duration": 100.0}, "v3": {"duration": 40.0}}
    flooded = {"v1": {"duration": 80.0}}
    result = travel_time_comparison(baseline, flooded)
    summary = result["summary"]
    assert summary["n_matched"] == 1
    assert summary["n_baseline_only"] == 2
    assert summary["n_flooded_only"] == 0
    assert set(result["baseline_only_ids"]) == {"v2", "v3"}
    # Unmatched vehicles are reported in per_trip, not dropped -- with a null
    # delta (not zero -- there is no flooded-run travel time to compare).
    per_trip_by_id = {r["vehicle_id"]: r for r in result["per_trip"]}
    assert per_trip_by_id["v2"]["flooded_travel_time_s"] is None
    assert per_trip_by_id["v2"]["delta_s"] is None
    assert "WARNING" not in summary  # baseline-only is the expected direction


def test_travel_time_comparison_flags_unexpected_flooded_only():
    # A vehicle ID present in flooded but not baseline is the *unexpected*
    # direction -- likely means the two runs don't actually share demand/seed.
    baseline = {"v1": {"duration": 50.0}}
    flooded = {"v1": {"duration": 60.0}, "v_mystery": {"duration": 10.0}}
    result = travel_time_comparison(baseline, flooded)
    assert result["summary"]["n_flooded_only"] == 1
    assert "WARNING" in result["summary"]


def test_travel_time_comparison_empty_matched_set_returns_none_not_crash():
    result = travel_time_comparison({"v1": {"duration": 1.0}}, {"v2": {"duration": 2.0}})
    summary = result["summary"]
    assert summary["n_matched"] == 0
    assert summary["baseline_mean_travel_time_s"] is None
    assert summary["mean_delta_s"] is None


# ---------------------------------------------------------------------------
# flooded_edge_sets / exposure_summary (pure)
# ---------------------------------------------------------------------------

def test_flooded_edge_sets_closed_vs_wet():
    rows = [
        {"edge_id": "a", "max_depth_m": 0.5, "closed": True},
        {"edge_id": "b", "max_depth_m": 0.1, "closed": False},
        {"edge_id": "c", "max_depth_m": 0.0, "closed": False},
    ]
    closed, wet = flooded_edge_sets(rows)
    assert closed == {"a"}
    assert wet == {"a", "b"}


def test_exposure_summary_counts_vehicles_touching_closed_edge():
    vehicle_edges = {
        "v1": {"e1", "e2"},  # touches closed edge e2
        "v2": {"e3"},        # dry
        "v3": {"e2", "e4"},  # touches closed edge e2
    }
    closed_edges = {"e2"}
    wet_edges = {"e2", "e4"}
    summary = exposure_summary(vehicle_edges, closed_edges, wet_edges)
    assert summary["total_vehicles_with_recorded_route"] == 3
    assert summary["n_exposed_closed_edge"] == 2
    assert summary["pct_exposed_closed_edge"] == pytest.approx(200.0 / 3.0)
    assert set(summary["exposed_closed_edge_vehicle_ids"]) == {"v1", "v3"}
    # v3 also touches a wet-but-open edge e4, still counted in wet exposure
    assert summary["n_exposed_wet_edge"] == 2


def test_exposure_summary_empty_population_no_zero_division():
    summary = exposure_summary({}, {"e1"}, {"e1"})
    assert summary["total_vehicles_with_recorded_route"] == 0
    assert summary["pct_exposed_closed_edge"] is None


# ---------------------------------------------------------------------------
# throughput_summary (pure)
# ---------------------------------------------------------------------------

def test_throughput_summary():
    baseline_health = {"arrived": 1398, "loaded": 1426, "inserted": 1426, "running": 28}
    flooded_health = {"arrived": 1335, "loaded": 1426, "inserted": 1410, "running": 75}
    result = throughput_summary(baseline_health, flooded_health)
    assert result["baseline_arrived"] == 1398
    assert result["flooded_arrived"] == 1335
    assert result["delta_arrived"] == -63
    assert result["flooded_discarded_before_insertion"] == 16


# ---------------------------------------------------------------------------
# closure_timeline (pure)
# ---------------------------------------------------------------------------

def test_closure_timeline_prefers_config_per_frame_summary():
    config = {"per_frame_summary": [{"mark_s": 900.0, "n_closed": 29, "n_slowed": 40, "n_full_speed": 879}]}
    result = closure_timeline(config, edge_state_rows=[])
    assert result == config["per_frame_summary"]


def test_closure_timeline_falls_back_to_multiframe_csv_rows():
    rows = [
        {"frame_min": 15, "edge_id": "a", "max_depth_m": 0.5, "closed": True},
        {"frame_min": 15, "edge_id": "b", "max_depth_m": 0.05, "closed": False},
        {"frame_min": 15, "edge_id": "c", "max_depth_m": 0.0, "closed": False},
        {"frame_min": 30, "edge_id": "a", "max_depth_m": 0.0, "closed": False},
        {"frame_min": 30, "edge_id": "b", "max_depth_m": 0.0, "closed": False},
        {"frame_min": 30, "edge_id": "c", "max_depth_m": 0.0, "closed": False},
    ]
    result = closure_timeline({}, rows)
    assert result[0]["mark_s"] == 900
    assert result[0]["n_closed"] == 1
    assert result[0]["n_slowed_approx"] == 1
    assert result[1]["mark_s"] == 1800
    assert result[1]["n_closed"] == 0


def test_closure_timeline_falls_back_to_single_frame_csv():
    rows = [
        {"edge_id": "a", "max_depth_m": 0.5, "closed": True},
        {"edge_id": "b", "max_depth_m": 0.0, "closed": False},
    ]
    result = closure_timeline({"closure_time_s": 900.0}, rows)
    assert len(result) == 1
    assert result[0]["n_closed"] == 1
    assert result[0]["n_slowed"] is None


# ---------------------------------------------------------------------------
# parse_edge_states_csv (I/O)
# ---------------------------------------------------------------------------

def test_parse_edge_states_csv_multiframe(tmp_path):
    path = tmp_path / "edge_states.csv"
    path.write_text(
        "frame_min,edge_id,max_depth_m,v_max_ms,closed\n"
        "15,a,0.4819,0.000,1\n"
        "15,b,0.0000,5.560,0\n"
    )
    rows = parse_edge_states_csv(path)
    assert rows[0]["frame_min"] == 15
    assert rows[0]["closed"] is True
    assert rows[1]["closed"] is False
    assert rows[1]["max_depth_m"] == pytest.approx(0.0)


def test_parse_edge_states_csv_single_frame(tmp_path):
    path = tmp_path / "edge_states.csv"
    path.write_text("edge_id,max_depth_m,v_max_ms,closed\na,0.5,0.0,1\n")
    rows = parse_edge_states_csv(path)
    assert "frame_min" not in rows[0]
    assert rows[0]["closed"] is True


# ---------------------------------------------------------------------------
# write_trip_csv (I/O)
# ---------------------------------------------------------------------------

def test_write_trip_csv_round_trip(tmp_path):
    per_trip = [
        {"vehicle_id": "v1", "baseline_travel_time_s": 50.0, "flooded_travel_time_s": 80.0, "delta_s": 30.0, "exposed": True},
        {"vehicle_id": "v2", "baseline_travel_time_s": 40.0, "flooded_travel_time_s": None, "delta_s": None, "exposed": None},
    ]
    path = write_trip_csv(per_trip, tmp_path / "trip_metrics.csv")
    content = path.read_text()
    lines = content.strip().splitlines()
    assert lines[0] == "vehicle_id,baseline_travel_time_s,flooded_travel_time_s,delta_s,exposed"
    assert lines[1] == "v1,50.0,80.0,30.0,True"
    assert lines[2] == "v2,40.0,,,"


# ---------------------------------------------------------------------------
# compute_metrics / compute_and_write -- full pipeline on synthetic run dirs
# ---------------------------------------------------------------------------

def _write_run_dir(tmp_path, name, config, tripinfo_xml, vehroutes_xml=None, edge_states_csv=None):
    run_dir = tmp_path / name
    run_dir.mkdir()
    (run_dir / "config.json").write_text(json.dumps(config))
    (run_dir / "tripinfo.xml").write_text(tripinfo_xml)
    if vehroutes_xml is not None:
        (run_dir / "vehroutes.xml").write_text(vehroutes_xml)
    if edge_states_csv is not None:
        (run_dir / "edge_states.csv").write_text(edge_states_csv)
    return run_dir


def test_compute_metrics_end_to_end(tmp_path):
    baseline_config = {
        "scenario": "baseline_no_flood",
        "seed": 42,
        "route_file": "routes.xml",
        "net_file": "net.xml",
        "run_health": {"arrived": 3, "loaded": 3, "inserted": 3, "running": 0, "teleports": 0},
    }
    flooded_config = {
        "scenario": "flooded_multiframe",
        "storm_scenario": "Test_Storm",
        "seed": 42,
        "route_file": "routes.xml",
        "net_file": "net.xml",
        "run_health": {"arrived": 2, "loaded": 3, "inserted": 3, "running": 1, "teleports": 0},
        "run_valid": True,
        "per_frame_summary": [{"mark_s": 900.0, "label": "t+15min", "n_closed": 1, "n_slowed": 1, "n_full_speed": 1}],
    }
    baseline_trips_xml = (
        '<tripinfos>'
        '<tripinfo id="v1" depart="0" arrival="50" duration="50" routeLength="500" waitingTime="0" timeLoss="0" rerouteNo="0"/>'
        '<tripinfo id="v2" depart="0" arrival="60" duration="60" routeLength="600" waitingTime="0" timeLoss="0" rerouteNo="0"/>'
        '<tripinfo id="v3" depart="0" arrival="40" duration="40" routeLength="400" waitingTime="0" timeLoss="0" rerouteNo="0"/>'
        '</tripinfos>'
    )
    flooded_trips_xml = (
        '<tripinfos>'
        '<tripinfo id="v1" depart="0" arrival="90" duration="90" routeLength="500" waitingTime="10" timeLoss="20" rerouteNo="1"/>'
        '<tripinfo id="v2" depart="0" arrival="65" duration="65" routeLength="600" waitingTime="0" timeLoss="2" rerouteNo="0"/>'
        '</tripinfos>'
    )
    flooded_vehroutes_xml = (
        '<routes>'
        '<vehicle id="v1"><route edges="e1 e2_closed"/></vehicle>'
        '<vehicle id="v2"><route edges="e3 e4"/></vehicle>'
        '</routes>'
    )
    edge_states_csv = (
        "frame_min,edge_id,max_depth_m,v_max_ms,closed\n"
        "15,e2_closed,0.5,0.0,1\n"
        "15,e3,0.0,10.0,0\n"
    )

    baseline_dir = _write_run_dir(tmp_path, "baseline", baseline_config, baseline_trips_xml)
    flooded_dir = _write_run_dir(
        tmp_path, "flooded", flooded_config, flooded_trips_xml,
        vehroutes_xml=flooded_vehroutes_xml, edge_states_csv=edge_states_csv,
    )

    metrics, per_trip = compute_metrics(baseline_dir, flooded_dir)

    assert metrics["vehicle_id_matching"]["n_matched"] == 2
    assert metrics["vehicle_id_matching"]["n_baseline_only"] == 1
    assert metrics["vehicle_id_matching"]["warnings"] == []
    assert metrics["travel_time"]["mean_delta_s"] == pytest.approx(((90 - 50) + (65 - 60)) / 2)
    assert metrics["exposure"]["n_exposed_closed_edge"] == 1  # v1 touched e2_closed
    assert metrics["exposure"]["total_vehicles_with_recorded_route"] == 2
    assert metrics["throughput"]["baseline_arrived"] == 3
    assert metrics["throughput"]["flooded_arrived"] == 2
    # closure_timeline carried verbatim from config's per_frame_summary
    assert metrics["closure_timeline"] == flooded_config["per_frame_summary"]
    assert metrics["source_runs"]["baseline_dir"] == str(baseline_dir)
    assert metrics["source_runs"]["flooded_dir"] == str(flooded_dir)

    per_trip_by_id = {r["vehicle_id"]: r for r in per_trip}
    assert per_trip_by_id["v1"]["exposed"] is True
    assert per_trip_by_id["v2"]["exposed"] is False
    assert per_trip_by_id["v3"]["exposed"] is None  # no recorded route in flooded run at all


def test_compute_and_write_creates_outputs(tmp_path):
    matplotlib = pytest.importorskip("matplotlib")

    baseline_config = {
        "scenario": "baseline_no_flood", "seed": 1, "route_file": "r.xml", "net_file": "n.xml",
        "run_health": {"arrived": 1, "loaded": 1, "inserted": 1, "running": 0, "teleports": 0},
    }
    flooded_config = {
        "scenario": "flooded_multiframe", "storm_scenario": "Unit_Test_Storm", "seed": 1,
        "route_file": "r.xml", "net_file": "n.xml",
        "run_health": {"arrived": 1, "loaded": 1, "inserted": 1, "running": 0, "teleports": 0},
        "run_valid": True,
    }
    trips_xml = (
        '<tripinfos><tripinfo id="v1" depart="0" arrival="10" duration="10" routeLength="100" '
        'waitingTime="0" timeLoss="0" rerouteNo="0"/></tripinfos>'
    )
    vehroutes_xml = '<routes><vehicle id="v1"><route edges="e1"/></vehicle></routes>'
    edge_states_csv = "frame_min,edge_id,max_depth_m,v_max_ms,closed\n15,e1,0.0,10.0,0\n"

    baseline_dir = _write_run_dir(tmp_path, "baseline", baseline_config, trips_xml)
    flooded_dir = _write_run_dir(
        tmp_path, "flooded", flooded_config, trips_xml,
        vehroutes_xml=vehroutes_xml, edge_states_csv=edge_states_csv,
    )
    out_dir = tmp_path / "metrics_out"
    result_dir = compute_and_write(baseline_dir, flooded_dir, out_dir=out_dir)

    assert result_dir == out_dir
    assert (out_dir / "metrics.json").exists()
    assert (out_dir / "trip_metrics.csv").exists()
    assert (out_dir / "travel_time_distribution.png").exists()
    metrics = json.loads((out_dir / "metrics.json").read_text())
    assert metrics["outputs"]["metrics_json"]
