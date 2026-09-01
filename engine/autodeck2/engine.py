"""Engine: scan -> candidates -> top-view outline -> development -> mapped curves.

This is v2's own driver over v1 stage functions (through v1compat), mirroring
analysis_pipeline.py 86-322 and 404-441 and stopping before any CAM fitting.
It produces, per panel, the developed mesh and, per detected curve, both the
world-mm points and the flat points in that panel's uv frame:

  RAW     directly mapped original detected boundary (never conditioned)
  OBSTACLE / FEATURE  raw obstacle / seam / hatch / nonskid contours
  HINT    lower-confidence families v1 discards
  ROBUST  v1's conditioned (de-noised) curve, kept separately as a guide

Everything is cached under cache/<key>/ with a strong identity (see cache.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import cache as cache_module
from . import v1compat
from .assign import Assignment, GridInfo, PanelGeometry, assign_curve
from .config import config_hash, deep_merge
from .progress import Progress, StageTimer, silent

ENGINE_CONFIG_SECTIONS = ("engine", "assignment")


class EngineError(RuntimeError):
    pass


@dataclass
class MappedCurve:
    curve_id: str
    family: str                 # OUTER | OBSTACLE | FEATURE | HINT | ROBUST
    source_layer: str
    name: str
    closed: bool
    confidence: float
    world_points_mm: np.ndarray  # (N, 3)
    flat_points_mm: np.ndarray   # (N, 2) in the panel's uv frame
    panel_id: int | None
    assignment: dict[str, Any] = field(default_factory=dict)
    mapping_status: str = "GOOD"
    mapping_distance_max_mm: float = 0.0
    mapping_distance_p95_mm: float = 0.0
    raw_curve_id: str | None = None  # for ROBUST: the RAW curve it de-noises

    @property
    def assigned(self) -> bool:
        return self.panel_id is not None

    def metadata(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id, "family": self.family, "source_layer": self.source_layer,
            "name": self.name, "closed": self.closed, "confidence": float(self.confidence),
            "panel_id": self.panel_id, "assignment": self.assignment, "mapping_status": self.mapping_status,
            "mapping_distance_max_mm": float(self.mapping_distance_max_mm),
            "mapping_distance_p95_mm": float(self.mapping_distance_p95_mm),
            "raw_curve_id": self.raw_curve_id, "point_count": int(len(self.world_points_mm)),
        }


@dataclass
class Panel:
    panel_id: int
    role: str
    area_mm2: float
    world_centroid_mm: np.ndarray
    world_bbox_min_mm: np.ndarray
    world_bbox_max_mm: np.ndarray
    development: v1compat.DevelopmentResult
    strategy: str
    status: str
    warnings: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id, "role": self.role, "area_mm2": float(self.area_mm2),
            "world_centroid_mm": self.world_centroid_mm.astype(float).tolist(),
            "world_bbox_min_mm": self.world_bbox_min_mm.astype(float).tolist(),
            "world_bbox_max_mm": self.world_bbox_max_mm.astype(float).tolist(),
            "strategy": self.strategy, "status": self.status, "warnings": list(self.warnings),
            "artifact": f"development_patch_{self.panel_id:03d}.npz",
        }


@dataclass
class EngineResult:
    input_path: str
    input_sha256: str
    units: str
    mm_per_unit: float
    panels: dict[int, Panel]
    curves: list[MappedCurve]
    warnings: list[str]
    stage_timings: dict[str, float]
    diagnostics: dict[str, Any]
    cache_key: str
    cache_hit: bool

    @property
    def primary_id(self) -> int:
        return next((pid for pid, p in self.panels.items() if p.role == "primary"), min(self.panels))

    def curves_for(self, panel_id: int | None, family: str | None = None) -> list[MappedCurve]:
        return [c for c in self.curves if c.panel_id == panel_id and (family is None or c.family == family)]

    def unassigned(self) -> list[MappedCurve]:
        return [c for c in self.curves if not c.assigned]


# ---------------------------------------------------------------------------

def flatten_quality(panel: Panel) -> dict[str, Any]:
    """How faithfully this panel was flattened: true development (or best-fit
    plane for a genuinely flat panel), never a plain projection to the world
    XY plane.  Tilt is the angle of the panel's best-fit plane against
    horizontal; the strain numbers say how much edge lengths / area changed
    between the 3D surface and the flat drawing (0 = perfectly true)."""

    mesh = panel.development.mesh
    uv = np.asarray(panel.development.uv_mm, dtype=float)
    vertices = np.asarray(mesh.base_vertices_mm, dtype=float)
    out: dict[str, Any] = {"strategy": panel.strategy, "status": panel.status}
    if len(vertices) >= 3:
        centered = vertices - vertices.mean(axis=0)
        _values, vecs = np.linalg.eigh(centered.T @ centered / len(centered))
        normal = vecs[:, 0]
        out["tilt_deg"] = float(np.degrees(np.arccos(np.clip(abs(float(normal[2])), 0.0, 1.0))))
    if len(uv) == len(vertices) and len(mesh.faces):
        d = v1compat.development_distortion(vertices, mesh.faces, uv[:, :2], 1.0)
        out["area_change_percent"] = float(d.get("area_change_percent", 0.0))
        out["edge_strain_p95_percent"] = float(d.get("absolute_edge_strain_percent", {}).get("p95", 0.0))
        out["edge_strain_max_percent"] = float(d.get("absolute_edge_strain_percent", {}).get("maximum", 0.0))
    return out


def _family_for_layer(layer: str, config: dict[str, Any]) -> str | None:
    settings = config["engine"]
    if any(layer.startswith(prefix) for prefix in settings["hint_layer_prefixes"]):
        return "HINT"
    if any(layer.startswith(prefix) for prefix in settings["obstacle_layer_prefixes"]):
        return "OBSTACLE"
    if any(layer.startswith(prefix) for prefix in settings["feature_layer_prefixes"]):
        return "FEATURE"
    return None


def merged_v1_config(v2_config: dict[str, Any]) -> dict[str, Any]:
    """v1's config with any v2 `v1_overrides` section merged on top."""

    base = v1compat.load_v1_config()
    overrides = v2_config.get("v1_overrides") or {}
    return deep_merge(base, overrides) if overrides else base


def engine_identity(input_path: Path, v2_config: dict[str, Any], v1_config: dict[str, Any]) -> cache_module.CacheIdentity:
    combined_hash = config_hash({
        "v2": {key: v2_config.get(key) for key in ENGINE_CONFIG_SECTIONS},
        "v1": v1_config,
    })
    return cache_module.make_identity(
        cache_module.sha256_file(input_path), combined_hash, v1compat.v1_version(), v1compat.v1_source_fingerprint(),
    )


def run_engine(
    input_path: Path,
    v2_config: dict[str, Any],
    units: str | None = None,
    progress: Progress | None = None,
    cache_root: Path | None = None,
    debug_dir: Path | None = None,
) -> EngineResult:
    input_path = Path(input_path)
    if not input_path.is_file():
        raise EngineError(f"INPUT MISSING: {input_path}")
    progress = progress or silent
    v1_config = merged_v1_config(v2_config)
    identity = engine_identity(input_path, v2_config, v1_config)
    hit = cache_module.lookup(identity, cache_root)
    if hit is not None:
        progress(f"Cache hit ({identity.key}); loading developed panels")
        return load_engine_result(hit, cache_key=identity.key, cache_hit=True)
    progress(f"Cache miss ({identity.key}); running full analysis")
    result = _compute(input_path, v2_config, v1_config, units, progress, debug_dir or (cache_module.entry_dir(identity, cache_root) / "debug"))
    result.input_sha256 = identity.input_sha256
    result.cache_key = identity.key
    result.cache_hit = False
    entry = cache_module.begin_entry(identity, cache_root)
    save_engine_result(result, entry)
    cache_module.complete_entry(entry)
    return result


def _compute(
    input_path: Path,
    v2_config: dict[str, Any],
    config: dict[str, Any],
    units: str | None,
    progress: Progress,
    debug_dir: Path,
) -> EngineResult:
    timer = StageTimer(progress)
    warnings: list[str] = []
    debug_dir = Path(debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)

    timer.start("load_obj", "Loading OBJ mesh")
    original = v1compat.load_obj(input_path)
    stats = v1compat.mesh_statistics(original)
    resolved_units = units or original.metadata.get("detected_units")
    if resolved_units is None:
        resolved_units = str(config["mesh"]["default_input_units"])
        warnings.append(f"OBJ declares no physical units; using configured default '{resolved_units}'.")
    mm_per_unit = v1compat.unit_scale_to_mm(str(resolved_units))

    timer.start("up_axis", "Checking up axis")
    requested_up = str(v2_config["engine"].get("up_axis", "+Z"))
    scores = v1compat.assess_up_axes(original, float(config["orientation"]["max_slope_deg"]),
                                     int(config.get("up_axis", {}).get("sample_face_count", 200000)))
    selected_up, up_vector = v1compat.resolve_up_axis(requested_up, scores)
    best_axis = max(scores, key=scores.get)
    if requested_up != "auto" and scores[best_axis] > 0 and scores[selected_up] < float(
        config.get("up_axis", {}).get("implausible_area_ratio", 0.20)) * scores[best_axis]:
        warnings.append(
            f"UP AXIS LOOKS WRONG: {selected_up} yields only {100 * scores[selected_up]:.2f}% near-level area; "
            f"{best_axis} yields {100 * scores[best_axis]:.2f}%. Re-check the Rhino export orientation."
        )
    if selected_up != "+Z":
        raise EngineError(f"UP AXIS {selected_up}: the top-view outline stage requires +Z up. Re-orient the scan in Rhino.")

    timer.start("preprocess", "Preprocessing mesh (repair, resolution)")
    analysis, pre_warnings = v1compat.preprocess(original, str(resolved_units), config, up_vector)
    warnings.extend(pre_warnings)
    adjacency = v1compat.build_adjacency(analysis.mesh_mm)

    timer.start("geometry_fields", "Computing geometry fields")
    fields = v1compat.calculate_geometry_fields(analysis.mesh_mm, adjacency, config)

    timer.start("boundary_field", "Computing wall/floor boundary evidence")
    boundary, boundary_warnings = v1compat.calculate_boundary_field(analysis.mesh_mm, adjacency, fields, config)
    warnings.extend(boundary_warnings)

    timer.start("orientation", "Computing orientation field and segmentation components")
    orientation = v1compat.calculate_orientation_field(adjacency, fields, boundary, config, up_vector)
    _diagnostics, components = v1compat.segmentation_components(orientation, fields, adjacency, boundary, config)

    timer.start("candidates", "Discovering deckable-surface candidates")
    candidates, candidate_warnings = v1compat.discover_candidates(
        analysis.mesh_mm, adjacency, fields, boundary, orientation, config, components,
    )
    warnings.extend(candidate_warnings)
    if not candidates:
        raise EngineError("SEGMENTATION FAILURE: no deck candidate survived area filtering. "
                          "Check that the deck is oriented to XY with +Z up and that units are millimetres.")
    primary = candidates[0]

    timer.start("topview", "Tracing deck outlines and features (top view)")
    if not bool(config.get("topview", {}).get("enabled", True)):
        raise EngineError("v1 config has topview.enabled=false; the outline stage needs it on.")
    topview = v1compat.build_topview_features(debug_dir, original, analysis, adjacency, fields, boundary, primary, config, candidates)
    warnings.extend(topview.warnings)
    surface = topview.accepted_surface
    if surface is None or topview.primary_outer_curve is None:
        raise EngineError("TOP-VIEW FAILURE: no accepted deck surface / primary outline was produced.")
    ratio = float(topview.statistics.get("primary_occupancy_to_surface_area_ratio", 0.0))
    if not 0.50 <= ratio <= 1.50:
        warnings.append(f"Top-view primary occupancy ratio {ratio:.3f} is not physically consistent with the candidate area.")

    timer.start("development", "Developing (unrolling) each panel")
    panels: dict[int, Panel] = {}
    candidate_by_id = {c.candidate_id: c for c in candidates}
    for pid in sorted(surface.patch_masks):
        result = v1compat.develop_patch(surface, pid, config)
        if result.status == "INVALID":
            warnings.append(f"panel {pid}: intrinsic development failed ({'; '.join(result.warnings)}); retrying rigid-planar")
            result = v1compat.develop_patch_rigid_planar(surface, pid, config)
        if result.status == "INVALID" or not len(result.uv_mm):
            warnings.append(f"panel {pid}: development INVALID; panel skipped")
            continue
        base_xy = result.mesh.base_vertices_mm[:, :2]
        candidate = candidate_by_id.get(pid)
        panels[pid] = Panel(
            panel_id=pid,
            role="primary" if pid == primary.candidate_id else "secondary",
            area_mm2=float(candidate.area_mm2) if candidate else float(np.count_nonzero(surface.patch_masks[pid])) * surface.resolution_mm ** 2,
            world_centroid_mm=base_xy.mean(axis=0),
            world_bbox_min_mm=base_xy.min(axis=0), world_bbox_max_mm=base_xy.max(axis=0),
            development=result, strategy=result.strategy, status=result.status, warnings=list(result.warnings),
        )
    if primary.candidate_id not in panels:
        raise EngineError("DEVELOPMENT FAILURE: the primary deck panel could not be developed.")

    timer.start("mapping", "Mapping raw curves onto developed panels")
    grid = GridInfo(surface.x_min_mm, surface.y_min_mm, surface.resolution_mm, dict(surface.patch_masks))
    geometry: dict[int, PanelGeometry] = {}
    outer_by_panel: dict[int, v1compat.FeatureCurve] = {primary.candidate_id: topview.primary_outer_curve}
    outer_by_panel.update({int(k): v for k, v in topview.secondary_outer_curves.items()})
    for pid, panel in panels.items():
        outer = outer_by_panel.get(pid)
        polygon = v1compat.clean_polygon(outer.points_input[:, :2] * mm_per_unit) if outer is not None else None
        geometry[pid] = PanelGeometry(pid, polygon, panel.development.mesh.base_vertices_mm[:, :2])
    max_distance = float(config["development"]["maximum_curve_mapping_distance_mm"])
    curves: list[MappedCurve] = []
    assignment_notes: list[dict[str, Any]] = []
    counter = 0

    def _map(points_mm: np.ndarray, pid: int) -> tuple[np.ndarray, str, float, float]:
        mapping = v1compat.map_curve_to_development(points_mm, panels[pid].development, max_distance)
        d = mapping.distances_mm
        return (mapping.flat_points_mm[:, :2].copy(), mapping.status,
                float(np.max(d)) if len(d) else 0.0, float(np.percentile(d, 95)) if len(d) else 0.0)

    def _add(family: str, feature: v1compat.FeatureCurve, pid: int | None, assignment: Assignment | None,
             raw_curve_id: str | None = None) -> MappedCurve:
        nonlocal counter
        counter += 1
        world = np.asarray(feature.points_input, dtype=float) * mm_per_unit
        if world.shape[1] == 2:
            world = np.column_stack([world, np.zeros(len(world))])
        curve_id = f"{family.lower()}-{counter:04d}-{feature.name}"
        if pid is not None:
            flat, status, dmax, dp95 = _map(world, pid)
        else:
            flat, status, dmax, dp95 = np.empty((0, 2)), "UNASSIGNED", 0.0, 0.0
        curve = MappedCurve(
            curve_id, family, feature.layer, feature.name, bool(feature.closed), float(feature.confidence),
            world, flat, pid, assignment.to_dict() if assignment else {"method": "outer", "fraction": 1.0},
            status, dmax, dp95, raw_curve_id,
        )
        curves.append(curve)
        return curve

    # Panel outers: RAW = the detected boundary itself.  Raw features are
    # keyed by (layer, name) because v1's conditioning hands back copies, not
    # the same FeatureCurve objects.
    raw_id_by_feature: dict[tuple[str, str], str] = {}
    for pid, outer in outer_by_panel.items():
        if pid in panels:
            curve = _add("OUTER", outer, pid, None)
            raw_id_by_feature[(outer.layer, outer.name)] = curve.curve_id

    # Obstacles / features / hints with conservative assignment.
    for feature in topview.feature_curves:
        family = _family_for_layer(feature.layer, v2_config)
        if family is None:
            continue
        world_xy = np.asarray(feature.points_input, dtype=float)[:, :2] * mm_per_unit
        assignment = assign_curve(world_xy, grid, geometry, v2_config)
        curve = _add(family, feature, assignment.panel_id, assignment)
        raw_id_by_feature[(feature.layer, feature.name)] = curve.curve_id
        if not assignment.assigned:
            warnings.append(f"UNASSIGNED {family}: {feature.name} ({feature.layer}) -- {assignment.note}")
            assignment_notes.append({"curve_id": curve.curve_id, "family": family, "name": feature.name, **assignment.to_dict()})
        elif assignment.method not in {"mask_majority", "outer"}:
            assignment_notes.append({"curve_id": curve.curve_id, "family": family, "name": feature.name, **assignment.to_dict()})

    # ROBUST: v1's conditioned curves, mapped onto the same panel as their raw source.
    timer.start("conditioning", "Conditioning curves for the ROBUST reference layer")
    raw_curves = v1compat.useful_raw_curves(topview.primary_outer_curve, topview.secondary_outer_curves, topview.feature_curves)
    conditioned = v1compat.condition_curves(raw_curves, surface, mm_per_unit, config)
    panel_by_raw_id = {c.curve_id: c.panel_id for c in curves}
    for item in conditioned:
        raw_id = raw_id_by_feature.get((item.raw_curve.layer, item.raw_curve.name))
        pid = panel_by_raw_id.get(raw_id) if raw_id else None
        if pid is None:
            continue
        smooth = item.smoothed_curve
        smooth_feature = v1compat.FeatureCurve(smooth.layer, smooth.name, smooth.points_input, smooth.closed, smooth.confidence, dict(smooth.metrics), smooth.curve_id)
        _add("ROBUST", smooth_feature, pid, None, raw_curve_id=raw_id)
    timer.finish()

    diagnostics = {
        "up_axis": {"requested": requested_up, "selected": selected_up, "scores": scores},
        "candidate_count": len(candidates),
        "candidates": [{"candidate_id": c.candidate_id, "area_mm2": float(c.area_mm2),
                        "centroid_mm": np.asarray(c.centroid_mm, dtype=float).tolist(),
                        "touches_open_mesh_boundary": bool(c.touches_open_mesh_boundary)} for c in candidates],
        "grid": {"x_min_mm": surface.x_min_mm, "y_min_mm": surface.y_min_mm, "resolution_mm": surface.resolution_mm,
                 "shape": list(np.asarray(surface.accepted_mask).shape)},
        "topview_statistics": {k: v for k, v in topview.statistics.items() if isinstance(v, (int, float, str, bool))},
        "mesh_statistics": {k: v for k, v in stats.items() if isinstance(v, (int, float, str, bool))},
        "assignment_notes": assignment_notes,
    }
    return EngineResult(str(input_path), "", str(resolved_units), float(mm_per_unit), panels, curves, warnings,
                        timer.timings, diagnostics, "", False)


# ---------------------------------------------------------------------------
# cache (de)serialisation

def save_engine_result(result: EngineResult, directory: Path) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for pid, panel in result.panels.items():
        v1compat.save_development_artifact(directory / panel.metadata()["artifact"], panel.development)
    arrays: dict[str, np.ndarray] = {}
    for index, curve in enumerate(result.curves):
        arrays[f"c{index:05d}_world"] = curve.world_points_mm.astype(np.float64)
        arrays[f"c{index:05d}_flat"] = curve.flat_points_mm.astype(np.float64)
    np.savez_compressed(directory / "curves.npz", **arrays)
    payload = {
        "input_path": result.input_path, "input_sha256": result.input_sha256, "units": result.units,
        "mm_per_unit": result.mm_per_unit, "warnings": result.warnings, "stage_timings": result.stage_timings,
        "diagnostics": result.diagnostics,
        "panels": [panel.metadata() for panel in result.panels.values()],
        "curves": [curve.metadata() for curve in result.curves],
    }
    (directory / "engine.json").write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def load_engine_result(directory: Path, cache_key: str, cache_hit: bool) -> EngineResult:
    directory = Path(directory)
    payload = json.loads((directory / "engine.json").read_text(encoding="utf-8"))
    panels: dict[int, Panel] = {}
    for meta in payload["panels"]:
        development = v1compat.load_development_artifact(directory / meta["artifact"], int(meta["panel_id"]), meta["strategy"], meta["status"])
        panels[int(meta["panel_id"])] = Panel(
            int(meta["panel_id"]), meta["role"], float(meta["area_mm2"]), np.asarray(meta["world_centroid_mm"], dtype=float),
            np.asarray(meta["world_bbox_min_mm"], dtype=float), np.asarray(meta["world_bbox_max_mm"], dtype=float),
            development, meta["strategy"], meta["status"], list(meta.get("warnings", [])),
        )
    curves: list[MappedCurve] = []
    with np.load(directory / "curves.npz") as data:
        for index, meta in enumerate(payload["curves"]):
            curves.append(MappedCurve(
                meta["curve_id"], meta["family"], meta["source_layer"], meta["name"], bool(meta["closed"]),
                float(meta["confidence"]), data[f"c{index:05d}_world"], data[f"c{index:05d}_flat"],
                None if meta["panel_id"] is None else int(meta["panel_id"]), meta.get("assignment", {}),
                meta.get("mapping_status", "GOOD"), float(meta.get("mapping_distance_max_mm", 0.0)),
                float(meta.get("mapping_distance_p95_mm", 0.0)), meta.get("raw_curve_id"),
            ))
    return EngineResult(payload["input_path"], payload["input_sha256"], payload["units"], float(payload["mm_per_unit"]),
                        panels, curves, list(payload["warnings"]), dict(payload["stage_timings"]),
                        dict(payload["diagnostics"]), cache_key, cache_hit)


def _json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return str(value)
