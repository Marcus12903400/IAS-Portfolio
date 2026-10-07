"""Do the pieces cut by an edge-settled seam keep every cut-out?  (follow-up to probe_edge_seam_cut)"""
import sys, json
from pathlib import Path
import numpy as np
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import unary_union
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN
for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"): sys.path.insert(0, p)
from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config
config = load_config(); options = sheets.settings(config)
frame, _ = sheetjob.resolve_frame(RUN, options); axis = np.asarray(frame.axis, float)
along, across = seamsnap.master_directions(axis)
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))[0]
polygons = seamplace.panel_polygons(loops, options); step = float(options["sample_step_mm"])
outer, holes = sheets.classify_loops(loops[1], step); panel = polygons[1]
print("panel 1 area (holes removed)", round(panel.area), "interiors", len(panel.interiors), "hole loops", len(holes))
hole_polys = [Polygon(r) for r in panel.interiors]
def check(name, p, unit, mode):
    chords = seamplace.seam_through(np.array(p), unit, polygons, options, panel_ids=[1])
    stub = min(chords, key=lambda s: abs(s["along_mm"]))
    seam = sheets.Seam("e", stub["x1"], stub["y1"], stub["x2"], stub["y2"], panel_id=1, mode=mode)
    pieces, w = sheets.split_panel(1, outer, holes, [seam], options)
    polys = [pc.polygon(step) for pc in pieces]
    material = unary_union(polys)
    lost = [i for i, h in enumerate(hole_polys) if material.contains(h.representative_point())]
    kerf = seam.extended(10000).buffer(3.0, cap_style=2).intersection(panel).area
    print(name, "pieces", [(pc.piece_id, len(pc.holes), round(pc.area_mm2)) for pc in pieces],
          "sum area", round(sum(pc.area_mm2 for pc in pieces)), "panel-kerf", round(panel.area - kerf),
          "union(piece.polygon) area", round(material.area), "holes lost in piece.polygon", lost, "warnings", w)
    # the reference the seam lies on
    refs = seamsnap.references_from_loops({1: loops[1]}, options)
    line = LineString([np.array(p) - unit * 5000, np.array(p) + unit * 5000])
    on = [(r.label, round(r.length_mm, 1), round(line.distance(Point(r.p0)), 3), round(line.distance(Point(r.p1)), 3)) for r in refs if r.kind == "line" and line.distance(Point(r.midpoint)) < 1.0]
    print("   straight fitted edges within 1 mm of the seam line:", on)
    for r in refs:
        if r.kind == "line" and line.distance(Point(r.midpoint)) < 1.0:
            print("   gap from that edge's middle to remaining material after the cut:", round(material.distance(Point(r.midpoint)), 3), "mm")
check("A_cutout_edge_along", (-821.48, 375.36), along, "along")
check("C_outer_edge_across", (2514.88, 177.38), across, "across")
