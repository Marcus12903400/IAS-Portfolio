import json, time
from pathlib import Path
import numpy as np
from shapely.geometry import LineString
from autodeck2 import sheets, sheetjob, nesting
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
RUN = S / "axisrun"
config = load_config()
options = sheets.settings(config)
step = options["sample_step_mm"]
print("options:", {k: options[k] for k in ("sample_step_mm","arc_rebuild_tolerance_mm","min_piece_area_mm2","seam_gap_mm","part_spacing_mm","nest_step_mm","grain_angle_deg")})
loops, pattern, kind = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
print("pattern kind", kind, "panels", sorted(loops), "loops per panel", {k: len(v) for k, v in loops.items()})
panels = {}
for pid in sorted(loops):
    outer, holes = sheets.classify_loops(loops[pid], step)
    poly = sheets.loop_polygon(outer, holes, step)
    panels[pid] = (outer, holes, poly)
    nb = int(np.count_nonzero(np.abs(outer.bulges) > 1e-12)) + sum(int(np.count_nonzero(np.abs(h.bulges) > 1e-12)) for h in holes)
    nv = len(outer.vertices) + sum(len(h.vertices) for h in holes)
    print(f"panel {pid}: outer verts {len(outer.vertices)} outer bulges {int(np.count_nonzero(np.abs(outer.bulges) > 1e-12))} holes {len(holes)} hole verts {[len(h.vertices) for h in holes]} hole bulges {[int(np.count_nonzero(np.abs(h.bulges) > 1e-12)) for h in holes]} area {poly.area:.0f} bounds {[round(b) for b in poly.bounds]} valid {poly.is_valid} type {poly.geom_type}")

def seg_stats(loop):
    xy = loop.xy; b = loop.bulges
    n = len(xy)
    lens = np.hypot(*(np.roll(xy, -1, axis=0) - xy).T)
    short = (np.abs(b) < 1e-12) & (lens <= 1.5)
    arr = np.concatenate([short, short])
    best = cur = 0
    for v in arr:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return n, int(np.count_nonzero(np.abs(b) > 1e-12)), int(short.sum()), min(best, n)

frame, fw = sheetjob.resolve_frame(RUN, options)
rotation = sheets.sheet_transform(frame.axis)
print("frame axis", frame.axis, "bow", frame.bow_sign, "warnings", fw)

def load(name):
    payload = json.loads((RUN / name).read_text(encoding="utf-8"))
    return [sheets.Seam.from_dict(s) for s in payload["seams"]]

for name in ["seams.json", "seams_previous.json", "seams_optimiser_run.json.bak"]:
    print("\n=================", name)
    raw_seams = load(name)
    snapped, results, warns = sheetjob.apply_snap(raw_seams, loops, frame.axis, options)
    for w in warns: print("  snapwarn:", w)
    for s0, s1, r in zip(raw_seams, snapped, results):
        d0 = LineString([(s0.x1, s0.y1), (s0.x2, s0.y2)])
        d1 = LineString([(s1.x1, s1.y1), (s1.x2, s1.y2)])
        hit0 = [pid for pid, (_o, _h, poly) in panels.items() if d0.intersects(poly)]
        hit1 = [pid for pid, (_o, _h, poly) in panels.items() if d1.intersects(poly)]
        move = max(np.hypot(s1.x1 - s0.x1, s1.y1 - s0.y1), np.hypot(s1.x2 - s0.x2, s1.y2 - s0.y2))
        print(f"  seam {s0.seam_id:16s} panel_id={s0.panel_id} mode={s0.mode!r:9s} len={d0.length:6.0f} moved {move:5.1f}mm drawn-hits {hit0} snapped-hits {hit1} kind={r.kind!r} note={r.note[:70]!r}")
    t0 = time.perf_counter()
    all_pieces = []
    for pid, (outer, holes, poly) in panels.items():
        pieces, warns = sheets.split_panel(pid, outer, holes, snapped, options)
        for w in warns: print("  splitwarn:", w)
        for p in pieces:
            n, nb, nshort, run = seg_stats(p.outer)
            hs = [seg_stats(h) for h in p.holes]
            w, l = sheets.oriented_extent(p, rotation, step)
            print(f"  {p.piece_id:7s} area {p.area_mm2:9.0f} verts {n:5d} bulges {nb:3d} short-straight {nshort:5d} longest-run {run:5d} holes {len(p.holes)} hole-verts {[h[0] for h in hs]} hole-bulges {[h[1] for h in hs]} ext {w:7.1f}x{l:7.1f} arcerr {p.max_arc_error_mm:.4f} from_seam {p.from_seam}")
        all_pieces.extend(pieces)
    print(f"  split time {time.perf_counter()-t0:.2f}s")
    over = sheets.oversize_report(all_pieces, rotation, options)
    print("  oversize:", [(o['piece_id'], o['width_mm'], o['length_mm'], o['hint']) for o in over])
    sheet_list, summary, nw = nesting.nest(all_pieces, rotation, options)
    print("  nest: sheets", summary["sheet_count"], "unplaced", summary["unplaced_piece_ids"])
    for w in nw: print("  nestwarn:", w)

print("\n===== plan vs preview naming (seams.json), no files written =====")
res = sheetjob.plan(RUN, config, write_files=False)
prev = sheetjob.preview(RUN, config)
plan_ids = [p["piece_id"] for p in res["pieces"]]
prev_ids = sorted({r["piece_id"] for s in prev["preview"]["sheets"] for r in s["rings"]})
placed_ids = sorted(pl["piece_id"] for s in res["sheets"] for pl in s["placements"])
print("plan pieces   :", plan_ids)
print("placed        :", placed_ids)
print("preview rings :", prev_ids)
print("status", res["status"], "max_arc_rebuild_error_mm", res["max_arc_rebuild_error_mm"])
print("warnings:"); [print("   -", w) for w in res["warnings"]]

print("\n===== naming drift when a seam is added elsewhere (panel 1) =====")
hand = load("seams.json")
snapped, _r, _w = sheetjob.apply_snap(hand, loops, frame.axis, options)
by_id = {s.seam_id: s for s in snapped}
outer, holes, poly = panels[1]
for subset in (["s3"], ["s3", "s4"], ["s1", "s2", "s3", "s4"]):
    pieces, _ = sheets.split_panel(1, outer, holes, [by_id[k] for k in subset], options)
    print(" seams", subset, "->", [(p.piece_id, round(p.area_mm2), [round(b) for b in p.polygon(step).bounds]) for p in pieces])
