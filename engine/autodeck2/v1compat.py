"""The ONLY module in AutoDeck2 that imports the v1 `autodeck` package.

Everything v2 needs from v1 -- public stage functions and the handful of
underscore-private pattern helpers -- is wrapped here with a stable v2-side
name.  `check_compatibility()` runs at startup and in the contract test: it
pins the v1 version, fingerprints the v1 source (v1 is an editable install
with no git history, so edits there must invalidate caches and fail loudly if
they change a wrapped signature), and verifies every wrapped signature.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np

from . import V1_REQUIRED_VERSION


class V1CompatibilityError(RuntimeError):
    pass


try:
    import autodeck as _v1
    from autodeck import candidates as _candidates
    from autodeck import component_diagnostics as _component_diagnostics
    from autodeck import conditioning as _conditioning
    from autodeck import config as _config
    from autodeck import curvature as _curvature
    from autodeck import development as _development
    from autodeck import mesh_io as _mesh_io
    from autodeck import orientation as _orientation
    from autodeck import patterns as _patterns
    from autodeck import preprocessing as _preprocessing
    from autodeck import relief as _relief
    from autodeck import topview as _topview
    from autodeck import up_axis as _up_axis
    from autodeck.models import (  # re-exported for typing in v2
        AcceptedSurfaceGrid,
        AnalysisMesh,
        CandidateRegion,
        ConditionedCurve,
        FeatureCurve,
        Mesh,
        TopViewResult,
    )
    from autodeck.development import CurveDevelopmentMap, DevelopmentMesh, DevelopmentResult
    from autodeck.patterns import BoatFrame, PatternLine
except ImportError as exc:  # pragma: no cover - environment problem, not logic
    raise V1CompatibilityError(
        "AutoDeck v1 (package 'autodeck') is not importable. Install it into this "
        "environment with `pip install -e /path/to/AutoDeck[rhino]` and make sure "
        f"PYTHONPATH includes its src/ directory. ({exc})"
    ) from exc

__all__ = [
    "AcceptedSurfaceGrid", "AnalysisMesh", "CandidateRegion", "ConditionedCurve",
    "FeatureCurve", "Mesh", "TopViewResult", "CurveDevelopmentMap", "DevelopmentMesh",
    "DevelopmentResult", "BoatFrame", "PatternLine",
]


# ---------------------------------------------------------------------------
# identity

def v1_version() -> str:
    return str(_v1.__version__)


def v1_source_dir() -> Path:
    return Path(_v1.__file__).resolve().parent


def v1_source_fingerprint() -> str:
    """SHA-256 over every *.py in the v1 package, in sorted order.  v1 has no
    git repository, so this is the only reliable 'has the engine changed'
    signal for cache invalidation."""

    digest = hashlib.sha256()
    for path in sorted(v1_source_dir().glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


# ---------------------------------------------------------------------------
# signature contract

_EXPECTED_SIGNATURES: dict[str, tuple[Callable[..., Any], list[str]]] = {}


def _register(name: str, fn: Callable[..., Any], params: list[str]) -> None:
    _EXPECTED_SIGNATURES[name] = (fn, params)


_register("config.load_config", _config.load_config, ["path", "validate"])
_register("config.unit_scale_to_mm", _config.unit_scale_to_mm, ["units"])
_register("mesh_io.load_obj", _mesh_io.load_obj, ["path"])
_register("mesh_io.mesh_statistics", _mesh_io.mesh_statistics, ["mesh"])
_register("up_axis.assess_up_axes", _up_axis.assess_up_axes, ["mesh", "maximum_slope_deg", "sample_face_count"])
_register("up_axis.resolve_up_axis", _up_axis.resolve_up_axis, ["requested", "scores"])
_register("preprocessing.preprocess", _preprocessing.preprocess, ["mesh", "units", "config", "up_vector"])
_register("preprocessing.build_adjacency", _preprocessing.build_adjacency, ["mesh"])
_register("curvature.calculate_geometry_fields", _curvature.calculate_geometry_fields, ["mesh", "adjacency", "config"])
_register("relief.calculate_boundary_field", _relief.calculate_boundary_field, ["mesh", "adjacency", "fields", "config", "paper_centers_mm", "face_colors"])
_register("orientation.calculate_orientation_field", _orientation.calculate_orientation_field, ["adjacency", "fields", "boundary", "config", "up_vector"])
_register("component_diagnostics.segmentation_stage_diagnostics", _component_diagnostics.segmentation_stage_diagnostics,
          ["orientation_mask", "conformability_mask", "adjacency", "face_areas", "boundary", "maximum_crossing_cost",
           "minimum_candidate_area_mm2", "segmentation_mode", "recovered_noise_faces"])
_register("candidates.discover_candidates", _candidates.discover_candidates,
          ["mesh", "adjacency", "fields", "boundary", "orientation", "config", "components", "use_conformability",
           "use_structural_boundaries", "outer_only"])
_register("topview.build_topview_features", _topview.build_topview_features,
          ["output_dir", "original", "analysis", "adjacency", "fields", "boundary", "primary", "config", "candidates"])
_register("conditioning.useful_raw_curves", _conditioning.useful_raw_curves, ["primary", "secondaries", "feature_curves"])
_register("conditioning.condition_curves", _conditioning.condition_curves, ["curves", "surface", "mm_per_input_unit", "config"])
_register("development.develop_patch", _development.develop_patch, ["surface", "patch_id", "config"])
_register("development.build_development_mesh", _development.build_development_mesh, ["surface", "patch_id", "config"])
_register("development.map_curve_to_development", _development.map_curve_to_development, ["points_mm", "result", "maximum_distance_mm", "tree"])
_register("development.save_development_artifact", _development.save_development_artifact, ["path", "result"])
_register("patterns.detect_boat_frame", _patterns.detect_boat_frame, ["primary", "settings"])
_register("patterns._teak", _patterns._teak, ["domain", "frame", "patch_id", "settings"])
_register("patterns._hex", _patterns._hex, ["domain", "frame", "patch_id", "settings"])
_register("development.development_distortion", _development.development_distortion,
          ["vertices", "faces", "uv", "minimum_edge_mm"])
_register("patterns._clean_polygon", _patterns._clean_polygon, ["points"])
_register("patterns._clip_line", _patterns._clip_line, ["start", "end", "domain", "patch_id", "reason", "minimum"])
_register("patterns._deduplicate_lines", _patterns._deduplicate_lines, ["lines", "tolerance"])


def check_compatibility() -> dict[str, Any]:
    """Raise V1CompatibilityError on any mismatch; return identity info."""

    problems: list[str] = []
    if v1_version() != V1_REQUIRED_VERSION:
        problems.append(f"autodeck version is {v1_version()}, AutoDeck2 requires {V1_REQUIRED_VERSION}")
    for name, (fn, expected) in _EXPECTED_SIGNATURES.items():
        actual = list(inspect.signature(fn).parameters)
        if actual != expected:
            problems.append(f"{name} signature changed: expected {expected}, found {actual}")
    for module_name in ("igl", "rhino3dm", "shapely", "ezdxf"):
        try:
            __import__(module_name)
        except ImportError as exc:
            problems.append(f"required module {module_name!r} not importable: {exc}")
    if problems:
        raise V1CompatibilityError(
            "AutoDeck v1 at " + str(v1_source_dir()) + " is not compatible with this AutoDeck2:\n  - "
            + "\n  - ".join(problems)
        )
    return {
        "v1_version": v1_version(),
        "v1_source_dir": str(v1_source_dir()),
        "v1_source_fingerprint": v1_source_fingerprint(),
    }


# ---------------------------------------------------------------------------
# thin wrappers (names are v2's stable contract)

def load_v1_config() -> dict[str, Any]:
    return _config.load_config()


def unit_scale_to_mm(units: str) -> float:
    return _config.unit_scale_to_mm(units)


def load_obj(path: Path) -> Mesh:
    return _mesh_io.load_obj(Path(path))


def mesh_statistics(mesh: Mesh) -> dict[str, Any]:
    return _mesh_io.mesh_statistics(mesh)


def assess_up_axes(mesh: Mesh, maximum_slope_deg: float, sample_face_count: int) -> dict[str, float]:
    return _up_axis.assess_up_axes(mesh, maximum_slope_deg, sample_face_count)


def resolve_up_axis(requested: str, scores: dict[str, float]) -> tuple[str, np.ndarray]:
    return _up_axis.resolve_up_axis(requested, scores)


def preprocess(mesh: Mesh, units: str, config: dict[str, Any], up_vector: np.ndarray | None) -> tuple[AnalysisMesh, list[str]]:
    return _preprocessing.preprocess(mesh, units, config, up_vector)


def build_adjacency(mesh: Mesh) -> Any:
    return _preprocessing.build_adjacency(mesh)


def calculate_geometry_fields(mesh: Mesh, adjacency: Any, config: dict[str, Any]) -> Any:
    return _curvature.calculate_geometry_fields(mesh, adjacency, config)


def calculate_boundary_field(mesh: Mesh, adjacency: Any, fields: Any, config: dict[str, Any],
                             face_colors: Any = None) -> tuple[Any, list[str]]:
    return _relief.calculate_boundary_field(mesh, adjacency, fields, config, [], face_colors)


def analysis_face_colors(original: Mesh, source_face_indices: Any, config: dict[str, Any],
                         log: Any = None) -> Any:
    """Per-face colour for the analysis mesh, or None when the scan has no
    usable texture. See autodeck.texture for why this samples the original."""

    from autodeck import texture as _texture
    return _texture.analysis_face_colors(original, source_face_indices, config, log)


def calculate_orientation_field(adjacency: Any, fields: Any, boundary: Any, config: dict[str, Any], up_vector: np.ndarray | None) -> Any:
    return _orientation.calculate_orientation_field(adjacency, fields, boundary, config, up_vector)


def segmentation_components(orientation: Any, fields: Any, adjacency: Any, boundary: Any, config: dict[str, Any]) -> tuple[dict[str, Any], list[np.ndarray]]:
    """Stage diagnostics in orientation mode; returns (diagnostics, discovery components)."""

    conformability_mask = fields.conformability >= float(config["segmentation"]["minimum_conformability"])
    diagnostics, components, _largest, _stage_a, _stage_a_largest = _component_diagnostics.segmentation_stage_diagnostics(
        orientation.fringe_candidate,
        conformability_mask,
        adjacency,
        fields.face_areas,
        boundary,
        float(config["segmentation"]["max_boundary_cost"]),
        float(config["segmentation"]["minimum_candidate_area_mm2"]),
        "orientation",
        orientation.recovered_noise_faces,
    )
    return diagnostics, components


def discover_candidates(mesh: Mesh, adjacency: Any, fields: Any, boundary: Any, orientation: Any,
                        config: dict[str, Any], components: list[np.ndarray]) -> tuple[list[CandidateRegion], list[str]]:
    return _candidates.discover_candidates(
        mesh, adjacency, fields, boundary, orientation, config,
        components=components, use_conformability=False, use_structural_boundaries=False, outer_only=True,
    )


def build_topview_features(output_dir: Path, original: Mesh, analysis: AnalysisMesh, adjacency: Any, fields: Any,
                           boundary: Any, primary: CandidateRegion, config: dict[str, Any],
                           candidates: list[CandidateRegion]) -> TopViewResult:
    return _topview.build_topview_features(Path(output_dir), original, analysis, adjacency, fields, boundary, primary, config, candidates)


def useful_raw_curves(primary: FeatureCurve | None, secondaries: dict[int, FeatureCurve], feature_curves: list[FeatureCurve]) -> list[FeatureCurve]:
    return _conditioning.useful_raw_curves(primary, secondaries, feature_curves)


def condition_curves(curves: list[FeatureCurve], surface: AcceptedSurfaceGrid, mm_per_input_unit: float, config: dict[str, Any]) -> list[ConditionedCurve]:
    return _conditioning.condition_curves(curves, surface, mm_per_input_unit, config)


def develop_patch(surface: AcceptedSurfaceGrid, patch_id: int, config: dict[str, Any]) -> DevelopmentResult:
    return _development.develop_patch(surface, patch_id, config)


def develop_patch_rigid_planar(surface: AcceptedSurfaceGrid, patch_id: int, config: dict[str, Any]) -> DevelopmentResult:
    mesh = _development.build_development_mesh(surface, patch_id, config)
    return _development.RigidPlanarDevelopment().develop(mesh, config)


def map_curve_to_development(points_mm: np.ndarray, result: DevelopmentResult, maximum_distance_mm: float,
                             tree: Any = None) -> CurveDevelopmentMap:
    return _development.map_curve_to_development(
        np.asarray(points_mm, dtype=np.float64), result, maximum_distance_mm, tree)


def build_surface_tree(result: DevelopmentResult) -> Any:
    """One reusable AABB per panel; see development.build_surface_tree."""

    return _development.build_surface_tree(result)


def save_development_artifact(path: Path, result: DevelopmentResult) -> None:
    _development.save_development_artifact(Path(path), result)


def load_development_artifact(path: Path, patch_id: int, strategy: str, status: str) -> DevelopmentResult:
    """Inverse of save_development_artifact (v1 has no loader of its own)."""

    with np.load(Path(path)) as data:
        mesh = DevelopmentMesh(
            int(data["patch_id"][0]),
            data["vertices_mm"].astype(np.float64),
            data["base_vertices_mm"].astype(np.float64),
            data["faces"].astype(np.int64),
            data["raster_rows"].astype(np.int64),
            data["raster_cols"].astype(np.int64),
            {}, {},
        )
        uv = data["uv_mm"].astype(np.float64)
    return DevelopmentResult(patch_id, strategy, mesh, uv, status, {}, {}, [])


# pattern helpers (private in v1; isolated here on purpose)

def clean_polygon(points: np.ndarray):
    return _patterns._clean_polygon(np.asarray(points, dtype=float))


def detect_boat_frame(primary_outline_2d: np.ndarray, settings: dict[str, Any]) -> BoatFrame:
    return _patterns.detect_boat_frame(SimpleNamespace(sampled_points=np.asarray(primary_outline_2d, dtype=float)), settings)


def teak_lines(domain: Any, frame: BoatFrame, patch_id: int, settings: dict[str, Any]) -> tuple[list[PatternLine], int]:
    lines, generated, _ = _patterns._teak(domain, frame, patch_id, settings)
    return lines, generated


def hex_lines(domain: Any, frame: BoatFrame, patch_id: int, settings: dict[str, Any]) -> tuple[list[PatternLine], int]:
    """v1's honeycomb lattice (shared edges deduplicated); extent follows the domain."""

    lines, generated, _ = _patterns._hex(domain, frame, patch_id, settings)
    return lines, generated


def development_distortion(vertices: np.ndarray, faces: np.ndarray, uv: np.ndarray, minimum_edge_mm: float) -> dict[str, Any]:
    return _development.development_distortion(
        np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64),
        np.asarray(uv, dtype=np.float64), float(minimum_edge_mm),
    )


def deduplicate_lines(lines: list[PatternLine], tolerance: float) -> tuple[list[PatternLine], int]:
    return _patterns._deduplicate_lines(lines, tolerance)


def clip_line(start: np.ndarray, end: np.ndarray, domain: Any, patch_id: int, reason: str, minimum: float) -> list[PatternLine]:
    return _patterns._clip_line(np.asarray(start, dtype=float), np.asarray(end, dtype=float), domain, patch_id, reason, minimum)
