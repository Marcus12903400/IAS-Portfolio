"""Production path: plan + write sheet DXFs on a staged copy with four hover-placed
seams boxing in panel 1's round cut-out, so the piece that holds it fits a sheet.
Then read the sheet DXF back: is the 207 mm circle in it?"""
import sys, json, shutil, math
from pathlib import Path
import numpy as np
import ezdxf
from shapely.geometry import Point
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HERE, RUN, dump
for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"): sys.path.insert(0, p)
from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config
config = load_config(); options = sheets.settings(config); step = float(options["sample_step_mm"])
frame, _ = sheetjob.resolve_frame(RUN, options); axis = np.asarray(frame.axis, float)
along, across = seamsnap.master_directions(axis)
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))[0]
polygons = seamplace.panel_polygons(loops, options)
outer, holes = sheets.classify_loops(loops[1], step)
circle = next(h for h in holes if len(h.vertices) == 2)
centre = circle.xy.mean(axis=0); radius = float(np.hypot(*(circle.xy[1] - circle.xy[0]))) / 2
print("round cut-out centre", centre.round(1).tolist(), "diameter", round(2 * radius, 1))
PLAN = HERE / "runs" / "axis_plan2"
if PLAN.exists(): shutil.rmtree(PLAN)
PLAN.mkdir(parents=True)
for name in ("final_auto.dxf", "run.json"): shutil.copyfile(RUN / name, PLAN / name)
seams = []
for i, (unit, mode, off) in enumerate([(along, "along", -260.0), (along, "along", 260.0), (across, "across", -380.0), (across, "across", 380.0)]):
    normal = np.array([-unit[1], unit[0]])
    point = centre + normal * off
    chords = seamplace.seam_through(point, unit, polygons, options, panel_ids=[1])
    s = min(chords, key=lambda c: abs(c["along_mm"]))      # the chord through the point, as the hover previews it
    seams.append(sheets.Seam(f"box{i+1}", s["x1"], s["y1"], s["x2"], s["y2"], panel_id=1, mode=mode, raw=(s["x1"], s["y1"], s["x2"], s["y2"])))
result = sheetjob.plan(PLAN, config, seams=seams, write_files=True, progress=lambda m: None)
print("status", result["status"], "pieces", result["piece_count"], "sheets", result["summary"]["sheet_count"], "files", [f["name"] for f in result["files"]])
print("pieces (id, holes):", [(p["piece_id"], p["holes"]) for p in result["pieces"]])
# which piece holds the circle's centre?
seam_list = [sheets.Seam.from_dict(d) for d in result["seams"]]
pieces, w = sheets.split_panel(1, outer, holes, seam_list, options)
holder = next((pc for pc in pieces if pc.polygon(step).buffer(1.0).contains(Point(centre))), None)
# the shapely part that holds the centre, before the rebuild
from shapely.ops import unary_union
panel = sheets.loop_polygon(outer, holes, step)
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
rem = panel.difference(unary_union([s.extended(reach).buffer(3.0, cap_style=2, join_style=2) for s in seam_list]))
raw_part = next(g for g in rem.geoms if g.buffer(1.0).contains(Point(centre)) or any(Point(centre).within(__import__('shapely').geometry.Polygon(r)) for r in g.interiors))
print("shapely part holding the circle: interiors", len(raw_part.interiors), "-> rebuilt piece", holder.piece_id if holder else None,
      "holes", [len(h.vertices) for h in holder.holes] if holder else None,
      "circle centre inside rebuilt piece material:", bool(holder and holder.polygon(step).contains(Point(centre))))
# and in the sheet DXF?
found = []
for f in result["files"]:
    doc = ezdxf.readfile(str(PLAN / f["name"]))
    for e in doc.modelspace():
        if e.dxftype() == "LWPOLYLINE":
            pts = list(e.get_points("xyb"))
            if len(pts) == 2 and all(abs(p[2]) > 0.5 for p in pts):
                d = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
                found.append((f["name"], e.dxf.layer, round(d, 1)))
    print(f["name"], "placed pieces:", f["pieces"] if isinstance(f["pieces"], list) else f["pieces"])
print("2-vertex circles in the sheet DXFs (file, layer, diameter):", found)
print("report mentions:", [l for l in (PLAN / "sheet_report.md").read_text().splitlines() if "arc" in l.lower()])
dump("round_hole_dxf.json", dict(status=result["status"], pieces=result["pieces"], circles_in_dxf=found, warnings=result["warnings"]))
