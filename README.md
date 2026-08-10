# Flood-Traffic Digital Twin

[![CI](https://github.com/ldocs17/flood-traffic-twin/actions/workflows/ci.yml/badge.svg)](https://github.com/ldocs17/flood-traffic-twin/actions/workflows/ci.yml)

Couples a CNN-LSTM urban flood forecasting model with microscopic traffic simulation
(Eclipse SUMO) to quantify how forecast flooding disrupts traffic in a Norfolk, VA
district, and how much traveler information (rerouting) mitigates it.

- **Plan and decisions:** [PROJECT_PLAN.md](PROJECT_PLAN.md)
- **Verified paths, data contracts, gotchas:** [IMPLEMENTATION_CONTEXT.md](IMPLEMENTATION_CONTEXT.md)
- **Vertical slice status:** [PROGRESS.md](PROGRESS.md)

## Setup

This project spans two Python environments:

- **Python 3.8** (SUMO/TraCI + coupling logic) — the primary environment. `sumolib`/
  `traci` are not pip packages; they load from `%SUMO_HOME%\tools` at runtime
  (see `src/floodtwin/sumo_env.py`). Requires a local [Eclipse SUMO](https://sumo.dlr.de/)
  install with `SUMO_HOME` set.
- **Python 3.13 with Keras 3** (flood model inference only) — `flood_pipeline.py` in
  the sibling `CNN-LSTM-Flood-Forecasting` repo needs `keras.ops`, unavailable under
  TF 2.13/Keras 2.13. `src/floodtwin/flood/` shells out to this interpreter and caches
  results to disk; the rest of the codebase never imports TensorFlow.

```bash
pip install -e ".[test]"
pytest tests/
```

The unit tests (`tests/`) are pure-Python and require neither SUMO nor TensorFlow.
Full pipeline runs (`floodtwin.sim.runner`) require the sibling repos and a local SUMO
install per `IMPLEMENTATION_CONTEXT.md` — they are not part of CI.
