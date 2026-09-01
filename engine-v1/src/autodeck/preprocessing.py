from __future__ import annotations

from collections import deque
import time

import numpy as np

from .config import unit_scale_to_mm
from .models import Adjacency, AnalysisMesh, Mesh


def build_adjacency(mesh: Mesh) -> Adjacency:
    edge_map: dict[tuple[int, int], list[int]] = {}
    for face_index, face in enumerate(mesh.faces):
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            key = (int(min(a, b)), int(max(a, b)))
            edge_map.setdefault(key, []).append(face_index)

    fa: list[int] = []
    fb: list[int] = []
    eu: list[int] = []
    ev: list[int] = []
    bf: list[int] = []
    bu: list[int] = []
    bv: list[int] = []
    nonmanifold: list[tuple[int, int, list[int]]] = []
    neighbors: list[list[tuple[int, int]]] = [[] for _ in mesh.faces]
    for (u, v), incident in edge_map.items():
        if len(incident) == 1:
            bf.append(incident[0]); bu.append(u); bv.append(v)
        elif len(incident) == 2:
            edge_index = len(fa)
            fa.append(incident[0]); fb.append(incident[1]); eu.append(u); ev.append(v)
            neighbors[incident[0]].append((incident[1], edge_index))
            neighbors[incident[1]].append((incident[0], edge_index))
        else:
            nonmanifold.append((u, v, list(incident)))
    return Adjacency(
        np.asarray(fa, dtype=np.int64), np.asarray(fb, dtype=np.int64),
        np.asarray(eu, dtype=np.int64), np.asarray(ev, dtype=np.int64),
        np.asarray(bf, dtype=np.int64), np.asarray(bu, dtype=np.int64), np.asarray(bv, dtype=np.int64),
        nonmanifold, neighbors,
    )


def _remove_degenerate(mesh: Mesh, minimum_area: float) -> tuple[Mesh, np.ndarray]:
    tri = mesh.vertices[mesh.faces]
    areas = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    keep = (areas > minimum_area) & np.all(
        np.stack([mesh.faces[:, 0] != mesh.faces[:, 1], mesh.faces[:, 1] != mesh.faces[:, 2], mesh.faces[:, 2] != mesh.faces[:, 0]], axis=1),
        axis=1,
    )
    indices = np.flatnonzero(keep)
    result = mesh.copy()
    result.faces = result.faces[keep]
    if result.face_uv is not None:
        result.face_uv = result.face_uv[keep]
    return result, indices


def _remove_small_components(mesh: Mesh, minimum_faces: int) -> tuple[Mesh, np.ndarray]:
    if minimum_faces <= 1:
        return mesh, np.arange(len(mesh.faces), dtype=np.int64)
    adjacency = build_adjacency(mesh)
    seen = np.zeros(len(mesh.faces), dtype=bool)
    keep = np.zeros(len(mesh.faces), dtype=bool)
    for start in range(len(mesh.faces)):
        if seen[start]:
            continue
        component: list[int] = []
        queue = deque([start]); seen[start] = True
        while queue:
            face = queue.popleft(); component.append(face)
            for neighbor, _ in adjacency.neighbors[face]:
                if not seen[neighbor]:
                    seen[neighbor] = True; queue.append(neighbor)
        if len(component) >= minimum_faces:
            keep[component] = True
    indices = np.flatnonzero(keep)
    result = mesh.copy(); result.faces = result.faces[keep]
    if result.face_uv is not None:
        result.face_uv = result.face_uv[keep]
    return result, indices


def _orient_faces_consistently(mesh: Mesh) -> tuple[Mesh, int]:
    edge_records: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for face_index, face in enumerate(mesh.faces):
        for u, v in ((int(face[0]), int(face[1])), (int(face[1]), int(face[2])), (int(face[2]), int(face[0]))):
            edge_records.setdefault((min(u, v), max(u, v)), []).append((face_index, u < v))
    constraints: list[list[tuple[int, bool]]] = [[] for _ in mesh.faces]
    for records in edge_records.values():
        if len(records) != 2:
            continue
        (face_a, direction_a), (face_b, direction_b) = records
        # Equal directed-edge orientation means exactly one face must flip.
        xor_required = direction_a == direction_b
        constraints[face_a].append((face_b, xor_required))
        constraints[face_b].append((face_a, xor_required))
    flip = np.zeros(len(mesh.faces), dtype=bool)
    assigned = np.zeros(len(mesh.faces), dtype=bool)
    conflicts = 0
    for start in range(len(mesh.faces)):
        if assigned[start]:
            continue
        queue = deque([start]); assigned[start] = True
        while queue:
            face = queue.popleft()
            for neighbor, xor_required in constraints[face]:
                expected = bool(flip[face]) ^ xor_required
                if not assigned[neighbor]:
                    assigned[neighbor] = True; flip[neighbor] = expected; queue.append(neighbor)
                elif bool(flip[neighbor]) != expected:
                    conflicts += 1
    result = mesh.copy()
    if np.any(flip):
        result.faces[flip] = result.faces[flip][:, [0, 2, 1]]
        if result.face_uv is not None:
            result.face_uv[flip] = result.face_uv[flip][:, [0, 2, 1]]
    return result, conflicts


def _vertex_cluster(mesh: Mesh, resolution_mm: float) -> tuple[Mesh, np.ndarray, np.ndarray]:
    if resolution_mm <= 0:
        return mesh, np.arange(len(mesh.vertices), dtype=np.int64), np.arange(len(mesh.faces), dtype=np.int64)
    origin = mesh.vertices.min(axis=0)
    keys = np.floor((mesh.vertices - origin) / resolution_mm).astype(np.int64)
    # The previous per-cluster ``inverse == cluster`` loop was O(vertices ×
    # clusters). First-occurrence representatives make clustering vectorized and
    # keep every analysis vertex exactly on the original full-resolution scan.
    _unique_keys, representatives_array, inverse = np.unique(
        keys, axis=0, return_index=True, return_inverse=True
    )
    representatives_array = representatives_array.astype(np.int64, copy=False)
    remapped = inverse[mesh.faces]
    keep = np.all(np.stack([remapped[:, 0] != remapped[:, 1], remapped[:, 1] != remapped[:, 2], remapped[:, 2] != remapped[:, 0]], axis=1), axis=1)
    remapped = remapped[keep]
    retained_faces = np.flatnonzero(keep)
    # A requested physical cell can be larger than a tiny test part or isolated
    # feature. Never replace a valid mesh with an empty analysis representation.
    if not len(remapped):
        return mesh, np.arange(len(mesh.vertices), dtype=np.int64), np.arange(len(mesh.faces), dtype=np.int64)
    if len(remapped):
        canonical = np.sort(remapped, axis=1)
        _, unique_indices = np.unique(canonical, axis=0, return_index=True)
        unique_indices = np.sort(unique_indices)
        remapped = remapped[unique_indices]
        retained_faces = retained_faces[unique_indices]
    clustered = Mesh(mesh.vertices[representatives_array], remapped, source_path=mesh.source_path, metadata=dict(mesh.metadata))
    return clustered, representatives_array, retained_faces


def _orient_connected_components_upward(mesh: Mesh, up_vector: np.ndarray | None = None) -> tuple[Mesh, int]:
    """Choose the winding of each connected component so its area-weighted Z normal is positive."""
    up_vector = np.asarray(up_vector if up_vector is not None else [0.0, 0.0, 1.0], dtype=float)
    adjacency = build_adjacency(mesh)
    visited = np.zeros(len(mesh.faces), dtype=bool)
    result = mesh.copy()
    flipped_components = 0
    for start in range(len(mesh.faces)):
        if visited[start]:
            continue
        component: list[int] = []
        queue = deque([start]); visited[start] = True
        while queue:
            face = queue.popleft(); component.append(face)
            for neighbor, _ in adjacency.neighbors[face]:
                if not visited[neighbor]:
                    visited[neighbor] = True; queue.append(neighbor)
        indices = np.asarray(component, dtype=np.int64)
        triangles = result.vertices[result.faces[indices]]
        area_vectors = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        if float(np.dot(area_vectors.sum(axis=0), up_vector)) < 0.0:
            result.faces[indices] = result.faces[indices][:, [0, 2, 1]]
            if result.face_uv is not None:
                result.face_uv[indices] = result.face_uv[indices][:, [0, 2, 1]]
            flipped_components += 1
    return result, flipped_components


def preprocess(mesh: Mesh, units: str, config: dict, up_vector: np.ndarray | None = None) -> tuple[AnalysisMesh, list[str]]:
    started = time.perf_counter()
    scale = unit_scale_to_mm(units)
    working = mesh.copy()
    working.vertices *= scale
    warnings: list[str] = []
    requested_resolution = float(config["mesh"]["analysis_resolution_mm"])
    original_bbox_min = working.vertices.min(axis=0)
    original_bbox_max = working.vertices.max(axis=0)
    original_triangles = working.vertices[working.faces]
    original_area = float((0.5 * np.linalg.norm(
        np.cross(original_triangles[:, 1] - original_triangles[:, 0], original_triangles[:, 2] - original_triangles[:, 0]),
        axis=1,
    )).sum())

    working, face_map = _remove_degenerate(working, float(config["mesh"]["degenerate_area_mm2"]))
    if len(face_map) != len(mesh.faces):
        warnings.append(f"Removed {len(mesh.faces) - len(face_map)} degenerate triangles")
    # Decimate before adjacency-heavy winding/component work. On the cockpit
    # scan this avoids constructing multiple 30-million-edge Python maps for the
    # full-resolution mesh while leaving the original mesh object untouched.
    clustering_started = time.perf_counter()
    clustered, vertex_map, clustered_face_map = _vertex_cluster(working, requested_resolution)
    clustering_seconds = time.perf_counter() - clustering_started
    face_map = face_map[clustered_face_map]
    clustered, component_map = _remove_small_components(
        clustered, int(config["mesh"]["minimum_component_faces"])
    )
    face_map = face_map[component_map]
    clustered, orientation_conflicts = _orient_faces_consistently(clustered)
    if orientation_conflicts:
        warnings.append(f"Detected {orientation_conflicts} contradictory face-winding constraints")
    clustered, upward_flips = _orient_connected_components_upward(clustered, up_vector)
    if upward_flips:
        warnings.append(f"Reoriented {upward_flips} connected mesh component(s) toward positive world Z")
    if len(clustered.vertices) < len(working.vertices):
        warnings.append(f"Vertex clustering reduced {len(working.vertices)} vertices to {len(clustered.vertices)}")
    adjacency = build_adjacency(clustered)
    if adjacency.nonmanifold_edges:
        warnings.append(f"Analysis mesh contains {len(adjacency.nonmanifold_edges)} non-manifold edges")
    analysis_triangles = clustered.vertices[clustered.faces]
    analysis_area = float((0.5 * np.linalg.norm(
        np.cross(analysis_triangles[:, 1] - analysis_triangles[:, 0], analysis_triangles[:, 2] - analysis_triangles[:, 0]),
        axis=1,
    )).sum()) if len(clustered.faces) else 0.0
    analysis_bbox_min = clustered.vertices.min(axis=0)
    analysis_bbox_max = clustered.vertices.max(axis=0)
    preprocessing_metrics = {
        "requested_analysis_resolution_mm": requested_resolution,
        "original_triangle_count": int(len(mesh.faces)),
        "analysis_triangle_count": int(len(clustered.faces)),
        "triangle_reduction_percent": 100.0 * (1.0 - len(clustered.faces) / max(len(mesh.faces), 1)),
        "original_surface_area_mm2": original_area,
        "analysis_surface_area_mm2": analysis_area,
        "surface_area_change_percent": 100.0 * (analysis_area - original_area) / max(original_area, 1e-12),
        "original_bbox_min_mm": original_bbox_min.tolist(),
        "original_bbox_max_mm": original_bbox_max.tolist(),
        "analysis_bbox_min_mm": analysis_bbox_min.tolist(),
        "analysis_bbox_max_mm": analysis_bbox_max.tolist(),
        "bbox_min_change_mm": (analysis_bbox_min - original_bbox_min).tolist(),
        "bbox_max_change_mm": (analysis_bbox_max - original_bbox_max).tolist(),
        "bbox_extent_change_mm": (
            (analysis_bbox_max - analysis_bbox_min) - (original_bbox_max - original_bbox_min)
        ).tolist(),
        "vertex_clustering_runtime_seconds": clustering_seconds,
        "preprocessing_runtime_seconds": time.perf_counter() - started,
    }
    return AnalysisMesh(clustered, vertex_map, face_map, units, scale, preprocessing_metrics), warnings
