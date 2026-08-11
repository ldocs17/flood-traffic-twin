"""Raster -> edge depth mapping (PROJECT_PLAN.md D4) and the depth -> speed
rule (D3: full Pregnolato depth-disruption curve as of Slice 2; Slice 1's
``closed_edges`` binary cutoff is kept for backward compatibility / tests).

Reusable core, independent of TraCI so it can run (and be tested) without a
live simulation:

    sample_edge_depths(net, depth_grid)                -> {edge_id: max_depth_normalized}
    depth_to_mm(depth_normalized)                       -> mm of real water (DEPTH_SCALE_M)
    closed_edges(edge_depths, threshold)                -> {edge_id, ...}                    (Slice 1)
    pregnolato_v_safe_kmh(depth_mm)                     -> km/h, or None if impassable        (Slice 2)
    speeds_and_closures(edge_depths, speed_limits_ms)   -> {edge_id: (v_max_ms, closed)}       (Slice 2)

``net`` is a ``sumolib.net.Net`` (or any object exposing ``.getEdges()`` ->
edges with ``.getID()``/``.getShape()``, and ``.convertXY2LonLat(x, y)``) --
kept duck-typed so this module has no hard import-time dependency on
``sumolib`` and stays trivially unit-testable with a fake net.

Slice 8 (PROJECT_PLAN.md Slice 8, sensitivity/robustness): the module-level
``DEPTH_SCALE_M`` / ``CLOSURE_THRESHOLD_MM`` constants and
``sample_edge_depths``'s max-only aggregation were exactly the "future
sensitivity analysis" seam D3/D4 called out. Every function that used a
module constant directly now also accepts it as an optional keyword argument
(default = the module constant, so every existing call site is unaffected);
``sample_edge_depths`` gained an ``aggregation`` parameter (``"max"``
(default) or ``"pNN"`` e.g. ``"p95"``) alongside D4's existing max rule. See
``floodtwin.analysis.sensitivity`` for the sweep that exercises these.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

from floodtwin.coupling import georef

# D4: sample at ~2.5 m spacing, matching the flood grid's native pixel size.
SAMPLE_SPACING_M = 2.5

# IMPLEMENTATION_CONTEXT.md #4 "unit gap" (Q2): the flood model outputs
# NORMALIZED depth (0 -> ~0.65), not meters, and the real-world scale factor
# lives in Wang et al.'s upstream preprocessing -- unrecoverable from this
# repo. Single named constant, interim value 1.0, per the plan's rule:
#   depth_mm = model_output * DEPTH_SCALE_M * 1000
DEPTH_SCALE_M = 1.0  # UNCONFIRMED -- see IMPLEMENTATION_CONTEXT.md Q2

# Pregnolato et al. (2017): roads are impassable at >= 300 mm standing water
# (D3).
CLOSURE_THRESHOLD_MM = 300.0

# Pregnolato et al. (2017) depth-disruption curve coefficients
# (IMPLEMENTATION_CONTEXT.md #4, exact form):
#   v_safe(w) = 0.0009*w^2 - 0.5529*w + 86.9448   [km/h, w = depth in mm]
#   valid for 0 <= w <= 300mm; w >= 300mm is impassable.
_PREGNOLATO_A = 0.0009
_PREGNOLATO_B = -0.5529
_PREGNOLATO_C = 86.9448

KMH_TO_MS = 1.0 / 3.6


def depth_to_mm(depth_normalized: float, depth_scale_m: float = DEPTH_SCALE_M) -> float:
    """Convert a normalized model-output depth to millimeters of real water.

    ``depth_scale_m`` (Slice 8, Risk R7 / Open Question Q2): defaults to the
    module constant so every pre-Slice-8 call site is unaffected; the
    sensitivity sweep passes {0.5, 1.0, 2.0} explicitly to answer "does the
    headline finding survive the unit-gap uncertainty".
    """
    return depth_normalized * depth_scale_m * 1000.0


def _interpolate_points(shape: List[Tuple[float, float]], spacing_m: float):
    """Yield (x, y) points along polyline ``shape`` spaced ~spacing_m apart,
    including both endpoints of every segment."""
    if not shape:
        return
    if len(shape) == 1:
        yield shape[0]
        return
    for i in range(len(shape) - 1):
        x0, y0 = shape[i]
        x1, y1 = shape[i + 1]
        seg_len = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if seg_len == 0:
            continue
        n_steps = max(1, int(seg_len // spacing_m))
        for s in range(n_steps):
            t = s / n_steps
            yield (x0 + t * (x1 - x0), y0 + t * (y1 - y0))
    yield shape[-1]


def _percentile(samples: List[float], pct: float) -> float:
    """Linear-interpolation percentile (matches numpy's default 'linear'
    method), ``pct`` on a 0-100 scale. Dependency-free (no numpy import in
    this module) so ``edge_mapper`` stays as light as it was before Slice 8."""
    if not samples:
        raise ValueError("cannot take a percentile of an empty sample list")
    s = sorted(samples)
    if len(s) == 1:
        return s[0]
    k = (pct / 100.0) * (len(s) - 1)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return s[lo]
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _aggregate(samples: List[float], aggregation: str) -> float:
    """D4's raster->edge aggregation rule. ``"max"`` (default, D4's original
    "deepest point governs passability" rule) or ``"pNN"`` (e.g. ``"p95"``,
    Slice 8 sensitivity axis -- D4's own note: "one-line change if we later
    prefer a percentile")."""
    if aggregation == "max":
        return max(samples)
    if aggregation.startswith("p"):
        try:
            pct = float(aggregation[1:])
        except ValueError:
            raise ValueError(f"unknown aggregation {aggregation!r}; use 'max' or 'pNN' e.g. 'p95'")
        return _percentile(samples, pct)
    raise ValueError(f"unknown aggregation {aggregation!r}; use 'max' or 'pNN' e.g. 'p95'")


def sample_edge_depths(
    net,
    depth_grid,
    spacing_m: float = SAMPLE_SPACING_M,
    transform: georef.GeoTransform = georef.DEFAULT_TRANSFORM,
    aggregation: str = "max",
) -> Dict[str, float]:
    """For every edge in ``net``, sample ``depth_grid`` (a
    (transform.grid_size, transform.grid_size) normalized-depth array) along
    the edge centerline at ~spacing_m intervals, converting each sample point
    to lon/lat via ``net.convertXY2LonLat`` (never hand-rolled UTM math --
    IMPLEMENTATION_CONTEXT.md #3). Edge depth = ``aggregation`` over samples
    (D4's default: max; Slice 8 also allows a percentile, e.g. ``"p95"``).

    ``transform`` carries the grid's georeferencing (north/south/east/west
    bounds); defaults to the flood model's fixed domain
    (``georef.DEFAULT_TRANSFORM``) but Slice 2's ``flood_runner`` NPZ output
    carries its own transform explicitly so this never has to hardcode the
    bounds (PROJECT_PLAN.md Slice 2).

    Edges with zero in-grid samples (the overwhelming majority at district
    scale -- the flood grid is only ~320m x 320m) are omitted from the
    result entirely; callers should treat a missing edge as depth 0 / dry.
    """
    result: Dict[str, float] = {}
    for edge in net.getEdges():
        shape = edge.getShape()
        samples: List[float] = []
        for x, y in _interpolate_points(shape, spacing_m):
            lon, lat = net.convertXY2LonLat(x, y)
            d = georef.sample_depth(depth_grid, lon, lat, transform)
            if d is None:
                continue
            samples.append(d)
        if samples:
            result[edge.getID()] = _aggregate(samples, aggregation)
    return result


def closed_edges(
    edge_depths_normalized: Dict[str, float],
    threshold_mm: float = CLOSURE_THRESHOLD_MM,
    depth_scale_m: float = DEPTH_SCALE_M,
) -> Set[str]:
    """Edges whose max sampled depth (normalized units) converts to
    >= threshold_mm of real water -- impassable per D3.

    Slice 1's simplified (closure-only) rule. Kept for backward
    compatibility / tests; Slice 2's ``speeds_and_closures`` below is the
    full D3 rule (Pregnolato speed curve + closure) and is what the runner
    now uses. ``depth_scale_m`` (Slice 8): see :func:`depth_to_mm`."""
    return {
        edge_id
        for edge_id, depth in edge_depths_normalized.items()
        if depth_to_mm(depth, depth_scale_m) >= threshold_mm
    }


def pregnolato_v_safe_kmh(
    depth_mm: float, closure_threshold_mm: float = CLOSURE_THRESHOLD_MM
) -> Optional[float]:
    """Pregnolato et al. (2017) depth-disruption function, exact form
    (IMPLEMENTATION_CONTEXT.md #4):

        v_safe(w) = 0.0009*w^2 - 0.5529*w + 86.9448   [km/h, w = depth in mm]
        valid for 0 <= w <= 300mm; w >= 300mm -> impassable (returns None)

    ``depth_mm`` below 0 (float noise from a ReLU-activated model output
    landing at e.g. -1e-7) is clamped to 0 before evaluating the curve.

    ``closure_threshold_mm`` (Slice 8, sensitivity axis "closure threshold
    20/30/40 cm"): defaults to the module constant so pre-Slice-8 call sites
    are unaffected. The curve's own coefficients are fit over 0-300mm in the
    source paper; a 400mm sensitivity run extrapolates the polynomial
    slightly past its fitted domain -- flagged in the Slice 8 writeup."""
    if depth_mm >= closure_threshold_mm:
        return None
    w = max(0.0, depth_mm)
    return _PREGNOLATO_A * w * w + _PREGNOLATO_B * w + _PREGNOLATO_C


def edge_speed_ms(
    depth_mm: float,
    speed_limit_ms: float,
    closure_threshold_mm: float = CLOSURE_THRESHOLD_MM,
) -> Tuple[float, bool]:
    """Per-edge speed rule (D3): ``v_edge = min(v_speedlimit, v_safe(max_depth))``,
    converted to m/s for ``traci.edge.setMaxSpeed``. Returns
    ``(v_max_ms, closed)``; when closed, ``v_max_ms`` is 0.0 (the edge is
    disallowed via ``setDisallowed``, not driven at a residual speed).
    ``closure_threshold_mm`` (Slice 8): see :func:`pregnolato_v_safe_kmh`."""
    v_safe_kmh = pregnolato_v_safe_kmh(depth_mm, closure_threshold_mm)
    if v_safe_kmh is None:
        return 0.0, True
    v_safe_ms = v_safe_kmh * KMH_TO_MS
    return min(speed_limit_ms, v_safe_ms), False


def speeds_and_closures(
    edge_depths_normalized: Dict[str, float],
    speed_limits_ms: Dict[str, float],
    closure_threshold_mm: float = CLOSURE_THRESHOLD_MM,
    depth_scale_m: float = DEPTH_SCALE_M,
) -> Dict[str, Tuple[float, bool]]:
    """Full D3 rule for every edge with a known speed limit: returns
    ``{edge_id: (v_max_ms, closed)}``.

    ``speed_limits_ms`` should cover every edge in the net (e.g.
    ``{e.getID(): e.getSpeed() for e in net.getEdges()}`` -- sumolib's
    ``Edge.getSpeed()`` is already in m/s). Edges absent from
    ``edge_depths_normalized`` are treated as dry (0 depth -> open at their
    full speed limit), consistent with ``sample_edge_depths``' "missing =
    dry" contract.

    ``closure_threshold_mm``/``depth_scale_m`` (Slice 8): default to the
    module constants so pre-Slice-8 call sites are unaffected; the
    sensitivity sweep (``floodtwin.analysis.sensitivity``) passes explicit
    values for each axis."""
    result: Dict[str, Tuple[float, bool]] = {}
    for edge_id, speed_limit_ms in speed_limits_ms.items():
        depth_norm = edge_depths_normalized.get(edge_id, 0.0)
        depth_mm = depth_to_mm(depth_norm, depth_scale_m)
        result[edge_id] = edge_speed_ms(depth_mm, speed_limit_ms, closure_threshold_mm)
    return result
