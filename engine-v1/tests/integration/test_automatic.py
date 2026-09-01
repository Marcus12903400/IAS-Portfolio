from __future__ import annotations

import copy
import unittest

import numpy as np

from autodeck.candidates import discover_candidates
from autodeck.component_diagnostics import segmentation_stage_diagnostics
from autodeck.config import load_config
from autodeck.curvature import calculate_geometry_fields
from autodeck.orientation import calculate_orientation_field, slope_from_positive_world_z
from autodeck.preprocessing import build_adjacency, preprocess
from autodeck.relief import calculate_boundary_field
from autodeck.synthetic import curved_strip, extruded_profile, grid_surface
from autodeck.up_axis import assess_up_axes, resolve_up_axis


def automatic(mesh, config=None):
    config = copy.deepcopy(config or load_config())
    config["segmentation"]["minimum_candidate_area_mm2"] = 100.0
    adjacency = build_adjacency(mesh)
    fields = calculate_geometry_fields(mesh, adjacency, config)
    boundary, warnings = calculate_boundary_field(mesh, adjacency, fields, config, [])
    orientation = calculate_orientation_field(adjacency, fields, boundary, config)
    candidates, candidate_warnings = discover_candidates(mesh, adjacency, fields, boundary, orientation, config)
    return candidates, orientation, boundary, warnings + candidate_warnings


class AutomaticCandidateTests(unittest.TestCase):
    def test_physical_analysis_resolution_reduces_dense_mesh_and_reports_change(self):
        mesh = grid_surface(80, 60, 0.5)
        original_vertices = mesh.vertices.copy()
        original_faces = mesh.faces.copy()
        config = load_config(); config["mesh"]["analysis_resolution_mm"] = 2.5
        prepared, _warnings = preprocess(mesh, "mm", config)
        self.assertLess(len(prepared.mesh_mm.faces), 0.25 * len(mesh.faces))
        np.testing.assert_array_equal(mesh.vertices, original_vertices)
        np.testing.assert_array_equal(mesh.faces, original_faces)
        metrics = prepared.preprocessing_metrics
        self.assertEqual(metrics["requested_analysis_resolution_mm"], 2.5)
        self.assertGreater(metrics["triangle_reduction_percent"], 75.0)
        self.assertIn("surface_area_change_percent", metrics)
        self.assertIn("bbox_extent_change_mm", metrics)
        self.assertGreaterEqual(metrics["preprocessing_runtime_seconds"], 0.0)

    def test_2_5_mm_analysis_mesh_preserves_resolvable_hatch_relief(self):
        def recessed_hatch(x, y):
            return -2.0 if 15.0 <= x <= 25.0 and 10.0 <= y <= 20.0 else 0.0

        mesh = grid_surface(80, 60, 0.5, recessed_hatch)
        config = load_config(); config["mesh"]["analysis_resolution_mm"] = 2.5
        prepared, _warnings = preprocess(mesh, "mm", config)
        adjacency = build_adjacency(prepared.mesh_mm)
        fields = calculate_geometry_fields(prepared.mesh_mm, adjacency, config)
        boundary, _boundary_warnings = calculate_boundary_field(
            prepared.mesh_mm, adjacency, fields, config, []
        )
        self.assertLess(len(prepared.mesh_mm.faces), 0.25 * len(mesh.faces))
        self.assertGreater(
            float(boundary.refined_cost.max()),
            float(config["segmentation"]["max_boundary_cost"]),
        )

    def test_multiscale_normal_neighborhoods_are_physically_distinct(self):
        mesh = curved_strip(radius_mm=120.0, segments=96, across=12, width_mm=120.0)
        config = load_config()
        adjacency = build_adjacency(mesh)
        fields = calculate_geometry_fields(mesh, adjacency, config)
        p95 = [
            float(np.percentile(fields.multiscale_normal_variation_deg[scale], 95))
            for scale in (5.0, 15.0, 40.0, 100.0)
        ]
        self.assertEqual(len({round(value, 6) for value in p95}), 4)
        self.assertTrue(all(later > earlier for earlier, later in zip(p95, p95[1:])), p95)

    def test_inconsistent_and_downward_face_winding_is_repaired_upward(self):
        mesh = grid_surface(8, 6, 10.0)
        mesh.faces[::2] = mesh.faces[::2][:, [0, 2, 1]]
        prepared, _warnings = preprocess(mesh, "mm", load_config())
        adjacency = build_adjacency(prepared.mesh_mm)
        fields = calculate_geometry_fields(prepared.mesh_mm, adjacency, load_config())
        self.assertTrue(np.all(fields.face_normals[:, 2] > 0.99))

    def test_slope_is_degrees_from_horizontal_using_positive_z(self):
        normals = np.array([
            [0.0, 0.0, 1.0],
            [np.sin(np.radians(30.0)), 0.0, np.cos(np.radians(30.0))],
            [0.0, 0.0, -1.0],
        ])
        np.testing.assert_allclose(slope_from_positive_world_z(normals), [0.0, 30.0, 180.0], atol=1e-8)

    def test_auto_up_axis_detects_a_minus_y_deck_before_expensive_processing(self):
        mesh = grid_surface(30, 20, 5.0)
        # Rotate +Z normal to -Y while retaining the original face winding.
        mesh.vertices = np.column_stack((mesh.vertices[:, 0], -mesh.vertices[:, 2], mesh.vertices[:, 1]))
        scores = assess_up_axes(mesh, 25.0, 10000)
        selected, vector = resolve_up_axis("auto", scores)
        self.assertEqual(selected, "-Y")
        config = load_config(); config["mesh"]["analysis_resolution_mm"] = 0.0
        prepared, _warnings = preprocess(mesh, "mm", config, vector)
        adjacency = build_adjacency(prepared.mesh_mm)
        fields = calculate_geometry_fields(prepared.mesh_mm, adjacency, config)
        boundary, _boundary_warnings = calculate_boundary_field(prepared.mesh_mm, adjacency, fields, config, [])
        orientation = calculate_orientation_field(adjacency, fields, boundary, config, vector)
        self.assertTrue(np.all(orientation.core_candidate))

    def test_no_seed_flat_floor_becomes_primary(self):
        candidates, orientation, *_ = automatic(grid_surface(20, 14, 10.0))
        self.assertGreaterEqual(len(candidates), 1)
        self.assertTrue(candidates[0].is_primary)
        self.assertAlmostEqual(candidates[0].mean_slope_deg, 0.0, places=6)
        self.assertTrue(np.all(orientation.core_candidate))
        self.assertTrue(candidates[0].touches_open_mesh_boundary)

    def test_mild_and_24_degree_surfaces_remain_eligible(self):
        for degrees in (15.0, 20.0, 24.0):
            slope = np.tan(np.radians(degrees))
            mesh = grid_surface(20, 12, 10.0, lambda x, _y, s=slope: s * x)
            candidates, orientation, *_ = automatic(mesh)
            self.assertEqual(len(candidates), 1, f"{degrees} degree surface was not retained")
            self.assertLessEqual(float(np.percentile(orientation.smoothed_slope_deg, 95)), 25.0)

    def test_30_degree_surface_is_rejected(self):
        slope = np.tan(np.radians(30.0))
        mesh = grid_surface(20, 12, 10.0, lambda x, _y: slope * x)
        candidates, orientation, *_ = automatic(mesh)
        self.assertEqual(candidates, [])
        self.assertTrue(np.all(orientation.smoothed_slope_deg > 25.0))

    def test_isolated_noisy_faces_do_not_punch_holes_in_23_degree_surface(self):
        base = np.tan(np.radians(23.0))
        mesh = grid_surface(24, 16, 10.0, lambda x, _y: base * x)
        # Perturb a handful of vertices, creating local raw slopes above 25 degrees.
        row = 25
        for j, i, dz in ((5, 7, 1.5), (9, 13, -1.5), (12, 19, 1.5)):
            mesh.vertices[j * row + i, 2] += dz
        candidates, orientation, *_ = automatic(mesh)
        self.assertGreater(np.count_nonzero(orientation.raw_slope_deg > 25.0), 0)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].face_count, len(mesh.faces))
        self.assertGreater(np.count_nonzero(orientation.recovered_noise_faces), 0)

    def test_floor_stops_before_near_vertical_wall(self):
        mesh = extruded_profile([(0, 0), (50, 0), (100, 0), (100, 50), (100, 100)], width_mm=120, across=12)
        candidates, orientation, *_ = automatic(mesh)
        self.assertGreaterEqual(len(candidates), 1)
        chosen_faces = np.flatnonzero(candidates[0].selected_faces)
        centroids = mesh.vertices[mesh.faces].mean(axis=1)
        self.assertFalse(np.any(centroids[chosen_faces, 2] > 1.0))
        self.assertTrue(np.any(orientation.smoothed_slope_deg > 80.0))

    def test_hatch_seam_can_split_horizontal_regions_and_create_internal_boundary(self):
        def height(x, y):
            on_vertical = (x in {60.0, 100.0}) and 40.0 <= y <= 80.0
            on_horizontal = (y in {40.0, 80.0}) and 60.0 <= x <= 100.0
            return -5.0 if on_vertical or on_horizontal else 0.0

        mesh = grid_surface(32, 24, 5.0, height)
        candidates, _, boundary, _ = automatic(mesh)
        self.assertGreater(float(boundary.refined_cost.max()), 0.56)
        self.assertGreaterEqual(len(candidates), 2)
        self.assertGreater(candidates[0].area_mm2, candidates[1].area_mm2)
        self.assertGreaterEqual(len(candidates[0].internal_boundaries), 1)

    def test_orientation_baseline_retains_parent_region_and_reports_seam_internally(self):
        def height(x, y):
            on_vertical = (x in {60.0, 100.0}) and 40.0 <= y <= 80.0
            on_horizontal = (y in {40.0, 80.0}) and 60.0 <= x <= 100.0
            return -1.5 if on_vertical or on_horizontal else 0.0

        mesh = grid_surface(32, 24, 5.0, height)
        config = load_config(); config["segmentation"]["minimum_candidate_area_mm2"] = 100.0
        adjacency = build_adjacency(mesh)
        fields = calculate_geometry_fields(mesh, adjacency, config)
        boundary, _warnings = calculate_boundary_field(mesh, adjacency, fields, config, [])
        orientation = calculate_orientation_field(adjacency, fields, boundary, config)
        diagnostics, components, *_unused = segmentation_stage_diagnostics(
            orientation.fringe_candidate,
            fields.conformability >= float(config["segmentation"]["minimum_conformability"]),
            adjacency,
            fields.face_areas,
            boundary,
            float(config["segmentation"]["max_boundary_cost"]),
            float(config["segmentation"]["minimum_candidate_area_mm2"]),
            "orientation",
            orientation.recovered_noise_faces,
        )
        candidates, _candidate_warnings = discover_candidates(
            mesh, adjacency, fields, boundary, orientation, config,
            components=components, use_conformability=False, use_structural_boundaries=False,
        )
        self.assertEqual(diagnostics["stage_a_orientation_only"]["connected_component_count"], 1)
        self.assertGreater(
            diagnostics["stage_c_orientation_conformability_structural_crossing"]["connected_component_count"],
            1,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(candidates[0].internal_boundaries), 0)
        self.assertGreater(float(boundary.refined_cost.max()), float(config["segmentation"]["max_boundary_cost"]))


if __name__ == "__main__":
    unittest.main()
