"""Slice 6: in-process job tracking for ``POST /api/runs``.

Execution model (see the PR description for the full justification): a
scenario run is a real SUMO simulation plus, on a forecast-cache miss, a
~30s flood-model inference cold start under a second interpreter
(IMPLEMENTATION_CONTEXT.md G2 / PROGRESS.md's Slice 2 note) -- long enough
that holding the HTTP request open for the whole pipeline would be a poor
fit for a browser form. Instead ``POST /api/runs`` returns a ``job_id``
immediately and the pipeline runs via a FastAPI ``BackgroundTasks`` call
(Starlette runs a sync background callable in a threadpool, so it doesn't
block the event loop or other requests -- including polls for this same
job); the frontend polls ``GET /api/run_jobs/{job_id}`` until the status
leaves ``"running"``.

Deliberately process-local, not persisted: a run's *artifact*
(``runs/<id>/``) is the durable record (PROJECT_PLAN.md #2 "Run artifact");
this dict only tracks in-flight/just-finished job status so the frontend
knows when to stop polling and load the replay. Restarting the API server
loses any in-flight job's tracking entry, which is acceptable -- the
SUMO/TraCI subprocess it was polling for is tied to that same server
process anyway.

Pure stdlib (threading/uuid/datetime) -- unit-testable without SUMO/TF.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}


def create_job(request: Dict[str, Any]) -> str:
    """Register a new job in ``"running"`` state and return its id."""
    job_id = uuid.uuid4().hex
    with _LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "request": request,
            "run_id": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
            "finished_at": None,
        }
    return job_id


def run_job(job_id: str, run_fn: Callable[[], Any]) -> None:
    """Execute ``run_fn`` (a zero-arg callable returning a run directory
    ``Path``, e.g. ``floodtwin.sim.runner.run_flooded_multiframe`` bound to
    its arguments) and record the outcome against ``job_id``. Meant to be
    scheduled as a FastAPI background task -- see module docstring for why
    that doesn't block the server."""
    try:
        run_dir = run_fn()
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return
            job["status"] = "done"
            job["run_id"] = run_dir.name
            job["finished_at"] = datetime.now().isoformat()
    except Exception as e:  # noqa: BLE001 -- deliberately broad: any pipeline
        # failure (bad scenario name, SUMO crash, TF subprocess failure,
        # unknown edge id slipping past the pre-check) must be recorded as
        # a job error, not crash the background task silently.
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return
            job["status"] = "error"
            job["error"] = str(e)
            job["traceback"] = traceback.format_exc()
            job["finished_at"] = datetime.now().isoformat()


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


def list_jobs() -> List[Dict[str, Any]]:
    with _LOCK:
        return [dict(j) for j in _JOBS.values()]
