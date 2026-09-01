"""Pre-run checks and the confirmation summary printed before expensive
processing starts.

The goal is to catch a mistake (missing input, unwritable output, a config
typo, a missing optional dependency) in under a second, rather than forty
minutes into mesh analysis. See spec sections 60-61 in the V0.3.6 plan.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any, Callable

from . import paths


class PreflightError(Exception):
    """A problem that must be fixed before AutoDeck starts real work."""


_REQUIRED_MODULES = ("numpy", "scipy", "ezdxf", "shapely")


def run_preflight(input_path: Path, output_dir: Path, config: dict[str, Any]) -> list[str]:
    """Validate everything cheap to check before expensive processing.
    Raises PreflightError with an actionable message on a hard failure;
    returns a list of non-fatal warnings (e.g. plausibility checks) otherwise.
    """

    warnings: list[str] = []

    if not input_path.exists():
        raise PreflightError(f"INPUT MISSING: {input_path} does not exist.")
    if input_path.suffix.lower() != ".obj":
        warnings.append(f"INPUT UNEXPECTED EXTENSION: {input_path.suffix!r} (AutoDeck expects .obj)")
    else:
        try:
            with input_path.open("r", encoding="utf-8", errors="replace") as handle:
                has_vertex = any(line.startswith(("v ", "v\t")) for _, line in zip(range(2000), handle))
        except OSError as exc:
            raise PreflightError(f"INPUT UNREADABLE: could not open {input_path}: {exc}") from exc
        if not has_vertex:
            raise PreflightError(
                f"INPUT UNITS IMPLAUSIBLE: {input_path} does not look like a valid OBJ mesh "
                "(no 'v ' vertex lines found in the first 2000 lines)."
            )

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".autodeck_write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise PreflightError(f"OUTPUT NOT WRITABLE: {output_dir}: {exc}") from exc

    for module_name in _REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            raise PreflightError(
                f"DEPENDENCY MISSING: required package {module_name!r} is not importable "
                f"({exc}). Run `pip install -e .` in an AutoDeck venv."
            ) from exc

    up_axis = str(config.get("up_axis", {}).get("default", "+Z")) if isinstance(config.get("up_axis"), dict) else "+Z"
    if up_axis not in {"+Z", "auto"}:
        warnings.append(
            f"UP AXIS LOOKS UNUSUAL: config default up_axis={up_axis!r}. The normal Vega/Rhino "
            "workflow expects the deck plane already oriented to +Z -- confirm this is intentional."
        )

    return warnings


def print_run_summary(
    *,
    input_path: Path,
    units: str,
    up_axis: str,
    segmentation_mode: str,
    pattern: str,
    cache_status: str,
    output_dir: Path,
) -> None:
    print("INPUT:")
    print(f"  {input_path}")
    print("UNITS:")
    print(f"  {units}")
    print("UP AXIS:")
    print(f"  {up_axis}")
    print("SEGMENTATION:")
    print(f"  {segmentation_mode}")
    print("PATTERN:")
    print(f"  {pattern}")
    print("CACHE:")
    print(f"  {cache_status}")
    print("OUTPUT:")
    print(f"  {output_dir}")
    print()


def cache_status_label(input_path: Path, config: dict[str, Any]) -> str:
    """Best-effort human label for the run summary; not a hard dependency of
    caching itself. Returns 'miss' if cache/ has nothing plausibly matching
    this input yet, 'hit' style detail is left to the cache module itself."""

    if not paths.cache_dir().is_dir() or not any(paths.cache_dir().iterdir()):
        return "miss (cache/ is empty)"
    return "unknown (run `python -m autodeck cache status` for detail)"


def make_progress_printer() -> Callable[[str], None]:
    """A progress callback for analyze_scan(): prints each pipeline stage as
    it starts, with elapsed time since the previous one. This is the only
    feedback a user gets during a run that can take minutes on a real scan
    -- without it, the wizard/CLI looks hung."""

    state = {"last": time.perf_counter()}

    def _print_stage(stage: str) -> None:
        now = time.perf_counter()
        elapsed = now - state["last"]
        state["last"] = now
        print(f"  [+{elapsed:5.1f}s] {stage}...")

    return _print_stage
