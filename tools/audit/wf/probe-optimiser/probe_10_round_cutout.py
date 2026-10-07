"""The 207 mm round cut-out of panel 1 is dropped from any seam-cut piece that
fully contains it: sheets._rebuild_ring rebuilds the full-circle hole as a
2-vertex bulge loop and split_panel (sheets.py:576) keeps only holes with >= 3
vertices.  Checks the optimiser's default layout (scratch copy runs/main, whose
seams.json and sheet_01.dxf were written by probe_01_main.py), the AXIS run's own
hand seams, the uncut panel, and the other two fixture runs' own seams.json.

Re-run (after probe_01_main.py):
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_10_round_cutout.py"
"""

import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
from common import AXIS_RUN, RUNS, SCRATCH  # noqa: E402
from autodeck2 import sheets, sheetjob  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

config = load_config()
options = sheets.settings(config)
step = 1.0
work = SCRATCH / "runs" / "main"
loops, _p, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(work))
outer, holes = sheets.classify_loops(loops[1], step)
frame, _ = sheetjob.resolve_frame(work, options)
rotation = sheets.sheet_transform(frame.axis)
for h in holes:
    pts, _s = sheets.sample_loop(h, step)
    poly = Polygon(pts)
    c = np.array(poly.centroid.coords[0]) @ rotation.T
    print(f"panel-1 hole: area {poly.area:9.0f} mm2, centroid sheet-frame {c.round(1).tolist()}, vertices {len(h.vertices)}, bulges {np.round(h.bulges, 3).tolist()[:4]}")
result = sheetjob.plan(work, config, write_files=False)
seams = [sheets.Seam.from_dict(s) for s in result["seams"]]
panel = sheets.loop_polygon(outer, holes, step)
print("panel polygon valid:", panel.is_valid, "interiors:", len(panel.interiors))
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
kerfs = [s.extended(reach).buffer(3.0, cap_style=2, join_style=2) for s in seams if s.panel_id == 1]
remainder = panel.difference(unary_union(kerfs))
big = sorted(remainder.geoms, key=lambda g: (-g.area, g.bounds))[0]
print("largest remainder part (P1-1) area", round(big.area), "interiors", len(big.interiors), [round(Polygon(r).area) for r in big.interiors])
sources = []
for loop in [outer, *holes]:
    pts, src = sheets.sample_loop(loop, step)
    sources.append((loop, pts, src))
for ring in big.interiors:
    rebuilt, err = sheets._rebuild_ring(np.asarray(ring.coords)[:-1], sources, float(options["arc_rebuild_tolerance_mm"]), step)
    print("  interior ring pts", len(ring.coords) - 1, "-> rebuilt vertices", len(rebuilt.vertices), "bulges",
          np.round(rebuilt.bulges, 4).tolist(), "err", round(err, 4),
          "KEPT" if len(rebuilt.vertices) >= 3 else "DROPPED by split_panel (sheets.py:576 needs >= 3 vertices)")
pieces, warn = sheets.split_panel(1, outer, holes, seams, options)
print("split_panel P1 pieces -> holes:", [(p.piece_id, len(p.holes)) for p in pieces], "warnings:", warn)
if (work / "sheet_01.dxf").is_file():
    import ezdxf
    doc = ezdxf.readfile(str(work / "sheet_01.dxf"))
    print("sheet_01.dxf entities by layer (LINEs omitted):",
          Counter((e.dxf.layer, e.dxftype()) for e in doc.modelspace() if e.dxftype() != "LINE"))


def holes_report(run_dir, label):
    res = sheetjob.plan(run_dir, config, write_files=False)
    lps, _p, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(run_dir))
    o, h = sheets.classify_loops(lps[1], step)
    circ = [x for x in h if len(x.vertices) == 2]
    pcs = [p for p in res["pieces"] if p["panel_id"] == 1]
    print(f"{label}: status {res['status']}, panel-1 holes {len(h)} (2-vertex circles: {len(circ)}), pieces {res['piece_count']}, seams {res['seam_count']}")
    print("   panel-1 pieces holes:", [(p["piece_id"], p["holes"]) for p in pcs], " total holes kept:", sum(p["holes"] for p in pcs))
    if circ:
        cp = Polygon(sheets.sample_loop(circ[0], step)[0])
        ss = [sheets.Seam.from_dict(s) for s in res["seams"]]
        crossed = any(s.extended(reach).buffer(3.0).intersects(cp) for s in ss if s.panel_id in (None, 1))
        print("   circle crossed by a seam kerf:", crossed, "-> if not crossed it must survive as a hole in exactly one piece")


def copy_run(src, name, with_seams):
    dst = SCRATCH / "runs" / name
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for n in ("final_auto.dxf", "run.json", "final.dxf") + (("seams.json",) if with_seams else ()):
        if (src / n).exists():
            shutil.copyfile(src / n, dst / n)
    return dst


holes_report(copy_run(AXIS_RUN, "hand", True), "AXIS run, its own seams.json (4 hand seams)")
holes_report(copy_run(AXIS_RUN, "nocut", False), "AXIS run, no seams")
for rid in ("21kwcockpit-3-20260902-024834", "21kwcockpit-2-20260901-184924"):
    if (RUNS / rid / "final_auto.dxf").exists():
        holes_report(copy_run(RUNS / rid, "other_" + rid[:14], True), f"{rid}, its own seams.json")
