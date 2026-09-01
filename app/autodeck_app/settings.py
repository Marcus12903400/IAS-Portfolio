"""Where things live.  Everything is overridable with environment variables so
the folder can be moved or the engine relocated without touching code."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
AUTODECK2_ROOT = Path(os.environ.get("AUTODECK2_ROOT", Path.home() / "AutoDeck2")).expanduser()
V1_ROOT = Path(os.environ.get("AUTODECK_V1_ROOT", Path.home() / "Documents" / "Codex" / "AutoDeck")).expanduser()
INPUTS_DIR = Path(os.environ.get("AUTODECK_INPUTS_DIR", APP_ROOT / "inputs")).expanduser()
PREVIEW_CACHE_DIR = Path(os.environ.get("AUTODECK_PREVIEW_CACHE", APP_ROOT / "cache" / "preview")).expanduser()
# Runs stay with the engine so the CLI, the wizard and the app all see the same runs.
RUNS_DIR = Path(os.environ.get("AUTODECK_RUNS_DIR", AUTODECK2_ROOT / "outputs" / "runs")).expanduser()
HOST = os.environ.get("AUTODECK_HOST", "127.0.0.1")
PORT = int(os.environ.get("AUTODECK_PORT", "8765"))
PREVIEW_TARGET_FACES = int(os.environ.get("AUTODECK_PREVIEW_FACES", "250000"))


def ensure_engine_on_path() -> None:
    """The launchers set PYTHONPATH; running `python -m autodeck_app` by hand
    from an activated venv should work too."""

    for candidate in (V1_ROOT / "src", AUTODECK2_ROOT):
        text = str(candidate)
        if candidate.is_dir() and text not in sys.path:
            sys.path.insert(0, text)


def ensure_dirs() -> None:
    for directory in (INPUTS_DIR, PREVIEW_CACHE_DIR, RUNS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
