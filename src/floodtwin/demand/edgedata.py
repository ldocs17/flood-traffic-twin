"""Build a ``routeSampler``-compatible edgeData count input: a ``<meandata>``
XML file with one ``<interval>`` covering the sim horizon and one ``<edge
id="..." entered="N"/>`` per edge with a real matched VDOT count.

``routeSampler.py``'s default ``--edgedata-attribute`` is ``entered``
(``tools/routeSampler.py`` -- verified against the installed SUMO 1.26.0),
so that's the attribute this writes. Only edges with an actual count are
written: an edge absent from the file is unconstrained for routeSampler
(free), which is exactly how this slice honestly represents corridor
segments/edges with no VDOT coverage (never a fabricated 0 or guessed
value -- PROJECT_PLAN.md Slice 7 data-integrity requirement).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict


def build_edgedata_xml(
    edge_counts: Dict[str, float],
    begin_s: float = 0.0,
    end_s: float = 3600.0,
    interval_id: str = "calibrated_v2",
) -> ET.Element:
    """Build the ``<meandata>`` root element. Counts are rounded to the
    nearest non-negative integer (vehicle counts are discrete;
    ``routeSampler`` samples an integer number of vehicles per route)."""
    root = ET.Element("meandata")
    interval = ET.SubElement(
        root, "interval", {"id": interval_id, "begin": str(begin_s), "end": str(end_s)}
    )
    for edge_id in sorted(edge_counts):
        count = max(0, round(edge_counts[edge_id]))
        ET.SubElement(interval, "edge", {"id": edge_id, "entered": str(count)})
    return root


def write_edgedata_xml(
    edge_counts: Dict[str, float],
    out_path: "Path | str",
    begin_s: float = 0.0,
    end_s: float = 3600.0,
    interval_id: str = "calibrated_v2",
) -> Path:
    root = build_edgedata_xml(edge_counts, begin_s, end_s, interval_id)
    ET.indent(root, space="    ") if hasattr(ET, "indent") else None
    tree = ET.ElementTree(root)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return out_path
