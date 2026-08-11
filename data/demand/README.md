# data/demand/ — route files for the cropped district

Two demand variants exist, selectable via `--demand {v1,calibrated_v2}` on
`floodtwin.sim.runner`/`floodtwin.analysis.sweep`'s CLIs (or the
`demand_variant=` parameter on their Python entry points — see
`floodtwin.sim.paths.DEMAND_VARIANTS`; the route file was always meant to be
swappable-by-design, PROJECT_PLAN.md D6):

- **`district_routes.xml`** (this directory, described below) — the
  original `randomTrips`-based placeholder demand, internally called `v1`.
  **Still labeled *illustrative*** (D6) — it was never calibrated against
  traffic counts, and that hasn't changed.
- **`calibrated_v2/district_routes.xml`** (Slice 7, SG4) — real VDOT
  traffic-count-calibrated demand for the Hampton Blvd/Colley Ave
  corridors, fit with `routeSampler`. **This is the variant that sheds the
  D6 *illustrative* label** — see `calibrated_v2/README.md` for its own
  provenance, fit quality, and the important scope caveat that it is
  corridor-focused, not full-district, demand.

## `district_routes.xml` (`v1`, illustrative)

`district_routes.xml` is `sumo_norfolk/norfolk_routes.xml` (read-only sibling input)
cut to `data/net/district.net.xml` with SUMO's `tools/route/cutRoutes.py`:

```
python cutRoutes.py district.net.xml norfolk_routes.xml ^
    --orig-net norfolk_hampton.net.xml ^
    -o district_routes.xml ^
    --disconnected-action keep -v
```

Source `norfolk_routes.xml` provenance (per IMPLEMENTATION_CONTEXT.md, unconfirmed but
visible in its own header comment): `randomTrips.py` (period=2.0s) + `duarouter` on the
full `norfolk_hampton.net.xml`, 1800 vehicles over a 3600 s horizon.

Result: **1426 vehicles kept** (1800 parsed, 41 routes were disconnected in the
subnetwork and split/kept via `--disconnected-action keep`, 0 broken). Departure times
range ~8 s to ~3697 s — matches the original ~1-hour horizon, unchanged by the cut
(`cutRoutes.py` preserves original depart times here since no `--orig-net`-based
extrapolation was needed... actually `--orig-net` *was* passed for edge-length lookups;
depart times come from the original route file directly, no `exit-times` present so no
extrapolation occurred).

D6 (PROJECT_PLAN.md): this demand is **placeholder/illustrative** — it was not
calibrated against traffic counts, and stays that way; it remains available (as `v1`)
for before/after comparison against `calibrated_v2` rather than being deleted.
Calibration lives in Slice 7 (`routeSampler` against VDOT counts) — see
`calibrated_v2/README.md`.
