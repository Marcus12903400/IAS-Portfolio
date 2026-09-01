import os
import sys
from pathlib import Path

os.environ.setdefault("AUTODECK2_IGNORE_LOCAL_CONFIG", "1")

# v1's editable-install .pth is not reliably processed on every machine.
# engine-v1 sits beside this package in the bundle; AUTODECK_V1_ROOT wins when set.
_candidates = [
    Path(os.environ["AUTODECK_V1_ROOT"]) / "src" if os.environ.get("AUTODECK_V1_ROOT") else None,
    Path(__file__).resolve().parents[2] / "engine-v1" / "src",
]
try:
    import autodeck  # noqa: F401
except ImportError:
    for _candidate in _candidates:
        if _candidate is not None and _candidate.is_dir():
            sys.path.insert(0, str(_candidate))
            break
