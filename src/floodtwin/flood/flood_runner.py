"""``flood_runner`` CLI (PROJECT_PLAN.md Slice 2): loads the deployment CNN-LSTM
model per IMPLEMENTATION_CONTEXT.md G2, runs it on a chosen storm scenario
**input** frame, and writes a georeferenced 4-frame depth-stack NPZ.

Must run under a Keras-3 interpreter (see ``floodtwin.flood.paths`` /
``floodtwin.flood.model`` docstrings) -- NOT the repo's usual Python 3.8:

    C:\\Users\\dcost\\AppData\\Local\\Programs\\Python\\Python313\\python.exe ^
        -m floodtwin.flood.flood_runner --scenario Sep_30_2022_74.75

Output: ``data/scenarios/<scenario>_<run_name>_forecast.npz`` with keys
``depth_stack`` (128,128,4 float32, normalized units), ``north``/``south``/
``east``/``west``/``grid_size`` (georeferencing, IMPLEMENTATION_CONTEXT.md #2),
and provenance fields (``scenario``, ``input_npy``, ``variant``, ``run_name``,
``weights_path``, ``generated_at``).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from . import paths
from .model import load_deployment_model, load_input_frame, predict_depth_stack


def resolve_input_path(scenario: str) -> Path:
    """``scenario`` may be a full path to an input .npy, or a bare scenario
    name (with or without ``.npy``) that's looked up in the Example Dataset's
    input/ folder."""
    p = Path(scenario)
    if p.suffix == ".npy" and p.exists():
        return p
    name = scenario if scenario.endswith(".npy") else scenario + ".npy"
    candidate = paths.EXAMPLE_INPUT_DIR / name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"could not resolve scenario {scenario!r} to an input .npy "
        f"(tried {p}, {candidate})"
    )


def run_inference(
    scenario: str,
    variant: str = paths.DEFAULT_VARIANT,
    run_name: str = paths.DEFAULT_RUN_NAME,
    out_path: Path = None,
) -> Path:
    input_path = resolve_input_path(scenario)
    scenario_stem = input_path.stem

    print(f"Loading deployment model (variant={variant!r}, run={run_name!r}) ...")
    model = load_deployment_model(variant=variant, run_name=run_name)

    print(f"Loading input frame: {input_path}")
    x = load_input_frame(input_path)

    print("Running inference ...")
    depth_stack = predict_depth_stack(model, x).astype("float32")
    print(
        f"  depth_stack shape={depth_stack.shape}, "
        f"min={depth_stack.min():.4f}, max={depth_stack.max():.4f}"
    )

    if out_path is None:
        out_path = paths.cached_forecast_path(scenario_stem, run_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Georeferencing metadata travels with the stack (PROJECT_PLAN.md Slice 2
    # / IMPLEMENTATION_CONTEXT.md #2) so downstream coupling code doesn't
    # hardcode the grid bounds -- see floodtwin.coupling.georef.GeoTransform.
    from floodtwin.coupling.georef import DEFAULT_TRANSFORM

    np.savez(
        out_path,
        depth_stack=depth_stack,
        north=np.float64(DEFAULT_TRANSFORM.north),
        south=np.float64(DEFAULT_TRANSFORM.south),
        east=np.float64(DEFAULT_TRANSFORM.east),
        west=np.float64(DEFAULT_TRANSFORM.west),
        grid_size=np.int64(DEFAULT_TRANSFORM.grid_size),
        scenario=np.array(scenario_stem),
        input_npy=np.array(str(input_path)),
        variant=np.array(variant),
        run_name=np.array(run_name),
        weights_path=np.array(str(paths.weights_path(run_name))),
        generated_at=np.array(datetime.now().isoformat()),
    )
    print(f"Wrote {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Run CNN-LSTM flood inference on a storm scenario input frame.")
    parser.add_argument(
        "--scenario",
        required=True,
        help="Scenario name (e.g. Sep_30_2022_74.75) or full path to an input .npy",
    )
    parser.add_argument("--variant", default=paths.DEFAULT_VARIANT)
    parser.add_argument("--run-name", default=paths.DEFAULT_RUN_NAME)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    run_inference(args.scenario, variant=args.variant, run_name=args.run_name, out_path=args.out)


if __name__ == "__main__":
    main()
