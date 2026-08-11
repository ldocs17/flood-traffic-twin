# Vertical slice progress tracker

Tracks completion of the vertical slices defined in [PROJECT_PLAN.md](PROJECT_PLAN.md) §3.
Maintained by the `/loop` orchestrator across iterations — one subagent implements one
slice per loop iteration; the orchestrator verifies before checking it off here.

| # | Slice | Sub-goal | Status |
|---|-------|----------|--------|
| 1 | Walking skeleton — vehicles detour around the flooded block | SG1 | **done** |
| 2 | Real forecasts, real curve — full 60-minute coupling | SG1 | **done** |
| 3 | Baseline comparison and first research figure | SG2 | **done** |
| 4 | Information sweep — the headline result | SG2 | **done** |
| 5 | Web replay of a completed run | SG3 | **done** |
| 6 | Run from the browser | SG3 | **done** |
| 7 | Calibrated demand | SG4 | **done** |
| 8 | Sensitivity and robustness | SG4 | **done** |

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

- **2026-08-10, Slice 4 done — headline result.** `src/floodtwin/analysis/sweep.py`:
  parameterized `--device.rerouting.probability` (was hardcoded 100% since Slice 1),
  swept {0,25,50,75,100}% × 3 seeds (30 SUMO runs), baseline re-run per point (not
  shared across the sweep — documented reasoning: SUMO's rerouting device affects the
  no-flood case too). **Operational note**: the implementing subagent's background
  sweep process died at 3/15 points when its session ended — no live process, log
  stalled at ~124 min of subagent wall time with only ~2 min of actual sweep progress.
  Rather than resume the exhausted agent, the orchestrator re-ran the sweep directly
  (fresh checkout, 143.5s for all 15 points) and finished commit/push/PR itself — worth
  remembering for future slices with a long-running batch step: prefer having the
  *orchestrator* run the expensive final verification pass in its own properly-monitored
  background shell rather than trusting a subagent's self-managed background process to
  survive to the end of its turn. [PR #2](https://github.com/ldocs17/flood-traffic-twin/pull/2).
  Result (`Sep_30_2022_74.75`, base_seed=42): mean travel-time delta drops 77.7s→26.2s
  and p95 drops 371.7s→111.7s as rerouting goes 0%→100%, monotonic with tight seed
  variance (no reversals) — traveler information clearly mitigates flood disruption,
  and more so in the tail than the mean. 57/57 tests pass; CI green; figure/CSV/JSON
  verified by independent re-run.

- **2026-08-10, Slice 5 done — web replay.** `src/floodtwin/api/` (FastAPI: `/api/runs`,
  `/api/runs/{id}/config`, `/api/runs/{id}/fcd`, `/api/runs/{id}/edge_states`,
  `/api/network`, `/api/runs/{id}/flood/frames`, `/api/runs/{id}/flood/{i}.png`) +
  `web/` (static MapLibre GL JS page, no build step, run picker + time scrubber + flood
  overlay + edge coloring + animated FCD positions). 84/84 tests pass (27 new).
  [PR #3](https://github.com/ldocs17/flood-traffic-twin/pull/3). Verified independently:
  started the server myself, hit every endpoint with real requests (948-edge network
  GeoJSON, real flood PNG viewed and visually sane, `bounds_match_georef: true` with
  bounds matching IMPLEMENTATION_CONTEXT.md §2 exactly, path-traversal rejected).
  **Known environment limitation** (hit independently by both the subagent and the
  orchestrator): this sandbox's browser pane doesn't composite frames unless actively
  displayed, so MapLibre's `style.load` event never fires and the map canvas can't be
  screenshotted here — confirmed via `map.isStyleLoaded()` → `false` and `addSource`
  throwing "Style is not done loading." This is standard MapLibre gating behavior, not
  an app bug (the app correctly waits for `map.on('load', ...)` per MapLibre's own
  best practice). Worked around by manually replicating the run-list population logic
  in the live console, which worked correctly. **Recommend a manual spot-check in a
  normal desktop browser** before relying on this for the paper demo — neither agent
  could get a real pixel screenshot of the rendered map in this environment.

- **2026-08-10, Slice 6 done -- run from the browser.** `POST /api/runs` (validates
  storm/rerouting/seed/manual-closures, schedules `runner.run_flooded_multiframe` -- the
  same orchestration the CLI uses -- as a FastAPI `BackgroundTasks` job) + `GET
  /api/run_jobs/{id}` polling + `GET /api/scenarios`. New "intervention" feature: manual
  edge closures, force-closed at t=0 via `controller.run_with_edge_states` and never
  reopened by the flood-driven per-mark schedule -- additive to, not a replacement for,
  flood-derived closures -- and overlaid onto the written edge-state table so replay
  coloring matches what TraCI actually enforced. Frontend refactored Slice 5's replay UI
  into a `createPanel()` factory instantiated twice (`panelA`/`panelB`) for genuine
  side-by-side comparison, plus a scenario form and click-to-toggle manual closures on
  the map. [Issue #4](https://github.com/ldocs17/flood-traffic-twin/issues/4),
  [PR #5](https://github.com/ldocs17/flood-traffic-twin/pull/5). 113/113 tests pass (29
  new). Verified independently: checked out the branch into a **separate** worktree and
  repointed the editable install at it (so verification wasn't silently exercising the
  implementing agent's own working copy), reran the full suite, read the full diff, then
  started the real API server and drove `POST /api/runs` end-to-end against real SUMO --
  submitted a real scenario, polled to completion (~22s), and specifically proved the
  closure is genuinely additive (not just flood-derived) by force-closing an edge with
  `depth_m: 0.0` that was open in a plain run and confirming `closed: true` at all 4
  marks with the run still healthy (0 teleports/collisions). Exercised all five
  validation error paths (bad edge id, bad scenario, out-of-range rerouting probability,
  missing scenario, unknown job id) directly via curl -- all matched the code. Read
  `web/app.js`/`index.html` end to end; selectors and panel wiring check out. Same known
  environment limitation as Slice 5 (browser pane can't composite MapLibre canvases for
  screenshots) -- the implementing agent worked around it by driving the real UI
  (button clicks, a real MapLibre map click event) rather than faking evidence; worth a
  manual desktop-browser spot-check before the paper demo, same as Slice 5.

- **2026-08-11, Slice 7 done -- calibrated demand, real VDOT counts.** Before
  dispatching this slice, the orchestrator independently confirmed VDOT traffic-count
  data was actually available for this district (a real risk for a slice whose whole
  point is replacing placeholder demand with real data) by querying VDOT's public
  ArcGIS "Bidirectional Traffic Volume 2022" feature service directly -- found Colley
  Ave and Hampton Blvd (filed under state route `VA-337` in this dataset) both have
  real, high-quality (`A`/`G`) AADT segments inside the district, including one right
  at ODU. That verified endpoint and query were handed to the implementer instead of
  leaving data discovery to chance.

  Result: `data/demand/vdot_counts/` (raw fetch + `PROVENANCE.md`: query URL, fetch
  date, quality-code inclusion rules, AADT->peak-hour `K_FACTOR` conversion) and
  `data/demand/calibrated_v2/` (routeSampler output + methodology). 88 fetched
  features -> 20 raw Hampton/Colley records -> 9 deduped segments (VDOT
  double/triple-counts each physical segment across direction-of-travel entries) ->
  only 2 actually overlap `data/net/district.net.xml` (VDOT's count segments are
  bounded by major cross streets kilometers apart; the district happens to sit
  entirely inside one representative segment per corridor) -> a 3-stage edge-matching
  filter (corridor restriction + bearing check, hit-count threshold, modal-speed
  filter, in `src/floodtwin/demand/edge_matching.py`) narrows that to 47 of 948
  district edges (39 Hampton, 8 Colley) getting a real VDOT-derived count; the other
  901 are left unconstrained for `routeSampler`, never fabricated. `routeSampler` fit:
  GEH < 5.0 at 100% of the 47 counted locations, 3,807 vehicles. New `--demand
  {v1,calibrated_v2}` selector threaded through `runner.py`/`sweep.py`
  (`paths.DEFAULT_DEMAND_VARIANT = "v1"` preserves all prior slices' behavior;
  `src/floodtwin/api/` untouched, so Slice 6 is unaffected). [Issue
  #6](https://github.com/ldocs17/flood-traffic-twin/issues/6), [PR
  #7](https://github.com/ldocs17/flood-traffic-twin/pull/7). 149/149 tests pass (36
  new, pure Python -- count parsing, AADT conversion, edge-matching against a fake
  net, edgeData XML, `PROVENANCE.md` structure -- no SUMO/TF needed).

  **Operational note, same lesson as Slice 4**: the implementing agent launched the
  5-fraction x 3-seed rerouting sweep for both demand variants as its own background
  process and then paused mid-task waiting on it. The orchestrator found the `v1`
  sweep had already finished cleanly (15/15 points) but ran the `calibrated_v2` sweep
  itself in a separately-monitored background shell rather than trust a
  subagent-managed background process to survive a turn/session boundary (this
  session in fact restarted mid-wait; the OS-level process survived because it was
  orchestrator-launched, independent of any agent session) -- then ran the final
  `scripts/compare_demand_variants.py` comparison itself and handed the results back
  to the implementer to write up.

  Verified independently before merging: checked out the branch into a separate
  worktree, repointed the editable install at it, reran the full suite (149/149).
  **Cross-checked the committed `raw_query_district.geojson` against the
  orchestrator's own pre-dispatch VDOT API query** -- exact match on every ADT/quality
  value for both corridors, confirming the data is real, not fabricated. Read
  `vdot.py`/`edge_matching.py`/`edgedata.py`/`tests/test_demand.py` in full: quality
  filtering excludes low-confidence `N` records, dedup surfaces real
  disagreements (`adt_conflict`) rather than averaging them away, unconstrained edges
  are never written as a fabricated zero. Re-derived the headline comparison numbers
  independently and cross-checked the PR's full sweep-by-fraction table against the
  raw `sweep_summary.json` aggregates -- every number matched exactly. Confirmed
  `DEFAULT_DEMAND_VARIANT = "v1"` and zero changes to `src/floodtwin/api/` in the
  diff.

  **Headline finding**: calibrated demand shows ~4x the mean travel-time disruption of
  the illustrative `v1` random demand (25.2s -> 107.7s) and ~3.5x the p95 tail
  (110.0s -> 388.0s), holding across the full rerouting sweep (0/25/50/75/100%), with
  0 teleports/collisions throughout both variants (a real signal, not a simulation
  artifact). Because `calibrated_v2`'s ~3,807 vehicles are real VDOT-measured traffic
  that actually uses Hampton Blvd/Colley Ave -- exactly where the flood hits -- while
  `v1`'s ~1,426 arbitrary-volume vehicles only incidentally touch those corridors,
  the random-demand baseline used in Slices 1-6 was *understating* real flood
  disruption, not just an unverified placeholder. `calibrated_v2` is corridor-focused
  demand (only routes touching a counted edge), not full-district demand like `v1` --
  documented prominently in `data/demand/calibrated_v2/README.md` as an honest scope
  limitation, not glossed over.

- **2026-08-11, Slice 8 done -- sensitivity and robustness (last slice in the plan).**
  Six sensitivity axes over the coupling/model layer (not the demand layer -- that was
  Slice 7), each varied one-at-a-time against the Slices 1-7 baseline (v1 checkpoint,
  300mm closure threshold, max aggregation, 120s rerouting period,
  `DEPTH_SCALE_M=1.0`), scenario `Sep_30_2022_74.75` @ 100% rerouting (the established
  headline point): closure threshold (200/300/400mm), edge-depth aggregation (max/p95),
  rerouting period (60/120/300s), seed (42/43/44), `DEPTH_SCALE_M` (0.5/1.0/2.0), and
  flood-model checkpoint (v1 vs v4). All four new parameters
  (`closure_threshold_mm`/`depth_scale_m`/`aggregation` on
  `src/floodtwin/coupling/edge_mapper.py`, `rerouting_period_s` on
  `src/floodtwin/sim/runner.py`) default to the pre-Slice-8 hardcoded values, so no
  existing call site changed behavior. New `src/floodtwin/analysis/sensitivity.py`
  (11-point sweep) + `SENSITIVITY.md` (full table + writeup for the paper's limitations
  section). [Issue #8](https://github.com/ldocs17/flood-traffic-twin/issues/8), [PR
  #9](https://github.com/ldocs17/flood-traffic-twin/pull/9). 180/180 tests pass (34
  new).

  **Real infrastructure blocker hit and handled honestly, not papered over**: the
  flood-model-checkpoint axis (v1 vs v4) -- the one question this slice exists
  specifically to answer ("do routing conclusions survive the flood paper's own
  precision/recall trade-off?") -- crashed with a native access violation. Root cause
  (independently diagnosed by both the orchestrator and the implementer, matching
  exactly): the Python 3.13/Keras-3 interpreter `flood_runner` inference depends on
  (IMPLEMENTATION_CONTEXT.md G2) has `numpy==1.26.4` installed from a from-source
  MinGW-W64 build -- no official `cp313-win_amd64` wheel exists for that pin, and
  numpy's own runtime warning says "CRASHES ARE TO BE EXPECTED." `python -c "import
  numpy"` alone segfaults, 100% reproducible, in that interpreter -- unrelated to v4
  specifically; every other sweep point reused an already-warm `v1_random_s42` cached
  forecast and never touched that interpreter, so the checkpoint axis was the only
  point that exposed this pre-existing environment defect. Not fixed as part of this
  slice (changing the numpy/Python-3.13 pin risks destabilizing the already-verified
  Slice 2 weights-loading pipeline) -- flagged as a separate follow-up task instead.
  **Operational lesson layered on top of Slice 4/7's**: the original
  `run_sensitivity` implementation let this one point's uncaught exception take the
  whole sweep down, losing the 10 already-completed points too (they only existed in
  an in-memory list). Fixed as part of this slice, on the orchestrator's instruction,
  as a legitimate robustness improvement to a *robustness-analysis tool*: each point's
  run is now wrapped in try/except, a failed point is recorded as a clearly-marked
  error row (real exception message, every metric field `None`, never fabricated)
  rather than crashing, and results are rewritten to disk after every point (not just
  at the end).

  Verified independently before merging: checked out the branch into a separate
  worktree, reran the full suite (180/180), read the parameter-plumbing diffs (every
  default preserves prior behavior), and **independently re-ran the entire 11-point
  sweep myself from scratch** -- matched the PR's reported numbers to the decimal on
  all 10 completed points (one harmless 0.1s rounding difference on a single p95
  value), 0 teleports/collisions on all 10, and the checkpoint axis errored out with
  the identical exception pattern in my own run too, confirming both that the
  environment blocker is real/reproducible (not a fluke) and that the fault-tolerance
  fix genuinely works end-to-end.

  **Headline sensitivity findings** (full table + interpretation in `SENSITIVITY.md`):
  closure threshold, aggregation, rerouting period, and seed all move the headline
  mean/p95 travel-time delta by single-digit percentages (-0.4% to +10.3%), never flip
  its sign -- robust, and no more uncertain than ordinary seed-to-seed noise (~7% band
  across seeds 42-44 alone). `DEPTH_SCALE_M` is a different story: halving it (0.5)
  cuts the mean delta 62% and nearly eliminates closures (3.1% -> 0.1% of edges);
  doubling it (2.0) pushes the delta up 14% and closures up 43% -- an order of
  magnitude more consequential than every other axis combined, confirming Risk R7's
  concern was well-founded rather than resolving Open Question Q2 (still open,
  `PROJECT_PLAN.md`'s R7 row now points to this result). The flood-model-checkpoint
  question is a confirmed **open item**, not answered, due to the environment blocker
  above.

  This closes PROJECT_PLAN.md's full vertical-slice roadmap (SG1-SG4, 8/8 slices
  done); SG5 (live-data upgrade path) remains explicitly flagged-not-scheduled.
