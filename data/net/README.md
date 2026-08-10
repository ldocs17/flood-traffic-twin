# data/net/ — cropped district network

`district.net.xml` is produced by `crop.ps1`, which crops
`C:\Users\dcost\ChandraMentorship\sumo_norfolk\norfolk_hampton.net.xml`
(read-only sibling input, never edited in place) with:

```
netconvert --sumo-net-file norfolk_hampton.net.xml ^
    --keep-edges.in-geo-boundary -76.3125,36.8840,-76.2925,36.9060 ^
    --keep-edges.components 1 ^
    --remove-edges.isolated ^
    --output-file district.net.xml
```

Crop box (WEST,SOUTH,EAST,NORTH): `-76.3125,36.8840,-76.2925,36.9060`
(~1.78 km E-W x ~2.44 km N-S).

Regenerate with `powershell -ExecutionPolicy Bypass -File .\crop.ps1` — never hand-edit
`district.net.xml`.

## Why this box

- Fully contains the flood model's georeferenced grid (IMPLEMENTATION_CONTEXT.md #2):
  `N=36.898650 S=36.895770 W=-76.304447 E=-76.300846`.
- Retains two parallel N-S corridors: **Hampton Blvd** and **Colley Ave** (identified
  via `sumo_norfolk/road_segments.json`, since the net itself carries no `name`
  attributes on edges — the default OSM typemap used to build it doesn't emit them).
- Retains an E-W connector tying them together: **Jamestown Crescent** (and Magnolia
  Ave crosses both longitudes as a second connector).
- `--keep-edges.components 1` keeps only the largest weakly connected component, so
  the crop can't leave behind disconnected fragments (PROJECT_PLAN.md R3).

## Verification performed

Loaded `district.net.xml` with `sumolib.net.readNet` and checked, for sampled points
along each named road from `road_segments.json`, whether a net edge exists within 25 m
(`net.getNeighboringEdges`):

- Colley Avenue: 24/24 sample points matched.
- Jamestown Crescent: 46/46 sample points matched.
- Hampton Boulevard: 21/21 sample points *that fall inside the crop box* matched (the
  full road extends well north of the district and those out-of-box points correctly
  found nothing — Hampton Blvd continues past the crop boundary, which is expected for
  a district-scale crop).

Net stats after crop: 948 edges, 401 nodes (vs. the full-city source net).
