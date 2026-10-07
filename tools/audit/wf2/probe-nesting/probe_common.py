"""Shared plumbing for the nester probes.  READ-ONLY on D:/AutoDeck.

Everything here points at the scratch copy of the AXIS run in ./runs/axis and
writes only into ./out.  Run any probe with:

  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
      .venv/Scripts/python.exe <scratch>/probe-nesting/probeN_xxx.py
"""

from __future__ import annotations

import dataclasses
import json
import math
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
REPO = Path("D:/AutoDeck")
for sub in ("engine-v1/src", "engine", "app"):
    candidate = str(REPO / sub)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from autodeck2 import nesting, seamsnap, sheetjob, sheets  # noqa: E402
from autodeck2.config import load_config  # noqa: E402
from autodeck2.sheets import Piece, Seam  # noqa: E402

RUN = HERE / "runs" / "axis"             # scratch copy of 21kwcockpit-1-20260901-180939
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

CONFIG = load_config()
OPTIONS = sheets.settings(CONFIG)
STEP = float(OPTIONS["sample_step_mm"])
USABLE_W = float(OPTIONS["max_part_width_mm"])
USABLE_L = float(OPTIONS["max_part_length_mm"])
MARGIN_X = (float(OPTIONS["sheet_width_mm"]) - USABLE_W) / 2.0
MARGIN_Y = (float(OPTIONS["sheet_length_mm"]) - USABLE_L) / 2.0
SPACING = float(OPTIONS["part_spacing_mm"])
ENVELOPE = box(MARGIN_X, MARGIN_Y, MARGIN_X + USABLE_W, MARGIN_Y + USABLE_L)

_RUN_CACHE: dict | None = None


def load_run() -> dict:
    """Fitted loops, pattern, axis and rotation of the scratch AXIS run."""

    global _RUN_CACHE
    if _RUN_CACHE is not None:
        return _RUN_CACHE
    assert RUN.is_dir(), f"scratch run copy missing: {RUN}"
    source = sheetjob.source_dxf(RUN)
    loops, pattern, kind = sheets.read_fitted_dxf(source)
    frame, warnings = sheetjob.resolve_frame(RUN, OPTIONS)
    axis = frame.axis
    rotation = sheets.sheet_transform(axis)
    masters = seamsnap.master_directions(axis)
    panels: dict[int, tuple] = {}
    for pid in sorted(loops):
        outer, holes = sheets.classify_loops(loops[pid], STEP)
        if outer is None:
            continue
        polygon = sheets.loop_polygon(outer, holes, STEP)
        panels[pid] = (outer, list(holes), polygon)
    _RUN_CACHE = dict(source=source, loops=loops, pattern=pattern, kind=kind, axis=axis,
                      rotation=rotation, masters=masters, panels=panels, frame=frame,
                      warnings=warnings)
    return _RUN_CACHE


# ---------------------------------------------------------------- seam sets

MIN_CHORD_MM = 20.0
MIN_SEPARATION_MM = 50.0


def _projected_extent(polygon: Polygon, direction: np.ndarray) -> tuple[float, float]:
    pts = np.asarray(polygon.exterior.coords, dtype=float)
    proj = pts @ direction
    return float(proj.min()), float(proj.max())


def full_span_seam(seam_id: str, polygon: Polygon, point: np.ndarray, direction: np.ndarray,
                   panel_id: int, mode: str) -> Seam | None:
    """The chord of the infinite line through `point` along `direction`, from
    the first to the last crossing of the panel's OUTER boundary (edge to
    edge, straight through any cut-out), or None if it misses or only grazes."""

    minx, miny, maxx, maxy = polygon.bounds
    reach = math.hypot(maxx - minx, maxy - miny) + 10.0
    line = LineString([point - direction * reach, point + direction * reach])
    clipped = Polygon(polygon.exterior).intersection(line)
    if clipped.is_empty:
        return None
    distances = []
    for part in getattr(clipped, "geoms", [clipped]):
        for c in np.asarray(getattr(part, "coords", []), dtype=float):
            distances.append(float(np.dot(c[:2] - point, direction)))
    if not distances or max(distances) - min(distances) < MIN_CHORD_MM:
        return None
    start = point + direction * min(distances)
    end = point + direction * max(distances)
    return Seam(seam_id, float(start[0]), float(start[1]), float(end[0]), float(end[1]),
                panel_id=panel_id, snap=False, raw=None, mode=mode)


def _draw_offsets(rng, lo: float, hi: float, count: int, fitted: bool, limit: float) -> list[float]:
    """`count` offsets inside (lo, hi) with a 5 % margin and >= 50 mm apart.
    With `fitted`, the strips between consecutive offsets (and the ends) must
    be <= `limit`, by rejection."""

    span = hi - lo
    for _attempt in range(2000):
        offs = sorted(rng.uniform(lo + 0.05 * span, hi - 0.05 * span, size=count).tolist())
        if any(b - a < MIN_SEPARATION_MM for a, b in zip(offs, offs[1:])):
            continue
        if fitted:
            edges = [lo, *offs, hi]
            if any(b - a > limit for a, b in zip(edges, edges[1:])):
                continue
        return offs
    raise RuntimeError("could not draw a seam set")


def make_seams(seed: int, family: str = "random") -> list[Seam]:
    """Panel 1: 1-3 along-boat + 1-4 across-boat full-span seams at random
    offsets; panels 2 and 3: one across seam each.  `family == "fitted"` draws
    the panel-1 offsets so every strip is <= the usable envelope."""

    run = load_run()
    along, across = run["masters"]
    rng = np.random.default_rng(seed)
    fitted = family == "fitted"
    seams: list[Seam] = []
    outer1, holes1, poly1 = run["panels"][1]
    n_along = int(rng.integers(1, 4))      # 1..3
    n_across = int(rng.integers(1, 5))     # 1..4
    if fitted:
        n_along = max(n_along, 2)          # a 2100 mm wide deck needs >= 2 along cuts
    # along-boat seams are offset ACROSS the boat (their normal is `across`)
    lo, hi = _projected_extent(poly1, across)
    for i, off in enumerate(_draw_offsets(rng, lo, hi, n_along, fitted, USABLE_W - 6.0)):
        for _try in range(50):
            seam = full_span_seam(f"r{seed}-p1-along-{i + 1}", poly1, across * off, along, 1, "along")
            if seam is not None:
                break
            off = float(rng.uniform(lo, hi))
        assert seam is not None
        seams.append(seam)
    lo, hi = _projected_extent(poly1, along)
    for i, off in enumerate(_draw_offsets(rng, lo, hi, n_across, fitted, USABLE_L - 6.0)):
        for _try in range(50):
            seam = full_span_seam(f"r{seed}-p1-across-{i + 1}", poly1, along * off, across, 1, "across")
            if seam is not None:
                break
            off = float(rng.uniform(lo, hi))
        assert seam is not None
        seams.append(seam)
    for pid in (2, 3):
        if pid not in run["panels"]:
            continue
        _o, _h, poly = run["panels"][pid]
        lo, hi = _projected_extent(poly, along)
        for _try in range(200):
            off = float(rng.uniform(lo + 0.05 * (hi - lo), hi - 0.05 * (hi - lo)))
            if fitted and not (hi - lo - off + lo <= USABLE_L - 6.0 and off - lo <= USABLE_L - 6.0):
                # both halves must fit along the boat
                if not ((off - lo) <= USABLE_L - 6.0 and (hi - off) <= USABLE_L - 6.0):
                    continue
            seam = full_span_seam(f"r{seed}-p{pid}-across-1", poly, along * off, across, pid, "across")
            if seam is not None:
                break
        assert seam is not None, f"no across seam for panel {pid}"
        seams.append(seam)
    return seams


def build_pieces(seed: int, family: str = "random") -> tuple[list[Seam], list[Piece], list[str]]:
    """Split the fitted panels with the seam set for `seed`, cached on disk."""

    cache = OUT / f"pieces_{family}_{seed:03d}.pkl"
    if cache.is_file():
        with cache.open("rb") as handle:
            return pickle.load(handle)
    run = load_run()
    seams = make_seams(seed, family)
    pieces: list[Piece] = []
    warnings: list[str] = []
    for pid, (outer, holes, _poly) in run["panels"].items():
        mine = [s for s in seams if s.panel_id == pid]
        got, warn = sheets.split_panel(pid, outer, holes, mine, OPTIONS)
        pieces.extend(got)
        warnings.extend(warn)
    with cache.open("wb") as handle:
        pickle.dump((seams, pieces, warnings), handle)
    return seams, pieces, warnings


# ---------------------------------------------------------------- geometry

def placed_polygon(piece: Piece, placement, rotation: np.ndarray, step: float = STEP):
    shell, _s = sheets.sample_loop(piece.outer, step)
    rings = []
    for hole in piece.holes:
        ring, _h = sheets.sample_loop(hole, step)
        if len(ring) >= 3:
            rings.append(placement.apply(ring, rotation))
    polygon = Polygon(placement.apply(shell, rotation), rings)
    if not polygon.is_valid:
        polygon = polygon.buffer(0.0)
    return polygon


def piece_polygon_sheet_frame(piece: Piece, rotation: np.ndarray, step: float = STEP):
    """The piece rotated into sheet axes (no placement), as the nester sees it."""

    shell, holes = nesting._sampled_rings(piece, step)
    return nesting._piece_polygon(shell, holes, rotation)


def layout(sheet_list) -> tuple:
    return tuple(
        (p.sheet_index, p.piece_id, p.rotation_deg,
         float(p.offset[0]), float(p.offset[1]), float(p.origin[0]), float(p.origin[1]))
        for sheet in sheet_list for p in sheet.placements
    )


def verify(pieces: list[Piece], sheet_list, summary, rotation: np.ndarray,
           options: dict = OPTIONS, single_nest_check: bool = True) -> dict:
    """Every invariant a fabricator would want, measured with shapely."""

    usable_w = float(options["max_part_width_mm"])
    usable_l = float(options["max_part_length_mm"])
    margin_x = (float(options["sheet_width_mm"]) - usable_w) / 2.0
    margin_y = (float(options["sheet_length_mm"]) - usable_l) / 2.0
    spacing = float(options["part_spacing_mm"])
    by_id = {p.piece_id: p for p in pieces}
    report: dict = {"violations": [], "worst_outside_mm": -1e9, "min_clearance_mm": 1e9,
                    "max_overlap_mm2": 0.0, "placed": 0, "empty_sheets": 0,
                    "invalid_polygons": 0, "worst_arc_overrun_mm": -1e9}
    placed_ids: list[str] = []
    for sheet in sheet_list:
        if not sheet.placements:
            report["empty_sheets"] += 1
            report["violations"].append(("empty_sheet", sheet.index))
        polys = []
        for pl in sheet.placements:
            if pl.rotation_deg not in (0, 180):
                report["violations"].append(("rotation", pl.piece_id, pl.rotation_deg))
            piece = by_id[pl.piece_id]
            placed_ids.append(pl.piece_id)
            poly = placed_polygon(piece, pl, rotation)
            raw = Polygon(placement_points(piece, pl, rotation))
            if not raw.is_valid:
                report["invalid_polygons"] += 1
            minx, miny, maxx, maxy = poly.bounds
            outside = max(margin_x - minx, maxx - (margin_x + usable_w),
                          margin_y - miny, maxy - (margin_y + usable_l))
            report["worst_outside_mm"] = max(report["worst_outside_mm"], outside)
            if outside > 1e-6:
                report["violations"].append(("outside_envelope", pl.piece_id, sheet.index, outside))
            # true arcs: sample ten times finer and see how far they bow past the chords
            fine = Polygon(placement_points(piece, pl, rotation, step=0.1))
            fminx, fminy, fmaxx, fmaxy = fine.bounds
            overrun = max(margin_x - fminx, fmaxx - (margin_x + usable_w),
                          margin_y - fminy, fmaxy - (margin_y + usable_l))
            report["worst_arc_overrun_mm"] = max(report["worst_arc_overrun_mm"], overrun)
            polys.append((pl.piece_id, poly))
        for i in range(len(polys)):
            for j in range(i + 1, len(polys)):
                a, b = polys[i][1], polys[j][1]
                d = float(a.distance(b))
                report["min_clearance_mm"] = min(report["min_clearance_mm"], d)
                if d < spacing - 1e-6:
                    report["violations"].append(("clearance", polys[i][0], polys[j][0], sheet.index, d))
                if a.intersects(b):
                    inter = float(a.intersection(b).area)
                    report["max_overlap_mm2"] = max(report["max_overlap_mm2"], inter)
                    if inter > 0:
                        report["violations"].append(("overlap", polys[i][0], polys[j][0], sheet.index, inter))
    report["placed"] = len(placed_ids)
    all_ids = set(by_id)
    placed = set(placed_ids)
    unplaced = set(summary["unplaced_piece_ids"])
    if len(placed_ids) != len(placed):
        report["violations"].append(("duplicate_placement", sorted(placed_ids)))
    if placed | unplaced != all_ids or placed & unplaced:
        report["violations"].append(("placed_xor_unplaced", sorted(all_ids - placed - unplaced),
                                     sorted(placed & unplaced)))
    oversize = sheets.oversize_report(pieces, rotation, options)
    oversize_ids = {item["piece_id"] for item in oversize}
    report["oversize_ids"] = sorted(oversize_ids)
    report["unplaced_ids"] = sorted(unplaced)
    if oversize_ids != unplaced:
        report["violations"].append(("oversize_vs_unplaced", sorted(oversize_ids ^ unplaced)))
    if single_nest_check:
        for piece in pieces:
            w, l = sheets.oriented_extent(piece, rotation, STEP)
            fits = w <= usable_w and l <= usable_l
            alone, alone_summary, _w = nesting.nest([piece], rotation, options)
            placed_alone = sum(len(s.placements) for s in alone) == 1
            if fits != placed_alone:
                report["violations"].append(("fits_vs_single_nest", piece.piece_id, w, l, fits, placed_alone))
    return report


def placement_points(piece: Piece, placement, rotation: np.ndarray, step: float = STEP) -> np.ndarray:
    shell, _s = sheets.sample_loop(piece.outer, step)
    return placement.apply(shell, rotation)


def area_lower_bound(pieces: list[Piece], rotation: np.ndarray, options: dict = OPTIONS) -> tuple[int, float]:
    usable = float(options["max_part_width_mm"]) * float(options["max_part_length_mm"])
    total = 0.0
    for piece in pieces:
        total += float(piece_polygon_sheet_frame(piece, rotation).area)
    return int(math.ceil(total / usable - 1e-9)), total


def placeable(pieces: list[Piece], rotation: np.ndarray, options: dict = OPTIONS) -> list[Piece]:
    out = []
    for piece in pieces:
        w, l = sheets.oriented_extent(piece, rotation, STEP)
        if w <= float(options["max_part_width_mm"]) and l <= float(options["max_part_length_mm"]):
            out.append(piece)
    return out


def rect_loop(width: float, length: float, x: float = 0.0, y: float = 0.0) -> sheets.Loop:
    corners = np.array([[x, y], [x + width, y], [x + width, y + length], [x, y + length]], dtype=float)
    return sheets.Loop(np.column_stack([corners, np.zeros(len(corners))]))


def poly_loop(points) -> sheets.Loop:
    pts = np.asarray(points, dtype=float)
    return sheets.Loop(np.column_stack([pts, np.zeros(len(pts))]))


def dump_json(path: Path, payload) -> None:
    def default(value):
        if isinstance(value, (np.floating, np.integer)):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, Path):
            return str(value)
        return str(value)
    path.write_text(json.dumps(payload, indent=1, default=default), encoding="utf-8")


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self.start
