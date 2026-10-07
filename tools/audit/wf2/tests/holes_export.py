"""Does the missing round cut-out reach a sheet DXF?  Plan + export on SCRATCH copies with the AXIS run's stored optimiser seam sets."""
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
NOAX = "21kwcockpit-1-20260901-172519"
config = load_config()
opts = sheets.settings(config)
step = opts["sample_step_mm"]

for seam_file in ("seams_previous.json", "seams_optimiser_run.json.bak"):
    stored = json.loads((RUNS / AXIS / seam_file).read_text(encoding="utf-8"))["seams"]
    work = SCRATCH / "holes_export" / seam_file.replace(".", "_") / AXIS
    work.mkdir(parents=True, exist_ok=True)
    for f in ("final_auto.dxf", "run.json"):
        shutil.copyfile(RUNS / AXIS / f, work / f)
    (work / "seams.json").write_text(json.dumps({"seams": stored}), encoding="utf-8")
    result = sheetjob.plan(work, config, write_files=True)       # DXFs land in the scratch copy
    modes = sorted({(s.get("mode") or "free") for s in stored})
    print(f"{seam_file}: {len(stored)} seams (modes {modes}) -> status {result['status']}, {result['piece_count']} pieces, "
          f"{len(result['sheets'])} sheets, oversize {[o['piece_id'] for o in result['oversize']]}, refused {result['refused']}")
    loops, _p, _k = sheets.read_fitted_dxf(work / "final_auto.dxf")
    outer, holes = sheets.classify_loops(loops[1], step)
    seam_list = [sheets.Seam.from_dict(s) for s in result["seams"]]
    pieces, warnings = sheets.split_panel(1, outer, holes, seam_list, opts)
    rh = next(h for h in holes if len(h.vertices) == 2)
    c = sheets.bulge_to_arc(rh.vertices[0, :2], rh.vertices[1, :2], rh.vertices[0, 2])[0]
    centre = Point(float(c[0]), float(c[1]))
    frame, _w = sheetjob.resolve_frame(work, opts)
    rotation = sheets.sheet_transform(frame.axis)
    for p in pieces:
        shell = Polygon(sheets.sample_loop(p.outer, step)[0])
        if not shell.contains(centre):
            continue
        covered = any(Polygon(sheets.sample_loop(h, step)[0]).contains(centre) for h in p.holes)
        placed = [(sh["sheet"], pl) for sh in result["sheets"] for pl in sh["placements"] if pl["piece_id"] == p.piece_id]
        print(f"   round cut-out centre lies in {p.piece_id}: holes={len(p.holes)}, hole covers centre={covered}, "
              f"nested={'sheet %d' % placed[0][0] if placed else 'NO'}")
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
            print(f"   {path.name} layer {layer}: {len(polys)} polyline(s) written (1 outer + holes); "
                  f"polylines enclosing the cut-out's centre: {enclosing} (2 = outer + the hole, 1 = THE HOLE IS MISSING)")
    print("   split warnings:", warnings)

# why the NO-AXIS run keeps zero holes: are all six cut-outs crossed by a kerf?
work = SCRATCH / "holes_export" / "noaxis" / NOAX
work.mkdir(parents=True, exist_ok=True)
for f in ("final_auto.dxf", "run.json"):
    shutil.copyfile(RUNS / NOAX / f, work / f)
pinned = json.loads((RUNS / NOAX / "seams.json").read_text(encoding="utf-8"))["seams"]   # read-only; same 8 seams the test pins
(work / "seams.json").write_text(json.dumps({"seams": pinned}), encoding="utf-8")
result = sheetjob.plan(work, config, write_files=False)
loops, _p, _k = sheets.read_fitted_dxf(work / "final_auto.dxf")
outer, holes = sheets.classify_loops(loops[1], step)
seam_list = [sheets.Seam.from_dict(s) for s in result["seams"]]
panel = sheets.loop_polygon(outer, holes, step)
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
kerfs = unary_union([s.extended(reach).buffer(opts["seam_gap_mm"] / 2, cap_style=2, join_style=2) for s in seam_list])
print(f"NO-AXIS run: cut-outs crossed by a kerf: {[kerfs.intersects(Polygon(sheets.sample_loop(h, step)[0])) for h in holes]} "
      f"(vertex counts {[len(h.vertices) for h in holes]}); pieces' hole counts: {[p['holes'] for p in result['pieces'] if p['panel_id'] == 1]}")
