"""Georeferencing for the CNN-LSTM flood model's 128x128 output grid.

Pure functions, no SUMO / TraCI dependency -- easy to unit test in isolation
(PROJECT_PLAN.md tests requirement for the raster->edge mapping).

Grid convention, verified against ``sumo_norfolk/webmap.py:24-27`` and
IMPLEMENTATION_CONTEXT.md #2:

    row 0 = north edge, col 0 = west edge
    lat = NORTH - row * (NORTH - SOUTH) / 127
    lon = WEST  + col * (EAST  - WEST)  / 127

The ``/127`` (pixel-centers-at-corners, i.e. grid points 0..127 span the full
NORTH..SOUTH / WEST..EAST range) convention must stay consistent everywhere --
never silently switch to ``/128``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

GRID_SIZE = 128

# Flood model domain (IMPLEMENTATION_CONTEXT.md #2 / webmap.py:24-27).
NORTH = 36.898650
SOUTH = 36.895770
WEST = -76.304447
EAST = -76.300846


@dataclass(frozen=True)
class GeoTransform:
    """Georeferencing metadata for one (grid_size x grid_size) depth grid.
    Slice 1 hardcoded the module-level NORTH/SOUTH/EAST/WEST constants
    everywhere; Slice 2's ``flood_runner`` writes these into every forecast
    NPZ it produces (PROJECT_PLAN.md Slice 2: "georeferencing metadata ...
    so downstream code doesn't hardcode the grid bounds") so a future
    scenario on a different domain (e.g. a live provider, SG5) doesn't
    require code changes here -- just a different ``GeoTransform`` loaded
    from that scenario's own NPZ."""

    north: float
    south: float
    east: float
    west: float
    grid_size: int = GRID_SIZE


# Default transform == the Slice 1 hardcoded constants, kept as the fallback
# so existing call sites (and Slice 1's tests) keep working unchanged.
DEFAULT_TRANSFORM = GeoTransform(north=NORTH, south=SOUTH, east=EAST, west=WEST, grid_size=GRID_SIZE)


def rowcol_to_lonlat(row: float, col: float, transform: GeoTransform = DEFAULT_TRANSFORM) -> Tuple[float, float]:
    """Grid (row, col) -> (lon, lat). Inverse of :func:`lonlat_to_rowcol`."""
    lat = transform.north - row * (transform.north - transform.south) / (transform.grid_size - 1)
    lon = transform.west + col * (transform.east - transform.west) / (transform.grid_size - 1)
    return lon, lat


def lonlat_to_rowcol(lon: float, lat: float, transform: GeoTransform = DEFAULT_TRANSFORM) -> Tuple[float, float]:
    """(lon, lat) -> grid (row, col), as continuous (non-integer) coordinates.
    Inverse of :func:`rowcol_to_lonlat`."""
    row = (transform.north - lat) * (transform.grid_size - 1) / (transform.north - transform.south)
    col = (lon - transform.west) * (transform.grid_size - 1) / (transform.east - transform.west)
    return row, col


def in_grid_bounds(row: float, col: float, transform: GeoTransform = DEFAULT_TRANSFORM) -> bool:
    return 0.0 <= row <= transform.grid_size - 1 and 0.0 <= col <= transform.grid_size - 1


def sample_depth(
    depth_grid, lon: float, lat: float, transform: GeoTransform = DEFAULT_TRANSFORM
) -> Optional[float]:
    """Nearest-neighbor sample of a ``(grid_size, grid_size)`` depth grid at
    a lon/lat point. Returns ``None`` if the point falls outside the flood
    grid's georeferenced extent (i.e. most of a district-scale net will be
    dry / out of frame -- this is expected, not an error)."""
    row, col = lonlat_to_rowcol(lon, lat, transform)
    if not in_grid_bounds(row, col, transform):
        return None
    r = min(max(int(round(row)), 0), transform.grid_size - 1)
    c = min(max(int(round(col)), 0), transform.grid_size - 1)
    return float(depth_grid[r, c])
