"""Probe (5): pick_flat round trips.

flat (placed frame) -> _unplace -> lifter (MLS, what bridge draws with) -> world
  -> pick_flat -> flat again:  error in mm, per panel.
Also the test's direction: mesh vertex -> pick_flat -> unplace -> lift -> world.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import dump, grid_inside, load

ctx = load()
bridge, view, polygons = ctx["bridge"], ctx["view"], ctx["polygons"]
rng = np.random.default_rng(5)

out = {}
t0 = time.perf_counter(); bridge.warm_picking(view); out["warm_picking_s"] = time.perf_counter() - t0
try:
    import igl  # noqa: F401
    out["igl"] = True
except Exception:
    out["igl"] = False

for pid in sorted(polygons):
    pts = grid_inside(polygons[pid], 40.0 if pid == 1 else 8.0, margin=2.0)
    idx = rng.choice(len(pts), size=min(20 if pid == 1 else 10, len(pts)), replace=False)
    flat = np.array([pts[i] for i in idx], float)
    uv = bridge._unplace(view.placements[pid], flat)
    world = view.lifters[pid](uv)
    t0 = time.perf_counter(); picks = bridge.pick_flat(view, world); dt = (time.perf_counter() - t0) * 1000
    # unrounded path, the same arithmetic without the 0.01 mm rounding in pick_flat
    uv_back, dist = bridge.locator_for(view, pid)(world)
    flat_back = view.placements[pid].apply(uv_back)
    rows = []
    for f, p, fb, d, w in zip(flat, picks, flat_back, dist, world):
        err = float(np.hypot(p["x"] - f[0], p["y"] - f[1]))
        rows.append(dict(flat=f.tolist(), picked=[p["x"], p["y"]], panel=p["panel_id"], dist_mm=p["distance_mm"],
                         err_mm=err, err_unrounded_mm=float(np.hypot(fb[0] - f[0], fb[1] - f[1])), world=w.tolist()))
    errs = np.array([r["err_mm"] for r in rows])
    wrong = [r for r in rows if r["panel"] != pid]
    # and back out to the world through the lift, from the picked flat point
    picked_flat = np.array([[r["picked"][0], r["picked"][1]] for r in rows])
    world_back = view.lifters[pid](bridge._unplace(view.placements[pid], picked_flat))
    werr = np.linalg.norm(world_back - world, axis=1)
    out[f"panel_{pid}"] = dict(n=len(rows), pick_ms_for_batch=dt, wrong_panel=len(wrong),
                               flat_err_mm=dict(max=float(errs.max()), median=float(np.median(errs)), p95=float(np.percentile(errs, 95))),
                               surface_gap_mm=dict(max=float(max(r["dist_mm"] for r in rows)), median=float(np.median([r["dist_mm"] for r in rows]))),
                               world_err_mm=dict(max=float(werr.max()), median=float(np.median(werr))),
                               worst=sorted(rows, key=lambda r: -r["err_mm"])[:3], wrong_rows=wrong[:5])
    print(f"panel {pid}: n={len(rows)} flat err max {errs.max():.3f} mm median {np.median(errs):.3f} mm, "
          f"surface gap max {max(r['dist_mm'] for r in rows):.3f} mm, world err max {werr.max():.3f} mm, wrong panel {len(wrong)}")

# the test's direction, on the biggest panel: raw mesh vertices
pid = max(view.result.panels, key=lambda p: len(view.result.panels[p].development.uv_mm))
xyz = np.asarray(view.result.panels[pid].development.mesh.base_vertices_mm, float)
sample = xyz[np.linspace(0, len(xyz) - 1, 20, dtype=int)]
picks = bridge.pick_flat(view, sample)
back_err = []
for pick, original in zip(picks, sample):
    uv = bridge._unplace(view.placements[pick["panel_id"]], np.array([[pick["x"], pick["y"]]]))
    back = view.lifters[pick["panel_id"]](uv)[0]
    back_err.append(float(np.linalg.norm(back - original)))
out["mesh_vertices"] = dict(panel=pid, panels_picked=sorted({p["panel_id"] for p in picks}),
                            dist_max=max(p["distance_mm"] for p in picks),
                            back_err_mm=dict(max=max(back_err), median=float(np.median(back_err))))
print("mesh-vertex round trip:", out["mesh_vertices"])

# points off the deck
(miss,) = bridge.pick_flat(view, np.array([[0.0, 0.0, 50000.0]]))
out["miss"] = miss
print("miss", miss)
# a world point 30 mm above the deck (inside PICK_MAX_MM): picked as the panel below?
above = world[:3] + np.array([0.0, 0.0, 30.0])
out["above_30mm"] = bridge.pick_flat(view, above)
print("30 mm above the deck:", out["above_30mm"])
dump("pick_roundtrip.json", out)
