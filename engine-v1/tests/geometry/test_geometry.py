from __future__ import annotations

import copy
import unittest

import numpy as np

from autodeck.boundaries import extract_boundary_loops
from autodeck.config import load_config
from autodeck.curvature import calculate_geometry_fields
from autodeck.preprocessing import build_adjacency
from autodeck.relief import calculate_boundary_field, temporary_overlay_face_mask
from autodeck.segmentation import seed_faces_from_points, segment
from autodeck.synthetic import curved_strip, extruded_profile, grid_surface


def run_geometry(mesh, point, paper_centers=None, config=None):
    config = copy.deepcopy(config or load_config())
    adjacency = build_adjacency(mesh)
    fields = calculate_geometry_fields(mesh, adjacency, config)
    boundary, warnings = calculate_boundary_field(mesh, adjacency, fields, config, paper_centers or [])
    seeds, _ = seed_faces_from_points(fields.face_centroids, [np.asarray(point, dtype=float)], config["markers"]["seed_radius_mm"])
    overlay_faces = temporary_overlay_face_mask(
        fields.face_centroids, paper_centers or [], config["markers"]["paper_suppression_radius_mm"]
    )
    selected = segment(adjacency, fields, boundary, seeds, config, overlay_faces)
    loops, loop_warnings = extract_boundary_loops(mesh, adjacency, boundary, selected, config)
    return selected, loops, boundary, fields, warnings + loop_warnings


class GeometryAcceptanceTests(unittest.TestCase):
    def test_flat_and_sloped_planes_grow_completely(self):
        for slope in (0.0, 0.08):
            mesh = grid_surface(12, 8, 5.0, lambda x, _y, s=slope: s * x)
            selected, loops, *_ = run_geometry(mesh, [10.0, 15.0, slope * 10.0])
            self.assertTrue(np.all(selected))
            self.assertEqual(sum(loop.kind == "outer" for loop in loops), 1)

    def test_seed_location_does_not_define_flat_region_outline(self):
        mesh = grid_surface(14, 10, 5.0)
        first, first_loops, *_ = run_geometry(mesh, [8.0, 8.0, 0.0])
        second, second_loops, *_ = run_geometry(mesh, [60.0, 40.0, 0.0])
        np.testing.assert_array_equal(first, second)
        self.assertAlmostEqual(first_loops[0].length_mm, second_loops[0].length_mm, places=6)

    def test_gradual_single_axis_bend_continues(self):
        mesh = curved_strip(radius_mm=300.0)
        selected, _, boundary, fields, _ = run_geometry(mesh, mesh.vertices[0])
        self.assertTrue(np.all(selected))
        self.assertLess(float(np.percentile(boundary.normal_turn_deg_per_mm, 95)), 0.35)
        self.assertLess(float(np.percentile(np.abs(fields.gaussian_curvature), 95)), 5e-4)

    def test_tight_bend_stops_growth(self):
        mesh = curved_strip(radius_mm=20.0)
        config = load_config(); config["markers"]["seed_radius_mm"] = 1.0
        selected, *_ = run_geometry(mesh, mesh.vertices[0], config=config)
        self.assertGreater(np.count_nonzero(selected), 0)
        self.assertLess(np.count_nonzero(selected), len(selected))

    def test_abrupt_floor_to_wall_transition_stops(self):
        mesh = extruded_profile([(0, 0), (10, 0), (20, 0), (20, 10), (20, 20)])
        config = load_config(); config["markers"]["seed_radius_mm"] = 2.0
        selected, *_ = run_geometry(mesh, [5, 20, 0], config=config)
        centroids_x = mesh.vertices[mesh.faces].mean(axis=1)[:, 0]
        centroids_z = mesh.vertices[mesh.faces].mean(axis=1)[:, 2]
        self.assertTrue(np.any(selected & (centroids_x < 20)))
        self.assertFalse(np.any(selected & (centroids_z > 1.0)))

    def test_recessed_seam_is_a_high_cost_barrier(self):
        mesh = grid_surface(20, 8, 5.0, lambda x, _y: -5.0 if 48.0 <= x <= 52.0 else 0.0)
        config = load_config(); config["markers"]["seed_radius_mm"] = 2.0
        selected, _, boundary, *_ = run_geometry(mesh, [10, 20, 0], config=config)
        centroids_x = mesh.vertices[mesh.faces].mean(axis=1)[:, 0]
        self.assertFalse(np.any(selected & (centroids_x > 55.0)))
        self.assertGreater(float(boundary.refined_cost.max()), config["segmentation"]["max_boundary_cost"])

    def test_temporary_paper_suppression_prevents_false_outline(self):
        height = lambda x, y: 1.5 if 35.0 <= x <= 55.0 and 10.0 <= y <= 30.0 else 0.0
        mesh = grid_surface(18, 8, 5.0, height)
        config = load_config(); config["markers"]["seed_radius_mm"] = 2.0
        config["markers"]["paper_suppression_radius_mm"] = 30.0
        without, *_ = run_geometry(mesh, [10, 20, 0], config=config)
        with_paper, loops, boundary, *_ = run_geometry(mesh, [10, 20, 0], [np.array([45, 20, 1.5])], config)
        self.assertLess(np.count_nonzero(without), len(without))
        self.assertTrue(np.all(with_paper))
        self.assertTrue(np.any(boundary.suppressed))
        self.assertEqual(sum(loop.kind == "internal_exclusion" for loop in loops), 0)

    def test_raised_obstruction_becomes_internal_exclusion(self):
        height = lambda x, y: 8.0 if 35.0 <= x <= 55.0 and 15.0 <= y <= 35.0 else 0.0
        mesh = grid_surface(18, 10, 5.0, height)
        config = load_config(); config["markers"]["seed_radius_mm"] = 2.0
        selected, loops, *_ = run_geometry(mesh, [10, 10, 0], config=config)
        self.assertLess(np.count_nonzero(selected), len(selected))
        self.assertGreaterEqual(sum(loop.kind == "internal_exclusion" for loop in loops), 1)


if __name__ == "__main__":
    unittest.main()
