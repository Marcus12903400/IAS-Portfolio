"""H1: does a hovered chord (one side of the console) cut only that chord, or the whole line?"""
import sys, json, shutil
from pathlib import Path
sys.path[:0] = ['engine-v1/src', 'engine', 'app']
import numpy as np
from shapely.geometry import Point, LineString
from autodeck2 import sheetjob, sheets, seamplace, seamsnap
from autodeck2.config import load_config

S = Path(sys.argv[1])
src = Path('engine/outputs/runs/21kwcockpit-1-20260901-180939')
work = S / src.name
work.mkdir(parents=True, exist_ok=True)
for n in ('final_auto.dxf', 'run.json', 'panels.json'):
    shutil.copyfile(src / n, work / n)
config = load_config()
options = sheets.settings(config)
loops, pattern, kind = sheets.read_fitted_dxf(work / 'final_auto.dxf')
polys = seamplace.panel_polygons(loops, options)
frame, warn = sheetjob.resolve_frame(work, options)
along, across = seamsnap.master_directions(frame.axis)
p1 = polys[1]
print('panel1 bounds', [round(v) for v in p1.bounds], 'holes', len(p1.interiors))
holes = sorted(p1.interiors, key=lambda r: -abs(r.area if hasattr(r,'area') else 0))
from shapely.geometry import Polygon
hole_polys = sorted([Polygon(r) for r in p1.interiors], key=lambda g: -g.area)
console = hole_polys[0]
print('console bounds', [round(v) for v in console.bounds], 'area', round(console.area))
cx, cy = console.centroid.x, console.centroid.y
minx, miny, maxx, maxy = p1.bounds

def run(label, point, unit, mode):
    segs = seamplace.seam_through(point, unit, polys, options)
    # the chord under the pointer (same rule as app.js segmentUnder)
    best = None; bestgap = 1e9
    for s in segs:
        a = np.array([s['x1'], s['y1']]); b = np.array([s['x2'], s['y2']])
        d = b - a; L2 = d @ d
        t = ((point - a) @ d) / L2
        gap = (0 if 0 <= t <= 1 else (-t if t < 0 else t - 1)) * np.sqrt(L2)
        if gap < bestgap: bestgap, best = gap, s
    print(f'\n== {label}: hover point {np.round(point,1).tolist()} -> {len(segs)} chords; under pointer: '
          f'panel {best["panel_id"]} len {best["length_mm"]:.0f} mm from {round(best["x1"])},{round(best["y1"])} to {round(best["x2"])},{round(best["y2"])}')
    seam = sheets.Seam('h', best['x1'], best['y1'], best['x2'], best['y2'], panel_id=best['panel_id'],
                       snap=True, raw=(best['x1'], best['y1'], best['x2'], best['y2']), mode=mode)
    res = sheetjob.plan(work, config, seams=[seam], write_files=False)
    pcs = [(p['piece_id'], p['panel_id'], round(p['area_mm2'])) for p in res['pieces'] if p['panel_id'] == 1]
    print('   plan pieces from panel 1:', pcs)
    print('   seam as cut:', {k: (round(v, 1) if isinstance(v, float) else v) for k, v in res['seams'][0].items() if k in ('x1','y1','x2','y2','mode')})
    print('   snap note:', res['seam_snaps'][0].get('note'))
    # what the drawn chord alone would cut (no extension): subtract just the chord's kerf
    kerf = LineString([(best['x1'], best['y1']), (best['x2'], best['y2'])]).buffer(3.0, cap_style=2)
    rem = p1.difference(kerf)
    n = len(rem.geoms) if hasattr(rem, 'geoms') else 1
    print(f'   if only the drawn chord were cut: {n} piece(s)')
    return res

# A: across seam in the strip between the console and the +y gunwale, at the console's x
pt = np.array([cx, (console.bounds[3] + maxy) / 2.0])
assert p1.contains(Point(*pt)), 'point A not on panel'
run('A across, starboard strip only', pt, across, 'across')

# B: along seam in the bow extension (far +x), at y of the console centroid
pt = np.array([maxx - 250.0, cy])
if not p1.contains(Point(*pt)):
    # walk left until on panel
    for dx in range(0, 1500, 25):
        pt = np.array([maxx - 250.0 - dx, cy])
        if p1.contains(Point(*pt)): break
print('point B on panel?', p1.contains(Point(*pt)))
run('B along, bow extension only', pt, along, 'along')

# C: across seam far aft, clear of the console (should be one full-width cut either way)
pt = np.array([minx + 400.0, cy])
print('point C on panel?', p1.contains(Point(*pt)))
run('C across, aft of console (control)', pt, across, 'across')
