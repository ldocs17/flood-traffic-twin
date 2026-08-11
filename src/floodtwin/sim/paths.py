"""Repo-relative paths for Slice 1's district net / demand / scenario inputs
and the runs/ artifact directory."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

NET_FILE = REPO_ROOT / "data" / "net" / "district.net.xml"

# Slice 7 (PROJECT_PLAN.md D6/SG4): the route file was always meant to be
# swappable-by-design ("swappable route file by design; calibrate later
# with routeSampler against VDOT counts"). DEMAND_VARIANTS is the
# swap point -- ``v1`` is the original randomTrips-based placeholder
# demand (data/demand/README.md, labeled *illustrative* per D6); ``calibrated_v2``
# is the VDOT-routeSampler-calibrated demand from this slice
# (data/demand/calibrated_v2/README.md), which sheds that label.
DEMAND_VARIANTS = {
    "v1": REPO_ROOT / "data" / "demand" / "district_routes.xml",
    "calibrated_v2": REPO_ROOT / "data" / "demand" / "calibrated_v2" / "district_routes.xml",
}
DEFAULT_DEMAND_VARIANT = "v1"  # preserves Slices 1-6 behavior for existing callers
ROUTE_FILE = DEMAND_VARIANTS[DEFAULT_DEMAND_VARIANT]  # back-compat alias

DEFAULT_SCENARIO = REPO_ROOT / "data" / "scenarios" / "Sep_30_2022_74.75_output.npy"
RUNS_DIR = REPO_ROOT / "runs"

# Matches the demand's original horizon (sumo_norfolk/norfolk.sumocfg used
# 0-3600s; the cut routes have a few departures past 3600s -- see
# data/demand/README.md), rounded up.
SIM_END_S = 3700


def route_file_for_demand(demand_variant: str = DEFAULT_DEMAND_VARIANT) -> Path:
    """Resolve a demand variant name (see ``DEMAND_VARIANTS``) to its route
    file path. Raises ``ValueError`` (not a bare ``KeyError``) naming the
    valid choices, since this is reachable from CLI args / the sweep
    module, not just internal code."""
    try:
        return DEMAND_VARIANTS[demand_variant]
    except KeyError:
        raise ValueError(
            f"unknown demand variant {demand_variant!r}; choose one of {sorted(DEMAND_VARIANTS)}"
        )
