"""Draw the fitted deck (placed frame, bow-up), optional seams and the pieces
they cut, to a PNG with PIL -- no matplotlib here.

  python render_deck.py --seams out/snap_hovered.json:six --out out/deck_six.png
  python render_deck.py --seams-file runs/axis/seams_optimiser_run.json.bak --out out/deck_opt.png
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN

for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"):
    sys.path.insert(0, p)
from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

ap = argparse.ArgumentParser()
ap.add_argument("--seams", default=None, help="json file[:key] holding a list of seam dicts")
ap.add_argument("--seams-file", default=None, help="a seams.json-style file ({'seams': [...]})")
ap.add_argument("--out", required=True)
ap.add_argument("--scale", type=float, default=0.3, help="pixels per mm")
ap.add_argument("--no-pieces", action="store_true")
ap.add_argument("--labels", action="store_true", help="label every piece with its size")
args = ap.parse_args()

config = load_config()
options = sheets.settings(config)
frame, _w = sheetjob.resolve_frame(RUN, options)
axis = np.asarray(frame.axis, float)
loops, pattern, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))
step = float(options["sample_step_mm"])

seam_dicts = []
if args.seams:
    path, _, key = args.seams.rpartition(":")
    if len(key) == 1 or path.endswith((":", "/", "\\")):   # a bare path, no key
        path, key = args.seams, ""
    data = json.loads(Path(path).read_text())
    seam_dicts = data[key] if key else data
if args.seams_file:
    seam_dicts = json.loads(Path(args.seams_file).read_text())["seams"]
seams = [sheets.Seam.from_dict({**s, "seam_id": s.get("seam_id") or f"s{i + 1}"}) for i, s in enumerate(seam_dicts)]
if seams:
    seams, snaps, _w = sheetjob.apply_snap(seams, loops, axis, options)

# bow-up frame: rotate so the axis points +Y (screen up)
R = sheets.sheet_transform(axis)          # placed -> sheet (x across, y along)


def to_sheet(xy):
    return np.asarray(xy, float) @ R.T


all_pts = np.vstack([to_sheet(sheets.sample_loop(l, 5.0)[0]) for ls in loops.values() for l in ls])
minx, miny = all_pts.min(axis=0) - 60; maxx, maxy = all_pts.max(axis=0) + 60
W, H = int((maxx - minx) * args.scale), int((maxy - miny) * args.scale)
img = Image.new("RGB", (W, H), "white")
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("arial.ttf", 14)
except Exception:
    font = ImageFont.load_default()


def px(p):
    return ((p[0] - minx) * args.scale, (maxy - p[1]) * args.scale)


def poly_px(points):
    return [px(p) for p in to_sheet(points)]


colours = ["#f28e2b", "#4e79a7", "#59a14f", "#e15759", "#b07aa1", "#76b7b2", "#edc948", "#ff9da7", "#9c755f", "#bab0ab"]
pieces_all = []
for pid in sorted(loops):
    outer, holes = sheets.classify_loops(loops[pid], step)
    if seams and not args.no_pieces:
        pieces, _pw = sheets.split_panel(pid, outer, holes, seams, options)
        for i, piece in enumerate(pieces):
            w, l = sheets.oriented_extent(piece, R, step)
            pts, _s = sheets.sample_loop(piece.outer, 3.0)
            colour = colours[i % len(colours)]
            if min(w, l) < 20:
                colour = "#ff0000"
            draw.polygon(poly_px(pts), fill=colour, outline="black")
            for hole in piece.holes:
                hp, _s = sheets.sample_loop(hole, 3.0)
                draw.polygon(poly_px(hp), fill="white", outline="black")
            c = px(to_sheet(pts).mean(axis=0))
            if args.labels or min(w, l) < 20 or True:
                draw.text((c[0] - 20, c[1] - 7), f"{piece.piece_id} {w:.0f}x{l:.0f}", fill="black", font=font)
            pieces_all.append((piece.piece_id, w, l, piece.area_mm2))
    else:
        pts, _s = sheets.sample_loop(outer, 3.0)
        draw.polygon(poly_px(pts), fill="#dddddd", outline="black")
        for hole in holes:
            hp, _s = sheets.sample_loop(hole, 3.0)
            draw.polygon(poly_px(hp), fill="white", outline="black")
        c = px(to_sheet(pts).mean(axis=0))
        draw.text(c, f"panel {pid}", fill="black", font=font)
for pid, lines in pattern.items():
    for ln in lines:
        a, b = poly_px(ln)
        draw.line([a, b], fill="#bbbbbb", width=1)
for seam in seams:
    a, b = px(to_sheet([seam.x1, seam.y1])), px(to_sheet([seam.x2, seam.y2]))
    draw.line([a, b], fill="#d00000", width=3)
    draw.text(((a[0] + b[0]) / 2 + 4, (a[1] + b[1]) / 2 - 16), seam.seam_id, fill="#d00000", font=font)
# axes
draw.text((8, 8), f"bow up; axis {math.degrees(math.atan2(axis[1], axis[0])):.2f} deg; scale {args.scale} px/mm; red seams; pieces <20 mm wide in bright red",
          fill="black", font=font)
img.save(args.out)
print("wrote", args.out, img.size)
for row in sorted(pieces_all, key=lambda r: min(r[1], r[2]))[:10]:
    print("  piece", row[0], f"{row[1]:.1f} x {row[2]:.1f} mm, {row[3]:.0f} mm2")
