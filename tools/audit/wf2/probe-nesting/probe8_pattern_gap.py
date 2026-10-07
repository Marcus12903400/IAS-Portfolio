"""Probe 8: the band without teak lines inside P1-3 of fitted seed 14, sheet 3.
Is it a nesting/export defect (lines lost in clipping) or is the gap already in
final_auto.dxf's PATTERN lines for panel 1?"""

import ezdxf
import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from probe_common import OPTIONS, OUT, STEP, build_pieces, dump_json, load_run, nesting, placeable, sheets

run = load_run()
R = run["rotation"]
seams, pieces, _w = build_pieces(14, "fitted")
fit = placeable(pieces, R)
sheet_list, summary, _ = nesting.nest(fit, R, OPTIONS)
by_id = {p.piece_id: p for p in fit}
pl = next(p for s in sheet_list for p in s.placements if p.piece_id == "P1-3")
piece = by_id["P1-3"]
w, l = sheets.oriented_extent(piece, R, STEP)
print(f"P1-3: {w:.0f} x {l:.0f} mm, holes={len(piece.holes)}, placed on sheet {pl.sheet_index + 1} rot {pl.rotation_deg}")

# 1. pattern coverage in the PLACED frame straight from the source DXF (before export)
shell, _s = sheets.sample_loop(piece.outer, STEP)
piece_poly = Polygon(shell)
grooves = run["pattern"][1]
inside = [LineString(g).intersection(piece_poly) for g in grooves]
inside = [g for g in inside if not g.is_empty]
print(f"source pattern segments for panel 1: {len(grooves)}; segments touching P1-3: {len(inside)}")
# the along-boat coordinate of every groove endpoint inside the piece, projected on the axis
along = np.asarray(run["axis"], dtype=float)
across = np.array([along[1], -along[0]])
ends = []
for g in inside:
    for part in getattr(g, "geoms", [g]):
        c = np.asarray(part.coords)
        if len(c) >= 2:
            ends.append((float(np.dot(c[0], across)), float(np.dot(c[0], along)), float(np.dot(c[-1], along))))
ends.sort()
print("per groove (across offset -> along span inside P1-3):")
for off, a0, a1 in ends:
    print(f"  across {off:8.1f}: along {min(a0, a1):8.1f} .. {max(a0, a1):8.1f}  (len {abs(a1 - a0):6.1f})")
piece_along = shell @ along
print(f"P1-3 spans along {piece_along.min():.1f} .. {piece_along.max():.1f}")

# 2. the same from the exported sheet DXF
doc = ezdxf.readfile(str(OUT / "export_fitted_014" / f"sheet_{pl.sheet_index + 1:02d}.dxf"))
lines = [e for e in doc.modelspace().query("LINE") if str(e.dxf.layer) == nesting._layer("PATTERN_TEAK", "P1-3")]
print(f"exported PATTERN_TEAK__P1_3 lines: {len(lines)}")
spans = sorted((round(e.dxf.start.x, 1), round(min(e.dxf.start.y, e.dxf.end.y), 1), round(max(e.dxf.start.y, e.dxf.end.y), 1)) for e in lines)
for s in spans:
    print("  x=%7.1f  y %7.1f .. %7.1f" % s)

# 3. where are the source grooves broken?  gaps between consecutive segments on the same groove line
by_offset = {}
for g in grooves:
    off = round(float(np.dot(g[0], across)), 1)
    by_offset.setdefault(off, []).append(sorted([float(np.dot(g[0], along)), float(np.dot(g[1], along))]))
print("\nsource grooves of panel 1 with more than one segment (broken around cut-outs):")
for off in sorted(by_offset):
    segs = sorted(by_offset[off])
    if len(segs) > 1:
        gaps = [(round(segs[i][1], 1), round(segs[i + 1][0], 1)) for i in range(len(segs) - 1)]
        print(f"  across {off:8.1f}: {len(segs)} segments, gaps at along {gaps}")
dump_json(OUT / "probe8_pattern_gap.json", {"piece": "P1-3", "size": (w, l), "ends": ends, "exported_spans": spans})
