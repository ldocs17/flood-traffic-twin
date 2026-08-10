"""Unit tests for Slice 6 ("Run from the browser", PROJECT_PLAN.md SG3)'s
pure-Python pieces: request validation (``floodtwin.api.run_request``), the
in-process job tracker (``floodtwin.api.run_jobs``), and scenario listing
(``floodtwin.flood.paths.list_available_scenarios``).

Deliberately excludes anything requiring SUMO/sumolib/FastAPI/TensorFlow --
``floodtwin.api.app`` (route wiring, sumolib-dependent via
``floodtwin.api.network``) and ``floodtwin.sim.runner``/``floodtwin.sim.
controller`` (sumolib/traci-dependent) are exercised by manual end-to-end
verification instead (see the PR description), following
``tests/test_api.py``'s and ``tests/test_sweep.py``'s existing scope
discipline for this repo's CI (no SUMO_HOME, no TensorFlow, ``test`` extra
only -- see ``pyproject.toml`` / ``.github/workflows/ci.yml``).
"""
from __future__ import annotations

import time

import pytest

from floodtwin.api.run_request import (
    InvalidRunRequestError,
    RunRequest,
    parse_run_request,
)
from floodtwin.api import run_jobs
from floodtwin.flood import paths as flood_paths


# ---------------------------------------------------------------------------
# run_request.parse_run_request
# ---------------------------------------------------------------------------


def test_parse_run_request_minimal_body_uses_defaults():
    req = parse_run_request({"storm_scenario": "Sep_30_2022_74.75"})
    assert req == RunRequest(
        storm_scenario="Sep_30_2022_74.75",
        rerouting_probability=1.0,
        seed=42,
        manual_closures=[],
    )


def test_parse_run_request_full_body():
    req = parse_run_request(
        {
            "storm_scenario": "Sep_30_2022_74.75",
            "rerouting_probability": 0.5,
            "seed": 7,
            "manual_closures": ["e2", "e1", "e1"],
        }
    )
    assert req.rerouting_probability == pytest.approx(0.5)
    assert req.seed == 7
    # deduped + sorted
    assert req.manual_closures == ["e1", "e2"]


def test_parse_run_request_strips_whitespace_and_blank_closures():
    req = parse_run_request(
        {
            "storm_scenario": "  Sep_30_2022_74.75  ",
            "manual_closures": ["  e1  ", "", "   "],
        }
    )
    assert req.storm_scenario == "Sep_30_2022_74.75"
    assert req.manual_closures == ["e1"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"storm_scenario": ""},
        {"storm_scenario": "   "},
        {"storm_scenario": 5},
        {"storm_scenario": None},
    ],
)
def test_parse_run_request_requires_nonblank_string_scenario(body):
    with pytest.raises(InvalidRunRequestError):
        parse_run_request(body)


@pytest.mark.parametrize("bad_value", [-0.1, 1.1, "50%", None, True, False])
def test_parse_run_request_rejects_out_of_range_or_wrong_type_rerouting(bad_value):
    # bool is a subclass of int in Python -- True/False are explicitly
    # rejected too, so a stray `true`/`false` in the JSON body doesn't
    # silently become 1.0/0.0.
    with pytest.raises(InvalidRunRequestError):
        parse_run_request({"storm_scenario": "x", "rerouting_probability": bad_value})


def test_parse_run_request_boundary_rerouting_values_are_valid():
    assert parse_run_request({"storm_scenario": "x", "rerouting_probability": 0.0}).rerouting_probability == 0.0
    assert parse_run_request({"storm_scenario": "x", "rerouting_probability": 1.0}).rerouting_probability == 1.0


def test_parse_run_request_rejects_non_integer_seed():
    with pytest.raises(InvalidRunRequestError):
        parse_run_request({"storm_scenario": "x", "seed": 4.5})
    with pytest.raises(InvalidRunRequestError):
        parse_run_request({"storm_scenario": "x", "seed": "42"})


def test_parse_run_request_rejects_non_list_manual_closures():
    with pytest.raises(InvalidRunRequestError):
        parse_run_request({"storm_scenario": "x", "manual_closures": "e1"})


def test_parse_run_request_rejects_non_string_items_in_manual_closures():
    with pytest.raises(InvalidRunRequestError):
        parse_run_request({"storm_scenario": "x", "manual_closures": ["e1", 2]})


def test_parse_run_request_rejects_non_dict_body():
    with pytest.raises(InvalidRunRequestError):
        parse_run_request(["not", "a", "dict"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# run_jobs
# ---------------------------------------------------------------------------


def test_create_job_starts_running_and_is_retrievable():
    job_id = run_jobs.create_job({"storm_scenario": "x"})
    job = run_jobs.get_job(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert job["run_id"] is None
    assert job["error"] is None
    assert job["request"] == {"storm_scenario": "x"}


def test_get_job_unknown_id_returns_none():
    assert run_jobs.get_job("does-not-exist") is None


def test_run_job_success_records_run_id_and_done_status(tmp_path):
    job_id = run_jobs.create_job({})
    fake_run_dir = tmp_path / "20260810_120000_flooded_multiframe"
    fake_run_dir.mkdir()

    run_jobs.run_job(job_id, lambda: fake_run_dir)

    job = run_jobs.get_job(job_id)
    assert job["status"] == "done"
    assert job["run_id"] == "20260810_120000_flooded_multiframe"
    assert job["error"] is None
    assert job["finished_at"] is not None


def test_run_job_failure_records_error_status_and_message():
    job_id = run_jobs.create_job({})

    def boom():
        raise RuntimeError("sumo run failed (exit 1)")

    run_jobs.run_job(job_id, boom)

    job = run_jobs.get_job(job_id)
    assert job["status"] == "error"
    assert "sumo run failed" in job["error"]
    assert job["run_id"] is None
    assert "RuntimeError" in job["traceback"]


def test_run_job_unknown_job_id_does_not_raise():
    # Defensive: a job id that was never created (or got GC'd in some
    # future eviction policy) shouldn't crash the background task.
    run_jobs.run_job("never-created", lambda: 1 / 0)


def test_list_jobs_returns_all_created_jobs():
    before = len(run_jobs.list_jobs())
    run_jobs.create_job({"a": 1})
    run_jobs.create_job({"b": 2})
    assert len(run_jobs.list_jobs()) == before + 2


def test_jobs_are_independent_snapshots_not_live_references():
    # get_job/list_jobs return copies -- mutating the returned dict must not
    # corrupt the tracker's internal state.
    job_id = run_jobs.create_job({})
    snapshot = run_jobs.get_job(job_id)
    snapshot["status"] = "corrupted"
    assert run_jobs.get_job(job_id)["status"] == "running"


# ---------------------------------------------------------------------------
# flood.paths.list_available_scenarios (Slice 6 scenario dropdown)
# ---------------------------------------------------------------------------


def test_list_available_scenarios_empty_when_input_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(flood_paths, "EXAMPLE_INPUT_DIR", tmp_path / "does_not_exist")
    assert flood_paths.list_available_scenarios() == []


def test_list_available_scenarios_lists_npy_stems_sorted(monkeypatch, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "Sep_30_2022_75.00.npy").write_bytes(b"")
    (input_dir / "Aug_29_2017_100.00.npy").write_bytes(b"")
    (input_dir / "not_a_scenario.txt").write_bytes(b"")
    monkeypatch.setattr(flood_paths, "EXAMPLE_INPUT_DIR", input_dir)
    monkeypatch.setattr(flood_paths, "SCENARIOS_DIR", tmp_path / "scenarios")

    scenarios = flood_paths.list_available_scenarios()
    names = [s["name"] for s in scenarios]
    assert names == ["Aug_29_2017_100.00", "Sep_30_2022_75.00"]
    assert all(s["cached"] is False for s in scenarios)


def test_list_available_scenarios_reports_cached_flag(monkeypatch, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "Sep_30_2022_75.00.npy").write_bytes(b"")
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "Sep_30_2022_75.00_v1_random_s42_forecast.npz").write_bytes(b"")
    monkeypatch.setattr(flood_paths, "EXAMPLE_INPUT_DIR", input_dir)
    monkeypatch.setattr(flood_paths, "SCENARIOS_DIR", scenarios_dir)

    scenarios = flood_paths.list_available_scenarios()
    assert scenarios == [{"name": "Sep_30_2022_75.00", "cached": True}]
