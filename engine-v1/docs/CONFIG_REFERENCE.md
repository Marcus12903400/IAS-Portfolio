# Configuration Reference

## Precedence

Configuration is loaded and merged in this order, each layer overriding the
previous (`autodeck/config.py::load_config`):

1. `config/default.yaml` — always loaded, the authoritative baseline.
2. `config/local.yaml` — optional, git-ignored. Not required to exist. Use
   it for a personal override (e.g. a different `segmentation.mode` while
   developing) that shouldn't be committed.
3. An explicit path (e.g. `--config path/to/file.yaml`, or a value passed
   directly to `load_config()`) — layered on top of both of the above, for a
   specific run.

All three are the same JSON-compatible-YAML format (plain JSON is valid
YAML, and AutoDeck reads it with the standard library `json` module rather
than adding a PyYAML dependency).

`config/default.yaml` is resolved via `autodeck/paths.py::default_config_path()`,
which is project-root-relative (`Path(__file__).resolve().parents[2]`) —
never dependent on the user's home directory or current working directory.

## Validation

`load_config()` validates the merged result by default
(`autodeck/config.py::validate_config`) and raises `ConfigError` — a clear,
actionable message naming the file and field — rather than letting a bad
value surface as a `KeyError` deep inside the pipeline. Currently checked:

- `config/default.yaml` (and `local.yaml`, if present) must exist and parse.
- `segmentation.mode` must be one of `orientation`, `structural-experimental`,
  `advanced` (a deprecated alias for `structural-experimental`).
- `pattern.selected` (if the `pattern` section is present) must be one of
  `none`, `teak`, `diamond`, `hex`.
- The top-level sections most of the pipeline reads unconditionally
  (`mesh`, `orientation`, `curvature`, `boundary`, `segmentation`,
  `manufacturing_fit`, `robust_reference`, `polyarc_fit`, `dxf`) must be
  present and be mappings.

This is deliberately not exhaustive — see "Scope" below.

## Top-level sections

`config/default.yaml` has 22 top-level sections. Grouped by what they
govern:

| Section | Governs |
|---|---|
| `mesh` | OBJ loading, analysis-mesh resolution/clustering |
| `up_axis` | Up-direction detection/assessment |
| `orientation` | Orientation-field slope thresholds (core/max, hysteresis) — see `docs/SEGMENTATION.md` |
| `curvature` | Curvature-field calculation for structural evidence |
| `boundary`, `boundary_curve` | Wall/floor transition and boundary-curve evidence |
| `markers` | ArUco marker seed detection |
| `segmentation` | Segmentation mode selection, area filtering — see `docs/SEGMENTATION.md` |
| `verification` | 3-D verification export contents |
| `topview` | Top-view raster features (V0.2-era, still used for some diagnostics) |
| `nonskid`, `seams` | Geometric nonskid/seam evidence (no texture-based detection — see `docs/SEGMENTATION.md`) |
| `obstacles` | Obstacle/void detection (console, T-top feet, etc.) |
| `v02_export` | Legacy V0.2 export options |
| `curve_conditioning` | Raw-boundary smoothing/persistence before flattening |
| `flattening` | `PlanarFlattening` (rigid, true-length-preserving) |
| `development` | `RigidPlanarDevelopment` / `IntrinsicMeshDevelopment` (LSCM+ARAP), distortion thresholds — see `docs/SURFACE_DEVELOPMENT.md` |
| `manufacturing_fit` | Corner detection, span fitting, join-gap/deviation tolerances |
| `robust_reference` | The persistent physical reference used as the CAM fit corridor centerline |
| `polyarc_fit` | LINE/ARC/biarc fitting tolerances (3.0 mm absolute ceiling, tangent target/max) — see `docs/CAM_GEOMETRY.md` |
| `pattern` | Teak/diamond/hex dimensions and selection — see `docs/PATTERNS.md` |
| `dxf` | DXF export options (units, layer behavior) |

## `polyarc_fit` keys established in V0.3.6

| Key | Default | Meaning |
|---|---|---|
| `signed_safety_penetration_allowance_mm` | `2.0` | Depth beyond the raw contour on the forbidden side that counts as scan noise rather than a crossing. See `docs/CAM_GEOMETRY.md`. Do not raise it to lower primitive counts. |
| `adaptive_refine_invalid_manual_primary` | `true` (was `false`) | Re-audit the repaired manual proposal for gaps/tangency/safety/self-intersection and fall back to the tangent chain if it fails. |
| `manual_join_trim_schedule_multipliers` | `[1, 2, 3, 4]` | Multiples of `manual_join_trim_mm` tried at a smooth join before it is declared unbridgeable. |
| `compare_manual_with_tangent_chain` | `true` | When the manual proposal passes, also build the tangent chain and keep whichever valid construction has fewer primitives. |

## Test isolation

`load_config()` skips `config/local.yaml` when the environment variable
`AUTODECK_IGNORE_LOCAL_CONFIG` is set. `tests/conftest.py` sets it, so a
developer's local experiment can never silently change what the suite
asserts against.

## Scope of this document

A full field-by-field audit of all ~22 sections (hundreds of leaf values,
several accumulated across V0.2/V0.3/V0.3.1/V0.3.2/V0.3.3 development) was
not performed as part of V0.3.6 — that would require tracing every field's
read sites individually to confirm which are still live, renamed, or dead,
which is a larger undertaking than this pass's scope. Nothing was deleted
from `config/default.yaml`. If you find a field that's no longer read
anywhere, `grep -rn "the_field_name" src/autodeck/` before removing it.

## Cache dependency (informational)

`cache.py` supports hashing only the config sections relevant to a given
cached stage (`hash_config(config, relevant_keys=(...))`), so that, for
example, changing `pattern.*` does not invalidate a cache entry keyed only
on `segmentation`/`mesh`. See `cache.py`'s module docstring — no pipeline
stage is wired into this cache yet (see `docs/V0_3_6_RELEASE_REPORT.md` for
why that was deliberately kept minimal in this pass). Separately,
`reprocess.py`'s `pattern`/`reprocess-manufacturing` subcommands already
reuse saved run-directory artifacts (segmentation, development, CAM curves)
across a pattern change without rescanning — that is a distinct,
already-working mechanism, not part of `cache/`.
