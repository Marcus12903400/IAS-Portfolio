# Architecture

## Pipeline stages

```
prepared boat mesh (OBJ, mm, +Z up)
        |
        v
mesh_io.load_obj / mesh_statistics        -- parse, sanity-check
        |
        v
up_axis.assess_up_axes / resolve_up_axis  -- confirm/detect +Z
        |
        v
preprocessing / boundaries / curvature    -- adjacency, geometry fields
        |
        v
boundary.calculate_boundary_field         -- wall/floor transition evidence
orientation.calculate_orientation_field   -- upward-facing slope field
        |
        v
segmentation / candidates                  -- deckable-surface candidates
        |         (orientation mode is the production baseline;
        |          structural-experimental is diagnostic only --
        |          see docs/SEGMENTATION.md)
        v
conditioning                               -- raw boundary smoothing, RAW_3D preserved
        |
        v
development / flattening                  -- unroll to true flat dimensions,
        |                                     not a top-view Z-drop projection
        |                                     (see docs/SURFACE_DEVELOPMENT.md)
        v
curve_fit / robust_reference / polyarc     -- CAM LINE/ARC reconstruction
        |                                     (see docs/CAM_GEOMETRY.md)
        v
patterns                                   -- optional teak/diamond/hex
        |                                     (see docs/PATTERNS.md)
        v
dxf_export / rhino_export / v03_export     -- verification.3dm, flattened DXFs
```

`analysis_pipeline.py::analyze_scan()` is the top-level orchestrator; both
`cli.py`'s `analyze` subcommand and `wizard.py` call it. `reprocess.py`
provides two faster re-entry points that skip the expensive early stages by
reading back a previous run's saved artifacts:

- `reprocess_manufacturing()` — rerun CAM fitting/DXF/back-projection from
  saved development artifacts (used by `autodeck reprocess-manufacturing`).
- `rerun_pattern()` — regenerate only pattern artifacts from saved CAM
  primitives (used by `autodeck pattern`), without rescanning or
  re-developing.

## Verification architecture

Both the 3-D and flat-space verification exports retain every stage's
output side by side — the cleaned/reconstructed geometry never replaces the
raw evidence:

- **3-D** (`verification.3dm`): `RAW_3D`, `CONDITIONED_3D`,
  `CAM_BACKPROJECTED_3D`.
- **Flat** (`flattened_preview.3dm`): `FLAT_RAW_DEVELOPED`,
  `ROBUST_REFERENCE`, `CAM_POLYARC`.

## Status vocabularies (deliberately separate)

- **Mathematical geometry status**: `GOOD` / `NEEDS_REVIEW` / `INVALID`
  (or, inside `polyarc.py`'s own curve object, `TEST_GEOMETRY` / `REVIEW` /
  `INVALID` — same three-tier idea, older naming, not yet unified — see
  `docs/CAM_GEOMETRY.md`).
- **Manufacturing approval**: `TEST_ONLY` / `APPROVED`. Always `TEST_ONLY`
  in this codebase; nothing sets it to `APPROVED` automatically, and
  nothing should — that requires a human physically verifying a cut.

Passing every mathematical check does not mean CNC-ready; failing one does
not mean the geometry can't be manually reviewed and used. Keep these two
axes separate when reading a report.

## Where to look for more detail

- `docs/SEGMENTATION.md` — deckable-surface detection philosophy
- `docs/SURFACE_DEVELOPMENT.md` — flattening/unrolling
- `docs/CAM_GEOMETRY.md` — LINE/ARC reconstruction, tangent/gap guarantees
- `docs/PATTERNS.md` — decking pattern generation
- `docs/PROJECT_STRUCTURE.md` — directory/file layout and run output contents
