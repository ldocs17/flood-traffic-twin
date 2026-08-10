"""Paths for the flood inference subpackage.

IMPLEMENTATION_CONTEXT.md #1: the flood model repo and the Example Dataset
are read-only sibling inputs -- never edit them in place, only read/copy.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[3]

FLOOD_REPO = Path(r"C:\Users\dcost\ChandraMentorship\CNN-LSTM-Flood-Forecasting")
REVISION_DIR = FLOOD_REPO / "revision_experiments"
RESULTS_DIR = REVISION_DIR / "results"

EXAMPLE_DATASET_DIR = Path(r"C:\Users\dcost\ChandraMentorship\Example Dataset")
EXAMPLE_INPUT_DIR = EXAMPLE_DATASET_DIR / "input"
EXAMPLE_OUTPUT_DIR = EXAMPLE_DATASET_DIR / "output"

# IMPLEMENTATION_CONTEXT.md Q1 (resolved): v1 is the deployment checkpoint --
# best street recall (0.85), the safety-critical metric for routing.
DEFAULT_VARIANT = "v1"
DEFAULT_RUN_NAME = "v1_random_s42"

SCENARIOS_DIR = REPO_ROOT / "data" / "scenarios"


def weights_path(run_name: str = DEFAULT_RUN_NAME) -> Path:
    return RESULTS_DIR / run_name / "best.weights.h5"


def cached_forecast_path(scenario_stem: str, run_name: str = DEFAULT_RUN_NAME) -> Path:
    """Where a scenario's inference output NPZ is cached
    (``data/scenarios/<scenario>_<run_name>_forecast.npz``) so repeated CLI
    invocations for the same scenario/checkpoint don't re-run TF inference."""
    return SCENARIOS_DIR / f"{scenario_stem}_{run_name}_forecast.npz"


def list_available_scenarios(run_name: str = DEFAULT_RUN_NAME) -> List[Dict[str, object]]:
    """Slice 6: storm scenario names the browser's scenario form can offer,
    scanned from the Example Dataset's ``input/`` folder (every scenario
    that has a valid model *input* frame, IMPLEMENTATION_CONTEXT.md #2 --
    300 across the two storm events). Each entry also reports whether a
    ``flood_runner`` forecast is already cached for ``run_name`` (an
    instant run vs. a ~30s inference cold start, IMPLEMENTATION_CONTEXT.md
    G2 / PROGRESS.md's Slice 2 note) so the frontend can hint at it.

    Pure ``pathlib`` glob -- no TensorFlow import, safe to call from the
    API's usual (SUMO-capable, TF-less) Python 3.8 process."""
    if not EXAMPLE_INPUT_DIR.is_dir():
        return []
    result = []
    for p in sorted(EXAMPLE_INPUT_DIR.glob("*.npy")):
        stem = p.stem
        result.append({"name": stem, "cached": cached_forecast_path(stem, run_name).exists()})
    return result


# Slice 2 deviation (documented in the report): the repo's usual Python 3.8
# interpreter (`C:\Python38\python.exe`) has TensorFlow 2.13 / Keras 2.13,
# which predates `keras.ops` -- `flood_pipeline.py` (written against Keras 3,
# per the flood repo's cluster training env, see
# revision_experiments/README_CLUSTER.md "Keras 3 + TF") fails to import
# there. A local Python 3.13 interpreter with Keras 3.15 / TF 2.21 was found
# and verified to load the deployment weights correctly (see
# scripts/verify_weights_load.py and the Slice 2 report). `flood_runner.py`
# must be invoked with that interpreter; override via the
# FLOODTWIN_TF_PYTHON env var if the path differs on another machine.
DEFAULT_TF_PYTHON = r"C:\Users\dcost\AppData\Local\Programs\Python\Python313\python.exe"


def tf_python() -> str:
    return os.environ.get("FLOODTWIN_TF_PYTHON", DEFAULT_TF_PYTHON)
