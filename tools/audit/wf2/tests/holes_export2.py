"""A seam layout on the AXIS run in which the piece holding the round cut-out FITS a sheet: is the hole in the DXF?"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import ezdxf
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from autodeck2 import nesting, sheetjob, sheets
from autodeck2.config import load_config

SCRATCH = Path(sys.argv[1])
RUNS = Path("D:/AutoDeck/engine/outputs/runs")
AXIS = "21kwcockpit-1-20260901-180939"
config = load_config()
opts = sheets.settings(config)
step = opts["sample_step_mm"]

work = SCRATCH / "holes_export2" / AXIS
work.mkdir(parents=True, exist_ok=True)
for f in ("final_auto.dxf", "run.json"):
    shutil.copyfile(RUNS / AXIS / f, work / f)
# two along-boat seams and two across-boat seams, placed by mode so the corrector squares them to the axis
seams = [
    {"seam_id": "a1", "x1": -1600.0, "y1": -400.0, "x2": 2600.0, "y2": -400.0, "panel_id": 1, "mode": "along"},
    {"seam_id": "a2", "x1": -1600.0, "y1": 300.0, "x2": 2600.0, "y2": 300.0, "panel_id": 1, "mode": "along"},
    {"seam_id": "c1", "x1": -170.0, "y1": -1200.0, "x2": -170.0, "y2": 1100.0, "panel_id": 1, "mode": "across"},
    {"seam_id": "c2", "x1": 1180.0, "y1": -1200.0, "x2": 1180.0, "y2": 1100.0, "panel_id": 1, "mode": "across"},
]
(work / "seams.json").write_text(json.dumps({"seams": seams}), encoding="utf-8")
result = sheetjob.plan(work, config, write_files=True)
print(f"custom layout -> status {result['status']}, {result['piece_count']} pieces, {len(result['sheets'])} sheets, "
      f"oversize {[o['piece_id'] for o in result['oversize']]}, refused {result['refused']}, warnings: {[w for w in result['warnings'] if 'sliver' in w]}")
loops, _p, _k = sheets.read_fitted_dxf(work / "final_auto.dxf")
outer, holes = sheets.classify_loops(loops[1], step)
seam_list = [sheets.Seam.from_dict(s) for s in result["seams"]]
pieces, warnings = sheets.split_panel(1, outer, holes, seam_list, opts)
panel = sheets.loop_polygon(outer, holes, step)
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
kerfs = unary_union([s.extended(reach).buffer(opts["seam_gap_mm"] / 2, cap_style=2, join_style=2) for s in seam_list])
rh = next(h for h in holes if len(h.vertices) == 2)
c = sheets.bulge_to_arc(rh.vertices[0, :2], rh.vertices[1, :2], rh.vertices[0, 2])[0]
centre = Point(float(c[0]), float(c[1]))
radius = float(np.hypot(*(rh.vertices[0, :2] - c)))
print(f"round cut-out: centre ({centre.x:.0f},{centre.y:.0f}) radius {radius:.1f} mm; crossed by a kerf: {kerfs.intersects(Polygon(sheets.sample_loop(rh, step)[0]))}")
frame, _w = sheetjob.resolve_frame(work, opts)
rotation = sheets.sheet_transform(frame.axis)
for p in pieces:
    shell = Polygon(sheets.sample_loop(p.outer, step)[0])
    if not shell.contains(centre):
        continue
    covered = any(Polygon(sheets.sample_loop(h, step)[0]).contains(centre) for h in p.holes)
    placed = [(sh["sheet"], pl) for sh in result["sheets"] for pl in sh["placements"] if pl["piece_id"] == p.piece_id]
    print(f"   centre lies in {p.piece_id} ({p.area_mm2 / 1e6:.2f} m2): holes={len(p.holes)}, a hole covers the centre={covered}, nested={'sheet %d' % placed[0][0] if placed else 'NO'}")
    if placed:
        sheet_no, pl = placed[0]
        path = work / f"sheet_{sheet_no:02d}.dxf"
        layer = nesting._layer("CAM", p.piece_id)
        doc = ezdxf.readfile(str(path))
        polys = [e for e in doc.modelspace().query("LWPOLYLINE") if str(e.dxf.layer) == layer]
        placement = nesting.Placement(piece_id=p.piece_id, panel_id=1, sheet_index=sheet_no - 1, rotation_deg=int(pl["rotation_deg"]),
                                      offset=np.asarray(pl["offset_mm"], float), origin=np.asarray(pl["origin_mm"], float))
        centre_on_sheet = placement.apply(np.array([[centre.x, centre.y]]), rotation)[0]
        enclosing = 0
        for e in polys:
            verts = np.asarray([(x, y, b) for x, y, b in e.get_points("xyb")], float)
            if Polygon(sheets.sample_loop(sheets.Loop(verts), step)[0]).contains(Point(*centre_on_sheet)):
                enclosing += 1
        print(f"   {path.name} layer {layer}: {len(polys)} polyline(s) on the piece's layer; polylines enclosing the cut-out centre: {enclosing} "
              f"(2 = outer + hole present, 1 = THE HOLE IS MISSING FROM THE CUT FILE)")
        # the other cut-outs of this piece, for comparison
        others = [h for h in holes if len(h.vertices) > 2 and shell.contains(Polygon(sheets.sample_loop(h, step)[0]).representative_point())]
        print(f"   other (polyline) cut-outs whose centre lies in this piece: {len(others)}; holes written for the piece: {len(polys) - 1}")
