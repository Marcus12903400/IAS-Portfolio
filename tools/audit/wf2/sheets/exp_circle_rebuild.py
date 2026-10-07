import json
from pathlib import Path
import numpy as np
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from autodeck2 import sheets, sheetjob
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
RUN = S / "axisrun"
config = load_config(); options = sheets.settings(config); step = options["sample_step_mm"]
loops, _p, _k = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
frame, _w = sheetjob.resolve_frame(RUN, options)
outer, holes = sheets.classify_loops(loops[1], step)
seams = [sheets.Seam.from_dict(s) for s in json.loads((RUN / "seams.json").read_text(encoding="utf-8"))["seams"]]
snapped, _r, _sw = sheetjob.apply_snap(seams, loops, frame.axis, options)
panel = sheets.loop_polygon(outer, holes, step)
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
kerfs = [s.extended(reach).buffer(3.0, cap_style=2, join_style=2) for s in snapped]
remainder = panel.difference(unary_union(kerfs))
parts = sorted([p for p in remainder.geoms if p.area >= 400], key=lambda g: (-g.area, g.bounds))
big = orient(parts[0], 1.0)
sources = [(l, *sheets.sample_loop(l, step)) for l in [outer, *holes]]
print("largest part interiors:", len(big.interiors))
for i, ring in enumerate(big.interiors):
    coords = np.asarray(ring.coords)[:-1]
    rebuilt, err = sheets._rebuild_ring(coords, sources, options["arc_rebuild_tolerance_mm"], step)
    print(f"  interior {i}: ring pts {len(coords)} bbox {[round(b) for b in ring.bounds]} -> rebuilt verts {len(rebuilt.vertices)} bulges {np.round(rebuilt.bulges, 4).tolist() if len(rebuilt.vertices) <= 4 else '...'} err {err:.4f}  kept by split_panel? {len(rebuilt.vertices) >= 3}")
