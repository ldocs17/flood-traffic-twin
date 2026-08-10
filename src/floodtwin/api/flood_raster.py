"""Flood raster overlay: forecast NPZ depth stack -> RGBA PNG, for MapLibre
to drop in as an ``ImageSource``/``RasterLayer``.

Adapted from ``sumo_norfolk/webmap.py``'s ``depth_to_rgba`` (read-only
reference per the Slice 5 brief) with one deliberate change: bounds come
from the run's own forecast NPZ (``north``/``south``/``east``/``west``,
written by ``floodtwin.flood.flood_runner`` -- see
``floodtwin.coupling.georef.GeoTransform``), never the module-level
constants ``webmap.py`` hardcodes. For this project's single flood-model
domain the two happen to be numerically identical (verified below in
``verify_bounds_match_georef``), but wiring it through the NPZ is what
makes this correct if a future scenario ever used a different domain
(PROJECT_PLAN.md D2's ``FloodProvider`` upgrade path) -- exactly the trap
IMPLEMENTATION_CONTEXT.md flags about not hand-copying legacy constants.

Pure numpy for the color mapping (unit-testable); PIL only in the PNG
encoding step (thin wrapper, not unit tested beyond a smoke test since it's
a straight ``Image.fromarray(...).save(...)`` call).
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from floodtwin.coupling import georef

DEFAULT_FRAME_MARKS_S = [900.0, 1800.0, 2700.0, 3600.0]
DEFAULT_FRAME_LABELS = ["t+15min", "t+30min", "t+45min", "t+60min"]

# Wet-pixel threshold: depths below this (normalized units) render fully
# transparent -- matches webmap.py's ``depth_grid > 0.01`` convention so the
# overlay doesn't paint the entire 320x320 domain a faint blue.
WET_THRESHOLD = 0.01

# Upsampling factor for the exported PNG (128x128 native grid -> nicer to
# look at without MapLibre's own smoothing artifacts at high zoom).
UPSCALE = 4


def depth_to_rgba(depth_grid: np.ndarray, max_depth: float) -> np.ndarray:
    """Normalized depth grid -> (H, W, 4) uint8 RGBA, deeper = more opaque
    blue. Direct port of ``webmap.py:depth_to_rgba`` (same channel formulas)
    -- kept byte-for-byte compatible so the rendering this project has
    already eyeballed doesn't change."""
    h, w = depth_grid.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    wet = depth_grid > WET_THRESHOLD
    safe_max = max_depth if max_depth > 0 else 1.0
    norm = np.clip(depth_grid / safe_max, 0, 1)
    rgba[wet, 0] = (30 * (1 - norm[wet])).astype(np.uint8)
    rgba[wet, 1] = (100 * (1 - norm[wet]) + 50).astype(np.uint8)
    rgba[wet, 2] = (255 * (0.4 + 0.6 * norm[wet])).astype(np.uint8)
    rgba[wet, 3] = (80 + 175 * norm[wet]).astype(np.uint8)
    return rgba


def load_forecast_stack(npz_path: Path) -> Tuple[np.ndarray, georef.GeoTransform]:
    """Load a flood_runner forecast NPZ's depth stack + its own
    georeferencing transform (never the module-level fallback -- see module
    docstring)."""
    data = np.load(npz_path)
    depth_stack = data["depth_stack"]
    transform = georef.GeoTransform(
        north=float(data["north"]),
        south=float(data["south"]),
        east=float(data["east"]),
        west=float(data["west"]),
        grid_size=int(data["grid_size"]),
    )
    return depth_stack, transform


def frame_rgba(depth_stack: np.ndarray, frame_index: int, global_max: float = None) -> np.ndarray:
    """RGBA array for one frame of a (grid, grid, n_frames) depth stack.
    ``global_max`` (if given) normalizes color intensity consistently
    across all frames of a run, matching webmap.py's behavior of computing
    one ``global_max`` across all 4 frames rather than per-frame -- so a
    deepening flood visibly gets *more* opaque/saturated across frames
    instead of each frame independently maxing out its own color range."""
    depth_grid = depth_stack[:, :, frame_index]
    max_depth = global_max if global_max is not None else float(depth_grid.max())
    return depth_to_rgba(depth_grid, max_depth)


def rgba_to_png_bytes(rgba: np.ndarray, upscale: int = UPSCALE) -> bytes:
    from PIL import Image

    img = Image.fromarray(rgba, "RGBA")
    if upscale and upscale != 1:
        img = img.resize((rgba.shape[1] * upscale, rgba.shape[0] * upscale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class FloodSource:
    depth_stack: np.ndarray  # (grid, grid, n_frames), normalized units
    transform: georef.GeoTransform
    frame_marks_s: List[float]
    frame_labels: List[str]
    bounds_match_georef: bool


def resolve_flood_source(config: dict) -> Optional[FloodSource]:
    """Figure out where (if anywhere) a run's flood raster data lives, from
    its ``config.json``, and load it. Two shapes exist:

    - Slice 2+ multiframe runs (``run_flooded_multiframe``): config has
      ``forecast_npz`` pointing at a ``flood_runner`` NPZ, which carries its
      own georeferencing transform (see module docstring) plus
      ``frame_marks_s``/``frame_labels``.
    - Slice 1 single-frame runs (``run_flooded``): config has
      ``scenario_npy`` pointing at a plain ``(128, 128, 4)`` precomputed
      output array with no embedded transform -- falls back to
      ``georef.DEFAULT_TRANSFORM`` (the flood model's fixed domain, which is
      what ``sample_edge_depths`` used by default for these runs too -- see
      ``floodtwin.sim.runner.compute_edge_states``). All 4 frames of the
      underlying model output are exposed even though only one frame_index
      was applied as a closure, since the raster overlay is independently
      useful context.

    Returns ``None`` for runs with neither field (e.g. baseline runs, which
    have no flood data at all).
    """
    forecast_npz = config.get("forecast_npz")
    if forecast_npz and Path(forecast_npz).exists():
        depth_stack, transform = load_forecast_stack(Path(forecast_npz))
        return FloodSource(
            depth_stack=depth_stack,
            transform=transform,
            frame_marks_s=config.get("frame_marks_s", DEFAULT_FRAME_MARKS_S),
            frame_labels=config.get("frame_labels", DEFAULT_FRAME_LABELS),
            bounds_match_georef=verify_bounds_match_georef(transform),
        )

    scenario_npy = config.get("scenario_npy")
    if scenario_npy and Path(scenario_npy).exists():
        depth_stack = np.load(scenario_npy)
        transform = georef.DEFAULT_TRANSFORM
        return FloodSource(
            depth_stack=depth_stack,
            transform=transform,
            frame_marks_s=DEFAULT_FRAME_MARKS_S,
            frame_labels=DEFAULT_FRAME_LABELS,
            bounds_match_georef=verify_bounds_match_georef(transform),
        )

    return None


def verify_bounds_match_georef(transform: georef.GeoTransform, tol: float = 1e-6) -> bool:
    """Sanity check invoked by the API route: does this run's forecast NPZ
    transform match ``georef.DEFAULT_TRANSFORM`` (the value
    ``coupling/edge_mapper.py`` used to map depths onto edges for this same
    run)? A mismatch would mean the flood overlay and the edge closures
    the frontend draws next to it were computed against different bounds --
    exactly the R2 coordinate-alignment risk PROJECT_PLAN.md flags. Returns
    True/False rather than raising so the API can surface it as a response
    field instead of a 500."""
    d = georef.DEFAULT_TRANSFORM
    return (
        abs(transform.north - d.north) < tol
        and abs(transform.south - d.south) < tol
        and abs(transform.east - d.east) < tol
        and abs(transform.west - d.west) < tol
        and transform.grid_size == d.grid_size
    )
