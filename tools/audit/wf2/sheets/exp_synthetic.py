import math
import numpy as np
from shapely.geometry import Polygon
from autodeck2 import sheets
from autodeck2.config import load_config

config = load_config()
opts = sheets.settings(config)
step = opts["sample_step_mm"]

def rect(x0, y0, w, h):
    return sheets.Loop(np.array([[x0, y0, 0], [x0 + w, y0, 0], [x0 + w, y0 + h, 0], [x0, y0 + h, 0]], dtype=float))

def rounded_rect(width, height, radius, x=0.0, y=0.0, bulge_sign=1.0):
    b = math.tan(math.pi / 8) * bulge_sign
    r = radius
    return sheets.Loop(np.array([
        [x + r, y, 0.0], [x + width - r, y, b], [x + width, y + r, 0.0], [x + width, y + height - r, b],
        [x + width - r, y + height, 0.0], [x + r, y + height, b], [x, y + height - r, 0.0], [x, y + r, b]], dtype=float))

def circle(cx, cy, r):
    return sheets.Loop(np.array([[cx - r, cy, 1.0], [cx + r, cy, 1.0]], dtype=float))

def show(label, pieces, warns):
    print(f"--- {label}")
    for w in warns: print("   warn:", w)
    for p in pieces:
        poly = p.polygon(step)
        nb = int(np.count_nonzero(np.abs(p.outer.bulges) > 1e-12))
        print(f"   {p.piece_id:6s} from_seam={p.from_seam} area={p.area_mm2:9.1f} verts={len(p.outer.vertices):5d} bulges={nb} holes={len(p.holes)} hole-verts={[len(h.vertices) for h in p.holes]} bounds={[round(b, 2) for b in poly.bounds]} arcerr={p.max_arc_error_mm:.4f}")

panel = rect(0, 0, 1000, 600)
# (a1) seam exactly on an edge
show("seam exactly on edge x=0", *sheets.split_panel(1, panel, [], [sheets.Seam("s", 0.0, -50.0, 0.0, 650.0)], opts))
# (a2) seam 2 mm inside the edge
show("seam 2 mm inside edge x=2", *sheets.split_panel(1, panel, [], [sheets.Seam("s", 2.0, -50.0, 2.0, 650.0)], opts))
# (a3) zero length seam inside the panel
show("zero-length seam at (500,300)", *sheets.split_panel(1, panel, [], [sheets.Seam("s", 500.0, 300.0, 500.0, 300.0)], opts))
# (a4) zero length seam outside the panel, global
show("zero-length seam outside, global", *sheets.split_panel(1, panel, [], [sheets.Seam("s", 5000.0, 300.0, 5000.0, 300.0)], opts))
# (a5) seam entirely inside a hole: global vs bound
hole = rect(400, 200, 200, 200)
show("seam inside hole only, GLOBAL", *sheets.split_panel(1, panel, [hole], [sheets.Seam("s", 500.0, 220.0, 500.0, 380.0, panel_id=None)], opts))
show("seam inside hole only, BOUND to panel 1", *sheets.split_panel(1, panel, [hole], [sheets.Seam("s", 500.0, 220.0, 500.0, 380.0, panel_id=1)], opts))
# (a6) grazing a corner
show("seam grazing corner (x+y=20)", *sheets.split_panel(1, panel, [], [sheets.Seam("s", -30.0, 50.0, 50.0, -30.0)], opts))
# (a7) seam touching the panel at exactly one corner point, global
show("seam touching corner point only, global", *sheets.split_panel(1, panel, [], [sheets.Seam("s", -100.0, 100.0, 0.0, 0.0)], opts))
# (b1) 25 mm gunwale strip, across seam 10 mm from the end
strip = rect(0, 0, 25, 2400)
show("25mm strip, across seam 10 mm from end", *sheets.split_panel(2, strip, [], [sheets.Seam("s", -10.0, 10.0, 40.0, 10.0)], opts))
show("25mm strip, across seam 20 mm from end", *sheets.split_panel(2, strip, [], [sheets.Seam("s", -10.0, 20.0, 40.0, 20.0)], opts))
show("25mm strip, ALONG seam down the middle", *sheets.split_panel(2, strip, [], [sheets.Seam("s", 12.5, -10.0, 12.5, 2410.0)], opts))
# (c/d) hole clipped by a seam: rounded hole
rr = rounded_rect(1600, 900, 120)
rhole = rounded_rect(300, 200, 40, 650, 350, bulge_sign=1.0)
# ingest convention: holes CW. Reverse to CW:
v = rhole.vertices[::-1].copy()
# reversing vertex order needs bulges shifted and negated
b = -np.roll(v[:, 2], -1)
rhole_cw = sheets.Loop(np.column_stack([v[:, :2], b]))
print("hole CW signed area:", 0.5 * float(np.sum(v[:, 0] * np.roll(v[:, 1], -1) - np.roll(v[:, 0], -1) * v[:, 1])))
show("rounded rect + rounded hole, seam MISSES hole (x=300)", *sheets.split_panel(1, rr, [rhole_cw], [sheets.Seam("s", 300.0, -50.0, 300.0, 950.0)], opts))
show("rounded rect + rounded hole, seam THROUGH hole (x=800)", *sheets.split_panel(1, rr, [rhole_cw], [sheets.Seam("s", 800.0, -50.0, 800.0, 950.0)], opts))
# circle hole untouched / cut
show("rounded rect + circle hole untouched", *sheets.split_panel(1, rr, [circle(1200, 450, 100)], [sheets.Seam("s", 300.0, -50.0, 300.0, 950.0)], opts))
show("rounded rect + circle hole cut through centre", *sheets.split_panel(1, rr, [circle(1200, 450, 100)], [sheets.Seam("s", 1200.0, -50.0, 1200.0, 950.0)], opts))
# (d) arc cut in the middle: seam through the corner fillet at 45 deg of corner (1600, 0) r=120: fillet centre (1480,120); seam vertical at x=1565
show("seam through a fillet arc (x=1565)", *sheets.split_panel(1, rr, [], [sheets.Seam("s", 1565.0, -50.0, 1565.0, 950.0)], opts))
# small radii fillets: do the arcs survive an unrelated cut?
for r in (1.0, 2.0, 3.0, 5.0, 10.0):
    loop = rounded_rect(1600, 900, r)
    pieces, warns = sheets.split_panel(1, loop, [], [sheets.Seam("s", 800.0, -50.0, 800.0, 950.0)], opts)
    nb = sum(int(np.count_nonzero(np.abs(p.outer.bulges) > 1e-12)) for p in pieces)
    nv = sum(len(p.outer.vertices) for p in pieces)
    print(f"--- fillet r={r}: bulges after={nb} (before 4) verts after={nv} arcerr={max(p.max_arc_error_mm for p in pieces):.4f}")
# multipolygon / bowtie outline with no seams and with a seam
bow = sheets.Loop(np.array([[0, 0, 0], [500, 300, 0], [1000, 0, 0], [1000, 600, 0], [500, 300, 0], [0, 600, 0]], dtype=float))
poly = sheets.loop_polygon(bow, [], step)
print("bowtie polygon:", poly.geom_type, poly.is_valid, round(poly.area))
show("bowtie, no seams", *sheets.split_panel(1, bow, [], [], opts))
show("bowtie, seam x=250", *sheets.split_panel(1, bow, [], [sheets.Seam("s", 250.0, -50.0, 250.0, 650.0)], opts))
# two disjoint loops drawn on one panel layer: classify makes the smaller a hole
a, b2 = rect(0, 0, 800, 500), rect(1200, 0, 300, 300)
outer, holes = sheets.classify_loops([a, b2], step)
poly = sheets.loop_polygon(outer, holes, step)
print("two disjoint loops on one panel -> polygon", poly.geom_type, "valid", poly.is_valid, "area", round(poly.area), "(800x500=400000, second part 90000)")
# hole crossing the outer boundary
h2 = rect(900, 200, 300, 200)
poly = sheets.loop_polygon(rect(0, 0, 1000, 600), [h2], step)
print("hole crossing outer -> polygon", poly.geom_type, "valid", poly.is_valid, "area", round(poly.area), "(expect 600000-20000=580000 if notch)")
