"""What the cut does with a seam that the hover settled ONTO a fitted edge line.

Case A (debug_point -1071.33 355.58 along): the along chord was slid 3.69 mm onto a
'panel 1 cut-out edge' and re-trimmed to a 7.7 mm stub at (-821.48,375.36)-(-813.80,375.85).
Case C (debug_point 2489.05 313.25 across): the across chord was slid 17.06 mm onto
'panel 1 outer edge' at the bow tip, chord (2514.88,177.38)-(2522.67,56.42).
Cut panel 1 with each line and measure how far the pieces' edges end up from the
original cut-out edge / outline the seam lies on.  Pure engine, no run loaded.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN, dump

for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"):
    sys.path.insert(0, p)
from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

config = load_config()
options = sheets.settings(config)
frame, _w = sheetjob.resolve_frame(RUN, options)
axis = np.asarray(frame.axis, float)
along, across = seamsnap.master_directions(axis)
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))[0]
polygons = seamplace.panel_polygons(loops, options)
step = float(options["sample_step_mm"])
outer, holes = sheets.classify_loops(loops[1], step)
panel = polygons[1]
Seam = sheets.Seam

cases = {
    "A_cutout_edge_along": dict(p=(-821.48, 375.36), q=(-813.80, 375.85), unit=along),
    "C_outer_edge_across": dict(p=(2514.88, 177.38), q=(2522.67, 56.42), unit=across),
}
out = {}
for name, c in cases.items():
    p = np.array(c["p"]); unit = c["unit"]
    # the full set of chords on this line, and the seam as the page would store the previewed one
    chords = seamplace.seam_through(p, unit, polygons, options, panel_ids=[1])
    stub = min(chords, key=lambda s: abs(s["along_mm"]))
    seam = Seam("edge", stub["x1"], stub["y1"], stub["x2"], stub["y2"], panel_id=1, mode="along" if unit is along else "across")
    pieces, warnings = sheets.split_panel(1, outer, holes, [seam], options)
    material = unary_union([pc.polygon(step) for pc in pieces])
    # which fitted edge does the line lie on?  distance from the line to every straight reference
    refs = seamsnap.references_from_loops({1: loops[1]}, options)
    line = LineString([p - unit * 5000, p + unit * 5000])
    on = [r for r in refs if r.kind == "line" and line.distance(Point(r.p0)) < 0.05 and line.distance(Point(r.p1)) < 0.05]
    labels = sorted({(r.label, round(r.length_mm, 1)) for r in on})
    # gap between that edge and the nearest remaining material, measured at the edge's middle
    gaps = []
    for r in on:
        mid = Point(r.midpoint)
        gaps.append(round(material.distance(mid), 3))
    # control: the same edge before any cut sits ON the panel boundary (distance 0)
    control = [round(panel.distance(Point(r.midpoint)), 6) for r in on]
    sizes = sorted([(pc.piece_id, *[round(v, 1) for v in sheets.oriented_extent(pc, sheets.sheet_transform(axis), step)], round(pc.area_mm2)) for pc in pieces],
                   key=lambda t: t[3])
    out[name] = dict(chords_on_line=[round(s["length_mm"], 1) for s in chords], previewed_stub_len=round(stub["length_mm"], 1),
                     seam_lies_on=labels, edge_to_material_gap_after_cut_mm=gaps, edge_to_panel_before_cut_mm=control,
                     pieces=len(pieces), smallest_pieces=sizes[:4], warnings=warnings,
                     material_removed_mm2=round(panel.area - material.area, 1))
    print(name, json.dumps(out[name]))
dump("edge_seam_cut.json", out)
