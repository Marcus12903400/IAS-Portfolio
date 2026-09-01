"""Auto-fit pipeline: run the fitter on every panel of an existing run and put
the result through exactly the same validation as a hand drawing.

Outputs in the run directory:
  auto_cam.3dm        outline.3dm + AUTO_CAM::PANEL_n (unlocked) + AUTO_CORNERS
  final_auto.dxf      only if every loop validates (closed LWPOLYLINE + bulges, mm)
  autofit.json / autofit_report.md / autofit_panel<n>.png
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import rhino3dm
from PIL import Image, ImageDraw

from .config import config_hash
from .engine import EngineResult, run_engine
from .fitter import FitSettings, LoopFit, fit_closed_loop
from .ingest import PanelIngest, machining_pattern, validate_panel, write_final_dxf
from .layout import PanelPlacement, compute_layout
from .pipeline import panel_sources
from .progress import Progress, silent


def _placed(curve, placement: PanelPlacement) -> np.ndarray:
    return placement.apply(curve.flat_points_mm)


def fit_run(run_dir: Path, config: dict[str, Any], progress: Progress | None = None, cache_root: Path | None = None) -> dict[str, Any]:
    progress = progress or silent
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    input_path = Path(meta["input_path"])
    if not input_path.is_file():
        raise FileNotFoundError(f"the scan this run was made from is not at {input_path}")
    settings = FitSettings.from_config(config)
    progress("Loading developed panels (cache)")
    result = run_engine(input_path, config, units=meta.get("units"), progress=progress, cache_root=cache_root, debug_dir=run_dir / "debug")
    placements, _ = compute_layout(panel_sources(result), config, meta.get("layout_mode") or config["layout"].get("mode", "nest"))
    prefer_robust = str(config.get("autofit", {}).get("fit_reference", "robust")) == "robust"

    panels_out: dict[int, PanelIngest] = {}
    report: dict[str, Any] = {"run_id": run_dir.name, "settings": settings.__dict__, "config_hash": config_hash(config), "panels": []}
    for pid in sorted(result.panels):
        placement = placements[pid]
        outer_raw = next((c for c in result.curves_for(pid, "OUTER")), None)
        if outer_raw is None:
            continue
        progress(f"Fitting panel {pid}")
        robust_by_raw = {c.raw_curve_id: c for c in result.curves_for(pid, "ROBUST")}

        def target(curve) -> np.ndarray:
            robust = robust_by_raw.get(curve.curve_id) if prefer_robust else None
            source = robust if robust is not None and len(robust.flat_points_mm) >= 8 else curve
            return _placed(source, placement)

        fits: list[tuple[str, LoopFit, np.ndarray]] = []
        outer_fit = fit_closed_loop(target(outer_raw), settings, f"p{pid}-outer", loop_kind="outer")
        fits.append(("outer", outer_fit, _placed(outer_raw, placement)))
        for index, obstacle in enumerate(result.curves_for(pid, "OBSTACLE")):
            # obstacles keep the symmetric fit: on real scans the safe-side bias
            # bought no clearance worth its extra review flags there, and the
            # signed deviation stats vs RAW still police the clearance
            fits.append((f"obstacle {index + 1}",
                         fit_closed_loop(target(obstacle), settings, f"p{pid}-obs{index + 1}"),
                         _placed(obstacle, placement)))

        # Validate exactly like a hand drawing: all segments + corner points -> validate_panel
        segments = [s for _kind, fit, _raw in fits for s in fit.loop.segments]
        corners = []
        for _kind, fit, _raw in fits:
            joins = fit.loop.joins()
            corners.extend(np.asarray(joins[j]["point_mm"]) for j in fit.loop.intentional_corner_joins if j < len(joins))
        corners_xy = np.vstack(corners) if corners else np.empty((0, 2))
        reference = {"outer": _placed(outer_raw, placement), "obstacles": [_placed(o, placement) for o in result.curves_for(pid, "OBSTACLE")]}
        validated = validate_panel(pid, segments, corners_xy, reference, config)
        panels_out[pid] = validated
        panel_report = {
            "panel_id": pid, "role": result.panels[pid].role, "valid": validated.valid, "problems": validated.problems,
            "loops": [{"kind": kind, **fit.to_dict()} for kind, fit, _raw in fits],
            "validation": validated.loops_report,
        }
        report["panels"].append(panel_report)
        _render(run_dir / f"autofit_panel{pid}.png", fits, reference)

    valid_panels = {pid: p for pid, p in panels_out.items() if p.valid}
    flagged_total = sum(len(loop["flagged"]) for panel in report["panels"] for loop in panel["loops"])
    if not panels_out:
        report["status"] = "NEEDS_REVIEW"
    elif len(valid_panels) == len(panels_out):
        report["status"] = "VALID" if flagged_total == 0 else "VALID_WITH_FLAGS"
    elif valid_panels:
        report["status"] = "PARTIAL"
    else:
        report["status"] = "NEEDS_REVIEW"
    report["missing_panels"] = sorted(pid for pid in panels_out if pid not in valid_panels)
    report["flagged_total"] = flagged_total
    final_path = run_dir / "final_auto.dxf"
    if valid_panels:
        # the run's selected pattern, clipped to the FITTED loops, goes in as machinable grooves
        pattern_payload = None
        stored = meta.get("teak") or {}
        pattern_info: dict[str, Any] = {"enabled": bool(stored.get("enabled"))}
        primary_outer = next((c for c in result.curves_for(result.primary_id, "OUTER")), None)
        if stored.get("enabled") and primary_outer is not None and result.primary_id in placements:
            primary_placement = placements[result.primary_id]
            boat_plan = primary_placement.apply(primary_outer.flat_points_mm) - primary_placement.nest_offset
            offsets = {pid: placements[pid].nest_offset for pid in valid_panels if pid in placements}
            pattern_payload, pattern_info = machining_pattern(valid_panels, stored, boat_plan, offsets, config)
            if pattern_payload is not None:
                progress(f"Pattern for machining: {pattern_info['kind']}, {pattern_info['line_count']} grooves clipped to the fitted loops")
        report["machining_pattern"] = pattern_info
        # every panel that validates goes out; a flag is a place to look at, not a veto
        report["final_auto_dxf"] = write_final_dxf(final_path, valid_panels, pattern=pattern_payload)
    elif final_path.exists():
        final_path.unlink()
    progress("Writing auto_cam.3dm")
    report["auto_cam_3dm"] = str(_write_auto_3dm(run_dir, panels_out, fits_by_panel={pid: p for pid, p in panels_out.items()}))
    (run_dir / "autofit.json").write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
    _write_report(run_dir / "autofit_report.md", report)
    return report


def _write_auto_3dm(run_dir: Path, panels: dict[int, PanelIngest], fits_by_panel: dict[int, PanelIngest]) -> Path:
    model = rhino3dm.File3dm.Read(str(run_dir / "outline.3dm"))
    if model is None:
        model = rhino3dm.File3dm()
        model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Millimeters
    existing = {layer.Name: index for index, layer in enumerate(model.Layers)}

    def layer(name: str, color: tuple[int, int, int, int]) -> int:
        if name in existing:
            return existing[name]
        item = rhino3dm.Layer(); item.Name = name; item.Color = color; item.Locked = False
        existing[name] = model.Layers.Add(item)
        return existing[name]

    corner_layer = layer("AUTO_CORNERS", (255, 0, 120, 255))
    for pid, panel in panels.items():
        index = layer(f"AUTO_CAM::PANEL_{pid}", (0, 160, 200, 255))
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = index
        loops = ([panel.outer] if panel.outer else []) + list(panel.holes)
        for loop in loops:
            for seg in loop.segments:
                p = lambda q: rhino3dm.Point3d(float(q[0]), float(q[1]), 0.0)  # noqa: E731
                if seg.kind == "LINE":
                    model.Objects.AddLine(p(seg.start), p(seg.end), attrs)
                else:
                    s = seg.sample(1.0); mid = s[len(s) // 2]
                    arc = rhino3dm.Arc(p(seg.start), p(mid), p(seg.end))
                    model.Objects.AddCurve(rhino3dm.ArcCurve.CreateFromArc(arc), attrs)
            joins = loop.joins()
            cattrs = rhino3dm.ObjectAttributes(); cattrs.LayerIndex = corner_layer
            for j in loop.intentional_corner_joins:
                if j < len(joins):
                    q = joins[j]["point_mm"]
                    model.Objects.AddPoint(rhino3dm.Point3d(float(q[0]), float(q[1]), 0.0), cattrs)
    path = run_dir / "auto_cam.3dm"
    model.Write(str(path), 8)
    return path


def _render(path: Path, fits: list[tuple[str, LoopFit, np.ndarray]], reference: dict[str, Any]) -> None:
    raws = [reference["outer"], *reference["obstacles"]]
    allxy = np.vstack(raws)
    lo = allxy.min(axis=0) - 100; hi = allxy.max(axis=0) + 100
    width = 2400; scale = (width - 40) / max(hi - lo); height = int((hi[1] - lo[1]) * scale) + 40
    image = Image.new("RGB", (width, height), "white"); draw = ImageDraw.Draw(image)
    T = lambda q: (20 + (q[0] - lo[0]) * scale, height - 20 - (q[1] - lo[1]) * scale)  # noqa: E731
    for raw in raws:
        draw.line([T(q) for q in np.vstack([raw, raw[:1]])], fill=(170, 170, 170), width=2)
    for _kind, fit, _raw in fits:
        for seg in fit.loop.segments:
            draw.line([T(q) for q in seg.sample(2.0)], fill=(0, 130, 0) if seg.kind == "LINE" else (0, 0, 220), width=3)
        joins = fit.loop.joins()
        for j in fit.loop.intentional_corner_joins:
            if j < len(joins):
                x, y = T(joins[j]["point_mm"]); draw.ellipse([x - 6, y - 6, x + 6, y + 6], outline=(200, 0, 200), width=3)
    image.save(path)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    s = report["settings"]
    lines = [f"# AutoDeck2 auto-fit report -- {report['run_id']}", "", f"- Status: **{report['status']}**",
             f"- Band: {s['band_mm']} mm around the {'robust' if True else 'raw'} reference; max {s['max_arcs_per_connection']} arcs per connection",
             f"- Safe-side bias (outer loops): the fit is centred {s.get('edge_bias_mm', 0.0)} mm INSIDE the detected border and may cut up to "
             f"{s.get('inward_extra_mm', 0.0)} mm deeper into the panel for a straighter edge / bigger arc -- slightly small fits the boat, "
             "slightly big does not. Obstacle cut-outs are fitted symmetrically; their signed deviations vs RAW are below.", ""]
    if report.get("final_auto_dxf"):
        info = report["final_auto_dxf"]
        lines.append(f"- final_auto.dxf: {info['polyline_count']} closed polylines, units {info['units']} (4 = mm). Manufacturing approval TEST_ONLY.")
        mp = report.get("machining_pattern") or {}
        if mp.get("line_count"):
            dims = ", ".join(f"{k.replace('_mm', '')} {v:g} mm" for k, v in (mp.get("dimensions_mm") or {}).items())
            lines.append(f"- pattern for machining: {mp['kind']} ({dims}) -- {mp['line_count']} groove lines on `PATTERN_*__PANEL_n` "
                         "layers, clipped to the fitted loops. In VCarve: CAM layers = profile cut, pattern layers = groove pass.")
        elif mp.get("error"):
            lines.append(f"- pattern NOT added to final_auto.dxf: {mp['error']}")
        if report.get("missing_panels"):
            lines.append(f"- panels NOT in final_auto.dxf (failed validation): {report['missing_panels']} -- fix them in auto_cam.3dm and ingest.")
        if report.get("flagged_total"):
            lines.append(f"- {report['flagged_total']} flagged spot(s) to look at in Rhino (listed per panel below): the fit is tangent and closed "
                         "there, but sits outside the band or was joined with a kink declared as a corner.")
    else:
        lines.append("- final_auto.dxf NOT written (see problems). Open auto_cam.3dm, fix on AUTO_CAM/USER_CAM, and ingest.")
    lines.append("")
    for panel in report["panels"]:
        lines.append(f"## Panel {panel['panel_id']} ({panel['role']}) -- {'valid' if panel['valid'] else 'NEEDS REVIEW'}")
        lines.append("")
        lines.append("| loop | method | lines | arcs | corners | primitives | connections 1/2/3 arcs | flagged | max dev vs fit ref (mm) |")
        lines.append("|---|---|---:|---:|---:|---:|---|---:|---:|")
        for loop in panel["loops"]:
            counts = {1: 0, 2: 0, 3: 0}
            for c in loop["connections"]:
                if c["kind"] == "arcs" and c["arcs"] in counts:
                    counts[c["arcs"]] += 1
            lines.append(f"| {loop['kind']} | {loop['method']} | {loop['line_count']} | {loop['arc_count']} | {loop['corner_count']} | {loop['primitive_count']} | "
                         f"{counts[1]}/{counts[2]}/{counts[3]} | {len(loop['flagged'])} | {loop['max_deviation_mm']:.2f} |")
        for loop in panel["loops"]:
            for flag in loop["flagged"]:
                lines.append(f"- FLAG {loop['kind']}: {flag}")
        for v in panel["validation"]:
            s = v["stats"]; d = s.get("deviation") or {}
            dev = (f"; vs RAW signed min {d['signed_min_mm']:+.1f} / max {d['signed_max_mm']:+.1f} mm, |p95| {d['absolute']['p95_mm']:.1f}" if d else "")
            lines.append(f"- {v['kind']}: {s['primitive_count']} primitives, {s['tangent_failure_count']} tangent failures, "
                         f"{s['intentional_corner_count']} corners, max gap {s['max_gap_mm']:.6f} mm{dev}")
            for problem in v.get("problems", []):
                lines.append(f"  - PROBLEM: {problem}")
        for problem in panel["problems"]:
            lines.append(f"- PANEL PROBLEM: {problem}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return str(value)
