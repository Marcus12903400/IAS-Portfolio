"""Probe 4c: the 60 x (150 x 150) job at part spacings just off 20 mm, to show
that the 45-per-sheet result is the 'touching counts as blocked' rule plus the
180 degree rounding asymmetry, not the sheet geometry."""

import numpy as np

from probe_common import OPTIONS, OUT, dump_json, nesting, rect_loop, sheets, verify

AX = sheets.sheet_transform(np.array([1.0, 0.0]))
out = {}
for spacing in (20.0, 19.999, 20.001, 22.0, 24.999, 25.0):
    for allow in (True, False):
        opts = {**OPTIONS, "part_spacing_mm": spacing, "allow_180_rotation": allow}
        squares = [sheets.Piece(f"sq{i:02d}", 1, rect_loop(150.0, 150.0), [], 22500.0) for i in range(60)]
        sheet_list, summary, warnings = nesting.nest(squares, AX, opts)
        report = verify(squares, sheet_list, summary, AX, opts, single_nest_check=False)
        per_sheet = [len(s.placements) for s in sheet_list]
        rot = sum(1 for s in sheet_list for p in s.placements if p.rotation_deg == 180)
        print(f"spacing {spacing:7.3f} allow_180={allow!s:5}: sheets={summary['sheet_count']} per_sheet={per_sheet} "
              f"rot180={rot} min_clr={report['min_clearance_mm']:.3f} violations={len(report['violations'])}")
        out[f"{spacing}_{allow}"] = {"sheets": summary["sheet_count"], "per_sheet": per_sheet, "rot180": rot,
                                     "min_clearance": report["min_clearance_mm"], "violations": report["violations"]}
dump_json(OUT / "probe4c_spacing_density.json", out)
