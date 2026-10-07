"""Read-only measurements on scratch copies of the fixture runs (never the real dirs)."""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import ezdxf
from shapely.geometry import Polygon
from shapely.ops import unary_union

from autodeck2 import nesting, seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

SCRATCH = Path(sys.argv[1])
RUNS = Path("D:/AutoDeck/engine/outputs/runs")
AXIS = "21kwcockpit-1-20260901-180939"
HAND = [
    {"seam_id": "s1", "x1": 110.61724090576172, "y1": -1005.9564819335938, "x2": 86.17920684814453, "y2": -453.04376220703125, "panel_id": None},
    {"seam_id": "s2", "x1": 9.80996036529541, "y1": 451.1668701171875, "x2": -23.79237174987793, "y2": 1004.07958984375, "panel_id": None},
    {"seam_id": "s3", "x1": -1474.8062744140625, "y1": 374.7977600097656, "x2": 40.35771179199219, "y2": 484.76934814453125, "panel_id": None},
    {"seam_id": "s4", "x1": 1836.56005859375, "y1": 344.2501525878906, "x2": 1469.9881591796875, "y2": 328.97625732421875, "panel_id": None},
]


def copy_run(name, seams, tag):
    src = RUNS / name
    work = SCRATCH / tag / name
    work.mkdir(parents=True, exist_ok=True)
    for f in ("final_auto.dxf", "run.json"):
        shutil.copyfile(src / f, work / f)
    (work / "seams.json").write_text(json.dumps({"seams": seams}, indent=2), encoding="utf-8")
    return work


config = load_config()
options = sheets.settings(config)
step = options["sample_step_mm"]

# ---------------- A. hole survival through split_panel on the real deck
work = copy_run(AXIS, HAND, "holes")
loops, pattern, kind = sheets.read_fitted_dxf(work / "final_auto.dxf")
print("A. panels and loop counts:", {pid: len(v) for pid, v in loops.items()})
outer, holes = sheets.classify_loops(loops[1], step)
print("   panel 1 holes before split:", len(holes), "vertex counts:", [len(h.vertices) for h in holes])
seam_objs = [sheets.Seam.from_dict(s) for s in HAND]
frame, _w = sheetjob.resolve_frame(work, options)
snapped, _r, _w2 = sheetjob.apply_snap(seam_objs, loops, frame.axis, options)
pieces, warnings = sheets.split_panel(1, outer, holes, snapped, options)
print("   panel 1 pieces after split:", len(pieces), "holes per piece:", [len(p.holes) for p in pieces],
      "total holes after:", sum(len(p.holes) for p in pieces))
print("   split warnings:", warnings)
before_areas = sorted(round(abs(Polygon(sheets.sample_loop(h, step)[0]).area)) for h in holes)
after_areas = sorted(round(abs(Polygon(sheets.sample_loop(h, step)[0]).area)) for p in pieces for h in p.holes)
print("   hole areas before:", before_areas)
print("   hole areas after :", after_areas)
panel_poly = sheets.loop_polygon(outer, holes, step)
reach = float(np.hypot(*(np.asarray(panel_poly.bounds[2:]) - np.asarray(panel_poly.bounds[:2])))) + 10.0
kerfs = unary_union([s.extended(reach).buffer(options["seam_gap_mm"] / 2, cap_style=2, join_style=2) for s in snapped])
for h in holes:
    hp = Polygon(sheets.sample_loop(h, step)[0])
    print(f"   hole area {abs(hp.area):.0f} verts={len(h.vertices)} kerf crosses it: {kerfs.intersects(hp)}")
# rebuilt hole vertex counts (a round hole is 2 bulged vertices in the DXF)
print("   rebuilt hole vertex counts per piece:", [[len(h.vertices) for h in p.holes] for p in pieces])
print("   rebuilt hole bulge counts per piece:", [[int(np.count_nonzero(np.abs(h.bulges) > 1e-12)) for h in p.holes] for p in pieces])

# ---------------- B. nesting invariants + exporter vs preview on the real pieces
work = copy_run(AXIS, HAND, "nest_axis")
result = sheetjob.plan(work, config, write_files=True)   # writes sheet_NN.dxf INTO THE SCRATCH COPY only
prev = sheetjob.preview(work, config)
frame, _w = sheetjob.resolve_frame(work, options)
rotation = sheets.sheet_transform(frame.axis)
loops, _p, _k = sheets.read_fitted_dxf(work / "final_auto.dxf")
seam_list = [sheets.Seam.from_dict(s) for s in result["seams"]]
by_id = {}
for pid in sorted(loops):
    o, h = sheets.classify_loops(loops[pid], step)
    for piece in sheets.split_panel(pid, o, h, seam_list, options)[0]:
        by_id[piece.piece_id] = piece
usable_w, usable_l = options["max_part_width_mm"], options["max_part_length_mm"]
mx = (options["sheet_width_mm"] - usable_w) / 2
my = (options["sheet_length_mm"] - usable_l) / 2
print(f"B. {AXIS}: status={result['status']} pieces={result['piece_count']} sheets={len(result['sheets'])} unplaced={result['summary']['unplaced_piece_ids']}")
worst_env = -1e9
min_gap = 1e9
overlaps = []
for sheet in result["sheets"]:
    polys = []
    for pl in sheet["placements"]:
        piece = by_id[pl["piece_id"]]
        placement = nesting.Placement(piece_id=pl["piece_id"], panel_id=pl["panel_id"], sheet_index=sheet["sheet"] - 1,
                                      rotation_deg=int(pl["rotation_deg"]), offset=np.asarray(pl["offset_mm"], float),
                                      origin=np.asarray(pl["origin_mm"], float))
        pts, _s = sheets.sample_loop(piece.outer, step)
        poly = Polygon(placement.apply(pts, rotation))
        x0, y0, x1, y1 = poly.bounds
        worst_env = max(worst_env, mx - x0, my - y0, x1 - (mx + usable_w), y1 - (my + usable_l))
        polys.append((pl["piece_id"], poly))
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            d = polys[i][1].distance(polys[j][1])
            min_gap = min(min_gap, d)
            if polys[i][1].intersects(polys[j][1]):
                overlaps.append((polys[i][0], polys[j][0]))
print(f"   envelope: worst excursion outside usable area = {worst_env:.3f} mm (negative = inside)")
print(f"   spacing : minimum clear distance between placed pieces = {min_gap:.3f} mm (rule 20); overlaps={overlaps}")
rings = {}
for sh in prev["preview"]["sheets"]:
    for ring in sh["rings"]:
        if not ring["hole"]:
            rings[(sh["sheet"], ring["piece_id"])] = np.asarray(ring["points"], float)
worst_dxf = 0.0
compared = 0
for entry in result["files"]:
    doc = ezdxf.readfile(entry["path"])
    for e in doc.modelspace().query("LWPOLYLINE"):
        layer = str(e.dxf.layer)
        if not layer.startswith("CAM__"):
            continue
        body = layer[5:]
        piece_id = body.replace("_", "-", 1)
        verts = np.asarray([(x, y, b) for x, y, b in e.get_points("xyb")], float)
        pts, _s = sheets.sample_loop(sheets.Loop(verts), step)
        key = (entry["sheet"], piece_id)
        if key not in rings:
            key = (entry["sheet"], body)
            if key not in rings:
                continue
        ring = rings[key]
        if Polygon(pts).area < 0.5 * Polygon(ring).area:   # a hole polyline on the same layer
            continue
        diff = np.abs(np.asarray(Polygon(pts).bounds) - np.asarray(Polygon(ring).bounds)).max()
        worst_dxf = max(worst_dxf, diff)
        compared += 1
print(f"   exporter vs preview: {compared} outer rings compared, worst bounds difference = {worst_dxf:.3f} mm")
print("   refused at export:", result["refused"], " oversize:", [o['piece_id'] for o in result['oversize']])
print("   piece ids:", [p['piece_id'] for p in result['pieces']], " holes:", [p['holes'] for p in result['pieces']])

# ---------------- B2. piece naming stability: add one seam and see ids shift
work2 = copy_run(AXIS, HAND, "naming")
r1 = sheetjob.plan(work2, config, write_files=False)
extra = HAND + [{"seam_id": "s5", "x1": -800.0, "y1": -1200.0, "x2": -800.0, "y2": 1200.0, "panel_id": None, "mode": "across"}]
r2 = sheetjob.plan(work2, config, seams=[sheets.Seam.from_dict(s) for s in extra], write_files=False)
a1 = {p['piece_id']: p['area_mm2'] for p in r1['pieces']}
a2 = {p['piece_id']: p['area_mm2'] for p in r2['pieces']}
renamed = [pid for pid in a1 if pid in a2 and abs(a1[pid] - a2[pid]) > 1.0]
print(f"B2. naming: {len(a1)} pieces -> {len(a2)} after one extra seam; ids whose geometry changed under the same name: {renamed}")
print("    oversize before:", [o['piece_id'] for o in r1['oversize']], " after:", [o['piece_id'] for o in r2['oversize']])

# ---------------- C. hover (settled) -> place -> apply_snap round trip, emulating bridge._settled without a view
work3 = copy_run(AXIS, [], "hover")
loops, _p, _k = sheets.read_fitted_dxf(work3 / "final_auto.dxf")
polygons = seamplace.panel_polygons(loops, options)
frame, _w = sheetjob.resolve_frame(work3, options)
references = seamsnap.references_from_loops(loops, options)   # edge_references with no saved seams
big = max(polygons, key=lambda pid: polygons[pid].area)
minx, miny, maxx, maxy = polygons[big].bounds
rng = np.random.default_rng(5)
points = rng.uniform([minx, miny], [maxx, maxy], size=(400, 2))
moved_after_place = []
chords_total = 0
settled_moved = 0
for mode in ("along", "across"):
    unit = seamplace.direction_for(frame.axis, mode)
    for pt in points:
        segs = seamplace.seam_through(pt, unit, polygons, options)
        if not segs:
            continue
        for seg in segs:
            chords_total += 1
            res = seamsnap.snap_seam(seg["x1"], seg["y1"], seg["x2"], seg["y2"], references, options, direction_locked=True)
            chord = seg
            if res.applied:
                settled_moved += 1
                middle = np.array([(res.x1 + res.x2) / 2.0, (res.y1 + res.y2) / 2.0])
                again = seamplace.seam_through(middle, unit, polygons, options, panel_ids=[seg["panel_id"]])
                if again:
                    chord = min(again, key=lambda s: float(np.hypot((s["x1"] + s["x2"]) / 2 - middle[0], (s["y1"] + s["y2"]) / 2 - middle[1])))
            placed = sheets.Seam("hover", chord["x1"], chord["y1"], chord["x2"], chord["y2"], panel_id=chord["panel_id"], mode=mode,
                                 raw=(chord["x1"], chord["y1"], chord["x2"], chord["y2"]))
            (cut,), (snap,), _ww = sheetjob.apply_snap([placed], loops, frame.axis, options)
            moved_after_place.append((snap.moved_mm, snap.angle_change_deg, snap.note, mode, chord["panel_id"], round(chord["length_mm"])))
moved = sorted(moved_after_place, key=lambda t: -t[0])
print(f"C. hover emulation: {chords_total} chords; settled (slid) before click: {settled_moved}; "
      f"moved again by apply_snap after the click (>1e-6 mm): {sum(1 for m in moved if m[0] > 1e-6)}; worst: {moved[0][:3] if moved else None}")
for m in moved[:5]:
    print("    ", m)

# ---------------- D. the confirmed disagreement on the real deck: one short chord vs the cut
work4 = copy_run(AXIS, [], "chord")
unit = seamplace.direction_for(frame.axis, "across")
# a point in the starboard strip between the console and the gunwale: console y -524..601, deck y -1094..1007
segs = seamplace.seam_through((700.0, -800.0), unit, polygons, options)
print("D. hover across at (700,-800) on the real deck ->", [(s['panel_id'], round(s['length_mm'])) for s in segs])
short = min((s for s in segs if s["panel_id"] == 1), key=lambda s: s["length_mm"])
one = sheets.Seam("c", short["x1"], short["y1"], short["x2"], short["y2"], panel_id=1, mode="across", raw=(short["x1"], short["y1"], short["x2"], short["y2"]))
res = sheetjob.plan(work4, config, seams=[one], write_files=False)
print(f"   placing only the {short['length_mm']:.0f} mm chord -> panel 1 pieces: {[ (p['piece_id'], round(p['area_mm2']/1e6,3)) for p in res['pieces'] if p['panel_id']==1]}")
print("   seam as cut:", {k: round(v, 1) for k, v in res["seams"][0].items() if k in ("x1", "y1", "x2", "y2")}, "snap:", res["seam_snaps"][0]["note"])
