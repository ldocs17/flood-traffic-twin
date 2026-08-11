"""Fetch VDOT's public "Bidirectional Traffic Volume 2022" feature service
for the district crop box and save the raw response.

Slice 7 (PROJECT_PLAN.md SG4): source of the traffic counts used to
calibrate ``data/demand/calibrated_v2/district_routes.xml``. Stdlib-only
(``urllib``) so it needs no new dependency -- this is a one-shot fetch
script, not code any run-time path imports.

Regenerate with (from the repo root)::

    python data/demand/vdot_counts/fetch_vdot_counts.py

which overwrites ``raw_query_district.geojson`` with a fresh pull of the
same query. See ``PROVENANCE.md`` in this directory for the exact query
used, fetch date, and how the response was turned into calibration counts.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FEATURE_SERVICE_QUERY_URL = (
    "https://services.arcgis.com/p5v98VHDX9Atv3l7/arcgis/rest/services/"
    "VDOTTrafficVolume/FeatureServer/0/query"
)

# Envelope (WEST,SOUTH,EAST,NORTH, WGS84) covering data/net/README.md's
# district crop box (-76.3125,36.8840,-76.2925,36.9060) with a small margin,
# so any VDOT segment that could plausibly touch a district edge is
# included. Same envelope the orchestrator used to confirm coverage before
# scoping this slice.
DISTRICT_ENVELOPE = "-76.32,36.87,-76.28,36.92"

QUERY_PARAMS = {
    "geometry": DISTRICT_ENVELOPE,
    "geometryType": "esriGeometryEnvelope",
    "inSR": "4326",
    "spatialRel": "esriSpatialRelIntersects",
    "outFields": "*",
    "returnGeometry": "true",
    "f": "geojson",
}

OUT_PATH = Path(__file__).resolve().parent / "raw_query_district.geojson"


def fetch() -> dict:
    url = FEATURE_SERVICE_QUERY_URL + "?" + urllib.parse.urlencode(QUERY_PARAMS)
    with urllib.request.urlopen(url, timeout=60) as resp:
        body = resp.read()
    data = json.loads(body)
    data["_floodtwin_fetch_meta"] = {
        "query_url": url,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return data


def main() -> None:
    data = fetch()
    OUT_PATH.write_text(json.dumps(data, indent=2))
    n = len(data.get("features", []))
    print(f"Wrote {n} features to {OUT_PATH}")


if __name__ == "__main__":
    main()
