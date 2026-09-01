# Testing

```bash
pytest
```

## Organization

- `tests/unit/` — fast, isolated pure-function tests (no mesh I/O). E.g.
  `test_cache.py`.
- `tests/geometry/` — synthetic-geometry construction and fitting tests:
  curve conditioning/flattening, curve-fit hotfix, polyarc CAM fitting,
  manual/pattern reconstruction, development/manufacturing. These build
  small synthetic point sets by hand rather than loading a real mesh.
- `tests/integration/` — full-pipeline tests using the synthetic-cockpit
  fixture (`tests/fixtures/synthetic-cockpit/scan.obj`): `analyze_scan`,
  `inspect_scan`, and production-output contract tests.
- `tests/regression/` — reserved for real-boat regression once a raw scan
  is available under `inputs/boats/`. Currently empty; see
  `reference-data/legacy/README.md` for why no live regression exists yet.
- `tests/fixtures/` — the one synthetic mesh fixture shared by tests.

## Critical CAM coverage (in `tests/geometry/test_v032_polyarc.py`)

- Straight noisy edge -> one line; large-radius circular edge -> one true arc
- Closed rectangle preserves 4 hard corners and closes exactly
- Tolerance cannot be configured above the locked 3.000 mm ceiling
- DXF native/polyline round-trip via `ezdxf`
- Line + tangent arc + line -> exact G1 (`_equal_distance_biarc`)
- Organic edge / S-curve stay low-primitive-count G1 polyarcs
- Notch/obstacle keep signed forbidden-side safety
- 2.9 mm accepted as one line; 3.1 mm forces a split (corridor boundary)
- Landmark dimension audit separates development change from CAM-fit change
- **Regression tests for the two fixed CAM bugs** (see
  `docs/CAM_GEOMETRY.md#historical-bugs-and-their-fixes-verified-in-this-repository`):
  forcing the tangent-losing-fallback condition raises/flags instead of
  emitting a silent kink; `map_points_to_indices` resolves by coordinate and
  fails loudly on a genuine mismatch instead of wrapping via modulo.

## Development/pattern coverage (`tests/geometry/test_v03_manufacturing.py`,
`test_v033_manual_patterns.py`)

Tilted (7°) rectangle recovers true physical size (not a top-view
projection), development preserves circular/rectangular holes, compound
curvature reports real nonzero distortion, teak/diamond/hex dimensions and
edge-sharing, rotated boat axis detected without manual angle entry,
obstacle clipping, cached pattern rerun doesn't repeat scan/development.

## Fixtures, not giant generated data

Synthetic tests build point arrays directly in the test file. The one real
fixture (`tests/fixtures/synthetic-cockpit/scan.obj`) is small (17 KB).
Real-boat regression evidence (once available) should stay compact —
expected-range metadata (area/obstacle-count ranges, expected status,
max join gap/tangent, expected output existence), not multi-gigabyte
generated artifacts committed into the test tree. See
`reference-data/legacy/` for how this project currently handles the
pre-V0.3.6 real-boat data it inherited (kept, but explicitly outside the
test tree and not runnable as a live regression).
