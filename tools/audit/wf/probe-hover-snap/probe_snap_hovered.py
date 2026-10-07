"""Probe (3): seams placed by hovering must survive the corrector untouched,
and six of them must really cut the deck through sheetjob.plan.

Runs on the staged copy ./runs/axis (hover + apply_snap) and writes the plan
into a second staged copy ./runs/axis_plan (sheets.json, sheet_report.md,
sheet_NN.dxf) so nothing real is touched.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HERE, RUN, angle_between_deg, dump, grid_inside, load, segment_under

ctx = load()
bridge, view, options, frame, config = ctx["bridge"], ctx["view"], ctx["options"], ctx["frame"], ctx["config"]
loops, polygons = ctx["loops"], ctx["polygons"]
seamplace, seamsnap, sheetjob, sheets = ctx["seamplace"], ctx["seamsnap"], ctx["sheetjob"], ctx["sheets"]
Seam = sheets.Seam
axis = np.asarray(frame.axis, float)
along, across = ctx["masters"]

MODES = [("along", None), ("across", None), ("angle", 30.0), ("angle", 60.0)]


def hover_seam(point, mode, angle=None, panel=None):
    """What placeHovered() would push for a hover at `point`."""
    r = bridge.seam_hover(view, {}, mode, angle, point_flat=list(point), with_world=False)
    segs = r.get("segments") or []
    under = segment_under(r.get("point_flat"), segs)
    if under < 0:
        return None
    s = segs[under]
    if panel is not None and s["panel_id"] != panel:
        return None
    return {"x1": s["x1"], "y1": s["y1"], "x2": s["x2"], "y2": s["y2"], "panel_id": s["panel_id"],
            "mode": mode, "angle_deg": angle if mode == "angle" else None, "snap": True,
            "raw": [s["x1"], s["y1"], s["x2"], s["y2"]]}


# ---- 40 hovered seams spread over panels and modes
rng = np.random.default_rng(3)
picked = []
quota = {1: 20, 2: 6, 3: 6, 4: 4, 5: 4}
for pid, n in quota.items():
    pts = grid_inside(polygons[pid], 60.0 if pid == 1 else 15.0, margin=5.0)
    rng.shuffle(pts)
    i = 0
    for pt in pts:
        mode = MODES[i % 4]
        s = hover_seam(pt, mode[0], mode[1], panel=pid)
        if s is None:
            continue
        s["hover_point"] = list(pt)
        picked.append(s)
        i += 1
        if i >= n:
            break
print("hovered seams", len(picked), {pid: sum(1 for s in picked if s["panel_id"] == pid) for pid in quota})

# server.checked_seams passes the dicts through; bridge.sheet_preview builds Seams like this:
seams = [Seam.from_dict({**{k: v for k, v in s.items() if k != "hover_point"}, "seam_id": f"h{i + 1}"})
         for i, s in enumerate(picked)]


def displacement(a: Seam, b: Seam):
    return max(math.hypot(a.x1 - b.x1, a.y1 - b.y1), math.hypot(a.x2 - b.x2, a.y2 - b.y2))


report = {"alone": [], "together": [], "with_existing": []}
# (a) each on its own
worst_alone = 0.0
for seam in seams:
    (out,), (res,), warns = sheetjob.apply_snap([seam], loops, axis, options)
    d = displacement(seam, out)
    worst_alone = max(worst_alone, d)
    report["alone"].append(dict(id=seam.seam_id, mode=seam.mode, angle=seam.angle_deg, panel=seam.panel_id,
                                moved_mm=d, applied=res.applied, kind=res.kind, note=res.note,
                                angle_change=res.angle_change_deg, warnings=warns))
    if d > 1e-9:
        hp = picked[int(seam.seam_id[1:]) - 1]["hover_point"]
        raw = bridge.seam_hover(view, {"seam_snap_enabled": False}, seam.mode, seam.angle_deg, point_flat=hp, with_world=False)
        st = bridge.seam_hover(view, {}, seam.mode, seam.angle_deg, point_flat=hp, with_world=False)
        ru = segment_under(raw["point_flat"], raw["segments"]); su = segment_under(st["point_flat"], st["segments"])
        a = raw["segments"][ru]; b = st["segments"][su]
        refs = bridge.edge_references(view, options) + bridge.seam_references(view, options)
        r_raw = seamsnap.snap_seam(a["x1"], a["y1"], a["x2"], a["y2"], refs, options, direction_locked=True)
        r_set = seamsnap.snap_seam(b["x1"], b["y1"], b["x2"], b["y2"], refs, options, direction_locked=True)
        half_a = math.hypot(a["x2"] - a["x1"], a["y2"] - a["y1"]) / 2; half_b = math.hypot(b["x2"] - b["x1"], b["y2"] - b["y1"]) / 2
        print(f"  DEBUG {seam.seam_id} {seam.mode} panel {seam.panel_id} hover {hp}")
        print(f"    raw chord len {2*half_a:.1f} -> snap: applied={r_raw.applied} {r_raw.note!r} moved {r_raw.moved_mm:.3f}; angle limit {min(0.5, math.degrees(math.atan2(1.0, half_a))):.4f} deg")
        print(f"    settled chord len {2*half_b:.1f} (preview) -> snap again: applied={r_set.applied} {r_set.note!r} moved {r_set.moved_mm:.3f}; angle limit {min(0.5, math.degrees(math.atan2(1.0, half_b))):.4f} deg")
        print(f"    apply_snap on the placed seam: {res.note!r} moved {d:.3f} -> out ({out.x1:.3f},{out.y1:.3f})-({out.x2:.3f},{out.y2:.3f})")
        print(f"    raw ({a['x1']:.3f},{a['y1']:.3f})-({a['x2']:.3f},{a['y2']:.3f}); settled ({b['x1']:.3f},{b['y1']:.3f})-({b['x2']:.3f},{b['y2']:.3f})")
# (b) all forty through snapped_seams (the production entry point, reads the staged run)
outs, results, warns = sheetjob.snapped_seams(RUN, config, seams)
worst_together = 0.0
for seam, out, res in zip(seams, outs, results):
    d = displacement(seam, out)
    worst_together = max(worst_together, d)
    report["together"].append(dict(id=seam.seam_id, moved_mm=d, applied=res.applied, kind=res.kind,
                                   note=res.note, label=res.reference_label))
# (c) as the page would post them: the run's existing seams first, then the new ones
existing = sheets.read_seams(RUN)
outs2, results2, warns2 = sheetjob.apply_snap(existing + seams, loops, axis, options)
worst_existing = 0.0
for seam, out, res in zip(seams, outs2[len(existing):], results2[len(existing):]):
    d = displacement(seam, out)
    worst_existing = max(worst_existing, d)
    report["with_existing"].append(dict(id=seam.seam_id, moved_mm=d, applied=res.applied, kind=res.kind, note=res.note))

moved_together = [r for r in report["together"] if r["moved_mm"] > 1e-9]
moved_existing = [r for r in report["with_existing"] if r["moved_mm"] > 1e-9]
print(f"max displacement alone {worst_alone:.3e} mm, together {worst_together:.3e} mm "
      f"({len(moved_together)} moved), with existing seams first {worst_existing:.3e} mm ({len(moved_existing)} moved)")
for r in moved_together[:10]:
    print("  together moved:", r)
for r in moved_existing[:10]:
    print("  with-existing moved:", r)
# direction after the corrector, against the master it was placed on
dir_err = []
for seam, out in zip(seams, outs):
    unit = seamplace.direction_for(axis, seam.mode, seam.angle_deg)
    dir_err.append(angle_between_deg([out.x2 - out.x1, out.y2 - out.y1], unit))
print("max direction error after corrector (deg):", max(dir_err))
# bit-identical through JSON (what seams.json would hold)
rt = [Seam.from_dict(json.loads(json.dumps(o.to_dict()))) for o in outs]
print("json round trip bit-identical:", all(a.to_dict() == b.to_dict() for a, b in zip(outs, rt)))

# ---- plan with six hovered seams: 2 along + 2 across on panel 1, 1 on panel 2, 1 on panel 3
PLAN = HERE / "runs" / "axis_plan"
if PLAN.exists():
    shutil.rmtree(PLAN)
PLAN.mkdir(parents=True)
for name in ("final_auto.dxf", "run.json"):
    shutil.copyfile(RUN / name, PLAN / name)

six_points = [
    ("along", (300.0, -600.0), 1), ("along", (300.0, 600.0), 1),
    ("across", (-700.0, -400.0), 1), ("across", (1300.0, 400.0), 1),
]
# the strips: hover their middles, across the boat
for pid in (2, 3):
    rp = polygons[pid].representative_point()
    six_points.append(("across", (rp.x, rp.y), pid))
six = []
inside1 = np.array(grid_inside(polygons[1], 20.0, margin=10.0))
for mode, pt, pid in six_points:
    if pid == 1:
        # the nearest grid point that is really inside panel 1 (not in the console)
        pt = tuple(inside1[np.argmin(np.hypot(inside1[:, 0] - pt[0], inside1[:, 1] - pt[1]))])
    s = hover_seam(pt, mode, None, panel=pid)
    assert s is not None, (mode, pt, pid)
    six.append(Seam.from_dict({**s, "seam_id": f"p{len(six) + 1}"}))
    print(f"  plan seam {six[-1].seam_id}: {mode} on panel {pid} "
          f"({s['x1']:.1f},{s['y1']:.1f})-({s['x2']:.1f},{s['y2']:.1f}) {math.hypot(s['x2']-s['x1'], s['y2']-s['y1']):.0f} mm")

rotation = sheets.sheet_transform(axis)
step = float(options["sample_step_mm"])


def piece_table(result):
    rows = []
    # rebuild the pieces to measure them (plan only reports areas)
    seam_list = [Seam.from_dict(item) for item in result["seams"]]
    for pid in sorted(loops):
        outer, holes = sheets.classify_loops(loops[pid], step)
        pieces, _w = sheets.split_panel(pid, outer, holes, seam_list, options)
        for p in pieces:
            w, l = sheets.oriented_extent(p, rotation, step)
            rows.append(dict(piece=p.piece_id, panel=pid, area=round(p.area_mm2), across=round(w, 1), along=round(l, 1)))
    return rows


baseline = sheetjob.plan(PLAN, config, seams=[], write_files=False)
print("baseline pieces", baseline["piece_count"], "status", baseline["status"], "oversize", [o["piece_id"] for o in baseline["oversize"]])
progression = []
prev = baseline["piece_count"]
for k in range(1, len(six) + 1):
    r = sheetjob.plan(PLAN, config, seams=six[:k], write_files=(k == len(six)), progress=lambda m: None)
    progression.append(dict(seams=k, pieces=r["piece_count"], delta=r["piece_count"] - prev, status=r["status"],
                            oversize=[o["piece_id"] for o in r["oversize"]], sheets=r["summary"]["sheet_count"],
                            warnings=[w for w in r["warnings"] if "sliver" in w or "degenerated" in w]))
    prev = r["piece_count"]
    final = r
for row in progression:
    print("  ", row)
table = piece_table(final)
slivers = [row for row in table if min(row["across"], row["along"]) < 20.0 or row["area"] < 2000]
print("pieces after six seams:", len(table), "slivers:", slivers)
for row in sorted(table, key=lambda r: r["area"])[:8]:
    print("   smallest:", row)
print("snaps reported by plan:", [(s["kind"], s["note"], s["moved_mm"]) for s in final["seam_snaps"]])
print("written:", sorted(p.name for p in PLAN.iterdir()))
dump("snap_hovered.json", dict(report=report, worst=dict(alone=worst_alone, together=worst_together,
                                                        with_existing=worst_existing),
                               progression=progression, pieces=table, slivers=slivers,
                               six=[s.to_dict() for s in six], snaps=final["seam_snaps"]))
