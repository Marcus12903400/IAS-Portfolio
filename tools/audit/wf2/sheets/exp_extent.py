import json
from pathlib import Path
import numpy as np
from autodeck2 import sheets, sheetjob, nesting
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
RUN = S / "axisrun"
config = load_config()
options = sheets.settings(config)
step = options["sample_step_mm"]
loops, _p, _k = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
frame, _w = sheetjob.resolve_frame(RUN, options)
rotation = sheets.sheet_transform(frame.axis)
worst = {"extent_vs_variant0": 0.0, "extent_vs_variant180": 0.0, "variant_vs_placed_remeasure": 0.0}
mismatch = []
for name in ["seams.json", "seams_previous.json", "seams_optimiser_run.json.bak"]:
    seams = [sheets.Seam.from_dict(s) for s in json.loads((RUN / name).read_text(encoding="utf-8"))["seams"]]
    snapped, _r, _sw = sheetjob.apply_snap(seams, loops, frame.axis, options)
    pieces = []
    for pid in sorted(loops):
        outer, holes = sheets.classify_loops(loops[pid], step)
        pieces.extend(sheets.split_panel(pid, outer, holes, snapped, options)[0])
    over = {o["piece_id"] for o in sheets.oversize_report(pieces, rotation, options)}
    sheet_list, summary, _nw = nesting.nest(pieces, rotation, options)
    unplaced = set(summary["unplaced_piece_ids"])
    if over != unplaced:
        mismatch.append((name, over, unplaced))
    placements = {pl.piece_id: pl for s in sheet_list for pl in s.placements}
    for p in pieces:
        w, l = sheets.oriented_extent(p, rotation, step)
        shell, holes_ = nesting._sampled_rings(p, step)
        for angle in (0, 180):
            poly = nesting._piece_polygon(shell, holes_, nesting._rotation(angle) @ rotation)
            minx, miny, maxx, maxy = poly.bounds
            d = max(abs((maxx - minx) - w), abs((maxy - miny) - l))
            worst[f"extent_vs_variant{angle}"] = max(worst[f"extent_vs_variant{angle}"], d)
        pl = placements.get(p.piece_id)
        if pl is not None:
            b = nesting._placed_bounds(p, pl, rotation, step)
            d = max(abs((b[2] - b[0]) - pl.width_mm), abs((b[3] - b[1]) - pl.length_mm))
            worst["variant_vs_placed_remeasure"] = max(worst["variant_vs_placed_remeasure"], d)
            if nesting._refuse(p, pl, rotation, options):
                print("  REFUSED at export:", name, p.piece_id, nesting._refuse(p, pl, rotation, options)["reason"])
    print(name, "oversize", sorted(over), "unplaced", sorted(unplaced), "sheets", summary["sheet_count"])
print("worst differences (mm):", worst)
print("oversize/unplaced mismatches:", mismatch)
# tolerance asymmetry: oversize_report uses <= limit with no tolerance, nester uses > usable (no tol) for unplaced and > usable+1e-9 in _find_spot
print("limits:", options["max_part_width_mm"], options["max_part_length_mm"])
