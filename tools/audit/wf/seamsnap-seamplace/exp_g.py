import json, math, re
from pathlib import Path
import numpy as np
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import unary_union
from autodeck2 import sheets, seamplace, seamsnap, sheetjob
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/seamsnap-seamplace/axisrun")
opts = sheets.settings(load_config())
loops = sheets.read_fitted_dxf(S / "final_auto.dxf")[0]
polys = seamplace.panel_polygons(loops, opts)
frame = sheetjob.boat_axis(S)
along, across = seamsnap.master_directions(frame.axis)
edge_refs = seamsnap.references_from_loops(loops, opts)


def settled(segments, unit, references):
    """bridge._settled, verbatim logic."""
    out = []
    for segment in segments:
        result = seamsnap.snap_seam(segment["x1"], segment["y1"], segment["x2"], segment["y2"], references, opts, direction_locked=True)
        if not result.applied:
            out.append(segment)
            continue
        middle = np.array([(result.x1 + result.x2) / 2.0, (result.y1 + result.y2) / 2.0])
        again = seamplace.seam_through(middle, unit, polys, opts, panel_ids=[int(segment["panel_id"])])
        if again:
            out.append(min(again, key=lambda s: math.hypot((s["x1"] + s["x2"]) / 2 - middle[0], (s["y1"] + s["y2"]) / 2 - middle[1])))
        else:
            out.append(segment)
    return out


def segment_under(segments, p):
    """app.js segmentUnder: 2 mm grace along the chord's parameter only."""
    best, best_gap = -1, math.inf
    for i, s in enumerate(segments):
        dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
        L2 = dx * dx + dy * dy
        if L2 < 1e-12:
            continue
        t = ((p[0] - s["x1"]) * dx + (p[1] - s["y1"]) * dy) / L2
        gap = (-t if t < 0 else (t - 1 if t > 1 else 0)) * math.sqrt(L2)
        if gap < best_gap:
            best_gap, best = gap, i
    return best if best_gap <= 2.0 else -1


print("== g1: full hover replay (seam_through -> _settled -> segmentUnder) over a grid: pointer ON the panel but nothing/elsewhere previewed")
for pid, poly in sorted(polys.items()):
    minx, miny, maxx, maxy = poly.bounds
    step = 60.0 if (maxx - minx) > 1500 else 30.0
    xs = np.arange(minx + 5, maxx, step)
    ys = np.arange(miny + 5, maxy, step)
    for name, unit in (("along", along), ("across", across)):
        n = nothing = moved_far = 0
        ex_nothing = ex_far = None
        for x in xs:
            for y in ys:
                if not poly.contains(Point(x, y)):
                    continue
                n += 1
                raw = seamplace.seam_through((x, y), unit, polys, opts)
                before = segment_under(raw, (x, y))
                shown = settled(raw, unit, edge_refs)
                after = segment_under(shown, (round(x, 2), round(y, 2)))
                if before >= 0 and after < 0:
                    nothing += 1
                    if ex_nothing is None:
                        ex_nothing = (round(x, 1), round(y, 1), "hovered", tuple(round(raw[before][k], 1) for k in ("x1", "y1", "x2", "y2")), "settled", tuple(round(shown[before][k], 1) for k in ("x1", "y1", "x2", "y2")))
                elif before >= 0 and after >= 0:
                    a, b = raw[before], shown[after]
                    perp = abs(seamsnap._cross(unit, np.array([b["x1"] - x, b["y1"] - y])))
                    if perp > 25.0 + 1e-6 or abs(b["length_mm"] - a["length_mm"]) > 100:
                        moved_far += 1
                        if ex_far is None:
                            ex_far = (round(x, 1), round(y, 1), "hovered len", round(a["length_mm"]), "shown len", round(b["length_mm"]), "perp offset", round(perp, 1))
        print(f"  panel {pid} {name}: hovers={n} preview vanished after settle={nothing} {ex_nothing or ''}; previewed chord jumped (>25 mm sideways or >100 mm length change)={moved_far} {ex_far or ''}")

print("\n== g2: a seam settled onto the console wall -- what the kerf does to the console opening")
walls = sorted([r for r in edge_refs if r.kind == "line" and r.label == "panel 1 cut-out edge" and r.length_mm > 300], key=lambda r: -r.length_mm)
hole0 = Polygon(polys[1].interiors[0])
poly = polys[1]
for wall in walls[:2]:
    dirname = "along" if seamsnap._direction_angle_deg(wall.unit, along) < seamsnap._direction_angle_deg(wall.unit, across) else "across"
    unit = along if dirname == "along" else across
    off = seamsnap._direction_angle_deg(wall.unit, unit)
    normal = np.array([-wall.unit[1], wall.unit[0]])
    # hover 10 mm to the panel side of the wall
    for sign in (1, -1):
        pt = wall.midpoint + normal * 10.0 * sign
        if poly.contains(Point(pt[0], pt[1])) and not hole0.contains(Point(pt[0], pt[1])):
            break
    raw = seamplace.seam_through(pt, unit, polys, opts, panel_ids=[1])
    idx = segment_under(raw, pt)
    shown = settled(raw, unit, edge_refs)
    seg = shown[idx] if idx >= 0 else None
    res = seamsnap.snap_seam(raw[idx]["x1"], raw[idx]["y1"], raw[idx]["x2"], raw[idx]["y2"], edge_refs, opts, direction_locked=True)
    print(f"  wall {wall.length_mm:.0f} mm is {off:.3f} deg off {dirname}; hover 10 mm beside it -> note={res.note!r}; shown chord len {None if seg is None else round(seg['length_mm'])}")
    if seg is None:
        continue
    seam = sheets.Seam("w", seg["x1"], seg["y1"], seg["x2"], seg["y2"], panel_id=1, mode=dirname, raw=(seg["x1"], seg["y1"], seg["x2"], seg["y2"]))
    outer, holes = sheets.classify_loops(loops[1], opts["sample_step_mm"])
    pieces, warn = sheets.split_panel(1, outer, holes, [seam], opts)
    union = unary_union([p.polygon(opts["sample_step_mm"]) for p in pieces])
    # probe points along the wall, stepped into the panel by d mm: are they still decking?
    probes = {}
    for d in (0.5, 1.5, 2.5, 3.5, 4.5, 6.5):
        inside = 0; total = 0
        for t in np.linspace(0.05, 0.95, 40):
            q = wall.p0 + (wall.p1 - wall.p0) * t
            for sgn in (1, -1):
                qq = q + normal * d * sgn
                if poly.contains(Point(qq[0], qq[1])) and not hole0.contains(Point(qq[0], qq[1])):
                    total += 1
                    inside += union.contains(Point(qq[0], qq[1]))
        probes[d] = f"{inside}/{total}"
    print(f"     split_panel -> {len(pieces)} pieces {warn}; decking still present at distance d from the fitted wall: {probes}")

print("\n== g3: NaN / odd axes")
for ax in ([float('nan'), float('nan')], [1e-300, 0.0], [float('inf'), 0.0]):
    m = seamsnap.master_directions(ax)
    d = seamplace.direction_for(ax, "across")
    segs = seamplace.seam_through(polys[1].representative_point().coords[0], d, polys, opts) if d is not None else None
    print(f"  axis {ax}: masters={m} direction_for={d} seam_through={'None' if segs is None else len(segs)}")
o = {**opts, "grain_angle_deg": float('nan')}
fr, w = sheetjob.resolve_frame(S, o)
print(f"  resolve_frame(grain nan): axis={fr.axis} warnings={w[:1]}")

print("\n== g4: can a panel-1 hover slide onto a SEAM stored on panel 2 (nest-layout neighbours)?")
stored = [sheets.Seam.from_dict(d) for d in json.load(open(S / "seams_optimiser_run.json.bak"))["seams"]]
seam_refs = [seamsnap.Reference("seam", f"seam {s.seam_id}", s.panel_id, np.array([s.x1, s.y1]), np.array([s.x2, s.y2])) for s in stored]
refs = edge_refs + seam_refs
hits = {}
poly = polys[1]
for x in np.arange(poly.bounds[0] + 5, poly.bounds[2], 20.0):
    for y in np.arange(poly.bounds[1] + 5, poly.bounds[3], 20.0):
        if not poly.contains(Point(x, y)):
            continue
        for name, unit in (("along", along), ("across", across)):
            for s in seamplace.seam_through((x, y), unit, polys, opts, panel_ids=[1]):
                r = seamsnap.snap_seam(s["x1"], s["y1"], s["x2"], s["y2"], refs, opts, direction_locked=True)
                if r.applied and r.reference_label.startswith("seam auto-2") or (r.applied and r.reference_label.startswith("seam auto-3")):
                    hits.setdefault(r.reference_label, []).append((round(x), round(y), name, r.note))
for label, rows in hits.items():
    print(f"  {label}: {len(rows)} panel-1 chords slid onto it, e.g. {rows[:2]}")
if not hits:
    print("  none: panel-2/3 seams are out of reach of every panel-1 chord on this layout")
print("  distance panel 1 -> seam auto-2-across-1 segment:", round(poly.distance(LineString([(stored[4].x1, stored[4].y1), (stored[4].x2, stored[4].y2)])), 1), "mm; -> auto-3-across-1:", round(poly.distance(LineString([(stored[5].x1, stored[5].y1), (stored[5].x2, stored[5].y2)])), 1), "mm (reach limit", opts["seam_snap_reach_mm"], ")")
