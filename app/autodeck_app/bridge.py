"""Glue to the AutoDeck2 engine: run the stages, and turn a run into overlay
geometry -- in the world frame (drawn over the mesh) and in the flat panel
layout (as in outline.dxf)."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy.spatial import cKDTree

from . import settings

Log = Callable[[str], None]


def _engine():
    """Late import so a missing engine is reported by the server, not at import time."""

    settings.ensure_engine_on_path()
    import autodeck2  # noqa: F401
    from autodeck2 import autofit, config, engine, ingest, layout, pipeline, teak, v1compat
    return autofit, config, engine, ingest, layout, pipeline, teak, v1compat


# ---------------------------------------------------------------------------
# runs

def list_runs() -> list[dict[str, Any]]:
    runs = []
    if not settings.RUNS_DIR.is_dir():
        return runs
    for run_dir in sorted(settings.RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        meta_path = run_dir / "run.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        runs.append({
            "run_id": run_dir.name, "run_dir": str(run_dir), "input_name": meta.get("input_name"),
            "input_path": meta.get("input_path"), "input_exists": Path(str(meta.get("input_path", ""))).is_file(),
            "status": meta.get("status"), "units": meta.get("units"), "layout_mode": meta.get("layout_mode"),
            "panels": len(meta.get("panels", [])), "teak": bool((meta.get("teak") or {}).get("enabled")),
            "pattern": ((meta.get("teak") or {}).get("pattern") or ("teak" if (meta.get("teak") or {}).get("enabled") else None)),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(meta_path.stat().st_mtime)),
            "files": run_files(run_dir),
        })
    return runs


def run_files(run_dir: Path) -> dict[str, bool]:
    names = ["outline.3dm", "outline.dxf", "auto_cam.3dm", "final_auto.dxf", "final.dxf",
             "outline_report.md", "autofit_report.md", "final_report.md", "calibration_report.md",
             "run.json", "sheet_report.md"]
    files = {name: (run_dir / name).is_file() for name in names}
    # Sheet DXFs are numbered and there can be any number of them.
    for path in sorted(run_dir.glob("sheet_*.dxf")):
        files[path.name] = True
    return files


# ---------------------------------------------------------------------------
# jobs

def pattern_overrides(pattern: str, size_mm: float | None) -> dict[str, float] | None:
    """One 'size' number from the page -> the engine's pattern dimensions.
    teak: spacing on centre; diamond: long diagonal (short = half, the stock
    2:1 stitch); hex: across the flats."""

    if size_mm is None or size_mm <= 0:
        return None
    if pattern == "teak":
        return {"teak_spacing_mm": float(size_mm)}
    if pattern == "diamond":
        return {"diamond_long_diagonal_mm": float(size_mm), "diamond_short_diagonal_mm": float(size_mm) / 2.0}
    if pattern == "hex":
        return {"hex_across_flats_mm": float(size_mm)}
    return None


def job_outline(scan_path: Path, units: str | None, layout_mode: str, pattern: str, pattern_size: float | None, log: Log) -> dict[str, Any]:
    _autofit, config_mod, _engine_mod, _ingest, _layout, pipeline, _teak, _v1 = _engine()
    config = config_mod.load_config()
    run_id = pipeline.make_run_id(scan_path) if hasattr(pipeline, "make_run_id") else None
    run_dir = settings.RUNS_DIR / run_id if run_id else None
    log("Engine: scan -> panels -> raw curves (a new scan takes minutes; cached afterwards)")
    summary = pipeline.run_outline(scan_path, config, units=units, layout_mode=layout_mode, pattern=pattern,
                                   pattern_overrides=pattern_overrides(pattern, pattern_size),
                                   run_dir=run_dir, progress=log)
    log(f"Outline written: {summary['outline']['outline_3dm']}")
    return {"run_id": Path(summary["run_dir"]).name, "run_dir": summary["run_dir"], "status": summary["status"],
            "warnings": summary.get("warnings", []), "panels": summary.get("panels", {})}


def job_autofit(run_dir: Path, log: Log) -> dict[str, Any]:
    autofit, config_mod, *_rest = _engine()
    config = config_mod.load_config()
    report = autofit.fit_run(run_dir, config, progress=log)
    lines = []
    for panel in report["panels"]:
        for loop in panel["loops"]:
            lines.append(f"panel {panel['panel_id']} {loop['kind']}: {loop['line_count']} lines + {loop['arc_count']} arcs, "
                         f"{loop['corner_count']} corners, max dev {loop['max_deviation_mm']:.1f} mm"
                         + (f", {len(loop['flagged'])} flagged" if loop["flagged"] else ""))
    for line in lines:
        log(line)
    log(f"Auto-fit status: {report['status']}")
    return {"status": report["status"], "final_auto_dxf": bool(report.get("final_auto_dxf")),
            "missing_panels": report.get("missing_panels", []), "flagged_total": report.get("flagged_total", 0), "lines": lines}


def job_ingest(run_dir: Path, drawing: Path, log: Log) -> dict[str, Any]:
    _autofit, config_mod, _engine_mod, ingest, *_rest = _engine()
    config = config_mod.load_config()
    log(f"Ingesting {drawing.name}")
    try:
        result = ingest.ingest_run(run_dir, drawing, config)
    except ingest.IngestError as exc:
        raise RuntimeError(str(exc)) from exc
    for rejection in result.rejections:
        log(f"rejected: {rejection}")
    for pid, panel in result.panels.items():
        for problem in panel.problems:
            log(f"panel {pid}: {problem}")
        for loop in panel.loops_report:
            s = loop["stats"]
            log(f"panel {pid} {loop['kind']}: {s['primitive_count']} primitives ({s['line_count']}L/{s['arc_count']}A), "
                f"{s['tangent_failure_count']} tangent failures, {s['intentional_corner_count']} corners")
    log(f"Ingest status: {result.status}")
    return {"status": result.status, "final_dxf": bool(result.final_dxf), "rejections": result.rejections}


# ---------------------------------------------------------------------------
# sheets: seams, nesting, per-sheet DXF


def _sheet_modules():
    settings.ensure_engine_on_path()
    from autodeck2 import config as config_mod, sheetjob, sheets
    return config_mod, sheetjob, sheets


def _sheet_config(overrides: dict[str, Any]) -> dict[str, Any]:
    config_mod, _sheetjob, _sheets = _sheet_modules()
    config = config_mod.load_config()
    if overrides:
        config = {**config, "sheets": {**(config.get("sheets") or {}), **overrides}}
    return config


def sheet_preview(run_dir: Path, overrides: dict[str, Any],
                  seams: list[dict[str, Any]] | None = None, save: bool = False) -> dict[str, Any]:
    """Seams plus the nested sheet layout as drawable rings; writes no DXFs."""

    _config_mod, sheetjob, sheets_mod = _sheet_modules()
    config = _sheet_config(overrides)
    if seams is not None:
        parsed = [sheets_mod.Seam.from_dict({**s, "seam_id": s.get("seam_id") or f"s{i + 1}"})
                  for i, s in enumerate(seams)]
        if save:
            sheets_mod.write_seams(run_dir, parsed)
    else:
        parsed = sheets_mod.read_seams(run_dir)

    resolved = sheets_mod.settings(config)
    if sheetjob.source_dxf(run_dir) is None:
        return {"available": False,
                "reason": "run auto-fit (or ingest a drawing) first -- sheets are cut from the fitted outline",
                "seams": [s.to_dict() for s in parsed], "settings": resolved}
    result = sheetjob.preview(run_dir, config, seams=parsed)
    result["available"] = True
    result["seams"] = [s.to_dict() for s in parsed]
    result["settings"] = resolved
    return result


def job_sheets(run_dir: Path, overrides: dict[str, Any], log: Log) -> dict[str, Any]:
    _config_mod, sheetjob, _sheets = _sheet_modules()
    result = sheetjob.plan(run_dir, _sheet_config(overrides), progress=log)
    for warning in result.get("warnings", []):
        log(f"warning: {warning}")
    for entry in result.get("files", []):
        log(f"{entry['name']}: {len(entry['pieces'])} piece(s), "
            f"{entry['utilisation'] * 100:.0f}% used, closed polylines={entry['all_closed']}")
    log(f"Sheets: {result['summary']['sheet_count']} for {result['piece_count']} piece(s) "
        f"-- status {result['status']}")
    return {"status": result["status"], "sheet_count": result["summary"]["sheet_count"],
            "piece_count": result["piece_count"],
            "files": [entry["name"] for entry in result.get("files", [])],
            "oversize": result.get("oversize", [])}


# ---------------------------------------------------------------------------
# a loaded run and its overlays

@dataclass
class RunView:
    run_dir: Path
    meta: dict[str, Any]
    result: Any                       # autodeck2 EngineResult
    placements: dict[int, Any]        # panel id -> PanelPlacement
    teak_lines: Any                   # dict[pid, list[(p0, p1)]] in the placed frame, or None
    mm_per_unit: float
    pattern_info: dict[str, Any] = field(default_factory=dict)
    flatten: dict[int, dict[str, Any]] = field(default_factory=dict)
    lifters: dict[int, Callable[[np.ndarray], np.ndarray]] = field(default_factory=dict)


def load_run(run_dir: Path, log: Log) -> RunView:
    _autofit, config_mod, engine_mod, _ingest, layout, pipeline, teak, v1compat = _engine()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    input_path = Path(meta["input_path"])
    if not input_path.is_file():
        raise FileNotFoundError(f"the scan this run was made from is not at {input_path}")
    config = config_mod.load_config()
    log("Loading developed panels (engine cache)")
    result = engine_mod.run_engine(input_path, config, units=meta.get("units"), progress=log, debug_dir=run_dir / "debug")
    placements, _warnings = layout.compute_layout(pipeline.panel_sources(result), config, meta.get("layout_mode") or "nest")
    pattern_lines = None
    pattern_info: dict[str, Any] = {}
    stored = meta.get("teak") or {}
    if stored.get("enabled"):
        pattern = str(stored.get("pattern") or "teak")
        log({"teak": "Teak lines", "diamond": "Diamond stitch", "hex": "Hexagons"}.get(pattern, pattern))
        pattern_settings = dict(engine_mod.merged_v1_config(config)["pattern"])
        pattern_settings.update(stored.get("dimensions_mm") or {})   # the run keeps its own sizes
        if hasattr(teak, "generate_pattern"):
            pattern_lines, pattern_info = teak.generate_pattern(result, placements, pattern_settings, pattern)
        else:
            pattern_lines, pattern_info = teak.generate_teak(result, placements, pattern_settings)
    mm_per_unit = float(v1compat.unit_scale_to_mm(str(meta.get("units") or result.units)))
    view = RunView(run_dir, meta, result, placements, pattern_lines, mm_per_unit, pattern_info)
    for pid, panel in result.panels.items():
        view.lifters[pid] = _make_lifter(panel.development)
        try:
            view.flatten[pid] = engine_mod.flatten_quality(panel)
        except Exception:  # noqa: BLE001 -- quality info is advisory
            view.flatten[pid] = {"strategy": panel.strategy, "status": panel.status}
    return view


def _make_lifter(development: Any) -> Callable[[np.ndarray], np.ndarray]:
    """uv (panel frame, mm) -> world xyz (mm) through the development mesh."""

    uv = np.asarray(development.uv_mm, dtype=float)[:, :2]
    xyz = np.asarray(development.mesh.base_vertices_mm, dtype=float)
    faces = np.asarray(development.mesh.faces, dtype=int)
    tri = uv[faces]                                   # (M,3,2)
    tree = cKDTree(tri.mean(axis=1))
    k = min(12, len(faces))

    def lift(points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=float)[:, :2]
        if not len(pts):
            return np.empty((0, 3))
        _d, idx = tree.query(pts, k=k)
        idx = np.asarray(idx).reshape(len(pts), -1)
        cand = tri[idx]                               # (N,k,3,2)
        v0 = cand[:, :, 1] - cand[:, :, 0]; v1 = cand[:, :, 2] - cand[:, :, 0]; v2 = pts[:, None, :] - cand[:, :, 0]
        d00 = (v0 * v0).sum(-1); d01 = (v0 * v1).sum(-1); d11 = (v1 * v1).sum(-1)
        d20 = (v2 * v0).sum(-1); d21 = (v2 * v1).sum(-1)
        den = d00 * d11 - d01 * d01
        den = np.where(np.abs(den) < 1e-12, 1e-12, den)
        b = (d11 * d20 - d01 * d21) / den; c = (d00 * d21 - d01 * d20) / den; a = 1.0 - b - c
        inside = (a >= -1e-3) & (b >= -1e-3) & (c >= -1e-3)
        score = np.where(inside, 1.0, np.minimum(np.minimum(a, b), c))
        best = np.argmax(score, axis=1)
        rows = np.arange(len(pts))
        bary = np.stack([a, b, c], axis=-1)[rows, best]
        bary = np.clip(bary, 0.0, 1.0)
        bary /= np.maximum(bary.sum(axis=1, keepdims=True), 1e-12)
        corners = xyz[faces[idx[rows, best]]]         # (N,3,3)
        return (corners * bary[:, :, None]).sum(axis=1)

    return lift


def _unplace(placement: Any, xy: np.ndarray) -> np.ndarray:
    xy = np.asarray(xy, dtype=float)[:, :2]
    return (xy - placement.translation) @ placement.rotation.T + placement.uv_centroid


def _densify(points: np.ndarray, spacing: float) -> np.ndarray:
    pts = np.asarray(points, dtype=float)[:, :2]
    if len(pts) < 2:
        return pts
    out = [pts[:1]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(math.ceil(float(np.linalg.norm(b - a)) / spacing)))
        out.append(np.linspace(a, b, n + 1)[1:])
    return np.vstack(out)


def _rounded(points: np.ndarray, decimals: int = 1) -> list[list[float]]:
    return np.round(np.asarray(points, dtype=float), decimals).tolist()


_CAM_RE = re.compile(r"(?:^|::)(AUTO_CAM|USER_CAM)::PANEL_(\d+)$")
_DXF_PANEL_RE = re.compile(r"__PANEL_(\d+)$")


def _sample_bulge_polyline(points_xyb: list, closed: bool, step: float = 3.0) -> np.ndarray:
    """Sample an LWPOLYLINE (x, y, bulge) exactly as CAD interprets it."""

    n = len(points_xyb)
    out: list[np.ndarray] = []
    last = n if closed else n - 1
    for i in range(last):
        x1, y1, b = points_xyb[i]
        x2, y2, _ = points_xyb[(i + 1) % n]
        p1 = np.array([x1, y1]); p2 = np.array([x2, y2])
        chord = p2 - p1; c = float(np.linalg.norm(chord))
        if abs(b) < 1e-12 or c < 1e-12:
            k = max(1, int(c / step))
            out.append(np.linspace(p1, p2, k, endpoint=False))
            continue
        theta = 4.0 * math.atan(b)
        r = c / (2.0 * abs(math.sin(theta / 2.0)))
        mid = (p1 + p2) / 2.0
        h = math.sqrt(max(r * r - (c / 2.0) ** 2, 0.0))
        nrm = np.array([-chord[1], chord[0]]) / c
        center = mid + nrm * (h if theta > 0 else -h) * (1.0 if abs(theta) <= math.pi else -1.0)
        a0 = math.atan2(p1[1] - center[1], p1[0] - center[0])
        if np.linalg.norm(center + r * np.array([math.cos(a0 + theta), math.sin(a0 + theta)]) - p2) > 0.01:
            center = mid - nrm * (h if theta > 0 else -h) * (1.0 if abs(theta) <= math.pi else -1.0)
            a0 = math.atan2(p1[1] - center[1], p1[0] - center[0])
        k = max(3, int(abs(theta) * r / step))
        angles = np.linspace(a0, a0 + theta, k, endpoint=False)
        out.append(center + r * np.column_stack([np.cos(angles), np.sin(angles)]))
    if not closed:
        out.append(np.array([points_xyb[-1][:2]], dtype=float))
    else:
        out.append(np.array([points_xyb[0][:2]], dtype=float))   # close the displayed ring
    return np.vstack(out)


def read_final_dxf(path: Path) -> tuple[dict[int, list[np.ndarray]], dict[int, list[np.ndarray]]]:
    """The exact content of a final DXF: CAM loops (arcs reconstructed from
    bulges, the way VCarve will) and pattern lines, per panel."""

    import ezdxf

    doc = ezdxf.readfile(str(path))
    loops: dict[int, list[np.ndarray]] = {}
    pattern: dict[int, list[np.ndarray]] = {}
    for entity in doc.modelspace():
        layer = str(entity.dxf.layer)
        match = _DXF_PANEL_RE.search(layer)
        if not match:
            continue
        pid = int(match.group(1))
        if entity.dxftype() == "LWPOLYLINE":
            pts = _sample_bulge_polyline(list(entity.get_points("xyb")), bool(entity.closed))
            (pattern if layer.startswith("PATTERN_") else loops).setdefault(pid, []).append(pts)
        elif entity.dxftype() == "LINE" and layer.startswith("PATTERN_"):
            s, e = entity.dxf.start, entity.dxf.end
            pattern.setdefault(pid, []).append(np.array([[s.x, s.y], [e.x, e.y]], dtype=float))
    return loops, pattern


def _layer_name(layer: Any) -> str:
    full = getattr(layer, "FullPath", None)
    return str(full) if full else str(layer.Name)


def _sample_curve(geometry: Any) -> list[np.ndarray]:
    """Points along a rhino3dm curve (xy), splitting poly curves into pieces."""

    import rhino3dm

    if isinstance(geometry, rhino3dm.PolyCurve):
        pieces = []
        for k in range(geometry.SegmentCount):
            pieces.extend(_sample_curve(geometry.SegmentCurve(k)))
        return pieces
    if isinstance(geometry, rhino3dm.PolylineCurve):
        return [np.array([[geometry.Point(i).X, geometry.Point(i).Y] for i in range(geometry.PointCount)])]
    if isinstance(geometry, rhino3dm.LineCurve):
        return [np.array([[geometry.PointAtStart.X, geometry.PointAtStart.Y], [geometry.PointAtEnd.X, geometry.PointAtEnd.Y]])]
    if not isinstance(geometry, rhino3dm.Curve):
        return []
    nurbs = geometry.ToNurbsCurve() if not isinstance(geometry, rhino3dm.NurbsCurve) else geometry
    if nurbs is None:
        return []
    domain = nurbs.Domain
    count = 2 if nurbs.IsLinear() else (48 if isinstance(geometry, rhino3dm.ArcCurve) else 96)
    ts = np.linspace(domain.T0, domain.T1, count)
    return [np.array([[nurbs.PointAt(float(t)).X, nurbs.PointAt(float(t)).Y] for t in ts])]


def read_cam_layers(path: Path) -> tuple[dict[str, dict[int, list[np.ndarray]]], dict[str, list[np.ndarray]]]:
    """AUTO_CAM / USER_CAM polylines per panel and AUTO_CORNERS / USER_CORNERS points from a 3dm."""

    import rhino3dm

    model = rhino3dm.File3dm.Read(str(path))
    curves: dict[str, dict[int, list[np.ndarray]]] = {"AUTO_CAM": {}, "USER_CAM": {}}
    corners: dict[str, list[np.ndarray]] = {"AUTO_CORNERS": [], "USER_CORNERS": []}
    if model is None:
        return curves, corners
    layers = list(model.Layers)
    for obj in model.Objects:
        index = obj.Attributes.LayerIndex
        if index < 0 or index >= len(layers):
            continue
        name = _layer_name(layers[index])
        geometry = obj.Geometry
        short = name.split("::")[-1]
        if short in corners and isinstance(geometry, rhino3dm.Point):
            corners[short].append(np.array([geometry.Location.X, geometry.Location.Y]))
            continue
        match = _CAM_RE.search(name)
        if not match:
            continue
        family, pid = match.group(1), int(match.group(2))
        for piece in _sample_curve(geometry):
            if len(piece) >= 2:
                curves[family].setdefault(pid, []).append(piece)
    return curves, corners


def overlays(view: RunView, log: Log) -> dict[str, Any]:
    """Every drawable layer of a run: `world` polylines (mm, over the mesh) and `flat` polylines (placed layout)."""

    result = view.result
    layers: list[dict[str, Any]] = []

    def add(layer_id: str, label: str, color: str, world: list, flat: list, kind: str = "lines", default_on: bool = True, **extra: Any) -> None:
        if not world and not flat:
            return
        layers.append({"id": layer_id, "label": label, "color": color, "kind": kind, "on": default_on,
                       "world": world, "flat": flat, "count": max(len(world), len(flat)), **extra})

    def lifted(pid: int, placed_xy: np.ndarray, spacing: float) -> list[list[float]]:
        dense = _densify(placed_xy, spacing)
        return _rounded(view.lifters[pid](_unplace(view.placements[pid], dense)))

    for family, layer_id, label, color, on in (
        ("OUTER", "raw_outer", "Raw outline (wall line)", "#ff3b30", True),
        ("OBSTACLE", "raw_obstacles", "Raw obstacles", "#ff9500", True),
        ("FEATURE", "raw_features", "Seams / hatches / nonskid", "#ffd60a", True),
        ("HINT", "raw_hints", "Hints (low confidence)", "#8e8e93", False),
    ):
        world = []; flat = []
        for pid in sorted(result.panels):
            for curve in result.curves_for(pid, family):
                world.append(_rounded(curve.world_points_mm))
                flat.append(_rounded(view.placements[pid].apply(curve.flat_points_mm)))
        for curve in result.unassigned():
            if curve.family == family:
                world.append(_rounded(curve.world_points_mm))
        add(layer_id, label, color, world, flat, default_on=on)

    world = []; flat = []
    for pid in sorted(result.panels):
        for curve in result.curves_for(pid, "ROBUST"):
            flat.append(_rounded(view.placements[pid].apply(curve.flat_points_mm)))
            world.append(_rounded(view.lifters[pid](curve.flat_points_mm)))
    add("robust", "Robust reference (de-noised)", "#34c759", world, flat, default_on=False)

    if view.teak_lines:
        pattern = str(view.pattern_info.get("pattern") or "teak")
        dims = view.pattern_info.get("dimensions_mm") or {}
        label, color = {
            "teak": (f"Teak lines ({dims.get('teak_spacing_mm', 63.5):g} mm)", "#c68a3c"),
            "diamond": (f"Diamond stitch ({dims.get('diamond_long_diagonal_mm', 152.4):g} × {dims.get('diamond_short_diagonal_mm', 76.2):g} mm)", "#2fd6c3"),
            "hex": (f"Hexagons ({dims.get('hex_across_flats_mm', 152.4):g} mm)", "#ff6482"),
        }.get(pattern, ("Pattern", "#c68a3c"))
        world = []; flat = []
        for pid, lines in view.teak_lines.items():
            if pid not in view.placements:
                continue
            for p0, p1 in lines:
                seg = np.array([p0[:2], p1[:2]], dtype=float)
                flat.append(_rounded(seg))
                world.append(lifted(pid, seg, 40.0))
        add("pattern", label, color, world, flat)

    for filename, family, layer_id, label, color in (
        ("auto_cam.3dm", "AUTO_CAM", "auto_cam", "Auto-fit CAM outline", "#0a84ff"),
        ("outline.3dm", "USER_CAM", "user_cam", "Your drawn CAM outline", "#bf5af2"),
    ):
        path = view.run_dir / filename
        if not path.is_file():
            continue
        try:
            curves, corners = read_cam_layers(path)
        except Exception as exc:  # noqa: BLE001
            log(f"could not read {filename}: {exc}")
            continue
        world = []; flat = []
        for pid, pieces in curves[family].items():
            if pid not in view.placements:
                continue
            for piece in pieces:
                flat.append(_rounded(piece))
                world.append(lifted(pid, piece, 20.0))
        add(layer_id, label, color, world, flat)
        corner_key = "AUTO_CORNERS" if family == "AUTO_CAM" else "USER_CORNERS"
        points = corners.get(corner_key) or []
        if points:
            xy = np.vstack(points)
            world_pts = []
            for q in xy:
                pid = _panel_at(view, q)
                if pid is not None:
                    world_pts.append(_rounded(view.lifters[pid](_unplace(view.placements[pid], q[None, :])))[0])
            add(layer_id + "_corners", label.replace("CAM outline", "corners"), color, world_pts, _rounded(xy), kind="points")

    # the exact files VCarve receives, reconstructed from the DXFs themselves
    for filename, layer_id, label, color in (
        ("final_auto.dxf", "final_auto_file", "final_auto.dxf — exact VCarve file", "#00e676"),
        ("final.dxf", "final_file", "final.dxf — exact VCarve file (your drawing)", "#ff9f0a"),
    ):
        path = view.run_dir / filename
        if not path.is_file():
            continue
        try:
            loops, pattern_lines = read_final_dxf(path)
        except Exception as exc:  # noqa: BLE001
            log(f"could not read {filename}: {exc}")
            continue
        world = []; flat = []
        for pid, pieces in loops.items():
            if pid not in view.placements:
                continue
            for piece in pieces:
                flat.append(_rounded(piece))
                world.append(lifted(pid, piece, 20.0))
        for pid, lines in pattern_lines.items():
            if pid not in view.placements:
                continue
            for seg in lines:
                flat.append(_rounded(seg))
                world.append(lifted(pid, seg, 40.0))
        add(layer_id, label, color, world, flat)

    panels = []
    for pid in sorted(result.panels):
        placement = view.placements[pid]
        outline = placement.placed_outline
        bbox = None
        if outline is not None and len(outline):
            bbox = [outline.min(axis=0).round(1).tolist(), outline.max(axis=0).round(1).tolist()]
        panels.append({"id": pid, "role": result.panels[pid].role, "bbox_flat": bbox,
                       "world_centroid": np.round(np.asarray(result.panels[pid].world_centroid_mm, dtype=float), 1).tolist(),
                       "flatten": view.flatten.get(pid, {})})
    return {"run_id": view.run_dir.name, "mm_per_unit": view.mm_per_unit, "units": view.meta.get("units"),
            "layout_mode": view.meta.get("layout_mode"), "layers": layers, "panels": panels, "files": run_files(view.run_dir),
            "pattern": view.pattern_info.get("pattern") if view.pattern_info else None}


def _panel_at(view: RunView, q: np.ndarray) -> int | None:
    best = None; best_d = float("inf")
    for pid, placement in view.placements.items():
        outline = placement.placed_outline
        if outline is None or not len(outline):
            continue
        d = float(np.min(np.linalg.norm(outline - q, axis=1)))
        if d < best_d:
            best, best_d = pid, d
    return best
