import os
import sys
from pathlib import Path

os.environ.setdefault("AUTODECK2_IGNORE_LOCAL_CONFIG", "1")

# v1's editable-install .pth is not reliably processed on this machine.
_v1_src = Path.home() / "Documents" / "Codex" / "AutoDeck" / "src"
try:
    import autodeck  # noqa: F401
except ImportError:
    if _v1_src.is_dir():
        sys.path.insert(0, str(_v1_src))
