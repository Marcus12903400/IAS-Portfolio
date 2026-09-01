"""Feature -> panel assignment, conservatively.

v1 assigns every obstacle/seam/nonskid curve to the primary deck because only
secondary outer curves carry a candidate id (analysis_pipeline.py:416).  That
is how a footrest's cutouts ended up drawn on the main deck.  Here a curve is
assigned by the chain

    exact mask majority -> 3x3-dilated mask majority
    -> candidate outer polygon containment -> nearest development mesh

and if none of those is decisive the curve is UNASSIGNED with a warning.  The
primary is never a silent default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.ndimage import binary_dilation
from scipy.spatial import cKDTree
from shapely import contains_xy
from shapely.geometry import Polygon


@dataclass
class GridInfo:
    x_min_mm: float
    y_min_mm: float
    resolution_mm: float
    patch_masks: dict[int, np.ndarray]


@dataclass
class PanelGeometry:
    panel_id: int
    outer_polygon: Polygon | None
    mesh_xy_mm: np.ndarray  # (N, 2) world XY of the development mesh vertices


@dataclass
class Assignment:
    panel_id: int | None
    method: str
    fraction: float
    votes: dict[int, float] = field(default_factory=dict)
    ambiguous: bool = False
    note: str = ""

    @property
    def assigned(self) -> bool:
        return self.panel_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "method": self.method,
            "fraction": round(float(self.fraction), 4),
            "votes": {str(k): round(float(v), 4) for k, v in self.votes.items()},
            "ambiguous": self.ambiguous,
            "note": self.note,
        }


def grid_indices(points_xy: np.ndarray, grid: GridInfo, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Row/col per point using v1's convention (development.py:174-175:
    x = x_min + (col + 0.5) * res), plus an in-range mask."""

    xy = np.asarray(points_xy, dtype=float)[:, :2]
    cols = np.floor((xy[:, 0] - grid.x_min_mm) / grid.resolution_mm).astype(int)
    rows = np.floor((xy[:, 1] - grid.y_min_mm) / grid.resolution_mm).astype(int)
    valid = (rows >= 0) & (rows < shape[0]) & (cols >= 0) & (cols < shape[1])
    return rows, cols, valid


def mask_votes(points_xy: np.ndarray, grid: GridInfo, dilate: int = 0) -> dict[int, float]:
    votes: dict[int, float] = {}
    count = len(points_xy)
    if count == 0:
        return votes
    for panel_id, mask in grid.patch_masks.items():
        mask = np.asarray(mask, dtype=bool)
        if dilate:
            mask = binary_dilation(mask, iterations=dilate)
        rows, cols, valid = grid_indices(points_xy, grid, mask.shape)
        hits = np.zeros(count, dtype=bool)
        hits[valid] = mask[rows[valid], cols[valid]]
        votes[panel_id] = float(np.count_nonzero(hits)) / count
    return votes


def _pick(votes: dict[int, float], decisive: float) -> tuple[int | None, float, bool]:
    if not votes:
        return None, 0.0, False
    ordered = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
    best_id, best = ordered[0]
    second = ordered[1][1] if len(ordered) > 1 else 0.0
    tie = best > 0 and (best - second) < 0.05
    if best >= decisive and not tie:
        return best_id, best, False
    return None, best, tie


def assign_curve(
    points_xy: np.ndarray,
    grid: GridInfo,
    panels: dict[int, PanelGeometry],
    config: dict[str, Any],
) -> Assignment:
    settings = config["assignment"]
    decisive = float(settings.get("decisive_fraction", 0.5))
    xy = np.asarray(points_xy, dtype=float)[:, :2]
    if not len(xy):
        return Assignment(None, "empty", 0.0, note="curve has no points")

    votes = mask_votes(xy, grid, 0)
    panel_id, fraction, tie = _pick(votes, decisive)
    if panel_id is not None:
        return Assignment(panel_id, "mask_majority", fraction, votes)

    votes_dilated = mask_votes(xy, grid, 1)
    panel_id, fraction, tie = _pick(votes_dilated, decisive)
    if panel_id is not None:
        return Assignment(panel_id, "mask_majority_dilated", fraction, votes_dilated)

    # Polygon containment: smallest candidate outline that contains a decisive
    # fraction of the points (and its centroid).
    centroid = xy.mean(axis=0)
    containing: list[tuple[float, int, float]] = []
    for pid, geometry in panels.items():
        polygon = geometry.outer_polygon
        if polygon is None or polygon.is_empty:
            continue
        inside = contains_xy(polygon, xy[:, 0], xy[:, 1])
        frac = float(np.count_nonzero(inside)) / len(xy)
        if frac >= decisive and contains_xy(polygon, centroid[0], centroid[1]):
            containing.append((polygon.area, pid, frac))
    if containing:
        containing.sort()
        _area, pid, frac = containing[0]
        return Assignment(pid, "outer_polygon", frac, votes_dilated)

    # Nearest development mesh, but only if the curve is genuinely close to it.
    limit = float(settings.get("nearest_mesh_max_distance_factor", 2.0)) * grid.resolution_mm
    distances: dict[int, float] = {}
    for pid, geometry in panels.items():
        if not len(geometry.mesh_xy_mm):
            continue
        d, _ = cKDTree(geometry.mesh_xy_mm[:, :2]).query(xy, k=1)
        distances[pid] = float(np.median(d))
    if distances:
        ordered = sorted(distances.items(), key=lambda item: item[1])
        pid, best = ordered[0]
        second = ordered[1][1] if len(ordered) > 1 else float("inf")
        if best <= limit and (second - best) > grid.resolution_mm:
            return Assignment(pid, "nearest_mesh", 1.0, votes_dilated,
                              note=f"median distance {best:.2f} mm to panel {pid} mesh")

    top = ", ".join(f"panel {k}: {v:.0%}" for k, v in sorted(votes_dilated.items(), key=lambda i: -i[1])[:3])
    return Assignment(None, "unassigned", fraction, votes_dilated, ambiguous=True,
                      note=f"no decisive panel (dilated mask votes: {top or 'none'})")
