# tests/unit/

Fast, isolated pure-function tests (no mesh I/O, no synthetic geometry
construction) — e.g. `test_cache.py`. The existing suite's fine-grained
geometry tests live under `tests/geometry/` since they exercise real
geometry construction rather than pure logic.
