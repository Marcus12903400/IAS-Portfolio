"""Numbered wizard (default) plus explicit subcommands.  Both call the same
pipeline functions; there is one implementation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, paths
from .config import ConfigError, load_config
from .progress import make_progress_printer


def _ask_choice(prompt: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    print(f"\n{prompt}")
    for index, (_, label) in enumerate(options, 1):
        print(f"  [{index}] {label}{' (default)' if index - 1 == default_index else ''}")
    while True:
        raw = input("> ").strip()
        if not raw:
            return options[default_index][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print(f"Enter a number from 1 to {len(options)}.")


def _ask_path(prompt: str, default: Path | None = None, must_exist: bool = True) -> Path:
    print(f"\n{prompt}")
    if default is not None:
        print(f"  (press Enter for: {default})")
    while True:
        raw = input("> ").strip().strip('"').strip("'")
        candidate = default if (not raw and default is not None) else (Path(raw).expanduser() if raw else None)
        if candidate is None:
            print("Enter a path.")
            continue
        if must_exist and not candidate.exists():
            print(f"Not found: {candidate}")
            continue
        return candidate


def _discover_scans() -> list[Path]:
    scans = sorted(paths.boats_dir().glob("*/*.obj")) if paths.boats_dir().is_dir() else []
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        scans += sorted(p for p in downloads.glob("*.obj") if p.stat().st_size > 1_000_000)
    return scans


def _recent_runs() -> list[Path]:
    if not paths.runs_dir().is_dir():
        return []
    runs = [p for p in paths.runs_dir().iterdir() if p.is_dir() and (p / "outline.3dm").is_file()]
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)[:8]


def wizard() -> int:
    try:
        return _wizard()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 0


def _wizard() -> int:
    print("AutoDeck2 -- numbered wizard (answer with a number; Enter accepts the default)")
    action = _ask_choice("What do you want to do?", [
        ("outline", "Make a raw outline from a scan (outline.3dm to draw on)"),
        ("autofit", "Auto-fit the CAM outline (lines first, then tangent arcs) -> final_auto.dxf"),
        ("ingest", "Ingest my drawn outline.3dm -> validate -> final.dxf + calibration"),
        ("doctor", "Check the environment"),
    ])
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"autodeck2: error: {exc}")
        return 2
    if action == "doctor":
        return doctor()
    if action == "autofit":
        runs = _recent_runs()
        if runs:
            run = _ask_choice("Which run?", [(str(r), r.name) for r in runs] + [("__manual__", "Enter a run folder path")])
            run_dir = _ask_path("Run folder:") if run == "__manual__" else Path(run)
        else:
            run_dir = _ask_path("Run folder (outputs/runs/<id>):")
        return _do_autofit(run_dir, config)
    if action == "ingest":
        runs = _recent_runs()
        if runs:
            run = _ask_choice("Which run produced the outline you drew on?", [(str(r), r.name) for r in runs] + [("__manual__", "Enter a run folder path")])
            run_dir = _ask_path("Run folder:") if run == "__manual__" else Path(run)
        else:
            run_dir = _ask_path("Run folder (outputs/runs/<id>):")
        default_drawing = run_dir / "outline.3dm"
        drawing = _ask_path("Path to the .3dm you drew on:", default=default_drawing)
        return _do_ingest(run_dir, drawing, config)

    scans = _discover_scans()
    if scans:
        choice = _ask_choice("Select a scan:", [(str(s), f"{s.name}  ({s.stat().st_size / 1e6:.0f} MB, {s.parent})") for s in scans] + [("__manual__", "Enter a path manually")])
        scan = _ask_path("Path to the OBJ scan:") if choice == "__manual__" else Path(choice)
    else:
        scan = _ask_path("Path to the OBJ scan:")
    units = _ask_choice("Scan units:", [("mm", "Millimetres (Vega/Rhino default)"), ("cm", "Centimetres"), ("m", "Metres"), ("in", "Inches")])
    layout = _ask_choice("Panel layout:", [
        ("nest", "Nest: overlapping/nested panels moved to a row below the deck (guaranteed no overlap)"),
        ("boat-plan", "Boat plan: true relative positions (nested panels stay in place)"),
    ], 0 if config["layout"].get("mode", "nest") == "nest" else 1)
    pattern = _ask_choice("Surface pattern?", [
        ("teak", "Teak lines (63.5 mm on centre)"),
        ("diamond", "Diamond stitch (152.4 x 76.2 mm)"),
        ("hex", "Hexagons (152.4 mm across flats)"),
        ("none", "None"),
    ], 0 if config["teak"].get("enabled_default", True) else 3)
    run_dir = paths.runs_dir() / f"{scan.stem}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    print("\nINPUT:      ", scan)
    print("UNITS:      ", units)
    print("LAYOUT:     ", layout)
    print("PATTERN:    ", pattern)
    print("OUTPUT:     ", run_dir)
    if _ask_choice("Proceed?", [("yes", "Yes"), ("no", "No, cancel")]) == "no":
        print("Cancelled.")
        return 0
    return _do_outline(scan, units, layout, pattern, config, run_dir)


def _do_outline(scan: Path, units: str, layout: str, pattern: str | bool, config: dict, run_dir: Path | None) -> int:
    from .pipeline import run_outline

    if isinstance(pattern, bool):
        pattern = "teak" if pattern else "none"
    print("\nProcessing -- a real scan takes a few minutes the first time (cached afterwards).\n")
    summary = run_outline(scan, config, units=units, layout_mode=layout, pattern=pattern, run_dir=run_dir, progress=make_progress_printer())
    print(f"\nStatus: {summary['status']}")
    print(f"Panels: {summary['panels']}")
    print(f"Overlaps after layout: {summary['overlaps'] or 'none'}")
    print(f"Unassigned features: {summary['unassigned_count']}")
    print(f"\nOpen in Rhino:\n  {summary['outline']['outline_3dm']}")
    print(f"Report:\n  {Path(summary['run_dir']) / 'outline_report.md'}")
    return 0 if summary["status"] == "OK" else 3


def _do_ingest(run_dir: Path, drawing: Path, config: dict) -> int:
    from .ingest import IngestError, ingest_run

    try:
        result = ingest_run(run_dir, drawing, config)
    except IngestError as exc:
        print(f"autodeck2: error: {exc}")
        return 2
    print(f"\nStatus: {result.status}")
    for rejection in result.rejections:
        print(f"  - {rejection}")
    for pid, panel in result.panels.items():
        for problem in panel.problems:
            print(f"  - panel {pid}: {problem}")
        for loop in panel.loops_report:
            s = loop["stats"]
            print(f"  panel {pid} {loop['kind']}: {s['primitive_count']} primitives ({s['line_count']}L/{s['arc_count']}A), "
                  f"{s['tangent_failure_count']} tangent failures, {s['intentional_corner_count']} intentional corners")
            for problem in loop.get("problems", []):
                print(f"    - {problem}")
    for panel in (result.summary.get("analysis") or {}).get("panels", []):
        t = panel["training_summary"]; j = t["joins"]; d = t["signed_deviation_mm"]
        print(f"\nWhat panel {panel['panel_id']} teaches: {t['primitives']} primitives ({t['lines']}L/{t['arcs']}A), "
              f"{t['primitives_per_metre']:.1f}/m; arc radius {t['arc_radius_mm']['min']:.0f}-{t['arc_radius_mm']['max']:.0f} mm; "
              f"joins {j['marked_corner']} corners / {j['tangent']} tangent / {j['near_tangent']} near-tangent / {j['unmarked_corner']} unmarked corners; "
              f"deviation median {d['median']:+.1f} mm (p5 {d['p5']:+.1f}, p95 {d['p95']:+.1f}); {panel['open_chain_count']} open chains")
    if result.final_dxf:
        print(f"\nfinal.dxf:\n  {result.final_dxf}")
    print(f"Reports:\n  {run_dir / 'calibration_report.md'}\n  {run_dir / 'final_report.md'}")
    return 0 if result.status == "VALID" else 3


def _do_autofit(run_dir: Path, config: dict) -> int:
    from .autofit import fit_run

    print("\nFitting: lines first, then tangent arcs (max 3 per connection), corners where the shape turns.\n")
    try:
        report = fit_run(run_dir, config, progress=make_progress_printer())
    except FileNotFoundError as exc:
        print(f"autodeck2: error: {exc}")
        return 2
    print(f"\nStatus: {report['status']}")
    for panel in report["panels"]:
        for loop in panel["loops"]:
            print(f"  panel {panel['panel_id']} {loop['kind']}: {loop['line_count']} lines + {loop['arc_count']} arcs, "
                  f"{loop['corner_count']} corners, max dev {loop['max_deviation_mm']:.2f} mm"
                  + (f", {len(loop['flagged'])} flagged" if loop["flagged"] else ""))
        for problem in panel["problems"]:
            print(f"  panel {panel['panel_id']}: {problem}")
    if report.get("final_auto_dxf"):
        print(f"\nfinal_auto.dxf:\n  {run_dir / 'final_auto.dxf'}")
    print(f"Inspect in Rhino:\n  {report['auto_cam_3dm']}\nReport:\n  {run_dir / 'autofit_report.md'}")
    return 0 if report["status"] == "VALID" else 3


def doctor() -> int:
    from . import v1compat
    print(f"AutoDeck2 {__version__}  Python {sys.version.split()[0]}")
    print(f"Project: {paths.project_root()}")
    try:
        info = v1compat.check_compatibility()
        print(f"AutoDeck v1: {info['v1_version']} at {info['v1_source_dir']} (fingerprint {info['v1_source_fingerprint']})")
    except Exception as exc:  # noqa: BLE001 - report everything
        print(f"AutoDeck v1: NOT COMPATIBLE -- {exc}")
        return 2
    import rhino3dm, ezdxf, shapely, numpy  # noqa: E401
    print(f"rhino3dm {rhino3dm.__version__}, ezdxf {ezdxf.__version__}, shapely {shapely.__version__}, numpy {numpy.__version__}")
    from .cache import list_entries
    entries = list_entries()
    print(f"cache/: {len(entries)} entries, {sum(e['size_bytes'] for e in entries) / 1e6:.1f} MB")
    print(f"runs/: {paths.runs_dir()}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodeck2", description="AutoDeck2 -- raw outline for hand-drawn CAM")
    parser.add_argument("--version", action="version", version=f"autodeck2 {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("wizard", help="numbered prompts (default)")
    outline = sub.add_parser("outline", help="scan -> outline.3dm")
    outline.add_argument("--input", type=Path, required=True)
    outline.add_argument("--units", choices=["mm", "cm", "m", "in"], default=None)
    outline.add_argument("--layout", choices=["nest", "boat-plan"], default=None)
    outline.add_argument("--no-teak", action="store_true", help="same as --pattern none")
    outline.add_argument("--pattern", choices=["teak", "diamond", "hex", "none"], default=None)
    outline.add_argument("--output", type=Path, default=None, help="run directory (default outputs/runs/<id>)")
    outline.add_argument("--config", type=Path, default=None)
    ingest = sub.add_parser("ingest", help="drawn .3dm -> final.dxf + calibration")
    ingest.add_argument("--run", type=Path, required=True)
    ingest.add_argument("--drawing", type=Path, required=True)
    ingest.add_argument("--config", type=Path, default=None)
    autofit = sub.add_parser("autofit", help="auto-fit lines-then-arcs CAM outline for a run -> final_auto.dxf")
    autofit.add_argument("--run", type=Path, required=True)
    autofit.add_argument("--config", type=Path, default=None)
    sub.add_parser("doctor", help="environment check")
    cache = sub.add_parser("cache", help="cache status|clean")
    cache.add_argument("action", choices=["status", "clean"])
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return wizard()
    args = _parser().parse_args(argv)
    if args.command in (None, "wizard"):
        return wizard()
    if args.command == "doctor":
        return doctor()
    if args.command == "cache":
        from .cache import clean, list_entries
        if args.action == "status":
            for entry in list_entries():
                print(f"{entry['key']}  {entry['size_bytes'] / 1e6:.1f} MB  {'complete' if entry['complete'] else 'INCOMPLETE'}")
            print(f"{len(list_entries())} entries")
            return 0
        removed, freed = clean()
        print(f"Removed {removed} entries, freed {freed / 1e6:.1f} MB.")
        return 0
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"autodeck2: error: {exc}", file=sys.stderr)
        return 2
    if args.command == "outline":
        layout = args.layout or str(config["layout"].get("mode", "nest"))
        pattern = "none" if args.no_teak else (args.pattern or "teak")
        return _do_outline(args.input, args.units or "mm", layout, pattern, config, args.output)
    if args.command == "ingest":
        return _do_ingest(args.run, args.drawing, config)
    if args.command == "autofit":
        return _do_autofit(args.run, config)
    return 2
