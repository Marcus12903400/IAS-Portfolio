"""Probe 9b: the same 2-vertex rebuild on a ROUND PART (panel 5, 187 mm) cut by
one seam, and on a round hole with a seam through it."""

import numpy as np
from shapely.geometry import Polygon

from probe_common import OPTIONS, STEP, load_run, sheets

run = load_run()
outer5, holes5, poly5 = run["panels"][5]
c = poly5.centroid
print(f"panel 5: {len(outer5.vertices)} vertices, bulges {outer5.bulges.tolist()}, centre ({c.x:.1f}, {c.y:.1f}), area {poly5.area:.0f}")
seam = sheets.Seam("s5", c.x - 300, c.y, c.x + 300, c.y, panel_id=5, snap=False)
pieces, warnings = sheets.split_panel(5, outer5, holes5, [seam], OPTIONS)
print(f"one seam through the round part -> pieces={[(p.piece_id, len(p.outer.vertices), round(p.area_mm2)) for p in pieces]} warnings={warnings}")

# a seam through the round HOLE of panel 1: the hole merges into the outline, and the
# outline's arc runs are rebuilt -- do the two half-circle arcs survive as bulges?
outer1, holes1, poly1 = run["panels"][1]
hole = next(h for h in holes1 if len(h.vertices) == 2)
hc = Polygon(sheets.sample_loop(hole, STEP)[0]).centroid
seam = sheets.Seam("through", hc.x - 1000, hc.y, hc.x + 1000, hc.y, panel_id=1, snap=False)
pieces, warnings = sheets.split_panel(1, outer1, holes1, [seam], OPTIONS)
for p in pieces:
    arcs = int(np.sum(np.abs(p.outer.bulges) > 1e-9))
    print(f"  {p.piece_id}: outer {len(p.outer.vertices)} vertices ({arcs} arcs), holes={len(p.holes)}, max_arc_error={p.max_arc_error_mm:.4f}")
print("warnings:", warnings)
