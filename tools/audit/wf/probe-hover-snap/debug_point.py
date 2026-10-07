"""Print everything a hover does for one point:  python debug_point.py X Y mode [angle]"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import line_distance, load, segment_under

x, y, mode = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
angle = float(sys.argv[4]) if len(sys.argv) > 4 else None
ctx = load(log=lambda m: None)
bridge, view, options, polygons, seamsnap, seamplace = (ctx["bridge"], ctx["view"], ctx["options"], ctx["polygons"],
                                                        ctx["seamsnap"], ctx["seamplace"])
pid = next((p for p, poly in polygons.items() if poly.contains(Point(x, y))), None)
print(f"point ({x}, {y}) is in panel {pid}; distance to its boundary "
      f"{polygons[pid].boundary.distance(Point(x, y)) if pid else None}")
refs = bridge.edge_references(view, options) + bridge.seam_references(view, options)
for label, overrides in (("raw", {"seam_snap_enabled": False}), ("settled", {})):
    r = bridge.seam_hover(view, overrides, mode, angle, point_flat=[x, y], with_world=False)
    segs = r.get("segments") or []
    under = segment_under(r.get("point_flat"), segs)
    print(f"\n{label}: {len(segs)} segment(s), previewed index {under}, reason {r.get('reason')!r}")
    for i, s in enumerate(segs):
        d = line_distance((x, y), s)
        print(f"  [{i}] panel {s['panel_id']} ({s['x1']:.2f},{s['y1']:.2f})-({s['x2']:.2f},{s['y2']:.2f}) "
              f"{s['length_mm']:.1f} mm, line {d:.3f} mm from pointer")
        if label == "raw":
            res = seamsnap.snap_seam(s["x1"], s["y1"], s["x2"], s["y2"], refs, options, direction_locked=True)
            if res.applied:
                print(f"      -> settle: {res.note!r} (moved {res.moved_mm:.3f} mm)")
