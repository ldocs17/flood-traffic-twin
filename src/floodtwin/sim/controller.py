"""TraCI controller: steps a SUMO simulation and applies edge states (closures
and/or Pregnolato speed reductions) once simulated time reaches target marks.
Slice 1 applied exactly one frame's closures at t=15min / 900s
(:func:`run_with_closures`); Slice 2 applies the full per-edge state
(:func:`speeds_and_closures`) at all four 15-min marks
(:func:`run_with_edge_states`). Kept separate from run orchestration
(``runner.py``) so the artifact plumbing doesn't need to know about TraCI.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from floodtwin.sumo_env import ensure_sumo_tools_on_path

ensure_sumo_tools_on_path()
import traci  # noqa: E402


def run_with_closures(
    sumo_cmd: List[str],
    closures: Set[str],
    closure_time_s: float,
    end_time_s: float,
) -> dict:
    """Start SUMO under TraCI using ``sumo_cmd``, step the simulation forward
    to ``end_time_s``, and apply ``closures`` via
    ``traci.edge.setDisallowed(edge, ["all"])`` the first time simulated time
    reaches ``closure_time_s``. Returns a small dict describing what happened
    (for the run's ``config.json``).
    """
    traci.start(sumo_cmd)
    applied = False
    apply_time: Optional[float] = None
    try:
        while True:
            t = traci.simulation.getTime()
            if t >= end_time_s:
                break
            if not applied and t >= closure_time_s:
                for edge_id in closures:
                    traci.edge.setDisallowed(edge_id, ["all"])
                applied = True
                apply_time = t
            if applied and traci.simulation.getMinExpectedNumber() <= 0:
                break
            traci.simulationStep()
    finally:
        traci.close()
    return {
        "closures_applied": applied,
        "closure_applied_at_s": apply_time,
        "n_closed_edges": len(closures),
    }


def run_with_edge_states(
    sumo_cmd: List[str],
    edge_states_by_mark: Dict[float, Dict[str, Tuple[float, bool]]],
    end_time_s: float,
) -> dict:
    """Start SUMO under TraCI using ``sumo_cmd``, step the simulation forward
    to ``end_time_s``, applying ``edge_states_by_mark[mark]`` (each value a
    ``{edge_id: (v_max_ms, closed)}`` dict from
    :func:`floodtwin.coupling.edge_mapper.speeds_and_closures`) the first
    time simulated time reaches each ``mark``, in ascending order (Slice 2:
    all four 15-min marks, not just one).

    Closures use ``traci.edge.setDisallowed(edge, ["all"])``; if an edge
    that was previously closed is open in a later frame (depth receding),
    it's reopened with ``setAllowed(edge, ["all"])`` before its new max
    speed is applied. Non-closed edges get ``traci.edge.setMaxSpeed``.

    Trapped-vehicle handling (D5 / Plan R4): vehicles already on an edge
    when it closes are NOT relocated -- they stop and block it, which is the
    realistic behavior we want (hiding this with teleports would hide
    congestion). This is exactly what SUMO does by default when an edge
    becomes disallowed mid-route with ``--time-to-teleport -1`` (teleporting
    disabled); nothing in this function forces vehicles off a closed edge.
    """
    traci.start(sumo_cmd)
    marks = sorted(edge_states_by_mark.keys())
    applied_marks: List[float] = []
    currently_closed: Set[str] = set()
    try:
        idx = 0
        while True:
            t = traci.simulation.getTime()
            if t >= end_time_s:
                break
            while idx < len(marks) and t >= marks[idx]:
                mark = marks[idx]
                state = edge_states_by_mark[mark]
                for edge_id, (v_max_ms, closed) in state.items():
                    if closed:
                        traci.edge.setDisallowed(edge_id, ["all"])
                        currently_closed.add(edge_id)
                    else:
                        if edge_id in currently_closed:
                            traci.edge.setAllowed(edge_id, ["all"])
                            currently_closed.discard(edge_id)
                        traci.edge.setMaxSpeed(edge_id, v_max_ms)
                applied_marks.append(mark)
                idx += 1
            if idx >= len(marks) and traci.simulation.getMinExpectedNumber() <= 0:
                break
            traci.simulationStep()
    finally:
        traci.close()
    return {
        "marks_applied_s": applied_marks,
        "n_marks_applied": len(applied_marks),
    }
