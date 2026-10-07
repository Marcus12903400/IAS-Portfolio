"""Probe (1)+(2): hover a grid of points over every panel of the staged AXIS
run through bridge.seam_hover, in all four modes, raw (snap off) and settled
(snap on, the default), and check every returned chord.

  python probe_hover.py            full grids (25 mm on panel 1, 10/10/10/5 mm)
  python probe_hover.py --quick    coarse grids
  python probe_hover.py --timing-only

Writes out/hover_summary.json and out/hover_failures.json.
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter, defaultdict

import numpy as np
from shapely.geometry import LineString, MultiLineString, Point
from shapely.prepared import prep

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from common import (RUN, angle_between_deg, dump, grid_inside, holes_of, line_distance, load,
                    panel_of_point, ring_samples, seg_unit, segment_under)

QUICK = "--quick" in sys.argv
TIMING_ONLY = "--timing-only" in sys.argv
SUFFIX = "_quick" if QUICK else ("_timing" if TIMING_ONLY else "")

ctx = load()
bridge, view, options, frame = ctx["bridge"], ctx["view"], ctx["options"], ctx["frame"]
loops, polygons, masters = ctx["loops"], ctx["polygons"], ctx["masters"]
seamplace, seamsnap, sheetjob, sheets = ctx["seamplace"], ctx["seamsnap"], ctx["sheetjob"], ctx["sheets"]
axis = np.asarray(frame.axis, float)
print("axis", axis, "warnings", ctx["warnings"])
print("panels", {pid: (round(p.area), len(p.interiors)) for pid, p in polygons.items()})

MODES = [("along", None), ("across", None), ("angle", 30.0), ("angle", 60.0)]
UNITS = {m: seamplace.direction_for(axis, m[0], m[1]) for m in MODES}
OFFSET_LIMIT = float(options["seam_snap_offset_mm"])
GRACE = 2.0   # app.js SEGMENT_GRACE_MM

_l, pattern, kind = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))
pattern_ml = {pid: MultiLineString([ln.tolist() for ln in lines]) for pid, lines in pattern.items()}
print("pattern kind", kind, {pid: len(v) for pid, v in pattern.items()})

BOUND = {pid: poly.boundary for pid, poly in polygons.items()}
EXT = {pid: poly.exterior for pid, poly in polygons.items()}
HOLE_RINGS = {pid: list(poly.interiors) for pid, poly in polygons.items()}
HOLES_IN = {pid: [h.buffer(-1e-3) for h in holes_of(poly)] for pid, poly in polygons.items()}
INSIDE = {pid: prep(poly.buffer(1e-3)) for pid, poly in polygons.items()}
REFS = bridge.edge_references(view, options) + bridge.seam_references(view, options)
print("references", len(REFS), Counter(r.kind for r in REFS))


def own_chord_through(point, unit, pid):
    """The unfiltered chord of panel `pid` through `point` (no MIN_CHORD floor)."""
    poly = polygons[pid]
    reach = seamplace._reach(poly, np.asarray(point, float))
    trial = LineString([np.asarray(point) - unit * reach, np.asarray(point) + unit * reach])
    best = None
    for a, b in seamplace._straight_parts(poly.intersection(trial)):
        seg = LineString([a, b])
        if seg.distance(Point(point)) < 1e-6:
            best = seg.length
    return best


# ---------------------------------------------------------------- samples
PITCH = {1: 25.0, 2: 10.0, 3: 10.0, 4: 10.0, 5: 5.0}
if QUICK:
    PITCH = {1: 100.0, 2: 40.0, 3: 40.0, 4: 40.0, 5: 20.0}
samples: list[tuple[str, int | None, tuple[float, float]]] = []
if not TIMING_ONLY:
    for pid, poly in sorted(polygons.items()):
        for pt in grid_inside(poly, PITCH[pid]):
            samples.append(("grid", pid, pt))
        inner = poly.buffer(-1.0)
        geoms = [inner] if inner.geom_type == "Polygon" else list(inner.geoms)
        rings = [g.exterior for g in geoms] + [r for g in geoms for r in g.interiors]
        for ring in rings:
            for pt in ring_samples(ring, 50.0 if pid == 1 else 20.0):
                if poly.contains(Point(pt)):
                    samples.append(("edge1mm", pid, pt))
        for hole in holes_of(poly):
            for pt in grid_inside(hole, 20.0 if not QUICK else 60.0):
                samples.append(("in_hole", None, pt))
            rp = hole.representative_point()
            samples.append(("in_hole", None, (rp.x, rp.y)))
        for d, tag in ((1.0, "out1mm"), (10.0, "out10mm")):
            for pt in ring_samples(poly.buffer(d).exterior, 50.0 if pid == 1 else 20.0):
                if panel_of_point(polygons, pt) is None:
                    samples.append((tag, None, pt))
    samples.append(("far", None, (500000.0, 500000.0)))
print("samples", Counter(tag for tag, _p, _x in samples), "total", len(samples))

fails: dict[str, list] = defaultdict(list)
stats: dict[str, list] = defaultdict(list)
counts = Counter()
timings: dict[str, list] = defaultdict(list)
slide_labels = Counter()


def record(cat, **info):
    fails[cat].append(info)


def check_call(tag, expected_pid, point, mode, settled: bool):
    key = "settled" if settled else "raw"
    overrides = {} if settled else {"seam_snap_enabled": False}
    t0 = time.perf_counter()
    result = bridge.seam_hover(view, overrides, mode[0], mode[1], point_flat=list(point), with_world=False)
    timings[key].append((time.perf_counter() - t0) * 1000.0)
    segments = result.get("segments") or []
    unit = UNITS[mode]
    counts[(key, tag, "calls")] += 1
    counts[(key, tag, "segments")] += len(segments)
    info = dict(tag=tag, mode=f"{mode[0]}{'' if mode[1] is None else mode[1]:}", point=list(point),
                expected_pid=expected_pid, kind=key)

    for s in segments:
        pid = int(s["panel_id"])
        line = LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])])
        d1 = BOUND[pid].distance(Point(s["x1"], s["y1"]))
        d2 = BOUND[pid].distance(Point(s["x2"], s["y2"]))
        stats["endpoint_boundary_mm"].append(max(d1, d2))
        if max(d1, d2) >= 0.5:
            record("endpoint_off_outline", **info, segment=s, d_mm=[d1, d2])
        for hole in HOLES_IN[pid]:
            cut = line.intersection(hole).length
            if cut > 1e-6:
                record("crosses_cutout", **info, segment=s, inside_hole_mm=cut)
                break
        if not INSIDE[pid].contains(line):
            record("chord_outside_panel", **info, segment=s)
        mid = Point((s["x1"] + s["x2"]) / 2, (s["y1"] + s["y2"]) / 2)
        dmid = BOUND[pid].distance(mid)
        if dmid < 0.01:
            where = "outer" if EXT[pid].distance(mid) < 0.01 else "cut-out"
            counts[(key, "chord_on_outline", where)] += 1
            record("chord_lies_on_outline", **info, segment=s, where=where, mid_gap_mm=dmid)
        length = math.hypot(s["x2"] - s["x1"], s["y2"] - s["y1"])
        stats["length_mm"].append(length)
        if length < seamplace.MIN_CHORD_MM:
            record("short_chord", **info, segment=s, length=length)
        if abs(length - s["length_mm"]) > 0.051:
            record("length_payload_mismatch", **info, segment=s, length=length)
        ang = angle_between_deg(seg_unit(s), unit)
        stats[f"dir_err_deg_{mode[0]}"].append(ang)
        if ang > 1e-6:
            record("direction_off_master", **info, segment=s, angle_err_deg=ang)
        if mode[0] == "angle":
            off_axis = angle_between_deg(seg_unit(s), axis)
            stats["diag_off_axis_err_deg"].append(abs(off_axis - mode[1]))
            if abs(off_axis - mode[1]) > 1e-6:
                record("diagonal_angle_wrong", **info, segment=s, off_axis_deg=off_axis)
        dline = line_distance(point, s)
        stats[f"through_{key}_mm"].append(dline)
        if not settled and dline > 0.01:
            record("raw_not_through_point", **info, segment=s, line_gap_mm=dline)
        if settled and dline > OFFSET_LIMIT + 0.01:
            record("settled_slid_too_far", **info, segment=s, line_gap_mm=dline)
        if pid in pattern_ml:
            dp = min(pattern_ml[pid].distance(Point(s["x1"], s["y1"])),
                     pattern_ml[pid].distance(Point(s["x2"], s["y2"])))
            if dp < 0.5:
                counts[(key, "endpoint_within_0.5mm_of_pattern_line")] += 1

    shown = result.get("point_flat")
    under = segment_under(shown, segments, GRACE) if shown else -1
    if expected_pid is not None:
        if under < 0:
            own = own_chord_through(point, unit, expected_pid)
            why = ("corner_graze_lt_5mm" if own is not None and own < seamplace.MIN_CHORD_MM
                   else "no_chord_through_point" if own is None else "chord_exists")
            counts[(key, "nothing_previewed", why)] += 1
            record("nothing_previewed_on_panel", **info, n_segments=len(segments),
                   reason=result.get("reason"), own_chord_mm=own, why=why)
        elif int(segments[under]["panel_id"]) != expected_pid:
            record("previewed_wrong_panel", **info, segment=segments[under])
    else:
        counts[(key, tag, "previewed" if under >= 0 else "nothing")] += 1
        if under >= 0:
            gap = min(polygons[p].distance(Point(point)) for p in polygons)
            beyond = gap > GRACE + 0.01
            counts[(key, tag, "previewed_beyond_grace" if beyond else "previewed_within_grace")] += 1
            if beyond:
                record("previewed_off_deck_beyond_grace", **info, segment=segments[under],
                       n_segments=len(segments), gap_to_deck_mm=gap)
        if tag == "far" and not result.get("reason"):
            record("far_no_reason", **info)
    return result, segments, under


# ---------------------------------------------------------------- run
slide: list[float] = []
slide_by_mode = defaultdict(list)
t_start = time.perf_counter()
for n, (tag, expected_pid, point) in enumerate(samples):
    for mode in MODES:
        raw, raw_segs, raw_under = check_call(tag, expected_pid, point, mode, settled=False)
        st, st_segs, st_under = check_call(tag, expected_pid, point, mode, settled=True)
        if raw_under >= 0 and st_under >= 0:
            a, b = raw_segs[raw_under], st_segs[st_under]
            moved = line_distance((a["x1"], a["y1"]), b)
            slide.append(moved)
            slide_by_mode[mode[0]].append(moved)
            if moved > 1e-9:
                counts[("slid", tag)] += 1
                res = seamsnap.snap_seam(a["x1"], a["y1"], a["x2"], a["y2"], REFS, options, direction_locked=True)
                slide_labels[res.reference_label] += 1
                if a["panel_id"] != b["panel_id"]:
                    record("slide_changed_panel", tag=tag, mode=mode, point=list(point), raw=a, settled=b,
                           onto=res.reference_label)
                if moved > 10.0:
                    record("slide_over_10mm", tag=tag, mode=mode, point=list(point), raw=a, settled=b,
                           onto=res.reference_label, moved_mm=moved)
        elif (raw_under >= 0) != (st_under >= 0):
            a = raw_segs[raw_under] if raw_under >= 0 else None
            res = (seamsnap.snap_seam(a["x1"], a["y1"], a["x2"], a["y2"], REFS, options, direction_locked=True)
                   if a else None)
            record("slide_changed_whether_previewed", tag=tag, mode=mode, point=list(point),
                   raw_under=raw_under, settled_under=st_under, raw_n=len(raw_segs), settled_n=len(st_segs),
                   raw_seg=a, settled_segs=st_segs, onto=(res.reference_label if res else None),
                   moved_mm=(res.moved_mm if res else None))
    if n % 500 == 0:
        print(f"  {n}/{len(samples)} samples, {time.perf_counter() - t_start:.0f} s, "
              f"fails so far {sum(len(v) for v in fails.values())}", flush=True)

# ---------------------------------------------------------------- timing (2)
rng = np.random.default_rng(11)
p1 = polygons[1]
minx, miny, maxx, maxy = p1.bounds
pts = []
while len(pts) < 300:
    x, y = rng.uniform(minx, maxx), rng.uniform(miny, maxy)
    if p1.contains(Point(x, y)):
        pts.append((x, y))
lat = defaultdict(list)
for x, y in pts:
    for mode in MODES:
        t0 = time.perf_counter(); bridge.seam_hover(view, {}, mode[0], mode[1], point_flat=[x, y], with_world=False)
        lat["flat_noworld"].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter(); bridge.seam_hover(view, {}, mode[0], mode[1], point_flat=[x, y], with_world=True)
        lat["flat_world"].append((time.perf_counter() - t0) * 1000)
uv = bridge._unplace(view.placements[1], np.array(pts))
world = view.lifters[1](uv)
t0 = time.perf_counter(); bridge.warm_picking(view); warm_s = time.perf_counter() - t0
for xyz, mode in zip(world, [MODES[i % 4] for i in range(len(world))]):
    t0 = time.perf_counter()
    bridge.seam_hover(view, {}, mode[0], mode[1], point_world=list(map(float, xyz)), with_world=True)
    lat["world_world"].append((time.perf_counter() - t0) * 1000)
# a cold hover (first call after a cache invalidation) -- what the first pointer move costs
view.sheet_cache.clear()
t0 = time.perf_counter(); bridge.seam_hover(view, {}, "across", None, point_flat=list(pts[0]), with_world=False)
cold_ms = (time.perf_counter() - t0) * 1000


def pct(v, q):
    return float(np.percentile(v, q)) if len(v) else float("nan")


summary = {
    "samples": dict(Counter(tag for tag, _p, _x in samples)),
    "calls": {k: len(v) for k, v in timings.items()},
    "fail_counts": {k: len(v) for k, v in fails.items()},
    "counts": {"|".join(map(str, k)): v for k, v in counts.items()},
    "slide_landed_on": dict(slide_labels),
    "stats": {k: dict(n=len(v), max=float(np.max(v)), p50=pct(v, 50), p95=pct(v, 95), p99=pct(v, 99))
              for k, v in stats.items() if len(v)},
    "slide": dict(n=len(slide), moved=sum(1 for s in slide if s > 1e-9),
                  max=float(np.max(slide)) if slide else None, p50=pct(slide, 50), p95=pct(slide, 95),
                  by_mode={m: dict(n=len(v), moved=sum(1 for s in v if s > 1e-9), max=float(np.max(v)))
                           for m, v in slide_by_mode.items()}),
    "latency_ms": {k: dict(n=len(v), p50=pct(v, 50), p95=pct(v, 95), max=float(np.max(v))) for k, v in lat.items()},
    "cold_hover_ms": cold_ms, "warm_picking_s": warm_s,
    "probe_latency_ms": {k: dict(n=len(v), p50=pct(v, 50), p95=pct(v, 95)) for k, v in timings.items()},
}
dump(f"hover_summary{SUFFIX}.json", summary)
worst = {}
for cat, items in fails.items():
    keyf = {"endpoint_off_outline": lambda i: -max(i["d_mm"]),
            "crosses_cutout": lambda i: -i["inside_hole_mm"],
            "direction_off_master": lambda i: -i["angle_err_deg"],
            "raw_not_through_point": lambda i: -i["line_gap_mm"],
            "settled_slid_too_far": lambda i: -i["line_gap_mm"],
            "previewed_off_deck_beyond_grace": lambda i: -i["gap_to_deck_mm"],
            "slide_over_10mm": lambda i: -i["moved_mm"],
            "short_chord": lambda i: i["length"]}.get(cat, lambda i: 0)
    worst[cat] = dict(count=len(items), examples=sorted(items, key=keyf)[:25])
dump(f"hover_failures{SUFFIX}.json", worst)

print(json.dumps(summary, indent=1, default=float))
for cat, item in worst.items():
    print(f"\n== {cat}: {item['count']}")
    for ex in item["examples"][:6]:
        print("  ", json.dumps(ex, default=float)[:700])
print("done in", round(time.perf_counter() - t_start), "s")
