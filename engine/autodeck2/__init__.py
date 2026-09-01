"""AutoDeck v2: raw deck outline for hand-drawn CAM, with validation and calibration.

Phase 1 scope: scan -> developed/raw panel geometry -> laid-out outline.3dm
(RAW / ROBUST / HINTS / TEAK reference layers + empty USER_CAM layers) ->
manual Rhino line/arc drawing -> ingest -> validation + calibration
measurements -> authoritative final.dxf.  No automatic fitter.
"""

__version__ = "2.0.0a1"

# Bump whenever the on-disk shape of a cache entry or its meaning changes.
# 2: entries now include REF::ROBUST curves matched by (layer, name).
CACHE_SCHEMA_VERSION = 2

# The v1 engine this package is written against.  v1compat.check_compatibility
# refuses to run against anything else.
V1_REQUIRED_VERSION = "0.3.6"
