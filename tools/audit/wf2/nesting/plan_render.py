"""plan(write_files=True) into the SCRATCH copy of the axis run, then verify the DXFs: layers, closed, units, bulge under 180."""
import sys, json, subprocess
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
sys.path.insert(0, str(S))
from autodeck2 import nesting, sheetjob, sheets
from autodeck2.config import load_config
import ezdxf
from shapely.geometry import Polygon, LineString
from shapely import hausdorff_distance
sys.path.insert(0, r"D:/AutoDeck/tools")
import render_sheets

for name in ("axis", "noaxis"):
    work = S / "runs" / name
    cfg = load_config()
    res = sheetjob.plan(work, cfg, write_files=True)
    print(f"== {name}: status={res['status']} sheets={res['summary']['sheet_count']} pieces={res['piece_count']} unplaced={res['summary']['unplaced_piece_ids']} refused={[r['piece_id'] for r in res['refused']]}")
    for f in res["files"]:
        print(f"   {f['name']} units={f['units']} cam={f['cam_polylines']} closed={f['all_closed']} pattern_lines={f['pattern_lines']} pieces={f['pieces']} util={f['utilisation']}")
        print(f"      layers: {f['layer_counts']}")
    for s in res["sheets"]:
        for p in s["placements"]:
            print(f"   sheet {s['sheet']} {p['piece_id']:6s} rot={p['rotation_deg']:3d} offset={p['offset_mm']} size={p['width_mm']}x{p['length_mm']}")
    # independent check of each DXF: reconstruct arcs from bulges (as VCarve would) and compare with the
    # nester's own placed sampled outline; also check pairwise clearance and envelope from the DXF itself
    options = sheets.settings(cfg)
    source = sheetjob.source_dxf(work)
    loops, pattern, kind = sheets.read_fitted_dxf(source)
    frame, _ = sheetjob.resolve_frame(work, options)
    rotation = sheets.sheet_transform(frame.axis)
    seam_list = [sheets.Seam.from_dict(d) for d in res["seams"]]
    pieces = {}
    for pid in sorted(loops):
        outer, holes = sheets.classify_loops(loops[pid], 1.0)
        if outer is None: continue
        for pc in sheets.split_panel(pid, outer, holes, seam_list, options)[0]:
            pieces[pc.piece_id] = pc
    mx = (options["sheet_width_mm"] - options["max_part_width_mm"]) / 2; my = (options["sheet_length_mm"] - options["max_part_length_mm"]) / 2
    for s in res["sheets"]:
        path = work / f"sheet_{s['sheet']:02d}.dxf"
        doc = ezdxf.readfile(str(path))
        polys = {}
        for e in doc.modelspace().query("LWPOLYLINE"):
            layer = e.dxf.layer
            if not layer.startswith("CAM__"): continue
            pts = render_sheets.sample_lwpolyline(e, 1.0)
            polys.setdefault(layer, []).append(Polygon(pts))
        outers = {}
        for layer, plist in polys.items():
            plist.sort(key=lambda g: -g.area)
            outer = plist[0]
            for h in plist[1:]:
                outer = outer.difference(h)
            outers[layer] = outer
            b = outer.bounds
            over = max(mx - b[0], b[2] - (mx + options["max_part_width_mm"]), my - b[1], b[3] - (my + options["max_part_length_mm"]))
            if over > 0: print(f"   !! {layer} pokes {over:.3f} mm outside the usable area")
        names = list(outers)
        worst = 1e9
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                d = outers[names[i]].distance(outers[names[j]])
                worst = min(worst, d)
                if d < options["part_spacing_mm"] - 0.05:
                    print(f"   !! {names[i]} <-> {names[j]} only {d:.3f} mm apart")
        # bulge check: DXF arcs vs the nester's placed sampled outline
        for p in s["placements"]:
            pc = pieces[p["piece_id"]]
            pl = nesting.Placement(p["piece_id"], p["panel_id"], s["sheet"] - 1, p["rotation_deg"], np.array(p["offset_mm"]), np.array(p["origin_mm"]))
            expected = Polygon(pl.apply(sheets.sample_loop(pc.outer, 1.0)[0], rotation))
            layer = nesting._layer("CAM", p["piece_id"])
            got = max(polys[layer], key=lambda g: g.area)
            hd = hausdorff_distance(expected.exterior, got.exterior)
            bul = np.abs(pc.outer.bulges).max()
            flag = "" if hd < 0.6 else "   <-- MISMATCH"
            print(f"   sheet {s['sheet']} {p['piece_id']:6s} rot={p['rotation_deg']:3d} max|bulge|={bul:.3f} hausdorff(DXF arcs vs placed outline)={hd:.3f} mm{flag}")
        print(f"   sheet {s['sheet']}: closest pair in DXF = {worst:.2f} mm; header $INSUNITS={doc.header.get('$INSUNITS')}  layers={[l.dxf.name for l in doc.layers if not l.dxf.name.startswith(('Defpoints','0'))][:12]}")
    out = S / f"{name}_sheets.png"
    r = subprocess.run([sys.executable, r"D:/AutoDeck/tools/render_sheets.py", "--run", str(work), "--out", str(out)], capture_output=True, text=True)
    print("   render:", r.returncode, (r.stdout + r.stderr).strip()[-300:])
