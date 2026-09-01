"""``python -m autodeck doctor`` -- a quick project-health report.

Answers "is my environment sane" without manual filesystem hunting: version,
interpreter, the paths AutoDeck actually resolves to, whether the important
dependencies import, free disk space, and cache size.
"""

from __future__ import annotations

import shutil
import sys

from . import __version__, paths


def _dir_size(path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def run_doctor() -> None:
    print(f"AutoDeck version:  {__version__}")
    print(f"Python:            {sys.version.split()[0]} ({sys.executable})")
    print()
    print(f"Project root:      {paths.project_root()}")
    print(f"Config:            {paths.default_config_path()} (exists: {paths.default_config_path().is_file()})")
    print(f"                   local override: {paths.local_config_path()} (exists: {paths.local_config_path().is_file()})")
    print(f"Inputs:            {paths.boats_dir()}")
    print(f"Cache:             {paths.cache_dir()} ({_format_bytes(_dir_size(paths.cache_dir()))})")
    print(f"Outputs:           {paths.runs_dir()}")
    print(f"Reference data:    {paths.reference_data_dir()} ({_format_bytes(_dir_size(paths.reference_data_dir()))})")
    print()
    print("Dependencies:")
    for module_name in ("numpy", "scipy", "ezdxf", "shapely", "igl", "PIL"):
        try:
            module = __import__(module_name)
            version = getattr(module, "__version__", "unknown")
            print(f"  {module_name:10s} OK ({version})")
        except ImportError as exc:
            print(f"  {module_name:10s} MISSING ({exc})")
    for module_name in ("rhino3dm", "cv2"):
        try:
            module = __import__(module_name)
            version = getattr(module, "__version__", "unknown")
            print(f"  {module_name:10s} OK ({version}, optional)")
        except ImportError:
            print(f"  {module_name:10s} not installed (optional)")
    print()
    usage = shutil.disk_usage(paths.project_root())
    print(f"Free disk space:   {_format_bytes(usage.free)} of {_format_bytes(usage.total)}")
