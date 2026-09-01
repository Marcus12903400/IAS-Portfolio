from __future__ import annotations

"""Fast V0.3 manufacturing reprocessing from saved development artifacts."""

import json
from pathlib import Path
from typing import Any

import numpy as np

from . import __version__
from .analysis_pipeline import _development_payload, _manufacturing_payload, _status_counts, _v031_primary_curve_contract
from .curve_fit import ManufacturingCurve, fit_manufacturing_curves
from .development import DevelopmentMesh, DevelopmentResult, backproject_flat_points
from .dxf_export import write_manufacturing_dxfs, write_v032_dxfs
from .mesh_io import save_json
from .models import ConditionedCurve, FeatureCurve, FlattenedCurve
from .polyarc import fit_polyarc_curves, polyarc_curve_from_dict
from .patterns import generate_pattern, write_pattern_outputs, write_pattern_report
from .production_output import PROCESSING_DIRECTORY, finalize_production_output, processing_directory
from .robust_reference import fit_robust_reference_curves
from .v03_export import append_backprojected_to_verification, export_development_preview, export_flattened_preview
from .v031_reports import make_length_audit, write_v031_reports
from .v032_reports import make_landmark_dimension_audit, write_polyarc_report
from .v033_reports import write_v033_report


def _cached_manufacturing_curves(
    metrics: dict[str, Any],
    detail: dict[str, Any],
) -> list[ManufacturingCurve]:
    flat_records = {
        str(item["flattened_curve_id"]): item
        for item in detail.get("flat_raw_curves", [])
    }
    result: list[ManufacturingCurve] = []
    for record in metrics.get("manufacturing_fit", {}).get("curves", []):
        flat_id = str(record.get("flat_raw_id", ""))
        flat_record = flat_records.get(flat_id, {})
        feature_class = str(record.get("feature_class", "AUTODECK::OTHER"))
        closed = bool(record.get("closed", True))
        raw = FeatureCurve(feature_class, flat_id, np.empty((0, 3)), closed, 1.0)
        smooth = FeatureCurve(feature_class, flat_id + "-smooth", np.empty((0, 3)), closed, 1.0)
        conditioned = ConditionedCurve(raw, flat_id + "-raw", smooth, flat_id + "-smooth", np.empty((0, 3)), [], "GOOD")
        flat = FlattenedCurve(
            conditioned,
            flat_id,
            np.empty((0, 3)),
            str(record.get("layer", "FLAT_RAW_OTHER")),
            closed,
            "GOOD",
            dict(flat_record.get("metrics", {})),
        )
        result.append(ManufacturingCurve(
            source=flat,
            cam_curve_id=str(record.get("cam_curve_id", "cached-cam")),
            layer=str(record.get("layer", "FLAT_RAW_OTHER")),
            closed=closed,
            anchor_indices=[int(value) for value in record.get("protected_corner_indices", [])],
            anchor_points_mm=np.asarray(record.get("protected_corner_points_mm", []), dtype=float).reshape((-1, 3)),
            spans=[],
            points_mm=np.empty((0, 3)),
            status=str(record.get("status", "NEEDS_REVIEW")),
            metrics=dict(record.get("metrics", {})),
            warnings=list(record.get("warnings", [])),
        ))
    return result


def rerun_pattern(output_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Regenerate only pattern artifacts from serialized V0.3.3 CAM primitives."""

    run_directory = Path(output_dir)
    output_dir = processing_directory(run_directory)
    metrics_path = output_dir / "debug_metrics.json"
    detail_path = output_dir / "debug" / "development" / "development_metrics.json"
    if not metrics_path.exists() or not detail_path.exists():
        raise ValueError("cached run metrics and development metadata are required")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    polyarcs = [
        polyarc_curve_from_dict(item)
        for item in metrics.get("polyarc_fit", {}).get("curves", [])
    ]
    manufacturing = _cached_manufacturing_curves(metrics, detail)
    if not polyarcs or len(polyarcs) != len(manufacturing):
        raise ValueError("cached V0.3.3 CAM primitives are missing or inconsistent")
    pattern = generate_pattern(polyarcs, manufacturing, config)
    outputs = write_pattern_outputs(output_dir, pattern, polyarcs, manufacturing)
    write_pattern_report(output_dir / "pattern_report.md", pattern)
    metrics["autodeck_version"] = __version__
    metrics["pattern"] = pattern.to_dict()
    metrics.setdefault("export", {}).update({
        "pattern_selected": pattern.selected,
        "pattern_entity_count": len(pattern.lines),
        "pattern_dxf_status": outputs.get("status", "NOT_APPLICABLE"),
        "preferred_file": outputs.get("preferred_file", "flattened_curves_polyarc.dxf"),
    })
    detail["pattern"] = pattern.to_dict()
    primary = next((curve for spline, curve in zip(manufacturing, polyarcs) if "DECK_PRIMARY" in spline.source.source.raw_curve.layer.upper()), polyarcs[0])
    pattern_valid = outputs.get("status", "GOOD") == "GOOD"
    final_valid = bool(primary.status != "INVALID" and pattern_valid)
    production = finalize_production_output(
        run_directory,
        output_dir / str(outputs.get("preferred_file", "flattened_curves_polyarc.dxf")),
        final_valid,
        [] if final_valid else ["The cached CAM perimeter or selected pattern did not pass validation."],
    )
    metrics.setdefault("export", {})["production_output"] = production
    metrics["production_output"] = production
    compact_artifacts = processing_directory(run_directory)
    save_json(compact_artifacts / "debug_metrics.json", metrics)
    (compact_artifacts / "debug" / "development").mkdir(parents=True, exist_ok=True)
    save_json(
        compact_artifacts / "debug" / "development" / "development_metrics.json",
        detail,
    )
    return {
        "status": "GOOD" if outputs.get("status", "GOOD") == "GOOD" else "INVALID",
        "cached_pattern_only_rerun": True,
        "scan_reprocessed": False,
        "development_reprocessed": False,
        "primary_polyarc_summary": primary.to_dict(),
        "pattern": pattern.to_dict(),
        "preferred_file": "final.dxf" if production.get("final_dxf_written") else production.get("failure_notice"),
        "production_output": production,
    }


def _load_development(output_dir: Path, metrics: dict[str, Any]) -> dict[int, DevelopmentResult]:
    results: dict[int, DevelopmentResult] = {}
    for key, payload in metrics["development"]["patches"].items():
        patch_id = int(key)
        artifact = output_dir / str(payload["artifact"])
        with np.load(artifact) as arrays:
            mesh = DevelopmentMesh(
                patch_id,
                arrays["vertices_mm"].astype(np.float64),
                arrays["base_vertices_mm"].astype(np.float64),
                arrays["faces"].astype(np.int64),
                arrays["raster_rows"].astype(np.int64),
                arrays["raster_cols"].astype(np.int64),
                payload["topology"],
                payload["base_surface"],
            )
            results[patch_id] = DevelopmentResult(
                patch_id, payload["strategy"], mesh, arrays["uv_mm"].astype(np.float64),
                payload["status"], payload["distortion"], payload["planarity"], list(payload["warnings"]),
            )
    return results


def _reconstruct_flat_curves(
    output_dir: Path,
    records: list[dict[str, Any]],
    development: dict[int, DevelopmentResult],
) -> tuple[list[FlattenedCurve], dict[str, np.ndarray]]:
    archive_path = output_dir / "development_curve_maps.npz"
    with np.load(archive_path) as arrays:
        archive = {key: arrays[key].copy() for key in arrays.files}
    curves: list[FlattenedCurve] = []
    for index, record in enumerate(records, 1):
        key = f"curve_{index:04d}"
        triangle_key = f"{key}_triangle_ids"; barycentric_key = f"{key}_barycentric"
        if triangle_key not in archive or barycentric_key not in archive:
            continue
        patch_id = int(record["metrics"]["development_patch_id"])
        result = development[patch_id]
        triangle_ids = archive[triangle_key].astype(np.int64)
        barycentric = archive[barycentric_key].astype(np.float64)
        flat_points_key = f"{key}_flat_points_mm"
        if flat_points_key in archive:
            points = archive[flat_points_key].astype(np.float64)
        else:
            flat_xy = np.einsum("ni,nij->nj", barycentric, result.uv_mm[result.mesh.faces[triangle_ids]])
            points = np.column_stack((flat_xy, np.zeros(len(flat_xy))))
            archive[flat_points_key] = points.astype(np.float64)
        raw = FeatureCurve(record["feature_class"], record["flattened_curve_id"], np.empty((0, 3)), bool(record["closed"]), 1.0)
        smooth = FeatureCurve(record["feature_class"], record["flattened_curve_id"] + "_conditioned", np.empty((0, 3)), bool(record["closed"]), 1.0)
        conditioned = ConditionedCurve(
            raw, record["raw_curve_id"], smooth, record["smoothed_curve_id"],
            np.empty((0, 3)), [], "GOOD",
        )
        curve_metrics = dict(record["metrics"])
        curve_metrics["curve_archive_key"] = key
        curves.append(FlattenedCurve(
            conditioned, record["flattened_curve_id"], points, record["dxf_layer"],
            bool(record["closed"]), record["status"], curve_metrics,
        ))
    return curves, archive


def reprocess_manufacturing(output_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    output_dir = Path(output_dir)
    metrics_path = output_dir / "debug_metrics.json"
    development_metrics_path = output_dir / "debug" / "development" / "development_metrics.json"
    if not metrics_path.exists() or not development_metrics_path.exists():
        raise ValueError("Saved V0.3 development artifacts are required for manufacturing reprocessing")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["autodeck_version"] = __version__
    detail = json.loads(development_metrics_path.read_text(encoding="utf-8"))
    development = _load_development(output_dir, metrics)
    flat_raw, archive = _reconstruct_flat_curves(output_dir, detail["flat_raw_curves"], development)
    manufacturing = fit_manufacturing_curves(flat_raw, config)
    robust_references = fit_robust_reference_curves(manufacturing, config.get("robust_reference", {}))
    polyarc_settings = dict(config["polyarc_fit"])
    polyarc_settings["absolute_fit_tolerance_mm"] = float(
        config["manufacturing_fit"].get("reference_max_deviation_mm", 3.0)
    )
    polyarcs = fit_polyarc_curves(manufacturing, polyarc_settings, robust_references)
    polyarc_by_cam = {
        spline.cam_curve_id: polyarc for spline, polyarc in zip(manufacturing, polyarcs)
    }
    conditioning_by_raw_id = {
        str(record.get("raw_curve_id")): record
        for record in metrics.get("curve_conditioning", {}).get("curves", [])
    }
    length_audits: list[dict[str, Any]] = []
    dimension_audits: list[dict[str, Any]] = []
    for curve in manufacturing:
        key = str(curve.source.metrics.get("curve_archive_key", ""))
        triangle_key = f"{key}_triangle_ids"; barycentric_key = f"{key}_barycentric"
        if triangle_key not in archive or barycentric_key not in archive:
            continue
        patch_id = int(curve.source.metrics["development_patch_id"])
        result = development[patch_id]
        triangle_ids = archive[triangle_key].astype(np.int64)
        barycentric = archive[barycentric_key].astype(np.float64)
        mapped_source = np.einsum(
            "ni,nij->nj", barycentric, result.mesh.vertices_mm[result.mesh.faces[triangle_ids]],
        )
        mapped_base = np.einsum(
            "ni,nij->nj", barycentric, result.mesh.base_vertices_mm[result.mesh.faces[triangle_ids]],
        )
        conditioned_record = conditioning_by_raw_id.get(str(curve.source.metrics.get("raw_curve_id")), {})
        conditioned_length = float(conditioned_record.get("metrics", {}).get(
            "smoothed_length_mm", np.linalg.norm(np.diff(mapped_source, axis=0), axis=1).sum(),
        ))
        audit = make_length_audit(
            curve.cam_curve_id, conditioned_length, mapped_source, mapped_base,
            curve.source.points_mm, curve.points_mm,
        )
        curve.metrics["perimeter_length_audit"] = audit
        length_audits.append(audit)
        polyarc = polyarc_by_cam.get(curve.cam_curve_id)
        if polyarc is not None and len(polyarc.hard_corner_indices):
            dimension_audits.append(make_landmark_dimension_audit(
                curve.cam_curve_id,
                polyarc.hard_corner_indices,
                mapped_base,
                curve.source.points_mm,
                polyarc,
            ))
    dxf = write_manufacturing_dxfs(output_dir, manufacturing, config)
    v032_dxf = write_v032_dxfs(output_dir, polyarcs, manufacturing, config)
    development_preview = export_development_preview(output_dir / "development_preview.3dm", development, flat_raw)
    flat_preview = export_flattened_preview(
        output_dir / "flattened_preview.3dm", flat_raw, manufacturing, polyarcs,
        robust_references,
    )
    mm_per_input_unit = float(metrics["mm_per_input_unit"])
    maximum_distance = float(config["development"]["maximum_curve_mapping_distance_mm"])
    backprojected: list[FeatureCurve] = []
    for index, curve in enumerate(manufacturing, 1):
        patch_id = int(curve.source.metrics["development_patch_id"])
        if curve.status == "INVALID":
            continue
        points3d, mapping = backproject_flat_points(curve.points_mm, development[patch_id], maximum_distance)
        backprojected.append(FeatureCurve(
            "AUTODECK::CAM_BACKPROJECTED_3D", f"cam_backprojected_{index:04d}",
            points3d / mm_per_input_unit, curve.closed, 1.0,
            {"cam_curve_id": curve.cam_curve_id, "development_patch_id": patch_id},
            f"backprojected-{index:04d}-{curve.cam_curve_id}",
        ))
        key = f"cam_{index:04d}"
        archive[f"{key}_triangle_ids"] = mapping.source_triangle_ids.astype(np.int32)
        archive[f"{key}_barycentric"] = mapping.barycentric_coordinates.astype(np.float64)
    np.savez_compressed(output_dir / "development_curve_maps.npz", **archive)
    verification = append_backprojected_to_verification(output_dir / "verification.3dm", backprojected)
    robust_backprojected: list[FeatureCurve] = []
    for index, (spline, reference) in enumerate(zip(manufacturing, robust_references), 1):
        patch_id = int(spline.source.metrics["development_patch_id"])
        flat_points = np.column_stack((reference.points[:, :2], np.zeros(len(reference.points))))
        points3d, mapping = backproject_flat_points(flat_points, development[patch_id], maximum_distance)
        robust_backprojected.append(FeatureCurve(
            "ROBUST_REFERENCE_3D",
            f"robust_reference_{index:04d}",
            points3d / mm_per_input_unit,
            reference.closed,
            1.0,
            {"cam_curve_id": spline.cam_curve_id, "development_patch_id": patch_id},
            f"robust-reference-{index:04d}-{spline.cam_curve_id}",
        ))
        archive[f"robust_{index:04d}_flat_points_mm"] = flat_points.astype(np.float64)
        archive[f"robust_{index:04d}_triangle_ids"] = mapping.source_triangle_ids.astype(np.int32)
        archive[f"robust_{index:04d}_barycentric"] = mapping.barycentric_coordinates.astype(np.float64)
    robust_verification = append_backprojected_to_verification(
        output_dir / "verification.3dm",
        robust_backprojected,
        "ROBUST_REFERENCE_3D",
        "AUTODECK_DEBUG::ROBUST_REFERENCE_DENSE_SAMPLES",
    )
    polyarc_backprojected: list[FeatureCurve] = []
    for index, (spline, polyarc) in enumerate(zip(manufacturing, polyarcs), 1):
        if polyarc.status == "INVALID" or not len(polyarc.sampled_points):
            continue
        patch_id = int(spline.source.metrics["development_patch_id"])
        flat_points = np.column_stack((polyarc.sampled_points[:, :2], np.zeros(len(polyarc.sampled_points))))
        points3d, mapping = backproject_flat_points(flat_points, development[patch_id], maximum_distance)
        polyarc_backprojected.append(FeatureCurve(
            "AUTODECK::CAM_POLYARC_BACKPROJECTED_3D",
            f"polyarc_backprojected_{index:04d}",
            points3d / mm_per_input_unit,
            polyarc.is_closed,
            1.0,
            {"cam_curve_id": spline.cam_curve_id, "development_patch_id": patch_id},
            f"polyarc-backprojected-{index:04d}",
        ))
        key = f"polyarc_{index:04d}"
        archive[f"{key}_triangle_ids"] = mapping.source_triangle_ids.astype(np.int32)
        archive[f"{key}_barycentric"] = mapping.barycentric_coordinates.astype(np.float64)
    np.savez_compressed(output_dir / "development_curve_maps.npz", **archive)
    polyarc_verification = append_backprojected_to_verification(
        output_dir / "verification.3dm",
        polyarc_backprojected,
        "CAM_BACKPROJECTED_3D",
        "AUTODECK_DEBUG::CAM_V033_BACKPROJECTED_DENSE_SAMPLES",
    )
    pattern = generate_pattern(polyarcs, manufacturing, config, development)
    pattern_outputs = write_pattern_outputs(output_dir, pattern, polyarcs, manufacturing)
    write_pattern_report(output_dir / "pattern_report.md", pattern)

    fit_status = "INVALID" if not any(item.status != "INVALID" for item in manufacturing) else (
        "NEEDS_REVIEW" if any(item.status != "GOOD" for item in manufacturing) else "GOOD"
    )
    primary = next((item for item in manufacturing if item.source.source.raw_curve.layer == "AUTODECK::DECK_PRIMARY_OUTER"), None)
    metrics["manufacturing_fit"] = {
        "status": fit_status, "curve_count": len(manufacturing),
        "status_counts": _status_counts([item.status for item in manufacturing]),
        "curves": [_manufacturing_payload(item) for item in manufacturing],
        "verification_cam_backprojection": verification,
        "perimeter_length_audits": length_audits,
    }
    metrics["dxf"] = dxf
    primary_polyarc = (
        polyarc_by_cam.get(primary.cam_curve_id) if primary is not None else None
    )
    polyarc_status = (
        "INVALID"
        if primary_polyarc is None or primary_polyarc.status == "INVALID"
        else "NEEDS_REVIEW"
    )
    metrics["polyarc_fit"] = {
        "status": polyarc_status,
        "curve_count": len(polyarcs),
        "status_counts": _status_counts([item.status for item in polyarcs]),
        "curves": [item.to_dict() for item in polyarcs],
        "dxf": v032_dxf,
        "verification_cam_backprojection": polyarc_verification,
        "landmark_dimension_audits": dimension_audits,
        "cnc_ready": False,
        "test_verification_geometry": True,
    }
    metrics["robust_reference"] = {
        "status": "TEST_REFERENCE",
        "curve_count": len(robust_references),
        "curves": [item.to_dict() for item in robust_references],
        "verification_3d": robust_verification,
        "raw_geometry_preserved": True,
    }
    metrics["pattern"] = pattern.to_dict()
    metrics["flattening"].update({
        "curve_count": len(flat_raw), "status_counts": _status_counts([item.status for item in flat_raw]),
        "dxf": dxf, "preview_3dm": flat_preview,
    })
    export = metrics["export"]
    export.update({
        "manufacturing_fit_status": fit_status,
        "manufacturing_curve_count": len(manufacturing),
        "primary_cam_curve_id": None if primary is None else primary.cam_curve_id,
        "primary_cam_fit_status": "NOT_EVALUATED" if primary is None else primary.status,
        "cam_backprojected_curve_count": len(backprojected),
        "flattened_dxf_entity_count": int(dxf.get("native", {}).get("total_entity_count", 0)),
        "dxf_status": dxf.get("status", "INVALID"),
        "native_dxf_roundtrip_valid": bool(dxf.get("native", {}).get("valid", False)),
        "polyline_dxf_roundtrip_valid": bool(dxf.get("polyline", {}).get("valid", False)),
        "flattened_preview_3dm_written": bool(flat_preview.get("written", False)),
        "flattened_preview_3dm_curve_count": int(flat_preview.get("raw_curve_count", 0) + flat_preview.get("cam_curve_count", 0)),
        "verification_3dm_object_count": int(verification.get("object_count", export.get("verification_3dm_object_count", 0))),
        "verification_3dm_curve_count": int(verification.get("object_count", export.get("verification_3dm_curve_count", 0))),
    })
    export["primary_native_curve_contract"] = _v031_primary_curve_contract(primary)
    export["successful_v031_run"] = bool(
        export.get("successful_v02_run")
        and primary is not None and primary.status in {"GOOD", "NEEDS_REVIEW"}
        and export["primary_native_curve_contract"]
        and fit_status in {"GOOD", "NEEDS_REVIEW"}
        and dxf.get("status") == "GOOD"
        and int(flat_preview.get("native_smooth_object_count", 0)) > 0
        and len(backprojected) > 0
    )
    export["successful_v03_run"] = export["successful_v031_run"]
    export["primary_polyarc_status"] = "NOT_EVALUATED" if primary_polyarc is None else primary_polyarc.status
    export["primary_polyarc_primitive_count"] = 0 if primary_polyarc is None else primary_polyarc.primitive_count
    export["primary_polyarc_line_count"] = 0 if primary_polyarc is None else primary_polyarc.line_count
    export["primary_polyarc_arc_count"] = 0 if primary_polyarc is None else primary_polyarc.arc_count
    export["primary_polyarc_max_deviation_mm"] = None if primary_polyarc is None else primary_polyarc.max_deviation_mm
    export["polyarc_dxf_status"] = v032_dxf.get("status", "INVALID")
    export["successful_v032_run"] = bool(
        export["successful_v031_run"]
        and primary_polyarc is not None
        and primary_polyarc.metrics.get("fit_reference") == "RAW_DEVELOPED_CONTOUR"
        and primary_polyarc.status == "TEST_GEOMETRY"
        and primary_polyarc.max_deviation_mm <= 3.0 + 1e-8
        and primary_polyarc.metrics["forbidden_side_violations"] == 0
        and not primary_polyarc.metrics["self_intersection"]
        and primary_polyarc.metrics["closure_error_mm"] <= 1e-7
        and primary_polyarc.metrics["maximum_smooth_tangent_mismatch_deg"] <= float(config["polyarc_fit"]["tangent_max_deg"]) + 1e-8
        and primary_polyarc.metrics["line_arc_only"]
        and len(primary_polyarc.hard_corner_indices) == len(primary.anchor_indices)
        and v032_dxf.get("status") == "GOOD"
        and int(flat_preview.get("polyarc_v032_object_count", 0)) > 0
        and len(polyarc_backprojected) > 0
    )
    export["successful_v033_run"] = bool(
        export["successful_v031_run"]
        and primary_polyarc is not None
        and primary_polyarc.status == "TEST_GEOMETRY"
        and primary_polyarc.metrics.get("fit_reference") in {
            "ROBUST_PHYSICAL_REFERENCE", "MANUAL_BROAD_PHYSICAL_REFERENCE"
        }
        and primary_polyarc.max_deviation_mm
            <= float(config["manufacturing_fit"].get("reference_max_deviation_mm", 3.0)) + 1e-8
        and primary_polyarc.metrics["forbidden_side_violations"] == 0
        and not primary_polyarc.metrics["self_intersection"]
        and v032_dxf.get("status") == "GOOD"
        and pattern_outputs.get("status", "GOOD") == "GOOD"
    )
    save_json(metrics_path, metrics)
    detail.update({
        "manufacturing_curves": [_manufacturing_payload(item) for item in manufacturing],
        "dxf": dxf, "development_preview_3dm": development_preview,
        "flattened_preview_3dm": flat_preview,
        "verification_cam_backprojection": verification,
        "perimeter_length_audits": length_audits,
        "polyarc_curves": [item.to_dict() for item in polyarcs],
        "polyarc_dxf": v032_dxf,
        "polyarc_verification_cam_backprojection": polyarc_verification,
        "landmark_dimension_audits": dimension_audits,
        "robust_reference_curves": [item.to_dict() for item in robust_references],
        "robust_reference_verification": robust_verification,
        "pattern": pattern.to_dict(),
    })
    save_json(development_metrics_path, detail)
    lineage_path = output_dir / "curve_lineage.json"
    if lineage_path.exists():
        lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
        back_by_cam = {str(item.metrics["cam_curve_id"]): item.curve_id for item in backprojected}
        for record in lineage.get("records", []):
            cam = next((item for item in manufacturing if item.source.flattened_curve_id == record.get("flat_raw_id")), None)
            record["cam_fit_id"] = None if cam is None else cam.cam_curve_id
            record["backprojected_3d_id"] = None if cam is None else back_by_cam.get(cam.cam_curve_id)
        save_json(lineage_path, lineage)
        segmentation_path = output_dir / "segmentation.json"
        if segmentation_path.exists():
            segmentation = json.loads(segmentation_path.read_text(encoding="utf-8"))
            segmentation["curve_lineage"] = lineage.get("records", [])
            segmentation["export_validation"] = export
            save_json(segmentation_path, segmentation)
    write_v031_reports(
        output_dir, development, flat_raw, manufacturing, dxf, flat_preview,
        [], length_audits, config,
    )
    write_polyarc_report(
        output_dir / "polyarc_report.md",
        polyarcs,
        manufacturing,
        v032_dxf,
        flat_preview,
        dimension_audits,
        config,
    )
    write_v033_report(
        output_dir / "v033_manual_reconstruction_report.md",
        robust_references,
        polyarcs,
        pattern,
        v032_dxf,
        flat_preview,
    )
    # Re-fitting CAM geometry must re-decide final.dxf exactly like a fresh
    # run does; otherwise a corrected fit leaves a stale FINAL_NOT_READY (or a
    # stale final.dxf) at the run root.
    run_directory = output_dir.parent if output_dir.name == PROCESSING_DIRECTORY else output_dir
    final_valid = bool(export["successful_v033_run"])
    failure_reasons: list[str] = []
    if primary_polyarc is None:
        failure_reasons.append("No primary LINE/ARC CAM perimeter was produced.")
    elif not final_valid:
        pm = primary_polyarc.metrics
        failure_reasons.extend([
            f"Maximum join gap: {float(pm.get('maximum_join_gap_mm', float('inf'))):.6f} mm.",
            f"Maximum soft-join tangent mismatch: {float(pm.get('maximum_smooth_tangent_mismatch_deg', float('inf'))):.6f} degrees.",
            f"Signed-safety violations: {int(pm.get('forbidden_side_violations', 0))}.",
            f"Maximum fit deviation: {float(pm.get('max_deviation_mm', float('inf'))):.6f} mm.",
            f"Primary polyarc status: {primary_polyarc.status}.",
        ])
    production = finalize_production_output(
        run_directory,
        output_dir / str(pattern_outputs.get("preferred_file", "flattened_curves_polyarc.dxf")),
        final_valid,
        failure_reasons,
    )
    metrics["production_output"] = production
    export["production_output"] = production
    save_json(processing_directory(run_directory) / "debug_metrics.json", metrics)
    return {
        "status": "INVALID" if polyarc_status == "INVALID" else "NEEDS_REVIEW",
        "production_output": production,
        "primary_cam_fit_status": export["primary_cam_fit_status"],
        "successful_v03_run": export["successful_v03_run"],
        "successful_v031_run": export["successful_v031_run"],
        "successful_v032_run": export["successful_v032_run"],
        "successful_v033_run": export["successful_v033_run"],
        "manufacturing_curve_count": len(manufacturing),
        "native_dxf_entity_count": dxf.get("native", {}).get("total_entity_count", 0),
        "primary_polyarc_status": export["primary_polyarc_status"],
        "primary_polyarc_primitive_count": export["primary_polyarc_primitive_count"],
        "primary_polyarc_line_count": export["primary_polyarc_line_count"],
        "primary_polyarc_arc_count": export["primary_polyarc_arc_count"],
        "primary_polyarc_max_deviation_mm": export["primary_polyarc_max_deviation_mm"],
        "polyarc_dxf_entity_count": v032_dxf.get("polyarc", {}).get("total_entity_count", 0),
        "primary_polyarc_summary": None if primary_polyarc is None else primary_polyarc.to_dict(),
        "pattern": pattern.to_dict(),
        "preferred_file": pattern_outputs.get("preferred_file", "flattened_curves_polyarc.dxf"),
    }
