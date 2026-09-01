"""Outline pipeline orchestration: engine (cached) -> layout -> teak -> outline.3dm + run.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from . import cache as cache_module
from . import paths, v1compat
from .config import config_hash, deep_merge
from .engine import EngineResult, flatten_quality, merged_v1_config, run_engine
from .layout import PanelPlacement, PanelSource, compute_layout, overlaps
from .outline3dm import write_outline
from .progress import Progress, StageTimer, silent
from .runmeta import RunMeta, make_run_id
from .teak import PATTERN_LABELS, PATTERNS, generate_pattern


def panel_sources(result: EngineResult) -> dict[int, PanelSource]:
    sources: dict[int, PanelSource] = {}
    for pid, panel in result.panels.items():
        outer = next((c for c in result.curves_for(pid, "OUTER")), None)
        outline_uv = outer.flat_points_mm if outer is not None else panel.development.uv_mm[:, :2]
        sources[pid] = PanelSource(
            pid, panel.role, panel.development.uv_mm[:, :2], panel.development.mesh.base_vertices_mm[:, :2],
            np.asarray(outline_uv, dtype=float)[:, :2], panel.area_mm2,
        )
    return sources


def resolve_pattern(config: dict[str, Any], teak_enabled: bool | None, pattern: str | None) -> str:
    """The pattern to draw: an explicit name wins; the legacy teak flag maps to
    teak/none; otherwise the configured default."""

    if pattern is not None:
        name = str(pattern).lower()
        if name not in (*PATTERNS, "none"):
            raise ValueError(f"pattern must be one of {(*PATTERNS, 'none')}, not {pattern!r}")
        return name
    if teak_enabled is not None:
        return "teak" if teak_enabled else "none"
    if "selected_default" in config.get("pattern", {}):
        return str(config["pattern"]["selected_default"]).lower()
    return "teak" if bool(config["teak"].get("enabled_default", True)) else "none"


def run_outline(
    input_path: Path,
    config: dict[str, Any],
    units: str | None = None,
    layout_mode: str | None = None,
    teak_enabled: bool | None = None,
    pattern: str | None = None,
    pattern_overrides: dict[str, Any] | None = None,
    run_dir: Path | None = None,
    progress: Progress | None = None,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    progress = progress or silent
    input_path = Path(input_path)
    compat = v1compat.check_compatibility()
    run_id = make_run_id(input_path)
    run_dir = Path(run_dir) if run_dir is not None else paths.runs_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    timer = StageTimer(progress)

    timer.start("engine", "Engine (scan -> panels -> raw curves)")
    result = run_engine(input_path, config, units=units, progress=progress, cache_root=cache_root,
                        debug_dir=run_dir / "debug")
    timer.start("layout", "Laying out panels")
    mode = layout_mode or str(config["layout"].get("mode", "nest"))
    placements, layout_warnings = compute_layout(panel_sources(result), config, mode)
    pattern_lines = None
    selected = resolve_pattern(config, teak_enabled, pattern)
    teak_info: dict[str, Any] = {"enabled": False, "pattern": selected}
    if selected != "none":
        timer.start("pattern", f"Generating {PATTERN_LABELS[selected].lower()}")
        pattern_settings = merged_v1_config(config)["pattern"]
        if pattern_overrides:
            pattern_settings = deep_merge(pattern_settings, {k: v for k, v in pattern_overrides.items() if v is not None})
        pattern_lines, teak_info = generate_pattern(result, placements, pattern_settings, selected)
        teak_info["enabled"] = True
    timer.start("outline", "Writing outline.3dm / outline.dxf / panels.json")
    outline_info = write_outline(run_dir, result, placements, pattern_lines, config, pattern=selected)
    timer.finish()

    warnings = list(result.warnings) + layout_warnings
    unassigned = [c.metadata() for c in result.unassigned()]
    overlap_pairs = outline_info["overlaps"]
    status = "OK"
    if overlap_pairs and mode == "nest":
        status = "OVERLAP"
        warnings.append(f"panels still overlap after nest layout: {overlap_pairs}")
    meta = RunMeta(
        run_id=run_id, input_path=str(input_path), input_name=input_path.name, input_sha256=result.input_sha256,
        units=result.units, v1_version=compat["v1_version"], v1_source_fingerprint=compat["v1_source_fingerprint"],
        config_hash=config_hash(config), cache_key=result.cache_key, cache_hit=result.cache_hit,
        panels=[{**result.panels[pid].metadata(), "placement": placements[pid].to_dict(),
                 "flatten": flatten_quality(result.panels[pid]),
                 "curve_counts": {fam: len(result.curves_for(pid, fam)) for fam in ("OUTER", "OBSTACLE", "FEATURE", "HINT", "ROBUST")}}
                for pid in sorted(result.panels)],
        layout_mode=mode,
        stage_timings_seconds={**{f"engine.{k}": v for k, v in result.stage_timings.items()}, **timer.timings},
        warnings=warnings,
        assignment_ambiguities=list(result.diagnostics.get("assignment_notes", [])),
        unassigned_curves=unassigned,
        teak=teak_info,
        outputs={k: v for k, v in outline_info.items() if isinstance(v, str)},
        status=status,
    )
    meta.write(run_dir / "run.json")
    _write_outline_report(run_dir / "outline_report.md", meta, result, placements, outline_info)
    return {
        "run_id": run_id, "run_dir": str(run_dir), "status": status, "cache_hit": result.cache_hit,
        "panels": {pid: result.panels[pid].role for pid in sorted(result.panels)},
        "overlaps": overlap_pairs, "unassigned_count": len(unassigned), "warnings": warnings,
        "teak": teak_info, "outline": outline_info,
    }


def _write_outline_report(path: Path, meta: RunMeta, result: EngineResult, placements: dict[int, PanelPlacement], info: dict[str, Any]) -> None:
    lines = [f"# AutoDeck2 outline report -- {meta.run_id}", ""]
    lines.append(f"- Scan: `{meta.input_name}` (sha256 {meta.input_sha256[:12]}...), units {meta.units}")
    lines.append(f"- Engine: AutoDeck v1 {meta.v1_version} (source {meta.v1_source_fingerprint}), AutoDeck2 {meta.v2_version}; cache {'HIT' if meta.cache_hit else 'MISS'} ({meta.cache_key})")
    lines.append(f"- Layout: {meta.layout_mode}; overlaps after layout: {len(info['overlaps'])}")
    lines.append(f"- Status: **{meta.status}**")
    lines.append("")
    lines.append("## Panels")
    lines.append("")
    lines.append("| panel | role | area m2 | boat XY (mm) | placed | flattening | tilt | area change | obstacles | features | hints |")
    lines.append("|---|---|---:|---|---|---|---:|---:|---:|---:|---:|")
    for pid in sorted(result.panels):
        p = result.panels[pid]; pl = placements[pid]
        placed = "moved to row" if pl.moved_to_row else ("nested" if pl.nested_in is not None else "true position")
        fq = flatten_quality(p)
        lines.append(f"| {pid} | {p.role} | {p.area_mm2 / 1e6:.2f} | ({p.world_centroid_mm[0]:.0f}, {p.world_centroid_mm[1]:.0f}) | {placed} | "
                     f"{fq['strategy']} | {fq.get('tilt_deg', 0.0):.1f} deg | {fq.get('area_change_percent', 0.0):+.2f}% | "
                     f"{len(result.curves_for(pid, 'OBSTACLE'))} | {len(result.curves_for(pid, 'FEATURE'))} | {len(result.curves_for(pid, 'HINT'))} |")
    lines.append("")
    lines.append("`RAW::PANEL_n::OUTER` is the detected floor-to-wall transition -- it IS the wall line. v1 has no separate wall curve family.")
    lines.append("")
    lines.append("Flattening is a true development: an angled-but-flat panel is measured in its own plane (rigid-planar, "
                 "proportions exact whatever the tilt); a curved panel is unrolled intrinsically (LSCM+ARAP). Nothing is "
                 "'projected to CPlane'. `area change` and edge strain say how far the flat drawing is from the 3D surface.")
    lines.append("")
    if meta.unassigned_curves:
        lines.append("## Unassigned features (on layer HINTS::UNASSIGNED, world position)")
        lines.append("")
        for c in meta.unassigned_curves:
            lines.append(f"- {c['curve_id']} ({c['family']}): {c['assignment'].get('note', '')}")
        lines.append("")
    if meta.teak.get("enabled"):
        dims = ", ".join(f"{k.replace('_mm', '')} {v:g} mm" for k, v in (meta.teak.get("dimensions_mm") or {}).items())
        lines.append(f"## Pattern: {meta.teak.get('pattern_label', 'Teak lines')} ({dims}); lines per panel {meta.teak.get('line_counts')}; "
                     f"boat axis confidence {meta.teak['frame'].get('axis_confidence', 0):.3f}")
        lines.append("")
    if meta.warnings:
        lines.append("## Warnings")
        lines.append("")
        lines.extend(f"- {w}" for w in meta.warnings)
        lines.append("")
    lines.append("## Next steps in Rhino")
    lines.append("")
    lines.extend([
        "1. Open `outline.3dm`. Reference layers are locked; `USER_CAM::PANEL_n` and `USER_CORNERS` are unlocked.",
        "2. For each panel draw the outline you want cut on `USER_CAM::PANEL_n` with Line and Arc tools only (any order/direction). Draw obstacle cut-outs as separate closed loops inside the outer loop.",
        "3. Where a sharp corner is intentional, put a Point on `USER_CORNERS` at the corner.",
        "4. Save the file (keep it as .3dm) and run AutoDeck2 -> Ingest, pointing at this run and the saved file.",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def cache_status() -> list[dict[str, Any]]:
    return cache_module.list_entries()
