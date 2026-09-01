# V0.3.6 Migration Log

Source tree (**left fully intact, nothing deleted from it**):
`~/Documents/Codex/2026-08-21/files-pasted-by-the-user-you/outputs/AutoDeck_V0.2_Conditioning_Flattening_Source_Clean/`

New project: `~/Documents/Codex/AutoDeck/`

Per the approved V0.3.6 plan, this migration **copies** required material
into the new project; it does not delete anything from the old tree. Any
future deletion from the old tree is a separate, explicitly-confirmed step.

## Copied

| Old path | New path | Reason |
|---|---|---|
| `src/autodeck/` | `src/autodeck/` | Required source code, layout unchanged |
| `tests/*.py` | `tests/{geometry,integration}/*.py` | Required test code, recategorized (see below) |
| `docs/*.md` (16 files) | `docs/history/*.md` | Historical version-tagged results, superseded by fresh V0.3.6 docs |
| `config/default.yaml` | `config/default.yaml` | Required configuration |
| `pyproject.toml`, `README.md` | same | Required project metadata (README will be rewritten in the docs pass) |
| `test-data/synthetic-cockpit/` | `tests/fixtures/synthetic-cockpit/` | Required test fixture, relocated as a fixture rather than a top-level input |
| `work/key-west-local-join-20260827-01/` | `reference-data/legacy/keywest/local-join-20260827/` | Richest, most recent real-boat CAM run; raw source scan unrecoverable in this environment (see storage audit) |
| `output/key-west-v03-orientation/` | `reference-data/legacy/keywest/v03-orientation/` | Canonical copy of the shared base analysis blobs |
| `output/key-west-v031-orientation/` | `reference-data/legacy/keywest/v031-orientation/` | Reports/DXF/3dm only — its 5 large derived arrays were byte-identical to `v03-orientation/` (SHA-256 verified) and were not duplicated |
| `output/key-west-v033-orientation/` | `reference-data/legacy/keywest/v033-orientation/` | Full copy — distinct derived-file set, no overlap with the others |
| `output/key-west-v03-structural-experimental/` | `reference-data/legacy/keywest/v03-structural-experimental/` | Reports/DXF/3dm plus its 2 genuinely distinct large arrays; its other 3 large arrays were identical to `v03-orientation/` and were not duplicated |
| `output/v02-r2-audit-preserved/` | `reference-data/legacy/seapro/r2-audit/` | Only preserved SeaPro analysis; raw source scan unrecoverable in this environment |
| `work/prototype_local_connectors.py` | `docs/history/prototypes/` | Historical dev script, not imported by `src/autodeck` |
| `work/prototype_manual_fit.py` | `docs/history/prototypes/` | Historical dev script |
| `work/prototype_transition_repair.py` | `docs/history/prototypes/` | Historical dev script |
| `work/prototype_g1_optimize.py` | `docs/history/prototypes/` | Historical dev script |
| `work/prototype_line_biarc.py` | `docs/history/prototypes/` | Historical dev script |
| `work/build_portable_zip.py` | `docs/history/prototypes/` | Historical packaging script |
| `work/run_pytest.py` | `docs/history/prototypes/` | Historical dev script |
| `work/run_v033_tests_direct.py` | `docs/history/prototypes/` | Historical dev script |
| `work/inspect_primary_v033.py` | `docs/history/prototypes/` | Historical dev script |

## Test recategorization (within the new project only)

| File | New location |
|---|---|
| `test_geometry.py`, `test_conditioning_flattening.py`, `test_v031_curve_hotfix.py`, `test_v032_polyarc.py`, `test_v033_manual_patterns.py`, `test_v03_manufacturing.py` | `tests/geometry/` |
| `test_automatic.py`, `test_integration.py`, `test_production_output.py` | `tests/integration/` |

`tests/unit/` and `tests/regression/` created empty with explanatory READMEs
(no pure-unit tests identified yet to split out; no live real-boat
regression possible until a raw scan is recovered).

## Not copied (regenerable or proven duplicate) — still present, untouched, in the old tree

| Old path | Size | Regenerable? | Reason |
|---|---|---|---|
| `.autodeck_packages/` | 292 MB | Yes | Vendored dependency env, `pip install -e .` reproduces it |
| `vendor_packages/` | 298 MB | Yes | Duplicate vendored dependency env |
| `output/click-run-20900` | ~600 MB | Yes | Byte-identical `boundary_scores.npz` to `key-west-v031-orientation` (SHA-256 verified); repeat re-run of the same scan |
| `output/click-run-6193` | ~small | Yes | Same as above |
| `output/click-run-6618` | ~small | Yes | Same as above |
| `output/click-run-27133` | ~small | Yes | Same as above |
| `output/click-run-10034` | ~small | Yes | Same as above |
| `output/dev-smoke` | small | Yes | Ran against the preserved synthetic fixture; regenerable from `tests/fixtures/synthetic-cockpit/scan.obj` |
| `output/v03-synthetic-inspect` | small | Yes | Same |
| `output/v03-synthetic-inspect100` | small | Yes | Same |
| `output/v03-synthetic-verified` | small | Yes | Same |
| `output/v02-r2-audit-preserved` (original) | 28 MB | N/A | Copied (see above); original left in place |
| `deliverables/AutoDeck_V0.3.4_Portable_Windows_x64.zip` | 197 MB | Yes | Packaged Windows build artifact; `work/build_portable_zip.py` (preserved) can rebuild it |
| `deliverables/AutoDeck_V0.3.1_Source.zip`, `AutoDeck_V0.3.1_Source/` | ~small | Yes | Old source snapshot, superseded by current `src/autodeck/` |
| `deliverables/AutoDeck_V0.3.1_KeyWest_Review.zip`, `AutoDeck_V0.3.1_KeyWest_Review/` | ~150 MB | N/A | Content (boundary `.obj` files) confirmed already present under `output/key-west-v031-orientation/`, which was copied |
| `deliverables/AutoDeck_V0.3_KeyWest_Review/` | ~90 MB | N/A | Content confirmed already present under `output/key-west-v03-orientation/`, which was copied |
| Root `AutoDeck_V0.3_Source/` (nested self-copy) | 847 MB | N/A | Self-duplicate of the same source snapshot, including yet another copy of Key West click-run output |
| Root `AutoDeck_V0.3_Source.zip` | 157 KB | N/A | Duplicate of the above |
| Root `AutoDeck_V0.3_KeyWest_Review.zip` | 174 MB | N/A | Content confirmed already present under `output/key-west-v03-orientation/` and `key-west-v03-structural-experimental/` |
| `work/pytest-production-output`, `pytest-integration-compact-2`, `pytest-local-join`, `pytest-local-join-2`, `pytest-adaptive`, `pytest-pattern-compact`, `pytest-pattern-compact-2`, `pytest-full-v033` | small each | Yes | pytest tmp-output scratch directories, regenerable by rerunning the suite |
| `.autodeck_cache/` | 9.6 MB | N/A (orphaned) | Confirmed unread by any code in `src/autodeck` — not a functioning cache |
| `__pycache__/`, `.pytest_cache/` (throughout) | — | Yes | Standard Python caches |

None of the "not copied" items were deleted — they remain exactly where they
were in the old tree, per the plan's read-only-during-migration rule.

## Space accounting

- Old tree: 7.3 GB
- New project (excl. `.venv/`, which is gitignored and regenerable): ~918 MB
  - `reference-data/legacy/`: 916 MB (irreplaceable — see
    `reference-data/legacy/README.md`)
  - code + config + docs + tests + scripts: ~2.3 MB
- Naive (non-deduplicated) copy of the same "keep" set would have been
  ~1.72 GB; deduplicating the shared Key West analysis blobs by SHA-256
  brought that down to 916 MB.
