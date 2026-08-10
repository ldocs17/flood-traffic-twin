# Implementation Context — verified facts, gotchas, and unknowns

Companion to [PROJECT_PLAN.md](PROJECT_PLAN.md). Everything here was verified against
the actual files on 2026-08-10 unless marked **UNCONFIRMED**. An agent executing the
plan should trust this file over paths/claims found inside the legacy scripts.

---

## 1. Asset inventory (verified paths)

| Asset | Path | Notes |
|---|---|---|
| Flood model repo | `C:\Users\dcost\ChandraMentorship\CNN-LSTM-Flood-Forecasting` | Git repo. Do not modify. |
| **Canonical model code** | `...\CNN-LSTM-Flood-Forecasting\revision_experiments\flood_pipeline.py` | `build_model(variant, pretrained=True)`, `VARIANTS` dict, architecture. This supersedes `CNN-LSTM_Yidi.py` and `Physics_Informed_Loss.py` for deployment purposes. |
| **Deployment weights** | `...\revision_experiments\results\v1_random_s42\best.weights.h5` | The paper's canonical runs. V1 chosen for deployment: street recall 0.85 (0.77 mean across seeds) — missing a flooded street is the costly error for routing. Weights-only file — see G2. |
| Balanced alternative | `...\revision_experiments\results\v4_random_s42\best.weights.h5` | Best constrained-variant MAE (0.0133) + F1 (0.43), cleaner false-alarm profile at some recall cost. Used in the Slice 8 checkpoint-sensitivity check. |
| Per-run validation data | `...\results\<run>\config.json`, `predictions_val.npy`, `eval_indices.npy`, `metrics.json` | Enables exact load verification — see G2 |
| Legacy architecture code | `...\CNN-LSTM-Flood-Forecasting\CNN-LSTM_Yidi.py`, `Physics_Informed_Loss.py` | Historical; do not use for loading |
| Legacy checkpoints | `...\CNN-LSTM-Flood-Forecasting\checkpoints\*.keras`, `Weights\*.keras` | ⚠️ Ad-hoc one-off runs, NOT behind the paper's numbers. Do not deploy. |
| Model design report | `...\CNN-LSTM-Flood-Forecasting\Physics_Informed_Loss_Report.md` | Describes V3 architecture & training |
| Storm dataset | `C:\Users\dcost\ChandraMentorship\Example Dataset\input\` and `...\output\` | 300 input `.npy` samples. ⚠️ Gotcha G1 |
| SUMO project | `C:\Users\dcost\ChandraMentorship\sumo_norfolk\` | Net, routes, trips, cfg, outputs, webmap |
| SUMO network | `...\sumo_norfolk\norfolk_hampton.net.xml` | Source for the district crop |
| Existing routes/trips | `...\sumo_norfolk\norfolk_routes.xml`, `norfolk_trips.xml` | Provenance **UNCONFIRMED** (likely randomTrips) |
| Sim config | `...\sumo_norfolk\norfolk.sumocfg` | 0–3600 s, fcd/tripinfo/edgedata/summary outputs |
| Flood overlay renderer | `...\sumo_norfolk\webmap.py` | Reusable RGBA depth-render logic; source of grid bounds |
| SUMO install | `C:\Program Files (x86)\Eclipse\Sumo` | netconvert/sumo **v1.26.0**; tools under `tools\` (e.g. `tools\route\cutRoutes.py`, `tools\randomTrips.py`) |

### G1 — Stale paths inside legacy scripts
`CNN-LSTM_Yidi.py` and `webmap.py` hardcode `C:\Users\dcost\Chandra Mentorship\Example Dataset`
(with a space). That directory **does not exist**. The real location is
`C:\Users\dcost\ChandraMentorship\Example Dataset` (no space). Never copy paths from the
legacy scripts without checking here first.

---

## 2. Flood model data contract

### Input: `(128, 128, 11)` float array per sample
Channel map (inferred from code — indices confirmed where noted):

| Channel | Meaning | Evidence |
|---|---|---|
| 0 | **DEM, min–max normalized to [0, 1]** | `flood_pipeline.py:241` — `dem = x[:, :, :, 0]` |
| 1–8 | Historical depth stack (per the paper). ⚠️ Channel 1 reads like a [0,1] elevation field — double-check it before ever assembling inputs by hand | Paper; `webmap.py` uses `[6,7,8,9]` as recent-depth fallback |
| 9 | Last observed depth (most recent frame) | `flood_pipeline.py:242` |
| 10 | Rainfall/tide time series, embedded as a 12×8 block in the top-left corner; rest of the plane unused | `flood_pipeline.py:118,243`; `CNN-LSTM_Yidi.py:142` |

### Output: `(128, 128, 4)` = water depth at t+15, t+30, t+45, t+60 min
- Output activation is **ReLU** (non-negative depth). The baseline `CNN-LSTM_Yidi.py`
  still shows sigmoid — superseded.
- **Depth units are NORMALIZED, not meters** (resolved 2026-08-10). Targets range
  0 → ~0.65; code and paper call them "normalized units" (`evaluate_runs.py:55`,
  `Physics_Informed_Loss.py:135` "normalized DEM"; paper Table 2 hedges "meters
  (normalized depth units)"). The DEM is min–max normalized to [0,1] and depths share
  that vertical scale, so `0.5` means `0.5 × (unknown scale factor)` meters. The
  normalization constant lives in Wang et al.'s upstream preprocessing, **not in this
  repo** — it cannot be recovered here. The paper's wet threshold (0.01) is likewise
  in normalized units. See Q2 and §4 for how the depth rule handles this.

### Filenames (resolved 2026-08-10)
`<Mon>_<DD>_<YYYY>_<NN.NN>.npy`. The numeric suffix is a **sequential time index in
hours** — it steps by exactly 0.25 (= 15 min, the model cadence) every frame. It is
NOT a tide-gauge reading (a real gauge never increments by a perfect constant; the
"gauge reading: 65.75 ft" in a paper figure is a hand-written annotation, and the
loaders only use the suffix as a `sorted()` key).
- Two events: `Aug_29_2017` (73.75 → 111.00) and `Sep_30_2022` (57.75 → 95.00),
  150 frames each = the 300 samples.
- Consecutive suffixes are consecutive 15-min timesteps — scenario selection can pick
  a temporally contiguous window when continuity matters (Plan OQ2).
- Input and output folders use identical filenames for X/Y pairing.

### Georeferencing of the 128×128 grid (from `webmap.py:24-27`)
```
NORTH = 36.898650   SOUTH = 36.895770
WEST  = -76.304447  EAST  = -76.300846
row 0 = north edge; col 0 = west edge
lat = NORTH - row * (NORTH-SOUTH)/127
lon = WEST  + col * (EAST-WEST)/127
```
≈ 320 m × 320 m, ≈ 2.5 m/pixel. Keep the `/127` (pixel-centers-at-corners) convention
consistent everywhere; do not silently switch to `/128`.

### G2 — Weights loading recipe (canonical)
The deployment weights are **weights-only** (`best.weights.h5`), so the architecture
must be rebuilt in code first — and it must come from `flood_pipeline.py`, nothing else:

```python
import sys
sys.path.insert(0, r"C:\Users\dcost\ChandraMentorship\CNN-LSTM-Flood-Forecasting\revision_experiments")
from flood_pipeline import build_model

model = build_model("v1", pretrained=True)   # variant string must match the run folder
model.load_weights(r"...\revision_experiments\results\v1_random_s42\best.weights.h5")
```

Traps:
- Do **not** rebuild from `CNN-LSTM_Yidi.py` (baseline architecture; won't match).
- Do **not** `load_model()` the legacy `.keras` files in `checkpoints\`/`Weights\` —
  those are ad-hoc runs unrelated to the paper's numbers.
- The variant string passed to `build_model` must match the weights' run folder
  (`v1_...` → `"v1"`); a mismatched variant may still load silently if architectures
  are identical across variants, so verify as below.

**Exact verification** (better than an MAE spot check): each run folder has
`predictions_val.npy` and `eval_indices.npy`. After loading, predict on the validation
samples selected by `eval_indices.npy` (see `evaluate_runs.py` for the indexing
convention) and confirm the output matches `predictions_val.npy` to float tolerance.
A match proves weights + architecture + preprocessing are all correct.

### Python environment for TensorFlow: **UNCONFIRMED**
No venv found in the flood repo. Locate with `pip list | findstr tensorflow` in the
user's environments, or build a fresh env pinned in this repo's `pyproject.toml` and
verify checkpoint loading there (Plan risk R1; scheduled in Slice 2).

---

## 3. SUMO network facts

From `norfolk_hampton.net.xml` line 43:
```
netOffset="-382501.15,-4082511.61"
convBoundary="0.00,0.00,3184.93,4120.70"        (net-local meters)
origBoundary="-76.335680,36.865173,-76.258949,36.918690"
projParameter="+proj=utm +zone=18 +ellps=WGS84 +datum=WGS84 +units=m +no_defs"
```
- Built by netconvert **1.26.0** from `sumo_norfolk\map.osm` with the default OSM typemap.
- The flood grid (§2) lies fully inside `origBoundary`.
- Coordinate conversion: use `sumolib.net.readNet(...).convertLonLat2XY(lon, lat)` —
  never hand-roll the UTM+offset math.
- Existing sim: 3600 s horizon, outputs already demonstrated (fcd, tripinfo, edgedata).

### District crop (Slice 1)
- Use `netconvert --sumo-net-file norfolk_hampton.net.xml --keep-edges.in-geo-boundary
  <W,S,E,N>` (geo-boundary variant takes lon/lat, avoiding offset math).
- Crop box: start from ~1 km buffer around the flood grid and expand until at least
  two parallel N–S corridors (Hampton Blvd AND Colley Ave) plus E–W connectors survive
  with intact connectivity. Record the final box in `data/net/crop.ps1` (Plan R3:
  the crop must be a script, never hand-edited).
- Cut existing routes with `%SUMO_HOME%\tools\route\cutRoutes.py`, or regenerate demand
  with `tools\randomTrips.py` on the cropped net (simpler; demand is placeholder per D6).

### Simulation settings that are decisions, not defaults
- Rerouting: `--device.rerouting.probability <fraction>` (the D5 sweep parameter),
  `--device.rerouting.period 120`.
- `--time-to-teleport -1` (disabled) or ≥ 600 s; teleport count is a run-health metric —
  any teleport flags the run invalid (Plan R4).
- Fix `--seed` per run; store it in the run artifact `config.json`.
- Closures via TraCI: `traci.edge.setDisallowed(edge, ["all"])` (or set allowed to
  emergency-only); slowdowns via `traci.edge.setMaxSpeed(...)`. Applying to the *edge*
  (not per-lane) is sufficient at this scale.

---

## 4. Depth → speed rule (D3, exact form)

Pregnolato et al. (2017), "The impact of flooding on road transport: A depth-disruption
function", *Transportation Research Part D* 55:67–81. Fitted curve:

```
v_safe(w) = 0.0009·w² − 0.5529·w + 86.9448     [v in km/h, w = water depth in mm]
valid for 0 ≤ w ≤ 300 mm; at w ≥ 300 mm the road is impassable (close the edge)
```
Applied per edge: `v_edge = min(v_speedlimit, v_safe(max_depth_on_edge))`, converted to
m/s for `setMaxSpeed`. ⚠️ Verify the coefficients against the paper before the first
research figure ships (memory-transcribed; the shape and 300 mm cutoff are standard).

⚠️ **Unit gap (Q2):** the curve takes depth in **mm of real water**, but the model
outputs **normalized units** with an unknown scale factor (§2). Implementation rule:
```
depth_mm = model_output * DEPTH_SCALE_M * 1000
```
where `DEPTH_SCALE_M` is a single named constant in one place, interim value `1.0`
(values are plausibly near-meters — ~0.65 max street flooding is realistic), marked
`# UNCONFIRMED — see IMPLEMENTATION_CONTEXT Q2`. Every figure produced while Q2 is
open carries an "depth units pending confirmation" caveat, and the Slice 8 sensitivity
table includes `DEPTH_SCALE_M` ∈ {0.5, 1.0, 2.0} until the constant is confirmed with
Yidi/Wang's data prep. Do not hard-code any real-world cutoff (e.g. 300 mm) directly
against raw model output.

Edge depth (D4): sample the depth grid at ~2.5 m intervals along each lane centerline
(`sumolib` shape → lon/lat → grid row/col via §2 formulas), take the **max** across
samples and lanes. Precompute the edge↔pixel index once per net; cache it.

---

## 5. Open questions for collaborators (blocking science, not code)

| # | Question | Status / Blocks | Interim assumption |
|---|---|---|---|
| Q1 | Which checkpoint to deploy? | **RESOLVED 2026-08-10**: `revision_experiments\results\v1_random_s42\best.weights.h5` — best street recall (0.85), the safety-critical metric for routing. `v4_random_s42` is the balanced alternative for the Slice 8 sensitivity check. Root `checkpoints\`/`Weights\` folders are ad-hoc, not paper-canonical. | — |
| Q2 | **Depth normalization constant** — outputs are normalized units (0 → ~0.65), scale factor set in Wang et al.'s upstream preprocessing, unrecoverable from this repo. Ask Yidi/Wang for the constant (and whether DEM and depth share one scale). | **Open — the only question now blocking real-unit results.** Slice 2 depth rule; see §4 unit gap | `DEPTH_SCALE_M = 1.0`, results caveated "units pending" |
| Q3 | Filename suffix meaning | **RESOLVED 2026-08-10**: sequential time index in hours, 0.25 = 15 min; two 150-frame events. See §2 Filenames. | — |
| Q4 | Input channel semantics | **RESOLVED 2026-08-10**: 11 channels — ch0 DEM (normalized), ch1–8 depth history (ch1 needs a second look before hand-assembling inputs), ch9 last depth, ch10 rain/tide block. See §2 table. | — |

Note: for Slices 1–4 none of the open questions block progress — precomputed
`Example Dataset\output\*.npy` files can stand in for model inference if needed.

---

## 6. Conventions for agents working in this repo

### Delivery workflow (required for every slice / unit of work)

1. **Issue first.** Before writing any implementation code, create a GitHub issue
   (`gh issue create`) scoping the work: which slice from PROJECT_PLAN.md, what will
   change, and the slice's demo criterion as the acceptance criterion. No issue, no code.
2. **Branch + implement.** Work on a branch named `<issue-number>-<short-slug>`
   (e.g. `12-slice1-district-crop`). Never commit directly to `main`.
3. **Pull request.** Open a PR (`gh pr create`) that references the issue
   (`Closes #<n>`), describes what was done, and includes evidence the slice's demo
   criterion is met (command output, metrics, screenshot of the demo).
4. **Orchestration review.** The orchestration agent reviews the PR against the
   issue's acceptance criterion and this file's conventions, then approves and merges.
   Implementing agents do **not** merge their own PRs.

Prerequisite: this repo currently has no GitHub remote. The first agent to start
implementation work must set one up (`gh repo create` + `git push -u origin main`)
— with the user's confirmation on repo name and visibility — before step 1 is possible.

### General

- Trust order: this file > PROJECT_PLAN.md > code comments in legacy repos > legacy hardcoded values.
- Sibling repos (`CNN-LSTM-Flood-Forecasting`, `sumo_norfolk`) are **read-only inputs**;
  copy what you need into `data/` with a provenance note, don't edit in place.
- Every run lands in `runs/<timestamp>_<label>/` with `config.json` capturing scenario,
  seed, rerouting fraction, closures, and code version. No figure without a run artifact.
- When an UNCONFIRMED fact gets confirmed, update this file in the same commit.
