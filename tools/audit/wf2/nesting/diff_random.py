"""Randomised differential test of the committed vs new nester on synthetic pieces (rects, Ls, holes, concave)."""
import sys, time
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
sys.path.insert(0, str(S))
import nesting_old
from autodeck2 import nesting, sheets
from autodeck2.config import load_config
from shapely.geometry import Polygon
from shapely import affinity

def layout(sheet_list):
    return tuple((p.sheet_index, p.piece_id, p.rotation_deg, float(p.offset[0]), float(p.offset[1]),
                  float(p.origin[0]), float(p.origin[1]), p.width_mm, p.length_mm) for s in sheet_list for p in s.placements)

def loop(pts, bulges=None):
    pts = np.asarray(pts, float)
    b = np.zeros(len(pts)) if bulges is None else np.asarray(bulges, float)
    return sheets.Loop(np.column_stack([pts, b]))

def rect(w, l, x=0.0, y=0.0):
    return loop([[x, y], [x + w, y], [x + w, y + l], [x, y + l]])

def lshape(w, l, nw, nl, x=0.0, y=0.0):
    return loop([[x, y], [x + w, y], [x + w, y + l - nl], [x + w - nw, y + l - nl], [x + w - nw, y + l], [x, y + l]])

def star(cx, cy, r1, r2, n):
    ang = np.linspace(0, 2 * np.pi, 2 * n, endpoint=False)
    rad = np.where(np.arange(2 * n) % 2 == 0, r1, r2)
    return loop(np.column_stack([cx + rad * np.cos(ang), cy + rad * np.sin(ang)]))

def rounded(w, l, x=0.0, y=0.0):
    # a rectangle with bulged (arc) short ends
    return loop([[x, y], [x + w, y], [x + w, y + l], [x, y + l]], [0.0, 0.35, 0.0, 0.35])

def make_piece(rng, pid, scale):
    kind = rng.integers(0, 6)
    ox, oy = rng.uniform(-800, 800, 2)
    w, l = rng.uniform(40, 260, 2) * scale
    holes = []
    if kind == 0:
        outer = rect(w, l, ox, oy)
    elif kind == 1:
        outer = lshape(w, l, w * rng.uniform(0.3, 0.7), l * rng.uniform(0.3, 0.7), ox, oy)
    elif kind == 2:
        outer = rect(w, l, ox, oy)
        if w > 80 and l > 80:
            holes = [rect(w * 0.3, l * 0.3, ox + w * 0.35, oy + l * 0.35)]
    elif kind == 3:
        outer = star(ox, oy, max(w, l) / 2, max(w, l) / 4, int(rng.integers(5, 9)))
    elif kind == 4:
        outer = rounded(w, l, ox, oy)
    else:
        outer = lshape(w, l, w * 0.5, l * 0.5, ox, oy)
        if w > 100 and l > 100:
            holes = [rect(w * 0.2, l * 0.2, ox + w * 0.1, oy + l * 0.1)]
    area = Polygon(sheets.sample_loop(outer, 1.0)[0]).area - sum(Polygon(sheets.sample_loop(h, 1.0)[0]).area for h in holes)
    return sheets.Piece(pid, 1, outer, holes, float(area))

base = load_config()
def options(**ov):
    return sheets.settings({**base, "sheets": {**(base.get("sheets") or {}), **ov}})

rng = np.random.default_rng(12345)
mismatch = 0
trials = 0
t_old = t_new = 0.0
for trial in range(0):
    n = int(rng.integers(3, 12))
    scale = rng.choice([1.0, 1.0, 2.0, 0.5])
    pieces = [make_piece(rng, f"R{trial}-{i}", scale) for i in range(n)]
    ang = rng.uniform(0, 2 * np.pi)
    rot = sheets.sheet_transform(np.array([np.cos(ang), np.sin(ang)]))
    grid = float(rng.choice([5.0, 3.0, 7.3]))
    spacing = float(rng.choice([20.0, 10.0, 0.0, 3.3]))
    small = True
    opts = options(nest_step_mm=grid, part_spacing_mm=spacing,
                   **({"sheet_width_mm": 500.0, "sheet_length_mm": 700.0, "max_part_width_mm": 480.0, "max_part_length_mm": 680.0} if small else {}),
                   allow_180_rotation=bool(rng.random() < 0.8))
    a = time.perf_counter(); old = nesting_old.nest(pieces, rot, opts); b = time.perf_counter()
    new = nesting.nest(pieces, rot, opts); c = time.perf_counter()
    t_old += b - a; t_new += c - b
    trials += 1
    if trial % 10 == 0: print('  progress trial', trial, flush=True)
    if layout(old[0]) != layout(new[0]) or old[1] != new[1] or old[2] != new[2]:
        mismatch += 1
        print("MISMATCH trial", trial, "grid", grid, "spacing", spacing, "small", small)
        lo, ln = layout(old[0]), layout(new[0])
        for i in range(max(len(lo), len(ln))):
            if i >= len(lo) or i >= len(ln) or lo[i] != ln[i]:
                print("  OLD", lo[i] if i < len(lo) else None); print("  NEW", ln[i] if i < len(ln) else None)
                break
print(f"trials={trials} mismatches={mismatch} old={t_old:.1f}s new={t_new:.1f}s")

# ---- direct _find_spot differential against DENSE occupancy: every piece against every final sheet region
print("== _find_spot old vs new against the final occupancy of each trial ==")
rng = np.random.default_rng(777)
checked = bad = 0
for trial in range(12):
    n = int(rng.integers(6, 12))
    pieces = [make_piece(rng, f"D{trial}-{i}", 0.6) for i in range(n)]
    ang = rng.uniform(0, 2 * np.pi)
    rot = sheets.sheet_transform(np.array([np.cos(ang), np.sin(ang)]))
    grid = float(rng.choice([5.0, 3.0]))
    spacing = float(rng.choice([20.0, 10.0, 0.0, 3.3]))
    opts = options(nest_step_mm=grid, part_spacing_mm=spacing, sheet_width_mm=500.0, sheet_length_mm=700.0,
                   max_part_width_mm=480.0, max_part_length_mm=680.0)
    # build occupancy with the OLD nester's own commit path
    uw, ul = opts["max_part_width_mm"], opts["max_part_length_mm"]
    old_sheets, _, _ = nesting_old.nest(pieces, rot, opts)
    occ = [None] * len(old_sheets)
    for s in old_sheets:
        for p in s.placements:
            pc = next(x for x in pieces if x.piece_id == p.piece_id)
            poly = nesting_old._piece_polygon(pc, nesting_old._rotation(p.rotation_deg) @ rot, opts["sample_step_mm"])
            poly = affinity.translate(poly, -p.origin[0] + p.offset[0] - (opts["sheet_width_mm"] - uw) / 2, -p.origin[1] + p.offset[1] - (opts["sheet_length_mm"] - ul) / 2)
            clear = poly.buffer(spacing, join_style=1)
            occ[s.index] = clear if occ[s.index] is None else occ[s.index].union(clear)
    for pc in pieces:
        shell, holes = nesting._sampled_rings(pc, opts["sample_step_mm"])
        new_v, old_v = {}, {}
        for a in (0, 180):
            poly_new = nesting._piece_polygon(shell, holes, nesting._rotation(a) @ rot)
            minx, miny, maxx, maxy = poly_new.bounds
            moved = affinity.translate(poly_new, -minx, -miny)
            v = nesting._Variant(moved, maxx - minx, maxy - miny, moved.bounds); v.probes = nesting._probes(moved, nesting._PROBE_LEVELS)
            new_v[a] = v
            poly_old = nesting_old._piece_polygon(pc, nesting_old._rotation(a) @ rot, opts["sample_step_mm"])
            minx, miny, maxx, maxy = poly_old.bounds
            old_v[a] = (affinity.translate(poly_old, -minx, -miny), maxx - minx, maxy - miny)
        for region in occ:
            if region is None: continue
            o = nesting_old._find_spot(old_v, region, uw, ul, grid, (0, 180))
            nw = nesting._find_spot(new_v, region, uw, ul, grid, (0, 180))
            checked += 1
            if o != nw:
                bad += 1
                print("   MISMATCH", trial, pc.piece_id, o, nw)
print(f"_find_spot comparisons={checked} mismatches={bad}")
