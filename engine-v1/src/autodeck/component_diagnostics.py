from __future__ import annotations

import heapq

import numpy as np

from .models import Adjacency, BoundaryField


def component_statistics(
    eligible: np.ndarray,
    adjacency: Adjacency,
    face_areas: np.ndarray,
    boundary: BoundaryField | None = None,
    maximum_crossing_cost: float | None = None,
    crossing_override_faces: np.ndarray | None = None,
    return_components_at_or_above_mm2: float | None = None,
) -> tuple[dict[str, object], list[np.ndarray], np.ndarray | None]:
    """Measure face-connected components and optionally retain physically large ones.

    When ``boundary`` is omitted, every mesh-adjacency edge is traversable. This
    distinction is what makes Stage A/B independent from structural refinement.
    """
    eligible = np.asarray(eligible, dtype=bool)
    visited = np.zeros(len(eligible), dtype=bool)
    retained: list[np.ndarray] = []
    largest_component: np.ndarray | None = None
    largest_area = 0.0
    largest_face_count = 0
    second_largest_area = 0.0
    top_areas: list[float] = []
    component_count = 0
    components_ge_10000 = 0

    for start in np.flatnonzero(eligible):
        if visited[start]:
            continue
        stack = [int(start)]
        visited[start] = True
        members: list[int] = []
        area = 0.0
        while stack:
            face = stack.pop()
            members.append(face)
            area += float(face_areas[face])
            for neighbor, edge_index in adjacency.neighbors[face]:
                if visited[neighbor] or not eligible[neighbor]:
                    continue
                if boundary is not None and maximum_crossing_cost is not None:
                    crossing_overridden = bool(
                        crossing_override_faces is not None
                        and (crossing_override_faces[face] or crossing_override_faces[neighbor])
                    )
                    if boundary.refined_cost[edge_index] > maximum_crossing_cost and not crossing_overridden:
                        continue
                visited[neighbor] = True
                stack.append(neighbor)
        component_count += 1
        member_array = np.asarray(members, dtype=np.int64)
        if area >= 10000.0:
            components_ge_10000 += 1
        if return_components_at_or_above_mm2 is not None and area >= return_components_at_or_above_mm2:
            retained.append(member_array)
        if area > largest_area:
            second_largest_area = largest_area
            largest_area = area
            largest_face_count = len(members)
            largest_component = member_array
        elif area > second_largest_area:
            second_largest_area = area
        if len(top_areas) < 10:
            heapq.heappush(top_areas, area)
        elif area > top_areas[0]:
            heapq.heapreplace(top_areas, area)

    retained.sort(key=lambda component: float(face_areas[component].sum()), reverse=True)
    stats: dict[str, object] = {
        "eligible_face_count": int(np.count_nonzero(eligible)),
        "eligible_surface_area_mm2": float(face_areas[eligible].sum()),
        "connected_component_count": component_count,
        "largest_component_area_mm2": largest_area,
        "second_largest_component_area_mm2": second_largest_area,
        "top_10_component_areas_mm2": sorted(top_areas, reverse=True),
        "components_at_or_above_10000_mm2": components_ge_10000,
        "largest_component_face_count": largest_face_count,
    }
    return stats, retained, largest_component


def segmentation_stage_diagnostics(
    orientation_mask: np.ndarray,
    conformability_mask: np.ndarray,
    adjacency: Adjacency,
    face_areas: np.ndarray,
    boundary: BoundaryField,
    maximum_crossing_cost: float,
    minimum_candidate_area_mm2: float,
    segmentation_mode: str,
    recovered_noise_faces: np.ndarray | None = None,
) -> tuple[
    dict[str, dict[str, object]],
    list[np.ndarray],
    np.ndarray | None,
    list[np.ndarray],
    np.ndarray | None,
]:
    stage_a, stage_a_components, stage_a_largest = component_statistics(
        orientation_mask,
        adjacency,
        face_areas,
        return_components_at_or_above_mm2=minimum_candidate_area_mm2,
    )
    stage_b_mask = orientation_mask & conformability_mask
    stage_b, _unused_b, _largest_b = component_statistics(stage_b_mask, adjacency, face_areas)
    stage_c, stage_c_components, stage_c_largest = component_statistics(
        stage_b_mask,
        adjacency,
        face_areas,
        boundary,
        maximum_crossing_cost,
        recovered_noise_faces,
        minimum_candidate_area_mm2 if segmentation_mode == "structural-experimental" else None,
    )
    diagnostics = {
        "stage_a_orientation_only": stage_a,
        "stage_b_orientation_plus_conformability": stage_b,
        "stage_c_orientation_conformability_structural_crossing": stage_c,
    }
    if segmentation_mode == "structural-experimental":
        return diagnostics, stage_c_components, stage_c_largest, stage_a_components, stage_a_largest
    return diagnostics, stage_a_components, stage_a_largest, stage_a_components, stage_a_largest
