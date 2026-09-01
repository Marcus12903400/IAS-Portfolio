from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from autodeck.config import load_config
from autodeck.pipeline import analyze_scan, inspect_scan
from autodeck.synthetic import grid_surface, save_obj
from autodeck.topview import _Grid, _components, _curves_from_mask, _mask_from_components


class PipelineIntegrationTests(unittest.TestCase):
    def test_topview_run_length_components_preserve_exact_pixels(self):
        mask = np.zeros((5, 9), dtype=bool)
        mask[1, 2:5] = True
        mask[2, 3:7] = True
        mask[4, 8] = True
        components = _components(mask)
        self.assertEqual([component.area_pixels for component in components], [7, 1])
        self.assertTrue(np.array_equal(_mask_from_components(mask.shape, components), mask))

    def test_feature_export_uses_one_outer_envelope_per_region(self):
        mask = np.zeros((30, 30), dtype=bool)
        mask[2:20, 2:20] = True
        mask[7:15, 7:15] = False  # A raster-only interior texture gap.
        curves = _curves_from_mask(
            mask, "AUTODECK::NONSKID_MEDIUM", "nonskid", np.zeros(mask.shape),
            _Grid(0.0, 0.0, 1.0, 30, 30), 1.0, 0.5, 0.7,
        )
        self.assertEqual(len(curves), 1)
        self.assertGreater(curves[0].metrics["area_mm2"], 300.0)

    def test_inspection_and_analysis_emit_reproducible_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); scan = root / "scan.obj"
            save_obj(grid_surface(40, 30, 10.0), scan)
            inspection = inspect_scan(scan, root / "inspection")
            self.assertEqual(inspection["resolved_units"], "mm")
            result = analyze_scan(scan, root / "analysis", load_config(), None, [], [], False)
            output = root / "analysis"
            artifacts = output / "processing_and_debug"
            expected = {
                "proposed_boundaries.obj", "analysis_mesh.ply", "boundary_scores.npz",
                "boundary_scores.json", "segmentation.json", "debug_metrics.json", "run_report.md",
                "orientation_scores.npz", "slope_bands.ply",
                "candidate_masks.npz",
                "development_preview.3dm", "flattened_preview.3dm",
                "flattened_curves_native.dxf", "flattened_curves_polyline.dxf",
                "scale_check.dxf", "development_report.md", "curve_fit_report.md",
                "feature_summary.json",
            }
            self.assertTrue(expected.issubset({path.name for path in artifacts.iterdir()}))
            root_items = {path.name for path in output.iterdir()}
            self.assertEqual(len(root_items), 3)
            self.assertIn("processing_and_debug", root_items)
            self.assertIn("verification.3dm", root_items)
            self.assertEqual(
                len(root_items & {"final.dxf", "FINAL_NOT_READY.txt"}), 1
            )
            segmentation = json.loads((artifacts / "segmentation.json").read_text(encoding="utf-8"))
            self.assertEqual(segmentation["seed_mode"], "automatic")
            self.assertEqual(len(segmentation["candidates"]), 1)
            self.assertEqual(segmentation["primary_candidate_id"], 1)
            self.assertIsNone(segmentation["approved_geometry"])
            self.assertIsNotNone(result["proposal"])
            self.assertEqual(segmentation["segmentation_mode"], "orientation")
            validation = segmentation["export_validation"]
            self.assertTrue(validation["successful_v01_run"])
            self.assertGreater(validation["primary_boundary_point_count"], 0)
            self.assertGreater(validation["proposed_boundary_vertex_count"], 0)
            self.assertGreater(validation["verification_3dm_object_count"], 0)
            self.assertGreater(validation["verification_3dm_curve_count"], 0)
            self.assertLessEqual(validation["default_visible_curve_count"], 100)
            self.assertTrue(validation["successful_v02_run"])
            self.assertTrue(validation["primary_raster_area_is_plausible"])
            self.assertTrue(validation["primary_outer_contour_closed"])
            self.assertTrue(validation["secondary_outer_contours_complete"])
            self.assertTrue((output / "verification.3dm").exists())
            obj_text = (artifacts / "proposed_boundaries.obj").read_text(encoding="utf-8")
            self.assertIn("AUTODECK_DECK_PRIMARY_OUTER", obj_text)
            self.assertNotIn("INTERNAL_BOUNDARY", obj_text)
            self.assertNotIn("DISPLAY_LIFT", obj_text)
            self.assertIn("\nv ", obj_text)
            self.assertIn("\nl ", obj_text)
            self.assertNotIn("selected_face_indices_analysis", segmentation["candidates"][0])
            self.assertIn("selection_hole_count", segmentation["candidates"][0])
            metrics = json.loads((artifacts / "debug_metrics.json").read_text(encoding="utf-8"))
            for stage in (
                "stage_a_orientation_only",
                "stage_b_orientation_plus_conformability",
                "stage_c_orientation_conformability_structural_crossing",
            ):
                self.assertIn(stage, metrics["segmentation_stages"])
                self.assertEqual(metrics["segmentation_stages"][stage]["connected_component_count"], 1)

    def test_large_orientation_area_without_candidate_is_an_explicit_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); scan = root / "scan.obj"; output = root / "analysis"
            save_obj(grid_surface(40, 40, 10.0), scan)  # 160,000 mm² of eligible floor.
            config = load_config()
            config["segmentation"]["minimum_candidate_area_mm2"] = 200000.0
            result = analyze_scan(scan, output, config, "mm", [], [], False)
            self.assertEqual(result["status"], "SEGMENTATION_FAILURE")
            artifacts = output / "processing_and_debug"
            segmentation = json.loads((artifacts / "segmentation.json").read_text(encoding="utf-8"))
            self.assertEqual(segmentation["candidates"], [])
            self.assertIsNone(segmentation["primary_candidate_id"])
            self.assertTrue(any(
                warning.startswith("SEGMENTATION FAILURE: 160,000 mm² passed orientation filtering")
                for warning in segmentation["warnings"]
            ))
            validation = segmentation["export_validation"]
            self.assertFalse(validation["successful_v01_run"])
            self.assertEqual(validation["proposed_boundary_vertex_count"], 0)
            self.assertEqual(validation["verification_3dm_object_count"], 0)
            self.assertGreater(validation["orientation_only_diagnostic_curve_count"], 0)
            self.assertNotIn("\nv ", (artifacts / "proposed_boundaries.obj").read_text(encoding="utf-8"))
            self.assertIn("\nv ", (artifacts / "orientation_only_diagnostic.obj").read_text(encoding="utf-8"))
            report = (artifacts / "run_report.md").read_text(encoding="utf-8")
            self.assertIn("## SEGMENTATION FAILURE", report)
            self.assertIn("Stage A — orientation mask only", report)

    def test_v02_topview_hypotheses_are_distinct_and_project_to_floor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); output = root / "analysis"
            scan = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic-cockpit" / "scan.obj"
            config = load_config(); config["segmentation"]["minimum_candidate_area_mm2"] = 100.0
            result = analyze_scan(scan, output, config, "mm", [], [], False)
            self.assertNotIn("FAILURE", result["status"])
            artifacts = output / "processing_and_debug"
            for name in (
                "topview_primary_mask.png", "topview_height_residual.png", "topview_raw_feature_density.png",
                "topview_nonskid_loose.png", "topview_nonskid_medium.png", "topview_nonskid_strict.png",
                "topview_seam_response.png", "topview_obstacle_mask.png", "topview_combined_overlay.png",
            ):
                self.assertTrue((artifacts / "debug" / name).exists(), name)
            with np.load(artifacts / "topview_features.npz") as maps:
                self.assertIn("secondary_mask", maps.files)
                shape = tuple(int(value) for value in maps["primary_mask_shape"])
                size = shape[0] * shape[1]
                counts = [
                    int(np.count_nonzero(np.unpackbits(maps[f"nonskid_{name}"], bitorder="little")[:size]))
                    for name in ("loose", "medium", "strict")
                ]
            self.assertGreater(counts[0], counts[1])
            self.assertGreater(counts[1], counts[2])
            segmentation = json.loads((artifacts / "segmentation.json").read_text(encoding="utf-8"))
            self.assertLess((artifacts / "segmentation.json").stat().st_size, 1_000_000)
            medium_nonskid = [feature for feature in segmentation["features"] if feature["layer"] == "AUTODECK::NONSKID_MEDIUM"]
            self.assertGreaterEqual(len(medium_nonskid), 1)
            self.assertLess(max(point[2] for feature in medium_nonskid for point in feature["points_input_coordinates"]), 10.0)
            self.assertLessEqual(segmentation["export_validation"]["default_visible_curve_count"], 100)
            topview = segmentation["topview"]
            self.assertEqual(
                len(topview["secondary_outer_curves"]),
                max(0, len(segmentation["candidates"]) - 1),
            )
            self.assertGreater(topview["primary_occupancy_to_surface_area_ratio"], 0.5)
            self.assertLess(topview["primary_occupancy_to_surface_area_ratio"], 1.5)
            self.assertGreater(topview["primary_outer_projected_area_mm2"], 0.5 * segmentation["candidates"][0]["area_mm2"])
            self.assertGreater(topview["primary_outer_length_mm"], 100.0)
            void_records = topview["obstacles"]["void_records"]
            if void_records:
                for key in (
                    "area_mm2", "perimeter_mm", "equivalent_diameter_mm", "compactness",
                    "cleanup_persistence_fraction", "height_above_floor_max_mm",
                    "selected_primary_footprint", "preserved_as_debug_alternative",
                ):
                    self.assertIn(key, void_records[0])
            import rhino3dm
            model = rhino3dm.File3dm.Read(str(output / "verification.3dm"))
            visibility = {layer.Name: layer.Visible for layer in model.Layers}
            self.assertTrue(visibility["AUTODECK::DECK_PRIMARY_OUTER"])
            self.assertFalse(visibility["AUTODECK_CALIBRATION::NONSKID_LOOSE"])

    def test_v02_seam_detector_retains_a_long_coherent_shallow_relief_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); scan = root / "seam.obj"
            mesh = grid_surface(240, 120, 1.0, lambda _x, y: -0.3 if abs(y - 60.0) < 0.1 else 0.0)
            save_obj(mesh, scan)
            config = load_config(); config["segmentation"]["minimum_candidate_area_mm2"] = 100.0
            result = analyze_scan(scan, root / "analysis", config, "mm", [], [], False)
            seams = result["metrics"]["topview"]["seams"]
            self.assertGreaterEqual(seams["high_confidence_count"], 1)
            self.assertGreater(seams["total_high_confidence_length_mm"], 100.0)
            self.assertLessEqual(result["metrics"]["export"]["default_visible_curve_count"], 100)

    def test_unknown_units_use_documented_configured_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            scan = Path(temporary) / "unknown.obj"
            text = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
            scan.write_text(text, encoding="utf-8")
            config = load_config(); config["segmentation"]["minimum_candidate_area_mm2"] = 0.1
            result = analyze_scan(scan, Path(temporary) / "analysis", config, None, [], [], False)
            self.assertEqual(result["metrics"]["input_units"], "mm")
            segmentation = json.loads((
                Path(temporary) / "analysis" / "processing_and_debug" / "segmentation.json"
            ).read_text(encoding="utf-8"))
            self.assertTrue(any("configured default 'mm'" in warning for warning in segmentation["warnings"]))


if __name__ == "__main__":
    unittest.main()
