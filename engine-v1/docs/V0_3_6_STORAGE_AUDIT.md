# V0.3.6 Storage Audit

Phase-1 read-only inventory of the source project tree, taken before any
migration or deletion:

```
~/Documents/Codex/2026-08-21/files-pasted-by-the-user-you/outputs/AutoDeck_V0.2_Conditioning_Flattening_Source_Clean/
```

**This tree is untouched by the V0.3.6 migration.** Everything below was
determined by reading it, not modifying it. See
`docs/V0_3_6_MIGRATION_LOG.md` for what was actually copied into the new
`AutoDeck/` project and what was left behind.

## Totals

- **Total size:** 7.3 GB
- **Git repository:** none (no `.git` anywhere in the tree)
- **New project after migration (excl. `.venv/`):** ~918 MB, of which 916 MB
  is `reference-data/legacy/` (irreplaceable real-boat derived data — see
  below) and ~2.3 MB is actual code + config + docs + tests.

## Size by top-level folder

| Folder | Size | Classification |
|---|---|---|
| `output/` | 4.4 GB | Mix of G (regenerable output) and UNKNOWN-but-irreplaceable derived data (see below) |
| `deliverables/` | 1.3 GB | H (obsolete packaged snapshots) |
| `AutoDeck_V0.3_Source/` (nested self-copy at root) | 847 MB | I (duplicate) |
| `vendor_packages/` | 298 MB | J (vendored dependency env) |
| `.autodeck_packages/` | 292 MB | J (vendored dependency env, duplicate of the above) |
| `work/` | 39 MB | Mix of E (one real regression run worth keeping) and F (pytest scratch) |
| `src/` | 2.2 MB | A (required source code) |
| `tests/` | 408 KB | D (required test fixtures/code) |
| `docs/` | 84 KB | A (required docs) |
| `test-data/` | 24 KB | D (required — the only synthetic fixture) |
| `config/` | 12 KB | C (required configuration) |
| Root-level zips (`AutoDeck_V0.3_KeyWest_Review.zip`, `AutoDeck_V0.3_Source.zip`) | 174 MB | I (duplicates of content already under `output/`/`deliverables/`) |

## 50 largest files

Dominated by 5 recurring derived-analysis filenames repeated across ~14
Key West run folders, all from the same underlying scan:

- `boundary_scores.npz` — ~147 MB each, appears in 11 run folders
- `analysis_mesh.ply` — ~98–111 MB each
- `slope_bands.ply` — ~99–104 MB each
- `topview_features.npz` — ~91–96 MB each
- `orientation_scores.npz` — ~72–78 MB each
- `deliverables/AutoDeck_V0.3.4_Portable_Windows_x64.zip` — 197 MB (packaged Windows distributable)
- `AutoDeck_V0.3_KeyWest_Review.zip` — 174 MB (root-level duplicate)

## File counts by extension (top of the list)

`.py` 9737, `.pyc` 9618, `.h` 1380, `.pyi` 1183, `.pyd` 593, `.mat` 444,
`.a` 424, `.png` 322, `.npz` 291, `.f90` 248, `.txt` 213, `.md` 207,
`.sav` 192, `.json` 168, `.dxf` 141, `.csv` 124, `.obj` 54, `.3dm` 82.

The `.py`/`.pyc`/`.h`/`.pyi`/`.pyd`/`.a`/`.f90`/`.pxd`/`.c`/`.pyx`/`.cmake`
counts are almost entirely `vendor_packages/` and `.autodeck_packages/` —
two full vendored copies of the pip-installable dependency set (scipy,
numpy, ezdxf, shapely, libigl, opencv, pytest, etc.), confirmed to be the
same package versions (differing `.pyc` bytes only — source is identical).

## Duplicate candidates (verified by SHA-256, not just size/name)

- `output/click-run-20900`, `click-run-6193`, `click-run-6618`,
  `click-run-27133`, `click-run-10034/processing_and_debug` — all share a
  byte-identical `boundary_scores.npz` with `output/key-west-v031-orientation`
  (same hash: `478f0763...e8db69`). All five are repeat re-runs of the same
  Key West scan; **not copied** into the new project.
- `output/key-west-v03-orientation` and `output/key-west-v031-orientation` —
  byte-identical for all 5 large derived arrays.
- `output/key-west-v03-structural-experimental` — shares 3 of 5 large arrays
  with `key-west-v03-orientation` (`boundary_scores.npz`, `slope_bands.ply`,
  `orientation_scores.npz`), but has genuinely distinct `analysis_mesh.ply`
  and `topview_features.npz`.
- `vendor_packages/` and `.autodeck_packages/` — same package set, source
  files identical, only compiled `.pyc` bytes differ (timestamps).
- Root-level `AutoDeck_V0.3_Source/AutoDeck_V0.3_Source/` — a self-nested
  duplicate of the same source snapshot, itself containing another full
  `output/click-run-1004/` copy of the same Key West derived data.

## Obvious generated files / caches

- `output/*` — all pipeline run output (reports, DXF, 3DM, derived NPZ/PLY).
- `.autodeck_cache/` (9.6 MB: `keywest_v031.pkl`, `keywest_poly.pkl`,
  `diag/`, `ezdxf/font_manager_cache.json`) — confirmed **orphaned**: zero
  references anywhere in `src/autodeck`, no pickle usage in the codebase.
  Not a functioning cache; not copied into the new project.
- `work/pytest-*` (8 directories) — pytest tmp-output scratch, byte-for-byte
  regenerable by rerunning the suite.
- `src/autodeck/__pycache__`, `tests/__pycache__` — standard Python bytecode
  cache.

## Real source inputs found

**None.** No raw boat scan mesh exists anywhere in this tree. The only
`.obj` files present are either the synthetic test fixture
(`test-data/synthetic-cockpit/scan.obj`, 17 KB) or *derived* outputs
(`feature_boundaries.obj`, `conditioned_boundaries.obj`,
`proposed_boundaries.obj` — boundary curves extracted from an already-loaded
mesh, not the source mesh itself).

Reading `run_report.md` inside the historical run folders reveals the real
input paths that were actually used, none of which exist in this
environment:

- Key West runs: `C:\Users\marcu\Downloads\21kwcockpit.obj`
- SeaPro run (`v02-r2-audit-preserved`): `C:\Users\marcu\Downloads\seaproautotest-OBJ\seaproautotestrotated1.obj`

Both were processed on a separate Windows machine and only their *derived*
analysis output made it into this tree — see
`reference-data/legacy/README.md` for how that derived data is now handled.

## Source code / configs / tests / docs

- `src/autodeck/` — 40 real `.py` modules (2.2 MB with `__pycache__`,
  ~1.7 MB without). This is the actual AutoDeck package; preserved in full.
- `config/default.yaml` — the one real configuration file. Already resolves
  correctly and contains no hardcoded developer paths.
- `tests/` — 10 real test modules (408 KB), categorized into
  `geometry/`/`integration/` in the new project.
- `docs/` — 16 markdown files, all version-tagged historical results
  (`V0_2_*`, `V0_3_*`, plus process docs); moved to `docs/history/` in the
  new project since none of them is a current general-reference doc.

## Development environment

- `vendor_packages/` (298 MB) and `.autodeck_packages/` (292 MB) — two full
  vendored Python environments. Neither is imported by `pyproject.toml`
  dependency resolution; both are regenerable via `pip install -e .` in a
  fresh venv (verified: this migration did exactly that, on Python 3.12,
  successfully).

## Old output runs

15 folders under `output/`, plus the equivalent nested copy under
`AutoDeck_V0.3_Source/AutoDeck_V0.3_Source/output/click-run-1004/`. See
`docs/V0_3_6_MIGRATION_LOG.md` for the per-folder disposition (kept in full,
kept-deduplicated, or dropped as a regenerable/duplicate synthetic run).

## Debug artifacts

`work/` mixes real historical prototype scripts (`prototype_*.py`,
`build_portable_zip.py`, `run_pytest.py`, `run_v033_tests_direct.py`,
`inspect_primary_v033.py` — preserved under `docs/history/prototypes/`) with
disposable pytest scratch directories (`pytest-production-output`,
`pytest-integration-compact-2`, `pytest-local-join`, `pytest-local-join-2`,
`pytest-adaptive`, `pytest-pattern-compact`, `pytest-pattern-compact-2`,
`pytest-full-v033` — not copied, fully regenerable).
