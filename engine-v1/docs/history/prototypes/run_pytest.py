"""Compatibility entry point for the repository's locally vendored pytest."""

from __future__ import annotations

import sys

from _pytest.config import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
