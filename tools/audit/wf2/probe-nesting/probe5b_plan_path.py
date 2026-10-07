"""Probe 5b: the PRODUCTION path.  Copy the scratch run, write one seam set
into its seams.json, call sheetjob.plan(write_files=True) on the COPY, and
compare the sheet DXFs it writes with the ones probe5 wrote by calling
nesting.write_sheet_dxfs directly on the same pieces.  Also reads back
sheets.json / sheet_report.md.  Usage: probe5b_plan_path.py [family] [seed]
"""

import json
import shutil
import sys

import ezdxf
import numpy as np

from probe_common import CONFIG, HERE, OUT, RUN, build_pieces, dump_json, sheetjob, sheets

family = sys.argv[1] if len(sys.argv) > 1 else "fitted"
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7

copy = HERE / "runs" / f"axis_plan_{family}_{seed:03d}"
if copy.exists():
    shutil.rmtree(copy)
shutil.copytree(RUN, copy)
seams, pieces, _w = build_pieces(seed, family)
sheets.write_seams(copy, seams)

log = []
result = sheetjob.plan(copy, CONFIG, progress=log.append, write_files=True)
print("status:", result["status"], " sheets:", result["summary"]["sheet_count"], " pieces:", result["piece_count"],
      " unplaced:", result["summary"]["unplaced_piece_ids"], " refused:", result["refused"])
print("seam_snaps applied:", [s["applied"] for s in result["seam_snaps"]])
print("warnings:", result["warnings"])
print("files:", [(f["name"], f["cam_polylines"], f["all_closed"], f["pattern_lines"], f["units"]) for f in result["files"]])
print("sheets.json exists:", (copy / "sheets.json").is_file(), " report exists:", (copy / "sheet_report.md").is_file())

# the plan's seams must be my seams, bit for bit (snap=False)
same_seams = all(abs(a["x1"] - b.x1) == 0 and abs(a["y1"] - b.y1) == 0 and abs(a["x2"] - b.x2) == 0 and abs(a["y2"] - b.y2) == 0
                 for a, b in zip(result["seams"], seams))
print("plan used the seams unchanged:", same_seams)


def entities(path):
    doc = ezdxf.readfile(str(path))
    polys = []
    lines = []
    for e in doc.modelspace():
        if e.dxftype() == "LWPOLYLINE":
            polys.append((str(e.dxf.layer), np.asarray([(x, y, b) for x, y, b in e.get_points("xyb")], dtype=float)))
        elif e.dxftype() == "LINE":
            lines.append((str(e.dxf.layer), (e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y)))
    return polys, lines


direct_dir = OUT / f"export_{family}_{seed:03d}"
report = []
for path in sorted(copy.glob("sheet_*.dxf")):
    other = direct_dir / path.name
    if not other.is_file():
        print(f"{path.name}: no direct-export counterpart in {direct_dir}")
        continue
    p1, l1 = entities(path)
    p2, l2 = entities(other)
    same_layers = [a[0] for a in p1] == [a[0] for a in p2]
    worst = 0.0
    if len(p1) == len(p2):
        for (la, va), (lb, vb) in zip(p1, p2):
            if va.shape != vb.shape:
                worst = float("inf")
                break
            worst = max(worst, float(np.max(np.abs(va - vb))))
    else:
        worst = float("inf")
    same_lines = l1 == l2
    print(f"{path.name}: polylines {len(p1)} vs {len(p2)} same_layers={same_layers} max|dxyb diff|={worst:.3e}  lines {len(l1)} vs {len(l2)} identical={same_lines}")
    report.append({"file": path.name, "polylines": (len(p1), len(p2)), "same_layers": same_layers,
                   "max_abs_diff": worst, "lines": (len(l1), len(l2)), "lines_identical": same_lines})

text = (copy / "sheet_report.md").read_text(encoding="utf-8")
print("\n--- sheet_report.md (first 40 lines) ---")
print("\n".join(text.splitlines()[:40]))
dump_json(OUT / f"probe5b_{family}_{seed:03d}.json", {"result_status": result["status"], "files": report,
                                                       "same_seams": same_seams, "warnings": result["warnings"]})
