"""How thin a 'piece' split_panel will hand to the nester: the sliver guard is by AREA only."""
import math
import numpy as np
from autodeck2 import sheets
from autodeck2.config import load_config


def rounded_rect(width, height, radius, x=0.0, y=0.0):
    b = math.tan(math.pi / 8)
    r = radius
    return sheets.Loop(np.array([[x + r, y, 0.0], [x + width - r, y, b], [x + width, y + r, 0.0], [x + width, y + height - r, b],
                                 [x + width - r, y + height, 0.0], [x + r, y + height, b], [x, y + height - r, 0.0], [x, y + r, b]], dtype=float))


opts = sheets.settings(load_config())
outer = rounded_rect(1600.0, 900.0, 120.0)
print("min_piece_area_mm2 =", opts["min_piece_area_mm2"])
for inset in (3.1, 3.2, 3.3, 3.5, 4.0, 6.0):
    seam = sheets.Seam("s", 200.0, inset, 1400.0, inset)    # parallel to the straight bottom edge, inset mm inside it
    pieces, warnings = sheets.split_panel(1, outer, [], [seam], opts)
    sizes = []
    for p in pieces:
        pts, _s = sheets.sample_loop(p.outer, opts["sample_step_mm"])
        sizes.append((round(pts[:, 0].max() - pts[:, 0].min()), round(pts[:, 1].max() - pts[:, 1].min(), 2), round(p.area_mm2)))
    print(f"seam {inset} mm inside the edge (kerf half-gap 3.0) -> {len(pieces)} piece(s): {sizes} warnings={warnings}")
