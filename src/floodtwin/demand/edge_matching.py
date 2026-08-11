"""Match named VDOT road segments (:class:`floodtwin.demand.vdot.CalibrationSegment`)
to ``data/net/district.net.xml`` edges.

Reuses the same technique ``data/net/README.md`` documents for the Slice 1
crop-corridor verification: sample points along the named road's real
geometry, convert to net XY, and look for a net edge within a small
tolerance (25 m, same value Slice 1 used) via ``sumolib``'s
``Net.getNeighboringEdges``.

Duck-typed against ``net`` (only needs ``.convertLonLat2XY(lon, lat)`` and
``.getNeighboringEdges(x, y, r)``) so this stays unit-testable with a fake
net and has no hard import-time dependency on ``sumolib`` -- following
``floodtwin.coupling.edge_mapper``'s pattern for the same reason (CI runs
without a SUMO install).

**Two-stage matching, not one shot.** A naive "any net edge within 25m of
any VDOT polyline vertex" match is too permissive in this dense urban grid:
a VDOT count segment can span several blocks, and at 25m tolerance that
polyline passes close to cross streets, driveways, and parallel minor roads
the whole way -- a probe run against the real data matched some VDOT
segments to 90+ net edges, most of them clearly not Hampton Blvd/Colley Ave
(different streets, different speed classes, edge lengths in the low
centimeters that are turn-lane/junction-internal geometry). The fix is the
same one the task description points at: first narrow to *only* the named
corridor's own edges (:func:`corridor_edge_ids`, using
``sumo_norfolk/road_segments.json``'s digitized Hampton
Boulevard/Colley Avenue points -- the exact technique
``data/net/README.md`` used for the Slice 1 crop verification), then match
each VDOT count segment's polyline against *that* restricted candidate set
(``candidate_edge_ids=`` on :func:`match_segment_to_edges`). Restricting the
candidates first removes the cross-street contamination without needing a
tighter (and therefore fragile) tolerance.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_TOLERANCE_M = 25.0  # Slice 1's data/net/README.md corridor-verification tolerance.

# corridor_edge_ids' bearing filter (see its docstring for why it's needed):
# a candidate edge is kept only if its own shape's overall direction is
# within this many degrees of the corridor's direction (mod 180, since a
# corridor has edges running both ways). 25 degrees comfortably covers a
# gently curving arterial while rejecting near-perpendicular cross streets
# (~90 degrees off) -- tuned empirically against the real Hampton Blvd/
# Colley Ave data (see the Slice 7 PR description for the probe that
# motivated this).
DEFAULT_MAX_BEARING_DIFF_DEG = 25.0


def match_polyline_to_edges(
    net,
    polyline: List[Tuple[float, float]],
    tolerance_m: float = DEFAULT_TOLERANCE_M,
    candidate_edge_ids: Optional[Set[str]] = None,
) -> Dict[str, int]:
    """Sample every vertex of ``polyline`` (a list of ``(lon, lat)``) and
    return ``{edge_id: n_points_within_tolerance}`` -- a hit-count per
    matched edge, so callers can tell a strong match (many points close to
    one edge) from a marginal one (a single point at the tolerance
    boundary).

    ``candidate_edge_ids``, if given, restricts matches to that set (see
    module docstring for why this matters in practice) -- edges found by
    ``getNeighboringEdges`` outside the set are simply not counted."""
    hits: Dict[str, int] = {}
    for lon, lat in polyline:
        x, y = net.convertLonLat2XY(lon, lat)
        for edge, _dist in net.getNeighboringEdges(x, y, tolerance_m):
            edge_id = edge.getID() if hasattr(edge, "getID") else str(edge)
            if candidate_edge_ids is not None and edge_id not in candidate_edge_ids:
                continue
            hits[edge_id] = hits.get(edge_id, 0) + 1
    return hits


def match_segment_to_edges(
    net,
    segment,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
    candidate_edge_ids: Optional[Set[str]] = None,
) -> Dict[str, int]:
    """Same as :func:`match_polyline_to_edges` but over every polyline
    attached to a :class:`floodtwin.demand.vdot.CalibrationSegment`
    (duplicate VDOT rows for the same physical segment can carry different
    digitized geometry -- see that module's docstring)."""
    hits: Dict[str, int] = {}
    for polyline in segment.polylines:
        for edge_id, count in match_polyline_to_edges(net, polyline, tolerance_m, candidate_edge_ids).items():
            hits[edge_id] = hits.get(edge_id, 0) + count
    return hits


def matched_edge_ids(
    net,
    segment,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
    min_hits: int = 1,
    candidate_edge_ids: Optional[Set[str]] = None,
) -> Set[str]:
    """Just the set of edge IDs matched with at least ``min_hits`` sample
    points -- what :mod:`floodtwin.demand.edgedata` needs to build the
    routeSampler count input."""
    hits = match_segment_to_edges(net, segment, tolerance_m, candidate_edge_ids)
    return {eid for eid, n in hits.items() if n >= min_hits}


def filter_to_modal_speed(net, edge_ids: Set[str], round_ndigits: int = 1) -> Set[str]:
    """Keep only the edges in ``edge_ids`` whose speed limit equals the
    *modal* (most common) speed among them.

    Used as the last refinement step when matching a long VDOT count
    segment against a corridor (see ``scripts/build_calibrated_v2.py``):
    once a VDOT segment's bounding extent is wider than the district (a
    real occurrence here -- the whole district-crop stretch of Hampton
    Blvd/Colley Ave each falls inside a single VDOT count segment), the
    matched candidate set is the *entire* corridor, including turn lanes
    and short connector edges picked up by proximity/bearing matching that
    aren't the arterial's own through-lanes. Those artifacts reliably carry
    a different posted speed (e.g. a 2.8 m/s / ~6mph turn lane next to a
    13.4 m/s / 30mph through lane) -- keeping only the modal speed removes
    them without hand-picking edge IDs."""
    if not edge_ids:
        return set()
    speeds = [round(net.getEdge(eid).getSpeed(), round_ndigits) for eid in edge_ids]
    modal_speed = max(set(speeds), key=speeds.count)
    return {eid for eid in edge_ids if round(net.getEdge(eid).getSpeed(), round_ndigits) == modal_speed}


def _bearing_deg(p0: Tuple[float, float], p1: Tuple[float, float]) -> Optional[float]:
    """Direction of the vector ``p0 -> p1`` in XY, normalized to [0, 180)
    (a line's bearing and its reverse are the same corridor direction)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    if dx == 0.0 and dy == 0.0:
        return None
    return math.degrees(math.atan2(dy, dx)) % 180.0


def _angular_diff_deg(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def corridor_edge_ids(
    net,
    road_points_latlon: Sequence[Sequence[Sequence[float]]],
    tolerance_m: float = DEFAULT_TOLERANCE_M,
    max_bearing_diff_deg: Optional[float] = DEFAULT_MAX_BEARING_DIFF_DEG,
) -> Set[str]:
    """Narrow to a named corridor's own net edges: candidates are every net
    edge within ``tolerance_m`` of any point in ``sumo_norfolk/
    road_segments.json``'s per-road point list (the same proximity test
    ``data/net/README.md`` used for the Slice 1 crop-corridor verification),
    additionally filtered to those whose own shape runs roughly parallel to
    the corridor (within ``max_bearing_diff_deg``, or unfiltered if
    ``None``).

    The bearing filter matters in practice: point-proximity alone is too
    permissive in a dense urban grid. At an intersection, a cross street's
    edges sit right next to the corridor's own sample point (they share the
    junction), so an unfiltered proximity match pulls them in too -- a probe
    against the real Hampton Blvd/Colley Ave data found this (see the
    module docstring and the Slice 7 PR). Requiring the *candidate edge's
    own direction* to roughly match the corridor's overall direction rejects
    those near-perpendicular false positives without needing a
    tolerance so tight it starts missing genuine corridor edges near a bend.

    ``road_points_latlon`` is that JSON's shape for one road: a list of
    polyline segments, each a list of ``[lat, lon]`` pairs (note: **lat,
    lon** order, matching the source file -- the opposite of this module's
    other functions, which take GeoJSON's ``(lon, lat)``).

    ``net`` additionally needs ``.getEdge(edge_id)`` returning an object
    with ``.getShape()`` (a list of XY points) when ``max_bearing_diff_deg``
    is not ``None``."""
    lonlat_polylines = [[(lon, lat) for lat, lon in polyline] for polyline in road_points_latlon]

    candidates: Set[str] = set()
    for lonlat_polyline in lonlat_polylines:
        candidates.update(match_polyline_to_edges(net, lonlat_polyline, tolerance_m).keys())

    if max_bearing_diff_deg is None or not candidates:
        return candidates

    # Reference corridor bearing: first-to-last point of the longest
    # sub-polyline (a corridor is treated as locally straight -- fine for
    # an urban arterial over district scale; a sharp bend would need a
    # per-point local bearing instead, not needed for Hampton Blvd/Colley
    # Ave here).
    longest = max(lonlat_polylines, key=len)
    ref_xy = [net.convertLonLat2XY(lon, lat) for lon, lat in longest]
    corridor_bearing = _bearing_deg(ref_xy[0], ref_xy[-1])
    if corridor_bearing is None:
        return candidates

    kept: Set[str] = set()
    for edge_id in candidates:
        edge = net.getEdge(edge_id)
        shape = edge.getShape()
        if len(shape) < 2:
            continue
        edge_bearing = _bearing_deg(shape[0], shape[-1])
        if edge_bearing is None:
            continue
        if _angular_diff_deg(edge_bearing, corridor_bearing) <= max_bearing_diff_deg:
            kept.add(edge_id)
    return kept
