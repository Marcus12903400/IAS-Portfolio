"""How many arcs survive split_panel as bulges, on panel 1 with one along seam far from every cut-out."""
import sys, json
from pathlib import Path
import numpy as np
from shapely.geometry import Polygon
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN
for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"): sys.path.insert(0, p)
from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config
config = load_config(); options = sheets.settings(config)
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))[0]
step = float(options["sample_step_mm"])
outer, holes = sheets.classify_loops(loops[1], step)
def describe(loop):
    b = np.abs(loop.bulges) > 1e-12
    return f"{len(loop.vertices)} v / {int(b.sum())} arcs"
print("original panel 1: outer", describe(outer), "holes", [describe(h) for h in holes])
six = json.loads(Path(sys.argv[1]).read_text())["six"]
seam = sheets.Seam.from_dict(six[0])   # p1, along, clear of every cut-out
pieces, w = sheets.split_panel(1, outer, holes, [seam], options)
for pc in pieces:
    print(pc.piece_id, "outer", describe(pc.outer), "holes", [describe(h) for h in pc.holes], "max arc err", round(pc.max_arc_error_mm, 4))
# which original hole segments failed the arc fit?  re-run rebuild on the console loop alone (no cut at all)
for i, h in enumerate(holes):
    pts, src = sheets.sample_loop(h, step)
    rebuilt, err = sheets.rebuild_loop(pts, src, h, float(options["arc_rebuild_tolerance_mm"]))
    print(f"hole {i}: original {describe(h)} -> rebuild_loop of its own 1 mm sampling: {describe(rebuilt)}, worst arc err {err:.4f}")
pts, src = sheets.sample_loop(outer, step)
rebuilt, err = sheets.rebuild_loop(pts, src, outer, float(options["arc_rebuild_tolerance_mm"]))
print(f"outer: original {describe(outer)} -> rebuild of its own sampling: {describe(rebuilt)}, worst arc err {err:.4f}")
# and with the ring start moved (as a boolean op would), the first/last run merge logic
pts2 = np.roll(pts, -len(pts)//3, axis=0); src2 = np.roll(src, -len(src)//3)
rebuilt2, err2 = sheets.rebuild_loop(pts2, src2, outer, float(options["arc_rebuild_tolerance_mm"]))
print(f"outer, ring start moved: {describe(rebuilt2)}")
