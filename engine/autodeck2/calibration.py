"""Line/arc primitives, loops, and the signed calibration measurements taken
from a hand-drawn USER_CAM outline against the RAW detected geometry.

Sign convention (established before measuring): the RAW outer loop is
oriented CCW so the deck interior is on the left of travel.  A drawn point's
signed deviation is POSITIVE when it lies inside the raw outline (into usable
deck area) and NEGATIVE when it lies beyond the detected wall boundary.  For
obstacle holes the sign is mirrored: positive = outside the obstacle (usable
deck), negative = penetrating the obstacle.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from shapely import contains_xy
from shapely.geometry import LineString, Polygon


@dataclass
class Segment:
    kind: str                       # "LINE" | "ARC"
    start: np.ndarray               # (2,)
    end: np.ndarray                 # (2,)
    center: np.ndarray | None = None
    radius_mm: float = 0.0
    sweep_rad: float = 0.0          # signed, CCW positive
    source: str = ""                # Rhino object id / name

    @property
    def length_mm(self) -> float:
        if self.kind == "LINE":
            return float(np.linalg.norm(self.end - self.start))
        return float(abs(self.sweep_rad) * self.radius_mm)

    def _radial_tangent(self, point: np.ndarray) -> np.ndarray:
        radial = point - self.center
        radial = radial / max(np.linalg.norm(radial), 1e-12)
        left = np.array([-radial[1], radial[0]])
        return left if self.sweep_rad >= 0 else -left

    def tangent_start(self) -> np.ndarray:
        if self.kind == "LINE":
            d = self.end - self.start
            return d / max(np.linalg.norm(d), 1e-12)
        return self._radial_tangent(self.start)

    def tangent_end(self) -> np.ndarray:
        if self.kind == "LINE":
            return self.tangent_start()
        return self._radial_tangent(self.end)

    def reversed(self) -> "Segment":
        return Segment(self.kind, self.end.copy(), self.start.copy(),
                       None if self.center is None else self.center.copy(), self.radius_mm, -self.sweep_rad, self.source)

    @property
    def bulge(self) -> float:
        return 0.0 if self.kind == "LINE" else float(math.tan(self.sweep_rad / 4.0))

    def sample(self, spacing_mm: float = 0.5) -> np.ndarray:
        count = max(2, int(math.ceil(self.length_mm / max(spacing_mm, 0.01))) + 1)
        if self.kind == "LINE":
            return np.linspace(self.start, self.end, count)
        a0 = math.atan2(self.start[1] - self.center[1], self.start[0] - self.center[0])
        angles = a0 + np.linspace(0.0, self.sweep_rad, count)
        pts = self.center + self.radius_mm * np.column_stack([np.cos(angles), np.sin(angles)])
        pts[0] = self.start
        pts[-1] = self.end
        return pts

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "start_mm": self.start.tolist(), "end_mm": self.end.tolist(),
            "center_mm": None if self.center is None else self.center.tolist(),
            "radius_mm": float(self.radius_mm), "sweep_deg": float(math.degrees(self.sweep_rad)),
            "length_mm": self.length_mm, "bulge": self.bulge, "source": self.source,
        }


def arc_from_three_points(start: np.ndarray, mid: np.ndarray, end: np.ndarray, source: str = "") -> Segment | None:
    ax, ay = start; bx, by = mid; cx, cy = end
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    center = np.array([ux, uy])
    radius = float(np.linalg.norm(start - center))
    a0 = math.atan2(start[1] - uy, start[0] - ux)
    a1 = math.atan2(end[1] - uy, end[0] - ux)
    am = math.atan2(mid[1] - uy, mid[0] - ux)
    ccw = (am - a0) % (2 * math.pi) < (a1 - a0) % (2 * math.pi)
    sweep = (a1 - a0) % (2 * math.pi)
    if not ccw:
        sweep = sweep - 2 * math.pi
    return Segment("ARC", np.asarray(start, float), np.asarray(end, float), center, radius, float(sweep), source)


@dataclass
class Loop:
    segments: list[Segment]
    intentional_corner_joins: list[int] = field(default_factory=list)
    closed: bool = True   # an open chain (unfinished drawing) has one join fewer

    def points(self, spacing_mm: float = 0.5) -> np.ndarray:
        chunks = [seg.sample(spacing_mm)[:-1] for seg in self.segments]
        if chunks and not self.closed:
            chunks.append(self.segments[-1].end[None, :])
        return np.vstack(chunks) if chunks else np.empty((0, 2))

    def signed_area(self) -> float:
        pts = self.points(1.0)
        if len(pts) < 3:
            return 0.0
        x, y = pts[:, 0], pts[:, 1]
        return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))

    @property
    def is_ccw(self) -> bool:
        return self.signed_area() > 0

    def reverse(self) -> "Loop":
        return Loop([seg.reversed() for seg in reversed(self.segments)])

    def polygon(self) -> Polygon | None:
        pts = self.points(1.0)
        if len(pts) < 3:
            return None
        polygon = Polygon(pts)
        return polygon if polygon.is_valid else polygon.buffer(0.0)

    def joins(self) -> list[dict[str, Any]]:
        result = []
        n = len(self.segments)
        for i in range(n if self.closed else max(n - 1, 0)):
            a = self.segments[i]
            b = self.segments[(i + 1) % n]
            gap = float(np.linalg.norm(a.end - b.start))
            result.append({"index": i, "point_mm": a.end.tolist(), "gap_mm": gap,
                           "tangent_mismatch_deg": tangent_mismatch_deg(a, b),
                           "before": a.kind, "after": b.kind})
        return result


def tangent_mismatch_deg(a: Segment, b: Segment) -> float:
    c = float(np.clip(np.dot(a.tangent_end(), b.tangent_start()), -1.0, 1.0))
    return math.degrees(math.acos(c))


def signed_deviation(points_xy: np.ndarray, reference: Polygon, side: str) -> np.ndarray:
    """Positive = usable deck side, negative = forbidden side (see module doc)."""

    pts = np.asarray(points_xy, dtype=float)[:, :2]
    if not len(pts):
        return np.empty(0)
    boundary = reference.exterior
    import shapely
    distance = shapely.distance(boundary, shapely.points(pts))
    inside = contains_xy(reference, pts[:, 0], pts[:, 1])
    sign = np.where(inside, 1.0, -1.0)
    if side == "obstacle":
        sign = -sign
    return sign * distance


def _stats(values: np.ndarray) -> dict[str, float]:
    if not len(values):
        return {"max_mm": 0.0, "p95_mm": 0.0, "mean_mm": 0.0, "count": 0}
    return {"max_mm": float(np.max(values)), "p95_mm": float(np.percentile(values, 95)),
            "mean_mm": float(np.mean(values)), "count": int(len(values))}


def loop_statistics(loop: Loop, reference: Polygon | None, side: str, config: dict[str, Any]) -> dict[str, Any]:
    spacing = float(config["ingest"].get("sample_spacing_mm", 0.5))
    target = float(config["ingest"].get("tangent_target_deg", 0.05))
    maximum = float(config["ingest"].get("tangent_max_deg", 0.10))
    joins = loop.joins()
    corner_set = set(loop.intentional_corner_joins)
    tangent_failures = [j for j in joins if j["index"] not in corner_set and j["tangent_mismatch_deg"] > maximum + 1e-9]
    tangent_above_target = [j for j in joins if j["index"] not in corner_set and target < j["tangent_mismatch_deg"] <= maximum]
    lengths = np.array([s.length_mm for s in loop.segments])
    radii = np.array([s.radius_mm for s in loop.segments if s.kind == "ARC"])
    total_length = float(lengths.sum()) if len(lengths) else 0.0
    per_primitive: list[dict[str, Any]] = []
    deviation_summary: dict[str, Any] = {}
    if reference is not None:
        all_dev = []
        for index, seg in enumerate(loop.segments):
            dev = signed_deviation(seg.sample(spacing), reference, side)
            all_dev.append(dev)
            per_primitive.append({
                "index": index, "kind": seg.kind, "length_mm": seg.length_mm, "radius_mm": seg.radius_mm,
                "deviation_abs_max_mm": float(np.max(np.abs(dev))) if len(dev) else 0.0,
                "forbidden_side_max_mm": float(-np.min(dev)) if len(dev) and np.min(dev) < 0 else 0.0,
                "deck_side_max_mm": float(np.max(dev)) if len(dev) and np.max(dev) > 0 else 0.0,
            })
        dev = np.concatenate(all_dev) if all_dev else np.empty(0)
        forbidden = -dev[dev < 0]
        deck = dev[dev > 0]
        deviation_summary = {
            "absolute": _stats(np.abs(dev)),
            "forbidden_side_penetration": _stats(forbidden),
            "deck_side_shortfall": _stats(deck),
            "signed_min_mm": float(np.min(dev)) if len(dev) else 0.0,
            "signed_max_mm": float(np.max(dev)) if len(dev) else 0.0,
        }
    return {
        "primitive_count": len(loop.segments),
        "line_count": sum(s.kind == "LINE" for s in loop.segments),
        "arc_count": sum(s.kind == "ARC" for s in loop.segments),
        "total_length_mm": total_length,
        "primitives_per_metre": (len(loop.segments) / (total_length / 1000.0)) if total_length > 0 else 0.0,
        "length_mm": _stats(lengths) | {"min_mm": float(np.min(lengths)) if len(lengths) else 0.0},
        "arc_radius_mm": _stats(radii) | {"min_mm": float(np.min(radii)) if len(radii) else 0.0},
        "joins": joins,
        "join_count": len(joins),
        "intentional_corner_count": len(corner_set),
        "tangent_failure_count": len(tangent_failures),
        "tangent_failures": tangent_failures,
        "tangent_above_target_count": len(tangent_above_target),
        "max_tangent_mismatch_non_corner_deg": max((j["tangent_mismatch_deg"] for j in joins if j["index"] not in corner_set), default=0.0),
        "max_gap_mm": max((j["gap_mm"] for j in joins), default=0.0),
        "is_ccw": loop.is_ccw,
        "deviation": deviation_summary,
        "primitives": per_primitive,
        "segments": [s.to_dict() for s in loop.segments],
    }


def self_intersects(loop: Loop, spacing_mm: float = 1.0) -> bool:
    pts = loop.points(spacing_mm)
    if len(pts) < 4:
        return False
    return not LineString(np.vstack([pts, pts[:1]])).is_simple


def write_calibration_report(path: Path, summary: dict[str, Any]) -> None:
    lines = ["# AutoDeck2 calibration report", ""]
    lines.append(f"- Run: `{summary.get('run_id', '')}`")
    lines.append(f"- Drawing: `{summary.get('drawing', '')}`")
    lines.append(f"- Status: **{summary.get('status', '')}**")
    lines.append("")
    lines.append("Sign convention: positive deviation = into usable deck area; negative = beyond the detected wall (or into an obstacle).")
    lines.append("")
    for panel in summary.get("panels", []):
        pid = panel["panel_id"]
        lines.append(f"## Panel {pid} ({panel.get('role', '')})")
        lines.append("")
        for loop in panel.get("loops", []):
            s = loop["stats"]
            lines.append(f"### {loop['kind']} loop")
            lines.append("")
            lines.append(f"- Primitives: {s['primitive_count']} ({s['line_count']} lines, {s['arc_count']} arcs); "
                         f"{s['primitives_per_metre']:.1f} per metre over {s['total_length_mm'] / 1000:.2f} m")
            lines.append(f"- Shortest primitive: {s['length_mm']['min_mm']:.1f} mm; median-ish mean {s['length_mm']['mean_mm']:.1f} mm; longest {s['length_mm']['max_mm']:.1f} mm")
            if s["arc_count"]:
                lines.append(f"- Arc radius: min {s['arc_radius_mm']['min_mm']:.1f} mm, max {s['arc_radius_mm']['max_mm']:.1f} mm")
            lines.append(f"- Joins: {s['join_count']} ({s['intentional_corner_count']} intentional corners); "
                         f"tangent failures: {s['tangent_failure_count']}; max non-corner mismatch {s['max_tangent_mismatch_non_corner_deg']:.4f} deg; max gap {s['max_gap_mm']:.4f} mm")
            dev = s.get("deviation") or {}
            if dev:
                lines.append(f"- Deviation from RAW: |max| {dev['absolute']['max_mm']:.2f} mm, |p95| {dev['absolute']['p95_mm']:.2f} mm; "
                             f"forbidden-side penetration max {dev['forbidden_side_penetration']['max_mm']:.2f} / p95 {dev['forbidden_side_penetration']['p95_mm']:.2f} mm; "
                             f"deck-side shortfall max {dev['deck_side_shortfall']['max_mm']:.2f} / p95 {dev['deck_side_shortfall']['p95_mm']:.2f} mm")
            for failure in s.get("tangent_failures", [])[:10]:
                lines.append(f"  - TANGENT FAILURE at join {failure['index']} ({failure['before']}->{failure['after']}) "
                             f"point {np.round(failure['point_mm'], 1).tolist()}: {failure['tangent_mismatch_deg']:.3f} deg")
            for problem in loop.get("problems", []):
                lines.append(f"  - PROBLEM: {problem}")
            lines.append("")
        for problem in panel.get("problems", []):
            lines.append(f"- PANEL PROBLEM: {problem}")
        lines.append("")
    analysis = summary.get("analysis") or {}
    for panel in analysis.get("panels", []):
        lines.append(f"## Drawing analysis, panel {panel['panel_id']} (loose pass, snap {analysis.get('loose_snap_tolerance_mm')} mm)")
        lines.append("")
        lines.append(f"{panel['segment_count']} segments -> {panel['chain_count']} chains ({panel['open_chain_count']} open).")
        lines.append("")
        lines.append("| chain | closed | reference | prims (L/A) | length m | per m | marked corners | tangent | near-tangent | unmarked corners | dev median | dev p5 | dev p95 |")
        lines.append("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for c in panel["chains"]:
            jc = c["join_classes"]; dev = c.get("deviation") or {}
            lines.append(f"| {c['index']} | {'yes' if c['closed'] else 'NO'} | {c['reference']} | {c['primitive_count']} ({c['line_count']}/{c['arc_count']}) | "
                         f"{c['total_length_mm'] / 1000:.2f} | {c['primitives_per_metre']:.1f} | {jc['marked_corner']} | {jc['tangent']} | {jc['near_tangent']} | {jc['unmarked_corner']} | "
                         f"{'' if not dev else f'{dev['signed_min_mm']:+.1f}..{dev['signed_max_mm']:+.1f}'} | | |")
        t = panel["training_summary"]
        lines.append("")
        lines.append(f"Training summary: {t['primitives']} primitives ({t['lines']} L / {t['arcs']} A), {t['primitives_per_metre']:.1f} per metre; "
                     f"line median {t['line_length_mm']['median']:.0f} mm, max {t['line_length_mm']['max']:.0f} mm; "
                     f"arc radius min/median/max {t['arc_radius_mm']['min']:.0f}/{t['arc_radius_mm']['median']:.0f}/{t['arc_radius_mm']['max']:.0f} mm; "
                     f"arc sweep median {t['arc_sweep_deg']['median']:.0f} deg; signed deviation median {t['signed_deviation_mm']['median']:+.2f} mm "
                     f"(p5 {t['signed_deviation_mm']['p5']:+.1f}, p95 {t['signed_deviation_mm']['p95']:+.1f}, min {t['signed_deviation_mm']['min']:+.1f}).")
        lines.append("")
    if summary.get("rejections"):
        lines.append("## Rejected objects")
        lines.append("")
        lines.extend(f"- {r}" for r in summary["rejections"])
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, default=lambda v: v.tolist() if hasattr(v, "tolist") else str(v)), encoding="utf-8")
