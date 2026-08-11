"""Unit tests for the raster -> edge mapping (PROJECT_PLAN.md D4) and the
depth -> closure rule (D3 interim / Slice 1 form). Pure-Python: no SUMO
install or running simulation required.
"""
import numpy as np
import pytest

from floodtwin.coupling import georef
from floodtwin.coupling.edge_mapper import (
    CLOSURE_THRESHOLD_MM,
    DEPTH_SCALE_M,
    closed_edges,
    depth_to_mm,
    edge_speed_ms,
    pregnolato_v_safe_kmh,
    sample_edge_depths,
    speeds_and_closures,
)


# ---------------------------------------------------------------------------
# georef: coordinate round-trip + known corner values
# ---------------------------------------------------------------------------

def test_georef_corners_match_documented_bounds():
    lon, lat = georef.rowcol_to_lonlat(0, 0)
    assert lat == pytest.approx(georef.NORTH)
    assert lon == pytest.approx(georef.WEST)

    lon, lat = georef.rowcol_to_lonlat(127, 127)
    assert lat == pytest.approx(georef.SOUTH)
    assert lon == pytest.approx(georef.EAST)


@pytest.mark.parametrize("row,col", [(0, 0), (63.5, 63.5), (127, 0), (0, 127), (127, 127), (40, 90)])
def test_georef_round_trip(row, col):
    lon, lat = georef.rowcol_to_lonlat(row, col)
    row2, col2 = georef.lonlat_to_rowcol(lon, lat)
    assert row2 == pytest.approx(row, abs=1e-9)
    assert col2 == pytest.approx(col, abs=1e-9)


def test_georef_out_of_bounds_returns_none():
    grid = np.zeros((128, 128))
    # Far outside the 320m x 320m flood domain.
    assert georef.sample_depth(grid, lon=-76.5, lat=37.0) is None


def test_georef_sample_depth_nearest_neighbor():
    grid = np.zeros((128, 128))
    grid[10, 20] = 0.42
    lon, lat = georef.rowcol_to_lonlat(10, 20)
    assert georef.sample_depth(grid, lon, lat) == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# edge_mapper: sample_edge_depths against a fake net + synthetic grid
# ---------------------------------------------------------------------------

class _FakeEdge:
    def __init__(self, edge_id, shape):
        self._id = edge_id
        self._shape = shape

    def getID(self):
        return self._id

    def getShape(self):
        return self._shape


class _FakeNet:
    """Identity-projected fake net: XY *is* lon/lat, so edges can be placed
    directly at known grid coordinates without needing real UTM math."""

    def __init__(self, edges):
        self._edges = edges

    def getEdges(self):
        return self._edges

    def convertXY2LonLat(self, x, y):
        return x, y


def test_sample_edge_depths_picks_max_along_centerline():
    grid = np.zeros((128, 128))
    # Put a deep spot at row=50, col=60 and a shallow spot at row=50, col=10.
    grid[50, 60] = 0.9
    grid[50, 10] = 0.1

    deep_lon, deep_lat = georef.rowcol_to_lonlat(50, 60)
    shallow_lon, shallow_lat = georef.rowcol_to_lonlat(50, 10)

    # Edge "wet" passes through the deep spot; edge "dry_ish" only the shallow one.
    wet_edge = _FakeEdge("wet", [(shallow_lon, shallow_lat), (deep_lon, deep_lat)])
    dry_edge = _FakeEdge("dry_ish", [(shallow_lon, shallow_lat), (shallow_lon, shallow_lat + 1e-6)])
    net = _FakeNet([wet_edge, dry_edge])

    depths = sample_edge_depths(net, grid, spacing_m=2.5)

    assert depths["wet"] == pytest.approx(0.9, abs=1e-6)
    assert depths["dry_ish"] == pytest.approx(0.1, abs=1e-6)


def test_sample_edge_depths_omits_out_of_grid_edges():
    grid = np.zeros((128, 128))
    far_away_edge = _FakeEdge("elsewhere", [(-76.5, 37.0), (-76.4, 37.1)])
    net = _FakeNet([far_away_edge])
    depths = sample_edge_depths(net, grid)
    assert "elsewhere" not in depths


# ---------------------------------------------------------------------------
# depth -> mm -> closure rule
# ---------------------------------------------------------------------------

def test_depth_to_mm_uses_scale_constant():
    assert depth_to_mm(0.3) == pytest.approx(0.3 * DEPTH_SCALE_M * 1000.0)


def test_closed_edges_threshold():
    # Threshold is 300mm; with DEPTH_SCALE_M == 1.0 that's normalized depth 0.3.
    threshold_normalized = CLOSURE_THRESHOLD_MM / (DEPTH_SCALE_M * 1000.0)
    depths = {
        "shallow": threshold_normalized * 0.5,
        "exactly_at_threshold": threshold_normalized,
        "deep": threshold_normalized * 2,
    }
    closed = closed_edges(depths)
    assert closed == {"exactly_at_threshold", "deep"}


def test_closed_edges_empty_when_no_deep_edges():
    depths = {"a": 0.001, "b": 0.01}
    assert closed_edges(depths) == set()


# ---------------------------------------------------------------------------
# Slice 2: Pregnolato speed curve (IMPLEMENTATION_CONTEXT.md #4, exact form)
# ---------------------------------------------------------------------------

def test_pregnolato_curve_matches_exact_coefficients():
    # v_safe(w) = 0.0009*w^2 - 0.5529*w + 86.9448 [km/h], w in mm.
    for w in (0.0, 1.0, 50.0, 100.0, 150.0, 250.0, 299.9):
        expected = 0.0009 * w**2 - 0.5529 * w + 86.9448
        assert pregnolato_v_safe_kmh(w) == pytest.approx(expected)


def test_pregnolato_curve_dry_road_is_unrestricted():
    # At w=0 the curve gives ~86.9 km/h -- well above any urban speed limit,
    # so a dry edge should never be slowed by the curve itself.
    assert pregnolato_v_safe_kmh(0.0) == pytest.approx(86.9448)


def test_pregnolato_curve_impassable_at_and_above_300mm():
    assert pregnolato_v_safe_kmh(300.0) is None
    assert pregnolato_v_safe_kmh(301.0) is None
    assert pregnolato_v_safe_kmh(1000.0) is None


def test_pregnolato_curve_just_below_threshold_is_a_crawl():
    v = pregnolato_v_safe_kmh(299.9)
    assert v is not None
    assert 0 < v < 5  # km/h -- curve should be near zero just under closure


def test_pregnolato_curve_monotonically_decreasing_over_valid_range():
    ws = [0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 299.0]
    values = [pregnolato_v_safe_kmh(w) for w in ws]
    assert all(a > b for a, b in zip(values, values[1:]))


def test_pregnolato_curve_clamps_small_negative_float_noise():
    # ReLU model output converted to mm can land at a tiny negative float
    # (e.g. -1e-7) due to rounding; should behave like exactly 0.
    assert pregnolato_v_safe_kmh(-1e-7) == pytest.approx(pregnolato_v_safe_kmh(0.0))


def test_edge_speed_ms_caps_at_speed_limit_when_dry():
    # Speed limit lower than the curve's dry-road value -> speed limit wins.
    v_max, closed = edge_speed_ms(depth_mm=0.0, speed_limit_ms=13.4)  # ~30mph
    assert closed is False
    assert v_max == pytest.approx(13.4)


def test_edge_speed_ms_reduced_by_curve_when_flooded_but_open():
    speed_limit_ms = 100.0  # deliberately high so the curve, not the limit, binds
    v_max, closed = edge_speed_ms(depth_mm=200.0, speed_limit_ms=speed_limit_ms)
    assert closed is False
    expected_kmh = 0.0009 * 200.0**2 - 0.5529 * 200.0 + 86.9448
    assert v_max == pytest.approx(expected_kmh / 3.6)
    assert v_max < speed_limit_ms


def test_edge_speed_ms_closed_at_threshold():
    v_max, closed = edge_speed_ms(depth_mm=300.0, speed_limit_ms=13.4)
    assert closed is True
    assert v_max == 0.0


def test_speeds_and_closures_missing_edge_is_dry_at_full_speed():
    speed_limits = {"a": 13.4, "b": 20.0}
    states = speeds_and_closures({}, speed_limits)
    assert states["a"] == (pytest.approx(13.4), False)
    assert states["b"] == (pytest.approx(20.0), False)


def test_speeds_and_closures_mixed():
    speed_limits = {"dry": 13.4, "shallow": 13.4, "deep_but_open": 100.0, "closed": 13.4}
    depths_normalized = {
        # DEPTH_SCALE_M == 1.0 interim -> normalized == meters -> *1000 == mm.
        "shallow": 0.05,  # 50mm
        "deep_but_open": 0.2,  # 200mm
        "closed": 0.35,  # 350mm >= 300mm threshold
    }
    states = speeds_and_closures(depths_normalized, speed_limits)

    v_dry, closed_dry = states["dry"]
    assert closed_dry is False
    assert v_dry == pytest.approx(13.4)

    v_shallow, closed_shallow = states["shallow"]
    assert closed_shallow is False
    assert v_shallow == pytest.approx(13.4)  # speed limit still binds at 50mm

    v_deep, closed_deep = states["deep_but_open"]
    assert closed_deep is False
    assert v_deep < 100.0  # curve now binds, not the (high) speed limit

    v_closed, closed_closed = states["closed"]
    assert closed_closed is True
    assert v_closed == 0.0


# ---------------------------------------------------------------------------
# Slice 2: GeoTransform (explicit georeferencing, not hardcoded)
# ---------------------------------------------------------------------------

def test_geotransform_default_matches_module_constants():
    lon, lat = georef.rowcol_to_lonlat(0, 0, georef.DEFAULT_TRANSFORM)
    assert lat == pytest.approx(georef.NORTH)
    assert lon == pytest.approx(georef.WEST)


def test_geotransform_custom_bounds_round_trip():
    custom = georef.GeoTransform(north=10.0, south=9.0, east=-70.0, west=-71.0, grid_size=64)
    lon, lat = georef.rowcol_to_lonlat(0, 0, custom)
    assert (lon, lat) == pytest.approx((-71.0, 10.0))
    lon, lat = georef.rowcol_to_lonlat(63, 63, custom)
    assert (lon, lat) == pytest.approx((-70.0, 9.0))
    row, col = georef.lonlat_to_rowcol(lon, lat, custom)
    assert (row, col) == pytest.approx((63, 63))


def test_sample_edge_depths_respects_custom_transform():
    custom = georef.GeoTransform(north=10.0, south=9.0, east=-70.0, west=-71.0, grid_size=64)
    grid = np.zeros((64, 64))
    grid[5, 5] = 0.77
    lon, lat = georef.rowcol_to_lonlat(5, 5, custom)
    edge = _FakeEdge("e", [(lon, lat), (lon, lat)])
    net = _FakeNet([edge])
    depths = sample_edge_depths(net, grid, transform=custom)
    assert depths["e"] == pytest.approx(0.77)


# ---------------------------------------------------------------------------
# Slice 8 (PROJECT_PLAN.md Slice 8, sensitivity/robustness): the parameter
# plumbing added on top of the module constants -- every function keeps its
# pre-Slice-8 default behavior, and every new keyword argument actually
# changes the result when passed explicitly.
# ---------------------------------------------------------------------------


def test_depth_to_mm_default_matches_module_constant():
    assert depth_to_mm(0.3) == pytest.approx(0.3 * DEPTH_SCALE_M * 1000.0)


def test_depth_to_mm_respects_explicit_scale():
    assert depth_to_mm(0.3, depth_scale_m=2.0) == pytest.approx(0.3 * 2.0 * 1000.0)
    assert depth_to_mm(0.3, depth_scale_m=0.5) == pytest.approx(0.3 * 0.5 * 1000.0)


def test_closed_edges_respects_explicit_threshold_and_scale():
    depths = {"a": 0.25}  # 250mm at DEPTH_SCALE_M=1.0
    assert closed_edges(depths, threshold_mm=300.0) == set()  # default threshold: not closed
    assert closed_edges(depths, threshold_mm=200.0) == {"a"}  # lower threshold: now closed
    # Scaling depth down means the same raw depth no longer crosses 300mm.
    assert closed_edges(depths, threshold_mm=300.0, depth_scale_m=2.0) == {"a"}  # 500mm >= 300mm


def test_pregnolato_curve_respects_explicit_closure_threshold():
    # Same curve, but the impassable cutoff moves.
    assert pregnolato_v_safe_kmh(250.0, closure_threshold_mm=200.0) is None  # would be open at default 300mm
    assert pregnolato_v_safe_kmh(250.0) is not None  # default (300mm) still open at 250mm
    assert pregnolato_v_safe_kmh(350.0, closure_threshold_mm=400.0) is not None  # default would close at 300mm


def test_edge_speed_ms_respects_explicit_closure_threshold():
    v_max, closed = edge_speed_ms(depth_mm=350.0, speed_limit_ms=13.4, closure_threshold_mm=400.0)
    assert closed is False
    assert v_max > 0.0
    v_max2, closed2 = edge_speed_ms(depth_mm=350.0, speed_limit_ms=13.4)  # default 300mm threshold
    assert closed2 is True
    assert v_max2 == 0.0


def test_speeds_and_closures_respects_explicit_threshold_and_scale():
    speed_limits = {"e": 13.4}
    depths_normalized = {"e": 0.35}  # 350mm at scale 1.0
    # Default (300mm threshold): closed.
    assert speeds_and_closures(depths_normalized, speed_limits)["e"][1] is True
    # Raised threshold (400mm): open.
    assert speeds_and_closures(depths_normalized, speed_limits, closure_threshold_mm=400.0)["e"][1] is False
    # Halved depth scale (350mm -> 175mm): open even at the default threshold.
    assert speeds_and_closures(depths_normalized, speed_limits, depth_scale_m=0.5)["e"][1] is False


# ---------------------------------------------------------------------------
# Slice 8: edge-depth aggregation (D4's own "one-line change" note) -- max
# (default, unchanged) vs percentile.
# ---------------------------------------------------------------------------


def test_sample_edge_depths_default_aggregation_is_max():
    grid = np.zeros((128, 128))
    grid[50, 60] = 0.9
    grid[50, 61] = 0.1
    lon0, lat0 = georef.rowcol_to_lonlat(50, 60)
    lon1, lat1 = georef.rowcol_to_lonlat(50, 61)
    edge = _FakeEdge("e", [(lon0, lat0), (lon1, lat1)])
    net = _FakeNet([edge])
    depths = sample_edge_depths(net, grid, spacing_m=0.5)
    assert depths["e"] == pytest.approx(0.9)


def test_sample_edge_depths_p95_aggregation_differs_from_max():
    # Many shallow samples and one deep outlier -- p95 should land well below
    # the max (unlike the default max rule). Square grid/transform chosen so
    # (lon, lat) = (col, 99 - row) exactly -- no nearest-neighbor rounding
    # ambiguity -- and a shape point per column (unit spacing, spacing_m=1.0
    # so _interpolate_points doesn't sub-sample within a 1-unit segment).
    grid = np.zeros((100, 100))
    grid[50, :99] = 0.1
    grid[50, 99] = 5.0  # a single deep outlier at the far (last-column) end
    transform = georef.GeoTransform(north=99.0, south=0.0, east=99.0, west=0.0, grid_size=100)
    shape = [(float(c), 49.0) for c in range(100)]  # row = 99-49 = 50 for every point
    edge = _FakeEdge("e", shape)
    net = _FakeNet([edge])

    depths_max = sample_edge_depths(net, grid, spacing_m=1.0, transform=transform, aggregation="max")
    depths_p95 = sample_edge_depths(net, grid, spacing_m=1.0, transform=transform, aggregation="p95")

    assert depths_max["e"] == pytest.approx(5.0)
    assert depths_p95["e"] < depths_max["e"]
    assert depths_p95["e"] == pytest.approx(0.1, abs=0.05)  # 95th percentile of mostly-0.1 samples


def test_sample_edge_depths_unknown_aggregation_raises():
    grid = np.zeros((128, 128))
    grid[0, 0] = 0.5
    lon, lat = georef.rowcol_to_lonlat(0, 0)
    edge = _FakeEdge("e", [(lon, lat), (lon, lat)])
    net = _FakeNet([edge])
    with pytest.raises(ValueError):
        sample_edge_depths(net, grid, aggregation="bogus")


def test_percentile_matches_numpy_linear_interpolation():
    from floodtwin.coupling.edge_mapper import _percentile

    values = [1.0, 2.0, 3.0, 4.0, 10.0, 20.0, 30.0, 40.0, 100.0, 200.0]
    for pct in (0, 25, 50, 75, 95, 100):
        assert _percentile(values, pct) == pytest.approx(float(np.percentile(values, pct)))


def test_percentile_single_sample():
    from floodtwin.coupling.edge_mapper import _percentile

    assert _percentile([0.42], 95) == pytest.approx(0.42)
