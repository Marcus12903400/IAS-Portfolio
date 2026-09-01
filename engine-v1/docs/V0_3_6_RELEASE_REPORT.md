# V0.3.6 Release Report

## Summary

This pass took AutoDeck from a 7.3 GB, undocumented, messy project tree
with two confirmed unfixed CAM-geometry bugs to a ~2.4 MB code+config+docs
+tests project (962 MB including 916 MB of intentionally-preserved
irreplaceable legacy real-boat data, 0 MB deleted from the original source
tree), both CAM bugs fixed and regression-tested, a working numbered wizard,
real config validation, a disposable cache system, and a documented,
reproducible build from a fresh Python 3.12 virtualenv.

**Manufacturing approval remains `TEST_ONLY`.** Nothing in this pass changes
that, and nothing should without a human physically verifying a cut.

## Priority 1: CAM correctness (done first, before any reorganization)

Both bugs a prior audit flagged were confirmed still present and are now
fixed, with regression tests. Full detail:
`docs/CAM_GEOMETRY.md#historical-bugs-and-their-fixes-verified-in-this-repository`.

- **Tangent-losing fallback** (`polyarc.py::_fit_recursive`) — no longer
  silently emits an untangented chord when a smooth join is required and no
  candidate is found. Raises `SharpCornerRequiresReview`; the span-level
  catch substitutes flagged raw geometry and forces the curve away from a
  clean status.
- **Hard-corner index aliasing** (3 sites: `robust_reference.py`,
  `polyarc.py`, `v032_reports.py`) — `% len(...)` modulo reuse replaced with
  `curve_fit.map_points_to_indices()`, which resolves by nearest coordinate
  and fails loudly on a genuine mismatch.
- Existing suite (90 tests) stayed green throughout; 9 new regression tests
  added (6 in `test_v032_polyarc.py`, 3 in `tests/unit/test_cache.py`).
  **99 tests pass** in a fresh venv.

## Priority 2: Storage cleanup / migration

- Full read-only inventory: `docs/V0_3_6_STORAGE_AUDIT.md`.
- Every copy/skip decision with size and reason: `docs/V0_3_6_MIGRATION_LOG.md`.
- **Nothing was deleted from the original 7.3 GB source tree.** Per the
  approved plan, it was treated as read-only; required material was copied
  out, proven duplicates/regenerable material was identified but left in
  place. Any deletion from that tree is a separate, future, explicitly-
  confirmed step.
- Deduplication: several ~100-500 MB derived analysis arrays were
  byte-identical (SHA-256 verified) across multiple historical run folders
  for the same scan; only one canonical copy of each was carried into
  `reference-data/legacy/`, cutting what would have been a ~1.72 GB naive
  copy down to 916 MB.
- Irreplaceable real-boat data (Key West, SeaPro — raw source scans absent
  from this environment, confirmed by reading the original `run_report.md`
  files, which point to paths on a different, Windows machine) was placed
  in a clearly protected `reference-data/legacy/` location, explicitly
  *not* inside the disposable `cache/` directory. See
  `reference-data/legacy/README.md`.

### Size accounting

| | Size |
|---|---|
| Original source tree | 7.3 GB |
| New project, excl. `.venv/` | 962 MB |
| &nbsp;&nbsp;— `reference-data/legacy/` (irreplaceable) | 916 MB |
| &nbsp;&nbsp;— code + config + docs + tests + scripts | ~2.4 MB |
| `.venv/` (regenerable, gitignored) | 404 MB |

The reduction (7.3 GB -> 962 MB active project) is dominated by removing
duplicate vendored dependency trees (590 MB), duplicate re-run analysis data
(SHA-256 verified, ~2 GB across `click-run-*`), and old packaging zips/
nested self-copies (~2.4 GB) — **without deleting the original tree**, and
without discarding the two real-boat analyses that have no recoverable raw
source in this environment.

## Priority 3: Authoritative paths/config

- `src/autodeck/paths.py` — single source of truth for project/config/
  cache/input/output/reference-data directories. Replaces the one prior
  ad-hoc path-resolution site in `config.py`.
- `config.py` — `config/local.yaml` optional override (git-ignored,
  documented precedence in `docs/CONFIG_REFERENCE.md`), plus
  `validate_config()` raising a clear `ConfigError` on missing/invalid
  config instead of a late `KeyError`.
- `preflight.py` — input/output/dependency checks and the run-summary print
  (input/units/up-axis/segmentation/pattern/cache/output), wired into the
  `analyze` CLI path and the wizard, before expensive work starts.
- `doctor.py` / `autodeck doctor` — version, interpreter, resolved paths,
  dependency status, cache size, free disk space.

## Priority 4: Interactive numbered wizard

`wizard.py` / `autodeck` (no args) — numbered prompts for scan selection,
units, up axis, segmentation mode, and the required pattern menu:

```
PATTERN:
[1] None
[2] Teak Lines
[3] Diamond
[4] Hexagon
```

It builds the same config dict and calls the same `analyze_scan()` pipeline
function the `autodeck analyze` subcommand calls — confirmed by direct
testing (both paths return the same `SEGMENTATION_FAILURE` status against
the same fixture at default thresholds). There is one implementation of
AutoDeck's processing behind two entry points, per the approved plan.

## Priority 5: Cache conveniences (deliberately modest)

- `cache.py` — a real, general-purpose, hash-keyed (`input hash + config
  hash + version`) disposable store, plus `autodeck cache status` /
  `autodeck cache clean`.
- **Deliberately not wired into `analysis_pipeline.py`'s internal stages
  in this pass.** Every expensive stage (mesh preprocessing, geometry
  fields, boundary/orientation fields, segmentation) is currently
  in-memory-sequential with no existing serialize/deserialize boundary;
  building one safely would mean designing new serialization for several
  numpy-heavy intermediate structures — exactly the kind of "sophisticated
  caching redesign" the approved plan said not to let distract from CAM
  correctness. The infrastructure is real, tested, and ready for a future
  pass to adopt at a specific, well-understood stage.
- Separately (not part of `cache/`): `reprocess.py`'s
  `pattern`/`reprocess-manufacturing` subcommands already avoid rescanning/
  re-developing on a pattern change, by reading back saved run-directory
  artifacts. That mechanism predates this pass and was not touched.

## Priority 6: Version, docs, cleanup polish

- Version unified to `0.3.6`: `__init__.py`, `pyproject.toml`. Also fixed:
  `patterns.py`'s module docstring and pattern-report header (now
  version-derived, so this exact drift can't recur at the next bump).
- **Deliberately left unchanged**: the DXF layer name constant
  `CAM_POLYARC_V033` and various internal "V0.3.1 spline" / "V0.3.2
  polyarc" / "V0.3.3 robust reference" labels throughout `polyarc.py`,
  `robust_reference.py`, `reprocess.py`, `cli.py` help text, and report
  headers. These are stable format/generation identifiers for still-active,
  config-selectable algorithm variants and a DXF layer name a user's
  existing VCarve setup may already reference — not a claim about the
  installed package version. Renaming them was judged higher-risk,
  lower-value churn than the plan's original blanket framing suggested; see
  `docs/CAM_GEOMETRY.md` and inline code comments for what each generation
  label actually means.
- Full documentation set written: `README.md`, `docs/ARCHITECTURE.md`,
  `docs/WORKFLOW.md`, `docs/PROJECT_STRUCTURE.md`, `docs/CONFIG_REFERENCE.md`,
  `docs/SEGMENTATION.md`, `docs/SURFACE_DEVELOPMENT.md`,
  `docs/CAM_GEOMETRY.md`, `docs/PATTERNS.md`, `docs/TESTING.md`, this file.
  16 old version-tagged docs moved to `docs/history/`.
- `.gitignore`, `.python-version` (3.12) added.
- `scripts/clean_workspace.py` (`--dry-run`/`--apply`) built and verified —
  operates only on this project, never the old source tree.

## Verified from a fresh environment

```
rm -rf .venv && python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[test,rhino,markers]"
python -c "import autodeck; print(autodeck.__version__)"   # 0.3.6
pytest                                                       # 99 passed
python -m autodeck doctor                                    # all deps OK
```

Wizard exercised end-to-end (piped input) through both the cancel path and
the run path, confirmed to call the shared `analyze_scan()` pipeline.
`autodeck cache status`/`clean` exercised. `scripts/clean_workspace.py
--dry-run`/`--apply` exercised on this project's own Python caches.

## Addendum (2026-08-29, later session): real scan -> `final.dxf`

Goal: the user's actual Key West scan (`~/Downloads/21kwcockpit.obj`,
807 MB) in, VCarve-ready `final.dxf` out. Starting state: the run produced
`FINAL_NOT_READY` with 8,105 signed-safety violations on the primary.

Root causes found and fixed (all with tests; suite now **109 passed**):

1. Raw raster noise was treated as a physical wall (0.35 mm allowance);
   measured violation depth on the real scan was p99 2.4 mm with zero real
   crossings. New `signed_safety_penetration_allowance_mm` (2.0), plus
   honest `wall_side_penetration` metrics. See `docs/CAM_GEOMETRY.md`.
2. `_repair_manual_joins` picked the least-unsafe connector; the adaptive
   safety net was shipped disabled. Both fixed; join trim now escalates.
3. The manual broad-regime fitter was not safety-aware and one failed join
   discarded the whole proposal; it is now safety-aware with safe
   projection, and the lower-count of the two valid constructions is kept.
4. Any advisory warning forced polyarc status `REVIEW` and blocked
   `final.dxf`; status is now INVALID / REVIEW (sharp-corner review only) /
   TEST_GEOMETRY.
5. A relative `--output` path made the finalizer look for the DXF in the
   wrong place and withhold `final.dxf` from *valid* geometry.
6. `reprocess-manufacturing` never re-finalized; it now does.
7. Obstacle corridor targets could drift into the obstacle (side-inverted
   re-projection test in `_corridor_targets`).
8. One unfittable interval surrendered a whole span to raw; repair is now
   local.
9. Tests silently absorbed a developer's `config/local.yaml`; `conftest.py`
   now isolates them.

Result on the real scan (`outputs/runs/21kwcockpit-20260829-181644-v036/`),
verified with an independent ezdxf-only reconstruction, not AutoDeck's own
audit: `final.dxf` written; 10 closed LWPOLYLINEs (primary + 3 secondary
decks + 6 obstacle cut-outs); mm units; every contour `TEST_GEOMETRY` with
0 forbidden violations and 0 review joins; primary 4055.6 x 2102.7 mm,
154 primitives (8 lines + 146 arcs), 9 protected corners, worst smooth-join
tangent 0.0769 deg, max fit deviation 2.973 mm, max wall-side penetration
1.99 mm, no self-intersection.

Known limitation: 154 primitives is more than the hand-drafted benchmark
(~32). The count is driven by the chain fitter adding arcs wherever a
longer LINE/ARC cannot stay inside the 2 mm wall-side band through raster
noise; the sweep showed a 3.0 mm band gives 97 primitives, but that is a
manufacturing tolerance decision for the user, not a default. Manufacturing
approval remains `TEST_ONLY`.

## What this pass did NOT do (explicit scope notes)

- **No real Key West/SeaPro end-to-end regression.** The raw source scans
  are not present in this environment (confirmed via the original
  `run_report.md` files, which point to a different Windows machine). Any
  check against the preserved `reference-data/legacy/` derived data is
  legacy-artifact validation, not a live regression — see
  `reference-data/legacy/README.md`. `tests/regression/` stays empty until
  a raw scan is recovered.
- **No `cam_joint_report.md` per-joint table.** The underlying data
  (`PolyarcJoin` gap/tangent/`sharp_corner_review` per joint) already
  exists and is folded into aggregate metrics in
  `manufacturing_report.md`/`polyarc_report.md`; rendering it as a
  dedicated per-joint markdown table was not done in this pass.
- **No full config dead-field audit.** `docs/CONFIG_REFERENCE.md`
  documents the 22 top-level sections and what each governs, and describes
  precedence/validation, but does not verify every individual leaf field
  across ~22 sections for live-vs-dead status. Nothing was deleted from
  `config/default.yaml`.
- **No deletion from the original 7.3 GB source tree.** By design — see
  Priority 2 above and `docs/V0_3_6_MIGRATION_LOG.md`.
- **No `scripts/package_source.py`** (a small source-review-only ZIP
  builder) — not built in this pass; `scripts/clean_workspace.py` covers
  the disk-space-hygiene half of that ask.
- **No cache integration into the analysis pipeline stages** — see
  Priority 5 above.
