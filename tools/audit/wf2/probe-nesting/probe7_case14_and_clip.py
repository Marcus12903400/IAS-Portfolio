"""Probe 7a: fitted seed 14 -- P1-9 (514 x 416 mm) ends up alone on sheet 5.
Replay the nest order and, at P1-9's turn, measure the free space on each
earlier sheet: free area, and the largest axis-aligned free rectangle (10 mm
grid), against the 534 x 436 mm (piece + spacing) it needed.
Probe 7b: `write_sheet_dxfs` pattern clipping drops a groove when the
LineString/Polygon intersection comes back as a GeometryCollection."""

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.ops import unary_union

from probe_common import (MARGIN_X, MARGIN_Y, OPTIONS, OUT, SPACING, STEP, USABLE_L, USABLE_W, build_pieces, dump_json,
                          load_run, nesting, placeable, sheets)

run = load_run()
R = run["rotation"]
out = {}

# ---------------------------------------------------------------- 7a
seams, pieces, _w = build_pieces(14, "fitted")
fit = placeable(pieces, R)
sheet_list, summary, _ = nesting.nest(fit, R, OPTIONS)
by_id = {p.piece_id: p for p in fit}
order = sorted(fit, key=lambda p: (-p.area_mm2, p.piece_id))
print("nest order:", [(p.piece_id, round(p.area_mm2 / 1e3)) for p in order])
placement_of = {pl.piece_id: pl for s in sheet_list for pl in s.placements}


def variant_polygon(piece, pl):
    shell, holes = nesting._sampled_rings(piece, STEP)
    poly = nesting._piece_polygon(shell, holes, nesting._rotation(pl.rotation_deg) @ R)
    minx, miny, _, _ = poly.bounds
    return affinity.translate(poly, -minx + (pl.offset[0] - MARGIN_X), -miny + (pl.offset[1] - MARGIN_Y))


def largest_free_rect(free_poly, grid=10.0):
    """Largest axis-aligned rectangle inside `free_poly` (usable coords), by a
    histogram scan on a `grid` mm raster."""

    xs = np.arange(grid / 2, USABLE_W, grid)
    ys = np.arange(grid / 2, USABLE_L, grid)
    mx, my = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(free_poly, mx.ravel(), my.ravel()).reshape(len(ys), len(xs))
    best = (0, 0, 0)
    heights = np.zeros(len(xs), dtype=int)
    for row in inside:
        heights = np.where(row, heights + 1, 0)
        stack = []
        for i, h in enumerate(list(heights) + [0]):
            start = i
            while stack and stack[-1][1] >= h:
                s, sh = stack.pop()
                area = sh * (i - s)
                if area > best[0]:
                    best = (area, (i - s) * grid, sh * grid)
                start = s
            stack.append((start, h))
    return best[1], best[2]


def _variants(piece):
    shell, holes = nesting._sampled_rings(piece, STEP)
    out_v = {}
    for angle in (0, 180):
        poly = nesting._piece_polygon(shell, holes, nesting._rotation(angle) @ R)
        minx, miny, maxx, maxy = poly.bounds
        moved = affinity.translate(poly, -minx, -miny)
        v = nesting._Variant(moved, maxx - minx, maxy - miny, moved.bounds)
        v.probes = nesting._probes(moved, nesting._PROBE_LEVELS)
        out_v[angle] = v
    return out_v



target = "P1-9"
usable = box(0, 0, USABLE_W, USABLE_L)
occupancy = {}
for piece in order:
    pl = placement_of.get(piece.piece_id)
    if piece.piece_id == target:
        w, l = sheets.oriented_extent(piece, R, STEP)
        print(f"\nat {target}'s turn ({w:.0f} x {l:.0f} mm, needs {w + SPACING:.0f} x {l + SPACING:.0f} with spacing):")
        for idx in sorted(occupancy):
            occ = unary_union(occupancy[idx])
            blocked = occ.buffer(SPACING, join_style=1)
            free = usable.difference(blocked)
            fw, fl = largest_free_rect(free)
            spot = nesting._find_spot(
                {a: v for a, v in _variants(piece).items()}, blocked, USABLE_W, USABLE_L, 5.0, (0, 180))
            print(f"  sheet {idx + 1}: pieces={[p for p in occupancy_ids[idx]]} free area={free.area / 1e6:.3f} m2 "
                  f"({100 * free.area / (USABLE_W * USABLE_L):.0f}% of usable), largest free rectangle ~{fw:.0f} x {fl:.0f} mm, "
                  f"_find_spot={spot}")
            out[f"sheet{idx + 1}"] = {"pieces": occupancy_ids[idx], "free_pct": 100 * free.area / (USABLE_W * USABLE_L),
                                      "largest_free_rect": (fw, fl), "find_spot": spot}
        break
    if pl is None:
        continue
    occupancy.setdefault(pl.sheet_index, []).append(variant_polygon(piece, pl))
    globals().setdefault("occupancy_ids", {}).setdefault(pl.sheet_index, []).append(piece.piece_id)


# export + render for the eye
export_dir = OUT / "export_fitted_014"
export_dir.mkdir(exist_ok=True)
for old in export_dir.glob("sheet_*.dxf"):
    old.unlink()
nesting.write_sheet_dxfs(export_dir, sheet_list, by_id, R, run["pattern"], run["kind"], OPTIONS)
print("exported", export_dir)

# ---------------------------------------------------------------- 7b
print("\n--- pattern clipping: a groove line that also grazes a vertex from outside")
piece = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]).union(Polygon([(12, 5), (11, 7), (13, 7)]))   # square + a spike whose tip touches y=5
line = LineString([(-1, 5), (21, 5)])
clipped = line.intersection(piece)
print("intersection type:", clipped.geom_type, "->", [g.geom_type for g in getattr(clipped, "geoms", [clipped])])
kept = []
for part in (clipped.geoms if isinstance(clipped, MultiLineString) else [clipped]):      # exactly nesting.py's logic
    if part.is_empty or part.geom_type != "LineString":
        continue
    kept.append(part)
print(f"nesting.write_sheet_dxfs logic keeps {len(kept)} segment(s); the 10 mm groove inside the square is "
      f"{'LOST' if not kept else 'kept'}")
out["clip_demo"] = {"type": clipped.geom_type, "kept": len(kept)}
dump_json(OUT / "probe7_case14_and_clip.json", out)
