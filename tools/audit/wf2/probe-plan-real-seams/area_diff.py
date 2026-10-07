"""Where does the DXF outline of a piece differ from the polygon plan() cut?

For every piece: the raw shapely part (replicating sheets.split_panel's own
steps, same ordering so the ids match) vs the REBUILT (x, y, bulge) loop that
goes into the DXF, both sampled at 0.25 mm: area difference, symmetric
difference (area, bbox), Hausdorff distance between the outlines, and the
largest distance from any rebuilt vertex to the raw outline.  Renders a zoomed
PNG of the worst piece per set with both outlines overlaid.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/area_diff.py [A B C D]
"""
import json
import sys

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from common import SETS, OUT, PNG, copy_dir, load_json, resplit
from autodeck2 import sheets
from autodeck2.config import load_config

FINE = 0.25


def raw_parts(rs, options):
    """piece_id -> raw shapely part, exactly as split_panel orders them."""
    half_gap = float(options["seam_gap_mm"]) / 2.0
    min_area = float(options["min_piece_area_mm2"])
    out = {}
    for pid, (outer, holes, poly) in rs["panels"].items():
        relevant = []
        for seam in rs["seams"]:
            if seam.panel_id == pid:
                relevant.append(seam)
            elif seam.panel_id is None and LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)]).intersects(poly):
                relevant.append(seam)
        if not relevant:
            out[f"P{pid}"] = poly
            continue
        reach = float(np.hypot(*(np.asarray(poly.bounds[2:]) - np.asarray(poly.bounds[:2])))) + 10.0
        kerfs = [s.extended(reach).buffer(half_gap, cap_style=2, join_style=2) for s in relevant]
        rem = poly.difference(unary_union(kerfs))
        parts = list(rem.geoms) if isinstance(rem, MultiPolygon) else [rem]
        parts = [p for p in parts if p.area >= min_area]
        for index, part in enumerate(sorted(parts, key=lambda g: (-g.area, g.bounds))):
            out[f"P{pid}-{index + 1}"] = orient(part, 1.0)
    return out


def main(names):
    config = load_config()
    options = sheets.settings(config)
    for name in names:
        work = copy_dir(name)
        plan = load_json(OUT / f"{name}_plan.json")
        rs = resplit(work, plan, options)
        raws = raw_parts(rs, options)
        print("=" * 110)
        print(f"SET {name}")
        worst = None
        for pid, piece in sorted(rs["pieces"].items()):
            raw = raws.get(pid)
            if raw is None:
                print(f"  {pid}: no raw part matched")
                continue
            rebuilt = sheets.loop_polygon(piece.outer, piece.holes, FINE)
            d_area = rebuilt.area - raw.area
            sym = raw.symmetric_difference(rebuilt)
            haus = raw.boundary.hausdorff_distance(rebuilt.boundary)
            verts = np.vstack([piece.outer.xy] + [h.xy for h in piece.holes])
            vert_dev = max(raw.boundary.distance(Point(v)) for v in verts)
            flag = "  <-- " if abs(d_area) > 5.0 or haus > 0.2 else ""
            print(f"  {pid:<8} raw area {raw.area:12.1f}  rebuilt {rebuilt.area:12.1f}  diff {d_area:+10.2f} mm2 "
                  f"({d_area / raw.area * 100:+.4f}%)  symdiff {sym.area:9.2f} mm2 bbox {tuple(round(v, 1) for v in sym.bounds) if not sym.is_empty else '-'}  "
                  f"hausdorff {haus:7.3f} mm  max vertex off raw outline {vert_dev:.4f} mm  verts {len(piece.outer.vertices)}{flag}")
            if worst is None or abs(d_area) > abs(worst[1]):
                worst = (pid, d_area, raw, rebuilt, sym)
        if worst and abs(worst[1]) > 5.0:
            pid, d_area, raw, rebuilt, sym = worst
            minx, miny, maxx, maxy = sym.bounds
            pad = 40.0
            minx -= pad; miny -= pad; maxx += pad; maxy += pad
            scale = min(1400.0 / max(maxx - minx, 1e-9), 900.0 / max(maxy - miny, 1e-9), 8.0)
            W = int((maxx - minx) * scale) + 1; H = int((maxy - miny) * scale) + 1
            img = Image.new("RGB", (W, H), (255, 255, 255))
            d = ImageDraw.Draw(img)

            def px(p):
                return (float((p[0] - minx) * scale), float((maxy - p[1]) * scale))

            for geom, colour, width in ((raw, (220, 0, 0), 3), (rebuilt, (0, 60, 220), 1)):
                rings = [geom.exterior] + list(geom.interiors) if geom.geom_type == "Polygon" else \
                    [r for g in geom.geoms for r in [g.exterior] + list(g.interiors)]
                for ring in rings:
                    pts = [px(p) for p in np.asarray(ring.coords)]
                    d.line(pts, fill=colour, width=width)
            for v in np.vstack([rs["pieces"][pid].outer.xy]):
                x, y = px(v)
                d.ellipse([x - 2, y - 2, x + 2, y + 2], outline=(0, 60, 220))
            d.text((5, 5), f"set {name} {pid}: red = polygon plan() cut (raw shapely part), blue = rebuilt loop written to DXF "
                           f"(dots = its vertices); area diff {d_area:+.1f} mm2; window {maxx - minx:.0f} x {maxy - miny:.0f} mm", fill=(0, 0, 0))
            out = PNG / f"area_diff_{name}_{pid}.png"
            img.save(out)
            print(f"  wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main(sys.argv[1:] or list(SETS))
