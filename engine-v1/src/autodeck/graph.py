from __future__ import annotations

from collections import deque

import numpy as np

from .models import Adjacency, BoundaryField, GeometryFields


def grow_region(
    adjacency: Adjacency,
    fields: GeometryFields,
    boundary: BoundaryField,
    seed_faces: np.ndarray,
    config: dict,
    temporary_overlay_faces: np.ndarray | None = None,
) -> np.ndarray:
    selected = np.zeros(len(fields.face_normals), dtype=bool)
    queued = np.zeros_like(selected)
    queue: deque[int] = deque()
    for seed in np.unique(seed_faces):
        if 0 <= seed < len(selected):
            queue.append(int(seed)); queued[int(seed)] = True
    maximum_cost = float(config["segmentation"]["max_boundary_cost"])
    minimum_conformability = float(config["segmentation"]["minimum_conformability"])
    temporary_overlay_faces = temporary_overlay_faces if temporary_overlay_faces is not None else np.zeros_like(selected)
    while queue:
        face = queue.popleft()
        if fields.conformability[face] < minimum_conformability and not temporary_overlay_faces[face]:
            continue
        selected[face] = True
        for neighbor, edge_index in adjacency.neighbors[face]:
            if queued[neighbor] or boundary.refined_cost[edge_index] > maximum_cost:
                continue
            queued[neighbor] = True
            queue.append(neighbor)
    return selected
