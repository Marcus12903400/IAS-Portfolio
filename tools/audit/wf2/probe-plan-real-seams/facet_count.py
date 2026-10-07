"""How much of each exported piece's outline is 1 mm facets instead of
LINE/ARC geometry?  Counts, per piece in the written sheet DXFs, the straight
polyline segments shorter than 1.5 mm, their total length, and (from the
production re-split) whether they trace a hole loop of the panel -- which is
what sheets._rebuild_ring does with ring segments owned by a non-dominant loop
(a cut-out edge that became part of a piece's outer boundary after the seam
cut through the cut-out).

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/facet_count.py [A B C D]
"""
import sys

import ezdxf
import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from common import SETS, OUT, copy_dir, load_json, resplit
from autodeck2 import nesting, sheets
from autodeck2.config import load_config


def main(names):
    config = load_config()
    options = sheets.settings(config)
    for name in names:
        work = copy_dir(name)
        plan = load_json(OUT / f"{name}_plan.json")
        rs = resplit(work, plan, options)
        print("=" * 110)
        print(f"SET {name}")
        for f in sorted(work.glob("sheet_*.dxf")):
            doc = ezdxf.readfile(str(f))
            size_kb = f.stat().st_size / 1024
            per_layer = {}
            for e in doc.modelspace().query("LWPOLYLINE"):
                layer = str(e.dxf.layer)
                if not layer.startswith("CAM__"):
                    continue
                pts = np.asarray([(x, y, b) for x, y, b in e.get_points("xyb")], float)
                n = len(pts)
                short = 0; short_len = 0.0; arcs = 0; total = 0.0
                for i in range(n):
                    p0 = pts[i, :2]; p1 = pts[(i + 1) % n, :2]; b = pts[i, 2]
                    L = float(np.hypot(*(p1 - p0)))
                    total += L
                    if abs(b) > 1e-12:
                        arcs += 1
                    elif L < 1.5:
                        short += 1; short_len += L
                d = per_layer.setdefault(layer, dict(verts=0, short=0, short_len=0.0, arcs=0, total=0.0, loops=0))
                d["verts"] += n; d["short"] += short; d["short_len"] += short_len; d["arcs"] += arcs; d["total"] += total; d["loops"] += 1
            print(f"  {f.name} ({size_kb:.0f} KB)")
            for layer, d in per_layer.items():
                pid = next((p for p in rs["pieces"] if nesting._layer("CAM", p) == layer), layer)
                flag = "  <-- faceted" if d["short"] > 50 else ""
                print(f"    {pid:<8} loops {d['loops']} verts {d['verts']:5d}  arcs {d['arcs']:4d}  straight segs < 1.5 mm: {d['short']:5d} "
                      f"({d['short_len']:.0f} mm of {d['total']:.0f} mm perimeter = {d['short_len'] / d['total'] * 100:.1f}%){flag}")
        # which original loops those facets trace (placed frame, production pieces)
        for pid, piece in sorted(rs["pieces"].items()):
            outer_l, holes_l, panel_poly = rs["panels"][piece.panel_id]
            xy, bl = piece.outer.xy, piece.outer.bulges
            n = len(xy)
            short_mids = []
            for i in range(n):
                p0 = xy[i]; p1 = xy[(i + 1) % n]
                L = float(np.hypot(*(p1 - p0)))
                if abs(bl[i]) < 1e-12 and L < 1.5:
                    short_mids.append((p0 + p1) / 2.0)
            if len(short_mids) < 50:
                continue
            hole_lines = [LineString(np.vstack([sheets.sample_loop(h, 1.0)[0], sheets.sample_loop(h, 1.0)[0][:1]])) for h in holes_l]
            counts = [0] * len(holes_l)
            other = 0
            for m in short_mids:
                dists = [hl.distance(Point(m)) for hl in hole_lines]
                j = int(np.argmin(dists)) if dists else -1
                if j >= 0 and dists[j] <= 0.75:
                    counts[j] += 1
                else:
                    other += 1
            print(f"  {pid}: {len(short_mids)} facets; on hole loops {counts} (hole index -> count; hole sizes "
                  f"{[tuple(round(v) for v in (sheets.loop_polygon(h, [], 1.0).bounds[2] - sheets.loop_polygon(h, [], 1.0).bounds[0], sheets.loop_polygon(h, [], 1.0).bounds[3] - sheets.loop_polygon(h, [], 1.0).bounds[1])) for h in holes_l]}), elsewhere {other}")


if __name__ == "__main__":
    main(sys.argv[1:] or list(SETS))
