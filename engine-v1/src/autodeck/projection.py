from __future__ import annotations

import numpy as np

from .models import AnalysisMesh, BoundaryLoop, GeometryFields, Mesh


def loop_points_in_input_coordinates(
    loop: BoundaryLoop,
    analysis: AnalysisMesh,
    original: Mesh,
) -> np.ndarray:
    source_indices = analysis.source_vertex_indices[np.asarray(loop.analysis_vertex_indices, dtype=np.int64)]
    # Representatives are original scan vertices, preserving exact scan/world coordinates.
    return original.vertices[source_indices]


def lifted_loop_points_in_input_coordinates(
    loop: BoundaryLoop,
    analysis: AnalysisMesh,
    original: Mesh,
    fields: GeometryFields,
    lift_mm: float,
) -> np.ndarray:
    points = loop_points_in_input_coordinates(loop, analysis, original)
    analysis_indices = np.asarray(loop.analysis_vertex_indices, dtype=np.int64)
    normals = fields.vertex_normals[analysis_indices]
    return points + normals * (lift_mm / analysis.mm_per_input_unit)

