from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import __version__
from .boundaries import extract_boundary_loops
from .config import unit_scale_to_mm
from .curvature import calculate_geometry_fields
from .debug import geometry_metrics, save_boundary_npz, save_boundary_summary, save_colored_ply
from .markers import detect_aruco_seeds
from .mesh_io import load_obj, mesh_statistics, save_json
from .models import PanelProposal, PanelStatus
from .preprocessing import build_adjacency, preprocess
from .relief import calculate_boundary_field, temporary_overlay_face_mask
from .rhino_export import export_3dm_optional, export_obj_curves
from .segmentation import seed_faces_from_points, segment


def inspect_scan(input_path: Path, output_dir: Path, units: str | None = None) -> dict[str, Any]:
    started = time.perf_counter(); output_dir.mkdir(parents=True, exist_ok=True)
    mesh = load_obj(input_path); stats = mesh_statistics(mesh)
    resolved_units = units or mesh.metadata.get("detected_units")
    warnings: list[str] = []
    if resolved_units is None:
        warnings.append("OBJ units are unknown; pass --units before analysis")
    else:
        scale = unit_scale_to_mm(str(resolved_units))
        stats["bbox_extent_mm"] = (np.asarray(stats["bbox_extent_input_units"]) * scale).tolist()
        stats["surface_area_mm2"] = float(stats["surface_area_input_units2"]) * scale * scale
    stats["resolved_units"] = resolved_units
    stats["warnings"] = warnings
    stats["runtime_seconds"] = time.perf_counter() - started
    save_json(output_dir / "scan_inspection.json", stats)
    report = [
        "# Scan inspection", "", f"- Input: `{Path(input_path).resolve()}`",
        f"- SHA-256: `{stats['sha256']}`", f"- Vertices: {stats['vertices']:,}",
        f"- Triangles: {stats['triangles']:,}", f"- Resolved units: {resolved_units or 'UNKNOWN'}",
        f"- Bounding-box extent (input units): {stats['bbox_extent_input_units']}",
        f"- UV corner coverage: {100.0 * float(stats['uv_corner_coverage']):.2f}%",
        f"- Boundary edges: {stats['boundary_edges']:,}", f"- Non-manifold edges: {stats['nonmanifold_edges']:,}",
        "", "## Textures", "", f"```json\n{json.dumps(stats['texture_files'], indent=2)}\n```",
        "", "## Warnings", "",
    ]
    report.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        report.append("- None")
    (output_dir / "SCAN_INSPECTION.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return stats


def analyze_scan_seeded_legacy(
    input_path: Path,
    output_dir: Path,
    config: dict[str, Any],
    units: str | None,
    seed_points_input: list[np.ndarray] | None = None,
    paper_centers_input: list[np.ndarray] | None = None,
    enable_marker_detection: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter(); output_dir.mkdir(parents=True, exist_ok=True)
    original = load_obj(input_path); stats = mesh_statistics(original)
    resolved_units = units or original.metadata.get("detected_units")
    if resolved_units is None:
        raise ValueError("OBJ units are unknown. Pass --units mm|cm|m|in; AutoDeck will not guess physical scale.")
    mm_per_unit = unit_scale_to_mm(str(resolved_units))
    analysis, warnings = preprocess(original, str(resolved_units), config)
    adjacency = build_adjacency(analysis.mesh_mm)
    fields = calculate_geometry_fields(analysis.mesh_mm, adjacency, config)

    seed_points_input = list(seed_points_input or [])
    paper_centers_input = list(paper_centers_input or [])
    marker_records: list[dict[str, object]] = []
    if enable_marker_detection:
        detections, marker_warnings = detect_aruco_seeds(original, str(config["markers"]["dictionary"]))
        warnings.extend(marker_warnings)
        for detection in detections:
            seed_points_input.append(detection.point_input)
            paper_centers_input.append(detection.point_input)
            marker_records.append({
                "id": detection.marker_id, "role": "DECK_SEED", "point_input": detection.point_input.tolist(),
                "confidence": detection.confidence, "source_texture": detection.source_texture,
            })
    if not seed_points_input:
        raise ValueError("No deck seed was supplied or detected. Use --seed X Y Z in the scan's coordinate system.")
    seed_points_mm = [np.asarray(point, dtype=float) * mm_per_unit for point in seed_points_input]
    paper_centers_mm = [np.asarray(point, dtype=float) * mm_per_unit for point in paper_centers_input]
    seed_faces, seed_records = seed_faces_from_points(
        fields.face_centroids, seed_points_mm, float(config["markers"]["seed_radius_mm"])
    )
    boundary, boundary_warnings = calculate_boundary_field(
        analysis.mesh_mm, adjacency, fields, config, paper_centers_mm
    )
    warnings.extend(boundary_warnings)
    overlay_faces = temporary_overlay_face_mask(
        fields.face_centroids, paper_centers_mm, float(config["markers"]["paper_suppression_radius_mm"])
    )
    selected = segment(adjacency, fields, boundary, seed_faces, config, overlay_faces)
    selected_area = float(fields.face_areas[selected].sum())
    if selected_area < float(config["segmentation"]["minimum_region_area_mm2"]):
        warnings.append(f"Selected region area {selected_area:.3f} mm² is below configured minimum")
    loops, loop_warnings = extract_boundary_loops(analysis.mesh_mm, adjacency, boundary, selected, config)
    warnings.extend(loop_warnings)
    outer = [loop for loop in loops if loop.kind == "outer"]
    exclusions = [loop for loop in loops if loop.kind == "internal_exclusion"]
    invalid = [loop for loop in loops if loop.kind == "invalid"]
    material_warning = any("overlaps strong structural evidence" in warning for warning in warnings)
    status = PanelStatus.NEEDS_REVIEW if invalid or not outer or material_warning else PanelStatus.PROPOSED
    proposal = PanelProposal(status, selected, outer, exclusions, warnings=warnings)

    lift_mm = float(config["verification"]["display_lift_mm"])
    export_obj_curves(output_dir / "proposed_boundaries.obj", loops, analysis, original, fields, lift_mm)
    wrote_3dm, export_warning = export_3dm_optional(
        output_dir / "verification.3dm", loops, analysis, original, fields, lift_mm,
        seed_points_input, paper_centers_input,
    )
    if export_warning:
        warnings.append(export_warning)
    save_boundary_npz(output_dir / "boundary_scores.npz", adjacency, boundary)
    save_boundary_summary(output_dir / "boundary_scores.json", adjacency, boundary)
    save_colored_ply(output_dir / "analysis_mesh.ply", analysis.mesh_mm, adjacency, boundary, selected)

    segmentation_payload = {
        "panel_status": proposal.status.value,
        "proposed_geometry": {
            "selected_face_indices_analysis": np.flatnonzero(selected).tolist(),
            "outer_loops": [_loop_payload(loop) for loop in outer],
            "internal_exclusions": [_loop_payload(loop) for loop in exclusions],
            "invalid_loops": [_loop_payload(loop) for loop in invalid],
        },
        "approved_geometry": None,
        "seed_records": seed_records,
        "detected_markers": marker_records,
        "warnings": warnings,
    }
    save_json(output_dir / "segmentation.json", segmentation_payload)
    metrics = {
        "autodeck_version": __version__, "python": platform.python_version(),
        "input": str(Path(input_path).resolve()), "input_sha256": stats["sha256"],
        "input_units": resolved_units, "mm_per_input_unit": mm_per_unit,
        "config": config, "original_statistics": stats,
        "analysis_vertices": int(len(analysis.mesh_mm.vertices)),
        "analysis_triangles": int(len(analysis.mesh_mm.faces)),
        "geometry": geometry_metrics(fields),
        "boundary": {
            "transition_count": int(len(boundary.refined_cost)),
            "suppressed_transition_count": int(np.count_nonzero(boundary.suppressed)),
            "temporary_overlay_face_count": int(np.count_nonzero(overlay_faces)),
            "raw_cost_p95": float(np.percentile(boundary.raw_cost, 95)) if len(boundary.raw_cost) else 0.0,
            "refined_cost_p95": float(np.percentile(boundary.refined_cost, 95)) if len(boundary.refined_cost) else 0.0,
        },
        "runtime_seconds": time.perf_counter() - started,
    }
    save_json(output_dir / "debug_metrics.json", metrics)
    _write_run_report(
        output_dir / "run_report.md", stats, analysis, config, seed_records, marker_records,
        selected_area, loops, warnings, wrote_3dm, metrics["runtime_seconds"],
    )
    return {"proposal": proposal, "metrics": metrics, "outputs": str(output_dir.resolve())}


def _loop_payload(loop) -> dict[str, object]:
    return {
        "analysis_vertex_indices": loop.analysis_vertex_indices,
        "closed": loop.is_closed, "kind": loop.kind,
        "length_mm": loop.length_mm, "confidence": loop.confidence,
    }


def _write_run_report(
    path: Path, stats: dict[str, Any], analysis, config: dict[str, Any], seed_records: list[dict[str, object]],
    marker_records: list[dict[str, object]], selected_area: float, loops: list, warnings: list[str],
    wrote_3dm: bool, runtime: float,
) -> None:
    boundary_length = sum(loop.length_mm for loop in loops if loop.kind == "outer")
    report = [
        "# AutoDeck run report", "", f"- Input: `{analysis.mesh_mm.source_path}`",
        f"- Input SHA-256: `{stats['sha256']}`", f"- Input units: {analysis.input_units}",
        f"- Original vertices / triangles: {stats['vertices']:,} / {stats['triangles']:,}",
        f"- Analysis vertices / triangles: {len(analysis.mesh_mm.vertices):,} / {len(analysis.mesh_mm.faces):,}",
        f"- Marker seeds detected: {len(marker_records)}", f"- Seed neighborhoods: {len(seed_records)}",
        f"- Selected region area: {selected_area:.3f} mm²",
        f"- Outer / internal / invalid loops: {sum(l.kind == 'outer' for l in loops)} / {sum(l.kind == 'internal_exclusion' for l in loops)} / {sum(l.kind == 'invalid' for l in loops)}",
        f"- Proposed outer boundary length: {boundary_length:.3f} mm",
        f"- Native 3DM written: {'yes' if wrote_3dm else 'no'}",
        f"- Runtime: {float(runtime):.3f} s", "", "## Parameters", "",
        f"```json\n{json.dumps(config, indent=2, sort_keys=True)}\n```", "", "## Low-confidence areas / warnings", "",
    ]
    report.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        report.append("- None")
    report.extend(["", "## Human verification", "", "This geometry is **PROPOSED**, not approved manufacturing geometry. Overlay the true/display curves on the unchanged textured scan in Rhino and approve, edit, or reject them."])
    path.write_text("\n".join(report) + "\n", encoding="utf-8")


# Public analysis entry point: automatic orientation discovery by default.
from .analysis_pipeline import analyze_scan  # noqa: E402
