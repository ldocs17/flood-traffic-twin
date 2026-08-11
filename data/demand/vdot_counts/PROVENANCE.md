# data/demand/vdot_counts/ -- VDOT traffic count provenance

Source of the real traffic counts behind `data/demand/calibrated_v2/district_routes.xml`
(PROJECT_PLAN.md Slice 7, SG4 "Calibrated demand"). Every number that ends up
in `calibrated_v2` traces back to a record in `raw_query_district.geojson`,
fetched below -- nothing in this pipeline invents, interpolates, or guesses
a traffic count.

## Data source

VDOT's public ArcGIS Online Feature Service, **"VDOT Bidirectional Traffic
Volume 2022"** (`contentStatus: public_authoritative`, VDOT-owned).

- `FeatureServer` root:
  `https://services.arcgis.com/p5v98VHDX9Atv3l7/arcgis/rest/services/VDOTTrafficVolume/FeatureServer`
- Layer 0 (`Traffic Volume ADT`, polylines), queried at
  `.../FeatureServer/0/query`. No auth required.

## Exact query used

Fetched by `fetch_vdot_counts.py` in this directory (stdlib `urllib` only,
no new dependency; re-run it to refresh `raw_query_district.geojson`):

```
GET https://services.arcgis.com/p5v98VHDX9Atv3l7/arcgis/rest/services/VDOTTrafficVolume/FeatureServer/0/query
    ?geometry=-76.32,36.87,-76.28,36.92
    &geometryType=esriGeometryEnvelope
    &inSR=4326
    &spatialRel=esriSpatialRelIntersects
    &outFields=*
    &returnGeometry=true
    &f=geojson
```

The envelope (`WEST,SOUTH,EAST,NORTH`, WGS84) covers `data/net/README.md`'s
district crop box (`-76.3125,36.8840,-76.2925,36.9060`) with a small margin,
so every VDOT segment that could plausibly touch a district edge is
included.

**Fetched:** 2026-08-10 (UTC timestamp recorded per-fetch in
`raw_query_district.geojson`'s `_floodtwin_fetch_meta` key).

**Result:** 88 features in the envelope, across ~30 named roads. This slice
only calibrates against **Hampton Blvd** and **Colley Ave**
(PROJECT_PLAN.md Slice 7's explicit scope) -- every other named road in the
envelope is excluded (`floodtwin.demand.vdot.canonical_corridor` returns
`None` for them; see "Exclusions" below).

## Corridor identification

- **Colley Ave** appears directly as `ROUTE_COMMON_NAME` starting
  `"Colley AVE"`.
- **Hampton Blvd** is filed under its state route number, `VA-337E`/
  `VA-337W`, in this dataset -- confirmed two ways: (1) adjoining VDOT
  segments' `START_LABEL`/`END_LABEL` cross-street fields literally read
  "SR 337 Hampton Blvd"-style text on the wider VDOT network; (2)
  independently re-verified here by comparing every in-district `VA-337*`
  vertex's coordinates against `sumo_norfolk/road_segments.json`'s
  digitized "Hampton Boulevard" points -- mean nearest-point distance
  ~23.5 m (n=177 in-box points), well inside the 25 m tolerance this repo
  already uses for corridor verification (`data/net/README.md`, Slice 1).
  The equivalent check for Colley Ave vs. `road_segments.json`'s "Colley
  Avenue" gives ~71.7 m mean (n=14) -- higher because `road_segments.json`'s
  digitized Colley Avenue only covers part of the VDOT segments' extent, not
  because of a corridor mismatch (see `scripts/build_calibrated_v2.py`'s
  matching, which uses `road_segments.json` only to build the *candidate*
  edge set, not to accept/reject the VDOT identification).

## ADT_QUALITY: which records were kept

`ADT_QUALITY` codes observed in this dataset and their VDOT meaning (as
briefed for this slice):

| Code | Meaning | Used? |
|---|---|---|
| `A` | Average of Complete Continuous Data | Yes -- highest confidence |
| `G` | Corrected Factored Short Term Traffic Count Data | Yes -- high confidence |
| `N` | AADT of Similar Neighboring Traffic Link (an estimate borrowed from a nearby road, not a real count on this segment) | **No** -- would violate the "never fabricate/guess a count" requirement for this slice |

`floodtwin.demand.vdot.DEFAULT_ACCEPTED_QUALITY_CODES = ("A", "G")` encodes
this. Every Hampton Blvd/Colley Ave record actually returned by the query
happens to be `A` or `G` -- no `N`-quality (or other) record was excluded on
quality grounds for these two corridors specifically (see
`matching_report.json`'s `excluded_reasons`, which for this fetch is only
`"not a Hampton Blvd / Colley Ave corridor record"` -- the other ~30 named
roads in the envelope, out of scope for this slice).

## Deduplication: one physical segment, several VDOT rows

VDOT's linear referencing system stores the same physical count multiple
times: once per direction-of-travel route entry (`VA-337E` and `VA-337W`
report identical `ADT`/`K_FACTOR` for the same cross-street bounds), and
Colley Ave additionally carries `(NP - City of Norfolk)`/
`(PR - City of Norfolk)` classification duplicates. `floodtwin.demand.vdot.dedupe_segments`
merges these (grouped by corridor + unordered `{start_label, end_label}`)
into one count per physical segment, keeping every duplicate's geometry
(different LRS entries can trace slightly different carriageway lines) so
edge-matching can use all of it. Real dedup on this fetch: **20 raw
Hampton/Colley records -> 9 deduped calibration segments**, with zero
`ADT`/quality disagreement across any duplicate group (verified: every
segment's `adt_conflict` list is empty).

## The 9 calibration segments

| Corridor | From | To | ADT | Quality | K_FACTOR | Peak-hour (bidirectional) |
|---|---|---|---:|---|---:|---:|
| Colley Ave | 27th St | 52rd Street | 12,000 | G | 0.0898 | 1,077.6 |
| Colley Ave | 21st Street | 27th Street | 6,100 | G | 0.0983 | 599.6 |
| Colley Ave | Princess Anne Rd | 21st Street | 11,000 | G | 0.0840 | 924.0 |
| Hampton Blvd | SR 406 Terminal Blvd | Admiral Taussig Blvd | 24,000 | G | 0.0927 | 2,224.8 |
| Hampton Blvd | US 58 Brambleton Ave | 21st Street | 29,000 | G | 0.0801 | 2,322.9 |
| Hampton Blvd | SR 247, 26th St | 49th St, ODU | 24,000 | G | 0.0812 | 1,948.8 |
| Hampton Blvd | 21st Street | SR 247, 26th St | 32,000 | G | 0.0812 | 2,598.4 |
| Hampton Blvd | 49th St, ODU | SR 165 Little Creek Rd | 29,000 | **A** | 0.1003 | 2,908.7 |
| Hampton Blvd | SR 165 Little Creek Rd | SR 406 Terminal Blvd | 30,000 | G | 0.0776 | 2,328.0 |

Peak-hour bidirectional volume = `ADT * K_FACTOR` (`floodtwin.demand.vdot.peak_hour_volume`):
`K_FACTOR` is VDOT's standard fraction of AADT occurring in the design/peak
hour, needed because SUMO's sim horizon here is a single 3600 s window
(IMPLEMENTATION_CONTEXT.md), not a full day.

## What actually falls inside the district crop -- and what doesn't

VDOT's count segments are bounded by *major* cross streets kilometers apart
(state routes, not every block), while the district crop
(`data/net/README.md`) is only ~1.78 km x ~2.44 km. Checking each segment's
real geometry against `data/net/district.net.xml`
(`scripts/build_calibrated_v2.py`), only **2 of the 9** segments actually
overlap the district:

- **Hampton Blvd, "49th St, ODU" -> "SR 165 Little Creek Rd"** (ADT 29,000,
  quality **A**, peak-hour 2,908.7) -- this single VDOT segment's
  cross-street bounds are wide enough that the *entire* district-crop
  stretch of Hampton Blvd falls inside it. That's expected, not a bug: VDOT
  doesn't count every block, and this district happens to sit entirely
  within one representative count segment for Hampton Blvd.
- **Colley Ave, "27th St" -> "52rd Street"** (ADT 12,000, quality G,
  peak-hour 1,077.6) -- same situation for the Colley Ave stretch actually
  present in the crop.

The other 7 segments (the rest of Hampton Blvd north/south of the district,
and the rest of Colley Ave) describe real VDOT counts on real road, just not
on any edge inside `data/net/district.net.xml` -- they contribute nothing to
`calibrated_v2` and are recorded in `matching_report.json` for completeness,
not silently dropped.

## Edge matching methodology (see `scripts/build_calibrated_v2.py` for the
executable version)

Because the 2 in-scope VDOT segments are each wider than the whole
district, the naive "any district edge inside this VDOT segment's bounds"
match is the *entire* named corridor -- including turn lanes, short
junction-internal connectors, and (at a 20-25 m tolerance in this dense
grid) some cross-street edges. Three filters narrow this to the real
through-carriageway edges:

1. **Corridor restriction** (`corridor_edge_ids`): candidate edges are
   first restricted to those within 20 m of `sumo_norfolk/road_segments.json`'s
   own digitized Hampton Blvd/Colley Ave points, **and** whose own shape
   bearing is within 25 degrees of the corridor's overall direction (mod
   180 degrees, since a corridor has edges running both ways). The bearing
   check specifically rejects near-perpendicular cross-street edges that
   happen to sit right next to a corridor sample point at a shared
   intersection -- proximity alone can't tell those apart, but direction
   can.
2. **Hit-count threshold** (`min_hits=2`): within that restricted
   candidate set, an edge must be within 25 m of at least 2 of the VDOT
   segment's own digitized vertices (not just 1) to survive -- drops
   single-touch proximity noise.
3. **Modal-speed filter** (`filter_to_modal_speed`): of the survivors, keep
   only those at the single most common posted speed. Turn
   lanes/connectors reliably carry a different (usually much lower) speed
   than the arterial's own through lanes -- e.g. Hampton Blvd's matched set
   before this filter mixed 13.4 m/s (41 edges, the dominant/arterial
   speed) with 2.8, 5.6, 15.7, and 27.8 m/s edges; after the filter, only
   the 13.4 m/s edges remain.

Result: **39 Hampton Blvd edges** and **8 Colley Ave edges** (47 total, out
of 948 district edges) get a real VDOT-derived count. Both matched sets form
physically continuous chains of adjacent net nodes (spot-checked manually --
see the PR description), consistent with genuinely being the corridor's own
through-carriageway rather than an artifact of the matching tolerance.

The remaining 901 district edges (including the parts of Hampton
Blvd/Colley Ave whose one in-scope VDOT segment's matching didn't survive
the filters above, and every other street in the district) are left
**unconstrained** for `routeSampler` -- never assigned a fabricated count.
`routeSampler` supports partial counts by design; this is the honest,
partial-calibration outcome the task description explicitly allows for.

## Directional split: a documented assumption, not a guess

Each VDOT segment's `ADT` is already bidirectional (both directions
combined). `DIRECTION_FACTOR` exists in the schema but, on inspection,
reports the *same* value on both the `E` and `W` (or `NP`/`PR`) duplicate
rows for a given physical segment in this dataset -- i.e. it does not
reliably indicate which physical direction gets the larger share from the
feature service response alone (see `floodtwin.demand.vdot`'s docstring).
Rather than guess which compass direction is "the" `D`-weighted direction,
`scripts/build_calibrated_v2.py` applies a conservative **50/50 split**:
every matched edge (regardless of which physical direction it happens to
represent) is assigned `peak_hour_volume / 2` as its `routeSampler` target
count. Under a true 50/50 split this is exactly correct by flow
conservation along a corridor with no major in-district diversions,
regardless of which specific edge IDs belong to which direction thread.

- Hampton Blvd: 2,908.7 / 2 = **1,454.35** -> rounds to 1,454 vehicles/hour
  per matched edge (39 edges).
- Colley Ave: 1,077.6 / 2 = **538.8** -> rounds to 539 vehicles/hour per
  matched edge (8 edges).

## Files in this directory

- `fetch_vdot_counts.py` -- re-runs the exact query above.
- `raw_query_district.geojson` -- the raw fetch (committed; also the
  fixture `tests/test_demand.py` parses for its "real data" tests, so CI
  never needs network access).

See `data/demand/calibrated_v2/README.md` for the routeSampler run itself
(candidate pool generation, fit quality/GEH, final route file) and
`data/demand/calibrated_v2/matching_report.json` for the full per-segment
matching decisions in machine-readable form.
