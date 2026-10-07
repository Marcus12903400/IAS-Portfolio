"""Probe 4d: isolate the two mechanisms behind the 45-vs-55 packing with
PROCESS-LOCAL monkeypatches of the nester (nothing on disk is touched):

  A. `_rotation(180)` built from cos/sin(pi) is not exactly [[-1,0],[0,-1]];
     patch it to the exact matrix and re-run the 60 squares at spacing 20.
  B. a candidate that merely touches the spacing buffer is 'blocked'
     (`intersects`); patch `_commit`'s buffer to spacing - 1e-9 and re-run.

Then: do either of these patches move a REAL piece (fitted seeds 0-9)?
"""

import math
from unittest import mock

import numpy as np
from shapely import affinity
from shapely.ops import unary_union

from probe_common import OPTIONS, OUT, build_pieces, dump_json, layout, load_run, nesting, rect_loop, sheets, verify

AX = sheets.sheet_transform(np.array([1.0, 0.0]))
print("nesting._rotation(180) =", nesting._rotation(180).tolist(), " (sin(pi) =", math.sin(math.pi), ")")
out = {}


def squares():
    return [sheets.Piece(f"sq{i:02d}", 1, rect_loop(150.0, 150.0), [], 22500.0) for i in range(60)]


def exact_rotation(degrees):
    return {0: np.eye(2), 180: np.array([[-1.0, 0.0], [0.0, -1.0]])}[degrees]


original_commit = nesting._commit


def commit_with_shrunk_buffer(sheet, occupancy, piece, variants, origins, spot, margin_x, margin_y, spacing):
    return original_commit(sheet, occupancy, piece, variants, origins, spot, margin_x, margin_y, spacing - 1e-9)


def run(label, pieces, rotation=AX, options=OPTIONS):
    sheet_list, summary, _w = nesting.nest(pieces, rotation, options)
    report = verify(pieces, sheet_list, summary, rotation, options, single_nest_check=False)
    per = [len(s.placements) for s in sheet_list]
    rot = sum(1 for s in sheet_list for p in s.placements if p.rotation_deg == 180)
    print(f"{label:45s}: sheets={summary['sheet_count']} per_sheet={per} rot180={rot} "
          f"min_clr={report['min_clearance_mm']:.4f} violations={len(report['violations'])}")
    out[label] = {"sheets": summary["sheet_count"], "per_sheet": per, "rot180": rot, "min_clr": report["min_clearance_mm"]}
    return sheet_list


run("squares: production", squares())
with mock.patch.object(nesting, "_rotation", exact_rotation):
    run("squares: A exact 180 matrix", squares())
with mock.patch.object(nesting, "_commit", commit_with_shrunk_buffer):
    run("squares: B touching allowed", squares())
with mock.patch.object(nesting, "_rotation", exact_rotation), mock.patch.object(nesting, "_commit", commit_with_shrunk_buffer):
    run("squares: A + B", squares())

# real pieces: does either patch move anything?
runinfo = load_run()
R = runinfo["rotation"]
moved_a = moved_b = 0
changed_sheets_a = changed_sheets_b = 0
for seed in range(10):
    _s, pieces, _w = build_pieces(seed, "fitted")
    base, bs, _ = nesting.nest(pieces, R, OPTIONS)
    with mock.patch.object(nesting, "_rotation", exact_rotation):
        a, as_, _ = nesting.nest(pieces, R, OPTIONS)
    with mock.patch.object(nesting, "_commit", commit_with_shrunk_buffer):
        b, bs_, _ = nesting.nest(pieces, R, OPTIONS)
    la, lb, l0 = layout(a), layout(b), layout(base)
    moved_a += la != l0
    moved_b += lb != l0
    changed_sheets_a += as_["sheet_count"] != bs["sheet_count"]
    changed_sheets_b += bs_["sheet_count"] != bs["sheet_count"]
    diff_a = sum(1 for x, y in zip(la, l0) if x != y) if len(la) == len(l0) else -1
    diff_b = sum(1 for x, y in zip(lb, l0) if x != y) if len(lb) == len(l0) else -1
    print(f"fitted seed {seed}: sheets prod={bs['sheet_count']} exact180={as_['sheet_count']} touching={bs_['sheet_count']}; "
          f"placements differing: exact180={diff_a} touching={diff_b}")
print(f"\nreal pieces (10 fitted sets): exact-180 patch changed the layout in {moved_a} sets (sheet count in {changed_sheets_a}); "
      f"touching-allowed patch changed the layout in {moved_b} sets (sheet count in {changed_sheets_b})")
out["real_pieces"] = {"exact180_layout_changes": moved_a, "exact180_sheet_changes": changed_sheets_a,
                      "touching_layout_changes": moved_b, "touching_sheet_changes": changed_sheets_b}
dump_json(OUT / "probe4d_rootcause.json", out)
