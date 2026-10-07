"""Which cut-outs the proposed seams cut through, what is left of them on each
side, and where the tiny pieces sit -- for the 0.35 (default) and 0.0 layouts.

Re-run (after probe_01 and probe_03):
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_07_features.py"
"""

import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
from common import AXIS_RUN, HAND_PLACED, SCRATCH, stage  # noqa: E402
from autodeck2 import sheetjob, sheets  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

OUT = SCRATCH / "out"
HOLE_NAMES = {0: "console", 1: "round 207 mm", 2: "rect stbd-aft", 3: "rect stbd-fwd", 4: "rect port-aft", 5: "rect port-fwd"}


def main():
    config = load_config()
    options = sheets.settings(config)
    step = 1.0
    work = stage("features", AXIS_RUN, HAND_PLACED)
    loops, _p, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(work))
    frame, _w = sheetjob.resolve_frame(work, options)
    rotation = sheets.sheet_transform(frame.axis)
    outer, holes = sheets.classify_loops(loops[1], step)
    hole_polys = [Polygon(sheets.sample_loop(h, step)[0]) for h in holes]
    panel = sheets.loop_polygon(outer, holes, step)
    reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0

    main_seams = json.loads((OUT / "main_result.json").read_text(encoding="utf-8"))["result"]["seams"]
    tidy = json.loads((OUT / "tidiness.json").read_text(encoding="utf-8"))
    for label, payload in (("w0.35 default", main_seams), ("w0.0", tidy["0.0"]["seams"])):
        print(f"\n== {label} ==")
        result = sheetjob.plan(work, config, seams=[sheets.Seam.from_dict(s) for s in payload], write_files=False)
        seams = [sheets.Seam.from_dict(s) for s in result["seams"] if s["panel_id"] in (None, 1)]
        for index, hole in enumerate(hole_polys):
            hits = []
            for seam in seams:
                kerf = seam.extended(reach).buffer(3.0, cap_style=2, join_style=2)
                if kerf.intersects(hole):
                    remains = hole.difference(kerf)
                    parts = list(getattr(remains, "geoms", [remains])) if not remains.is_empty else []
                    sizes = []
                    for part in parts:
                        pts = np.asarray(part.exterior.coords) @ rotation.T
                        sizes.append((round(float(part.area)), round(float(np.ptp(pts[:, 0])), 1), round(float(np.ptp(pts[:, 1])), 1)))
                    hits.append((seam.seam_id, sizes))
            c = np.array(hole.centroid.coords[0]) @ rotation.T
            b = np.asarray(hole.exterior.coords) @ rotation.T
            print(f"  hole {index} ({HOLE_NAMES.get(index)}): sheet-frame X {b[:,0].min():.1f}..{b[:,0].max():.1f}, "
                  f"Y {b[:,1].min():.1f}..{b[:,1].max():.1f}; crossed by: {hits or 'nothing'}")
        # tiny pieces and where they are
        pieces, _pw = sheets.split_panel(1, outer, holes, [sheets.Seam.from_dict(s) for s in result["seams"]], options)
        for piece in pieces:
            poly = piece.polygon(step)
            if poly.area >= 40000:
                continue
            pts = np.asarray(poly.exterior.coords) @ rotation.T
            console_gap = float(poly.distance(hole_polys[0]))
            print(f"  small piece {piece.piece_id}: {poly.area:.0f} mm2, sheet-frame X {pts[:,0].min():.0f}..{pts[:,0].max():.0f} "
                  f"Y {pts[:,1].min():.0f}..{pts[:,1].max():.0f}, distance to console cut-out {console_gap:.1f} mm, "
                  f"holes {len(poly.interiors)}")
        along = [s for s in seams if s.mode == "along"]
        across = [s for s in seams if s.mode == "across"]
        print("  along cut sheet-X:", [round(float(np.dot(rotation[0], [(s.x1 + s.x2) / 2, (s.y1 + s.y2) / 2])), 1) for s in along],
              " across cut sheet-Y:", [round(float(np.dot(rotation[1], [(s.x1 + s.x2) / 2, (s.y1 + s.y2) / 2])), 1) for s in across])
        # console walls / fillet radius
        b = np.asarray(hole_polys[0].exterior.coords) @ rotation.T
        print(f"  console cut-out sheet-frame X {b[:,0].min():.1f}..{b[:,0].max():.1f}, Y {b[:,1].min():.1f}..{b[:,1].max():.1f}")


if __name__ == "__main__":
    main()
