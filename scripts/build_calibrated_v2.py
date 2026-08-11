"""Slice 7 (PROJECT_PLAN.md SG4 "Calibrated demand") orchestration: turns the
saved VDOT counts (``data/demand/vdot_counts/raw_query_district.geojson``)
into ``data/demand/calibrated_v2/district_routes.xml`` via ``routeSampler``.

Needs a real SUMO install (``sumolib``, ``randomTrips.py``,
``routeSampler.py`` -- run under the repo's usual Python 3.8 interpreter,
same as ``floodtwin.sim.runner``). Not exercised by ``pytest`` -- the
pure logic it calls (VDOT parsing, AADT->peak-hour conversion, edge
matching, edgeData XML) lives in ``src/floodtwin/demand/`` and is unit
tested there (``tests/test_demand.py``); this script is the SUMO-dependent
glue, verified manually (see the Slice 7 PR for command output).

Pipeline
--------
1. Load the saved VDOT GeoJSON, parse+dedupe into calibration segments
   (:mod:`floodtwin.demand.vdot`).
2. Restrict to each corridor's own net edges via
   ``sumo_norfolk/road_segments.json`` (:func:`corridor_edge_ids`), then
   match each VDOT segment's own polyline against that restricted set
   (:func:`match_segment_to_edges`), keep edges hit by >=2 sample points
   (``min_hits``, drops single-touch proximity noise), then keep only the
   *modal* speed among survivors (:func:`filter_to_modal_speed`, drops
   turn-lane/connector artifacts -- see that function's docstring for why
   this step exists: some VDOT count segments are longer than the whole
   district, so the raw candidate set is the entire corridor, including
   non-through-lane edges).
3. Build the routeSampler edgeData count input
   (:mod:`floodtwin.demand.edgedata`) from the matched edges' peak-hour
   volumes (AADT * K_FACTOR, split 50/50 across matched edges as a
   documented directional-split assumption -- see PROVENANCE.md).
4. Generate a candidate route pool with ``randomTrips.py -r`` (dense
   random OD pairs + duarouter, run internally by randomTrips) **plus**
   explicit long "through" candidates spanning each matched corridor chain
   end-to-end (:func:`generate_through_route_candidates`). The first
   end-to-end run without these (see the Slice 7 PR description) needed
   13,308 vehicles to satisfy the 47 edge counts because randomTrips' random
   OD pairs rarely traverse a whole 39-edge corridor in one trip -- most
   candidate routes only clip a handful of matched edges before turning off,
   so routeSampler had to stack many redundant partial-coverage vehicles to
   hit each block's target. Explicit through-candidates (real duarouter
   routes between the matched chain's own start/end nodes) let one vehicle
   satisfy many edges' counts at once, which is what a real corridor commute
   actually looks like.
5. Run ``routeSampler.py`` against the candidate pool + counts to produce
   ``district_routes.xml``.
6. Write ``data/demand/calibrated_v2/README.md`` + a machine-readable
   ``matching_report.json`` documenting exactly which edges got which
   count and why.

Run from the repo root with the Python 3.8 interpreter that has SUMO_HOME
reachable:

    python scripts/build_calibrated_v2.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from floodtwin.sumo_env import ensure_sumo_tools_on_path, sumo_binary  # noqa: E402

ensure_sumo_tools_on_path()
import sumolib  # noqa: E402

from floodtwin.demand.edge_matching import (  # noqa: E402
    corridor_edge_ids,
    filter_to_modal_speed,
    match_segment_to_edges,
)
from floodtwin.demand.edgedata import write_edgedata_xml  # noqa: E402
from floodtwin.demand.vdot import load_calibration_segments  # noqa: E402
from floodtwin.sim import paths as sim_paths  # noqa: E402

NET_FILE = sim_paths.NET_FILE
RAW_GEOJSON = REPO_ROOT / "data" / "demand" / "vdot_counts" / "raw_query_district.geojson"
ROAD_SEGMENTS_JSON = Path(r"C:\Users\dcost\ChandraMentorship\sumo_norfolk\road_segments.json")

OUT_DIR = REPO_ROOT / "data" / "demand" / "calibrated_v2"
EDGEDATA_XML = OUT_DIR / "edgedata_counts.xml"
CANDIDATE_ROUTES_XML = OUT_DIR / "candidate_routes.xml"
CANDIDATE_TRIPS_XML = OUT_DIR / "candidate_trips.xml"
THROUGH_TRIPS_XML = OUT_DIR / "through_trips.xml"
THROUGH_ROUTES_XML = OUT_DIR / "through_routes.xml"
OUTPUT_ROUTES_XML = OUT_DIR / "district_routes.xml"
MATCHING_REPORT_JSON = OUT_DIR / "matching_report.json"
ROUTESAMPLER_MISMATCH_XML = OUT_DIR / "routesampler_mismatch.xml"
ROUTESAMPLER_LOG_TXT = OUT_DIR / "routesampler_log.txt"

CORRIDOR_ROAD_SEGMENTS_KEY = {
    "hampton_blvd": "Hampton Boulevard",
    "colley_ave": "Colley Avenue",
}

CORRIDOR_TOLERANCE_M = 20.0  # corridor_edge_ids candidate proximity
SEGMENT_MATCH_TOLERANCE_M = 25.0  # match_segment_to_edges (Slice 1's tolerance)
MIN_HITS = 2  # drop single-touch proximity noise before the modal-speed filter

SIM_BEGIN_S = 0.0
SIM_END_S = float(sim_paths.SIM_END_S)

RANDOMTRIPS_PERIOD_S = 0.4  # dense candidate pool (~9000 trips over the horizon)
RANDOMTRIPS_SEED = 7
ROUTESAMPLER_SEED = 7


def build_matched_counts(net):
    """Returns ``(edge_counts, edges_by_corridor, report)`` -- ``edge_counts``
    is ``{edge_id: peak_hour_count}`` ready for
    :func:`floodtwin.demand.edgedata.write_edgedata_xml`; ``edges_by_corridor``
    is ``{corridor: set(edge_id)}`` (all final matched edges across every
    segment on that corridor, for :func:`generate_through_route_candidates`);
    ``report`` is a JSON-serializable dict of every decision made, for
    ``matching_report.json``."""
    geojson = json.loads(RAW_GEOJSON.read_text())
    segments, excluded = load_calibration_segments(geojson)
    road_segments = json.loads(ROAD_SEGMENTS_JSON.read_text())

    corridor_candidates: Dict[str, Set[str]] = {
        corridor: corridor_edge_ids(net, road_segments[key], tolerance_m=CORRIDOR_TOLERANCE_M)
        for corridor, key in CORRIDOR_ROAD_SEGMENTS_KEY.items()
    }

    edge_counts: Dict[str, float] = {}
    edges_by_corridor: Dict[str, Set[str]] = {c: set() for c in CORRIDOR_ROAD_SEGMENTS_KEY}
    per_segment_report = []
    for seg in segments:
        candidates = corridor_candidates.get(seg.corridor, set())
        hits = match_segment_to_edges(net, seg, tolerance_m=SEGMENT_MATCH_TOLERANCE_M, candidate_edge_ids=candidates)
        strong = {eid for eid, n in hits.items() if n >= MIN_HITS}
        final_edges = filter_to_modal_speed(net, strong)

        peak_hour = seg.peak_hour_volume()
        per_edge_count = None
        if final_edges and peak_hour is not None:
            # 50/50 directional split (documented assumption -- see
            # PROVENANCE.md: VDOT's DIRECTION_FACTOR field could not be
            # reliably mapped to a specific physical direction from the
            # feature service metadata alone). Applied uniformly to every
            # matched edge, which under a true 50/50 split is exactly
            # correct regardless of which direction each edge happens to
            # be (flow conservation along a corridor with no major
            # in-district diversions).
            per_edge_count = peak_hour / 2.0
            for eid in final_edges:
                edge_counts[eid] = per_edge_count
            edges_by_corridor[seg.corridor].update(final_edges)

        per_segment_report.append(
            {
                "corridor": seg.corridor,
                "start_label": seg.start_label,
                "end_label": seg.end_label,
                "adt": seg.adt,
                "adt_quality": seg.adt_quality,
                "k_factor": seg.k_factor,
                "peak_hour_volume_bidirectional": peak_hour,
                "source_objectids": seg.source_objectids,
                "route_common_names": seg.route_common_names,
                "n_candidate_edges_in_corridor": len(candidates),
                "n_raw_hits": len(hits),
                "n_after_min_hits_filter": len(strong),
                "n_final_matched_edges": len(final_edges),
                "per_edge_entered_count": per_edge_count,
                "final_matched_edge_ids": sorted(final_edges),
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "net_file": str(NET_FILE),
        "raw_geojson": str(RAW_GEOJSON),
        "road_segments_json": str(ROAD_SEGMENTS_JSON),
        "corridor_tolerance_m": CORRIDOR_TOLERANCE_M,
        "segment_match_tolerance_m": SEGMENT_MATCH_TOLERANCE_M,
        "min_hits": MIN_HITS,
        "n_calibration_segments": len(segments),
        "n_excluded_vdot_records": len(excluded),
        "excluded_reasons": sorted({e["reason"] for e in excluded}),
        "n_corridor_candidate_edges": {c: len(ids) for c, ids in corridor_candidates.items()},
        "n_total_edges_in_net": len(net.getEdges()),
        "n_edges_with_real_count": len(edge_counts),
        "per_segment": per_segment_report,
    }
    return edge_counts, edges_by_corridor, report


def generate_candidate_routes() -> Path:
    """Dense random-trips candidate pool for routeSampler to draw from
    (PROJECT_PLAN.md Slice 7 step 4): randomTrips.py with a short period
    (lots of candidate OD pairs) directly produces a route file via
    duarouter (``-r``)."""
    randomtrips_py = str(Path(sumo_binary("sumo")).parent.parent / "tools" / "randomTrips.py")
    cmd = [
        sys.executable, randomtrips_py,
        "-n", str(NET_FILE),
        "-o", str(CANDIDATE_TRIPS_XML),
        "-r", str(CANDIDATE_ROUTES_XML),
        "-b", str(int(SIM_BEGIN_S)),
        "-e", str(int(SIM_END_S)),
        "-p", str(RANDOMTRIPS_PERIOD_S),
        "--fringe-factor", "10",
        "--validate",
        "--seed", str(RANDOMTRIPS_SEED),
        "--random-depart",
    ]
    print("Generating candidate route pool:\n  " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(NET_FILE.parent))
    (OUT_DIR / "randomtrips_stdout.log").write_text(result.stdout)
    (OUT_DIR / "randomtrips_stderr.log").write_text(result.stderr)
    if result.returncode != 0 or not CANDIDATE_ROUTES_XML.exists():
        raise RuntimeError(
            f"randomTrips.py failed (exit {result.returncode}); see "
            f"{OUT_DIR / 'randomtrips_stderr.log'}"
        )
    print(f"  -> {CANDIDATE_ROUTES_XML}")
    return CANDIDATE_ROUTES_XML


def chain_endpoint_edges(net, edge_ids):
    """Edges at the topological start/end of a matched corridor chain: an
    edge is a "start" if no other matched edge ends at its from-node, and
    an "end" if no other matched edge starts at its to-node. Used to pick
    realistic origin/destination edges for explicit through-route
    candidates (real corridors have a handful of these, not one -- the
    matched set can include short branches/merges near intersections)."""
    from_nodes = {net.getEdge(e).getFromNode().getID() for e in edge_ids}
    to_nodes = {net.getEdge(e).getToNode().getID() for e in edge_ids}
    starts = [e for e in edge_ids if net.getEdge(e).getFromNode().getID() not in to_nodes]
    ends = [e for e in edge_ids if net.getEdge(e).getToNode().getID() not in from_nodes]
    return starts, ends


def generate_through_route_candidates(net, edge_counts_by_corridor, n_repeats: int = 150) -> Path:
    """Explicit long "through" candidates spanning each matched corridor
    chain end-to-end (see module docstring for why this matters --
    without it, routeSampler needs an unrealistically large vehicle count
    to satisfy per-edge targets one fragment at a time). Writes trips
    between every (start edge, end edge) pair found by
    :func:`chain_endpoint_edges`, in both directions, at ``n_repeats``
    staggered departure times each; routes them with ``duarouter`` on the
    full district net (so the resulting path is whatever a real driver
    would actually take, not artificially constrained to only the matched
    edges); unroutable pairs are silently skipped (``--ignore-errors``)."""
    trip_lines = ["<routes>"]
    n = 0
    for corridor, edge_ids in edge_counts_by_corridor.items():
        if not edge_ids:
            continue
        starts, ends = chain_endpoint_edges(net, edge_ids)
        for s in starts:
            for e in ends:
                if s == e:
                    continue
                for fr, to, tag in ((s, e, "fwd"), (e, s, "rev")):
                    for i in range(n_repeats):
                        depart = round(i * SIM_END_S / n_repeats, 2)
                        trip_lines.append(
                            f'  <trip id="through_{corridor}_{tag}_{s}_{e}_{i}" '
                            f'depart="{depart}" from="{fr}" to="{to}"/>'
                        )
                        n += 1
    trip_lines.append("</routes>")
    THROUGH_TRIPS_XML.write_text("\n".join(trip_lines))
    print(f"  wrote {n} through-trip candidates -> {THROUGH_TRIPS_XML}")

    duarouter = sumo_binary("duarouter")
    cmd = [
        duarouter, "-n", str(NET_FILE), "--route-files", str(THROUGH_TRIPS_XML),
        "-o", str(THROUGH_ROUTES_XML), "--ignore-errors", "--no-warnings",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    (OUT_DIR / "duarouter_through_stdout.log").write_text(result.stdout)
    (OUT_DIR / "duarouter_through_stderr.log").write_text(result.stderr)
    if result.returncode != 0 or not THROUGH_ROUTES_XML.exists():
        raise RuntimeError(
            f"duarouter (through-routes) failed (exit {result.returncode}); see "
            f"{OUT_DIR / 'duarouter_through_stderr.log'}"
        )
    print(f"  -> {THROUGH_ROUTES_XML}")
    return THROUGH_ROUTES_XML


def run_route_sampler(route_files) -> str:
    """Run routeSampler.py against the candidate pool(s) + edgedata counts.
    Returns its captured stdout (contains the GEH/fit-quality summary).
    ``route_files`` is an iterable of paths; routeSampler's ``-r`` accepts a
    comma-separated list."""
    routesampler_py = str(Path(sumo_binary("sumo")).parent.parent / "tools" / "routeSampler.py")
    cmd = [
        sys.executable, routesampler_py,
        "-r", ",".join(str(p) for p in route_files),
        "-d", str(EDGEDATA_XML),
        "-o", str(OUTPUT_ROUTES_XML),
        "--mismatch-output", str(ROUTESAMPLER_MISMATCH_XML),
        "-b", str(int(SIM_BEGIN_S)),
        "-e", str(int(SIM_END_S)),
        "--seed", str(ROUTESAMPLER_SEED),
        "-v",
        "--geh-ok", "5",
        # --optimize full runs routeSampler's LP-based optimizer (HiGHS),
        # which actually uses --minimize-vehicles's objective (without
        # --optimize, routeSampler falls back to a greedy incremental
        # sampler that ignores it entirely -- confirmed empirically: adding
        # --minimize-vehicles alone, without --optimize, produced a bit-for-
        # bit identical output to not having it). This matters a lot in
        # practice here: the greedy sampler needed 13,137 vehicles to
        # satisfy the 47 edge counts (many redundant partial-coverage
        # fragments -- see generate_through_route_candidates()'s docstring),
        # and running that many vehicles through the district in one SUMO
        # hour badly oversaturated the network (only 15% arrived, 42% never
        # even got inserted by sim end -- still 0 teleports/collisions, but
        # not a usable run for travel-time metrics). --optimize full finds
        # the minimum-vehicle route mix that still satisfies the same
        # counts: 3,852 vehicles, 100.00% of the target count matched,
        # GEH<5.0 for 100.00% of locations, and the resulting SUMO run
        # arrives normally (82% arrived, 0 teleports/collisions -- see the
        # Slice 7 PR for the before/after run-health numbers).
        "--optimize", "full",
        "--minimize-vehicles", "1",
    ]
    print("\nRunning routeSampler.py:\n  " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    log = result.stdout + "\n" + result.stderr
    ROUTESAMPLER_LOG_TXT.write_text(log)
    if result.returncode != 0:
        raise RuntimeError(f"routeSampler.py failed (exit {result.returncode}); see {ROUTESAMPLER_LOG_TXT}")
    print(log)
    return log


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    net = sumolib.net.readNet(str(NET_FILE))

    print("Step 1-2: matching VDOT segments to district.net.xml edges ...")
    edge_counts, edges_by_corridor, report = build_matched_counts(net)
    print(f"  {report['n_edges_with_real_count']} edges matched a real VDOT count "
          f"(of {report['n_total_edges_in_net']} total district edges)")
    MATCHING_REPORT_JSON.write_text(json.dumps(report, indent=2, default=str))
    print(f"  -> {MATCHING_REPORT_JSON}")

    print("\nStep 3: writing routeSampler edgeData count input ...")
    write_edgedata_xml(edge_counts, EDGEDATA_XML, begin_s=SIM_BEGIN_S, end_s=SIM_END_S)
    print(f"  -> {EDGEDATA_XML}")

    print("\nStep 4a: generating random candidate route pool ...")
    generate_candidate_routes()

    print("\nStep 4b: generating explicit through-route candidates ...")
    generate_through_route_candidates(net, edges_by_corridor)

    print("\nStep 5: running routeSampler ...")
    routesampler_log = run_route_sampler([CANDIDATE_ROUTES_XML, THROUGH_ROUTES_XML])

    print(f"\nDone. Calibrated route file: {OUTPUT_ROUTES_XML}")
    return {
        "edge_counts": edge_counts,
        "report": report,
        "routesampler_log": routesampler_log,
    }


if __name__ == "__main__":
    main()
