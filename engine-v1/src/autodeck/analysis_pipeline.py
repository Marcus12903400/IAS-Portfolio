from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import __version__
from .candidates import candidate_from_selection, discover_candidates
from .component_diagnostics import segmentation_stage_diagnostics
from .conditioning import condition_curves, curve_length, useful_raw_curves
from .config import unit_scale_to_mm
from .curvature import calculate_geometry_fields
from .debug import (
    geometry_metrics,
    save_boundary_npz,
    save_boundary_summary,
    save_colored_ply,
    save_orientation_npz,
    save_slope_bands_ply,
)
from .markers import detect_aruco_seeds
from .mesh_io import load_obj, mesh_statistics, save_json
from .curve_fit import ManufacturingCurve, fit_manufacturing_curves
from .development import (
    CurveDevelopmentMap,
    DevelopmentResult,
    backproject_flat_points,
    develop_patch,
    map_curve_to_development,
    save_development_artifact,
)
from .dxf_export import write_manufacturing_dxfs, write_v032_dxfs
from .flattening import flat_layer_name
from .models import CandidateRegion, ConditionedCurve, FeatureCurve, FlattenedCurve, PanelProposal, PanelStatus, TopViewResult
from .polyarc import PolyarcCurve, fit_polyarc_curves
from .patterns import PatternResult, generate_pattern, write_pattern_outputs, write_pattern_report
from .robust_reference import RobustReferenceCurve, fit_robust_reference_curves
from .orientation import calculate_orientation_field
from .preprocessing import build_adjacency, preprocess
from .production_output import PROCESSING_DIRECTORY, finalize_production_output
from .relief import calculate_boundary_field, temporary_overlay_face_mask
from .rhino_export import export_candidate_3dm_optional, export_candidate_obj_curves, export_feature_obj_curves
from .segmentation import seed_faces_from_points, segment
from .topview import build_topview_features
from .up_axis import assess_up_axes, resolve_up_axis
from .v03_export import append_backprojected_to_verification, export_development_preview, export_flattened_preview
from .v031_reports import make_length_audit, write_v031_reports
from .v032_reports import make_landmark_dimension_audit, write_polyarc_report
from .v033_reports import write_v033_report


def analyze_scan(
    input_path: Path,
    output_dir: Path,
    config: dict[str, Any],
    units: str | None,
    seed_points_input: list[np.ndarray] | None = None,
    paper_centers_input: list[np.ndarray] | None = None,
    enable_marker_detection: bool = True,
    seed_mode: str | None = None,
    segmentation_mode: str | None = None,
    up_axis: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Analyze a prepared scan; automatic orientation discovery is the default.

    ``progress``, if given, is called with a short human-readable label
    before each major stage starts -- this is the only user-facing feedback
    during a run that can take minutes on a real scan, so callers (the CLI,
    the wizard) should print it directly rather than silently discard it.
    """

    def _report(stage: str) -> None:
        if progress is not None:
            progress(stage)

    started = time.perf_counter()
    stage_timings: dict[str, float] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    _report("Loading OBJ mesh")
    stage_started = time.perf_counter()
    original = load_obj(input_path)
    stage_timings["obj_load_seconds"] = time.perf_counter() - stage_started
    stage_started = time.perf_counter()
    stats = mesh_statistics(original)
    stage_timings["input_inspection_seconds"] = time.perf_counter() - stage_started
    warnings: list[str] = []
    resolved_units = units or original.metadata.get("detected_units")
    if resolved_units is None:
        resolved_units = str(config["mesh"]["default_input_units"])
        warnings.append(
            f"OBJ declares no physical units; using configured default '{resolved_units}'. Verify scale before Rhino approval."
        )
    mm_per_unit = unit_scale_to_mm(str(resolved_units))
    requested_up_axis = up_axis or str(config.get("up_axis", {}).get("mode", "+Z"))
    up_axis_scores = assess_up_axes(
        original,
        float(config["orientation"]["max_slope_deg"]),
        int(config.get("up_axis", {}).get("sample_face_count", 200000)),
    )
    selected_up_axis, up_vector = resolve_up_axis(requested_up_axis, up_axis_scores)
    best_axis = max(up_axis_scores, key=up_axis_scores.get)
    selected_score = up_axis_scores[selected_up_axis]; best_score = up_axis_scores[best_axis]
    if requested_up_axis != "auto" and best_score > 0.0 and selected_score < float(config.get("up_axis", {}).get("implausible_area_ratio", 0.20)) * best_score:
        warnings.append(
            f"UP-AXIS WARNING: {selected_up_axis} yields only {100.0 * selected_score:.2f}% sampled <=25° area; "
            f"{best_axis} yields {100.0 * best_score:.2f}%. Verify Rhino OBJ axis mapping before continuing."
        )
    axis_summary = {
        "requested": requested_up_axis,
        "selected": selected_up_axis,
        "sampled_orientation_area_fractions": up_axis_scores,
        "bbox_extent_input_units": stats["bbox_extent_input_units"],
    }

    shared_edges = max(1, int((3 * int(stats["triangles"]) - int(stats["boundary_edges"])) / 2))
    inconsistent_fraction = float(stats["inconsistent_winding_edges"]) / shared_edges
    if inconsistent_fraction >= float(config["mesh"]["inconsistent_normal_warning_fraction"]):
        warnings.append(
            f"Large inconsistent-normal fraction before repair: {100.0 * inconsistent_fraction:.1f}% of shared edges"
        )

    _report("Preprocessing mesh (repair, resolution)")
    stage_started = time.perf_counter()
    analysis, preprocessing_warnings = preprocess(original, str(resolved_units), config, up_vector)
    warnings.extend(preprocessing_warnings)
    adjacency = build_adjacency(analysis.mesh_mm)
    stage_timings["broad_preprocessing_seconds"] = time.perf_counter() - stage_started
    _report("Computing geometry fields")
    stage_started = time.perf_counter()
    fields = calculate_geometry_fields(analysis.mesh_mm, adjacency, config)
    stage_timings["broad_geometry_fields_seconds"] = time.perf_counter() - stage_started

    seed_points_input = list(seed_points_input or [])
    paper_centers_input = list(paper_centers_input or [])
    mode = seed_mode or ("manual" if seed_points_input else "automatic")
    if mode not in {"automatic", "manual", "marker"}:
        raise ValueError("seed_mode must be automatic, manual, or marker")
    segmentation_mode = segmentation_mode or str(config["segmentation"].get("mode", "orientation"))
    if segmentation_mode == "advanced":
        warnings.append(
            "STRUCTURAL-EXPERIMENTAL WARNING: --segmentation-mode advanced is a deprecated alias. "
            "Structural crossing fragmented the Key West Stage C surface into 255,847 components; "
            "it is diagnostic only and is not a production candidate source."
        )
        segmentation_mode = "structural-experimental"
    if segmentation_mode not in {"orientation", "structural-experimental"}:
        raise ValueError("segmentation_mode must be orientation or structural-experimental")
    if segmentation_mode == "structural-experimental":
        warnings.append(
            "STRUCTURAL-EXPERIMENTAL MODE: boundary crossing can heavily fragment the accepted "
            "orientation floor. Compare with orientation mode; do not treat this as the production result."
        )
    marker_records: list[dict[str, object]] = []
    if mode == "marker":
        if not enable_marker_detection:
            raise ValueError("marker seed mode cannot be combined with disabled marker detection")
        detections, marker_warnings = detect_aruco_seeds(original, str(config["markers"]["dictionary"]))
        warnings.extend(marker_warnings)
        for detection in detections:
            seed_points_input.append(detection.point_input)
            paper_centers_input.append(detection.point_input)
            marker_records.append({
                "id": detection.marker_id,
                "role": "DECK_SEED",
                "point_input": detection.point_input.tolist(),
                "confidence": detection.confidence,
                "source_texture": detection.source_texture,
            })

    paper_centers_mm = [np.asarray(point, dtype=float) * mm_per_unit for point in paper_centers_input]
    _report("Computing wall/floor boundary evidence")
    stage_started = time.perf_counter()
    boundary, boundary_warnings = calculate_boundary_field(
        analysis.mesh_mm, adjacency, fields, config, paper_centers_mm
    )
    warnings.extend(boundary_warnings)
    stage_timings["structural_response_seconds"] = time.perf_counter() - stage_started
    _report("Computing orientation field and segmentation diagnostics")
    stage_started = time.perf_counter()
    orientation = calculate_orientation_field(adjacency, fields, boundary, config, up_vector)
    conformability_mask = fields.conformability >= float(config["segmentation"]["minimum_conformability"])
    stage_diagnostics, discovery_components, _discovery_largest, _stage_a_components, stage_a_largest = (
        segmentation_stage_diagnostics(
            orientation.fringe_candidate,
            conformability_mask,
            adjacency,
            fields.face_areas,
            boundary,
            float(config["segmentation"]["max_boundary_cost"]),
            float(config["segmentation"]["minimum_candidate_area_mm2"]),
            segmentation_mode,
            orientation.recovered_noise_faces,
        )
    )
    stage_timings["orientation_and_component_diagnostics_seconds"] = time.perf_counter() - stage_started
    seed_records: list[dict[str, object]] = []

    _report("Discovering deckable-surface candidates")
    stage_started = time.perf_counter()
    if mode == "automatic":
        candidates, candidate_warnings = discover_candidates(
            analysis.mesh_mm,
            adjacency,
            fields,
            boundary,
            orientation,
            config,
            components=discovery_components,
            use_conformability=False,
            use_structural_boundaries=False,
            outer_only=True,
        )
        warnings.extend(candidate_warnings)
    else:
        if not seed_points_input:
            raise ValueError(f"No valid seed was supplied or detected for {mode} seed mode")
        seed_points_mm = [np.asarray(point, dtype=float) * mm_per_unit for point in seed_points_input]
        seed_faces, seed_records = seed_faces_from_points(
            fields.face_centroids, seed_points_mm, float(config["markers"]["seed_radius_mm"])
        )
        overlay_faces = temporary_overlay_face_mask(
            fields.face_centroids, paper_centers_mm, float(config["markers"]["paper_suppression_radius_mm"])
        )
        selected = segment(adjacency, fields, boundary, seed_faces, config, overlay_faces)
        seeded_candidate, seeded_warnings = candidate_from_selection(
            analysis.mesh_mm, adjacency, fields, boundary, orientation, selected, config
        )
        candidates = [seeded_candidate]
        warnings.extend(seeded_warnings)
    stage_timings["candidate_contour_generation_seconds"] = time.perf_counter() - stage_started

    orientation_eligible_area = float(
        stage_diagnostics["stage_a_orientation_only"]["eligible_surface_area_mm2"]
    )
    segmentation_failure_warning: str | None = None
    if not candidates:
        failure_threshold = float(
            config["segmentation"].get("segmentation_failure_orientation_area_mm2", 100000.0)
        )
        if orientation_eligible_area > failure_threshold:
            segmentation_failure_warning = (
                "SEGMENTATION FAILURE: "
                f"{orientation_eligible_area:,.0f} mm² passed orientation filtering but no candidate survived. "
                "See stage component diagnostics."
            )
        else:
            segmentation_failure_warning = (
                "SEGMENTATION FAILURE: no candidate survived physical-area filtering; "
                "see stage component diagnostics."
            )
        warnings.append(segmentation_failure_warning)
    primary = candidates[0] if candidates else None
    if primary and primary.touches_open_mesh_boundary:
        warnings.append(
            "Primary candidate touches an open mesh-cut boundary; preprocessing may have removed the physical floor transition"
        )

    selected_union = np.zeros(len(analysis.mesh_mm.faces), dtype=bool)
    for candidate in candidates:
        selected_union |= candidate.selected_faces

    topview_result: TopViewResult | None = None
    feature_curves: list[FeatureCurve] = []
    if primary and mode == "automatic" and bool(config.get("topview", {}).get("enabled", False)) and selected_up_axis == "+Z":
        topview_result = build_topview_features(
            output_dir, original, analysis, adjacency, fields, boundary, primary, config, candidates
        )
        feature_curves = topview_result.feature_curves
        warnings.extend(topview_result.warnings)
        stage_timings.update(topview_result.statistics.get("timings", {}))
        occupancy_ratio = float(
            topview_result.statistics.get("primary_occupancy_to_surface_area_ratio", 0.0)
        )
        if not 0.50 <= occupancy_ratio <= 1.50:
            warnings.append(
                "V0.2 RASTER FAILURE: the top-view primary occupancy is not physically consistent "
                f"with the accepted candidate surface area (ratio {occupancy_ratio:.6f})."
            )
    elif primary and mode == "automatic" and bool(config.get("topview", {}).get("enabled", False)):
        reason = f"the selected up axis is {selected_up_axis}, not +Z"
        warnings.append(f"V0.2 top-view feature extraction skipped because {reason}")

    conditioned_curves: list[ConditionedCurve] = []
    flattened_curves: list[FlattenedCurve] = []
    flattening_context = None
    development_results: dict[int, DevelopmentResult] = {}
    development_maps: list[CurveDevelopmentMap] = []
    manufacturing_curves: list[ManufacturingCurve] = []
    robust_references: list[RobustReferenceCurve] = []
    polyarc_curves: list[PolyarcCurve] = []
    backprojected_curves: list[FeatureCurve] = []
    polyarc_backprojected_curves: list[FeatureCurve] = []
    robust_backprojected_curves: list[FeatureCurve] = []
    development_preview_metrics: dict[str, Any] = {}
    verification_cam_metrics: dict[str, Any] = {}
    polyarc_verification_metrics: dict[str, Any] = {}
    robust_verification_metrics: dict[str, Any] = {}
    pattern_result = PatternResult("none", None, [], {})
    pattern_output_metrics: dict[str, Any] = {}
    dxf_metrics: dict[str, Any] = {}
    v032_dxf_metrics: dict[str, Any] = {}
    flat_preview_metrics: dict[str, Any] = {}
    length_audits: list[dict[str, Any]] = []
    dimension_audits: list[dict[str, Any]] = []
    length_audit_inputs: dict[str, tuple[float, np.ndarray, np.ndarray]] = {}
    conditioning_cfg = config.get("curve_conditioning", {})
    if topview_result and topview_result.accepted_surface is not None and bool(conditioning_cfg.get("enabled", True)):
        _report("Conditioning raw boundary curves")
        conditioning_started = time.perf_counter()
        raw_curves = useful_raw_curves(
            topview_result.primary_outer_curve,
            topview_result.secondary_outer_curves,
            feature_curves,
        )
        conditioned_curves = condition_curves(
            raw_curves, topview_result.accepted_surface, analysis.mm_per_input_unit, config
        )
        stage_timings["curve_conditioning_seconds"] = time.perf_counter() - conditioning_started
        for item in conditioned_curves:
            warnings.extend(f"{item.raw_curve_id}: {warning}" for warning in item.warnings)
        smoothing_debug = output_dir / "debug" / "smoothing"
        smoothing_debug.mkdir(parents=True, exist_ok=True)
        save_json(
            smoothing_debug / "conditioning_metrics.json",
            {
                "curve_count": len(conditioned_curves),
                "profiles": conditioning_cfg.get("profiles", {}),
                "curves": [_conditioned_payload(item) for item in conditioned_curves],
            },
        )
    elif topview_result and bool(conditioning_cfg.get("enabled", True)):
        warnings.append("CURVE CONDITIONING FAILURE: accepted deck surface raster was unavailable.")

    _report("Writing 3D verification export")
    export_started = time.perf_counter()
    lift_mm = float(config["verification"]["display_lift_mm"])
    deck_outer_overrides: dict[int, FeatureCurve] = {}
    if topview_result:
        if topview_result.primary_outer_curve is not None and primary is not None:
            deck_outer_overrides[primary.candidate_id] = topview_result.primary_outer_curve
        deck_outer_overrides.update(topview_result.secondary_outer_curves)
    obj_curve_count, obj_vertex_count = export_candidate_obj_curves(
        output_dir / "proposed_boundaries.obj", candidates, analysis, original, fields, lift_mm,
        deck_outer_overrides,
    )
    feature_obj_curve_count, feature_obj_vertex_count = export_feature_obj_curves(
        output_dir / "feature_boundaries.obj", feature_curves, analysis.input_units
    )
    wrote_3dm, export_warning, verification_3dm_object_count, verification_3dm_curve_count = export_candidate_3dm_optional(
        output_dir / "verification.3dm", candidates, analysis, original, fields, lift_mm,
        seed_points_input, paper_centers_input, feature_curves,
        deck_outer_overrides, conditioned_curves,
    )
    if export_warning:
        warnings.append(export_warning)
    if conditioned_curves:
        export_feature_obj_curves(
            output_dir / "conditioned_boundaries.obj",
            [item.smoothed_curve for item in conditioned_curves],
            analysis.input_units,
        )

    diagnostic_obj_curve_count = 0
    if not candidates and stage_a_largest is not None and len(stage_a_largest):
        diagnostic_selection = np.zeros(len(analysis.mesh_mm.faces), dtype=bool)
        diagnostic_selection[stage_a_largest] = True
        diagnostic_candidate, diagnostic_warnings = candidate_from_selection(
            analysis.mesh_mm,
            adjacency,
            fields,
            boundary,
            orientation,
            diagnostic_selection,
            config,
        )
        diagnostic_candidate.confidence_label = "DEBUG_ORIENTATION_ONLY"
        diagnostic_obj_curve_count, _diagnostic_vertex_count = export_candidate_obj_curves(
            output_dir / "orientation_only_diagnostic.obj",
            [diagnostic_candidate],
            analysis,
            original,
            fields,
            lift_mm,
        )
        if diagnostic_warnings:
            warnings.append(
                "Orientation-only diagnostic output was written despite final candidate failure: "
                "orientation_only_diagnostic.obj"
            )
    stage_timings["verification_export_seconds"] = time.perf_counter() - export_started

    flattening_cfg = config.get("flattening", {})  # retained in reports for V0.2 compatibility
    development_cfg = config.get("development", {})
    if conditioned_curves and topview_result and topview_result.accepted_surface is not None and bool(development_cfg.get("enabled", True)):
        _report("Developing surface, fitting CAM geometry, writing DXF")
        development_started = time.perf_counter()
        development_debug = output_dir / "debug" / "development"; development_debug.mkdir(parents=True, exist_ok=True)
        try:
            surface = topview_result.accepted_surface
            patch_ids = sorted(surface.patch_masks) or ([primary.candidate_id] if primary else [])
            for patch_id in patch_ids:
                result = develop_patch(surface, patch_id, config)
                development_results[patch_id] = result
                warnings.extend(f"development patch {patch_id}: {warning}" for warning in result.warnings)
                save_development_artifact(development_debug / f"development_patch_{patch_id:03d}.npz", result)

            mapping_archive: dict[str, np.ndarray] = {}
            primary_patch_id = primary.candidate_id if primary else (patch_ids[0] if patch_ids else -1)
            maximum_mapping_distance = float(development_cfg["maximum_curve_mapping_distance_mm"])
            for curve_index, item in enumerate(conditioned_curves, 1):
                patch_id = int(item.raw_curve.metrics.get("candidate_id", primary_patch_id))
                result = development_results.get(patch_id) or development_results.get(primary_patch_id)
                if result is None:
                    continue
                smooth_mm = item.smoothed_curve.points_input * analysis.mm_per_input_unit
                mapping = map_curve_to_development(smooth_mm, result, maximum_mapping_distance)
                development_maps.append(mapping)
                status = "INVALID" if result.status == "INVALID" or mapping.status == "INVALID" else (
                    "NEEDS_REVIEW" if result.status == "NEEDS_REVIEW" or mapping.status == "NEEDS_REVIEW" or item.status != "GOOD" else "GOOD"
                )
                flat_id = f"flat-raw-{curve_index:04d}-{item.raw_curve.name.lower().replace(' ', '_')}"
                distance_metrics = {
                    "maximum_mm": float(np.max(mapping.distances_mm)) if len(mapping.distances_mm) else 0.0,
                    "p95_mm": float(np.percentile(mapping.distances_mm, 95)) if len(mapping.distances_mm) else 0.0,
                    "rms_mm": float(np.sqrt(np.mean(mapping.distances_mm ** 2))) if len(mapping.distances_mm) else 0.0,
                }
                flattened_curves.append(FlattenedCurve(
                    item, flat_id, mapping.flat_points_mm,
                    "FLAT_RAW_" + flat_layer_name(item.raw_curve.layer), item.smoothed_curve.closed, status,
                    {
                        "raw_curve_id": item.raw_curve_id, "conditioned_3d_id": item.smoothed_curve_id,
                        "development_patch_id": result.patch_id, "development_strategy": result.strategy,
                        "triangle_association_count": int(len(mapping.source_triangle_ids)),
                        "mapping_distance_mm": distance_metrics,
                    },
                ))
                key = f"curve_{curve_index:04d}"
                mapped_source_mesh = np.einsum(
                    "ni,nij->nj", mapping.barycentric_coordinates,
                    result.mesh.vertices_mm[result.mesh.faces[mapping.source_triangle_ids]],
                )
                mapped_development_base = np.einsum(
                    "ni,nij->nj", mapping.barycentric_coordinates,
                    result.mesh.base_vertices_mm[result.mesh.faces[mapping.source_triangle_ids]],
                )
                length_audit_inputs[flat_id] = (
                    curve_length(smooth_mm), mapped_source_mesh, mapped_development_base,
                )
                mapping_archive[f"{key}_triangle_ids"] = mapping.source_triangle_ids.astype(np.int32)
                mapping_archive[f"{key}_barycentric"] = mapping.barycentric_coordinates.astype(np.float64)
                mapping_archive[f"{key}_distance_mm"] = mapping.distances_mm.astype(np.float32)
                mapping_archive[f"{key}_flat_points_mm"] = mapping.flat_points_mm.astype(np.float64)
                mapping_archive[f"{key}_conditioned_length_mm"] = np.asarray([curve_length(smooth_mm)], dtype=np.float64)
                mapping_archive[f"{key}_mapped_source_mesh_points_mm"] = mapped_source_mesh.astype(np.float32)
                mapping_archive[f"{key}_mapped_development_base_points_mm"] = mapped_development_base.astype(np.float32)
            np.savez_compressed(output_dir / "development_curve_maps.npz", **mapping_archive)

            manufacturing_curves = fit_manufacturing_curves(flattened_curves, config)
            for curve in manufacturing_curves:
                seed = length_audit_inputs.get(curve.source.flattened_curve_id)
                if seed is None:
                    continue
                conditioned_length, mapped_source_mesh, mapped_development_base = seed
                audit = make_length_audit(
                    curve.cam_curve_id, conditioned_length, mapped_source_mesh,
                    mapped_development_base, curve.source.points_mm, curve.points_mm,
                )
                curve.metrics["perimeter_length_audit"] = audit
                length_audits.append(audit)
            for curve in manufacturing_curves:
                warnings.extend(f"{curve.cam_curve_id}: {warning}" for warning in curve.warnings)
            robust_references = fit_robust_reference_curves(
                manufacturing_curves, config.get("robust_reference", {}),
            )
            for reference in robust_references:
                warnings.extend(f"{reference.curve_id}: {warning}" for warning in reference.warnings)
            polyarc_settings = dict(config["polyarc_fit"])
            polyarc_settings["absolute_fit_tolerance_mm"] = float(
                config["manufacturing_fit"].get("reference_max_deviation_mm", 3.0)
            )
            polyarc_curves = fit_polyarc_curves(
                manufacturing_curves, polyarc_settings, robust_references,
            )
            for spline, polyarc in zip(manufacturing_curves, polyarc_curves):
                warnings.extend(
                    f"polyarc-{polyarc.candidate_id:04d}: {warning}" for warning in polyarc.warnings
                )
                seed = length_audit_inputs.get(spline.source.flattened_curve_id)
                if seed is not None and len(polyarc.hard_corner_indices):
                    _, _, mapped_development_base = seed
                    dimension_audits.append(make_landmark_dimension_audit(
                        spline.cam_curve_id,
                        polyarc.hard_corner_indices,
                        mapped_development_base,
                        spline.source.points_mm,
                        polyarc,
                    ))
            if manufacturing_curves:
                dxf_metrics = write_manufacturing_dxfs(output_dir, manufacturing_curves, config)
                if dxf_metrics.get("status") != "GOOD":
                    warnings.append("DXF FAILURE: one or both ezdxf round-trip audits failed.")
                v032_dxf_metrics = write_v032_dxfs(
                    output_dir, polyarc_curves, manufacturing_curves, config,
                )
                if v032_dxf_metrics.get("status") != "GOOD":
                    warnings.append("V0.3.3 DXF FAILURE: a polyarc round-trip audit failed.")

            pattern_result = generate_pattern(
                polyarc_curves, manufacturing_curves, config, development_results,
            )
            pattern_output_metrics = write_pattern_outputs(
                output_dir, pattern_result, polyarc_curves, manufacturing_curves,
            )
            write_pattern_report(output_dir / "pattern_report.md", pattern_result)
            warnings.extend(f"pattern: {warning}" for warning in pattern_result.warnings)
            if pattern_output_metrics.get("status", "GOOD") != "GOOD":
                warnings.append("V0.3.3 PATTERN EXPORT FAILURE: DXF round-trip validation failed.")

            development_preview_metrics = export_development_preview(
                output_dir / "development_preview.3dm", development_results, flattened_curves
            )
            flat_preview_metrics = export_flattened_preview(
                output_dir / "flattened_preview.3dm", flattened_curves, manufacturing_curves,
                polyarc_curves, robust_references,
            )

            for curve_index, curve in enumerate(manufacturing_curves, 1):
                patch_id = int(curve.source.metrics.get("development_patch_id", primary_patch_id))
                result = development_results.get(patch_id)
                if result is None or curve.status == "INVALID":
                    continue
                points3d, inverse_map = backproject_flat_points(curve.points_mm, result, maximum_mapping_distance)
                backprojected = FeatureCurve(
                    "AUTODECK::CAM_BACKPROJECTED_3D", f"cam_backprojected_{curve_index:04d}",
                    points3d / analysis.mm_per_input_unit, curve.closed, 1.0,
                    {
                        "cam_curve_id": curve.cam_curve_id, "development_patch_id": patch_id,
                        "triangle_association_count": int(len(inverse_map.source_triangle_ids)),
                        "maximum_flat_mapping_distance_mm": float(np.max(inverse_map.distances_mm)) if len(inverse_map.distances_mm) else 0.0,
                    }, f"backprojected-{curve_index:04d}-{curve.cam_curve_id}",
                )
                backprojected_curves.append(backprojected)
                key = f"cam_{curve_index:04d}"
                mapping_archive[f"{key}_triangle_ids"] = inverse_map.source_triangle_ids.astype(np.int32)
                mapping_archive[f"{key}_barycentric"] = inverse_map.barycentric_coordinates.astype(np.float64)
            np.savez_compressed(output_dir / "development_curve_maps.npz", **mapping_archive)
            verification_cam_metrics = append_backprojected_to_verification(output_dir / "verification.3dm", backprojected_curves)
            for curve_index, (spline, reference) in enumerate(zip(manufacturing_curves, robust_references), 1):
                patch_id = int(spline.source.metrics.get("development_patch_id", primary_patch_id))
                result = development_results.get(patch_id)
                if result is None or not len(reference.points):
                    continue
                flat_points = np.column_stack((reference.points[:, :2], np.zeros(len(reference.points))))
                points3d, inverse_map = backproject_flat_points(flat_points, result, maximum_mapping_distance)
                robust_backprojected_curves.append(FeatureCurve(
                    "ROBUST_REFERENCE_3D", f"robust_reference_{curve_index:04d}",
                    points3d / analysis.mm_per_input_unit, reference.closed, 1.0,
                    {"cam_curve_id": spline.cam_curve_id, "development_patch_id": patch_id},
                    f"robust-reference-{curve_index:04d}-{spline.cam_curve_id}",
                ))
                key = f"robust_{curve_index:04d}"
                mapping_archive[f"{key}_flat_points_mm"] = flat_points.astype(np.float64)
                mapping_archive[f"{key}_triangle_ids"] = inverse_map.source_triangle_ids.astype(np.int32)
                mapping_archive[f"{key}_barycentric"] = inverse_map.barycentric_coordinates.astype(np.float64)
            robust_verification_metrics = append_backprojected_to_verification(
                output_dir / "verification.3dm", robust_backprojected_curves,
                "ROBUST_REFERENCE_3D", "AUTODECK_DEBUG::ROBUST_REFERENCE_DENSE_SAMPLES",
            )
            for curve_index, (spline, polyarc) in enumerate(zip(manufacturing_curves, polyarc_curves), 1):
                patch_id = int(spline.source.metrics.get("development_patch_id", primary_patch_id))
                result = development_results.get(patch_id)
                if result is None or polyarc.status == "INVALID" or not len(polyarc.sampled_points):
                    continue
                flat_points = np.column_stack((
                    polyarc.sampled_points[:, :2], np.zeros(len(polyarc.sampled_points)),
                ))
                points3d, inverse_map = backproject_flat_points(
                    flat_points, result, maximum_mapping_distance,
                )
                polyarc_backprojected_curves.append(FeatureCurve(
                    "AUTODECK::CAM_POLYARC_BACKPROJECTED_3D",
                    f"polyarc_backprojected_{curve_index:04d}",
                    points3d / analysis.mm_per_input_unit,
                    polyarc.is_closed,
                    1.0,
                    {"cam_curve_id": spline.cam_curve_id, "development_patch_id": patch_id},
                    f"polyarc-backprojected-{curve_index:04d}",
                ))
                key = f"polyarc_{curve_index:04d}"
                mapping_archive[f"{key}_triangle_ids"] = inverse_map.source_triangle_ids.astype(np.int32)
                mapping_archive[f"{key}_barycentric"] = inverse_map.barycentric_coordinates.astype(np.float64)
            np.savez_compressed(output_dir / "development_curve_maps.npz", **mapping_archive)
            polyarc_verification_metrics = append_backprojected_to_verification(
                output_dir / "verification.3dm",
                polyarc_backprojected_curves,
                "CAM_BACKPROJECTED_3D",
                "AUTODECK_DEBUG::CAM_V033_BACKPROJECTED_DENSE_SAMPLES",
            )
            if verification_cam_metrics.get("written"):
                verification_3dm_object_count = int(verification_cam_metrics.get("object_count", verification_3dm_object_count))
                verification_3dm_curve_count += int(verification_cam_metrics.get("curve_count", 0))
        except (ImportError, RuntimeError, ValueError) as exc:
            warnings.append(f"V0.3 MANUFACTURING GEOMETRY FAILURE: {exc}")
        stage_timings["surface_development_curve_fit_and_dxf_seconds"] = time.perf_counter() - development_started
        save_json(
            development_debug / "development_metrics.json",
            {
                "patches": {str(key): _development_payload(value) for key, value in development_results.items()},
                "flat_raw_curves": [_flattened_payload(item) for item in flattened_curves],
                "manufacturing_curves": [_manufacturing_payload(item) for item in manufacturing_curves],
                "dxf": dxf_metrics,
                "development_preview_3dm": development_preview_metrics,
                "flattened_preview_3dm": flat_preview_metrics,
                "verification_cam_backprojection": verification_cam_metrics,
                "perimeter_length_audits": length_audits,
                "polyarc_curves": [item.to_dict() for item in polyarc_curves],
                "polyarc_dxf": v032_dxf_metrics,
                "polyarc_verification_cam_backprojection": polyarc_verification_metrics,
                "landmark_dimension_audits": dimension_audits,
                "robust_reference_curves": [item.to_dict() for item in robust_references],
                "robust_reference_verification": robust_verification_metrics,
                "pattern": pattern_result.to_dict(),
            },
        )
    _report("Writing debug arrays (boundary/orientation scores, analysis mesh)")
    debug_serialization_started = time.perf_counter()
    save_boundary_npz(output_dir / "boundary_scores.npz", adjacency, boundary)
    save_boundary_summary(output_dir / "boundary_scores.json", adjacency, boundary)
    save_orientation_npz(output_dir / "orientation_scores.npz", orientation)
    save_colored_ply(output_dir / "analysis_mesh.ply", analysis.mesh_mm, adjacency, boundary, selected_union)
    save_slope_bands_ply(
        output_dir / "slope_bands.ply", analysis.mesh_mm, orientation,
        float(config["orientation"]["core_slope_deg"]),
        float(config["orientation"]["max_slope_deg"]),
    )
    stage_timings["broad_debug_serialization_seconds"] = time.perf_counter() - debug_serialization_started

    if topview_result and topview_result.primary_outer_curve is not None:
        primary_boundary_point_count = len(topview_result.primary_outer_curve.points_input)
    else:
        primary_boundary_point_count = sum(
            len(loop.analysis_vertex_indices)
            for loop in primary.outer_loops
        ) if primary else 0
    minimum_area = float(config["segmentation"]["minimum_candidate_area_mm2"])
    default_feature_curve_count = len(conditioned_curves) if conditioned_curves else sum(curve.layer.startswith("AUTODECK::") for curve in feature_curves)
    calibration_curve_count = sum(curve.layer.startswith("AUTODECK_CALIBRATION::") for curve in feature_curves)
    debug_curve_count = sum(curve.layer.startswith("AUTODECK_DEBUG::") for curve in feature_curves)
    default_visible_curve_count = len(conditioned_curves) if conditioned_curves else obj_curve_count + default_feature_curve_count
    export_validation = {
        "candidate_count_at_least_one": bool(candidates),
        "primary_candidate_id_is_not_null": primary is not None,
        "primary_candidate_exceeds_minimum_area": bool(primary and primary.area_mm2 >= minimum_area),
        "primary_boundary_point_count": primary_boundary_point_count,
        "proposed_boundary_curve_count": obj_curve_count,
        "proposed_boundary_vertex_count": obj_vertex_count,
        "proposed_boundary_has_geometry": obj_curve_count > 0 and obj_vertex_count > 0,
        "feature_obj_curve_count": feature_obj_curve_count,
        "feature_obj_vertex_count": feature_obj_vertex_count,
        "default_visible_curve_count": default_visible_curve_count,
        "calibration_curve_count": calibration_curve_count,
        "debug_curve_count": debug_curve_count,
        "conditioned_curve_count": len(conditioned_curves),
        "flattened_curve_count": len(flattened_curves),
        "flattened_preview_3dm_written": bool(flat_preview_metrics.get("written", False)),
        "flattened_preview_3dm_curve_count": int(
            flat_preview_metrics.get("raw_curve_count", 0) + flat_preview_metrics.get("cam_curve_count", 0)
        ),
        "flattened_dxf_written": bool(
            dxf_metrics and (output_dir / "flattened_curves_native.dxf").exists()
            and (output_dir / "flattened_curves_polyline.dxf").exists()
        ),
        "flattened_dxf_entity_count": int(dxf_metrics.get("total_entity_count", 0)),
        "verification_3dm_written": wrote_3dm,
        "verification_3dm_object_count": verification_3dm_object_count,
        "verification_3dm_curve_count": verification_3dm_curve_count,
        "verification_3dm_has_visible_curve": wrote_3dm and verification_3dm_curve_count > 0,
        "orientation_only_diagnostic_curve_count": diagnostic_obj_curve_count,
    }
    export_validation["successful_v01_run"] = all((
        export_validation["candidate_count_at_least_one"],
        export_validation["primary_candidate_id_is_not_null"],
        export_validation["primary_candidate_exceeds_minimum_area"],
        export_validation["proposed_boundary_has_geometry"],
        export_validation["verification_3dm_has_visible_curve"],
    ))
    topview_stats = topview_result.statistics if topview_result else {}
    meaningful_voids = int(topview_stats.get("obstacles", {}).get("meaningful_void_count", 0))
    obstacle_hypotheses = int(topview_stats.get("obstacles", {}).get("loose_count", 0))
    occupancy_ratio = float(topview_stats.get("primary_occupancy_to_surface_area_ratio", 0.0))
    raster_area_plausible = bool(0.50 <= occupancy_ratio <= 1.50)
    expected_secondary_count = max(0, len(candidates) - 1)
    raster_secondary_count = len(topview_result.secondary_outer_curves) if topview_result else 0
    v02_maximum_curves = int(config.get("v02_export", {}).get("maximum_reasonable_default_curves", 100))
    export_validation.update({
        "primary_outer_contour_exists": bool(
            primary and (
                (topview_result and topview_result.primary_outer_curve is not None)
                or primary.outer_loops
            )
        ),
        "primary_outer_contour_closed": bool(
            primary and (
                (topview_result and topview_result.primary_outer_curve is not None
                 and topview_result.primary_outer_curve.closed)
                or any(loop.is_closed for loop in primary.outer_loops)
            )
        ),
        "primary_raster_area_is_plausible": raster_area_plausible,
        "secondary_outer_contours_complete": raster_secondary_count == expected_secondary_count,
        "default_visible_curve_count_is_reasonable": default_visible_curve_count <= v02_maximum_curves,
        "topview_feature_density_raster_exists": bool(topview_result and (output_dir / "debug" / "topview_raw_feature_density.png").exists()),
        "nonskid_hypothesis_layers_exist": bool(topview_result and "nonskid" in topview_stats),
        "seam_analysis_completed": bool(topview_result and "seams" in topview_stats),
        "meaningful_void_count": meaningful_voids,
        "obstacle_hypothesis_requirement_met": meaningful_voids == 0 or obstacle_hypotheses > 0,
        "conditioned_primary_exists_and_is_valid": bool(
            not bool(conditioning_cfg.get("enabled", True))
            or (conditioned_curves and conditioned_curves[0].status != "INVALID")
        ),
        "flattened_primary_exists_and_is_valid": bool(
            not bool(flattening_cfg.get("enabled", True))
            or (flattened_curves and flattened_curves[0].status != "INVALID")
        ),
        "flattened_test_outputs_complete": bool(
            not bool(flattening_cfg.get("enabled", True))
            or (
                flat_preview_metrics.get("written", False)
                and dxf_metrics.get("status") == "GOOD"
                and dxf_metrics.get("native", {}).get("total_entity_count", 0) > 0
            )
        ),
    })
    export_validation["successful_v02_run"] = bool(export_validation["successful_v01_run"] and all((
        export_validation["primary_outer_contour_exists"],
        export_validation["primary_outer_contour_closed"],
        export_validation["primary_raster_area_is_plausible"],
        export_validation["secondary_outer_contours_complete"],
        export_validation["default_visible_curve_count_is_reasonable"],
        export_validation["topview_feature_density_raster_exists"],
        export_validation["nonskid_hypothesis_layers_exist"],
        export_validation["seam_analysis_completed"],
        export_validation["obstacle_hypothesis_requirement_met"],
        export_validation["conditioned_primary_exists_and_is_valid"],
        export_validation["flattened_primary_exists_and_is_valid"],
        export_validation["flattened_test_outputs_complete"],
    )))
    primary_development = development_results.get(primary.candidate_id) if primary else None
    development_status = "NOT_EVALUATED" if not development_results else (
        "INVALID" if primary_development is None or primary_development.status == "INVALID" else primary_development.status
    )
    fit_status = "NOT_EVALUATED" if not manufacturing_curves else (
        "INVALID" if not any(curve.status != "INVALID" for curve in manufacturing_curves) else
        ("NEEDS_REVIEW" if any(curve.status != "GOOD" for curve in manufacturing_curves) else "GOOD")
    )
    dxf_status = str(dxf_metrics.get("status", "NOT_EVALUATED"))
    primary_cam_curve = next((
        curve for curve in manufacturing_curves
        if curve.source.source.raw_curve.layer == "AUTODECK::DECK_PRIMARY_OUTER"
    ), None)
    primary_polyarc_curve = next((
        polyarc for spline, polyarc in zip(manufacturing_curves, polyarc_curves)
        if primary_cam_curve is not None and spline.cam_curve_id == primary_cam_curve.cam_curve_id
    ), None)
    polyarc_status = "NOT_EVALUATED" if not polyarc_curves else (
        "INVALID" if primary_polyarc_curve is None or primary_polyarc_curve.status == "INVALID"
        else "NEEDS_REVIEW"
    )
    export_validation.update({
        "development_status": development_status,
        "manufacturing_fit_status": fit_status,
        "dxf_status": dxf_status,
        "development_patch_count": len(development_results),
        "manufacturing_curve_count": len(manufacturing_curves),
        "cam_backprojected_curve_count": len(backprojected_curves),
        "primary_cam_curve_id": None if primary_cam_curve is None else primary_cam_curve.cam_curve_id,
        "primary_cam_fit_status": "NOT_EVALUATED" if primary_cam_curve is None else primary_cam_curve.status,
        "development_preview_written": bool(development_preview_metrics.get("written", False)),
        "native_dxf_roundtrip_valid": bool(dxf_metrics.get("native", {}).get("valid", False)),
        "polyline_dxf_roundtrip_valid": bool(dxf_metrics.get("polyline", {}).get("valid", False)),
        "polyarc_fit_status": polyarc_status,
        "primary_polyarc_status": "NOT_EVALUATED" if primary_polyarc_curve is None else primary_polyarc_curve.status,
        "primary_polyarc_primitive_count": 0 if primary_polyarc_curve is None else primary_polyarc_curve.primitive_count,
        "primary_polyarc_line_count": 0 if primary_polyarc_curve is None else primary_polyarc_curve.line_count,
        "primary_polyarc_arc_count": 0 if primary_polyarc_curve is None else primary_polyarc_curve.arc_count,
        "primary_polyarc_max_deviation_mm": None if primary_polyarc_curve is None else primary_polyarc_curve.max_deviation_mm,
        "polyarc_dxf_status": v032_dxf_metrics.get("status", "NOT_EVALUATED"),
        "polyarc_backprojected_curve_count": len(polyarc_backprojected_curves),
        "robust_reference_curve_count": len(robust_references),
        "robust_reference_backprojected_curve_count": len(robust_backprojected_curves),
        "pattern_selected": pattern_result.selected,
        "pattern_entity_count": len(pattern_result.lines),
        "pattern_dxf_status": pattern_output_metrics.get("status", "NOT_APPLICABLE"),
    })
    base_v03_success = _v03_success_contract(
        bool(export_validation["successful_v02_run"]), development_status, fit_status,
        "NOT_EVALUATED" if primary_cam_curve is None else primary_cam_curve.status,
        dxf_status, bool(export_validation["development_preview_written"]),
        bool(export_validation["flattened_preview_3dm_written"]),
        int(export_validation["cam_backprojected_curve_count"]),
    )
    export_validation["primary_native_curve_contract"] = _v031_primary_curve_contract(primary_cam_curve)
    export_validation["successful_v031_run"] = bool(
        base_v03_success
        and export_validation["primary_native_curve_contract"]
        and int(flat_preview_metrics.get("native_smooth_object_count", 0)) > 0
    )
    export_validation["successful_v03_run"] = export_validation["successful_v031_run"]
    export_validation["successful_v032_run"] = bool(
        export_validation["successful_v031_run"]
        and primary_polyarc_curve is not None
        and primary_polyarc_curve.metrics.get("fit_reference") == "RAW_DEVELOPED_CONTOUR"
        and primary_polyarc_curve.status == "TEST_GEOMETRY"
        and primary_polyarc_curve.max_deviation_mm <= 3.0 + 1e-8
        and primary_polyarc_curve.metrics["forbidden_side_violations"] == 0
        and not primary_polyarc_curve.metrics["self_intersection"]
        and primary_polyarc_curve.metrics["closure_error_mm"] <= 1e-7
        and primary_polyarc_curve.metrics["maximum_smooth_tangent_mismatch_deg"]
            <= float(config["polyarc_fit"]["tangent_max_deg"]) + 1e-8
        and primary_polyarc_curve.metrics["line_arc_only"]
        and len(primary_polyarc_curve.hard_corner_indices) == len(primary_cam_curve.anchor_indices)
        and v032_dxf_metrics.get("status") == "GOOD"
        and int(flat_preview_metrics.get("polyarc_v032_object_count", 0)) > 0
        and len(polyarc_backprojected_curves) > 0
    )
    export_validation["successful_v033_run"] = bool(
        export_validation["successful_v031_run"]
        and primary_polyarc_curve is not None
        and primary_polyarc_curve.status == "TEST_GEOMETRY"
        and primary_polyarc_curve.metrics.get("fit_reference") in {
            "ROBUST_PHYSICAL_REFERENCE", "MANUAL_BROAD_PHYSICAL_REFERENCE"
        }
        and primary_polyarc_curve.max_deviation_mm
            <= float(config["manufacturing_fit"].get("reference_max_deviation_mm", 3.0)) + 1e-8
        and primary_polyarc_curve.metrics["forbidden_side_violations"] == 0
        and not primary_polyarc_curve.metrics["self_intersection"]
        and primary_polyarc_curve.metrics["closure_error_mm"] <= 1e-7
        and primary_polyarc_curve.metrics["maximum_smooth_tangent_mismatch_deg"]
            <= float(config["polyarc_fit"]["tangent_max_deg"]) + 1e-8
        and primary_polyarc_curve.metrics["line_arc_only"]
        and len(primary_polyarc_curve.hard_corner_indices) == len(primary_cam_curve.anchor_indices)
        and v032_dxf_metrics.get("status") == "GOOD"
        and int(flat_preview_metrics.get("polyarc_v033_object_count", 0)) > 0
        and len(robust_backprojected_curves) > 0
        and len(polyarc_backprojected_curves) > 0
        and pattern_output_metrics.get("status", "GOOD") == "GOOD"
    )

    overall_status = "SEGMENTATION_FAILURE" if not candidates else "EXPORT_FAILURE"
    proposal: PanelProposal | None = None
    if primary:
        needs_review = bool(primary.invalid_loops or not primary.outer_loops or primary.touches_open_mesh_boundary)
        panel_status = PanelStatus.NEEDS_REVIEW if needs_review else PanelStatus.PROPOSED
        proposal = PanelProposal(
            panel_status,
            primary.selected_faces,
            primary.outer_loops,
            primary.internal_boundaries,
            warnings=warnings,
        )
        required_success = (
            export_validation["successful_v033_run"]
            if bool(config.get("topview", {}).get("enabled", False)) and mode == "automatic" and selected_up_axis == "+Z"
            else export_validation["successful_v01_run"]
        )
        if required_success:
            overall_status = panel_status.value
        else:
            overall_status = "FEATURE_EXTRACTION_FAILURE" if export_validation["successful_v01_run"] else "EXPORT_FAILURE"
            warnings.append(
                "MANUFACTURING/EXPORT FAILURE: output did not satisfy the active V0.3 verification contract."
            )

    segmentation_payload = {
        "seed_mode": mode,
        "segmentation_mode": segmentation_mode,
        "up_axis": axis_summary,
        "panel_status": overall_status,
        "primary_candidate_id": primary.candidate_id if primary else None,
        "candidates": [_candidate_payload(candidate) for candidate in candidates],
        "approved_geometry": None,
        "seed_records": seed_records,
        "detected_markers": marker_records,
        "stage_component_diagnostics": stage_diagnostics,
        "topview": None if topview_result is None else topview_stats,
        "feature_artifacts": {} if topview_result is None else topview_result.artifact_paths,
        "features": [_feature_payload(curve) for curve in feature_curves],
        "curve_lineage": _curve_lineage_payloads(
            conditioned_curves, flattened_curves, manufacturing_curves, backprojected_curves
        ),
        "export_validation": export_validation,
        "warnings": warnings,
    }
    _save_candidate_masks(output_dir / "candidate_masks.npz", candidates, len(analysis.mesh_mm.faces))
    save_json(output_dir / "segmentation.json", segmentation_payload)
    save_json(output_dir / "curve_lineage.json", {
        "artifact": "development_curve_maps.npz",
        "records": segmentation_payload["curve_lineage"],
    })

    area_metrics = _orientation_area_metrics(fields.face_areas, orientation, config)
    metrics = {
        "autodeck_version": __version__,
        "python": platform.python_version(),
        "input": str(Path(input_path).resolve()),
        "input_sha256": stats["sha256"],
        "input_units": resolved_units,
        "mm_per_input_unit": mm_per_unit,
        "seed_mode": mode,
        "segmentation_mode": segmentation_mode,
        "up_axis": axis_summary,
        "config": config,
        "original_statistics": stats,
        "analysis_vertices": int(len(analysis.mesh_mm.vertices)),
        "analysis_triangles": int(len(analysis.mesh_mm.faces)),
        "preprocessing": analysis.preprocessing_metrics,
        "geometry": geometry_metrics(fields),
        "orientation": area_metrics | {
            "recovered_noise_face_count": int(np.count_nonzero(orientation.recovered_noise_faces)),
            "upward_face_count": int(np.count_nonzero(orientation.upward_facing)),
            "downward_face_count": int(np.count_nonzero(~orientation.upward_facing)),
        },
        "boundary": {
            "transition_count": int(len(boundary.refined_cost)),
            "suppressed_transition_count": int(np.count_nonzero(boundary.suppressed)),
            "raw_cost_p95": float(np.percentile(boundary.raw_cost, 95)) if len(boundary.raw_cost) else 0.0,
            "refined_cost_p95": float(np.percentile(boundary.refined_cost, 95)) if len(boundary.refined_cost) else 0.0,
        },
        "candidate_count": len(candidates),
        "segmentation_stages": stage_diagnostics,
        "export": export_validation,
        "topview": topview_stats,
        "curve_conditioning": {
            "curve_count": len(conditioned_curves),
            "status_counts": _status_counts([item.status for item in conditioned_curves]),
            "curves": [_conditioned_payload(item) for item in conditioned_curves],
        },
        "flattening": {
            "strategy": "V0.3 surface development; legacy V0.2 context retired",
            "context": _flatten_context_payload(flattening_context),
            "curve_count": len(flattened_curves),
            "status_counts": _status_counts([item.status for item in flattened_curves]),
            "dxf": dxf_metrics,
            "preview_3dm": flat_preview_metrics,
        },
        "development": {
            "status": development_status,
            "patch_count": len(development_results),
            "patches": {str(key): _development_payload(value) for key, value in development_results.items()},
            "preview_3dm": development_preview_metrics,
        },
        "manufacturing_fit": {
            "status": fit_status,
            "curve_count": len(manufacturing_curves),
            "status_counts": _status_counts([item.status for item in manufacturing_curves]),
            "curves": [_manufacturing_payload(item) for item in manufacturing_curves],
            "verification_cam_backprojection": verification_cam_metrics,
            "perimeter_length_audits": length_audits,
        },
        "polyarc_fit": {
            "status": polyarc_status,
            "curve_count": len(polyarc_curves),
            "status_counts": _status_counts([item.status for item in polyarc_curves]),
            "curves": [item.to_dict() for item in polyarc_curves],
            "dxf": v032_dxf_metrics,
            "verification_cam_backprojection": polyarc_verification_metrics,
            "landmark_dimension_audits": dimension_audits,
            "cnc_ready": False,
            "test_verification_geometry": True,
        },
        "robust_reference": {
            "status": "TEST_REFERENCE" if robust_references else "NOT_EVALUATED",
            "curve_count": len(robust_references),
            "curves": [item.to_dict() for item in robust_references],
            "verification_3d": robust_verification_metrics,
            "raw_geometry_preserved": True,
        },
        "pattern": pattern_result.to_dict(),
        "dxf": dxf_metrics,
        "stage_timings_seconds": stage_timings,
        "runtime_seconds": time.perf_counter() - started,
    }
    save_json(output_dir / "debug_metrics.json", metrics)
    _write_feature_summary(
        output_dir / "feature_summary.json", conditioned_curves, flattened_curves,
        _flatten_context_payload(flattening_context), dxf_metrics,
    )
    write_v031_reports(
        output_dir, development_results, flattened_curves, manufacturing_curves,
        dxf_metrics, flat_preview_metrics, warnings, length_audits, config,
    )
    write_polyarc_report(
        output_dir / "polyarc_report.md",
        polyarc_curves,
        manufacturing_curves,
        v032_dxf_metrics,
        flat_preview_metrics,
        dimension_audits,
        config,
    )
    write_v033_report(
        output_dir / "v033_manual_reconstruction_report.md",
        robust_references,
        polyarc_curves,
        pattern_result,
        v032_dxf_metrics,
        flat_preview_metrics,
    )
    _write_run_report(
        output_dir / "run_report.md", stats, analysis, config, mode, segmentation_mode,
        area_metrics, stage_diagnostics, candidates, warnings, export_validation,
        topview_stats, stage_timings, axis_summary, float(metrics["runtime_seconds"]), segmentation_failure_warning,
    )
    final_geometry_valid = bool(export_validation.get("successful_v033_run", False))
    final_failure_reasons: list[str] = []
    if primary_polyarc_curve is None:
        final_failure_reasons.append("No primary LINE/ARC CAM perimeter was produced.")
    elif not final_geometry_valid:
        polyarc_metrics = primary_polyarc_curve.metrics
        final_failure_reasons.extend([
            f"Maximum join gap: {float(polyarc_metrics.get('maximum_join_gap_mm', float('inf'))):.6f} mm.",
            "Maximum soft-join tangent mismatch: "
            f"{float(polyarc_metrics.get('maximum_smooth_tangent_mismatch_deg', float('inf'))):.6f} degrees.",
            f"Signed-safety violations: {int(polyarc_metrics.get('forbidden_side_violations', 0))}.",
            f"Maximum fit deviation: {float(polyarc_metrics.get('max_deviation_mm', float('inf'))):.6f} mm.",
        ])
    preferred_name = str(
        pattern_output_metrics.get("preferred_file", "flattened_curves_polyarc.dxf")
    )
    production_output = finalize_production_output(
        output_dir,
        output_dir / preferred_name,
        final_geometry_valid,
        final_failure_reasons,
    )
    export_validation["production_output"] = production_output
    metrics["production_output"] = production_output
    compact_artifacts = output_dir / PROCESSING_DIRECTORY
    save_json(compact_artifacts / "debug_metrics.json", metrics)
    save_json(compact_artifacts / "segmentation.json", segmentation_payload)
    _report("Done")
    return {
        "proposal": proposal,
        "candidates": candidates,
        "status": overall_status,
        "metrics": metrics,
        "warnings": warnings,
        "outputs": str(output_dir.resolve()),
    }


def _orientation_area_metrics(face_areas: np.ndarray, orientation, config: dict) -> dict[str, float]:
    slope = orientation.smoothed_slope_deg
    upward = orientation.upward_facing
    core = float(config["orientation"]["core_slope_deg"])
    maximum = float(config["orientation"]["max_slope_deg"])
    return {
        "total_surface_area_mm2": float(face_areas.sum()),
        "upward_surface_area_mm2": float(face_areas[upward].sum()),
        "area_at_or_below_core_slope_mm2": float(face_areas[upward & (slope <= core)].sum()),
        "area_between_core_and_max_slope_mm2": float(face_areas[upward & (slope > core) & (slope <= maximum)].sum()),
        "area_above_max_slope_mm2": float(face_areas[upward & (slope > maximum)].sum()),
        "downward_back_facing_area_mm2": float(face_areas[~upward].sum()),
    }


def _loop_payload(loop) -> dict[str, object]:
    return {
        "analysis_vertex_indices": loop.analysis_vertex_indices,
        "closed": loop.is_closed,
        "kind": loop.kind,
        "length_mm": loop.length_mm,
        "confidence": loop.confidence,
    }


def _candidate_payload(candidate: CandidateRegion) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "role": "PRIMARY_CANDIDATE" if candidate.is_primary else "OTHER_CANDIDATE",
        "status": candidate.confidence_label,
        "confidence": candidate.confidence,
        "area_mm2": candidate.area_mm2,
        "area_m2": candidate.area_mm2 / 1_000_000.0,
        "face_count": candidate.face_count,
        "average_slope_deg": candidate.mean_slope_deg,
        "p95_slope_deg": candidate.p95_slope_deg,
        "max_slope_deg": candidate.max_slope_deg,
        "core_area_fraction": candidate.core_area_fraction,
        "bbox_min_mm": candidate.bbox_min_mm.tolist(),
        "bbox_max_mm": candidate.bbox_max_mm.tolist(),
        "centroid_mm": candidate.centroid_mm.tolist(),
        "touches_open_mesh_boundary": candidate.touches_open_mesh_boundary,
        "open_boundary_edge_count": candidate.open_boundary_edge_count,
        "open_boundary_length_mm": candidate.open_boundary_length_mm,
        "selected_face_mask_artifact": "candidate_masks.npz",
        "outer_loops": [_loop_payload(loop) for loop in candidate.outer_loops],
        "selection_hole_count": len(candidate.internal_boundaries),
        "invalid_contour_count": len(candidate.invalid_loops),
        "warnings": candidate.warnings,
    }


def _feature_payload(curve: FeatureCurve) -> dict[str, object]:
    return {
        "layer": curve.layer,
        "name": curve.name,
        "closed": curve.closed,
        "confidence": curve.confidence,
        "point_count": int(len(curve.points_input)),
        "points_input_coordinates": curve.points_input.tolist(),
        "metrics": curve.metrics,
    }


def _conditioned_payload(item: ConditionedCurve) -> dict[str, object]:
    return {
        "raw_curve_id": item.raw_curve_id,
        "smoothed_curve_id": item.smoothed_curve_id,
        "feature_class": item.raw_curve.layer,
        "name": item.raw_curve.name,
        "closed": item.raw_curve.closed,
        "status": item.status,
        "metrics": item.metrics,
        "warnings": item.warnings,
    }


def _flattened_payload(item: FlattenedCurve) -> dict[str, object]:
    return {
        "raw_curve_id": item.source.raw_curve_id,
        "smoothed_curve_id": item.source.smoothed_curve_id,
        "flattened_curve_id": item.flattened_curve_id,
        "feature_class": item.source.raw_curve.layer,
        "dxf_layer": item.layer,
        "closed": item.closed,
        "status": item.status,
        "point_count": int(len(item.points_mm)),
        "metrics": item.metrics,
    }


def _development_payload(item: DevelopmentResult) -> dict[str, object]:
    return {
        "patch_id": item.patch_id,
        "strategy": item.strategy,
        "status": item.status,
        "topology": item.mesh.topology,
        "base_surface": item.mesh.base_surface_metrics,
        "planarity": item.planarity_metrics,
        "distortion": item.distortion_metrics,
        "warnings": item.warnings,
        "artifact": f"debug/development/development_patch_{item.patch_id:03d}.npz",
    }


def _manufacturing_payload(item: ManufacturingCurve) -> dict[str, object]:
    return {
        "flat_raw_id": item.source.flattened_curve_id,
        "cam_curve_id": item.cam_curve_id,
        "feature_class": item.source.source.raw_curve.layer,
        "layer": item.layer,
        "closed": item.closed,
        "status": item.status,
        "protected_corner_indices": item.anchor_indices,
        "protected_corner_points_mm": item.anchor_points_mm.tolist(),
        "metrics": item.metrics,
        "warnings": item.warnings,
        "spans": [
            {
                "span_index": index,
                "kind": span.kind,
                "source_start_index": span.source_start_index,
                "source_end_index": span.source_end_index,
                "sample_point_count": int(len(span.sampled_points_mm)),
                "control_point_count": int(len(span.control_points_mm)),
                "degree": span.degree,
                "center_mm": None if span.center_mm is None else span.center_mm.tolist(),
                "radius_mm": span.radius_mm,
                "start_angle_deg": span.start_angle_deg,
                "end_angle_deg": span.end_angle_deg,
                "clockwise": span.clockwise,
                "metrics": span.metrics,
            }
            for index, span in enumerate(item.spans, 1)
        ],
    }


def _curve_lineage_payloads(
    conditioned: list[ConditionedCurve],
    flattened: list[FlattenedCurve],
    manufacturing: list[ManufacturingCurve],
    backprojected: list[FeatureCurve],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for item in conditioned:
        flat = next((value for value in flattened if value.source is item), None)
        cam = next((value for value in manufacturing if flat is not None and value.source is flat), None)
        back = next((value for value in backprojected if cam is not None and value.metrics.get("cam_curve_id") == cam.cam_curve_id), None)
        records.append({
            "raw_3d_id": item.raw_curve_id,
            "conditioned_3d_id": item.smoothed_curve_id,
            "development_patch_id": None if flat is None else flat.metrics.get("development_patch_id"),
            "triangle_barycentric_artifact": None if flat is None else "development_curve_maps.npz",
            "flat_raw_id": None if flat is None else flat.flattened_curve_id,
            "cam_fit_id": None if cam is None else cam.cam_curve_id,
            "native_dxf": None if cam is None else "flattened_curves_native.dxf",
            "polyline_dxf": None if cam is None else "flattened_curves_polyline.dxf",
            "backprojected_3d_id": None if back is None else back.curve_id,
        })
    return records


def _flatten_context_payload(context) -> dict[str, object] | None:
    if context is None:
        return None
    return {
        "status": context.status,
        "origin_mm": context.origin_mm.tolist(),
        "u_axis": context.u_axis.tolist(),
        "v_axis": context.v_axis.tolist(),
        "normal": context.normal.tolist(),
        "plane_metrics": context.plane_metrics,
        "distortion_metrics": context.distortion_metrics,
        "warnings": context.warnings,
    }


def _status_counts(statuses: list[str]) -> dict[str, int]:
    return {name: statuses.count(name) for name in ("GOOD", "NEEDS_REVIEW", "INVALID")}


def _v03_success_contract(
    v02_success: bool,
    development_status: str,
    fit_status: str,
    primary_fit_status: str,
    dxf_status: str,
    development_preview_written: bool,
    flat_preview_written: bool,
    backprojected_curve_count: int,
) -> bool:
    return bool(
        v02_success
        and development_status in {"GOOD", "NEEDS_REVIEW"}
        and fit_status in {"GOOD", "NEEDS_REVIEW"}
        and primary_fit_status in {"GOOD", "NEEDS_REVIEW"}
        and dxf_status == "GOOD"
        and development_preview_written and flat_preview_written
        and backprojected_curve_count > 0
    )


def _v031_primary_curve_contract(primary: ManufacturingCurve | None) -> bool:
    if primary is None or primary.status == "INVALID" or not len(primary.points_mm):
        return False
    metrics = primary.metrics; counts = metrics.get("span_type_counts", {})
    fallback = int(counts.get("POLYLINE", 0))
    spline = int(counts.get("SPLINE", 0))
    analytic = int(counts.get("LINE", 0)) + int(counts.get("ARC", 0)) + int(counts.get("CIRCLE", 0))
    native_smooth_requirement = spline > 0 or (
        fallback == 0 and analytic == int(metrics.get("logical_physical_span_count", len(primary.spans)))
    )
    return bool(
        native_smooth_requirement
        and metrics.get("logical_path_closed", not primary.closed)
        and float(metrics.get("maximum_join_gap_mm", float("inf")))
        <= float(metrics.get("join_gap_tolerance_mm", 0.0))
        and int(metrics.get("forbidden_side_violation_count", 0)) == 0
        and not bool(metrics.get("self_intersection", True))
    )


def _feature_family(layer: str) -> str:
    upper = layer.upper()
    for name in ("DECK_PRIMARY", "DECK_SECONDARY", "OBSTACLE", "NONSKID", "SEAM", "HATCH"):
        if name in upper:
            return name.lower()
    return "other"


def _write_feature_summary(
    path: Path,
    conditioned: list[ConditionedCurve],
    flattened: list[FlattenedCurve],
    context: dict[str, object] | None,
    dxf_metrics: dict[str, Any],
) -> None:
    flat_by_smooth = {item.source.smoothed_curve_id: item for item in flattened}
    records: list[dict[str, object]] = []
    for item in conditioned:
        flat = flat_by_smooth.get(item.smoothed_curve_id)
        records.append({
            "family": _feature_family(item.raw_curve.layer),
            "name": item.raw_curve.name,
            "raw_curve_id": item.raw_curve_id,
            "smoothed_curve_id": item.smoothed_curve_id,
            "flattened_curve_id": None if flat is None else flat.flattened_curve_id,
            "conditioning_status": item.status,
            "flattening_status": None if flat is None else flat.status,
            "closed": item.raw_curve.closed,
            "raw_points": item.metrics["raw_point_count"],
            "resampled_points": item.metrics["resampled_point_count"],
            "smooth_points": item.metrics["smoothed_point_count"],
            "raw_length_mm": item.metrics["raw_length_mm"],
            "smooth_length_mm": item.metrics["smoothed_length_mm"],
            "xy_deviation_mm": item.metrics["xy_deviation_mm"],
            "z_deviation_mm": item.metrics["raw_to_smooth_z_deviation_mm"],
            "flat_length_mm": None if flat is None else float(np.linalg.norm(np.diff(flat.points_mm, axis=0), axis=1).sum()),
            "flat_length_change_percent": None if flat is None else flat.metrics.get("length_change_percent"),
            "orientation_preserved": None if flat is None else flat.metrics.get("orientation_preserved"),
        })
    save_json(path, {
        "label": "TEST / VERIFICATION GEOMETRY — NOT CNC READY",
        "curve_lineage": records,
        "family_counts": {
            family: sum(record["family"] == family for record in records)
            for family in sorted({str(record["family"]) for record in records})
        },
        "flattening_context": context,
        "dxf": dxf_metrics,
    })


def _write_manufacturing_report(
    path: Path,
    development: dict[int, DevelopmentResult],
    flat_raw: list[FlattenedCurve],
    manufacturing: list[ManufacturingCurve],
    dxf: dict[str, Any],
    development_preview: dict[str, Any],
    flat_preview: dict[str, Any],
    warnings: list[str],
) -> None:
    primary_development = development[min(development)] if development else None
    development_status = "NOT_EVALUATED" if primary_development is None else primary_development.status
    fit_status = "NOT_EVALUATED" if not manufacturing else (
        "INVALID" if not any(item.status != "INVALID" for item in manufacturing) else
        ("NEEDS_REVIEW" if any(item.status != "GOOD" for item in manufacturing) else "GOOD")
    )
    lines = [
        "# AutoDeck V0.3 surface development and manufacturing-curve report",
        "",
        "> TEST / VERIFICATION GEOMETRY ONLY — not CNC-ready. No manufacturing offset has been applied.",
        "",
        "## Independent statuses", "",
        f"- Surface development: **{development_status}**",
        f"- Non-primary invalid development patches: {sum(item.status == 'INVALID' for key, item in development.items() if primary_development is not None and key != primary_development.patch_id)}",
        f"- Manufacturing fit: **{fit_status}**",
        f"- DXF round-trip: **{dxf.get('status', 'NOT_EVALUATED')}**",
        f"- Flat raw curves / CAM curves: {len(flat_raw)} / {len(manufacturing)}",
        "", "## Development patches", "",
    ]
    if not development:
        lines.append("No development patch was produced.")
    for patch_id, result in development.items():
        distortion = result.distortion_metrics.get("absolute_edge_strain_percent", {})
        planarity = result.planarity_metrics.get("absolute_residual_mm", {})
        lines.extend([
            f"### Patch {patch_id}", "",
            f"- Strategy / status: `{result.strategy}` / **{result.status}**",
            f"- Vertices / triangles: {len(result.mesh.base_vertices_mm):,} / {len(result.mesh.faces):,}",
            f"- Boundary loops / estimated holes / open chains: {result.mesh.topology.get('closed_boundary_loop_count', 0)} / {result.mesh.topology.get('estimated_hole_count', 0)} / {result.mesh.topology.get('open_boundary_chain_count', 0)}",
            f"- Planarity RMS / P95: {float(planarity.get('rms', 0.0)):.4f} / {float(planarity.get('p95', 0.0)):.4f} mm",
            f"- Absolute edge strain P50 / P95 / P99 / max: {float(distortion.get('p50', 0.0)):.4f}% / {float(distortion.get('p95', 0.0)):.4f}% / {float(distortion.get('p99', 0.0)):.4f}% / {float(distortion.get('maximum', 0.0)):.4f}%",
            f"- Flipped/collapsed triangles: {int(result.distortion_metrics.get('flipped_or_collapsed_triangle_count', 0))}", "",
        ])
    lines.extend(["## Manufacturing curves", ""])
    if not manufacturing:
        lines.append("No CAM-fit curve was produced.")
    for curve in manufacturing:
        counts = curve.metrics.get("span_type_counts", {}); deviation = curve.metrics.get("deviation", {})
        lines.append(
            f"- `{curve.cam_curve_id}`: {curve.status}; corners={len(curve.anchor_indices)}, spans={len(curve.spans)}, "
            f"LINE/ARC/CIRCLE/SPLINE/POLYLINE={counts.get('LINE', 0)}/{counts.get('ARC', 0)}/{counts.get('CIRCLE', 0)}/{counts.get('SPLINE', 0)}/{counts.get('POLYLINE', 0)}, "
            f"max deviation={float(deviation.get('maximum_mm', 0.0)):.4f} mm, forbidden-side violations={curve.metrics.get('forbidden_side_violation_count', 0)}"
        )
    native = dxf.get("native", {}); polyline = dxf.get("polyline", {})
    lines.extend([
        "", "## DXF and Rhino verification", "",
        f"- `flattened_curves_native.dxf`: valid={native.get('valid', False)}, entities={native.get('total_entity_count', 0)}, types={native.get('entity_counts', {})}, max round-trip deviation={native.get('maximum_roundtrip_deviation_mm', 'n/a')} mm",
        f"- `flattened_curves_polyline.dxf`: valid={polyline.get('valid', False)}, entities={polyline.get('total_entity_count', 0)}, max round-trip deviation={polyline.get('maximum_roundtrip_deviation_mm', 'n/a')} mm",
        f"- `development_preview.3dm`: written={development_preview.get('written', False)}, objects={development_preview.get('object_count', 0)}",
        f"- `flattened_preview.3dm`: written={flat_preview.get('written', False)}, objects={flat_preview.get('object_count', 0)}",
        "", "## Manufacturing warnings", "",
    ])
    selected_warnings = [warning for warning in warnings if any(token in warning for token in ("DEVELOPMENT", "DXF", "ANCHOR", "MANUFACTURING"))]
    lines.extend(f"- {warning}" for warning in selected_warnings)
    if not selected_warnings:
        lines.append("- None")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_flatten_report(
    path: Path,
    conditioned: list[ConditionedCurve],
    flattened: list[FlattenedCurve],
    context: dict[str, object] | None,
    dxf_metrics: dict[str, Any],
    preview_metrics: dict[str, Any],
    stage_timings: dict[str, float],
    warnings: list[str],
) -> None:
    lines = [
        "# AutoDeck V0.3 compatibility flattening report",
        "",
        "> **TEST / VERIFICATION GEOMETRY — NOT CNC READY.** No manufacturing offsets were applied.",
        "",
        "## Strategy and coordinate frame",
        "",
        "- This legacy report helper is superseded by `manufacturing_report.md` and `development_report.md`.",
        "- V0.3 uses rigid local planar or pinned libigl LSCM→ARAP development per patch.",
    ]
    if context:
        plane = context["plane_metrics"]; distortion = context["distortion_metrics"]
        lines.extend([
            f"- Test status: **{context['status']}**",
            f"- Plane origin (mm): {context['origin_mm']}",
            f"- U axis: {context['u_axis']}",
            f"- V axis: {context['v_axis']}",
            f"- Plane normal: {context['normal']}",
            f"- Rotation from world XY: {float(plane['rotation_from_world_xy_deg']):.6f}°",
            f"- Right-handed determinant: {float(plane['right_handed_determinant']):.9f}",
            f"- U·world+X / V·world+Y: {float(plane['u_dot_world_x']):.9f} / {float(plane['v_dot_world_y']):.9f}",
            "",
            "## Planar fit quality",
            "",
            f"- Accepted interior samples: {int(plane['accepted_interior_sample_count']):,}",
            f"- Surface-to-plane |distance| (mm): `{json.dumps(plane['surface_to_plane_distance_mm'])}`",
            f"- Broad surface normal variation (deg): `{json.dumps(plane['broad_surface_normal_variation_deg'])}`",
            "",
            "## Measured flattening distortion",
            "",
            f"- Representative neighbor samples: {int(distortion['representative_neighbor_count']):,}",
            f"- Mean strain: {float(distortion['mean_strain_percent']):.6f}%",
            f"- RMS strain: {float(distortion['rms_strain_percent']):.6f}%",
            f"- P95 absolute strain: {float(distortion['p95_absolute_strain_percent']):.6f}%",
            f"- Maximum absolute strain: {float(distortion['maximum_absolute_strain_percent']):.6f}%",
        ])
    else:
        lines.extend(["- Status: **FAIL** — no valid flattening context was produced."])
    lines.extend([
        "",
        "## Curve conditioning",
        "",
        "The stages are arc-length parameterization, uniform resampling, persistent-corner detection, bounded XY smoothing, floor-side robust surface reconstruction, physical-distance Z filtering, topology validation, and bounded simplification.",
        f"- Conditioned curves: {len(conditioned)}; statuses: {_status_counts([item.status for item in conditioned])}",
        f"- Flattened curves: {len(flattened)}; statuses: {_status_counts([item.status for item in flattened])}",
        "",
        "| Feature | Raw → resampled → smooth points | XY RMS / P95 / max mm | Z RMS / P95 / max mm | 3D → 2D length mm | Status |",
        "|---|---:|---:|---:|---:|---|",
    ])
    flat_by_id = {item.source.smoothed_curve_id: item for item in flattened}
    for item in conditioned:
        flat = flat_by_id.get(item.smoothed_curve_id); xy = item.metrics["xy_deviation_mm"]; z = item.metrics["raw_to_smooth_z_deviation_mm"]
        length = "not flattened" if flat is None else f"{flat.metrics['smooth_3d_length_mm']:.3f} → {flat.metrics['flat_2d_length_mm']:.3f}"
        status = item.status if flat is None else f"{item.status}/{flat.status}"
        lines.append(
            f"| {item.raw_curve.name} | {item.metrics['raw_point_count']} → {item.metrics['resampled_point_count']} → {item.metrics['smoothed_point_count']} "
            f"| {xy['rms']:.3f} / {xy['p95']:.3f} / {xy['max']:.3f} "
            f"| {z['rms']:.3f} / {z['p95']:.3f} / {z['max']:.3f} | {length} | {status} |"
        )
    lines.extend([
        "",
        "## DXF and flattened Rhino preview",
        "",
        f"- `flattened_preview.3dm`: written={preview_metrics.get('written', False)}, curve objects={preview_metrics.get('curve_object_count', 0)}, all Z=0={preview_metrics.get('all_z_zero', False)}",
        f"- V0.3 native/polyline DXF status: {dxf_metrics.get('status', 'not written')}; units={dxf_metrics.get('units', 'not written')}, entities={dxf_metrics.get('total_entity_count', 0)}",
        f"- Entity counts: `{json.dumps(dxf_metrics.get('entity_counts', {}), sort_keys=True)}`",
        f"- Layer counts: `{json.dumps(dxf_metrics.get('layer_entity_counts', {}), sort_keys=True)}`",
        f"- DXF bounding box (mm): {dxf_metrics.get('bbox_mm')}",
        f"- Duplicate geometries skipped: {dxf_metrics.get('duplicate_geometry_skipped', 0)}",
        "- Calibration geometry is excluded from the DXF. The scale check is a separate file.",
        "",
        "## Runtime by stage",
        "",
    ])
    lines.extend(f"- {name}: {seconds:.3f} s" for name, seconds in stage_timings.items())
    relevant_warnings = [warning for warning in warnings if "FLATTEN" in warning.upper() or "CONDITION" in warning.upper() or "SMOOTH" in warning.upper()]
    lines.extend(["", "## Conditioning / flattening warnings", ""])
    lines.extend(f"- WARNING: {warning}" for warning in relevant_warnings)
    if not relevant_warnings:
        lines.append("- None")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _save_candidate_masks(path: Path, candidates: list[CandidateRegion], face_count: int) -> None:
    """Persist dense face membership without expanding millions of JSON integers."""
    packed_width = (face_count + 7) // 8
    packed = np.zeros((len(candidates), packed_width), dtype=np.uint8)
    for row, candidate in enumerate(candidates):
        values = np.packbits(candidate.selected_faces.astype(np.uint8), bitorder="little")
        packed[row, :len(values)] = values
    np.savez_compressed(
        path,
        candidate_ids=np.asarray([candidate.candidate_id for candidate in candidates], dtype=np.int32),
        selected_faces_packbits=packed,
        analysis_face_count=np.asarray([face_count], dtype=np.int64),
        bitorder=np.asarray(["little"]),
    )


def _write_run_report(
    path: Path,
    stats: dict[str, Any],
    analysis,
    config: dict[str, Any],
    mode: str,
    segmentation_mode: str,
    area_metrics: dict[str, float],
    stage_diagnostics: dict[str, dict[str, object]],
    candidates: list[CandidateRegion],
    warnings: list[str],
    export_validation: dict[str, object],
    topview_stats: dict[str, Any],
    stage_timings: dict[str, float],
    axis_summary: dict[str, Any],
    runtime: float,
    segmentation_failure_warning: str | None,
) -> None:
    orientation_cfg = config["orientation"]
    preprocessing = analysis.preprocessing_metrics
    report = [
        "# AutoDeck run report", "",
    ]
    if segmentation_failure_warning:
        report.extend([
            "## SEGMENTATION FAILURE", "",
            f"**{segmentation_failure_warning}**", "",
        ])
    report.extend([
        f"- Input: `{analysis.mesh_mm.source_path}`",
        f"- Input SHA-256: `{stats['sha256']}`",
        f"- Input units: {analysis.input_units}",
        f"- Seed mode: {mode}",
        f"- Segmentation mode: {segmentation_mode}",
        f"- Requested / selected up axis: {axis_summary['requested']} / {axis_summary['selected']}",
        f"- Sampled <=25° area fractions by axis: {axis_summary['sampled_orientation_area_fractions']}",
        f"- Original vertices / triangles: {stats['vertices']:,} / {stats['triangles']:,}",
        f"- Analysis vertices / triangles: {len(analysis.mesh_mm.vertices):,} / {len(analysis.mesh_mm.faces):,}",
        f"- Requested analysis resolution: {float(preprocessing['requested_analysis_resolution_mm']):.3f} mm",
        f"- Triangle reduction: {float(preprocessing['triangle_reduction_percent']):.3f}%",
        f"- Surface-area change: {float(preprocessing['surface_area_change_percent']):.6f}%",
        f"- Bounding-box minimum change (mm): {preprocessing['bbox_min_change_mm']}",
        f"- Bounding-box maximum change (mm): {preprocessing['bbox_max_change_mm']}",
        f"- Bounding-box extent change (mm): {preprocessing['bbox_extent_change_mm']}",
        f"- Preprocessing runtime: {float(preprocessing['preprocessing_runtime_seconds']):.3f} s",
        f"- Core slope threshold: {float(orientation_cfg['core_slope_deg']):.1f}° from horizontal",
        f"- Maximum slope threshold: {float(orientation_cfg['max_slope_deg']):.1f}° from horizontal",
        f"- Candidate regions found: {len(candidates)}",
        f"- Primary candidate ID: {candidates[0].candidate_id if candidates else None}",
        f"- Proposed OBJ curve / vertex count: {export_validation['proposed_boundary_curve_count']} / {export_validation['proposed_boundary_vertex_count']}",
        f"- Primary boundary point count: {export_validation['primary_boundary_point_count']}",
        f"- Native 3DM written: {'yes' if export_validation['verification_3dm_written'] else 'no'}",
        f"- Verification 3DM object count: {export_validation['verification_3dm_object_count']}",
        f"- Verification 3DM curve count: {export_validation['verification_3dm_curve_count']}",
        f"- Default visible / calibration / debug curves: {export_validation['default_visible_curve_count']} / {export_validation['calibration_curve_count']} / {export_validation['debug_curve_count']}",
        f"- Conditioned / flattened curves: {export_validation['conditioned_curve_count']} / {export_validation['flattened_curve_count']}",
        f"- Flattened preview 3DM written / curve count: {export_validation['flattened_preview_3dm_written']} / {export_validation['flattened_preview_3dm_curve_count']}",
        f"- Flattened TEST DXF written / entity count: {export_validation['flattened_dxf_written']} / {export_validation['flattened_dxf_entity_count']}",
        f"- Successful V0.1 run contract: {'yes' if export_validation['successful_v01_run'] else 'no'}",
        f"- Successful V0.2 run contract: {'yes' if export_validation['successful_v02_run'] else 'no'}",
        f"- Development / primary CAM fit / DXF status: {export_validation.get('development_status')} / {export_validation.get('primary_cam_fit_status')} / {export_validation.get('dxf_status')}",
        f"- Successful V0.3 run contract: {'yes' if export_validation.get('successful_v03_run') else 'no'}",
        f"- Runtime: {runtime:.3f} s", "",
        "## Detected/upward-oriented mesh statistics", "",
        f"- Total surface area: {area_metrics['total_surface_area_mm2']:.3f} mm²",
        f"- Upward-facing area: {area_metrics['upward_surface_area_mm2']:.3f} mm²",
        f"- Area ≤ core threshold: {area_metrics['area_at_or_below_core_slope_mm2']:.3f} mm²",
        f"- Area core–maximum: {area_metrics['area_between_core_and_max_slope_mm2']:.3f} mm²",
        f"- Area > maximum: {area_metrics['area_above_max_slope_mm2']:.3f} mm²",
        f"- Downward/back-facing area: {area_metrics['downward_back_facing_area_mm2']:.3f} mm²",
        "", "## Segmentation stage component diagnostics", "",
    ])
    for label, key in (
        ("Stage A — orientation mask only", "stage_a_orientation_only"),
        ("Stage B — orientation + conformability", "stage_b_orientation_plus_conformability"),
        ("Stage C — orientation + conformability + structural crossing", "stage_c_orientation_conformability_structural_crossing"),
    ):
        stage = stage_diagnostics[key]
        report.extend([
            f"### {label}", "",
            f"- Total eligible face count: {int(stage['eligible_face_count']):,}",
            f"- Total eligible surface area: {float(stage['eligible_surface_area_mm2']):.3f} mm²",
            f"- Connected component count: {int(stage['connected_component_count']):,}",
            f"- Largest component area: {float(stage['largest_component_area_mm2']):.3f} mm²",
            f"- Second-largest component area: {float(stage['second_largest_component_area_mm2']):.3f} mm²",
            f"- Top 10 component areas: {stage['top_10_component_areas_mm2']}",
            f"- Components ≥ 10,000 mm²: {int(stage['components_at_or_above_10000_mm2']):,}",
            f"- Largest-component face count: {int(stage['largest_component_face_count']):,}",
            "",
        ])
    report.extend(["## Candidate regions", ""])
    secondary_outer_by_id = {
        int(record["candidate_id"]): record
        for record in topview_stats.get("secondary_outer_curves", [])
    }
    if not candidates:
        report.append("No candidate exceeded the configured minimum physical area.")
    for candidate in candidates:
        boundary_length = sum(loop.length_mm for loop in candidate.outer_loops)
        if candidate.is_primary and topview_stats:
            boundary_length = float(topview_stats.get("primary_outer_length_mm", boundary_length))
        elif candidate.candidate_id in secondary_outer_by_id:
            boundary_length = float(secondary_outer_by_id[candidate.candidate_id]["length_mm"])
        title = f"Candidate {candidate.candidate_id:03d}"
        if candidate.is_primary:
            title += " — PRIMARY_CANDIDATE"
        report.extend([
            f"### {title}", "",
            f"- Area: {candidate.area_mm2:.3f} mm² ({candidate.area_mm2 / 1_000_000.0:.6f} m²)",
            f"- Face count: {candidate.face_count}",
            f"- Average slope: {candidate.mean_slope_deg:.3f}°",
            f"- 95th-percentile slope: {candidate.p95_slope_deg:.3f}°",
            f"- Maximum slope: {candidate.max_slope_deg:.3f}°",
            f"- Outer boundary length: {boundary_length:.3f} mm",
            f"- Internal boundary loops: {len(candidate.internal_boundaries)}",
            f"- Bounding box (mm): {candidate.bbox_min_mm.tolist()} to {candidate.bbox_max_mm.tolist()}",
            f"- Centroid (mm): {candidate.centroid_mm.tolist()}",
            f"- Confidence: {candidate.confidence:.3f} ({candidate.confidence_label})",
            f"- Touches open mesh-cut boundary: {'yes' if candidate.touches_open_mesh_boundary else 'no'}", "",
        ])
    report.extend(["## V0.3 top-view feature evidence", ""])
    if not topview_stats:
        report.append("Top-view feature extraction was not run.")
    else:
        nonskid = topview_stats["nonskid"]; seams = topview_stats["seams"]; obstacles = topview_stats["obstacles"]
        report.extend([
            f"- Pixel resolution: {float(topview_stats['pixel_resolution_mm']):.3f} mm/pixel",
            f"- Grid dimensions: {int(topview_stats['grid_width_pixels']):,} × {int(topview_stats['grid_height_pixels']):,} pixels",
            f"- Physical extent: {topview_stats['physical_extent_mm']} mm",
            f"- Primary occupancy area: {float(topview_stats['primary_occupancy_area_mm2']):.3f} mm²",
            f"- Secondary overlay occupancy area: {float(topview_stats['secondary_occupancy_area_mm2']):.3f} mm²",
            f"- Occupancy / accepted surface area ratio: {float(topview_stats['primary_occupancy_to_surface_area_ratio']):.6f}",
            f"- Raster-derived outer projected area: {float(topview_stats['primary_outer_projected_area_mm2']):.3f} mm²",
            f"- Raster-derived outer length: {float(topview_stats['primary_outer_length_mm']):.3f} mm",
            f"- Raw strong-edge length accumulated: {float(topview_stats['raw_strong_edge_length_mm']):.3f} mm",
            "", "### Nonskid hypotheses", "",
            f"- Detection status / normal-visible regions: {nonskid.get('detection_status')} / {int(nonskid.get('normal_visible_region_count', 0))}",
            f"- Loose: {int(nonskid['loose']['region_count'])} regions / {float(nonskid['loose']['total_area_mm2']):.3f} mm²",
            f"- Medium: {int(nonskid['medium']['region_count'])} regions / {float(nonskid['medium']['total_area_mm2']):.3f} mm²",
            f"- Strict: {int(nonskid['strict']['region_count'])} regions / {float(nonskid['strict']['total_area_mm2']):.3f} mm²",
            "", "### Seams", "",
            f"- Detection status: {seams.get('detection_status')}",
            f"- High-confidence components: {int(seams['high_confidence_count'])}",
            f"- Low-confidence components: {int(seams['low_confidence_count'])}",
            f"- Hatch/closed-seam candidates: {int(seams['hatch_or_closed_seam_count'])}",
            f"- Total high-confidence seam length: {float(seams['total_high_confidence_length_mm']):.3f} mm",
            "", "### Obstacles", "",
            f"- Raw enclosed voids: {int(obstacles['raw_enclosed_void_count']):,}",
            f"- Meaningful voids: {int(obstacles['meaningful_void_count'])}",
            f"- Loose / medium / strict: {int(obstacles['loose_count'])} / {int(obstacles['medium_count'])} / {int(obstacles['strict_count'])}",
            f"- Nested groups / primary footprints: {int(obstacles['nested_group_count'])} / {int(obstacles['primary_footprint_count'])}",
            f"- Micro-void rejections retained as metrics: {int(obstacles['micro_void_rejection_count'])}",
            "", "### Feature distributions", "",
            f"```json\n{json.dumps({key: topview_stats[key] for key in ('feature_density_distributions_per_mm', 'height_residual_activity_distribution_mm', 'normal_variation_distribution_deg')}, indent=2)}\n```",
        ])
    report.extend(["", "## Runtime by stage", ""])
    report.extend(f"- {name}: {seconds:.3f} s" for name, seconds in stage_timings.items())
    report.extend([
        "## Parameters", "", f"```json\n{json.dumps(config, indent=2, sort_keys=True)}\n```",
        "", "## Warnings", "",
    ])
    report.extend(f"- WARNING: {warning}" for warning in warnings)
    if not warnings:
        report.append("- None")
    report.extend([
        "", "## Human verification", "",
        "All geometry is **PROPOSED**, not approved manufacturing geometry. Overlay the true/display curves on the unchanged prepared scan in Rhino and approve, edit, or reject them.",
    ])
    path.write_text("\n".join(report) + "\n", encoding="utf-8")
