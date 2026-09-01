from __future__ import annotations

import numpy as np

from .models import Mesh


def face_geometry(mesh: Mesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    triangles = mesh.vertices[mesh.faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    doubled_area = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(doubled_area[:, None], 1e-15)
    centroids = triangles.mean(axis=1)
    return normals, centroids, doubled_area * 0.5


def vertex_normals(mesh: Mesh, face_normals: np.ndarray, face_areas: np.ndarray) -> np.ndarray:
    accumulated = np.zeros_like(mesh.vertices)
    weighted = face_normals * face_areas[:, None]
    for corner in range(3):
        np.add.at(accumulated, mesh.faces[:, corner], weighted)
    lengths = np.linalg.norm(accumulated, axis=1)
    return accumulated / np.maximum(lengths[:, None], 1e-15)


def normalized(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.maximum(np.linalg.norm(vectors, axis=-1, keepdims=True), 1e-15)

