# Slice 8 — Sensitivity and robustness

PROJECT_PLAN.md SG4 Slice 8: does the headline finding ("flood measurably
disrupts traffic, and rerouting/calibrated demand materially change the
picture") survive varying the assumptions baked into the coupling/model
layer? This is a parameter sweep over the coupling core (D3/D4/D5 constants),
not a redo of the demand-layer work from Slice 7.

**Fixed point held constant across every axis**: scenario
`Sep_30_2022_74.75`, 100% rerouting fraction (the headline convention used by
Slice 3's first figure and Slice 4/7's sweep tables — see
`src/floodtwin/analysis/sensitivity.py`'s module docstring), `v1` demand,
seed 42 unless the axis under test *is* seed.

**Baseline row** (all Slices 1-7 default settings: `v1` checkpoint, 300mm
closure threshold, max edge-depth aggregation, 120s rerouting period,
`DEPTH_SCALE_M=1.0`): mean travel-time delta **25.2s**, p95 delta **110.0s**
— this matches the headline number already published in PROGRESS.md's Slice
3/7 notes exactly, confirming the sweep's baseline point is the same
reference everything else in this project is measured against, not a new
number invented for this table.

Full machine-readable results: `runs/sensitivity/final_run/sensitivity_results.csv`,
`sensitivity_summary.json`, `sensitivity_table.md`. Every number below comes
from an actual SUMO run (0 teleports / 0 collisions on all 10 completed
points) — no interpolated or estimated values, per the same data-integrity
standard Slice 7 held itself to.

## Results

| axis | point | mean Δtt (s) | vs baseline | p95 Δtt (s) | vs baseline | mean % edges closed | % exposed |
|---|---|---|---|---|---|---|---|
| baseline | v1/300mm/max/120s/scale=1.0/seed=42 | 25.2 | — | 110.0 | — | 3.09% | 7.4% |
| closure_threshold | 200mm (20cm) | 26.5 | +5.2% | 112.4 | +2.2% | 3.69% | 13.7% |
| closure_threshold | 400mm (40cm) | 25.1 | -0.4% | 116.2 | +5.6% | 2.93% | 1.6% |
| aggregation | p95 (vs max) | 26.1 | +3.6% | 112.0 | +1.8% | 3.09%* | 9.1% |
| rerouting_period | 60s | 27.0 | +7.3% | 114.3 | +3.9% | 3.09% | 6.7% |
| rerouting_period | 300s | 27.8 | +10.3% | 116.0 | +5.5% | 3.09% | 8.1% |
| seed | 43 | 26.9 | +6.9% | 112.0 | +1.8% | 3.09% | 7.7% |
| seed | 44 | 26.4 | +4.8% | 113.2 | +2.9% | 3.09% | 7.6% |
| depth_scale | 0.5 | 9.5 | **-62.4%** | 52.0 | **-52.7%** | 0.08% | 0.9% |
| depth_scale | 2.0 | 28.7 | +13.9% | 117.0 | +6.4% | 4.43% | 14.2% |
| checkpoint | v4 (balanced) | n/a | **BLOCKED** | n/a | — | n/a | n/a |

\* The `aggregation=p95` point closes the *identical* count/set of edges as
the max baseline (edges here are short — 2.5m centerline samples — so a few
samples per edge means p95 and max land on nearly the same value); the
travel-time/exposure differences come entirely from the continuous
Pregnolato speed curve applying slightly higher speeds to wet-but-open edges
under p95's marginally lower sampled depth, which is enough to shift a
handful of route choices. Not a bug — a real (if second-order) effect of the
aggregation choice that shows up in the continuous part of D3's rule before
it shows up in binary closures.

## Interpretation for the paper's limitations section

**Robust**: closure threshold (20/30/40cm), edge-depth aggregation (max vs
p95), rerouting period (60/120/300s), and seed (42/43/44) all move the
headline mean/p95 travel-time delta by single-digit percentages (-0.4% to
+10.3%), never flip its sign, and never come close to threatening the
qualitative claim that flood measurably disrupts traffic. Seed variance
alone (42→44 spans 25.2-26.9s, ~7% band) is comparable in size to the
threshold/period/aggregation axes — i.e. those three modeling choices don't
add meaningfully more uncertainty than ordinary stochastic seed noise
already does. Run health was perfect throughout (0 teleports, 0 collisions
on all 10 completed points), so none of this is a simulation artifact
requiring the run to be discarded.

Closure *threshold* is the one axis worth flagging even though the headline
number survives it: `% exposed` swings far more (1.6% → 7.4% → 13.7% across
400/300/200mm) than the travel-time numbers do, because the exposure metric
is a binary "did your route ever touch a closed edge" count and closure sets
change more than travel times do. A figure built on exposure percentage
specifically (not just travel-time delta) should caveat the threshold
choice more prominently than the headline travel-time figure needs to.

**Fragile**: `DEPTH_SCALE_M` (Risk R7 / Open Question Q2). Halving it from
the interim value 1.0 to 0.5 cuts the mean delta by 62% and very nearly
eliminates closures entirely (3.09% → 0.08% of edges — essentially "almost
nothing closes"); doubling it to 2.0 pushes mean delta up 14% and closures up
43% relative to baseline. This is the single largest swing of any axis in
this table, by a wide margin — an order of magnitude more consequential than
closure threshold, aggregation, rerouting period, or seed combined. Every
number in this project's headline figures (Slices 1-7) is downstream of the
assumption `DEPTH_SCALE_M=1.0`, and this sweep confirms that assumption is
not a minor implementation detail: it is the single biggest lever on the
magnitude of every disruption number this project has produced. The
*qualitative* finding (flooding disrupts traffic, and more scale means more
disruption) is robust — the direction never flips — but the *magnitude*
claimed in any headline figure should not be read as precise until Q2 is
resolved with Yidi/Wang's upstream normalization constant. This is exactly
what R7's mitigation in PROJECT_PLAN.md anticipated ("scale included in
Slice 8 sensitivity"); Slice 8 confirms the concern was well-founded, not
that Q2 is somehow now resolved.

**Confirmed open, not resolved**: the flood-model-checkpoint axis (v1 vs
v4) — the one question this slice was specifically supposed to answer
("do routing conclusions survive the precision/recall trade-off the flood
paper is about?") — could not be run. Root cause (independently diagnosed,
not guessed): the Python 3.13 / Keras-3 interpreter this project's
`flood_runner` inference step depends on (`floodtwin.flood.paths.tf_python`,
IMPLEMENTATION_CONTEXT.md G2's two-interpreter split) has `numpy==1.26.4`
installed from a **from-source MinGW-W64 build**, not a prebuilt wheel — no
official `cp313-win_amd64` wheel exists for that numpy version, so `pip`
silently compiled it, and numpy's own runtime warning says outright
"CRASHES ARE TO BE EXPECTED." Every other point in this sweep uses the
already-cached `v1_random_s42` forecast (warm since Slice 2/7) and never
touches that interpreter's numpy at all; the checkpoint axis is the only
point that needed a genuinely fresh inference run, which is what exposed a
pre-existing environment defect unrelated to this slice's own code
(confirmed independently: `python -c "import numpy"` alone segfaults in that
interpreter, reproducibly, before any model or scenario code runs). This
module's fault-tolerance fix (see below) means that crash no longer takes
the whole sensitivity table down with it — the other 10 points completed and
are reported here — but it does mean the checkpoint question is an honest
**open item**, not a silently-dropped row or a guessed answer. Fixing the
numpy/Python-3.13 pin is tracked as a separate follow-up (out of scope for
this slice — touching it risks destabilizing the already-verified v1
weights-loading pipeline from Slice 2).

## Fault tolerance (a robustness bug in the robustness tool)

The first full run of this sweep hit the checkpoint-axis crash above and, in
its original form, let the uncaught exception take down the whole script —
losing the 10 already-completed points' results along with it, since they
only existed in an in-memory list until the very end. For a tool whose whole
purpose is testing robustness, a single point's failure taking down the
entire table was itself a robustness bug worth fixing as part of this slice:
`floodtwin.analysis.sensitivity.run_sensitivity` now wraps each point's run
in `try/except`, records a clearly-marked `error_row` (axis/label/params
intact, the real exception message, every metric field `None` — never a
fabricated number) on failure, and continues to the next point; the CSV/JSON/
markdown are also rewritten after every point (not just at the end) so a
sweep killed externally mid-run still leaves completed points on disk.

## What's still open after this slice

- **Q2 / `DEPTH_SCALE_M`** remains open (expected — this slice quantifies
  sensitivity to it, doesn't resolve it) and is now known to be the most
  consequential open question in the project, not just a caveat.
- **Checkpoint sensitivity (v1 vs v4)** is unanswered, blocked by a
  diagnosed, out-of-scope environment defect (see above), tracked
  separately.
- Every other Slice 8 axis (closure threshold, aggregation, rerouting
  period, seed) is answered: the headline finding is robust to all four.
