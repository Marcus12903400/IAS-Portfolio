"""Exercise the exact bridge calls the routes make, on a scratch COPY of the AXIS run."""
import json, sys, time, copy
from pathlib import Path
run = Path(__file__).parent / "axisrun"
from autodeck_app import bridge

def post(seams, overrides=None):
    t = time.perf_counter()
    r = bridge.sheet_preview(run, overrides or {}, seams=seams, save=True, view=None)
    return r, time.perf_counter() - t

# 1. GET-equivalent: read seams.json, engine defaults, no save
t = time.perf_counter()
g = bridge.sheet_preview(run, {}, view=None)
print(f"GET /api/sheets equivalent: {time.perf_counter()-t:.2f}s available={g['available']} status={g.get('status')} "
      f"seams={len(g['seams'])} oversize={[o['piece_id'] for o in g.get('oversize',[])]} sheets={len(g['preview']['sheets'])}")
ids_before = [s['seam_id'] for s in g['seams']]
print("ids on disk:", ids_before)

# 2. remove s1 (not the last) then place a new seam with NO seam_id, as app.js placeHovered does
remaining = [s for s in g['seams'] if s['seam_id'] != 's1']
new = {"x1": -1368.6, "y1": -598.3, "x2": -1341.8, "y2": -1015.2, "panel_id": 1, "mode": "across",
       "angle_deg": None, "snap": True, "raw": [-1368.6, -598.3, -1341.8, -1015.2]}
r, dt = post(remaining + [new])
print(f"POST after remove+place: {dt:.2f}s ids written = {[s['seam_id'] for s in r['seams']]}")
disk = json.loads((run/'seams.json').read_text())['seams']
print("ids in seams.json:", [s['seam_id'] for s in disk], "duplicates:", len(disk) - len({s['seam_id'] for s in disk}))

# 3. POST an empty list with the plan 'failing' (oversize) -- does it save?
r, dt = post([])
print(f"POST []: {dt:.2f}s status={r.get('status')} http-would-be 200; oversize={[o['piece_id'] for o in r.get('oversize',[])]}")
print("seams.json exists:", (run/'seams.json').exists(), "content:", (run/'seams.json').read_text()[:80] if (run/'seams.json').exists() else None)

# 4. seam gap 0 as the Settings box allows (min="0")
try:
    post(remaining, {"seam_gap_mm": 0.0})
    print("seam_gap_mm=0 accepted")
except Exception as e:
    print("seam_gap_mm=0 ->", type(e).__name__, str(e)[:160])

# 5. snap sent as a string the way a form might
for v in ["false", "no", "maybe", 0, 2]:
    try:
        r, dt = post([dict(remaining[0], snap=v)])
        print(f"snap={v!r} -> stored snap={r['seams'][0]['snap']}")
    except Exception as e:
        print(f"snap={v!r} -> {type(e).__name__}: {str(e)[:100]}")

# 6. panel_id oddities
for v in [2.7, True, "abc", 99]:
    try:
        r, dt = post([dict(remaining[0], panel_id=v)])
        print(f"panel_id={v!r} -> stored panel_id={r['seams'][0]['panel_id']} pieces={r['piece_count']}")
    except Exception as e:
        print(f"panel_id={v!r} -> {type(e).__name__}: {str(e)[:100]}")
