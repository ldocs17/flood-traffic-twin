"""Slice 1 demo artifact: a folium HTML map comparing a baseline run against
a flooded run (closed edges + a snapshot of vehicle positions from each run's
FCD output), so the detour effect is inspectable without a GUI session.

Usage (from the repo root, with SUMO_HOME set / defaulted):
    python -m floodtwin.analysis.demo_map <baseline_run_dir> <flooded_run_dir> [--time 1500] [--out path.html]
"""
from __future__ import annotations

import argparse
import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple

import folium

from floodtwin.sumo_env import ensure_sumo_tools_on_path

ensure_sumo_tools_on_path()
import sumolib  # noqa: E402

from floodtwin.coupling import georef
from floodtwin.sim import paths


def _closest_timestep(fcd_path: Path, target_time: float) -> ET.Element:
    """FCD files can be large; iterate rather than building a full DOM."""
    best = None
    best_diff = None
    for _, elem in ET.iterparse(fcd_path, events=("end",)):
        if elem.tag != "timestep":
            continue
        t = float(elem.get("time"))
        diff = abs(t - target_time)
        if best_diff is None or diff < best_diff:
            best, best_diff = copy.deepcopy(elem), diff
        elem.clear()
        if t > target_time + 1:
            break
    return best


def _vehicle_lonlats(timestep_elem: ET.Element, net) -> List[Tuple[str, float, float, float]]:
    out = []
    for v in timestep_elem.findall("vehicle"):
        x, y = float(v.get("x")), float(v.get("y"))
        lon, lat = net.convertXY2LonLat(x, y)
        speed = float(v.get("speed"))
        out.append((v.get("id"), lon, lat, speed))
    return out


def build_comparison_map(
    baseline_run_dir: Path,
    flooded_run_dir: Path,
    snapshot_time_s: float,
    out_html: Path,
) -> Path:
    net = sumolib.net.readNet(str(paths.NET_FILE))
    flooded_cfg = json.load(open(flooded_run_dir / "config.json"))
    closed_edges = set(flooded_cfg["closures"])
    closure_time_s = flooded_cfg["closure_time_s"]

    base_ts = _closest_timestep(baseline_run_dir / "fcd.xml", snapshot_time_s)
    flood_ts = _closest_timestep(flooded_run_dir / "fcd.xml", snapshot_time_s)
    base_actual_t = float(base_ts.get("time"))
    flood_actual_t = float(flood_ts.get("time"))

    base_vehicles = _vehicle_lonlats(base_ts, net)
    flood_vehicles = _vehicle_lonlats(flood_ts, net)

    center = [(georef.NORTH + georef.SOUTH) / 2, (georef.WEST + georef.EAST) / 2]
    m = folium.Map(location=center, zoom_start=16, tiles="CartoDB positron")

    # District road network for context (thin grey).
    roads_group = folium.FeatureGroup(name="District roads", show=True)
    for edge in net.getEdges():
        if edge.getID() in closed_edges:
            continue
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y)[::-1] for x, y in shape]
        folium.PolyLine(coords, color="#999999", weight=1, opacity=0.5).add_to(roads_group)
    roads_group.add_to(m)

    # Closed edges (flood closures), highlighted.
    closed_group = folium.FeatureGroup(name=f"Closed edges (depth >= 30cm @ t=15min, n={len(closed_edges)})", show=True)
    for edge in net.getEdges():
        if edge.getID() not in closed_edges:
            continue
        shape = edge.getShape()
        coords = [net.convertXY2LonLat(x, y)[::-1] for x, y in shape]
        folium.PolyLine(
            coords, color="#d62728", weight=5, opacity=0.9,
            tooltip=f"CLOSED: {edge.getID()}",
        ).add_to(closed_group)
    closed_group.add_to(m)

    # Flood grid boundary.
    boundary_group = folium.FeatureGroup(name="Flood model domain (320m x 320m)", show=True)
    folium.Rectangle(
        bounds=[[georef.SOUTH, georef.WEST], [georef.NORTH, georef.EAST]],
        color="#ff00ff", weight=2, fill=False, dash_array="5,5",
        tooltip="CNN-LSTM flood model domain",
    ).add_to(boundary_group)
    boundary_group.add_to(m)

    # Vehicle snapshots.
    base_group = folium.FeatureGroup(
        name=f"Baseline vehicles @ t={base_actual_t:.0f}s (n={len(base_vehicles)})", show=True
    )
    for vid, lon, lat, speed in base_vehicles:
        folium.CircleMarker(
            location=[lat, lon], radius=4, color="#1f77b4", fill=True,
            fill_color="#1f77b4", fill_opacity=0.85, weight=1,
            tooltip=f"baseline vehicle {vid}, speed={speed:.1f} m/s",
        ).add_to(base_group)
    base_group.add_to(m)

    flood_group = folium.FeatureGroup(
        name=f"Flooded vehicles @ t={flood_actual_t:.0f}s (n={len(flood_vehicles)})", show=True
    )
    for vid, lon, lat, speed in flood_vehicles:
        folium.CircleMarker(
            location=[lat, lon], radius=4, color="#ff7f0e", fill=True,
            fill_color="#ff7f0e", fill_opacity=0.85, weight=1,
            tooltip=f"flooded vehicle {vid}, speed={speed:.1f} m/s",
        ).add_to(flood_group)
    flood_group.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    legend_html = f"""
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 1000;
         background-color: #1a1a2e; padding: 12px 16px; border-radius: 8px;
         border: 1px solid #444; font-family: Arial; color: white; font-size: 12px;">
      <b>Slice 1 demo: baseline vs flooded</b><br>
      <small style="color:#aaa;">closures applied at t={closure_time_s:.0f}s (15 min)</small><br><br>
      <span style="color:#d62728;">&#9644;&#9644;</span> Closed edges (depth &ge; 30cm)<br>
      <span style="color:#1f77b4;">&#11044;</span> Baseline vehicle position<br>
      <span style="color:#ff7f0e;">&#11044;</span> Flooded vehicle position<br>
      <span style="color:#ff00ff;">- -</span> Flood model domain<br>
      <span style="color:#999999;">&#9644;</span> District road network
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    out_html.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_html))
    return out_html


def main():
    parser = argparse.ArgumentParser(description="Build the Slice 1 baseline-vs-flooded demo map.")
    parser.add_argument("baseline_run_dir", type=Path)
    parser.add_argument("flooded_run_dir", type=Path)
    parser.add_argument("--time", type=float, default=1500.0, help="snapshot time in sim seconds")
    parser.add_argument("--out", type=Path, default=paths.RUNS_DIR / "demo_baseline_vs_flooded.html")
    args = parser.parse_args()

    out = build_comparison_map(args.baseline_run_dir, args.flooded_run_dir, args.time, args.out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
