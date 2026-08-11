# data/demand/calibrated_v2/ — VDOT-calibrated route file

`district_routes.xml` in this directory (Slice 7, PROJECT_PLAN.md SG4
"Calibrated demand") is produced by `routeSampler` fit against **real VDOT
Hampton Blvd/Colley Ave traffic counts**, not `randomTrips` scaled to a
plausible volume. This is the demand variant that **sheds the D6
"illustrative" label** — see `data/demand/README.md` for how to select it
(`--demand calibrated_v2`) and how it relates to `v1` (kept, still
illustrative, for comparison).

Full traffic-count provenance (query URL, fetch date, quality-code
inclusion rules, AADT→peak-hour conversion) is in
[`data/demand/vdot_counts/PROVENANCE.md`](../vdot_counts/PROVENANCE.md) —
this file covers the `routeSampler` run itself.

## Regenerate

```
python scripts/build_calibrated_v2.py
```

(from the repo root, with the usual Python 3.8 / SUMO_HOME interpreter).
Never hand-edit `district_routes.xml` — the script is the only source.

The script (see its module docstring for the full pipeline) does, in order:

1. Loads `data/demand/vdot_counts/raw_query_district.geojson`, parses +
   dedupes into 9 real calibration segments
   (`floodtwin.demand.vdot`), 2 of which actually overlap
   `data/net/district.net.xml` (see PROVENANCE.md — VDOT's count segments
   are bounded by major cross streets kilometers apart; the district crop
   happens to sit entirely inside one representative count segment per
   corridor).
2. Matches those 2 segments to district net edges in three stages
   (`floodtwin.demand.edge_matching`): corridor restriction via
   `sumo_norfolk/road_segments.json` + a bearing filter, a
   `min_hits=2` proximity threshold, then a modal-speed filter that drops
   turn-lane/connector artifacts. Result: **47 of 948 district edges**
   (39 Hampton Blvd, 8 Colley Ave) get a real VDOT-derived count; the other
   901 are left unconstrained for `routeSampler` (never a fabricated
   count).
3. Writes those 47 edges' 50/50-split peak-hour counts (539 veh/h on
   Colley Ave's 8 edges, 1,454 veh/h on Hampton Blvd's 39 edges — see
   `edgedata_counts.xml`, and PROVENANCE.md for the directional-split
   reasoning) as a `routeSampler` edgeData input, one 3600 s interval
   matching `norfolk.sumocfg`'s / `floodtwin.sim.paths.SIM_END_S`'s
   horizon.
4. Generates a candidate route pool for `routeSampler` to sample from:
   `randomTrips.py -p 0.4 --fringe-factor 10` (dense random OD pairs,
   ~9,250 candidates) **plus** explicit long "through" candidates spanning
   each matched corridor chain end-to-end (`duarouter`-routed between the
   chain's own topological start/end edges, ~1,200 more candidates) — see
   "Why explicit through-routes" below for why the random pool alone wasn't
   enough.
5. Runs `routeSampler.py --optimize full --minimize-vehicles 1` against
   the combined candidate pool + counts to produce `district_routes.xml`.

## Fit quality

```
Loaded 10450 routes (8251 distinct)
Ignored 3978 routes which do not pass any counting location
Starting optimization for interval [0.0, 3700.0] (mismatch 61018)
Optimization succeeded
Wrote 3807 routes (8 distinct) achieving total count 61020 (100.00%) at 47 locations. GEH<5.0 for 100.00%
Warning: overflow locations: count 2, min -1.00 (('1420544882#1',)), max -1.00 (('1420544882#1',)), mean -1.00, Q1 -1.00, median -1.00, Q3 -1.00 (total -2)
```

(full log: `routesampler_log.txt`). **GEH < 5.0 at 100.00% of the 47
counted locations** — GEH is the standard traffic-engineering
goodness-of-fit statistic for comparing a modeled count to an observed
count; GEH < 5 is the conventional "good fit" threshold, and every one of
this run's 47 real VDOT-derived targets clears it (the 2 "overflow"
locations are off by exactly 1 vehicle out of a ~1,454 target — noise, not
a fit problem). **3,807 vehicles, 8 distinct routes.**

Only 47/948 district edges (5.0%) carry a real VDOT-derived count; the
other 95.0% are unconstrained — `calibrated_v2` is a **partial, honestly
scoped calibration**, not a claim that every street's traffic is
individually verified. See PROVENANCE.md for exactly which segments were
in scope and why.

## Why explicit through-routes (`generate_through_route_candidates`)

The first end-to-end attempt used only the `randomTrips` candidate pool
(no `--optimize`, no through-routes) and needed **13,137 vehicles** to
satisfy the same 47 counts — random OD pairs rarely traverse a whole
39-edge corridor in one trip, so most candidates only clipped a handful of
matched edges before turning off, and the (default, greedy) sampler had to
stack many redundant partial-coverage vehicles to hit each block's target
one fragment at a time. Run in SUMO, that produced 0 teleports/collisions
but severe under-saturation of *arrivals*: only ~15% of vehicles had
arrived by the end of the 3600 s window, and 42% hadn't even been inserted
— technically "healthy" per PROJECT_PLAN.md R4 (no teleports) but useless
for travel-time metrics.

Two changes fixed it, verified together (not independently — the second
made the first effective, see the `--optimize` note below): explicit
through-route candidates (real `duarouter` routes between each matched
corridor chain's own topological start/end edges — `chain_endpoint_edges`
+ `generate_through_route_candidates` in `scripts/build_calibrated_v2.py`)
give the optimizer genuine single-vehicle-covers-many-edges options: a real
corridor commute, not a fragment. Combined with `routeSampler --optimize
full` (which runs an LP solver, HiGHS, that actually honors
`--minimize-vehicles`; without `--optimize`, that flag is silently a no-op
— confirmed empirically, see the module docstring in
`scripts/build_calibrated_v2.py`), the result dropped to **3,807 vehicles**
at **100.00% GEH-pass** fit quality, and the resulting SUMO run is healthy:
82% arrived by sim end, 0 teleports/collisions (see the Slice 7 PR
description for the full run-health JSON).

## Important scope caveat: corridor-focused, not full-district, demand

`routeSampler` only outputs candidate routes that touch **at least one**
counted location — of the 10,450 candidates loaded, 3,978 that never
touched any of the 47 calibrated edges were dropped entirely. That means
**`calibrated_v2` contains only demand that uses Hampton Blvd/Colley Ave**
(directly or via a route that happens to pass through them); it does not
add any background/local traffic on the district's other streets, unlike
`v1` (which spreads ~1,426 arbitrary-volume vehicles across the whole
network with no particular relationship to the two corridors).

This is a direct, honest consequence of only having real VDOT counts for
two corridors — inventing background counts elsewhere would violate the
data-integrity requirement this slice was built under. It also means a
`calibrated_v2` vs. `v1` comparison is not apples-to-apples in total
vehicle count or spatial coverage: `calibrated_v2`'s ~3,807 vehicles are
real, VDOT-measured corridor volume; `v1`'s ~1,426 are an arbitrary
whole-district placeholder that happens to touch the corridors only
incidentally. The Slice 7 PR's calibrated-vs-random comparison reports both
the difference in scale and the resulting metrics honestly rather than
normalizing it away.

## Files in this directory

- `district_routes.xml` — the calibrated route file (regenerate via the
  script above; never hand-edit).
- `edgedata_counts.xml` — the real VDOT-derived count input `routeSampler`
  was fit against (47 edges, values from PROVENANCE.md's peak-hour
  numbers).
- `matching_report.json` — every VDOT segment → net edge matching decision
  in machine-readable form (candidate counts at each filter stage, per-edge
  entered counts, which edges were kept/dropped and why).
- `routesampler_log.txt` — the fit-quality log quoted above.

Large regeneratable intermediates (`randomTrips`/`duarouter` candidate
pools) are not committed — re-run `scripts/build_calibrated_v2.py` to
recreate them; the seeds are fixed (`RANDOMTRIPS_SEED`/`ROUTESAMPLER_SEED
= 7`) so a re-run reproduces the same result.
