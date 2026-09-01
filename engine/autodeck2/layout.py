"""Place developed panels flat without overlapping.

Every v1 development frame is centred on its own patch (RigidPlanar: origin =
patch centroid, U = world +X projected; Intrinsic: Procrustes-aligned to the
patch's own centred world XY), so all panels land on the origin and overlap.
The boat-plan placement is

    placed = R(uv - mean(uv)) + world_centroid - primary_world_centroid

with R the rigid alignment of the uv frame to world XY (identity unless the
development introduced a rotation above the tolerance).  Collision is decided
by polygon intersection area, never bounding boxes.

Modes
  boat-plan : true relative positions; a positive-area overlap is only
              tolerated when one panel is nested inside another (flagged).
  nest      : nested/colliding panels are moved to a row below the primary
              with `nest_gap_mm` between everything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely.geometry import Polygon


@dataclass
class PanelSource:
    panel_id: int
    role: str
    uv_xy: np.ndarray          # (N, 2) developed mesh vertices
    world_xy: np.ndarray       # (N, 2) matching world vertices (same order)
    outline_uv: np.ndarray     # (M, 2) panel outer boundary in the uv frame
    area_mm2: float


@dataclass
class PanelPlacement:
    panel_id: int
    role: str
    mode: str
    rotation_deg: float
    rotation: np.ndarray       # 2x2, applied as (uv - uv_centroid) @ rotation
    uv_centroid: np.ndarray
    world_centroid: np.ndarray
    translation: np.ndarray    # added after rotation; includes any nest offset
    nest_offset: np.ndarray
    nested_in: int | None = None
    moved_to_row: bool = False
    collides_with: list[int] = field(default_factory=list)
    placed_outline: np.ndarray | None = None

    def apply(self, points_uv: np.ndarray) -> np.ndarray:
        xy = np.asarray(points_uv, dtype=float)[:, :2]
        return (xy - self.uv_centroid) @ self.rotation + self.translation

    def to_dict(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "role": self.role,
            "mode": self.mode,
            "rotation_deg": round(float(self.rotation_deg), 4),
            "rotation_matrix": self.rotation.astype(float).tolist(),
            "uv_centroid_mm": self.uv_centroid.astype(float).tolist(),
            "world_centroid_mm": self.world_centroid.astype(float).tolist(),
            "translation_mm": self.translation.astype(float).tolist(),
            "nest_offset_mm": self.nest_offset.astype(float).tolist(),
            "nested_in": self.nested_in,
            "moved_to_row": self.moved_to_row,
            "collides_with": list(self.collides_with),
            "placed_bbox_mm": (
                None if self.placed_outline is None or not len(self.placed_outline)
                else [self.placed_outline.min(axis=0).tolist(), self.placed_outline.max(axis=0).tolist()]
            ),
        }


def procrustes_rotation(uv_xy: np.ndarray, world_xy: np.ndarray) -> tuple[np.ndarray, float]:
    """Proper rotation R minimising |(uv - c_uv) @ R - (xy - c_xy)|, and its angle."""

    uv = np.asarray(uv_xy, dtype=float)[:, :2]
    xy = np.asarray(world_xy, dtype=float)[:, :2]
    if len(uv) < 3 or len(uv) != len(xy):
        return np.eye(2), 0.0
    a = uv - uv.mean(axis=0)
    b = xy - xy.mean(axis=0)
    left, _s, right = np.linalg.svd(a.T @ b)
    rotation = left @ right
    if np.linalg.det(rotation) < 0:
        left[:, -1] *= -1
        rotation = left @ right
    angle = math.degrees(math.atan2(rotation[0, 1], rotation[0, 0]))
    return rotation, angle


def _polygon(points: np.ndarray) -> Polygon | None:
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3:
        return None
    polygon = Polygon(pts[:, :2])
    if not polygon.is_valid:
        polygon = polygon.buffer(0.0)
    if polygon.is_empty:
        return None
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda g: g.area)
    return polygon


def compute_layout(
    panels: dict[int, PanelSource],
    config: dict[str, Any],
    mode: str | None = None,
) -> tuple[dict[int, PanelPlacement], list[str]]:
    settings = config["layout"]
    mode = mode or str(settings.get("mode", "nest"))
    if mode not in {"nest", "boat-plan"}:
        raise ValueError(f"layout mode must be 'nest' or 'boat-plan', not {mode!r}")
    gap = float(settings.get("nest_gap_mm", 150.0))
    tolerance_deg = float(settings.get("orientation_tolerance_deg", 1.0))
    nested_fraction = float(settings.get("nested_overlap_fraction", 0.9))
    warnings: list[str] = []
    if not panels:
        return {}, warnings

    primary_id = next((pid for pid, p in panels.items() if p.role == "primary"), min(panels))
    primary_world_c = panels[primary_id].world_xy[:, :2].mean(axis=0)

    placements: dict[int, PanelPlacement] = {}
    for pid, source in panels.items():
        uv_c = source.uv_xy[:, :2].mean(axis=0)
        world_c = source.world_xy[:, :2].mean(axis=0)
        rotation, angle = procrustes_rotation(source.uv_xy, source.world_xy)
        if abs(angle) <= tolerance_deg:
            rotation = np.eye(2)
        else:
            warnings.append(
                f"panel {pid}: development frame is rotated {angle:.2f} deg from world XY; rigid alignment applied"
            )
        translation = world_c - primary_world_c
        placement = PanelPlacement(
            panel_id=pid, role=source.role, mode=mode, rotation_deg=float(angle if abs(angle) > tolerance_deg else 0.0),
            rotation=rotation, uv_centroid=uv_c, world_centroid=world_c, translation=translation.copy(),
            nest_offset=np.zeros(2),
        )
        placement.placed_outline = placement.apply(source.outline_uv)
        placements[pid] = placement

    # Collision analysis on true positions.
    order = [primary_id] + sorted((pid for pid in panels if pid != primary_id), key=lambda pid: -panels[pid].area_mm2)
    polygons = {pid: _polygon(placements[pid].placed_outline) for pid in order}
    for i, pid in enumerate(order):
        for other in order[:i]:
            a, b = polygons[pid], polygons[other]
            if a is None or b is None:
                continue
            inter = a.intersection(b).area
            if inter <= 1.0:  # < 1 mm^2 is touching, not overlapping
                continue
            placements[pid].collides_with.append(other)
            if inter / max(a.area, 1e-9) >= nested_fraction:
                placements[pid].nested_in = other

    if mode == "boat-plan":
        for pid in order:
            p = placements[pid]
            if p.collides_with and p.nested_in is None:
                warnings.append(
                    f"panel {pid} overlaps panel(s) {p.collides_with} in boat-plan layout without being nested; "
                    "positions kept -- check the segmentation"
                )
            elif p.nested_in is not None:
                warnings.append(f"panel {pid} is nested inside panel {p.nested_in}; drawn in place (boat-plan mode)")
        return placements, warnings

    # nest mode: move colliding panels to a row below everything that stays.
    fixed = [pid for pid in order if not placements[pid].collides_with]
    moved = [pid for pid in order if placements[pid].collides_with]
    if not moved:
        return placements, warnings
    fixed_outlines = np.vstack([placements[pid].placed_outline for pid in fixed])
    row_top = float(fixed_outlines[:, 1].min()) - gap
    cursor_x = float(fixed_outlines[:, 0].min())
    for pid in moved:
        p = placements[pid]
        outline = p.placed_outline
        width = float(outline[:, 0].max() - outline[:, 0].min())
        height = float(outline[:, 1].max() - outline[:, 1].min())
        target_min = np.array([cursor_x, row_top - height])
        offset = target_min - outline.min(axis=0)
        p.nest_offset = offset
        p.translation = p.translation + offset
        p.placed_outline = p.apply(panels[pid].outline_uv)
        p.moved_to_row = True
        cursor_x += width + gap
        warnings.append(
            f"panel {pid} ({p.role}) overlapped panel(s) {p.collides_with}; moved to the row below the primary "
            f"by {offset.round(1).tolist()} mm (nest mode)"
        )
    return placements, warnings


def overlaps(placements: dict[int, PanelPlacement]) -> list[tuple[int, int, float]]:
    """Pairs of panels whose placed outlines overlap by more than 1 mm^2."""

    ids = sorted(placements)
    polygons = {pid: _polygon(placements[pid].placed_outline) for pid in ids}
    result = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            pa, pb = polygons[a], polygons[b]
            if pa is None or pb is None:
                continue
            area = pa.intersection(pb).area
            if area > 1.0:
                result.append((a, b, float(area)))
    return result
