"""Unit tests for Slice 7 (PROJECT_PLAN.md SG4 "Calibrated demand"): VDOT
count parsing/dedup, AADT -> peak-hour conversion, edge-matching logic, the
routeSampler edgeData XML writer, and PROVENANCE.md structure. Pure Python:
no SUMO install, no network access -- exercises the real saved
``data/demand/vdot_counts/raw_query_district.geojson`` fetch (committed to
the repo, not re-fetched by tests) so the parsing tests run against real
VDOT data, not synthetic fixtures, wherever that's practical.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from floodtwin.demand.edge_matching import (
    corridor_edge_ids,
    filter_to_modal_speed,
    match_polyline_to_edges,
    match_segment_to_edges,
    matched_edge_ids,
)
from floodtwin.demand.edgedata import build_edgedata_xml, write_edgedata_xml
from floodtwin.demand.vdot import (
    CalibrationSegment,
    canonical_corridor,
    dedupe_segments,
    is_high_quality,
    load_calibration_segments,
    parse_vdot_features,
    peak_hour_volume,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_GEOJSON_PATH = REPO_ROOT / "data" / "demand" / "vdot_counts" / "raw_query_district.geojson"
PROVENANCE_PATH = REPO_ROOT / "data" / "demand" / "vdot_counts" / "PROVENANCE.md"


@pytest.fixture(scope="module")
def raw_geojson():
    return json.loads(RAW_GEOJSON_PATH.read_text())


# ---------------------------------------------------------------------------
# canonical_corridor / is_high_quality (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Colley AVE (PR - City of Norfolk)", "colley_ave"),
        ("Colley AVE (NP - City of Norfolk)", "colley_ave"),
        ("VA-337E", "hampton_blvd"),
        ("VA-337W", "hampton_blvd"),
        ("US-460E", None),
        ("Granby ST (PR - City of Norfolk)", None),
        (None, None),
        ("", None),
    ],
)
def test_canonical_corridor(name, expected):
    assert canonical_corridor(name) == expected


def test_is_high_quality():
    assert is_high_quality("A") is True
    assert is_high_quality("G") is True
    assert is_high_quality("N") is False
    assert is_high_quality(None) is False
    assert is_high_quality("A", accepted=("N",)) is False


# ---------------------------------------------------------------------------
# peak_hour_volume (pure math)
# ---------------------------------------------------------------------------


def test_peak_hour_volume_is_adt_times_k_factor():
    assert peak_hour_volume(10000.0, 0.09) == pytest.approx(900.0)
    assert peak_hour_volume(29000.0, 0.1003) == pytest.approx(2908.7)


def test_peak_hour_volume_zero_k_factor_gives_zero():
    assert peak_hour_volume(20000.0, 0.0) == 0.0


def test_calibration_segment_peak_hour_volume_none_without_k_factor():
    seg = CalibrationSegment(
        corridor="hampton_blvd",
        start_label="A",
        end_label="B",
        adt=20000.0,
        adt_quality="G",
        k_factor=None,
        polylines=[[(0.0, 0.0)]],
        source_objectids=[1],
        route_common_names=["VA-337E"],
    )
    assert seg.peak_hour_volume() is None


def test_calibration_segment_peak_hour_volume_matches_module_function():
    seg = CalibrationSegment(
        corridor="colley_ave",
        start_label="21st Street",
        end_label="27th Street",
        adt=6100.0,
        adt_quality="G",
        k_factor=0.0983,
        polylines=[[(0.0, 0.0)]],
        source_objectids=[1],
        route_common_names=["Colley AVE (PR - City of Norfolk)"],
    )
    assert seg.peak_hour_volume() == pytest.approx(peak_hour_volume(6100.0, 0.0983))


# ---------------------------------------------------------------------------
# parse_vdot_features / dedupe_segments -- synthetic, exact control
# ---------------------------------------------------------------------------


def _feature(name, start, end, adt, quality, k_factor, objectid, coords, mline=False):
    geometry = (
        {"type": "MultiLineString", "coordinates": [coords]}
        if mline
        else {"type": "LineString", "coordinates": coords}
    )
    return {
        "type": "Feature",
        "properties": {
            "ROUTE_COMMON_NAME": name,
            "START_LABEL": start,
            "END_LABEL": end,
            "ADT": adt,
            "ADT_QUALITY": quality,
            "K_FACTOR": k_factor,
            "OBJECTID": objectid,
        },
        "geometry": geometry,
    }


def test_parse_vdot_features_excludes_other_corridors():
    geojson = {
        "features": [
            _feature("Granby ST (PR - City of Norfolk)", "A", "B", 5000, "G", 0.09, 1, [[-76.3, 36.9], [-76.29, 36.9]]),
            _feature("VA-337E", "A", "B", 20000, "G", 0.08, 2, [[-76.3, 36.9], [-76.29, 36.9]]),
        ]
    }
    kept, excluded = parse_vdot_features(geojson)
    assert len(kept) == 1
    assert kept[0]["corridor"] == "hampton_blvd"
    assert len(excluded) == 1
    assert "corridor" in excluded[0]["reason"] or "Hampton" in excluded[0]["reason"]


def test_parse_vdot_features_excludes_low_quality():
    geojson = {
        "features": [
            _feature("VA-337E", "A", "B", 20000, "N", 0.08, 1, [[-76.3, 36.9]]),
        ]
    }
    kept, excluded = parse_vdot_features(geojson)
    assert kept == []
    assert len(excluded) == 1
    assert "ADT_QUALITY" in excluded[0]["reason"]


def test_parse_vdot_features_excludes_missing_adt():
    geojson = {"features": [_feature("Colley AVE (PR - City of Norfolk)", "A", "B", None, "G", 0.09, 1, [[-76.3, 36.9]])]}
    kept, excluded = parse_vdot_features(geojson)
    assert kept == []
    assert excluded[0]["reason"] == "no ADT value"


def test_dedupe_segments_merges_direction_duplicates_and_unions_geometry():
    records, _ = parse_vdot_features(
        {
            "features": [
                _feature("VA-337E", "49th St", "SR 165 Little Creek Rd", 29000, "A", 0.1003, 1, [[-76.30, 36.90], [-76.29, 36.90]]),
                _feature("VA-337W", "49th St", "SR 165 Little Creek Rd", 29000, "A", 0.1003, 2, [[-76.30, 36.901], [-76.29, 36.901]]),
            ]
        }
    )
    segments = dedupe_segments(records)
    assert len(segments) == 1
    seg = segments[0]
    assert seg.adt == 29000
    assert seg.adt_conflict == []
    assert seg.source_objectids == [1, 2]
    assert len(seg.polylines) == 2  # both duplicates' geometry kept
    assert sorted(seg.route_common_names) == ["VA-337E", "VA-337W"]


def test_dedupe_segments_records_adt_conflict_without_fabricating():
    records, _ = parse_vdot_features(
        {
            "features": [
                _feature("VA-337E", "A", "B", 20000, "G", 0.08, 1, [[-76.30, 36.90]]),
                _feature("VA-337W", "A", "B", 21000, "G", 0.08, 2, [[-76.30, 36.90]]),
            ]
        }
    )
    segments = dedupe_segments(records)
    assert len(segments) == 1
    # Real disagreement is surfaced, not silently averaged/guessed.
    assert segments[0].adt_conflict == [20000, 21000]
    assert segments[0].adt in (20000, 21000)


def test_dedupe_segments_start_end_order_independent():
    records, _ = parse_vdot_features(
        {
            "features": [
                _feature("VA-337E", "A", "B", 20000, "G", 0.08, 1, [[-76.30, 36.90]]),
                _feature("VA-337W", "B", "A", 20000, "G", 0.08, 2, [[-76.30, 36.90]]),
            ]
        }
    )
    segments = dedupe_segments(records)
    assert len(segments) == 1
    assert segments[0].source_objectids == [1, 2]


# ---------------------------------------------------------------------------
# Real saved VDOT data (data/demand/vdot_counts/raw_query_district.geojson)
# ---------------------------------------------------------------------------


def test_real_raw_geojson_exists_and_has_features(raw_geojson):
    assert len(raw_geojson["features"]) > 0


def test_real_data_yields_hampton_and_colley_segments(raw_geojson):
    segments, excluded = load_calibration_segments(raw_geojson)
    corridors = {s.corridor for s in segments}
    assert "hampton_blvd" in corridors
    assert "colley_ave" in corridors
    # Every kept segment has a real, in-range ADT and a high quality code.
    for s in segments:
        assert s.adt_quality in ("A", "G")
        assert s.adt > 0
    # Excluded records (if any) are for a documented reason, never silent.
    for e in excluded:
        assert e.get("reason")


def test_real_data_dedup_reduces_count(raw_geojson):
    kept, _ = parse_vdot_features(raw_geojson)
    segments = dedupe_segments(kept)
    # The known VDOT quirk: duplicate direction/classification rows per
    # physical segment (see vdot.py docstring) means dedup must strictly
    # reduce the count for real data pulled from this feature service.
    assert len(segments) < len(kept)
    assert len(segments) > 0


# ---------------------------------------------------------------------------
# edge_matching -- duck-typed fake net (no real sumolib needed)
# ---------------------------------------------------------------------------


class _FakeEdge:
    def __init__(self, edge_id, shape=None):
        self._id = edge_id
        self._shape = shape or [(0.0, 0.0), (0.0, 0.0)]

    def getID(self):
        return self._id

    def getShape(self):
        return self._shape


class _FakeNet:
    """Identity-projected fake net (XY == lon/lat) with edges anchored at a
    single representative point; getNeighboringEdges returns edges whose
    anchor is within r of the query point. Enough to test the
    matching/aggregation logic in edge_matching.py without a real net.

    ``edge_shapes`` (optional) lets tests exercise the bearing filter in
    ``corridor_edge_ids`` -- each edge can carry its own ``[p0, p1]`` shape,
    independent of its anchor point used for proximity matching."""

    def __init__(self, edge_points, edge_shapes=None):
        self._edge_points = edge_points  # {edge_id: (x, y)}
        self._edge_shapes = edge_shapes or {}

    def convertLonLat2XY(self, lon, lat):
        return lon, lat

    def getNeighboringEdges(self, x, y, r=0.1, includeJunctions=True, allowFallback=True):
        out = []
        for edge_id, (ex, ey) in self._edge_points.items():
            dist = ((x - ex) ** 2 + (y - ey) ** 2) ** 0.5
            if dist <= r:
                out.append((_FakeEdge(edge_id, self._edge_shapes.get(edge_id)), dist))
        return out

    def getEdge(self, edge_id):
        return _FakeEdge(edge_id, self._edge_shapes.get(edge_id))


def test_match_polyline_to_edges_counts_hits_per_edge():
    net = _FakeNet({"edgeA": (0.0, 0.0), "edgeB": (10.0, 10.0)})
    polyline = [(0.0, 0.0), (0.0001, 0.0001), (10.0, 10.0)]
    hits = match_polyline_to_edges(net, polyline, tolerance_m=1.0)
    assert hits["edgeA"] == 2
    assert hits["edgeB"] == 1


def test_match_polyline_to_edges_respects_tolerance():
    net = _FakeNet({"edgeA": (0.0, 0.0)})
    polyline = [(5.0, 5.0)]  # far outside tolerance
    hits = match_polyline_to_edges(net, polyline, tolerance_m=1.0)
    assert hits == {}


def test_match_segment_to_edges_unions_across_polylines():
    net = _FakeNet({"edgeA": (0.0, 0.0), "edgeB": (5.0, 5.0)})
    seg = CalibrationSegment(
        corridor="hampton_blvd",
        start_label="X",
        end_label="Y",
        adt=1000.0,
        adt_quality="G",
        k_factor=0.09,
        polylines=[[(0.0, 0.0)], [(5.0, 5.0)]],
        source_objectids=[1],
        route_common_names=["VA-337E"],
    )
    hits = match_segment_to_edges(net, seg, tolerance_m=1.0)
    assert hits == {"edgeA": 1, "edgeB": 1}


def test_match_segment_to_edges_restricts_to_candidate_ids():
    # Real-data probe (see edge_matching.py module docstring): unrestricted
    # matching at 25m tolerance in a dense grid pulls in unrelated cross
    # streets. candidate_edge_ids simulates the corridor-only restriction.
    net = _FakeNet({"hamptonA": (0.0, 0.0), "crossStreetB": (0.0, 0.0)})
    seg = CalibrationSegment(
        corridor="hampton_blvd",
        start_label="X",
        end_label="Y",
        adt=1000.0,
        adt_quality="G",
        k_factor=0.09,
        polylines=[[(0.0, 0.0)]],
        source_objectids=[1],
        route_common_names=["VA-337E"],
    )
    hits = match_segment_to_edges(net, seg, tolerance_m=1.0, candidate_edge_ids={"hamptonA"})
    assert hits == {"hamptonA": 1}


def test_corridor_edge_ids_uses_lat_lon_order():
    # road_points_latlon is [lat, lon] (road_segments.json's native order);
    # the fake net's convertLonLat2XY is identity, so a correctly-ordered
    # call must query with (lon, lat) = (x, y) matching the edge anchor.
    net = _FakeNet({"hamptonA": (-76.30, 36.90)})
    road_points_latlon = [[[36.90, -76.30], [36.90, -76.30]]]
    ids = corridor_edge_ids(net, road_points_latlon, tolerance_m=0.5, max_bearing_diff_deg=None)
    assert ids == {"hamptonA"}


def test_corridor_edge_ids_bearing_filter_rejects_cross_street():
    # A north-south corridor (bearing ~90deg) passes near both a genuine
    # corridor edge (also north-south) and a cross street's edge (east-west)
    # at the same intersection point -- proximity alone can't tell them
    # apart, but bearing can. Midpoint (lat=5, lon=0) sits at the
    # intersection, within tolerance of both edges' anchors.
    road_points_latlon = [[[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]]]  # lat 0->10, lon 0 (north-south)
    net = _FakeNet(
        edge_points={"corridor_edge": (0.0, 5.0), "cross_street_edge": (0.0, 5.0)},
        edge_shapes={
            "corridor_edge": [(0.0, 0.0), (0.0, 10.0)],  # north-south, parallel
            "cross_street_edge": [(-5.0, 5.0), (5.0, 5.0)],  # east-west, perpendicular
        },
    )
    ids = corridor_edge_ids(net, road_points_latlon, tolerance_m=1.0)
    assert ids == {"corridor_edge"}


def test_corridor_edge_ids_bearing_filter_none_disables_it():
    road_points_latlon = [[[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]]]
    net = _FakeNet(
        edge_points={"corridor_edge": (0.0, 5.0), "cross_street_edge": (0.0, 5.0)},
        edge_shapes={
            "corridor_edge": [(0.0, 0.0), (0.0, 10.0)],
            "cross_street_edge": [(-5.0, 5.0), (5.0, 5.0)],
        },
    )
    ids = corridor_edge_ids(net, road_points_latlon, tolerance_m=1.0, max_bearing_diff_deg=None)
    assert ids == {"corridor_edge", "cross_street_edge"}


def test_filter_to_modal_speed_keeps_only_matching_speed():
    net = _FakeNet(
        edge_points={},
        edge_shapes={},
    )
    # getEdge returns a _FakeEdge with default shape; monkeypatch speed via
    # a tiny subclass since _FakeEdge has no speed concept yet.
    class _SpeedNet(_FakeNet):
        def __init__(self, speeds):
            super().__init__({}, {})
            self._speeds = speeds

        def getEdge(self, edge_id):
            edge = _FakeEdge(edge_id)
            edge.getSpeed = lambda: self._speeds[edge_id]
            return edge

    net = _SpeedNet({"a": 13.4, "b": 13.4, "c": 2.8})
    kept = filter_to_modal_speed(net, {"a", "b", "c"})
    assert kept == {"a", "b"}


def test_filter_to_modal_speed_empty_input():
    net = _FakeNet({}, {})
    assert filter_to_modal_speed(net, set()) == set()


def test_matched_edge_ids_filters_by_min_hits():
    net = _FakeNet({"strong": (0.0, 0.0), "weak": (2.0, 2.0)})
    seg = CalibrationSegment(
        corridor="colley_ave",
        start_label="X",
        end_label="Y",
        adt=1000.0,
        adt_quality="G",
        k_factor=0.09,
        polylines=[[(0.0, 0.0), (0.0, 0.0), (2.0, 2.0)]],
        source_objectids=[1],
        route_common_names=["Colley AVE (PR - City of Norfolk)"],
    )
    ids = matched_edge_ids(net, seg, tolerance_m=1.0, min_hits=2)
    assert ids == {"strong"}


# ---------------------------------------------------------------------------
# edgedata.py -- routeSampler count XML
# ---------------------------------------------------------------------------


def test_build_edgedata_xml_structure():
    root = build_edgedata_xml({"e1": 123.4, "e2": 0.4}, begin_s=0, end_s=3600, interval_id="calib")
    assert root.tag == "meandata"
    interval = root.find("interval")
    assert interval is not None
    assert interval.get("id") == "calib"
    assert interval.get("begin") == "0"
    assert interval.get("end") == "3600"
    edges = {e.get("id"): e.get("entered") for e in interval.findall("edge")}
    assert edges == {"e1": "123", "e2": "0"}  # rounded to nearest int


def test_build_edgedata_xml_omits_unconstrained_edges():
    # An edge simply absent from the dict means "no VDOT coverage" -- never
    # write a fabricated 0 for a segment that wasn't queried at all.
    root = build_edgedata_xml({"e1": 500.0})
    interval = root.find("interval")
    assert [e.get("id") for e in interval.findall("edge")] == ["e1"]


def test_write_edgedata_xml_round_trips(tmp_path):
    out = write_edgedata_xml({"e1": 42.0}, tmp_path / "counts.xml")
    assert out.exists()
    import xml.etree.ElementTree as ET

    root = ET.parse(out).getroot()
    assert root.find("interval/edge").get("entered") == "42"


# ---------------------------------------------------------------------------
# PROVENANCE.md structure
# ---------------------------------------------------------------------------


def test_provenance_md_exists_and_documents_required_fields():
    assert PROVENANCE_PATH.exists(), "data/demand/vdot_counts/PROVENANCE.md must exist"
    text = PROVENANCE_PATH.read_text()
    required_substrings = [
        "arcgis.com",  # the query URL
        "ADT_QUALITY",
        "K_FACTOR",
        "fetch",
    ]
    for s in required_substrings:
        assert s.lower() in text.lower(), f"PROVENANCE.md missing expected content: {s!r}"
