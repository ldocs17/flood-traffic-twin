"""Slice 7 (PROJECT_PLAN.md SG4): calibrated demand.

Turns real VDOT traffic-count records into a ``routeSampler`` count input
and helps match named VDOT road segments to ``data/net/district.net.xml``
edges. Pure/testable logic lives in :mod:`floodtwin.demand.vdot` (count
parsing, AADT -> peak-hour conversion), :mod:`floodtwin.demand.edge_matching`
(segment -> edge matching, duck-typed against a net-like object so it needs
no real ``sumolib`` install to unit test), and
:mod:`floodtwin.demand.edgedata` (routeSampler edgeData XML generation).
Orchestration (fetch, run ``randomTrips``/``routeSampler``) lives in
``scripts/build_calibrated_v2.py``, which does need a real SUMO install and
is verified manually, not by ``pytest``.
"""
