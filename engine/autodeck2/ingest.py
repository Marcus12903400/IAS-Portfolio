"""Ingest the user's hand-drawn outline from the returned outline.3dm.

USER_CAM::PANEL_n geometry may be drawn in any object order or direction.
Segments are normalised to LINE/ARC, endpoints are snapped within a
tolerance, an endpoint graph is built per panel, loops are walked
independently of creation order (reversing segments as needed), and
branches / duplicates / genuine gaps are rejected with precise messages.
USER_CORNERS markers turn the nearest join into an intentional corner.
Validated loops become final.dxf (closed LWPOLYLINEs with true arc bulges).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import rhino3dm
from shapely import contains_xy
from shapely.geometry import Polygon

from .calibration import (
    Loop,
    Segment,
    arc_from_three_points,
    dump_json,
    loop_statistics,
    self_intersects,
    signed_deviation,
    write_calibration_report,
)


class IngestError(Exception):
    pass


# ---------------------------------------------------------------------------
# reading Rhino geometry

def _p2(point: Any) -> np.ndarray:
    return np.array([float(point.X), float(point.Y)])


def _segments_from_curve(geometry: Any, source: str, tolerance: float) -> tuple[list[Segment], str | None]:
    """Normalise one Rhino curve to LINE/ARC segments; return (segments, rejection)."""

    kind = type(geometry).__name__
    if kind == "LineCurve":
        line = geometry.Line
        return [Segment("LINE", _p2(line.From), _p2(line.To), source=source)], None
    if kind == "ArcCurve":
        return [_segment_from_arc(geometry.Arc, source)], None
    if kind == "PolylineCurve":
        polyline = geometry.ToPolyline()
        pts = [_p2(polyline[i]) for i in range(polyline.Count)]
        return [Segment("LINE", pts[i], pts[i + 1], source=f"{source}[{i}]") for i in range(len(pts) - 1)
                if np.linalg.norm(pts[i + 1] - pts[i]) > 1e-9], None
    if kind == "PolyCurve":
        segments: list[Segment] = []
        for index, piece in enumerate(geometry.Explode()):
            part, rejection = _segments_from_curve(piece, f"{source}[{index}]", tolerance)
            if rejection:
                return [], rejection
            segments.extend(part)
        return segments, None
    if kind == "NurbsCurve":
        if geometry.IsLinear(tolerance):
            return [Segment("LINE", _p2(geometry.PointAtStart), _p2(geometry.PointAtEnd), source=source)], None
        arc = geometry.TryGetArc(tolerance)
        if arc is not None:
            return [_segment_from_arc(arc, source)], None
        polyline = geometry.TryGetPolyline()
        if polyline is not None:
            pts = [_p2(polyline[i]) for i in range(polyline.Count)]
            return [Segment("LINE", pts[i], pts[i + 1], source=f"{source}[{i}]") for i in range(len(pts) - 1)], None
        return [], f"{source}: NURBS curve is neither a line nor a true arc -- redraw it with Line/Arc tools"
    return [], f"{source}: unsupported geometry {kind} -- draw lines and arcs only"


def _segment_from_arc(arc: Any, source: str) -> Segment:
    center = _p2(arc.Center)
    start = _p2(arc.StartPoint)
    end = _p2(arc.EndPoint)
    mid = _p2(arc.MidPoint)
    segment = arc_from_three_points(start, mid, end, source)
    if segment is None:  # degenerate (collinear) -> treat as line
        return Segment("LINE", start, end, source=source)
    # Trust Rhino's centre/radius exactly; only the sweep sign came from the mid point.
    segment.center = center
    segment.radius_mm = float(arc.Radius)
    return segment


def read_user_geometry(path: Path, tolerance: float) -> tuple[dict[int, list[Segment]], np.ndarray, list[str]]:
    model = rhino3dm.File3dm.Read(str(path))
    if model is None:
        raise IngestError(f"could not read {path}")
    names = {index: layer.Name for index, layer in enumerate(model.Layers)}
    per_panel: dict[int, list[Segment]] = {}
    corners: list[np.ndarray] = []
    rejections: list[str] = []
    for obj in model.Objects:
        layer = names.get(int(obj.Attributes.LayerIndex), "")
        source = f"{layer} object {obj.Attributes.Id}"
        geometry = obj.Geometry
        if layer == "USER_CORNERS":
            kind = type(geometry).__name__
            if kind == "Point":
                corners.append(_p2(geometry.Location))
            elif kind == "TextDot":
                corners.append(_p2(geometry.Point))
            else:
                rejections.append(f"{source}: USER_CORNERS accepts points or text dots, not {kind}")
            continue
        if not layer.startswith("USER_CAM::PANEL_"):
            continue
        try:
            pid = int(layer.split("PANEL_")[1])
        except ValueError:
            rejections.append(f"{source}: cannot parse panel id from layer name")
            continue
        segments, rejection = _segments_from_curve(geometry, source, tolerance)
        if rejection:
            rejections.append(rejection)
            continue
        per_panel.setdefault(pid, []).extend(segments)
    return per_panel, (np.vstack(corners) if corners else np.empty((0, 2))), rejections


# ---------------------------------------------------------------------------
# chain construction

class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def snap_endpoints(segments: list[Segment], tolerance: float) -> tuple[list[Segment], list[int], float]:
    """Cluster endpoints within `tolerance`; returns (snapped segments,
    node id per endpoint [2 per segment], max snap displacement)."""

    endpoints = np.array([p for seg in segments for p in (seg.start, seg.end)])
    n = len(endpoints)
    uf = _UnionFind(n)
    for i in range(n):
        d = np.linalg.norm(endpoints[i + 1:] - endpoints[i], axis=1)
        for j in np.flatnonzero(d <= tolerance):
            uf.union(i, int(i + 1 + j))
    roots = [uf.find(i) for i in range(n)]
    ids = {root: index for index, root in enumerate(dict.fromkeys(roots))}
    node_of = [ids[r] for r in roots]
    centroids = np.zeros((len(ids), 2))
    counts = np.zeros(len(ids))
    for i, node in enumerate(node_of):
        centroids[node] += endpoints[i]
        counts[node] += 1
    centroids /= counts[:, None]
    max_shift = 0.0
    snapped: list[Segment] = []
    for k, seg in enumerate(segments):
        s = centroids[node_of[2 * k]]
        e = centroids[node_of[2 * k + 1]]
        max_shift = max(max_shift, float(np.linalg.norm(s - seg.start)), float(np.linalg.norm(e - seg.end)))
        new = Segment(seg.kind, s.copy(), e.copy(), None if seg.center is None else seg.center.copy(), seg.radius_mm, seg.sweep_rad, seg.source)
        if new.kind == "ARC":
            # Keep the centre; re-derive radius and sweep from the snapped ends.
            r0 = np.linalg.norm(s - new.center); r1 = np.linalg.norm(e - new.center)
            new.radius_mm = float(0.5 * (r0 + r1))
            a0 = math.atan2(s[1] - new.center[1], s[0] - new.center[0])
            a1 = math.atan2(e[1] - new.center[1], e[0] - new.center[0])
            sweep = (a1 - a0) % (2 * math.pi)
            if seg.sweep_rad < 0:
                sweep -= 2 * math.pi
            new.sweep_rad = float(sweep)
        snapped.append(new)
    return snapped, node_of, max_shift


def build_loops(segments: list[Segment], snap_tolerance: float, minimum_length: float) -> tuple[list[Loop], list[str]]:
    errors: list[str] = []
    segments = [s for s in segments if s.length_mm >= minimum_length]
    if not segments:
        return [], ["no line/arc geometry found"]
    snapped, node_of, _shift = snap_endpoints(segments, snap_tolerance)
    edges = [(node_of[2 * k], node_of[2 * k + 1]) for k in range(len(snapped))]
    adjacency: dict[int, list[int]] = {}
    for k, (a, b) in enumerate(edges):
        if a == b:
            errors.append(f"{snapped[k].source}: segment starts and ends at the same point (zero-length after snapping)")
            continue
        adjacency.setdefault(a, []).append(k)
        adjacency.setdefault(b, []).append(k)
    seen_pairs: dict[tuple[int, int], int] = {}
    for k, (a, b) in enumerate(edges):
        key = (min(a, b), max(a, b))
        if key in seen_pairs:
            other = snapped[seen_pairs[key]]
            if np.linalg.norm(snapped[k].sample(5.0).mean(axis=0) - other.sample(5.0).mean(axis=0)) < snap_tolerance * 4:
                errors.append(f"duplicate segment: {snapped[k].source} repeats {other.source}")
        else:
            seen_pairs[key] = k
    node_points = {}
    for k, seg in enumerate(snapped):
        node_points[edges[k][0]] = seg.start
        node_points[edges[k][1]] = seg.end
    open_nodes = [node for node, incident in adjacency.items() if len(incident) == 1]
    for node, incident in adjacency.items():
        if len(incident) > 2:
            sources = ", ".join(snapped[k].source for k in incident)
            errors.append(f"branch at {np.round(node_points[node], 2).tolist()} mm: {len(incident)} segments meet ({sources})")
    for node in open_nodes:
        p = node_points[node]
        others = [(float(np.linalg.norm(node_points[o] - p)), o) for o in open_nodes if o != node]
        nearest = min(others) if others else None
        hint = f"; nearest other open end is {nearest[0]:.2f} mm away" if nearest else ""
        errors.append(f"gap: open end at {np.round(p, 2).tolist()} mm ({snapped[adjacency[node][0]].source}){hint}")
    if errors:
        return [], errors

    visited = [False] * len(snapped)
    loops: list[Loop] = []
    for start_edge in range(len(snapped)):
        if visited[start_edge]:
            continue
        chain: list[Segment] = []
        edge = start_edge
        node = edges[edge][0]
        while True:
            visited[edge] = True
            seg = snapped[edge]
            a, b = edges[edge]
            if a == node:
                chain.append(seg); node = b
            else:
                chain.append(seg.reversed()); node = a
            candidates = [k for k in adjacency[node] if not visited[k]]
            if not candidates:
                break
            edge = candidates[0]
        if np.linalg.norm(chain[-1].end - chain[0].start) > 1e-6:
            errors.append(f"chain starting at {chain[0].source} does not close")
            continue
        loops.append(Loop(chain))
    return loops, errors


def order_loops(loops: list[Loop]) -> tuple[Loop | None, list[Loop], list[str]]:
    """Largest loop is the panel outer (CCW); the rest are holes (CW) and must
    lie inside the outer without touching each other."""

    errors: list[str] = []
    if not loops:
        return None, [], ["no closed loop"]
    ordered = sorted(loops, key=lambda l: -abs(l.signed_area()))
    outer = ordered[0] if ordered[0].is_ccw else ordered[0].reverse()
    outer_polygon = outer.polygon()
    holes: list[Loop] = []
    hole_polygons: list[Polygon] = []
    for loop in ordered[1:]:
        hole = loop.reverse() if loop.is_ccw else loop
        polygon = hole.polygon()
        if outer_polygon is None or polygon is None:
            errors.append("degenerate loop")
            continue
        if not outer_polygon.contains(polygon):
            errors.append(f"loop of area {abs(loop.signed_area()) / 1e6:.4f} m2 is not inside the outer loop")
            continue
        if any(polygon.intersects(other) for other in hole_polygons):
            errors.append("two hole loops intersect each other")
            continue
        holes.append(hole)
        hole_polygons.append(polygon)
    return outer, holes, errors


def attach_corners(loop: Loop, corners_xy: np.ndarray, snap_mm: float) -> list[int]:
    if not len(corners_xy):
        loop.intentional_corner_joins = []
        return []
    joins = loop.joins()
    marked: list[int] = []
    for corner in corners_xy:
        distances = [float(np.linalg.norm(np.asarray(j["point_mm"]) - corner)) for j in joins]
        if distances and min(distances) <= snap_mm:
            marked.append(int(np.argmin(distances)))
    loop.intentional_corner_joins = sorted(set(marked))
    return loop.intentional_corner_joins


# ---------------------------------------------------------------------------
# loose analysis: calibration from unsnapped / unfinished drawings

def chain_segments(segments: list[Segment], tolerance: float, minimum_length: float) -> tuple[list[Loop], list[dict[str, Any]], list[str]]:
    """Order-independent chaining that tolerates open ends.  Returns
    (chains, open-end reports, branch errors).  Open chains are Loops with
    closed=False so the same statistics apply to unfinished drawings."""

    segments = [s for s in segments if s.length_mm >= minimum_length]
    if not segments:
        return [], [], []
    snapped, node_of, _shift = snap_endpoints(segments, tolerance)
    edges = [(node_of[2 * k], node_of[2 * k + 1]) for k in range(len(snapped))]
    adjacency: dict[int, list[int]] = {}
    for k, (a, b) in enumerate(edges):
        adjacency.setdefault(a, []).append(k)
        adjacency.setdefault(b, []).append(k)
    errors = [f"branch: {len(v)} segments meet at one point" for v in adjacency.values() if len(v) > 2]
    visited = [False] * len(snapped)
    chains: list[Loop] = []
    # Prefer to start walks at open ends so an open chain is traversed end to end.
    order = sorted(range(len(snapped)), key=lambda k: min(len(adjacency[edges[k][0]]), len(adjacency[edges[k][1]])))
    for start in order:
        if visited[start]:
            continue
        a, b = edges[start]
        node = a if len(adjacency[a]) == 1 else (b if len(adjacency[b]) == 1 else a)
        chain: list[Segment] = []
        edge = start
        while True:
            visited[edge] = True
            seg = snapped[edge]
            ea, eb = edges[edge]
            if ea == node:
                chain.append(seg); node = eb
            else:
                chain.append(seg.reversed()); node = ea
            candidates = [k for k in adjacency[node] if not visited[k]]
            if not candidates:
                break
            edge = candidates[0]
        closed = bool(np.linalg.norm(chain[-1].end - chain[0].start) <= 1e-6)
        chains.append(Loop(chain, closed=closed))
    opens: list[dict[str, Any]] = []
    ends = [(i, c.segments[0].start, c.segments[-1].end) for i, c in enumerate(chains) if not c.closed]
    for i, start, end in ends:
        other_ends = [p for j, s, e in ends if j != i for p in (s, e)]
        nearest = min((float(np.linalg.norm(p - end)) for p in other_ends), default=float("nan"))
        opens.append({"chain": i, "start_mm": start.tolist(), "end_mm": end.tolist(),
                      "closing_gap_mm": float(np.linalg.norm(end - start)),
                      "nearest_other_open_end_mm": nearest})
    return chains, opens, errors


def _reference_for_chain(chain: Loop, reference: dict[str, Any] | None) -> tuple[Polygon | None, str, str]:
    """Nearest RAW boundary (outer or one obstacle) by median distance of the chain samples."""

    if not reference:
        return None, "outer", "none"
    import shapely
    pts = shapely.points(chain.points(2.0))
    best: tuple[float, Polygon, str, str] | None = None
    outer = _polygon(reference.get("outer"))
    if outer is not None:
        d = float(np.median(shapely.distance(outer.exterior, pts)))
        best = (d, outer, "outer", "RAW outer")
    for index, pts_raw in enumerate(reference.get("obstacles", [])):
        polygon = _polygon(pts_raw)
        if polygon is None:
            continue
        d = float(np.median(shapely.distance(polygon.exterior, pts)))
        if best is None or d < best[0]:
            best = (d, polygon, "obstacle", f"RAW obstacle {index + 1}")
    if best is None:
        return None, "outer", "none"
    return best[1], best[2], best[3]


def analyze_drawing(per_panel: dict[int, list[Segment]], corners: np.ndarray, reference: dict[int, dict[str, Any]],
                    config: dict[str, Any]) -> dict[str, Any]:
    """Loose pass: what the drawing teaches, even if it is unsnapped or unfinished."""

    settings = config["ingest"]
    tolerance = float(settings.get("loose_snap_tolerance_mm", 5.0))
    corner_snap = float(settings.get("corner_snap_mm", 10.0))
    tangent_intent = float(settings.get("tangent_intent_max_deg", 1.0))
    unmarked_corner = float(settings.get("unmarked_corner_min_deg", 5.0))
    minimum = float(settings.get("minimum_segment_length_mm", 0.05))
    result: dict[str, Any] = {"loose_snap_tolerance_mm": tolerance, "panels": []}
    for pid, segments in sorted(per_panel.items()):
        chains, opens, errors = chain_segments(segments, tolerance, minimum)
        panel: dict[str, Any] = {"panel_id": pid, "segment_count": len(segments), "chain_count": len(chains),
                                 "open_chain_count": sum(not c.closed for c in chains), "open_ends": opens,
                                 "branch_errors": errors, "chains": []}
        all_lengths: list[float] = []; line_lengths: list[float] = []; arc_lengths: list[float] = []
        radii: list[float] = []; sweeps: list[float] = []
        marked_mismatch: list[float] = []; tangent_mismatch: list[float] = []; near_mismatch: list[float] = []
        unmarked_corner_mismatch: list[float] = []
        deviation_all: list[np.ndarray] = []
        for index, chain in enumerate(chains):
            ref_polygon, side, ref_name = _reference_for_chain(chain, reference.get(pid))
            attach_corners(chain, corners, corner_snap)
            stats = loop_statistics(chain, ref_polygon, side, config)
            joins = stats["joins"]
            marked = set(chain.intentional_corner_joins)
            classes = {"marked_corner": 0, "tangent": 0, "near_tangent": 0, "unmarked_corner": 0}
            for j in joins:
                m = j["tangent_mismatch_deg"]
                if j["index"] in marked:
                    classes["marked_corner"] += 1; marked_mismatch.append(m); j["class"] = "marked_corner"
                elif m <= tangent_intent:
                    classes["tangent"] += 1; tangent_mismatch.append(m); j["class"] = "tangent"
                elif m < unmarked_corner:
                    classes["near_tangent"] += 1; near_mismatch.append(m); j["class"] = "near_tangent"
                else:
                    classes["unmarked_corner"] += 1; unmarked_corner_mismatch.append(m); j["class"] = "unmarked_corner"
            for s in chain.segments:
                all_lengths.append(s.length_mm)
                (line_lengths if s.kind == "LINE" else arc_lengths).append(s.length_mm)
                if s.kind == "ARC":
                    radii.append(s.radius_mm); sweeps.append(abs(math.degrees(s.sweep_rad)))
            if ref_polygon is not None:
                for s in chain.segments:
                    deviation_all.append(signed_deviation(s.sample(1.0), ref_polygon, side))
            panel["chains"].append({
                "index": index, "closed": chain.closed, "reference": ref_name, "side": side,
                "primitive_count": stats["primitive_count"], "line_count": stats["line_count"], "arc_count": stats["arc_count"],
                "total_length_mm": stats["total_length_mm"], "primitives_per_metre": stats["primitives_per_metre"],
                "join_classes": classes, "max_gap_mm": stats["max_gap_mm"], "deviation": stats["deviation"],
                "segments": stats["segments"], "joins": joins,
            })
        dev = np.concatenate(deviation_all) if deviation_all else np.empty(0)
        pct = lambda a, q: float(np.percentile(a, q)) if len(a) else 0.0  # noqa: E731
        panel["training_summary"] = {
            "primitives": len(all_lengths), "lines": len(line_lengths), "arcs": len(arc_lengths),
            "total_drawn_length_m": float(sum(all_lengths)) / 1000.0,
            "primitives_per_metre": (len(all_lengths) / (sum(all_lengths) / 1000.0)) if all_lengths else 0.0,
            "line_length_mm": {"min": min(line_lengths, default=0.0), "median": pct(np.array(line_lengths), 50), "max": max(line_lengths, default=0.0)},
            "arc_length_mm": {"min": min(arc_lengths, default=0.0), "median": pct(np.array(arc_lengths), 50), "max": max(arc_lengths, default=0.0)},
            "arc_radius_mm": {"min": min(radii, default=0.0), "p25": pct(np.array(radii), 25), "median": pct(np.array(radii), 50),
                              "p75": pct(np.array(radii), 75), "max": max(radii, default=0.0)},
            "arc_sweep_deg": {"min": min(sweeps, default=0.0), "median": pct(np.array(sweeps), 50), "max": max(sweeps, default=0.0)},
            "joins": {
                "marked_corner": len(marked_mismatch), "tangent": len(tangent_mismatch),
                "near_tangent": len(near_mismatch), "unmarked_corner": len(unmarked_corner_mismatch),
                "near_tangent_mismatch_deg_median": pct(np.array(near_mismatch), 50),
                "near_tangent_mismatch_deg_max": max(near_mismatch, default=0.0),
                "marked_corner_angle_deg_median": pct(np.array(marked_mismatch), 50),
            },
            "signed_deviation_mm": {
                "mean": float(dev.mean()) if len(dev) else 0.0, "median": pct(dev, 50), "p5": pct(dev, 5), "p95": pct(dev, 95),
                "min": float(dev.min()) if len(dev) else 0.0, "max": float(dev.max()) if len(dev) else 0.0,
                "fraction_beyond_boundary": float((dev < 0).mean()) if len(dev) else 0.0,
                "fraction_beyond_boundary_2mm": float((dev < -2.0).mean()) if len(dev) else 0.0,
                "fraction_inside_5mm": float((dev > 5.0).mean()) if len(dev) else 0.0,
            },
            "corner_markers_total": int(len(corners)),
        }
        result["panels"].append(panel)
    return result


# ---------------------------------------------------------------------------
# reference geometry from the run

def read_reference_geometry(outline_path: Path) -> dict[int, dict[str, Any]]:
    """RAW OUTER and OBSTACLE polylines per panel from the run's outline.3dm."""

    model = rhino3dm.File3dm.Read(str(outline_path))
    if model is None:
        raise IngestError(f"could not read {outline_path}")
    names = {index: layer.Name for index, layer in enumerate(model.Layers)}
    reference: dict[int, dict[str, Any]] = {}
    for obj in model.Objects:
        layer = names.get(int(obj.Attributes.LayerIndex), "")
        if not layer.startswith("RAW::PANEL_"):
            continue
        pid = int(layer.split("PANEL_")[1].split("::")[0])
        kind = layer.split("::")[-1]
        polyline = obj.Geometry.ToPolyline() if hasattr(obj.Geometry, "ToPolyline") else None
        if polyline is None:
            continue
        pts = np.array([[polyline[i].X, polyline[i].Y] for i in range(polyline.Count)])
        entry = reference.setdefault(pid, {"outer": None, "obstacles": []})
        if kind == "OUTER":
            entry["outer"] = pts
        elif kind == "OBSTACLES":
            entry["obstacles"].append(pts)
    return reference


def _polygon(points: np.ndarray | None) -> Polygon | None:
    if points is None or len(points) < 3:
        return None
    polygon = Polygon(np.asarray(points)[:, :2])
    if not polygon.is_valid:
        polygon = polygon.buffer(0.0)
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda g: g.area)
    return None if polygon.is_empty else polygon


# ---------------------------------------------------------------------------
# validation + final.dxf

@dataclass
class PanelIngest:
    panel_id: int
    outer: Loop | None
    holes: list[Loop]
    problems: list[str] = field(default_factory=list)
    loops_report: list[dict[str, Any]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.outer is not None and not self.problems and all(
            item["stats"]["tangent_failure_count"] == 0 and not item.get("problems") for item in self.loops_report
        )


def validate_panel(pid: int, segments: list[Segment], corners_xy: np.ndarray, reference: dict[str, Any] | None,
                   config: dict[str, Any]) -> PanelIngest:
    settings = config["ingest"]
    loops, errors = build_loops(segments, float(settings["snap_tolerance_mm"]), float(settings.get("minimum_segment_length_mm", 0.05)))
    result = PanelIngest(pid, None, [], list(errors))
    if errors:
        return result
    outer, holes, order_errors = order_loops(loops)
    result.problems.extend(order_errors)
    result.outer, result.holes = outer, holes
    if outer is None:
        return result
    raw_outer = _polygon(reference["outer"]) if reference else None
    raw_obstacles = [p for p in (_polygon(o) for o in (reference["obstacles"] if reference else [])) if p is not None]
    corner_snap = float(settings.get("corner_snap_mm", 5.0))
    spacing = float(settings.get("sample_spacing_mm", 0.5))

    def report(loop: Loop, kind: str, ref: Polygon | None, side: str) -> dict[str, Any]:
        attach_corners(loop, corners_xy, corner_snap)
        stats = loop_statistics(loop, ref, side, config)
        problems: list[str] = []
        if self_intersects(loop):
            problems.append(f"{kind} loop self-intersects")
        if kind == "outer":
            pts = loop.points(spacing)
            for index, obstacle in enumerate(raw_obstacles):
                inside = contains_xy(obstacle.buffer(-2.0), pts[:, 0], pts[:, 1]) if not obstacle.buffer(-2.0).is_empty else np.zeros(len(pts), bool)
                if inside.any():
                    problems.append(f"outer loop passes through RAW obstacle {index + 1} ({int(inside.sum())} samples)")
        return {"kind": kind, "stats": stats, "problems": problems}

    result.loops_report.append(report(outer, "outer", raw_outer, "outer"))
    for index, hole in enumerate(holes):
        # Match each drawn hole to the raw obstacle it most overlaps, if any.
        polygon = hole.polygon()
        best = None
        if polygon is not None and raw_obstacles:
            best = max(raw_obstacles, key=lambda o: o.intersection(polygon).area)
            if best.intersection(polygon).area <= 0:
                best = None
        result.loops_report.append(report(hole, f"hole {index + 1}", best, "obstacle"))
    return result


def _loop_polygon(loop: Loop):
    from shapely.geometry import Polygon

    pts = np.vstack([s.sample(2.0) for s in loop.segments if s.length_mm > 1e-9])
    if len(pts) < 4:
        return None
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0.0)
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
    return poly if (not poly.is_empty and poly.geom_type == "Polygon") else None


def machining_pattern(panels: dict[int, "PanelIngest"], stored_pattern: dict[str, Any], primary_boat_plan: np.ndarray,
                      nest_offsets: dict[int, Any], config: dict[str, Any]) -> tuple[tuple[str, dict[int, list[Any]]] | None, dict[str, Any]]:
    """Groove lines for a final DXF: the run's selected pattern clipped to the
    validated CAM loops (outer minus holes), same boat frame and phase as the
    preview.  Returns (payload for write_final_dxf, info).  A pattern problem
    never blocks the DXF -- it comes back as info['error'] instead."""

    from shapely.ops import unary_union

    from .engine import merged_v1_config
    from .teak import pattern_dimensions, pattern_for_domains

    if not stored_pattern.get("enabled"):
        return None, {"enabled": False}
    kind = str(stored_pattern.get("pattern") or "teak")
    try:
        settings = dict(merged_v1_config(config)["pattern"])
        settings.update({k: float(v) for k, v in (stored_pattern.get("dimensions_mm") or {}).items()})
        domains: dict[int, Any] = {}
        for pid, panel in panels.items():
            if panel.outer is None:
                continue
            outer = _loop_polygon(panel.outer)
            if outer is None:
                continue
            holes = [h for h in (_loop_polygon(hole) for hole in panel.holes) if h is not None]
            domain = outer.difference(unary_union(holes)) if holes else outer
            if not domain.is_empty:
                domains[pid] = domain
        lines = pattern_for_domains(primary_boat_plan, nest_offsets, settings, kind, domains)
        total = sum(len(v) for v in lines.values())
        return (kind, lines), {"enabled": True, "kind": kind, "dimensions_mm": pattern_dimensions(kind, settings),
                               "line_count": total, "lines_per_panel": {str(p): len(v) for p, v in lines.items()}}
    except Exception as exc:  # noqa: BLE001
        return None, {"enabled": True, "kind": kind, "error": str(exc)}


PATTERN_DXF_LAYER = {"teak": "PATTERN_TEAK", "diamond": "PATTERN_DIAMOND", "hex": "PATTERN_HEX"}
PATTERN_DXF_COLORS = {"teak": (150, 100, 40), "diamond": (40, 150, 160), "hex": (200, 80, 120)}


def write_final_dxf(path: Path, panels: dict[int, PanelIngest],
                    pattern: tuple[str, dict[int, list[Any]]] | None = None) -> dict[str, Any]:
    """The CAM loops (closed LWPOLYLINEs with bulges) and, when a pattern was
    selected for the run, its machinable groove lines clipped to those loops
    on per-panel `PATTERN_<KIND>__PANEL_n` layers -- select the CAM layers in
    VCarve for the profile cut and the pattern layers for the groove pass."""

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    counts: dict[str, int] = {}
    for pid, panel in panels.items():
        if panel.outer is None:
            continue
        layer = f"CAM_USER__PANEL_{pid}"  # DXF layer names cannot contain ':'
        if layer not in doc.layers:
            doc.layers.add(layer)
        for loop in [panel.outer, *panel.holes]:
            vertices = [(float(s.start[0]), float(s.start[1]), float(s.bulge)) for s in loop.segments]
            msp.add_lwpolyline(vertices, format="xyb", close=True, dxfattribs={"layer": layer})
            counts[layer] = counts.get(layer, 0) + 1
    pattern_kind = None
    if pattern is not None:
        pattern_kind, lines_by_panel = pattern
        prefix = PATTERN_DXF_LAYER.get(pattern_kind, "PATTERN")
        for pid in sorted(lines_by_panel):
            if pid not in panels or panels[pid].outer is None:
                continue
            layer = f"{prefix}__PANEL_{pid}"
            if layer not in doc.layers:
                doc.layers.add(layer).rgb = PATTERN_DXF_COLORS.get(pattern_kind, (150, 100, 40))
            for start, end in lines_by_panel[pid]:
                msp.add_line((float(start[0]), float(start[1])), (float(end[0]), float(end[1])),
                             dxfattribs={"layer": layer})
                counts[layer] = counts.get(layer, 0) + 1
    doc.saveas(path)
    # independent round trip
    reread = ezdxf.readfile(str(path))
    polylines = list(reread.modelspace().query("LWPOLYLINE"))
    pattern_lines = list(reread.modelspace().query("LINE"))
    return {"path": str(path), "units": reread.header.get("$INSUNITS"), "polyline_count": len(polylines),
            "all_closed": all(p.closed for p in polylines), "layer_counts": counts,
            "pattern": pattern_kind, "pattern_line_count": len(pattern_lines)}


@dataclass
class IngestResult:
    status: str
    run_dir: Path
    drawing: Path
    panels: dict[int, PanelIngest]
    rejections: list[str]
    final_dxf: Path | None
    summary: dict[str, Any]


def ingest_run(run_dir: Path, drawing: Path, config: dict[str, Any]) -> IngestResult:
    run_dir = Path(run_dir)
    drawing = Path(drawing)
    outline_path = run_dir / "outline.3dm"
    if not outline_path.is_file():
        raise IngestError(f"{run_dir} has no outline.3dm; ingest needs the run that produced the drawing")
    panels_meta = json.loads((run_dir / "panels.json").read_text(encoding="utf-8"))
    roles = {int(p["panel_id"]): p["role"] for p in panels_meta["panels"]}
    tolerance = float(config["ingest"].get("curve_fit_tolerance_mm", 0.01))
    per_panel, corners, rejections = read_user_geometry(drawing, tolerance)
    reference = read_reference_geometry(outline_path)
    panels: dict[int, PanelIngest] = {}
    for pid, segments in sorted(per_panel.items()):
        if pid not in roles:
            rejections.append(f"USER_CAM::PANEL_{pid}: no such panel in this run")
            continue
        panels[pid] = validate_panel(pid, segments, corners, reference.get(pid), config)
    if not panels and not rejections:
        rejections.append("no geometry found on any USER_CAM::PANEL_n layer")
    valid = bool(panels) and not rejections and all(p.valid for p in panels.values())
    final_path = run_dir / "final.dxf"
    dxf_info: dict[str, Any] = {}
    pattern_info: dict[str, Any] = {"enabled": False}
    if valid:
        pattern_payload = None
        meta_path = run_dir / "run.json"
        stored = (json.loads(meta_path.read_text(encoding="utf-8")).get("teak") or {}) if meta_path.is_file() else {}
        primary_pid = int(panels_meta.get("primary_panel_id", min(roles) if roles else 1))
        offsets = {int(p["panel_id"]): np.asarray((p.get("placement") or {}).get("nest_offset_mm", (0.0, 0.0)), dtype=float)
                   for p in panels_meta["panels"]}
        ref_primary = (reference.get(primary_pid) or {}).get("outer")
        if stored.get("enabled") and ref_primary is not None and len(ref_primary) >= 3:
            boat_plan = np.asarray(ref_primary, dtype=float)[:, :2] - offsets.get(primary_pid, np.zeros(2))
            pattern_payload, pattern_info = machining_pattern(panels, stored, boat_plan, offsets, config)
        dxf_info = write_final_dxf(final_path, panels, pattern=pattern_payload)
    elif final_path.exists():
        final_path.unlink()
    # Loose pass: calibration measurements even when the strict pass rejects
    # (snap off, unfinished chains).  It never produces final.dxf.
    analysis = analyze_drawing(per_panel, corners, reference, config) if per_panel else {"panels": []}
    summary = {
        "run_id": run_dir.name, "drawing": str(drawing), "status": "VALID" if valid else "REJECTED",
        "rejections": rejections, "final_dxf": dxf_info, "machining_pattern": pattern_info, "analysis": analysis,
        "panels": [
            {"panel_id": pid, "role": roles.get(pid, ""), "problems": panel.problems,
             "loops": panel.loops_report, "valid": panel.valid}
            for pid, panel in panels.items()
        ],
        "panels_without_drawing": [pid for pid in roles if pid not in panels],
    }
    dump_json(run_dir / "calibration.json", summary)
    write_calibration_report(run_dir / "calibration_report.md", summary)
    _write_final_report(run_dir / "final_report.md", summary)
    return IngestResult("VALID" if valid else "REJECTED", run_dir, drawing, panels, rejections,
                        final_path if valid else None, summary)


def _write_final_report(path: Path, summary: dict[str, Any]) -> None:
    lines = ["# AutoDeck2 final report", "", f"- Status: **{summary['status']}**", f"- Drawing: `{summary['drawing']}`"]
    if summary["status"] == "VALID":
        info = summary["final_dxf"]
        lines.append(f"- final.dxf written: {info['polyline_count']} closed polylines, units code {info['units']} (4 = mm)")
        mp = summary.get("machining_pattern") or {}
        if mp.get("line_count"):
            dims = ", ".join(f"{k.replace('_mm', '')} {v:g} mm" for k, v in (mp.get("dimensions_mm") or {}).items())
            lines.append(f"- pattern for machining: {mp['kind']} ({dims}) -- {mp['line_count']} groove lines on "
                         "`PATTERN_*__PANEL_n` layers, clipped to your CAM loops. In VCarve: CAM layers = profile cut, "
                         "pattern layers = groove pass.")
        elif mp.get("error"):
            lines.append(f"- pattern NOT added to final.dxf: {mp['error']}")
        lines.append("- Manufacturing approval: TEST_ONLY -- verify a test cut before production.")
        for panel in summary["panels"]:
            for loop in panel["loops"]:
                dev = loop["stats"].get("deviation") or {}
                pen = (dev.get("forbidden_side_penetration") or {}).get("max_mm", 0.0)
                if pen > 3.0:
                    lines.append(f"- WARNING panel {panel['panel_id']} {loop['kind']}: your drawing goes up to {pen:.1f} mm "
                                 "beyond the detected wall/obstacle boundary (see calibration_report.md). The geometry is "
                                 "valid; whether that is intended is your call.")
    else:
        lines.append("- final.dxf NOT written. Fix the items below in Rhino and run ingest again:")
        for r in summary["rejections"]:
            lines.append(f"  - {r}")
        for panel in summary["panels"]:
            for p in panel["problems"]:
                lines.append(f"  - panel {panel['panel_id']}: {p}")
            for loop in panel["loops"]:
                for p in loop.get("problems", []):
                    lines.append(f"  - panel {panel['panel_id']} {loop['kind']}: {p}")
                for f in loop["stats"].get("tangent_failures", []):
                    lines.append(f"  - panel {panel['panel_id']} {loop['kind']}: tangent break {f['tangent_mismatch_deg']:.3f} deg at {np.round(f['point_mm'], 1).tolist()} (mark it on USER_CORNERS if intentional)")
    if summary.get("panels_without_drawing"):
        lines.append(f"- Panels with no USER_CAM drawing: {summary['panels_without_drawing']}")
    for panel in (summary.get("analysis") or {}).get("panels", []):
        t = panel["training_summary"]
        lines.append("")
        lines.append(f"## What the drawing on panel {panel['panel_id']} teaches (loose pass, snap {summary['analysis']['loose_snap_tolerance_mm']} mm)")
        lines.append(f"- {t['primitives']} primitives ({t['lines']} lines, {t['arcs']} arcs) over {t['total_drawn_length_m']:.2f} m "
                     f"= {t['primitives_per_metre']:.1f} per metre; {panel['chain_count']} chains, {panel['open_chain_count']} still open")
        lines.append(f"- line length median {t['line_length_mm']['median']:.0f} mm (max {t['line_length_mm']['max']:.0f}); "
                     f"arc radius median {t['arc_radius_mm']['median']:.0f} mm (min {t['arc_radius_mm']['min']:.0f}, max {t['arc_radius_mm']['max']:.0f}); "
                     f"arc length max {t['arc_length_mm']['max']:.0f} mm")
        j = t["joins"]
        lines.append(f"- joins: {j['marked_corner']} marked corners, {j['tangent']} tangent, {j['near_tangent']} near-tangent "
                     f"(median {j['near_tangent_mismatch_deg_median']:.1f} deg -- tangent intent, snap/tangent tools would fix), "
                     f"{j['unmarked_corner']} unmarked corners (>= {5.0} deg, no USER_CORNERS point)")
        d = t["signed_deviation_mm"]
        lines.append(f"- deviation from RAW: median {d['median']:+.1f} mm, p5 {d['p5']:+.1f}, p95 {d['p95']:+.1f}, extremes {d['min']:+.1f}/{d['max']:+.1f}; "
                     f"{d['fraction_beyond_boundary'] * 100:.0f}% of drawn length beyond the boundary, {d['fraction_beyond_boundary_2mm'] * 100:.0f}% beyond by >2 mm")
        for o in panel["open_ends"]:
            lines.append(f"- open chain {o['chain']}: ends {np.round(o['end_mm'], 1).tolist()} / {np.round(o['start_mm'], 1).tolist()}, "
                         f"closing gap {o['closing_gap_mm']:.1f} mm, nearest other open end {o['nearest_other_open_end_mm']:.1f} mm")
    lines.append("")
    lines.append("See calibration_report.md for the measurements.")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
