from __future__ import annotations

from collections import deque

import numpy as np

from .models import Adjacency, BoundaryField, GeometryFields, OrientationField
from .normals import normalized


def smooth_normals_across_nonstructural_edges(
    adjacency: Adjacency,
    face_normals: np.ndarray,
    boundary: BoundaryField,
    iterations: int,
    maximum_crossing_cost: float,
) -> np.ndarray:
    """Locally average normals without blurring across strong walls/seams."""
    smoothed = face_normals.copy()
    passable = boundary.refined_cost <= maximum_crossing_cost
    edge_a = adjacency.face_a[passable]
    edge_b = adjacency.face_b[passable]
    for _ in range(max(0, iterations)):
        aggregate = smoothed.copy()
        counts = np.ones(len(smoothed))
        np.add.at(aggregate, edge_a, smoothed[edge_b])
        np.add.at(aggregate, edge_b, smoothed[edge_a])
        np.add.at(counts, edge_a, 1.0)
        np.add.at(counts, edge_b, 1.0)
        smoothed = normalized(aggregate / counts[:, None])
    return smoothed


def slope_from_positive_world_z(normals: np.ndarray) -> np.ndarray:
    """Return degrees from horizontal: angle between the face normal and +Z."""
    return np.degrees(np.arccos(np.clip(normals[:, 2], -1.0, 1.0)))


def slope_from_up_axis(normals: np.ndarray, up_vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(up_vector, dtype=float)
    vector /= max(float(np.linalg.norm(vector)), 1e-15)
    return np.degrees(np.arccos(np.clip(normals @ vector, -1.0, 1.0)))


def _recover_isolated_noise(
    initial: np.ndarray,
    slope_deg: np.ndarray,
    upward: np.ndarray,
    adjacency: Adjacency,
    boundary: BoundaryField,
    config: dict,
) -> tuple[np.ndarray, np.ndarray]:
    cfg = config["orientation"]
    recovered = np.zeros_like(initial)
    result = initial.copy()
    maximum_slope = float(cfg["max_slope_deg"]) + float(cfg["noise_recovery_margin_deg"])
    minimum_fraction = float(cfg["noise_recovery_neighbor_fraction"])
    maximum_cost = float(config["segmentation"]["max_boundary_cost"])
    for _ in range(int(cfg["noise_recovery_iterations"])):
        additions: list[int] = []
        for face in np.flatnonzero(~result & upward & (slope_deg <= maximum_slope)):
            # Classification majority intentionally examines both sides of an edge:
            # an isolated noisy triangle can itself create a false high structural cost.
            local_neighbors = [neighbor for neighbor, _edge in adjacency.neighbors[int(face)]]
            if len(local_neighbors) < 2:
                continue
            if float(np.mean(result[local_neighbors])) >= minimum_fraction:
                additions.append(int(face))
        if not additions:
            break
        result[additions] = True
        recovered[additions] = True
    return result, recovered


def _expand_hysteresis_from_core(
    core: np.ndarray,
    maximum_mask: np.ndarray,
    adjacency: Adjacency,
) -> np.ndarray:
    """Expand <=22° core faces through connected <=25° fringe faces.

    A fully and consistently tilted <=25° mesh is retained when no core face
    exists anywhere. That preserves the documented 24° whole-surface case while
    preventing an isolated fringe-only island from competing with a real core.
    """
    if not np.any(core):
        return maximum_mask.copy()
    accepted = core.copy()
    queue: deque[int] = deque(int(face) for face in np.flatnonzero(core))
    while queue:
        face = queue.popleft()
        for neighbor, _edge_index in adjacency.neighbors[face]:
            if maximum_mask[neighbor] and not accepted[neighbor]:
                accepted[neighbor] = True
                queue.append(neighbor)
    return accepted


def calculate_orientation_field(
    adjacency: Adjacency,
    fields: GeometryFields,
    boundary: BoundaryField,
    config: dict,
    up_vector: np.ndarray | None = None,
) -> OrientationField:
    cfg = config["orientation"]
    smoothed = smooth_normals_across_nonstructural_edges(
        adjacency,
        fields.face_normals,
        boundary,
        int(cfg["normal_smoothing_iterations"]),
        float(cfg["smoothing_boundary_cost"]),
    )
    up_vector = np.asarray(up_vector if up_vector is not None else [0.0, 0.0, 1.0], dtype=float)
    raw_slope = slope_from_up_axis(fields.face_normals, up_vector)
    slope = slope_from_up_axis(smoothed, up_vector)
    upward = (smoothed @ up_vector) > 0.0
    core = upward & (slope <= float(cfg["core_slope_deg"]))
    maximum_mask = upward & (slope <= float(cfg["max_slope_deg"]))
    maximum_mask, recovered = _recover_isolated_noise(
        maximum_mask, slope, upward, adjacency, boundary, config
    )
    fringe = _expand_hysteresis_from_core(core, maximum_mask, adjacency)
    recovered &= fringe
    return OrientationField(smoothed, raw_slope, slope, upward, core, fringe, recovered)


def connected_candidate_components(
    eligible: np.ndarray,
    adjacency: Adjacency,
    boundary: BoundaryField,
    maximum_crossing_cost: float,
    recovered_noise_faces: np.ndarray | None = None,
) -> list[np.ndarray]:
    """Split orientation-eligible faces at structural boundary edges."""
    seen = np.zeros_like(eligible)
    recovered_noise_faces = recovered_noise_faces if recovered_noise_faces is not None else np.zeros_like(eligible)
    components: list[np.ndarray] = []
    for start in np.flatnonzero(eligible):
        if seen[start]:
            continue
        members: list[int] = []
        queue: deque[int] = deque([int(start)])
        seen[start] = True
        while queue:
            face = queue.popleft(); members.append(face)
            for neighbor, edge_index in adjacency.neighbors[face]:
                if seen[neighbor] or not eligible[neighbor]:
                    continue
                if boundary.refined_cost[edge_index] > maximum_crossing_cost and not (
                    recovered_noise_faces[face] or recovered_noise_faces[neighbor]
                ):
                    continue
                seen[neighbor] = True; queue.append(neighbor)
        components.append(np.asarray(members, dtype=np.int64))
    return components
