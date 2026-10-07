"""Probe 1b: why three pairs ended up 3-17 um closer than part_spacing_mm.

Hypothesis: `_commit` grows the occupancy with `placed.buffer(spacing,
join_style=1)`; shapely approximates the round join with quad_segs=8 chords,
and a chord of an 11.25 degree arc at r=20 sits 20*(1-cos(5.625 deg)) =
0.096 mm INSIDE the true offset.  A later piece can therefore come within
19.904 mm of a convex corner of an earlier one.  Usage: probe1b_clearance.py
"""

import math

import numpy as np
import shapely
from shapely import affinity
from shapely.ops import nearest_points, unary_union

from probe_common import OPTIONS, OUT, build_pieces, dump_json, load_run, nesting, placed_polygon

run = load_run()
rotation = run["rotation"]
cases = [("random", 16, "P1-6", "P3-2"), ("random", 25, "P1-4", "P4"), ("fitted", 2, "P1-1", "P1-11")]
out = []
print(f"theoretical shortfall of an 8-segment round buffer at r=20: {20 * (1 - math.cos(math.radians(90 / 8 / 2))):.4f} mm")
for family, seed, a_id, b_id in cases:
    seams, pieces, _w = build_pieces(seed, family)
    sheet_list, summary, _warn = nesting.nest(pieces, rotation, OPTIONS)
    by_id = {p.piece_id: p for p in pieces}
    placements = {pl.piece_id: pl for s in sheet_list for pl in s.placements}
    order = [pl.piece_id for s in sheet_list for pl in s.placements]
    a, b = placed_polygon(by_id[a_id], placements[a_id], rotation), placed_polygon(by_id[b_id], placements[b_id], rotation)
    first, second = (a_id, b_id) if order.index(a_id) < order.index(b_id) else (b_id, a_id)
    earlier = a if first == a_id else b
    later = b if first == a_id else a
    d = earlier.distance(later)
    p_e, p_l = nearest_points(earlier, later)
    # is the nearest point on the earlier piece a vertex (corner)?
    verts = np.asarray(earlier.exterior.coords)
    nearest_vertex = float(np.min(np.hypot(verts[:, 0] - p_e.x, verts[:, 1] - p_e.y)))
    # the real buffer the nester used, and its local shortfall
    buffered = earlier.buffer(20.0, join_style=1)
    local = float(buffered.exterior.distance(p_e))
    # interior angle at that corner
    idx = int(np.argmin(np.hypot(verts[:, 0] - p_e.x, verts[:, 1] - p_e.y)))
    prev_pt, next_pt = verts[idx - 1], verts[(idx + 1) % (len(verts) - 1)]
    v1, v2 = prev_pt - verts[idx], next_pt - verts[idx]
    angle = math.degrees(math.acos(np.clip(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)), -1, 1)))
    row = {"family": family, "seed": seed, "earlier": first, "later": second, "distance_mm": d,
           "shortfall_um": (20.0 - d) * 1000.0, "nearest_point_on_earlier": (p_e.x, p_e.y),
           "nearest_vertex_distance_mm": nearest_vertex, "buffer_radius_at_point_mm": local,
           "corner_angle_deg": angle, "later_rotation": placements[second].rotation_deg}
    out.append(row)
    print(f"{family} seed {seed}: {first} placed before {second}; distance {d:.6f} mm (short by {(20 - d) * 1000:.1f} um); "
          f"nearest point on {first} is {nearest_vertex:.4f} mm from a vertex with corner angle {angle:.1f} deg; "
          f"the nester's buffered occupancy reaches only {local:.4f} mm from that point; later piece rotation {placements[second].rotation_deg}")

# how short can the production buffer fall around every real piece?  Sample the
# true 20 mm offset (quad_segs=64) against the one the nester uses (default 8).
worst = 0.0
worst_id = None
for family, seed in (("random", 25), ("fitted", 2)):
    seams, pieces, _w = build_pieces(seed, family)
    for piece in pieces:
        poly = nesting._piece_polygon(*nesting._sampled_rings(piece, 1.0), rotation)
        coarse = poly.buffer(20.0, join_style=1)
        fine = poly.buffer(20.0, join_style=1, quad_segs=64)
        gap = float(fine.difference(coarse).area)
        pts = np.asarray(fine.exterior.coords)
        inside = float(np.max(shapely.distance(shapely.points(pts), coarse.exterior)))
        if inside > worst:
            worst, worst_id = inside, (family, seed, piece.piece_id)
print(f"largest radial shortfall of the 8-segment buffer vs a 64-segment one on real pieces: {worst:.4f} mm at {worst_id}")
out.append({"worst_buffer_shortfall_mm": worst, "at": worst_id})
dump_json(OUT / "probe1b_clearance.json", out)
