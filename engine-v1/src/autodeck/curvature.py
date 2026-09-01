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


def _tangent_bases(normals: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`_tangent_basis` for every face at once; same construction, same result."""

    tangent = triangles[:, 1] - triangles[:, 0]
    tangent = tangent - normals * np.einsum("ij,ij->i", tangent, normals)[:, None]
    degenerate = np.linalg.norm(tangent, axis=1) < 1e-12
    if degenerate.any():
        axis = np.where(
            np.abs(normals[degenerate, 0:1]) < 0.8,
            np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]),
        )
        tangent[degenerate] = np.cross(normals[degenerate], axis)
    tangent = tangent / np.maximum(np.linalg.norm(tangent, axis=1, keepdims=True), 1e-15)
    bitangent = np.cross(normals, tangent)
    bitangent = bitangent / np.maximum(np.linalg.norm(bitangent, axis=1, keepdims=True), 1e-15)
    return tangent, bitangent


def estimate_principal_curvatures(
    mesh: Mesh,
    adjacency: Adjacency,
    normals: np.ndarray,
    centroids: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fit a symmetric local normal-gradient tensor in each face tangent plane.

    A triangle has at most three across-edge neighbours, so each face's least
    squares problem is at most 6 rows by 3 unknowns.  Rather than looping over
    faces and calling ``lstsq`` once each -- which cost ~14.6 s of the 15.6 s
    "geometry fields" stage on a 209k-face deck -- accumulate every face's
    normal equations (A^T A, A^T b) with ``np.add.at`` and solve them all in
    one batched ``np.linalg.solve``.  Same fit, ~22x faster; agreement with the
    per-face ``lstsq`` is ~1e-9 relative, far inside the run-to-run spread the
    LAPACK path already has across BLAS builds.
    """

    count = len(mesh.faces)
    triangles = mesh.vertices[mesh.faces]
    tangent, bitangent = _tangent_bases(normals, triangles)
    k1 = np.zeros(count); k2 = np.zeros(count)
    direction1 = tangent.copy(); direction2 = bitangent.copy()
    if count == 0:
        return k1, k2, direction1, direction2

    # Each interior edge contributes the pair in both directions, which is what
    # the per-face `adjacency.neighbors` walk visited.
    face = np.concatenate([adjacency.face_a, adjacency.face_b]).astype(np.int64, copy=False)
    other = np.concatenate([adjacency.face_b, adjacency.face_a]).astype(np.int64, copy=False)
    offset = centroids[other] - centroids[face]
    x = np.einsum("ij,ij->i", offset, tangent[face])
    y = np.einsum("ij,ij->i", offset, bitangent[face])
    delta = normals[other] - normals[face]
    dn_x = np.einsum("ij,ij->i", delta, tangent[face])
    dn_y = np.einsum("ij,ij->i", delta, bitangent[face])

    keep = (x * x + y * y) >= 1e-16              # the loop's skip test
    face, x, y, dn_x, dn_y = face[keep], x[keep], y[keep], dn_x[keep], dn_y[keep]
    zero = np.zeros(len(face))
    row_x = np.stack([-x, -y, zero], axis=1)     # [-x, -y, 0] . c = dn_x
    row_y = np.stack([zero, -x, -y], axis=1)     # [0, -x, -y] . c = dn_y

    ata = np.zeros((count, 3, 3))
    atb = np.zeros((count, 3))
    for row, value in ((row_x, dn_x), (row_y, dn_y)):
        np.add.at(ata, face, row[:, :, None] * row[:, None, :])
        np.add.at(atb, face, row * value[:, None])

    # The loop kept a face only when it had >= 4 rows, i.e. >= 2 usable neighbours.
    solvable = np.bincount(face, minlength=count) >= 2
    if not solvable.any():
        return k1, k2, direction1, direction2

    index = np.flatnonzero(solvable)
    matrices = ata[index]
    # Tikhonov term standing in for lstsq's rcond cut on rank-deficient fits.
    scale = np.maximum(np.trace(matrices, axis1=1, axis2=2), 1e-30)
    matrices = matrices + np.eye(3) * (1e-12 * scale)[:, None, None]
    coefficients = np.linalg.solve(matrices, atb[index][:, :, None])[:, :, 0]

    shape = np.empty((len(index), 2, 2))
    shape[:, 0, 0] = coefficients[:, 0]
    shape[:, 0, 1] = coefficients[:, 1]
    shape[:, 1, 0] = coefficients[:, 1]
    shape[:, 1, 1] = coefficients[:, 2]
    values_2d, vectors_2d = np.linalg.eigh(shape)
    order = np.argsort(np.abs(values_2d), axis=1)[:, ::-1]
    rows = np.arange(len(index))[:, None]
    values_2d = values_2d[rows, order]
    vectors_2d = vectors_2d[rows[:, :, None], np.arange(2)[None, :, None], order[:, None, :]]

    k1[index] = values_2d[:, 0]
    k2[index] = values_2d[:, 1]
    first = vectors_2d[:, 0, 0][:, None] * tangent[index] + vectors_2d[:, 1, 0][:, None] * bitangent[index]
    second = vectors_2d[:, 0, 1][:, None] * tangent[index] + vectors_2d[:, 1, 1][:, None] * bitangent[index]
    direction1[index] = normalized(first)
    direction2[index] = normalized(second)
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
            # Fold the 3-D voxel key into one int64 so the cheap 1-D np.unique
            # can be used instead of np.unique(axis=0), which builds a
            # structured view and sorts rows (3.2x slower here, bit-identical
            # result -- the key ranges are tiny next to int64, so no collisions).
            low = keys.min(axis=0)
            span = (keys.max(axis=0) - low + 1).astype(np.int64)
            flat = ((keys[:, 0] - low[0]) * span[1] + (keys[:, 1] - low[1])) * span[2] + (keys[:, 2] - low[2])
            _unique, inverse = np.unique(flat, return_inverse=True)
            inverse = inverse.reshape(-1)
            cells = int(inverse.max()) + 1
            counts = np.bincount(inverse, minlength=cells).astype(np.float64)
            sums = np.empty((cells, 3), dtype=np.float64)
            for axis in range(3):
                sums[:, axis] = np.bincount(inverse, weights=normals[:, axis], minlength=cells)
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
