"""Shared setup for the hover / snap probes.

Everything runs against the STAGED copy of the AXIS run in ./runs/axis, never
against D:/AutoDeck/engine/outputs/runs.  bridge.load_run reads run.json from
the run dir, the scan from run.json's input_path and the developed panels from
the engine cache (a cache HIT, so engine.run_engine never touches debug_dir).

Run every probe with:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
      .venv/Scripts/python.exe <scratch>/wf/probe-hover-snap/<script>.py
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.prepared import prep

HERE = Path(__file__).resolve().parent
RUN = HERE / "runs" / "axis"
REAL_RUN = Path("D:/AutoDeck/engine/outputs/runs/21kwcockpit-1-20260901-180939")
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"):
    if p not in sys.path:
        sys.path.insert(0, p)

assert RUN.is_dir(), f"stage the run first: {RUN}"
assert str(RUN).startswith(str(HERE)), "probes must only ever point at the scratch copy"


def guard_real_run():
    """Refuse to run if anything would write into the real run folder."""
    assert not str(RUN).lower().startswith(str(REAL_RUN).lower())


def load(log=print):
    guard_real_run()
    from autodeck_app import bridge
    from autodeck2 import seamplace, seamsnap, sheetjob, sheets
    from autodeck2.config import load_config

    t0 = time.perf_counter()
    view = bridge.load_run(RUN, log=log)
    log(f"load_run: {time.perf_counter() - t0:.1f} s")
    config = load_config()
    options, frame, warnings = bridge.hover_context(view, {})
    loops, polygons = bridge.panel_shapes(view, options)
    masters = seamsnap.master_directions(frame.axis)
    return dict(bridge=bridge, view=view, config=config, options=options, frame=frame,
                warnings=warnings, loops=loops, polygons=polygons, masters=masters,
                seamplace=seamplace, seamsnap=seamsnap, sheetjob=sheetjob, sheets=sheets)


def angle_between_deg(u, v):
    """Undirected angle between two directions, in degrees (atan2 form, no acos
    digit loss)."""
    u = np.asarray(u, float); v = np.asarray(v, float)
    u = u / np.hypot(*u); v = v / np.hypot(*v)
    cross = abs(u[0] * v[1] - u[1] * v[0])
    dot = abs(float(np.dot(u, v)))
    return math.degrees(math.atan2(cross, dot))


def seg_unit(s):
    d = np.array([s["x2"] - s["x1"], s["y2"] - s["y1"]], float)
    return d / np.hypot(*d)


def line_distance(point, s):
    """Distance from `point` to the INFINITE line through segment s."""
    p = np.asarray(point, float)
    a = np.array([s["x1"], s["y1"]]); u = seg_unit(s)
    w = p - a
    return abs(u[0] * w[1] - u[1] * w[0])


def segment_under(point, segments, grace_mm=2.0):
    """Port of app.js segmentUnder(): the chord whose parameter range brackets
    the hovered point (within SEGMENT_GRACE_MM), else -1."""
    p = point
    best, best_gap = -1, math.inf
    for i, s in enumerate(segments):
        dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
        l2 = dx * dx + dy * dy
        if l2 < 1e-12:
            continue
        t = ((p[0] - s["x1"]) * dx + (p[1] - s["y1"]) * dy) / l2
        gap = (-t if t < 0 else (t - 1 if t > 1 else 0)) * math.sqrt(l2)
        if gap < best_gap:
            best_gap, best = gap, i
    return best if best_gap <= grace_mm else -1


def grid_inside(polygon: Polygon, pitch: float, margin: float = 0.0):
    """Grid points strictly inside `polygon` (holes excluded)."""
    minx, miny, maxx, maxy = polygon.bounds
    xs = np.arange(minx + pitch / 2, maxx, pitch)
    ys = np.arange(miny + pitch / 2, maxy, pitch)
    target = polygon if margin <= 0 else polygon.buffer(-margin)
    pp = prep(target)
    pts = [(float(x), float(y)) for x in xs for y in ys if pp.contains(Point(x, y))]
    return pts


def ring_samples(ring, every_mm: float):
    n = max(4, int(ring.length // every_mm))
    return [tuple(ring.interpolate(i * ring.length / n).coords[0]) for i in range(n)]


def panel_of_point(polygons, point):
    for pid, poly in polygons.items():
        if poly.contains(Point(point)):
            return pid
    return None


def holes_of(polygon: Polygon):
    return [Polygon(r) for r in polygon.interiors]


def dump(name, payload):
    path = OUT / name
    path.write_text(json.dumps(payload, indent=1, default=float), encoding="utf-8")
    return path
