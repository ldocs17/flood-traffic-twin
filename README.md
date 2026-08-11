# Flood-Traffic Digital Twin

[![CI](https://github.com/ldocs17/flood-traffic-twin/actions/workflows/ci.yml/badge.svg)](https://github.com/ldocs17/flood-traffic-twin/actions/workflows/ci.yml)

Couples a CNN-LSTM urban flood forecasting model with microscopic traffic simulation
(Eclipse SUMO) to answer two questions for a Norfolk, VA district: **how much does
forecast flooding disrupt traffic**, and **how much does traveler information
(rerouting) mitigate it**?

Storm forecast → per-street depth/speed → SUMO traffic simulation → research
figures and an interactive replay/run web app.

## Quick start

```bash
pip install -e ".[api,analysis,test]"
python -m uvicorn floodtwin.api.app:app --reload --port 8000
```

Open **http://localhost:8000/** — pick a storm scenario, set a rerouting percentage,
optionally click edges on the map to close them, hit **Run scenario**, and watch the
replay once it finishes. Two panels side by side make it easy to compare two runs
(e.g. different rerouting levels) directly.

That's the whole interactive loop. Everything below is detail for setup, the
command-line tools, and where to find things.

## Prerequisites

- **[Eclipse SUMO](https://sumo.dlr.de/)** installed locally, with `SUMO_HOME` set.
  `sumolib`/`traci` aren't pip packages — they're loaded from `%SUMO_HOME%\tools` at
  runtime.
- Two sibling repos checked out alongside this one (read-only inputs, never edited in
  place): `CNN-LSTM-Flood-Forecasting` and `sumo_norfolk`.
- Two Python environments (see [IMPLEMENTATION_CONTEXT.md](IMPLEMENTATION_CONTEXT.md)
  §2 for the exact recipe):
  - **Python 3.8** — the main environment for everything except flood-model inference:
    SUMO/TraCI, the coupling logic, the analysis tools, and the web API.
  - **Python 3.13 + Keras 3** — used only to run the flood forecasting model
    (`flood_pipeline.py` needs `keras.ops`, unavailable under the Python 3.8 env's
    older TF/Keras). The rest of the codebase never imports TensorFlow, and this
    interpreter is only invoked automatically, on a cache miss, when a scenario's
    forecast hasn't been generated yet.

## Install

From the repo root, in the Python 3.8 environment:

```bash
pip install -e ".[api,analysis,test]"
```

Extras, install only what you need:

| Extra | Adds | Needed for |
|---|---|---|
| `api` | FastAPI, uvicorn, Pillow | the web app |
| `analysis` | matplotlib | research figures (`--metrics`, sweeps, sensitivity table) |
| `test` | pytest | running the unit test suite |
| `flood` | tensorflow | only inside the separate Python 3.13 interpreter, never here |

## Running it

### The web app

```bash
python -m uvicorn floodtwin.api.app:app --reload --port 8000
```

Then open `http://localhost:8000/`. From the browser you can:

- Pick a storm scenario, rerouting %, seed, and click-to-toggle manual edge closures
  (the "close this bridge" intervention feature)
- Submit a run — it executes the real SUMO + flood-coupling pipeline in the
  background and auto-loads the replay when done
- Scrub through simulated time, watch the flood raster and vehicle positions animate,
  see edges colored by state (open / slowed / closed)
- Run two configurations side by side to compare them directly

### Command line — a single research run

```bash
python -m floodtwin.sim.runner --scenario Sep_30_2022_74.75 --seed 42 --metrics
```

Common flags: `--rerouting-fraction 0.5`, `--demand calibrated_v2` (real
VDOT-calibrated demand instead of the illustrative default), `--manual-closures
<edge_id,...>`. Writes a timestamped run artifact under `runs/` (config, SUMO
outputs, edge-state table), plus `metrics.json` and a figure when `--metrics` is
passed.

### Command line — the headline sweep / sensitivity table

```bash
# Rerouting-fraction sweep (0/25/50/75/100% x 3 seeds) -- "how much does
# traveler information mitigate flood disruption?"
python -m floodtwin.analysis.sweep --scenario Sep_30_2022_74.75 --demand calibrated_v2

# Sensitivity/robustness table over closure threshold, edge-depth aggregation,
# rerouting period, seed, depth-scale, and flood-model checkpoint
python -m floodtwin.analysis.sensitivity --scenario Sep_30_2022_74.75
```

### Tests

```bash
pytest tests/
```

Pure Python, no SUMO or TensorFlow required — this is what CI runs. Full pipeline
runs (`floodtwin.sim.runner` and everything built on it) need the sibling repos and a
local SUMO install, so they're out of CI's scope; see `.github/workflows/ci.yml`.

## Project layout

```
data/           cropped SUMO network, demand/route files, storm scenario cache
src/floodtwin/  flood model runner, coupling logic, SUMO orchestration, analysis, API
web/            the MapLibre GL frontend (no build step)
runs/           generated run artifacts (gitignored)
tests/          pure-Python unit tests (no SUMO/TF needed)
```

## More detail

- **[PROJECT_PLAN.md](PROJECT_PLAN.md)** — the research plan: fixed decisions,
  architecture, and the vertical slices this was built in.
- **[IMPLEMENTATION_CONTEXT.md](IMPLEMENTATION_CONTEXT.md)** — verified paths, data
  contracts, known gotchas, and open questions. Read this before touching the flood
  model or coupling code.
- **[PROGRESS.md](PROGRESS.md)** — what's done, with verification notes for each
  slice.
- **[SENSITIVITY.md](SENSITIVITY.md)** — how robust the headline findings are to
  modeling assumptions (closure threshold, depth scale, seed, etc.) — useful context
  before citing any specific number from this project.
