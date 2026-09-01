from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .config import ConfigError, load_config
from .preflight import PreflightError, cache_status_label, make_progress_printer, print_run_summary, run_preflight


def _print_polyarc_summary(
    payload: dict | None,
    preferred_file: str = "flattened_curves_polyarc.dxf",
    pattern: dict | None = None,
) -> None:
    if not payload:
        return
    metrics = payload.get("metrics", {})
    lengths = [float(value) for value in metrics.get("primitive_lengths_mm", [])]
    print("\nCAM PERIMETER\n")
    print(f"Hard corners: {int(metrics.get('hard_corner_count', 0))}")
    print(f"Lines: {int(payload.get('line_count', metrics.get('line_count', 0)))}")
    print(f"Arcs: {int(payload.get('arc_count', metrics.get('arc_count', 0)))}")
    print(f"Total primitives: {int(payload.get('primitive_count', metrics.get('primitive_count', 0)))}")
    if lengths:
        print(f"\nShortest primitive: {min(lengths):.3f} mm")
        print(f"Median primitive: {float(np.median(lengths)):.3f} mm")
        print(f"Longest primitive: {max(lengths):.3f} mm")
    print(f"\nMaximum fit deviation: {float(metrics.get('max_deviation_mm', float('nan'))):.6f} mm")
    print(f"P95 fit deviation: {float(metrics.get('p95_deviation_mm', float('nan'))):.6f} mm")
    print(f"\nMax tangent mismatch: {float(metrics.get('maximum_smooth_tangent_mismatch_deg', float('nan'))):.8f} deg")
    print(f"Forbidden violations: {int(metrics.get('forbidden_side_violations', 0))}")
    print(f"Self intersections: {int(bool(metrics.get('self_intersection', False)))}")
    print(f"Closed: {'YES' if payload.get('is_closed', False) else 'NO'}")
    print(f"\nStatus: {payload.get('status', 'NOT_EVALUATED')}")
    pattern = pattern or {"selected_label": "None", "entity_count": 0, "metrics": {}}
    frame = pattern.get("frame") or {}
    validation = pattern.get("metrics", {}).get("dimension_checks", {})
    print("\nPATTERN\n")
    print(f"Selected: {pattern.get('selected_label', 'None')}")
    print(f"Boat axis confidence: {float(frame.get('axis_confidence', 0.0)):.4f}")
    print(f"Pattern entities: {int(pattern.get('entity_count', 0))}")
    print(f"Pattern dimension validation: {'PASS' if validation.get('valid', pattern.get('selected') == 'none') else 'FAIL'}")
    print(f"\nPreferred output: {preferred_file}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodeck", description="Geometry-first marine decking proposals")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="Inspect OBJ/MTL/UV characteristics without modifying the scan")
    inspect_parser.add_argument("--input", type=Path, required=True)
    inspect_parser.add_argument("--output", type=Path, required=True)
    inspect_parser.add_argument("--units", choices=["mm", "cm", "m", "in"])
    analyze_parser = subparsers.add_parser("analyze", help="Automatically discover prepared upward-facing decking candidates")
    analyze_parser.add_argument("--input", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path, required=True)
    analyze_parser.add_argument("--units", choices=["mm", "cm", "m", "in"])
    analyze_parser.add_argument("--config", type=Path)
    analyze_parser.add_argument("--max-slope", type=float, help="maximum candidate slope in degrees from world X-Y (default: 25)")
    analyze_parser.add_argument("--analysis-resolution", type=float, help="physical analysis-mesh vertex-clustering resolution in mm (default: 2.5)")
    analyze_parser.add_argument(
        "--segmentation-mode",
        choices=["orientation", "structural-experimental", "advanced"],
        help=(
            "orientation production baseline (default) or the fragmentation-prone "
            "structural-experimental A/B diagnostic; advanced is a deprecated alias"
        ),
    )
    analyze_parser.add_argument("--up-axis", choices=["auto", "+X", "-X", "+Y", "-Y", "+Z", "-Z"], help="broad-analysis up direction; V0.2 top-view features require +Z")
    analyze_parser.add_argument("--seed-mode", choices=["automatic", "manual", "marker"], help="default is automatic; manual/marker preserve optional seeded workflows")
    analyze_parser.add_argument("--seed", nargs=3, type=float, action="append", metavar=("X", "Y", "Z"), default=[])
    analyze_parser.add_argument("--paper-center", nargs=3, type=float, action="append", metavar=("X", "Y", "Z"), default=[])
    analyze_parser.add_argument("--no-marker-detection", action="store_true")
    analyze_parser.add_argument("--pattern", choices=["none", "teak", "diamond", "hex"], default="none")
    reprocess_parser = subparsers.add_parser(
        "reprocess-manufacturing",
        help="rerun V0.3 curve fitting/DXF/back-projection from saved development artifacts",
    )
    reprocess_parser.add_argument("--output", type=Path, required=True)
    reprocess_parser.add_argument("--config", type=Path)
    reprocess_parser.add_argument("--pattern", choices=["none", "teak", "diamond", "hex"], default="none")
    pattern_parser = subparsers.add_parser(
        "pattern",
        help="change the pattern from cached V0.3.3 CAM artifacts without scan/development reprocessing",
    )
    pattern_parser.add_argument("--run", type=Path, required=True)
    pattern_parser.add_argument("--pattern", choices=["none", "teak", "diamond", "hex"], required=True)
    pattern_parser.add_argument("--config", type=Path)
    subparsers.add_parser(
        "wizard",
        help="interactive numbered prompts -- the normal way to run AutoDeck (also the default with no command)",
    )
    subparsers.add_parser("doctor", help="report AutoDeck version, environment, and project paths")
    cache_parser = subparsers.add_parser("cache", help="inspect or clear the disposable cache/ directory")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)
    cache_subparsers.add_parser("status", help="report cache/ entry count and size")
    clean_parser = cache_subparsers.add_parser("clean", help="remove cache/ entries (always safe; only slows the next run)")
    clean_parser.add_argument(
        "--older-than-days", type=float, default=None,
        help="only remove entries older than this many days (default: remove all)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        argv = ["wizard"]
    args = _parser().parse_args(argv)
    # The pipeline pulls in scipy/shapely/igl; it is imported only on the
    # branches that run it so `--help`, `doctor`, `cache` and the wizard menu
    # appear instantly (and so an iCloud-offloaded venv is noticed by the
    # launcher, not as a silent multi-minute hang here).
    try:
        if args.command == "wizard":
            from .wizard import run_wizard

            return run_wizard()
        if args.command == "doctor":
            from .doctor import run_doctor

            run_doctor()
            return 0
        if args.command == "cache":
            from . import cache as cache_module

            if args.cache_command == "status":
                entries = cache_module.list_entries()
                total = sum(entry.size_bytes for entry in entries)
                print(f"cache/ : {len(entries)} entries, {total / (1024 * 1024):.1f} MB")
                for entry in entries:
                    print(f"  {entry.key}  ({entry.size_bytes / (1024 * 1024):.1f} MB)")
                return 0
            older_than_seconds = (
                args.older_than_days * 86400.0 if args.older_than_days is not None else None
            )
            removed, freed = cache_module.clean(older_than_seconds)
            print(f"Removed {removed} cache entries, freed {freed / (1024 * 1024):.1f} MB.")
            return 0
        if args.command == "inspect":
            from .pipeline import inspect_scan

            result = inspect_scan(args.input, args.output, args.units)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "reprocess-manufacturing":
            from .reprocess import reprocess_manufacturing

            config = load_config(args.config)
            config["pattern"]["selected"] = args.pattern
            print("Processing (reusing saved development artifacts)...\n")
            result = reprocess_manufacturing(args.output, config)
            _print_polyarc_summary(result.get("primary_polyarc_summary"), result.get("preferred_file", "flattened_curves_polyarc.dxf"), result.get("pattern"))
            print(json.dumps(result, indent=2))
            return 3 if result["status"] == "INVALID" else 0
        if args.command == "pattern":
            from .reprocess import rerun_pattern

            config = load_config(args.config)
            config["pattern"]["selected"] = args.pattern
            print("Processing (reusing saved CAM artifacts)...\n")
            result = rerun_pattern(args.run, config)
            _print_polyarc_summary(result.get("primary_polyarc_summary"), result.get("preferred_file", "flattened_curves_polyarc.dxf"), result.get("pattern"))
            print(json.dumps(result, indent=2))
            return 3 if result.get("status") == "INVALID" else 0
        config = load_config(args.config)
        config["pattern"]["selected"] = args.pattern
        if args.max_slope is not None:
            if not 0.0 < args.max_slope < 90.0:
                raise ValueError("--max-slope must be greater than 0 and less than 90 degrees")
            config["orientation"]["max_slope_deg"] = float(args.max_slope)
            config["orientation"]["core_slope_deg"] = min(
                float(config["orientation"]["core_slope_deg"]), float(args.max_slope)
            )
        if args.analysis_resolution is not None:
            if args.analysis_resolution < 0.0:
                raise ValueError("--analysis-resolution must be zero or a positive millimeter value")
            config["mesh"]["analysis_resolution_mm"] = float(args.analysis_resolution)
        segmentation_mode = args.segmentation_mode or str(config["segmentation"].get("mode", "orientation"))
        preflight_warnings = run_preflight(args.input, args.output, config)
        for warning in preflight_warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        print_run_summary(
            input_path=args.input,
            units=args.units or "mm (default)",
            up_axis=args.up_axis or "auto",
            segmentation_mode=segmentation_mode,
            pattern=args.pattern,
            cache_status=cache_status_label(args.input, config),
            output_dir=args.output,
        )
        print("Loading geometry libraries...")
        from .pipeline import analyze_scan

        print("Processing -- this can take several minutes on a real scan.\n")
        result = analyze_scan(
            args.input, args.output, config, args.units,
            [np.asarray(point, dtype=float) for point in args.seed],
            [np.asarray(point, dtype=float) for point in args.paper_center],
            not args.no_marker_detection,
            args.seed_mode,
            args.segmentation_mode,
            args.up_axis,
            make_progress_printer(),
        )
        print()
        proposal = result["proposal"]
        candidates = result["candidates"]
        polyarc_curves = result.get("metrics", {}).get("polyarc_fit", {}).get("curves", [])
        pattern = result.get("metrics", {}).get("pattern", {})
        production = result.get("metrics", {}).get("production_output", {})
        preferred = (
            "final.dxf"
            if production.get("final_dxf_written")
            else production.get("failure_notice", "FINAL_NOT_READY.txt")
        )
        _print_polyarc_summary(polyarc_curves[0] if polyarc_curves else None, preferred, pattern)
        if "FAILURE" in result["status"]:
            prominent = [
                warning for warning in result.get("warnings", [])
                if warning.startswith(("SEGMENTATION FAILURE:", "EXPORT FAILURE:", "FEATURE/EXPORT FAILURE:"))
            ]
            if prominent:
                print("\n" + "\n".join(prominent) + "\n", file=sys.stderr)
        print(json.dumps({
            "status": result["status"],
            "candidate_count": len(candidates),
            "primary_candidate": None if not candidates else {
                "candidate_id": candidates[0].candidate_id,
                "area_mm2": candidates[0].area_mm2,
                "average_slope_deg": candidates[0].mean_slope_deg,
                "p95_slope_deg": candidates[0].p95_slope_deg,
                "confidence": candidates[0].confidence,
                "confidence_label": candidates[0].confidence_label,
                "outer_loops": len(candidates[0].outer_loops),
                "internal_boundaries": len(candidates[0].internal_boundaries),
            },
            "output": result["outputs"],
        }, indent=2))
        return 3 if "FAILURE" in result["status"] else 0
    except (OSError, ValueError, ConfigError, PreflightError) as exc:
        print(f"autodeck: error: {exc}", file=sys.stderr)
        return 2
