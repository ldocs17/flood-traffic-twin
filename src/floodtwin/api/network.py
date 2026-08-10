"""Road network geometry -> GeoJSON, for the frontend to draw the district
net without shipping raw SUMO net XML to the browser.

Needs ``sumolib`` (via ``floodtwin.sumo_env.ensure_sumo_tools_on_path``),
so this module is deliberately kept out of anything ``tests/`` imports at
module scope (Slice 5 scope discipline: CI has no SUMO_HOME) -- only
``floodtwin.api.app`` (the FastAPI route wiring) imports this, and only
when the server actually starts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from floodtwin.sumo_env import ensure_sumo_tools_on_path

ensure_sumo_tools_on_path()
import sumolib  # noqa: E402

# In-process cache: the district net (~950 edges) doesn't change between
# requests within one server run, and parsing + GeoJSON-building it is not
# free (sumolib XML parse + per-edge coordinate conversion). Keyed by
# resolved net file path so a hypothetical future multi-net setup wouldn't
# silently serve a stale net.
_geojson_cache: Dict[str, dict] = {}
_net_cache: Dict[str, "sumolib.net.Net"] = {}


def load_net(net_file: Path):
    key = str(Path(net_file).resolve())
    net = _net_cache.get(key)
    if net is None:
        net = sumolib.net.readNet(str(net_file))
        _net_cache[key] = net
    return net


def net_to_geojson(net) -> dict:
    """SUMO net -> GeoJSON ``FeatureCollection`` of ``LineString`` features,
    one per edge, in [lon, lat] order (GeoJSON's required axis order --
    note this is the *opposite* of the [lat, lon] folium/Leaflet convention
    used elsewhere in this repo's demo maps, e.g. ``analysis/demo_map.py``).
    Internal (":"-prefixed) junction edges are skipped -- they're not real
    drivable road segments and would just clutter the map."""
    features = []
    for edge in net.getEdges():
        if edge.getID().startswith(":"):
            continue
        shape = edge.getShape()
        if len(shape) < 2:
            continue
        coords = [list(net.convertXY2LonLat(x, y)) for x, y in shape]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "id": edge.getID(),
                    "speed_limit_ms": edge.getSpeed(),
                    "from": edge.getFromNode().getID(),
                    "to": edge.getToNode().getID(),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def load_network_geojson(net_file: Path, force_reload: bool = False) -> dict:
    key = str(Path(net_file).resolve())
    if force_reload or key not in _geojson_cache:
        net = load_net(net_file)
        _geojson_cache[key] = net_to_geojson(net)
    return _geojson_cache[key]


def speed_limits_ms(net_file: Path) -> Dict[str, float]:
    net = load_net(net_file)
    return {e.getID(): e.getSpeed() for e in net.getEdges() if not e.getID().startswith(":")}


def lonlat_converter(net_file: Path):
    """Return a ``(x, y) -> (lon, lat)`` callable bound to this net, for
    ``floodtwin.api.fcd.parse_fcd_frames``'s injectable ``convert`` param."""
    net = load_net(net_file)

    def convert(x: float, y: float):
        return net.convertXY2LonLat(x, y)

    return convert
