import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString
from autodeck2 import sheets, sheetjob
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
RUN = S / "axisrun"
config = load_config(); options = sheets.settings(config); step = options["sample_step_mm"]
loops, _p, _k = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
frame, _w = sheetjob.resolve_frame(RUN, options)
polys = {}
for pid in sorted(loops):
    o, h = sheets.classify_loops(loops[pid], step)
    polys[pid] = (o, h, sheets.loop_polygon(o, h, step))
print("inter-panel distances (mm):", {(a, b): round(polys[a][2].distance(polys[b][2]), 2) for a in polys for b in polys if a < b})
seams = [sheets.Seam.from_dict(s) for s in json.loads((RUN / "seams.json").read_text(encoding="utf-8"))["seams"]]
snapped, _r, _sw = sheetjob.apply_snap(seams, loops, frame.axis, options)
outer, holes, panel = polys[1]
pieces, warns = sheets.split_panel(1, outer, holes, snapped, options)
minx, miny, maxx, maxy = -1600, -2300, 2600, 1100
scale = 0.45
W, H = int((maxx - minx) * scale) + 40, int((maxy - miny) * scale) + 40
img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
def px(x, y): return (20 + (x - minx) * scale, 20 + (maxy - y) * scale)
colours = ["#9ecae1", "#a1d99b", "#fdae6b", "#bcbddc", "#fdd0a2", "#c7e9c0", "#dadaeb", "#fc9272", "#fb6a4a", "#cccccc"]
for i, p in enumerate(pieces):
    pts = sheets.sample_loop(p.outer, 4.0)[0]
    d.polygon([px(*q) for q in pts], fill=colours[i % len(colours)], outline="black")
    for h in p.holes:
        hp = sheets.sample_loop(h, 4.0)[0]
        d.polygon([px(*q) for q in hp], fill="white", outline="black")
    c = p.polygon(step).representative_point()
    d.text(px(c.x, c.y), p.piece_id, fill="black")
for pid in (2, 3, 4, 5):
    o, h, poly = polys[pid]
    d.polygon([px(*q) for q in sheets.sample_loop(o, 4.0)[0]], fill="#eeeeee", outline="gray")
    c = poly.representative_point(); d.text(px(c.x, c.y), f"P{pid}", fill="gray")
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
for s in snapped:
    ext = np.asarray(s.extended(reach).coords)
    d.line([px(*ext[0]), px(*ext[1])], fill="#ff000055", width=1)
    d.line([px(s.x1, s.y1), px(s.x2, s.y2)], fill="red", width=4)
    d.text(px(s.x1, s.y1), s.seam_id, fill="red")
d.text((20, H - 18), "thick red = seam as drawn/snapped; thin red = line actually cut (extended); pieces coloured; grey = other panels", fill="black")
out = S / "panel1_hand_seams_split.png"; img.save(out); print("wrote", out, img.size)
for p in pieces:
    print(p.piece_id, round(p.area_mm2), [round(b) for b in p.polygon(step).bounds])
