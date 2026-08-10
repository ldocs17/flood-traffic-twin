"""Deployment model loading + inference (IMPLEMENTATION_CONTEXT.md G2).

Weights-only recipe, exact form from G2 -- do not "simplify" by loading a
``.keras`` file from ``checkpoints\\``/``Weights\\`` (ad-hoc, not the paper's
numbers) or rebuilding from ``CNN-LSTM_Yidi.py`` (wrong architecture):

    sys.path.insert(0, ".../CNN-LSTM-Flood-Forecasting/revision_experiments")
    from flood_pipeline import build_model
    model = build_model("v1", pretrained=True)
    model.load_weights(".../results/v1_random_s42/best.weights.h5")

Requires a Keras-3 environment (``keras.ops`` must exist) -- see
``floodtwin.flood.paths`` for why that's a different interpreter than the
rest of this repo. Import is deferred into the function bodies below so that
merely importing this module (e.g. transitively, by accident) doesn't
require TensorFlow to be installed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np

from . import paths


def build_deployment_model(variant: str = paths.DEFAULT_VARIANT):
    """Rebuild the architecture for ``variant`` via ``flood_pipeline.build_model``
    (IMPLEMENTATION_CONTEXT.md G2). Does not load weights."""
    revision_dir = str(paths.REVISION_DIR)
    if revision_dir not in sys.path:
        sys.path.insert(0, revision_dir)
    from flood_pipeline import build_model  # noqa: E402  (deferred, see module docstring)

    return build_model(variant, pretrained=True)


def load_deployment_model(
    variant: str = paths.DEFAULT_VARIANT, run_name: str = paths.DEFAULT_RUN_NAME
):
    """Build + load weights for the deployment checkpoint. The verified
    recipe (see scripts/verify_weights_load.py): max abs diff 2.6e-3 against
    ``predictions_val.npy`` on the run's held-out validation samples, vs.
    0.56 for a deliberately mismatched weights file -- confirms weights +
    architecture + preprocessing are correct (residual diff is ordinary
    cross-platform/cross-TF-version floating point noise, not a mismatch)."""
    model = build_deployment_model(variant)
    model.load_weights(str(paths.weights_path(run_name)))
    return model


def predict_depth_stack(model, input_array: np.ndarray) -> np.ndarray:
    """Run inference on one ``(128, 128, 11)`` input sample (or a batch
    ``(N, 128, 128, 11)``) and return the ``(128, 128, 4)`` (or
    ``(N, 128, 128, 4)``) predicted depth stack -- water depth at
    t+15/30/45/60 min, in the model's normalized units
    (IMPLEMENTATION_CONTEXT.md #2)."""
    single = input_array.ndim == 3
    batch = input_array[None, ...] if single else input_array
    batch = batch.astype("float32")
    pred = model.predict(batch, batch_size=max(1, min(4, len(batch))), verbose=0)
    return pred[0] if single else pred


def load_input_frame(input_npy: Path) -> np.ndarray:
    """Load and validate a storm scenario **input** sample: shape
    ``(128, 128, 11)`` (IMPLEMENTATION_CONTEXT.md #2 data contract) -- NOT
    the precomputed ``(128, 128, 4)`` output frames Slice 1 used directly."""
    arr = np.load(input_npy)
    if arr.shape != (128, 128, 11):
        raise ValueError(
            f"{input_npy}: expected an Example Dataset INPUT frame, shape "
            f"(128, 128, 11); got {arr.shape}. Did you point at output/ "
            f"instead of input/?"
        )
    return arr
