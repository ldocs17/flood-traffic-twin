# data/scenarios/ — flood frame used for Slice 1

`Sep_30_2022_74.75_output.npy` is a direct copy of
`C:\Users\dcost\ChandraMentorship\Example Dataset\output\Sep_30_2022_74.75.npy`
(read-only sibling input; the model-*output* folder, not the model-*input* folder --
see IMPLEMENTATION_CONTEXT.md G1 for the stale-path gotcha in the legacy scripts).

Shape `(128, 128, 4)`: predicted depth at t+15/30/45/60 min, in the model's
**normalized** depth units (IMPLEMENTATION_CONTEXT.md #2 -- not meters; see the
`DEPTH_SCALE_M` unit-gap note in `src/floodtwin/coupling/edge_mapper.py`).

## Why this frame

Picked from the `Sep_30_2022` event (the plan's OQ2 candidate) by scanning all 150
frames' t+15 channel for one with strong, spatially interesting flooding, so Slice 1's
closure demo has enough closed edges to produce a visible detour (Plan risk R5: a
320m x 320m patch might be too small to matter). `Sep_30_2022_74.75.npy` has the
highest t+15 max depth in the event (normalized 1.0, ~25.8% of the grid wet at >0.05),
and produces the intended result once mapped to the cropped district net: **60 of 948
edges have any in-grid depth sample; 29 of those close** (>=300mm at `DEPTH_SCALE_M
= 1.0`). See `src/floodtwin/coupling/edge_mapper.py` for the mapping code and
`runs/<...>_flooded/edge_states.csv` for the full per-edge table from an actual run.

This is a single-frame, single-scenario placeholder for the walking skeleton. Slice 2
replaces this with `flood_runner` running the real model on a chosen scenario and using
all four 15-minute frames.

## Slice 2: real inference

`flood_runner` (`src/floodtwin/flood/flood_runner.py`) runs the deployment model
(`v1_random_s42`, IMPLEMENTATION_CONTEXT.md G2) on the **input** frame with the same
suffix, `Sep_30_2022_74.75.npy` (`Example Dataset/input/`, shape (128,128,11)) --
continuity with Slice 1's chosen storm event/timestamp, but now driving real inference
instead of consuming a precomputed output frame directly. That sample happens to fall
in `v1_random_s42`'s held-out validation split (confirmed by checking membership in
`results/v1_random_s42/eval_indices.npy`), so this is a genuine out-of-training-sample
forecast, not a memorized one.

Output is cached at `Sep_30_2022_74.75_v1_random_s42_forecast.npz`
(`{scenario}_{run_name}_forecast.npz` naming) with keys: `depth_stack` (128,128,4
float32, normalized units), `north`/`south`/`east`/`west`/`grid_size` (georeferencing,
so downstream coupling code never hardcodes the grid bounds -- see
`floodtwin.coupling.georef.GeoTransform`), and provenance (`scenario`, `input_npy`,
`variant`, `run_name`, `weights_path`, `generated_at`). `floodtwin.sim.runner` reuses
this cache by (scenario, run_name) instead of re-running inference every CLI
invocation -- see `get_or_build_forecast` there.

`flood_runner` must run under a Keras-3 interpreter (`keras.ops` is required by
`flood_pipeline.py`), which is NOT the repo's usual Python 3.8 env (TF 2.13 / Keras
2.13, pre-Keras-3) -- see `src/floodtwin/flood/paths.py` and the Slice 2 report for
the full story and the verification that the weights load correctly under that
interpreter (`scripts/verify_weights_load.py`).
