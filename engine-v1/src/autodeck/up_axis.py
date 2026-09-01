from __future__ import annotations

import numpy as np

from .models import Mesh


AXIS_VECTORS: dict[str, np.ndarray] = {
    "+X": np.asarray([1.0, 0.0, 0.0]),
    "-X": np.asarray([-1.0, 0.0, 0.0]),
    "+Y": np.asarray([0.0, 1.0, 0.0]),
    "-Y": np.asarray([0.0, -1.0, 0.0]),
    "+Z": np.asarray([0.0, 0.0, 1.0]),
    "-Z": np.asarray([0.0, 0.0, -1.0]),
}


def assess_up_axes(mesh: Mesh, maximum_slope_deg: float, sample_face_count: int) -> dict[str, float]:
    count = len(mesh.faces)
    if not count:
        return {name: 0.0 for name in AXIS_VECTORS}
    if count <= sample_face_count:
        indices = np.arange(count, dtype=np.int64)
    else:
        indices = np.linspace(0, count - 1, sample_face_count, dtype=np.int64)
    triangles = mesh.vertices[mesh.faces[indices]]
    area_vectors = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    double_area = np.linalg.norm(area_vectors, axis=1)
    normals = area_vectors / np.maximum(double_area[:, None], 1e-15)
    total = max(float(double_area.sum()), 1e-15)
    minimum_dot = float(np.cos(np.radians(maximum_slope_deg)))
    return {
        name: float(double_area[(normals @ vector) >= minimum_dot].sum() / total)
        for name, vector in AXIS_VECTORS.items()
    }


def resolve_up_axis(requested: str, scores: dict[str, float]) -> tuple[str, np.ndarray]:
    if requested == "auto":
        selected = max(scores, key=scores.get)
    else:
        selected = requested.upper()
        if selected not in AXIS_VECTORS:
            raise ValueError("up axis must be auto, +X, -X, +Y, -Y, +Z, or -Z")
    return selected, AXIS_VECTORS[selected].copy()
