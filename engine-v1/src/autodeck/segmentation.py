from __future__ import annotations

import numpy as np

from .graph import grow_region
from .models import Adjacency, BoundaryField, GeometryFields


def seed_faces_from_points(
    centroids_mm: np.ndarray,
    points_mm: list[np.ndarray],
    radius_mm: float,
) -> tuple[np.ndarray, list[dict[str, object]]]:
    seeds: list[int] = []
    records: list[dict[str, object]] = []
    for point in points_mm:
        distances = np.linalg.norm(centroids_mm - point[None, :], axis=1)
        nearest = int(np.argmin(distances))
        neighborhood = np.flatnonzero(distances <= max(radius_mm, float(distances[nearest]) + 1e-9))
        seeds.extend(neighborhood.tolist())
        records.append({
            "point_mm": point.tolist(),
            "nearest_face": nearest,
            "nearest_distance_mm": float(distances[nearest]),
            "seed_face_count": int(len(neighborhood)),
        })
    return np.asarray(sorted(set(seeds)), dtype=np.int64), records


def segment(
    adjacency: Adjacency,
    fields: GeometryFields,
    boundary: BoundaryField,
    seed_faces: np.ndarray,
    config: dict,
    temporary_overlay_faces: np.ndarray | None = None,
) -> np.ndarray:
    if not len(seed_faces):
        raise ValueError("No valid DECK_SEED faces were established")
    return grow_region(adjacency, fields, boundary, seed_faces, config, temporary_overlay_faces)
