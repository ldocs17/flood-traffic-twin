"""FCD (Floating Car Data) XML -> frontend-friendly JSON.

Design choice (documented in the Slice 5 PR): a full run's ``fcd.xml`` has
one ``<timestep>`` per simulation second (SUMO's default ``--fcd-output``
period), e.g. 3700 timesteps x ~85 vehicles/timestep on average for the
Slice 2/4 flooded_multiframe runs (~46 MB of XML, ~311k <vehicle> records).
Shipping that raw to a browser (or re-parsing XML client-side) is wasteful:
a time scrubber doesn't need per-second resolution to look smooth, and JSON
numbers are far more compact than XML attribute soup.

So: pre-parse server-side with ``xml.etree.ElementTree.iterparse`` (streams
the file rather than building a full DOM -- needed given the file size) and
**decimate temporally** with a ``stride_s`` parameter (default 5s -- an
input, not a hardcoded constant, so the frontend/caller can trade resolution
for payload size). Vehicle rows are emitted as compact ``[id, lon, lat,
speed]`` arrays rather than objects (no repeated key names) to shrink the
JSON further.

Coordinate conversion (SUMO net-local x/y -> lon/lat) is injected as a
``convert`` callable rather than hardcoded to a ``sumolib`` call, so this
module's temporal/parsing logic is unit-testable without ``sumolib``/
SUMO_HOME (Slice 5 scope discipline) -- ``floodtwin.api.network`` supplies
the real converter (``net.convertXY2LonLat``) at the FastAPI route.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

DEFAULT_STRIDE_S = 5.0

Converter = Callable[[float, float], Tuple[float, float]]


def _identity(x: float, y: float) -> Tuple[float, float]:
    return x, y


def iter_fcd_timesteps(fcd_path: Path) -> Iterator[Tuple[float, List[Tuple[str, float, float, float]]]]:
    """Stream ``(time, [(id, x, y, speed), ...])`` per ``<timestep>``,
    without ever holding the full DOM in memory."""
    for _, elem in ET.iterparse(str(fcd_path), events=("end",)):
        if elem.tag != "timestep":
            continue
        t = float(elem.get("time"))
        vehicles = []
        for v in elem.findall("vehicle"):
            vid = v.get("id")
            x = float(v.get("x"))
            y = float(v.get("y"))
            speed = float(v.get("speed", 0.0))
            vehicles.append((vid, x, y, speed))
        yield t, vehicles
        elem.clear()


def parse_fcd_frames(
    fcd_path: Path,
    stride_s: float = DEFAULT_STRIDE_S,
    convert: Optional[Converter] = None,
    round_ndigits: int = 6,
) -> Dict:
    """Pre-parse an fcd.xml into a compact JSON-ready structure:

        {
          "stride_s": 5.0,
          "frames": [
            {"t": 0.0, "v": [["veh0", -76.3021, 36.8972, 4.5], ...]},
            ...
          ]
        }

    Only timesteps at (approximately) ``stride_s`` intervals are kept --
    the first timestep is always kept, then every subsequent one whose time
    has advanced by >= ``stride_s`` since the last kept frame. ``stride_s
    <= 0`` keeps every timestep (no decimation).
    """
    convert = convert or _identity
    frames = []
    next_keep_t = None
    for t, vehicles in iter_fcd_timesteps(fcd_path):
        if stride_s > 0:
            if next_keep_t is not None and t < next_keep_t:
                continue
            next_keep_t = t + stride_s
        row = []
        for vid, x, y, speed in vehicles:
            lon, lat = convert(x, y)
            row.append([vid, round(lon, round_ndigits), round(lat, round_ndigits), round(speed, 2)])
        frames.append({"t": t, "v": row})
    return {"stride_s": stride_s, "frames": frames}
