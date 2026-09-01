"""Interactive numbered wizard -- the primary way a user runs AutoDeck.

Every question is answered with a number key (Enter accepts the default).
This module is a thin front end: it builds the same config dict and calls
the same analyze_scan() pipeline function that the argparse `analyze`
subcommand calls (see cli.py). There is exactly one implementation of
AutoDeck processing behind these two entry points.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .config import ConfigError, load_config
from .preflight import PreflightError, cache_status_label, make_progress_printer, print_run_summary, run_preflight


def _ask_choice(prompt: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    """options: list of (value, label). Returns the chosen value.
    Pressing Enter with no input accepts the default option."""

    print(f"\n{prompt}")
    for index, (_, label) in enumerate(options, 1):
        marker = " (default)" if index - 1 == default_index else ""
        print(f"  [{index}] {label}{marker}")
    while True:
        raw = input("> ").strip()
        if not raw:
            return options[default_index][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print(f"Enter a number from 1 to {len(options)}.")


def _ask_path(prompt: str, default: Path | None = None) -> Path:
    print(f"\n{prompt}")
    if default is not None:
        print(f"  (press Enter for: {default})")
    while True:
        raw = input("> ").strip()
        if not raw and default is not None:
            return default
        if raw:
            return Path(raw).expanduser()
        print("Enter a path.")


def _discover_boat_scans() -> list[Path]:
    boats_dir = paths.boats_dir()
    if not boats_dir.is_dir():
        return []
    return sorted(boats_dir.glob("*/*.obj"))


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def run_wizard() -> int:
    try:
        return _run_wizard()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 0


def _run_wizard() -> int:
    print("AutoDeck -- numbered wizard\n")
    print("Answer each question with a number. Press Enter to accept the default.")

    scans = _discover_boat_scans()
    if scans:
        options = [
            (str(path), str(path.relative_to(paths.project_root())))
            for path in scans
        ]
        options.append(("__manual__", "Enter a path manually"))
        choice = _ask_choice("Select a boat scan:", options)
        input_path = (
            _ask_path("Enter the path to the OBJ scan:")
            if choice == "__manual__" else Path(choice)
        )
    else:
        print("\nNo scans found under inputs/boats/.")
        input_path = _ask_path("Enter the path to the OBJ scan:")

    units = _ask_choice(
        "Scan units:",
        [
            ("mm", "Millimeters (Vega/Rhino default)"),
            ("cm", "Centimeters"),
            ("m", "Meters"),
            ("in", "Inches"),
        ],
    )

    up_axis = _ask_choice(
        "Up axis (the deck plane should already be oriented to +Z in Rhino):",
        [
            ("+Z", "+Z (normal workflow)"),
            ("auto", "Auto-detect (scan orientation unconfirmed)"),
        ],
    )

    segmentation_mode = _ask_choice(
        "Segmentation mode:",
        [
            ("orientation", "Orientation (production baseline)"),
            (
                "structural-experimental",
                "Structural-experimental (diagnostic only -- fragments floors, not for production)",
            ),
        ],
    )

    pattern = _ask_choice(
        "PATTERN:",
        [
            ("none", "None"),
            ("teak", "Teak Lines"),
            ("diamond", "Diamond"),
            ("hex", "Hexagon"),
        ],
    )

    default_output = paths.runs_dir() / f"{input_path.stem}-{_timestamp()}"
    output_dir = _ask_path("Output directory:", default=default_output)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"\nautodeck: error: {exc}")
        return 2
    config["pattern"]["selected"] = pattern

    try:
        preflight_warnings = run_preflight(input_path, output_dir, config)
    except PreflightError as exc:
        print(f"\nautodeck: error: {exc}")
        return 2
    for warning in preflight_warnings:
        print(f"WARNING: {warning}")

    print_run_summary(
        input_path=input_path,
        units=units,
        up_axis=up_axis,
        segmentation_mode=segmentation_mode,
        pattern=pattern,
        cache_status=cache_status_label(input_path, config),
        output_dir=output_dir,
    )
    proceed = _ask_choice("Proceed?", [("yes", "Yes"), ("no", "No, cancel")])
    if proceed == "no":
        print("Cancelled.")
        return 0

    print("\nLoading geometry libraries...")
    from .pipeline import analyze_scan

    print("Processing -- this can take several minutes on a real scan.\n")
    result = analyze_scan(
        input_path, output_dir, config, units,
        [], [], True, None, segmentation_mode, up_axis,
        make_progress_printer(),
    )
    print(f"\nStatus: {result['status']}")
    print(f"Output: {result['outputs']}")
    return 3 if "FAILURE" in result["status"] else 0
