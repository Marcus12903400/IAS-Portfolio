from __future__ import annotations

import numpy as np

from .models import Adjacency, GeometryFields, Mesh
from .normals import face_geometry, normalized, vertex_normals


def _tangent_basis(normal: np.ndarray, triangle: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tangent = triangle[1] - triangle[0]
    tangent -= normal * float(np.dot(tangent, normal))
    if np.linalg.norm(tangent) < 1e-12:
        axis = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.8 else np.array([0.0, 1.0, 0.0])
        tangent = np.cross(normal, axis)
    tangent /= max(np.linalg.norm(tangent), 1e-15)
    bitangent = np.cross(normal, tangent)
    bitangent /= max(np.linalg.norm(bitangent), 1e-15)
    return tangent, bitangent


def estimate_principal_curvatures(
    mesh: Mesh,
    adjacency: Adjacency,
    normals: np.ndarray,
    centroids: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fit a symmetric local normal-gradient tensor in each face tangent plane."""
    count = len(mesh.faces)
    k1 = np.zeros(count); k2 = np.zeros(count)
    direction1 = np.zeros((count, 3)); direction2 = np.zeros((count, 3))
    triangles = mesh.vertices[mesh.faces]
    for face_index in range(count):
        tangent, bitangent = _tangent_basis(normals[face_index], triangles[face_index])
        rows: list[list[float]] = []
        values: list[float] = []
        for neighbor, _ in adjacency.neighbors[face_index]:
            offset = centroids[neighbor] - centroids[face_index]
            x, y = float(np.dot(offset, tangent)), float(np.dot(offset, bitangent))
            if x * x + y * y < 1e-16:
                continue
            normal_delta = normals[neighbor] - normals[face_index]
            dn_x, dn_y = float(np.dot(normal_delta, tangent)), float(np.dot(normal_delta, bitangent))
            rows.extend(([-x, -y, 0.0], [0.0, -x, -y]))
            values.extend((dn_x, dn_y))
        if len(rows) < 4:
            direction1[face_index] = tangent; direction2[face_index] = bitangent
            continue
        matrix = np.asarray(rows)
        target = np.asarray(values)
        try:
            coefficients = np.linalg.lstsq(matrix, target, rcond=1e-6)[0]
        except np.linalg.LinAlgError:
            coefficients = np.zeros(3)
        shape_operator = np.array([[coefficients[0], coefficients[1]], [coefficients[1], coefficients[2]]])
        values_2d, vectors_2d = np.linalg.eigh(shape_operator)
        order = np.argsort(np.abs(values_2d))[::-1]
        values_2d = values_2d[order]; vectors_2d = vectors_2d[:, order]
        k1[face_index], k2[face_index] = values_2d
        direction1[face_index] = normalized((vectors_2d[0, 0] * tangent + vectors_2d[1, 0] * bitangent)[None, :])[0]
        direction2[face_index] = normalized((vectors_2d[0, 1] * tangent + vectors_2d[1, 1] * bitangent)[None, :])[0]
    return k1, k2, direction1, direction2


def multiscale_normal_variation(
    adjacency: Adjacency,
    normals: np.ndarray,
    centroids: np.ndarray,
    scales_mm: list[float],
) -> dict[float, np.ndarray]:
    """Measure normal variation in distinct physical spatial neighborhoods.

    Two half-cell-shifted voxel grids reduce cell-boundary artifacts. Unlike the
    former capped diffusion approximation, a 100 mm request cannot collapse to
    the same computation as 15 or 40 mm on a dense mesh.
    """
    results: dict[float, np.ndarray] = {}
    origin = centroids.min(axis=0) if len(centroids) else np.zeros(3)
    for scale in scales_mm:
        physical_scale = float(scale)
        if physical_scale <= 0.0:
            raise ValueError("Curvature scales must be positive physical distances in millimeters")
        accumulated = np.zeros_like(normals)
        for shift_fraction in (0.0, 0.5):
            shifted = centroids - origin + shift_fraction * physical_scale
            keys = np.floor(shifted / physical_scale).astype(np.int64)
            _unique, inverse = np.unique(keys, axis=0, return_inverse=True)
            sums = np.zeros((int(inverse.max()) + 1, 3), dtype=np.float64)
            counts = np.zeros(len(sums), dtype=np.float64)
            np.add.at(sums, inverse, normals)
            np.add.at(counts, inverse, 1.0)
            cell_normals = normalized(sums / np.maximum(counts[:, None], 1.0))
            accumulated += cell_normals[inverse]
        neighborhood_normals = normalized(accumulated)
        dots = np.clip(np.einsum("ij,ij->i", normals, neighborhood_normals), -1.0, 1.0)
        results[float(scale)] = np.degrees(np.arccos(dots))
    return results


def _soft_range(values: np.ndarray, soft: float, hard: float) -> np.ndarray:
    return np.clip((values - soft) / max(hard - soft, 1e-12), 0.0, 1.0)


def calculate_geometry_fields(mesh: Mesh, adjacency: Adjacency, config: dict) -> GeometryFields:
    face_normals, centroids, areas = face_geometry(mesh)
    v_normals = vertex_normals(mesh, face_normals, areas)
    k1, k2, d1, d2 = estimate_principal_curvatures(mesh, adjacency, face_normals, centroids)
    mean = 0.5 * (k1 + k2)
    gaussian = k1 * k2
    multiscale = multiscale_normal_variation(adjacency, face_normals, centroids, config["curvature"]["scales_mm"])
    compound = np.abs(gaussian)
    curvature_cfg = config["curvature"]
    compound_penalty = _soft_range(
        compound,
        float(curvature_cfg["compound_curvature_soft_per_mm2"]),
        float(curvature_cfg["compound_curvature_hard_per_mm2"]),
    )
    # Single-axis curvature is intentionally not penalized here; structural salience is separate.
    conformability = 1.0 - compound_penalty
    return GeometryFields(
        face_normals, v_normals, centroids, areas, k1, k2, d1, d2,
        mean, gaussian, multiscale, conformability,
    )
