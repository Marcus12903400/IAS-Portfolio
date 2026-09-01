"""Synthetic panels/curves for tests (no scan needed)."""

from __future__ import annotations

import math

import numpy as np

from autodeck2 import v1compat
from autodeck2.calibration import Segment, arc_from_three_points
from autodeck2.engine import EngineResult, MappedCurve, Panel


def grid_mesh(center_xy: tuple[float, float], width: float, height: float, n: int = 6, rotation_deg: float = 0.0):
    """A flat n x n grid patch in world mm plus its uv (centred, optionally
    rotated) -- mimics a v1 DevelopmentResult."""

    xs = np.linspace(-width / 2, width / 2, n)
    ys = np.linspace(-height / 2, height / 2, n)
    gx, gy = np.meshgrid(xs, ys)
    local = np.column_stack([gx.ravel(), gy.ravel()])
    world = local + np.asarray(center_xy, dtype=float)
    theta = math.radians(rotation_deg)
    rot = np.array([[math.cos(theta), math.sin(theta)], [-math.sin(theta), math.cos(theta)]])
    uv = local @ rot
    faces = []
    for r in range(n - 1):
        for c in range(n - 1):
            a = r * n + c; b = a + 1; d = a + n; e = d + 1
            faces.append([a, b, e]); faces.append([a, e, d])
    faces = np.asarray(faces, dtype=np.int64)
    world3 = np.column_stack([world, np.zeros(len(world))])
    mesh = v1compat.DevelopmentMesh(0, world3.copy(), world3.copy(), faces, np.zeros(len(world), int), np.zeros(len(world), int), {}, {})
    return mesh, uv, local


def make_panel(pid: int, role: str, center_xy: tuple[float, float], width: float, height: float, rotation_deg: float = 0.0) -> tuple[Panel, np.ndarray]:
    mesh, uv, local = grid_mesh(center_xy, width, height, rotation_deg=rotation_deg)
    mesh.patch_id = pid
    dev = v1compat.DevelopmentResult(pid, "rigid-planar", mesh, uv, "GOOD", {}, {}, [])
    base = mesh.base_vertices_mm[:, :2]
    panel = Panel(pid, role, width * height, base.mean(axis=0), base.min(axis=0), base.max(axis=0), dev, "rigid-planar", "GOOD", [])
    # outline in uv: rectangle corners (rotated like uv)
    theta = math.radians(rotation_deg)
    rot = np.array([[math.cos(theta), math.sin(theta)], [-math.sin(theta), math.cos(theta)]])
    corners = np.array([[-width / 2, -height / 2], [width / 2, -height / 2], [width / 2, height / 2], [-width / 2, height / 2]])
    dense = []
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        dense.append(np.linspace(a, b, 20, endpoint=False))
    outline_uv = np.vstack(dense) @ rot
    return panel, outline_uv


def make_result(panel_specs: list[tuple[int, str, tuple[float, float], float, float, float]], with_obstacle: bool = True) -> EngineResult:
    panels: dict[int, Panel] = {}
    curves: list[MappedCurve] = []
    for pid, role, center, w, h, rot in panel_specs:
        panel, outline_uv = make_panel(pid, role, center, w, h, rot)
        panels[pid] = panel
        world = np.column_stack([outline_uv + np.asarray(center), np.zeros(len(outline_uv))])
        curves.append(MappedCurve(f"outer-{pid:04d}-deck", "OUTER", "AUTODECK::DECK", f"deck_{pid}", True, 1.0, world, outline_uv, pid, {"method": "outer"}))
        if with_obstacle and pid == panel_specs[0][0]:
            sq = np.array([[-40.0, -40.0], [40.0, -40.0], [40.0, 40.0], [-40.0, 40.0]]) + np.array([w * 0.25, 0.0])
            world_sq = np.column_stack([sq + np.asarray(center), np.zeros(4)])
            curves.append(MappedCurve(f"obstacle-{pid:04d}-console", "OBSTACLE", "AUTODECK::OBSTACLES_PRIMARY", "console", True, 0.7, world_sq, sq, pid, {"method": "mask_majority"}))
    return EngineResult("synthetic.obj", "0" * 64, "mm", 1.0, panels, curves, [], {}, {"assignment_notes": []}, "synthetic", False)


def fillet_rectangle(width: float, height: float, radius: float, origin: tuple[float, float] = (0.0, 0.0)) -> list[Segment]:
    """CCW rectangle with four tangent fillet arcs: 4 lines + 4 arcs."""

    ox, oy = origin
    w, h, r = width, height, radius
    P = lambda x, y: np.array([ox + x, oy + y], dtype=float)  # noqa: E731
    segments: list[Segment] = []
    # bottom edge left->right
    segments.append(Segment("LINE", P(r, 0), P(w - r, 0), source="bottom"))
    segments.append(arc_from_three_points(P(w - r, 0), P(w - r + r * math.sin(math.pi / 4), r - r * math.cos(math.pi / 4)), P(w, r), "arc-br"))
    segments.append(Segment("LINE", P(w, r), P(w, h - r), source="right"))
    segments.append(arc_from_three_points(P(w, h - r), P(w - r + r * math.cos(math.pi / 4), h - r + r * math.sin(math.pi / 4)), P(w - r, h), "arc-tr"))
    segments.append(Segment("LINE", P(w - r, h), P(r, h), source="top"))
    segments.append(arc_from_three_points(P(r, h), P(r - r * math.sin(math.pi / 4), h - r + r * math.cos(math.pi / 4)), P(0, h - r), "arc-tl"))
    segments.append(Segment("LINE", P(0, h - r), P(0, r), source="left"))
    segments.append(arc_from_three_points(P(0, r), P(r - r * math.cos(math.pi / 4), r - r * math.sin(math.pi / 4)), P(r, 0), "arc-bl"))
    return segments
