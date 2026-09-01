"""Surface patterns: one global boat frame, lattice in developed space,
clipped per placed panel -- v1's proven behaviour, now with panels in the
right places.  In `nest` mode a moved panel's lattice is computed at its true
boat position and then shifted with the panel, so the pattern stays
continuous in boat terms.

Three patterns (all straight LINE lattices, v1's generators):
  teak     longitudinal lines, `teak_spacing_mm` on centre (63.5 default)
  diamond  diamond stitch: two crossing families sharing grid nodes
           (`diamond_long_diagonal_mm` x `diamond_short_diagonal_mm`)
  hex      honeycomb, `hex_across_flats_mm` across the flats
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from shapely import affinity
from shapely.ops import unary_union

from shapely.geometry import Polygon

from . import v1compat
from .engine import EngineResult
from .layout import PanelPlacement

TeakLines = dict[int, list[tuple[np.ndarray, np.ndarray]]]

PATTERNS = ("teak", "diamond", "hex")
PATTERN_LABELS = {"teak": "Teak lines", "diamond": "Diamond stitch", "hex": "Hexagons"}


def _domain_polygon(points: np.ndarray) -> Polygon | None:
    """Like v1's clean_polygon but keeps the largest piece when a noisy raw
    outline repairs to a MultiPolygon instead of giving up."""

    polygon = v1compat.clean_polygon(points)
    if polygon is not None:
        return polygon
    pts = np.asarray(points, dtype=float)[:, :2]
    if len(pts) < 3:
        return None
    repaired = Polygon(pts).buffer(0.0)
    if repaired.is_empty:
        return None
    if repaired.geom_type == "MultiPolygon":
        repaired = max(repaired.geoms, key=lambda g: g.area)
    return repaired if repaired.geom_type == "Polygon" else None


def _lattice_lines(domain: Polygon, frame: Any, patch_id: int, settings: dict[str, Any]) -> tuple[list[Any], int]:
    """Global teak lattice clipped to one domain.  Unlike v1's `_teak`, the
    line span follows the domain's POSITION along the boat axis (v1 centred
    every line on the frame origin with a reach derived from the domain's
    size, which never reaches a small panel placed away from the origin)."""

    spacing = float(settings["teak_spacing_mm"])
    minimum = float(settings.get("clip_minimum_fragment_mm", 0.5))
    coords = np.asarray(domain.envelope.exterior.coords, dtype=float) - frame.origin
    s = coords @ frame.longitudinal
    t = coords @ frame.transverse
    s0, s1 = float(s.min()) - spacing, float(s.max()) + spacing
    lines = []
    generated = 0
    for index in range(math.floor(t.min() / spacing) - 1, math.ceil(t.max() / spacing) + 2):
        offset = index * spacing
        start = frame.origin + s0 * frame.longitudinal + offset * frame.transverse
        end = frame.origin + s1 * frame.longitudinal + offset * frame.transverse
        generated += 1
        lines.extend(v1compat.clip_line(start, end, domain, patch_id, "TEAK_LONGITUDINAL", minimum))
    return lines, generated


def _diamond_lines(domain: Polygon, frame: Any, patch_id: int, settings: dict[str, Any]) -> tuple[list[Any], int]:
    """Diamond-stitch lattice (v1's exact geometry: two crossing families of
    lines on a shared global grid), but with each line's span following the
    domain's POSITION along the boat axis -- v1's `_diamond` anchors every
    line at the frame origin with a reach taken from the domain's size, so it
    never reaches a small panel placed away from the origin."""

    long_d = float(settings["diamond_long_diagonal_mm"])
    short_d = float(settings["diamond_short_diagonal_mm"])
    minimum = float(settings.get("clip_minimum_fragment_mm", 0.5))
    coords = np.asarray(domain.envelope.exterior.coords, dtype=float) - frame.origin
    s = coords @ frame.longitudinal
    t = coords @ frame.transverse
    s0, s1 = float(s.min()) - long_d, float(s.max()) + long_d
    lines = []
    generated = 0
    for sign in (-1.0, 1.0):
        levels = s / long_d + sign * t / short_d
        for level in range(math.floor(float(levels.min())) - 2, math.ceil(float(levels.max())) + 3):
            # a lattice line satisfies s/L + sign*t/S = level  ->  t(s) = sign*S*(level - s/L)
            start = frame.origin + s0 * frame.longitudinal + (sign * short_d * (level - s0 / long_d)) * frame.transverse
            end = frame.origin + s1 * frame.longitudinal + (sign * short_d * (level - s1 / long_d)) * frame.transverse
            generated += 1
            lines.extend(v1compat.clip_line(start, end, domain, patch_id, "DIAMOND_SHARED_GRID_EDGE", minimum))
    return lines, generated


def _pattern_lines(part: Polygon, frame: Any, pid: int, settings: dict[str, Any], pattern: str) -> tuple[list[Any], int]:
    if pattern == "teak":
        return _lattice_lines(part, frame, pid, settings)
    if pattern == "diamond":
        return _diamond_lines(part, frame, pid, settings)
    if pattern == "hex":
        return v1compat.hex_lines(part, frame, pid, settings)
    raise ValueError(f"pattern must be one of {PATTERNS}, not {pattern!r}")


def pattern_dimensions(pattern: str, settings: dict[str, Any]) -> dict[str, float]:
    if pattern == "teak":
        return {"teak_spacing_mm": float(settings["teak_spacing_mm"])}
    if pattern == "diamond":
        return {"diamond_long_diagonal_mm": float(settings["diamond_long_diagonal_mm"]),
                "diamond_short_diagonal_mm": float(settings["diamond_short_diagonal_mm"])}
    return {"hex_across_flats_mm": float(settings["hex_across_flats_mm"])}


def generate_teak(result: EngineResult, placements: dict[int, PanelPlacement], pattern_settings: dict[str, Any]) -> tuple[TeakLines, dict[str, Any]]:
    return generate_pattern(result, placements, pattern_settings, "teak")


def pattern_for_domains(primary_boat_plan: np.ndarray, nest_offsets: dict[int, Any], pattern_settings: dict[str, Any],
                        pattern: str, domains: dict[int, Any]) -> TeakLines:
    """The pattern lattice clipped to caller-supplied panel domains (in the
    PLACED frame) -- used to put machinable pattern lines into a final DXF,
    clipped to the FITTED/DRAWN loops instead of the raw outline.  The boat
    frame comes from the same primary boat-plan outline generate_pattern
    uses, so phase and direction match the preview exactly."""

    if pattern not in PATTERNS:
        raise ValueError(f"pattern must be one of {PATTERNS}, not {pattern!r}")
    frame = v1compat.detect_boat_frame(np.asarray(primary_boat_plan, dtype=float)[:, :2], pattern_settings)
    out: TeakLines = {}
    for pid, domain in domains.items():
        if domain is None or domain.is_empty:
            continue
        offset = np.asarray(nest_offsets.get(pid, (0.0, 0.0)), dtype=float)[:2]
        moved = affinity.translate(domain, -float(offset[0]), -float(offset[1]))
        parts = list(moved.geoms) if moved.geom_type == "MultiPolygon" else [moved]
        lines: list[Any] = []
        for part in parts:
            if part.is_empty or part.geom_type != "Polygon":
                continue
            piece, _generated = _pattern_lines(part, frame, pid, pattern_settings, pattern)
            lines.extend(piece)
        lines, _dropped = v1compat.deduplicate_lines(lines, float(pattern_settings.get("deduplication_tolerance_mm", 0.001)))
        out[pid] = [(np.asarray(l.start, dtype=float) + offset, np.asarray(l.end, dtype=float) + offset) for l in lines]
    return out


def generate_pattern(result: EngineResult, placements: dict[int, PanelPlacement], pattern_settings: dict[str, Any],
                     pattern: str = "teak") -> tuple[TeakLines, dict[str, Any]]:
    if pattern not in PATTERNS:
        raise ValueError(f"pattern must be one of {PATTERNS}, not {pattern!r}")
    primary_id = result.primary_id
    primary_outer = next((c for c in result.curves_for(primary_id, "OUTER")), None)
    if primary_outer is None:
        raise ValueError("no primary OUTER curve; cannot detect the boat frame")
    primary_placement = placements[primary_id]
    boat_plan_primary = primary_placement.apply(primary_outer.flat_points_mm) - primary_placement.nest_offset
    frame = v1compat.detect_boat_frame(boat_plan_primary, pattern_settings)

    lines: TeakLines = {}
    generated_total = 0
    for pid, placement in placements.items():
        outer = next((c for c in result.curves_for(pid, "OUTER")), None)
        if outer is None:
            continue
        boat_plan = placement.apply(outer.flat_points_mm) - placement.nest_offset
        domain = _domain_polygon(boat_plan)
        if domain is None:
            continue
        holes = []
        for obstacle in result.curves_for(pid, "OBSTACLE"):
            hole = _domain_polygon(placement.apply(obstacle.flat_points_mm) - placement.nest_offset)
            if hole is not None:
                holes.append(hole)
        if holes:
            domain = domain.difference(unary_union(holes))
        parts = list(domain.geoms) if domain.geom_type == "MultiPolygon" else [domain]
        panel_lines = []
        for part in parts:
            if part.is_empty or part.geom_type != "Polygon":
                continue
            lattice, generated = _pattern_lines(part, frame, pid, pattern_settings, pattern)
            generated_total += generated
            panel_lines.extend(lattice)
        panel_lines, _dropped = v1compat.deduplicate_lines(panel_lines, float(pattern_settings.get("deduplication_tolerance_mm", 0.001)))
        offset = placement.nest_offset
        lines[pid] = [(np.asarray(l.start, dtype=float) + offset, np.asarray(l.end, dtype=float) + offset) for l in panel_lines]
    info = {
        "frame": frame.to_dict(),
        "pattern": pattern,
        "pattern_label": PATTERN_LABELS[pattern],
        "spacing_mm": float(pattern_settings["teak_spacing_mm"]),
        "dimensions_mm": pattern_dimensions(pattern, pattern_settings),
        "line_counts": {str(pid): len(v) for pid, v in lines.items()},
        "generated_lattice_lines": generated_total,
    }
    return lines, info
