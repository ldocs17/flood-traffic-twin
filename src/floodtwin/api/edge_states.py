"""``edge_states.csv`` -> frontend-friendly JSON, and the edge-state ->
color mapping used to draw the network colored by open/slowed/closed.

Two CSV shapes exist in the wild (both written by
``floodtwin.sim.artifact``):

- Slice 1 single-frame: ``edge_id, max_depth_m, v_max_ms, closed`` -- one
  row per edge, applying at whatever ``closure_time_s`` the run's
  ``config.json`` records.
- Slice 2 multiframe: ``frame_min, edge_id, max_depth_m, v_max_ms, closed``
  -- one row per (edge, 15-min mark).

Both are normalized here into the same ``{mark_s: {edge_id: state}}``
shape so the frontend doesn't need to know which run type it's looking at.
Pure stdlib csv module -- no pandas/sumolib dependency, safe to unit test.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Colors chosen for a dark MapLibre basemap: green=open, amber=slowed,
# red=closed -- a standard traffic-light convention readers will recognize
# without consulting the legend.
COLOR_OPEN = "#2ecc71"
COLOR_SLOWED = "#f39c12"
COLOR_CLOSED = "#e74c3c"

# An edge counts as "slowed" (vs "open") when its flood-imposed max speed is
# more than this fraction below the edge's own speed limit -- guards against
# floating point noise making every edge nominally "slowed" by a fraction of
# a percent.
SLOWDOWN_EPS_FRACTION = 0.02


def edge_state_color(
    closed: bool, v_max_ms: float, speed_limit_ms: Optional[float] = None
) -> str:
    """Pure open/slowed/closed -> hex color mapping.

    ``speed_limit_ms`` is optional: when known, "slowed" is detected by
    comparing ``v_max_ms`` against it (matches
    ``floodtwin.sim.runner._frame_counts``' definition). When unknown (the
    edge-states CSV alone doesn't carry the original speed limit), any
    positive-but-below-typical-urban-speed value is treated as a reasonable
    proxy is intentionally NOT used -- instead callers without a speed
    limit should treat ``v_max_ms`` as informational and rely on ``closed``
    only. This function still requires a definite closed/open value; pass
    ``speed_limit_ms=None`` to fall back to the coarser open/closed-only
    (never "slowed") mapping.
    """
    if closed:
        return COLOR_CLOSED
    if speed_limit_ms is not None and speed_limit_ms > 0:
        if v_max_ms < speed_limit_ms * (1 - SLOWDOWN_EPS_FRACTION):
            return COLOR_SLOWED
    return COLOR_OPEN


def parse_edge_states_csv(
    path: Path, default_mark_s: float = 0.0
) -> Dict[float, Dict[str, Tuple[float, float, bool]]]:
    """Parse either CSV shape into ``{mark_s: {edge_id: (max_depth_m,
    v_max_ms, closed)}}``.

    Single-frame files (no ``frame_min`` column) are reported under
    ``default_mark_s`` (callers pass the run's ``closure_time_s`` from
    ``config.json``, defaulting to 0.0 if that's also absent).
    """
    result: Dict[float, Dict[str, Tuple[float, float, bool]]] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        has_frame_min = reader.fieldnames is not None and "frame_min" in reader.fieldnames
        for row in reader:
            mark_s = float(row["frame_min"]) * 60.0 if has_frame_min else default_mark_s
            edge_id = row["edge_id"]
            max_depth_m = float(row["max_depth_m"])
            v_max_ms = float(row["v_max_ms"])
            closed = bool(int(row["closed"]))
            result.setdefault(mark_s, {})[edge_id] = (max_depth_m, v_max_ms, closed)
    return result


def edge_states_to_json(
    parsed: Dict[float, Dict[str, Tuple[float, float, bool]]],
    speed_limits_ms: Optional[Dict[str, float]] = None,
) -> Dict:
    """Convert ``parse_edge_states_csv``'s output into the JSON payload the
    frontend consumes:

        {
          "marks_s": [900.0, 1800.0, ...],
          "frames": [
            {"mark_s": 900.0, "edges": {edge_id: {"depth_m", "v_max_ms", "closed", "color"}}},
            ...
          ]
        }
    """
    marks = sorted(parsed.keys())
    frames = []
    for mark in marks:
        edges = {}
        for edge_id, (depth_m, v_max_ms, closed) in parsed[mark].items():
            speed_limit = (speed_limits_ms or {}).get(edge_id)
            edges[edge_id] = {
                "depth_m": depth_m,
                "v_max_ms": v_max_ms,
                "closed": closed,
                "color": edge_state_color(closed, v_max_ms, speed_limit),
            }
        frames.append({"mark_s": mark, "edges": edges})
    return {"marks_s": marks, "frames": frames}
