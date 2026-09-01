#!/usr/bin/env python3
"""Clean disposable data from an AutoDeck project checkout.

Operates ONLY on this project (the directory this script's parent lives in),
never on any other path. Never touches inputs/, src/, config/, or
reference-data/ -- those require explicit user action, not this tool.

Usage:
    python scripts/clean_workspace.py --dry-run [--keep-last N]
    python scripts/clean_workspace.py --apply   [--keep-last N]

Categories cleaned:
    python-caches   __pycache__/, *.pyc, .pytest_cache/, .mypy_cache/, .ruff_cache/, .DS_Store
    cache           contents of cache/ (never the reference-data/ tree)
    old-runs        outputs/runs/* beyond the newest --keep-last (default: keep all)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Removal:
    path: Path
    category: str
    size_bytes: int


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


_SKIP_DIRS = {".venv", "venv", ".git"}
_CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _walk_project(root: Path):
    """Yield every file/dir under root, without descending into .venv/.git
    or into a recognized cache directory once it has itself been yielded
    (its contents are removed as a unit, not individually)."""

    for path in root.iterdir():
        if path.is_dir():
            if path.name in _SKIP_DIRS:
                continue
            yield path
            if path.name not in _CACHE_DIR_NAMES:
                yield from _walk_project(path)
        else:
            yield path


def _find_python_caches(root: Path) -> list[Removal]:
    # Python caches are always safe to remove, even inside otherwise-protected
    # top-level directories (src/, tests/) -- only the real source content of
    # those directories is protected, not their incidental __pycache__ litter.
    # .venv/ itself is skipped entirely: it's a regenerable environment, not
    # project cache, and cleaning inside it here would be pointless churn.
    removals: list[Removal] = []
    for path in _walk_project(root):
        if path.name in _CACHE_DIR_NAMES and path.is_dir():
            removals.append(Removal(path, "python-caches", _dir_size(path)))
        elif path.name == ".DS_Store" and path.is_file():
            removals.append(Removal(path, "python-caches", _dir_size(path)))
        elif path.suffix == ".pyc" and path.is_file():
            removals.append(Removal(path, "python-caches", _dir_size(path)))
    return removals


def _find_cache_contents(root: Path) -> list[Removal]:
    cache_dir = root / "cache"
    if not cache_dir.is_dir():
        return []
    removals = []
    for child in cache_dir.iterdir():
        if child.name == "README.md":
            continue
        removals.append(Removal(child, "cache", _dir_size(child)))
    return removals


def _find_old_runs(root: Path, keep_last: int | None) -> list[Removal]:
    if keep_last is None:
        return []
    runs_dir = root / "outputs" / "runs"
    if not runs_dir.is_dir():
        return []
    runs = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [Removal(p, "old-runs", _dir_size(p)) for p in runs[keep_last:]]


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="report what would be removed, remove nothing")
    mode.add_argument("--apply", action="store_true", help="actually remove the listed items")
    parser.add_argument(
        "--keep-last", type=int, default=None,
        help="also prune outputs/runs/ down to the N most recently modified runs",
    )
    args = parser.parse_args()

    removals: list[Removal] = []
    removals += _find_python_caches(PROJECT_ROOT)
    removals += _find_cache_contents(PROJECT_ROOT)
    removals += _find_old_runs(PROJECT_ROOT, args.keep_last)

    if not removals:
        print("Nothing to clean.")
        return 0

    by_category: dict[str, list[Removal]] = {}
    for item in removals:
        by_category.setdefault(item.category, []).append(item)

    total = 0
    for category, items in sorted(by_category.items()):
        category_total = sum(item.size_bytes for item in items)
        total += category_total
        print(f"\n[{category}] {len(items)} item(s), {_format_bytes(category_total)}")
        for item in items:
            print(f"  {item.path.relative_to(PROJECT_ROOT)}  ({_format_bytes(item.size_bytes)})")

    print(f"\nTOTAL: {len(removals)} item(s), {_format_bytes(total)}")

    if args.dry_run:
        print("\nDry run -- nothing removed. Re-run with --apply to remove these.")
        return 0

    for item in removals:
        if item.path.is_dir():
            shutil.rmtree(item.path, ignore_errors=True)
        elif item.path.exists():
            item.path.unlink()
    print(f"\nRemoved {len(removals)} item(s), freed {_format_bytes(total)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
