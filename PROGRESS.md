# Vertical slice progress tracker

Tracks completion of the vertical slices defined in [PROJECT_PLAN.md](PROJECT_PLAN.md) §3.
Maintained by the `/loop` orchestrator across iterations — one subagent implements one
slice per loop iteration; the orchestrator verifies before checking it off here.

| # | Slice | Sub-goal | Status |
|---|-------|----------|--------|
| 1 | Walking skeleton — vehicles detour around the flooded block | SG1 | **done** |
| 2 | Real forecasts, real curve — full 60-minute coupling | SG1 | **done** |
| 3 | Baseline comparison and first research figure | SG2 | **done** |
| 4 | Information sweep — the headline result | SG2 | pending |
| 5 | Web replay of a completed run | SG3 | pending |
| 6 | Run from the browser | SG3 | pending |
| 7 | Calibrated demand | SG4 | pending |
| 8 | Sensitivity and robustness | SG4 | pending |

SG5 (live-data upgrade path) is explicitly flagged-not-scheduled in the plan — excluded
from this loop.

## Infrastructure

- **GitHub**: [ldocs17/flood-traffic-twin](https://github.com/ldocs17/flood-traffic-twin)
  (public), default branch `main`.
- **CI**: `.github/workflows/ci.yml` runs on every push/PR to `main` — pure unit tests
  (`tests/test_coupling.py`, no SUMO/TF needed) on Python 3.8 and 3.11, plus a
  compile-check of all sources. Full pipeline runs (SUMO/TraCI/model inference) depend
  on local sibling-repo paths and a SUMO install, so they are intentionally out of CI's
  scope — see README.md.

## Workflow (as of Slice 3)

Subagents now implement each slice on a feature branch (`slice-N-<short-name>`) and
open a PR against `main` (`gh pr create`) rather than committing directly. The
orchestrator fetches the branch, re-runs tests and the full pipeline independently,
reads the diff, posts a verification comment, then squash-merges and updates this file
on `main`. `PROGRESS.md` is still orchestrator-owned — subagents never edit it.

## Notes log

(Orchestrator appends one short entry per loop iteration below, newest last.)

- **2026-08-10, Slice 1 done.** Subagent built the full repo scaffold (`pyproject.toml`,
  `data/{net,demand,scenarios}`, `src/floodtwin/{coupling,sim,analysis}`, `tests/`).
  Cropped district net (948 edges, verified corridors via `road_segments.json` name
  matching), routes cut with `cutRoutes.py`, one Sep 30 2022 flood frame (t+15) mapped
  to 29 closed edges via `sample_edge_depths`/`closed_edges` (DEPTH_SCALE_M=1.0,
  UNCONFIRMED per Q2). Verified independently: `pytest` 14/14 pass; baseline run
  0 teleports/1398 arrived, flooded run 0 teleports/1345 arrived/29 closed edges — both
  match the subagent's report exactly (checked raw `summary.xml`/`tripinfo.xml`, not
  just re-reading the report). Rerouting evidence solid: 121→191 vehicles rerouted,
  and of vehicles that departed after the 900s closure, zero touched a closed edge
  (vs. 83 in baseline) — real detour behavior, not just "should happen." Demo map at
  `runs/demo_baseline_vs_flooded.html`. One flagged deviation (16 vehicles discarded
  because their *destination* edge was closed, not just their path) is a reasonable
  Slice 1 limitation, not a bug — worth a metric in Slice 3.

- **2026-08-10, Slice 2 done.** Subagent added `flood_runner` (real model inference via
  the G2 recipe), the full Pregnolato speed curve (`edge_mapper.pregnolato_v_safe_kmh`/
  `edge_speed_ms`/`speeds_and_closures`), 4-timestep TraCI application, and multiframe
  run artifacts. R1 (weights loading) resolved and verified against
  `predictions_val.npy`: max abs diff 2.6e-3 (vs 0.56 for a deliberately wrong-variant
  control) — I independently reran `scripts/verify_weights_load.py` and got the
  identical 2.61718407e-03. Key discovery: `C:\Python38` (TF 2.13/Keras 2.13) lacks
  `keras.ops` needed by `flood_pipeline.py`, so inference runs under a separate
  Python 3.13/Keras-3 interpreter (`C:\Users\dcost\AppData\Local\Programs\Python\
  Python313\python.exe`) with the NPZ cached to disk; SUMO/TraCI/coupling code stays
  on Python 3.8 and never imports TensorFlow. This two-interpreter split is now a
  standing project fact — worth folding into IMPLEMENTATION_CONTEXT.md if it keeps
  coming up. Re-ran the full `--scenario Sep_30_2022_74.75 --seed 42` command myself:
  reproduced the exact per-frame closed/slowed table (29/40, 30/37, 29/42, 29/40) and
  health (0 teleports, 0 collisions, 1335 arrived) reported by the subagent. 28/28
  tests pass (14 new).

- **2026-08-10, GitHub + CI set up.** Public repo at
  [ldocs17/flood-traffic-twin](https://github.com/ldocs17/flood-traffic-twin), Slices
  1-2 pushed as the initial commit on `main`. Added `.github/workflows/ci.yml` (pure
  unit tests on py3.8/py3.11 + a compile-check — no SUMO/TF needed, confirmed by
  running tests with `SUMO_HOME` unset) and a root `README.md`. First CI run green.

- **2026-08-10, Slice 3 done — first PR-based slice.** Workflow changed per user
  request: subagents now branch + PR instead of committing to the working tree
  directly (see "Workflow" above). Subagent built `src/floodtwin/analysis/metrics.py`
  (travel-time delta, exposure, throughput, closure timeline — all pure/testable) on
  `slice-3-baseline-metrics`, opened
  [PR #1](https://github.com/ldocs17/flood-traffic-twin/pull/1). Verified
  independently before merging: checked out the branch, ran `pytest` (45/45 pass),
  read the full diff (scoped exactly to the described files, no incidental changes),
  read `metrics.py` end to end, and — most importantly — reran the entire pipeline
  fresh (`runner.py --seed 42 --scenario Sep_30_2022_74.75 --metrics`, a brand new
  sim run, not reusing cached output) and got numbers matching the PR's claims exactly
  (mean travel time 169.9s→195.1s, p95 289.0s→353.0s, throughput 1398→1335, exposure
  99/1335 closed-edge / 286/1335 wet-edge, 0 teleports/collisions both runs). Opened
  the generated PNG and visually confirmed the right-shifted, longer-tailed flooded
  distribution. Squash-merged via `gh pr merge --squash --delete-branch`.
