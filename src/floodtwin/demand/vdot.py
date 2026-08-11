"""Parse VDOT "Bidirectional Traffic Volume 2022" feature-service records
(``data/demand/vdot_counts/raw_query_district.geojson``, fetched by
``fetch_vdot_counts.py``) into calibration segments: one real traffic count
per physical road segment, with the AADT -> peak-hour conversion applied.

Pure Python, no SUMO/TensorFlow -- this module operates on the already-saved
GeoJSON dict, not the network, so it's fully unit-testable in CI
(``tests/test_demand.py``).

VDOT dataset quirk (discovered while building this): the same physical
segment is frequently listed multiple times under different
``ROUTE_COMMON_NAME`` values -- once per direction-of-travel entry in VDOT's
route-based linear referencing system (e.g. ``VA-337E`` and ``VA-337W`` for
Hampton Blvd, or the ``(NP - City of Norfolk)``/``(PR - City of Norfolk)``
suffixes for Colley Ave). These duplicate rows report the *same* ``ADT`` (it
is already a bidirectional count) and usually the same ``K_FACTOR``/
``DIRECTION_FACTOR`` -- they are the same real-world count, not independent
measurements -- but often carry *different* digitized geometry (each
direction's LRS entry can trace a slightly different line, e.g. one
carriageway of a divided road vs the other). :func:`dedupe_segments` merges
these into one :class:`CalibrationSegment` per physical location, keeping
every duplicate's geometry (so downstream edge-matching can use all of
them) while counting the traffic exactly once.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

# IMPLEMENTATION_CONTEXT-equivalent note for this slice (see
# data/demand/vdot_counts/PROVENANCE.md for the full writeup): ADT_QUALITY
# codes 'A' ("Average of Complete Continuous Data") and 'G' ("Corrected
# Factored Short Term Traffic Count Data") are high-confidence VDOT counts.
# Lower-confidence codes (e.g. 'N', "AADT of Similar Neighboring Traffic
# Link" -- an estimate borrowed from a nearby road, not a real count on this
# segment) are excluded by default per the task's data-integrity requirement
# ("never fabricate/interpolate/guess a count").
DEFAULT_ACCEPTED_QUALITY_CODES = ("A", "G")

# Route-common-name prefixes identifying the two corridors this slice
# calibrates (PROJECT_PLAN.md Slice 7: "AADT for Hampton Blvd / Colley
# Ave"). Hampton Blvd is filed under state route VA-337 in this dataset
# (confirmed via cross-street labels literally reading "SR 337 Hampton
# Blvd" on adjoining segments, and independently re-confirmed here by
# comparing VDOT segment geometry against sumo_norfolk/road_segments.json's
# named "Hampton Boulevard"/"Colley Avenue" points -- mean nearest-point
# distance ~24m and ~72m respectively for in-district points, well within
# the 25m edge-matching tolerance used elsewhere in this repo).
CORRIDOR_PREFIXES: Dict[str, str] = {
    "Colley AVE": "colley_ave",
    "VA-337": "hampton_blvd",
}


def canonical_corridor(route_common_name: Optional[str]) -> Optional[str]:
    """Map a raw ``ROUTE_COMMON_NAME`` to one of this slice's two corridor
    keys (``"colley_ave"`` / ``"hampton_blvd"``), or ``None`` if the record
    isn't one of them (the district envelope query returns ~30 other named
    roads we don't calibrate against -- PROJECT_PLAN.md Slice 7 scopes this
    to Hampton Blvd / Colley Ave specifically)."""
    if not route_common_name:
        return None
    for prefix, corridor in CORRIDOR_PREFIXES.items():
        if route_common_name.startswith(prefix):
            return corridor
    return None


def is_high_quality(
    quality_code: Optional[str], accepted: Sequence[str] = DEFAULT_ACCEPTED_QUALITY_CODES
) -> bool:
    return quality_code in accepted


def _coords_from_geometry(geometry: dict) -> List[List[Tuple[float, float]]]:
    """Return a list of polylines (each a list of ``(lon, lat)`` tuples)
    from a GeoJSON ``LineString`` or ``MultiLineString`` geometry."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "LineString":
        return [[(float(x), float(y)) for x, y in coords]]
    if gtype == "MultiLineString":
        return [[(float(x), float(y)) for x, y in line] for line in coords]
    return []


class CalibrationSegment:
    """One real, physically-distinct VDOT count segment on a corridor this
    slice calibrates."""

    def __init__(
        self,
        corridor: str,
        start_label: str,
        end_label: str,
        adt: float,
        adt_quality: str,
        k_factor: Optional[float],
        polylines: List[List[Tuple[float, float]]],
        source_objectids: List[int],
        route_common_names: List[str],
        adt_conflict: Optional[List[float]] = None,
    ):
        self.corridor = corridor
        self.start_label = start_label
        self.end_label = end_label
        self.adt = adt
        self.adt_quality = adt_quality
        self.k_factor = k_factor
        self.polylines = polylines
        self.source_objectids = source_objectids
        self.route_common_names = route_common_names
        self.adt_conflict = adt_conflict or []

    @property
    def label(self) -> str:
        return f"{self.corridor}: {self.start_label} -> {self.end_label}"

    def peak_hour_volume(self) -> Optional[float]:
        """Bidirectional peak-hour vehicle volume: ``ADT * K_FACTOR``
        (IMPLEMENTATION_CONTEXT.md-style rule for this slice -- see
        :func:`peak_hour_volume`). ``None`` if ``K_FACTOR`` is missing (the
        segment then has no reliable AADT->peak-hour conversion and should
        be left out of the routeSampler count input, per the task's
        instruction not to guess)."""
        if self.k_factor is None:
            return None
        return peak_hour_volume(self.adt, self.k_factor)

    def to_dict(self) -> dict:
        return {
            "corridor": self.corridor,
            "start_label": self.start_label,
            "end_label": self.end_label,
            "adt": self.adt,
            "adt_quality": self.adt_quality,
            "k_factor": self.k_factor,
            "peak_hour_volume_bidirectional": self.peak_hour_volume(),
            "n_polylines": len(self.polylines),
            "source_objectids": self.source_objectids,
            "route_common_names": self.route_common_names,
            "adt_conflict": self.adt_conflict,
        }


def peak_hour_volume(adt: float, k_factor: float) -> float:
    """AADT -> peak-hour bidirectional volume: ``ADT * K_FACTOR``.

    ``K_FACTOR`` is VDOT's standard "fraction of AADT occurring in the
    design/peak hour" (a value like 0.09 means ~9% of the day's traffic
    passes in the single busiest hour). SUMO's sim horizon here is one
    3600s window (IMPLEMENTATION_CONTEXT.md), so a per-day AADT must be
    converted to a single-hour count before it can constrain
    ``routeSampler`` -- this is that conversion, nothing more.
    """
    return adt * k_factor


def parse_vdot_features(
    geojson: dict,
    accepted_quality: Sequence[str] = DEFAULT_ACCEPTED_QUALITY_CODES,
) -> Tuple[List[dict], List[dict]]:
    """Split raw GeoJSON features into ``(kept, excluded)`` records for the
    two corridors this slice cares about. ``kept``/``excluded`` are lists of
    plain dicts (not yet deduped) carrying the fields needed downstream:
    ``corridor``, ``start_label``, ``end_label``, ``adt``, ``adt_quality``,
    ``k_factor``, ``objectid``, ``route_common_name``, ``polylines``.

    A record is excluded (and shows up in ``excluded`` with a ``reason``)
    if its corridor doesn't match, or its ``ADT_QUALITY`` isn't in
    ``accepted_quality`` (per the task's data-integrity requirement).
    """
    kept: List[dict] = []
    excluded: List[dict] = []
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        name = props.get("ROUTE_COMMON_NAME")
        corridor = canonical_corridor(name)
        record = {
            "corridor": corridor,
            "start_label": props.get("START_LABEL"),
            "end_label": props.get("END_LABEL"),
            "adt": props.get("ADT"),
            "adt_quality": props.get("ADT_QUALITY"),
            "k_factor": props.get("K_FACTOR"),
            "objectid": props.get("OBJECTID"),
            "route_common_name": name,
            "polylines": _coords_from_geometry(feature.get("geometry") or {}),
        }
        if corridor is None:
            record["reason"] = "not a Hampton Blvd / Colley Ave corridor record"
            excluded.append(record)
            continue
        if record["adt"] is None:
            record["reason"] = "no ADT value"
            excluded.append(record)
            continue
        if not is_high_quality(record["adt_quality"], accepted_quality):
            record["reason"] = f"ADT_QUALITY={record['adt_quality']!r} not in {list(accepted_quality)!r}"
            excluded.append(record)
            continue
        kept.append(record)
    return kept, excluded


def dedupe_segments(records: List[dict]) -> List[CalibrationSegment]:
    """Merge duplicate VDOT rows describing the same physical segment (see
    module docstring) into one :class:`CalibrationSegment` per
    ``(corridor, {start_label, end_label})``. Geometry from every duplicate
    is kept (union of polylines) so edge-matching can use all of it; the
    count is taken once (from the first record in each group, since real
    duplicates report the same ADT/K_FACTOR -- a mismatch is recorded in
    ``adt_conflict`` rather than silently averaged/guessed)."""
    groups: "Dict[Tuple[str, frozenset], List[dict]]" = {}
    order: List[Tuple[str, frozenset]] = []
    for r in records:
        key = (r["corridor"], frozenset({r["start_label"], r["end_label"]}))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    segments: List[CalibrationSegment] = []
    for key in order:
        group = groups[key]
        base = group[0]
        adts = {g["adt"] for g in group if g["adt"] is not None}
        conflict = sorted(adts) if len(adts) > 1 else []
        polylines: List[List[Tuple[float, float]]] = []
        for g in group:
            polylines.extend(g["polylines"])
        # Prefer the highest-quality record's ADT/K_FACTOR when duplicates
        # disagree; 'A' ranks above 'G' per DEFAULT_ACCEPTED_QUALITY_CODES.
        best = min(group, key=lambda g: DEFAULT_ACCEPTED_QUALITY_CODES.index(g["adt_quality"])
                   if g["adt_quality"] in DEFAULT_ACCEPTED_QUALITY_CODES else len(DEFAULT_ACCEPTED_QUALITY_CODES))
        segments.append(
            CalibrationSegment(
                corridor=base["corridor"],
                start_label=base["start_label"],
                end_label=base["end_label"],
                adt=best["adt"],
                adt_quality=best["adt_quality"],
                k_factor=best["k_factor"],
                polylines=polylines,
                source_objectids=sorted(g["objectid"] for g in group if g["objectid"] is not None),
                route_common_names=sorted({g["route_common_name"] for g in group if g["route_common_name"]}),
                adt_conflict=conflict,
            )
        )
    return segments


def load_calibration_segments(
    geojson: dict, accepted_quality: Sequence[str] = DEFAULT_ACCEPTED_QUALITY_CODES
) -> Tuple[List[CalibrationSegment], List[dict]]:
    """Full parse: raw GeoJSON -> deduped :class:`CalibrationSegment` list,
    plus the excluded-record list (for provenance reporting)."""
    kept, excluded = parse_vdot_features(geojson, accepted_quality)
    return dedupe_segments(kept), excluded
