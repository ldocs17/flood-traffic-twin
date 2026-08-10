"""Flood forecasting: loads the deployment CNN-LSTM model (weights-only,
rebuilt from ``flood_pipeline.py`` per IMPLEMENTATION_CONTEXT.md G2) and runs
inference on a storm scenario input to produce a georeferenced 4-frame depth
stack.

NOTE on interpreters (see ``src/floodtwin/flood/model.py`` docstring and the
Slice 2 report for the full story): this subpackage requires TensorFlow/Keras
**3.x** (``keras.ops`` must exist), which is NOT what's installed under the
repo's usual ``C:\\Python38`` interpreter (TF 2.13 / Keras 2.13, pre-Keras-3).
Run ``flood_runner`` with a Keras-3 interpreter; the rest of the repo
(SUMO/TraCI/coupling/tests) is unaffected because nothing outside this
subpackage imports TensorFlow.
"""
