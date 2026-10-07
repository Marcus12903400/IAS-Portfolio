"""Probe 4e: with an exact 180 degree matrix, do REAL pieces physically move,
or do only the stored origin floats change by ~1e-13?  (Process-local patch.)"""

from unittest import mock

import numpy as np

from probe_common import OPTIONS, OUT, build_pieces, dump_json, load_run, nesting


def exact_rotation(degrees):
    return {0: np.eye(2), 180: np.array([[-1.0, 0.0], [0.0, -1.0]])}[degrees]


def physical(sheet_list, nd=6):
    return tuple((p.sheet_index, p.piece_id, p.rotation_deg, round(float(p.offset[0]), nd), round(float(p.offset[1]), nd))
                 for s in sheet_list for p in s.placements)


R = load_run()["rotation"]
rows = []
for family in ("fitted", "random"):
    for seed in range(40):
        _s, pieces, _w = build_pieces(seed, family)
        base, bs, _ = nesting.nest(pieces, R, OPTIONS)
        with mock.patch.object(nesting, "_rotation", exact_rotation):
            alt, as_, _ = nesting.nest(pieces, R, OPTIONS)
        pb, pa = physical(base), physical(alt)
        moved = [(x, y) for x, y in zip(pb, pa) if x != y] if len(pb) == len(pa) else [("len", len(pb), len(pa))]
        max_origin_shift = max((float(np.max(np.abs(p.origin - q.origin))) for sp, sq in zip(base, alt)
                                for p, q in zip(sp.placements, sq.placements)), default=0.0)
        rows.append({"family": family, "seed": seed, "sheets": (bs["sheet_count"], as_["sheet_count"]),
                     "physical_moves": len(moved), "max_origin_shift_mm": max_origin_shift, "moved": moved[:3]})
        if moved:
            print(f"{family} seed {seed}: {len(moved)} placement(s) physically differ (offset rounded to 1e-6): {moved[:3]}")
print(f"sets with a physical move: {sum(1 for r in rows if r['physical_moves'])}/{len(rows)}; "
      f"largest origin float shift: {max(r['max_origin_shift_mm'] for r in rows):.3e} mm; "
      f"sheet count changes: {sum(1 for r in rows if r['sheets'][0] != r['sheets'][1])}")
dump_json(OUT / "probe4e_exact180_real.json", rows)
