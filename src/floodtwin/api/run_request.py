"""Slice 6 ("Run from the browser", PROJECT_PLAN.md SG3): request schema +
validation for ``POST /api/runs``.

Deliberately plain stdlib (``dataclasses``), not a FastAPI/pydantic model:
this repo's existing API modules (``edge_states.py``, ``fcd.py``,
``flood_raster.py``'s ``FloodSource``) all keep their data shapes as
pure-Python objects with hand-written validation, importable and
unit-testable under the ``test`` extra's minimal dependency set (no
FastAPI/pydantic/sumolib -- see ``pyproject.toml`` and
``floodtwin.api.network``'s module docstring for the same scope
discipline). This module follows that convention so its validation logic
gets real CI coverage instead of only being exercised transitively through
FastAPI's request binding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


class InvalidRunRequestError(ValueError):
    """Raised for a malformed ``POST /api/runs`` body. The API layer turns
    this into an HTTP 400 with the message as the detail."""


@dataclass
class RunRequest:
    """Validated body for ``POST /api/runs``: enough to reproduce
    ``floodtwin.sim.runner.run_flooded_multiframe``'s parameters from the
    browser -- storm scenario, rerouting fraction (D5), seed, and the Slice
    6 "intervention" feature (manual edge closures, additive to the
    flood-derived ones)."""

    storm_scenario: str
    rerouting_probability: float = 1.0
    seed: int = 42
    manual_closures: List[str] = field(default_factory=list)


def parse_run_request(body: Dict[str, Any]) -> RunRequest:
    """Validate + normalize a raw JSON body into a :class:`RunRequest`.
    Raises :class:`InvalidRunRequestError` (with a message naming the
    problem) for anything malformed -- missing/blank scenario, an
    out-of-range rerouting probability, a non-integer seed, or a
    non-string/non-list ``manual_closures``.

    Does NOT check the scenario name or manual-closure edge IDs against
    real data (the scenario list / the net) -- that requires filesystem
    and sumolib access this module deliberately doesn't have; see
    ``floodtwin.sim.runner.validate_manual_closures`` and
    ``floodtwin.api.app``'s ``/api/runs`` route for those checks.
    """
    if not isinstance(body, dict):
        raise InvalidRunRequestError("request body must be a JSON object")

    storm_scenario = body.get("storm_scenario")
    if not isinstance(storm_scenario, str) or not storm_scenario.strip():
        raise InvalidRunRequestError("storm_scenario is required and must be a non-empty string")
    storm_scenario = storm_scenario.strip()

    rerouting_probability = body.get("rerouting_probability", 1.0)
    if isinstance(rerouting_probability, bool) or not isinstance(rerouting_probability, (int, float)):
        raise InvalidRunRequestError("rerouting_probability must be a number")
    rerouting_probability = float(rerouting_probability)
    if not (0.0 <= rerouting_probability <= 1.0):
        raise InvalidRunRequestError("rerouting_probability must be between 0.0 and 1.0")

    seed = body.get("seed", 42)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise InvalidRunRequestError("seed must be an integer")

    manual_closures = body.get("manual_closures", [])
    if not isinstance(manual_closures, list) or not all(isinstance(e, str) for e in manual_closures):
        raise InvalidRunRequestError("manual_closures must be a list of edge id strings")
    cleaned_closures = sorted({eid.strip() for eid in manual_closures if eid and eid.strip()})

    return RunRequest(
        storm_scenario=storm_scenario,
        rerouting_probability=rerouting_probability,
        seed=seed,
        manual_closures=cleaned_closures,
    )
