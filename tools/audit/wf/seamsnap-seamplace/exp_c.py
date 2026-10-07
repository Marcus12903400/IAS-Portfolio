import json, math, re, time
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
refs = seamsnap.references_from_loops(loops, opts)
print("references:", len(refs))

print("== c1: the run's 4 hand seams (old schema, free direction) through apply_snap")
saved = sheets.read_seams(S)
out, results, warns = sheetjob.apply_snap(saved, loops, frame.axis, opts)
for seam, snapped, r in zip(saved, out, results):
    before = seamplace.panels_crossed(seam.x1, seam.y1, seam.x2, seam.y2, polys, opts, seam.panel_id)
    after = seamplace.panels_crossed(snapped.x1, snapped.y1, snapped.x2, snapped.y2, polys, opts, snapped.panel_id)
    print(f"  {seam.seam_id}: kind={r.kind!r} angle={r.angle_change_deg:.2f} moved={r.moved_mm:.1f} note={r.note!r} panels before={before} after={after}")
print("  warnings:", warns)


def label_panel(label):
    m = re.match(r"panel (\d+)", label or "")
    return int(m.group(1)) if m else None


print("\n== c2: hover-settle emulation (seam_through chord -> snap_seam(direction_locked=True) with the fitted-edge references)")
stats = {}
examples = {}
t0 = time.time()
for pid, poly in sorted(polys.items()):
    minx, miny, maxx, maxy = poly.bounds
    step = 60.0 if (maxx - minx) > 1500 else 30.0
    xs = np.arange(minx + 5, maxx, step)
    ys = np.arange(miny + 5, maxy, step)
    for name, unit in (("along", along), ("across", across)):
        key = (pid, name)
        st = dict(hovers=0, chords=0, applied=0, other_panel=0, cutout=0, outer=0, off_part=0, on_boundary=0, shifts=[])
        for x in xs:
            for y in ys:
                if not poly.contains(Point(x, y)):
                    continue
                st["hovers"] += 1
                segs = seamplace.seam_through((x, y), unit, polys, opts, panel_ids=[pid])
                for s in segs:
                    st["chords"] += 1
                    res = seamsnap.snap_seam(s["x1"], s["y1"], s["x2"], s["y2"], refs, opts, direction_locked=True)
                    if not res.applied:
                        continue
                    st["applied"] += 1
                    st["shifts"].append(res.moved_mm)
                    lp = label_panel(res.reference_label)
                    tag = None
                    if lp is not None and lp != pid:
                        st["other_panel"] += 1
                        tag = "other_panel"
                    elif "cut-out" in res.reference_label:
                        st["cutout"] += 1
                        tag = "cutout"
                    elif "outer" in res.reference_label:
                        st["outer"] += 1
                        tag = "outer"
                    middle = np.array([(res.x1 + res.x2) / 2, (res.y1 + res.y2) / 2])
                    again = seamplace.seam_through(middle, unit, polys, opts, panel_ids=[pid])
                    chord = None
                    if not again:
                        st["off_part"] += 1
                        tag = "off_part"
                    else:
                        chord = min(again, key=lambda q: math.hypot((q["x1"] + q["x2"]) / 2 - middle[0], (q["y1"] + q["y2"]) / 2 - middle[1]))
                        line = LineString([(chord["x1"], chord["y1"]), (chord["x2"], chord["y2"])])
                        if poly.boundary.buffer(1e-3).contains(line):
                            st["on_boundary"] += 1
                            tag = "on_boundary"
                    if tag and tag not in examples.get(key, {}):
                        examples.setdefault(key, {})[tag] = dict(
                            hover=(round(x, 1), round(y, 1)),
                            hovered=(round(s["x1"], 1), round(s["y1"], 1), round(s["x2"], 1), round(s["y2"], 1), round(s["length_mm"], 1)),
                            label=res.reference_label, note=res.note, moved=round(res.moved_mm, 2),
                            settled=None if chord is None else (round(chord["x1"], 1), round(chord["y1"], 1), round(chord["x2"], 1), round(chord["y2"], 1), round(chord["length_mm"], 1)))
        st["shifts"] = (round(min(st["shifts"]), 2), round(max(st["shifts"]), 2)) if st["shifts"] else None
        stats[key] = st
        print(f"  panel {pid} {name}: {st}")
print(f"  ({time.time() - t0:.0f}s)")
for key, ex in examples.items():
    for tag, e in ex.items():
        print(f"  EXAMPLE {key} {tag}: {e}")


def cut_with(pid, seam):
    outer, holes = sheets.classify_loops(loops[pid], opts["sample_step_mm"])
    pieces, warn = sheets.split_panel(pid, outer, holes, [seam], opts)
    return pieces, warn


print("\n== c3: what a seam that settled onto a CUT-OUT edge does to the console / an OUTER edge does to the outline")
for key, ex in examples.items():
    pid, name = key
    for tag in ("cutout", "outer"):
        if tag not in ex or ex[tag]["settled"] is None:
            continue
        e = ex[tag]
        x1, y1, x2, y2, _ = e["settled"]
        seam = sheets.Seam("t", x1, y1, x2, y2, panel_id=pid, mode=name, raw=(x1, y1, x2, y2))
        poly = polys[pid]
        seam_line = LineString([(x1, y1), (x2, y2)])
        ring = min(list(poly.interiors) + [poly.exterior], key=lambda r: LineString(r.coords).distance(seam_line))
        ring_line = LineString(ring.coords)
        pieces, warn = cut_with(pid, seam)
        union = unary_union([p.polygon(opts["sample_step_mm"]) for p in pieces])
        removed = poly.difference(union)
        near = removed.intersection(ring_line.buffer(3.5))
        coincident = ring_line.intersection(seam_line.buffer(0.05)).length
        print(f"  {key} {tag}: settled seam {e['settled']} note={e['note']!r} -> pieces={len(pieces)} warn={warn} removed_total={removed.area:.0f} mm2; "
              f"removed within 3.5 mm of the nearest ring={near.area:.0f} mm2; seam coincides with {coincident:.0f} mm of that ring "
              f"(=> ~{(near.area / coincident if coincident else 0):.2f} mm shaved off the ring edge)")

print("\n== c5: a hover chord whose slide takes it OFF the part: what the click then stores and cuts")
for key, ex in examples.items():
    if "off_part" not in ex:
        continue
    pid, name = key
    e = ex["off_part"]
    x1, y1, x2, y2, L = e["hovered"]
    seam = sheets.Seam("t", x1, y1, x2, y2, panel_id=pid, mode=name, raw=(x1, y1, x2, y2))
    out, results, warns = sheetjob.apply_snap([seam], loops, frame.axis, opts)
    s2 = out[0]
    r = results[0]
    line = LineString([(s2.x1, s2.y1), (s2.x2, s2.y2)])
    pieces, warn = cut_with(pid, s2)
    print(f"  {key}: hovered {e['hovered']} (preview kept the unslid chord); click stores ({s2.x1:.1f},{s2.y1:.1f})-({s2.x2:.1f},{s2.y2:.1f}) note={r.note!r}; "
          f"stored seam is {polys[pid].distance(line):.2f} mm from panel {pid}; split_panel -> {len(pieces)} piece(s) {warn}")

print("\n== c6: other-panel straight edges within 0.5 deg of a master, and how close their SEGMENT is to each panel")
for pid, poly in sorted(polys.items()):
    for name, unit in (("along", along), ("across", across)):
        near = []
        for r in refs:
            if r.kind != "line" or r.panel_id == pid or seamsnap._direction_angle_deg(r.unit, unit) > 0.5:
                continue
            d = poly.distance(LineString([r.p0, r.p1]))
            if d <= opts["seam_snap_reach_mm"]:
                near.append((round(d, 1), r.label, round(r.length_mm)))
        near.sort()
        print(f"  panel {pid} {name}: other-panel parallel edges within reach: {near[:4]}")

print("\n== c7: exact coincidence -- a seam lying exactly on a console wall (reference line), what seam_through returns")
walls = [r for r in refs if r.kind == "line" and "cut-out" in r.label and r.length_mm > 300]
for r in walls[:4]:
    m = r.midpoint
    segs = seamplace.seam_through((m[0], m[1]), r.unit, polys, opts, panel_ids=[r.panel_id])
    poly = polys[r.panel_id]
    desc = []
    for s in segs:
        line = LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])])
        onb = poly.boundary.intersection(line.buffer(1e-6)).length
        desc.append((round(s["length_mm"]), f"{onb:.0f} mm of it on the boundary"))
    print(f"  {r.label} len={r.length_mm:.0f}: chords {desc}")
