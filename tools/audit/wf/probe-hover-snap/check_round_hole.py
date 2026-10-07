"""Does the round cut-out of panel 1 survive split_panel's loop rebuild?"""
import sys, json
from pathlib import Path
import numpy as np
from shapely.geometry import Point, Polygon
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN
for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"): sys.path.insert(0, p)
from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config
config = load_config(); options = sheets.settings(config)
frame, _ = sheetjob.resolve_frame(RUN, options); axis = np.asarray(frame.axis, float)
along, across = seamsnap.master_directions(axis)
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))[0]
step = float(options["sample_step_mm"])
for pid in sorted(loops):
    outer, holes = sheets.classify_loops(loops[pid], step)
    print(f"panel {pid}: outer {len(outer.vertices)} vertices; holes:", [(len(h.vertices), round(Polygon(sheets.sample_loop(h, step)[0]).area)) for h in holes])
outer, holes = sheets.classify_loops(loops[1], step)
hole_polys = [Polygon(sheets.sample_loop(h, step)[0]) for h in holes]
# one along seam well clear of every cut-out (the six-seam plan's p1)
six = json.loads(Path(sys.argv[1]).read_text())["six"]
for sd in six[:1] + six[2:3]:
    seam = sheets.Seam.from_dict(sd)
    pieces, w = sheets.split_panel(1, outer, holes, [seam], options)
    print(f"\nseam {seam.seam_id} {seam.mode}: {len(pieces)} pieces", w)
    for pc in pieces:
        shp = pc.polygon(step)
        inside_rebuilt = [i for i, h in enumerate(hole_polys) if shp.contains(h.representative_point())]
        print(f"  {pc.piece_id}: rebuilt holes {[len(h.vertices) for h in pc.holes]}, "
              f"original holes whose interior is now MATERIAL in the rebuilt piece: {inside_rebuilt}, max_arc_err {pc.max_arc_error_mm:.4f}")
# the direct check of rebuild_loop on a 2-vertex circle
circle = next(h for h in holes if len(h.vertices) == 2)
pts, src = sheets.sample_loop(circle, step)
rebuilt, err = sheets.rebuild_loop(pts, src, circle, float(options["arc_rebuild_tolerance_mm"]))
print("\nrebuild_loop(circle sampled at 1 mm):", len(rebuilt.vertices), "vertices, bulges", rebuilt.bulges.round(4).tolist(), "err", round(err, 5),
      "-> split_panel keeps a hole only if len(vertices) >= 3")
