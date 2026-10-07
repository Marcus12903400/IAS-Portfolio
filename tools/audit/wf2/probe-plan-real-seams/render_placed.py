"""Placed-frame picture per set (PIL only): panel outlines, the production
pieces filled and labelled, each seam's raw drawing (red, thick), the as-cut
chord (blue) and the EXTENDED cut inside the panel (orange, thin), plus the
boat axis.  Bow is toward axis * bow_sign.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/render_placed.py [A B C D]
"""
import math
import sys

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString

from common import SETS, OUT, PNG, copy_dir, load_json, resplit
from autodeck2 import sheets
from autodeck2.config import load_config

PALETTE = [(214, 231, 250), (250, 224, 214), (222, 245, 214), (245, 240, 200), (232, 214, 250),
           (214, 245, 240), (250, 214, 236), (236, 236, 236), (255, 236, 200), (200, 236, 255)]
SCALE = 0.22


def main(names):
    config = load_config()
    options = sheets.settings(config)
    for name in names:
        work = copy_dir(name)
        plan = load_json(OUT / f"{name}_plan.json")
        rs = resplit(work, plan, options)
        allpts = np.vstack([np.asarray(p[2].exterior.coords) for p in rs["panels"].values()])
        mn = allpts.min(axis=0) - 120
        mx = allpts.max(axis=0) + 120
        W = int((mx[0] - mn[0]) * SCALE) + 1
        H = int((mx[1] - mn[1]) * SCALE) + 1
        img = Image.new("RGB", (W, H), (250, 249, 246))
        d = ImageDraw.Draw(img)

        def px(p):
            return (float((p[0] - mn[0]) * SCALE), float((mx[1] - p[1]) * SCALE))

        def poly_px(xy):
            return [px(p) for p in xy]

        # pieces
        placed = {pl["piece_id"] for sh in plan["sheets"] for pl in sh["placements"]}
        over = {o["piece_id"] for o in plan["oversize"]}
        for i, (pid, piece) in enumerate(sorted(rs["pieces"].items())):
            shell, _s = sheets.sample_loop(piece.outer, 2.0)
            fill = PALETTE[i % len(PALETTE)]
            if pid in over:
                fill = (255, 170, 170)
            d.polygon(poly_px(shell), fill=fill, outline=(60, 60, 60))
            for h in piece.holes:
                ring, _r = sheets.sample_loop(h, 2.0)
                d.polygon(poly_px(ring), fill=(250, 249, 246), outline=(60, 60, 60))
            c = shell.mean(axis=0)
            label = pid + ("" if pid in placed else " (unplaced)")
            d.text(px(c), label, fill=(0, 0, 0))
        # panel outlines
        for pid, (outer, holes, poly) in rs["panels"].items():
            d.line(poly_px(np.asarray(poly.exterior.coords)), fill=(120, 120, 120), width=1)
        # seams
        for seam in rs["seams"]:
            raw = seam.drawn
            chord = LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)])
            for pid, (outer, holes, poly) in rs["panels"].items():
                relevant = seam.panel_id == pid or (seam.panel_id is None and chord.intersects(poly))
                if not relevant:
                    continue
                reach = float(np.hypot(*(np.asarray(poly.bounds[2:]) - np.asarray(poly.bounds[:2])))) + 10.0
                ext = seam.extended(reach).intersection(poly)
                parts = [ext] if ext.geom_type == "LineString" else list(getattr(ext, "geoms", []))
                for part in parts:
                    if part.geom_type != "LineString" or part.is_empty:
                        continue
                    cs = np.asarray(part.coords)
                    d.line([px(cs[0]), px(cs[-1])], fill=(255, 140, 0), width=2)
            d.line([px(raw[:2]), px(raw[2:])], fill=(220, 0, 0), width=5)
            d.line([px((seam.x1, seam.y1)), px((seam.x2, seam.y2))], fill=(0, 60, 220), width=2)
            m = ((seam.x1 + seam.x2) / 2, (seam.y1 + seam.y2) / 2)
            d.text((px(m)[0] + 4, px(m)[1] + 4), seam.seam_id, fill=(160, 0, 0))
        # axis
        fr = rs["frame"]
        if fr.axis is not None:
            o = np.array([mn[0] + 200, mn[1] + 200])
            a = o + fr.axis * 400 * (fr.bow_sign or 1)
            d.line([px(o), px(a)], fill=(0, 140, 0), width=3)
            d.text(px(a), "bow / along", fill=(0, 100, 0))
            t = o + np.array([-fr.axis[1], fr.axis[0]]) * 250
            d.line([px(o), px(t)], fill=(140, 0, 140), width=3)
            d.text(px(t), "across", fill=(100, 0, 100))
        d.text((10, 10), f"set {name}: red=raw drawing, blue=as-cut chord, orange=extended cut inside panel; "
                         f"pink fill=oversize; {plan['status']} {plan['piece_count']} pieces {plan['summary']['sheet_count']} sheets",
               fill=(0, 0, 0))
        out = PNG / f"placed_{name}.png"
        img.save(out)
        print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main(sys.argv[1:] or list(SETS))
