"""One-off verification script (Slice 2 Risk R1): confirms the deployment
weights load correctly by reproducing ``predictions_val.npy`` for
``v1_random_s42`` exactly, using the same indexing convention as
``evaluate_runs.py`` in the flood repo.

Run with the Python 3.13 / Keras 3 / TF 2.21 interpreter (the one that has
``keras.ops`` -- see IMPLEMENTATION_CONTEXT.md G2 and the Slice 2 report for
why this differs from the Python 3.8 interpreter used for SUMO/pytest):

    C:\\Users\\dcost\\AppData\\Local\\Programs\\Python\\Python313\\python.exe scripts\\verify_weights_load.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

FLOOD_REPO = Path(r"C:\Users\dcost\ChandraMentorship\CNN-LSTM-Flood-Forecasting")
REVISION_DIR = FLOOD_REPO / "revision_experiments"
RUN_DIR = REVISION_DIR / "results" / "v1_random_s42"
DATA_DIR = Path(r"C:\Users\dcost\ChandraMentorship\Example Dataset")

sys.path.insert(0, str(REVISION_DIR))
from flood_pipeline import build_model  # noqa: E402


def main():
    t0 = time.time()
    print("Building v1 architecture + loading best.weights.h5 ...")
    model = build_model("v1", pretrained=True)
    model.load_weights(str(RUN_DIR / "best.weights.h5"))
    print(f"  loaded in {time.time() - t0:.1f}s, params={model.count_params()}")

    # Same indexing convention as evaluate_runs.py: sorted filenames across
    # the full 300-sample dataset, indexed by eval_indices.npy.
    names = sorted(p.name for p in (DATA_DIR / "input").glob("*.npy"))
    print(f"  {len(names)} total input samples")

    eval_idx = np.load(RUN_DIR / "eval_indices.npy")
    print(f"  eval_indices: {eval_idx.shape}, range [{eval_idx.min()}, {eval_idx.max()}]")

    x_eval = np.stack(
        [np.load(DATA_DIR / "input" / names[i]) for i in eval_idx]
    ).astype("float32")
    print(f"  x_eval shape: {x_eval.shape}")

    t0 = time.time()
    y_pred = model.predict(x_eval, batch_size=4, verbose=0)
    print(f"  inference done in {time.time() - t0:.1f}s, y_pred shape={y_pred.shape}")

    y_pred_expected = np.load(RUN_DIR / "predictions_val.npy")
    print(f"  predictions_val.npy shape: {y_pred_expected.shape}")

    diff = np.abs(y_pred.astype("float64") - y_pred_expected.astype("float64"))
    max_diff = float(diff.max())
    mean_diff = float(diff.mean())
    close_1e4 = np.allclose(y_pred, y_pred_expected, atol=1e-4, rtol=1e-4)
    close_1e3 = np.allclose(y_pred, y_pred_expected, atol=1e-3, rtol=1e-3)

    print()
    print(f"max abs diff:  {max_diff:.8e}")
    print(f"mean abs diff: {mean_diff:.8e}")
    print(f"allclose(atol=1e-4, rtol=1e-4): {close_1e4}")
    print(f"allclose(atol=1e-3, rtol=1e-3): {close_1e3}")


if __name__ == "__main__":
    main()
