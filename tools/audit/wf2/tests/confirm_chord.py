"""The hovered chord vs the cut, on the synthetic console panel the tests already use."""
import math, numpy as np
from autodeck2 import seamplace, sheets
from autodeck2.config import load_config

def rounded_rect(width, height, radius, x=0.0, y=0.0):
    b = math.tan(math.pi / 8); r = radius
    return sheets.Loop(np.array([[x + r, y, 0.0],[x + width - r, y, b],[x + width, y + r, 0.0],[x + width, y + height - r, b],
        [x + width - r, y + height, 0.0],[x + r, y + height, b],[x, y + height - r, 0.0],[x, y + r, b]], dtype=float))

opts = sheets.settings(load_config())
outer = rounded_rect(1600.0, 900.0, 120.0)
console = rounded_rect(400.0, 300.0, 40.0, x=600.0, y=300.0)
polys = seamplace.panel_polygons({1: [outer, console]}, opts)
segs = seamplace.seam_through((800.0, 450.0), np.array([0.0, 1.0]), polys, opts)
print("hover at (800,450) across the console returns", len(segs), "chords:", [(round(s['y1']), round(s['y2']), round(s['length_mm'])) for s in segs])
below = segs[0]
seam = sheets.Seam("hover", below["x1"], below["y1"], below["x2"], below["y2"], panel_id=1, mode="across")
pieces, w = sheets.split_panel(1, outer, [console], [seam], opts)
print("placing ONLY the lower 300 mm chord -> split_panel pieces:", len(pieces), "areas:", [round(p.area_mm2) for p in pieces], "holes:", [len(p.holes) for p in pieces], w)
# what the test suite checks instead: a seam entirely off the panel
far = sheets.Seam("s", 5000.0, -900.0, 5000.0, 900.0)
print("test_sheets' off-panel seam ->", len(sheets.split_panel(1, outer, [console], [far], opts)[0]), "piece(s)")
# seam_world_polylines logic (bridge 261-266): chords of the infinite line through the seam midpoint
mid = ((below['x1']+below['x2'])/2, (below['y1']+below['y2'])/2)
crossed = seamplace.panels_crossed(below['x1'], below['y1'], below['x2'], below['y2'], polys, opts, 1)
print("3D overlay would draw", len(seamplace.seam_through(mid, np.array([0.0,1.0]), polys, opts, panel_ids=crossed)), "chord(s) for that one placed seam")
# slivers: a seam 10 mm inside the straight bottom edge, drops a 1600x10 strip? (area 16000 > 400 so kept) ; 0.2 mm inside -> sliver dropped
for inset in (10.0, 0.2):
    s = sheets.Seam("s", -100.0, inset, 1700.0, inset)
    p, w = sheets.split_panel(1, outer, [], [s], opts)
    print(f"seam {inset} mm inside the bottom edge -> {len(p)} piece(s); warnings={w}")
