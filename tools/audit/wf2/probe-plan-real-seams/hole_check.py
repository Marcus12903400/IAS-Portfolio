"""Confirm why the round cut-out vanishes from a piece that fully contains it:
rebuild each interior ring of the raw shapely part with the PRODUCTION
sheets._rebuild_ring and count the vertices, then apply split_panel's own
`len(rebuilt.vertices) >= 3` test.  Also lists every original hole loop of the
panel with its vertex count and bulges, and what the written DXF holds.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/hole_check.py D P1-3 A P1-1 B P1-1
"""
import sys

import ezdxf
import numpy as np

from common import OUT, copy_dir, load_json, resplit
from area_diff import raw_parts
from autodeck2 import nesting, sheets
from autodeck2.config import load_config


def main(args):
    config = load_config()
    options = sheets.settings(config)
    step = float(options["sample_step_mm"])
    tol = float(options["arc_rebuild_tolerance_mm"])
    for name, pid in zip(args[0::2], args[1::2]):
        work = copy_dir(name)
        plan = load_json(OUT / f"{name}_plan.json")
        rs = resplit(work, plan, options)
        piece = rs["pieces"][pid]
        outer_l, holes_l, panel_poly = rs["panels"][piece.panel_id]
        raw = raw_parts(rs, options)[pid]
        print("=" * 110)
        print(f"SET {name} {pid}: raw part has {len(raw.interiors)} interior ring(s); production piece has {len(piece.holes)} hole(s); "
              f"plan json says holes={next(p['holes'] for p in plan['pieces'] if p['piece_id'] == pid)}")
        print(f"  original hole loops of panel {piece.panel_id}: {len(holes_l)}")
        for i, h in enumerate(holes_l):
            v = h.vertices
            poly = sheets.loop_polygon(h, [], step)
            print(f"    hole {i}: {len(v)} vertices, bulges {np.round(v[:, 2], 4).tolist()}, bbox {tuple(round(float(x), 1) for x in poly.bounds)}, "
                  f"area {poly.area:.1f} mm2, size {poly.bounds[2] - poly.bounds[0]:.1f} x {poly.bounds[3] - poly.bounds[1]:.1f}")
        sources = []
        for loop in [outer_l, *holes_l]:
            pts, src = sheets.sample_loop(loop, step)
            sources.append((loop, pts, src))
        for i, ring in enumerate(raw.interiors):
            coords = np.asarray(ring.coords)[:-1]
            rebuilt, err = sheets._rebuild_ring(coords, sources, tol, step)
            minx, miny, maxx, maxy = ring.bounds
            kept = len(rebuilt.vertices) >= 3
            print(f"  raw interior {i}: {len(coords)} ring points, bbox ({minx:.1f},{miny:.1f})-({maxx:.1f},{maxy:.1f}), "
                  f"size {maxx - minx:.1f} x {maxy - miny:.1f} -> _rebuild_ring gives {len(rebuilt.vertices)} vertices "
                  f"(bulges {np.round(rebuilt.bulges, 4).tolist() if len(rebuilt.vertices) else []}), arc err {err:.4f}; "
                  f"split_panel keeps it (>= 3 vertices): {kept}{'   <-- HOLE DROPPED' if not kept else ''}")
        # what is in the written DXF for this piece
        layer = nesting._layer("CAM", pid)
        for f in sorted(work.glob("sheet_*.dxf")):
            doc = ezdxf.readfile(str(f))
            loops = [e for e in doc.modelspace().query("LWPOLYLINE") if str(e.dxf.layer) == layer]
            if loops:
                print(f"  {f.name}: layer {layer} holds {len(loops)} closed polyline(s) "
                      f"(vertex counts {[len(list(e.get_points())) for e in loops]})")


if __name__ == "__main__":
    main(sys.argv[1:])
