"""Slice 5: FastAPI backend serving completed run artifacts for web replay
(PROJECT_PLAN.md SG3 / Slice 5).

Module split (see IMPLEMENTATION_CONTEXT.md conventions + Slice 5 scope
discipline -- CI must not need SUMO_HOME to *collect* the test suite):

- ``runs.py``, ``fcd.py``, ``edge_states.py``, ``flood_raster.py`` are pure
  Python (stdlib + numpy/PIL only) and safe to import/unit-test without
  ``sumolib``/SUMO_HOME.
- ``network.py`` needs ``sumolib`` (via ``floodtwin.sumo_env``) to parse
  ``district.net.xml`` into GeoJSON -- isolated here so nothing in
  ``tests/`` has to import it at module scope.
- ``app.py`` wires everything into the FastAPI app and is the only module
  that imports ``fastapi``/``uvicorn`` (the optional ``api`` extra).
"""
