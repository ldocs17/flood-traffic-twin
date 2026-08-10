"""Repo-relative paths for Slice 1's district net / demand / scenario inputs
and the runs/ artifact directory."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

NET_FILE = REPO_ROOT / "data" / "net" / "district.net.xml"
ROUTE_FILE = REPO_ROOT / "data" / "demand" / "district_routes.xml"
DEFAULT_SCENARIO = REPO_ROOT / "data" / "scenarios" / "Sep_30_2022_74.75_output.npy"
RUNS_DIR = REPO_ROOT / "runs"

# Matches the demand's original horizon (sumo_norfolk/norfolk.sumocfg used
# 0-3600s; the cut routes have a few departures past 3600s -- see
# data/demand/README.md), rounded up.
SIM_END_S = 3700
