"""SUMO tooling is not pip-installed in this environment (Python 3.8.3):
`sumolib` and `traci` live under ``%SUMO_HOME%\\tools`` and must be added to
``sys.path`` before they can be imported. Call :func:`ensure_sumo_tools_on_path`
once, early, before importing ``sumolib``/``traci`` anywhere in this repo.
"""
from __future__ import annotations

import os
import sys

DEFAULT_SUMO_HOME = r"C:\Program Files (x86)\Eclipse\Sumo"


def get_sumo_home() -> str:
    return os.environ.get("SUMO_HOME", DEFAULT_SUMO_HOME)


def ensure_sumo_tools_on_path() -> str:
    """Add ``%SUMO_HOME%\\tools`` to ``sys.path`` (idempotent). Returns the
    tools directory that was added."""
    tools = os.path.join(get_sumo_home(), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return tools


def sumo_binary(name: str = "sumo") -> str:
    """Absolute path to a SUMO executable, e.g. ``sumo``, ``sumo-gui``,
    ``netconvert``."""
    exe = name if name.endswith(".exe") else name + ".exe"
    return os.path.join(get_sumo_home(), "bin", exe)
