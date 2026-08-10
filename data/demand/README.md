# data/demand/ — route file for the cropped district

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
calibrated against traffic counts. Calibration is deferred to Slice 7 (`routeSampler`
against VDOT counts).
