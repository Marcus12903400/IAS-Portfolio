# Project Structure

```
AutoDeck/
├── src/
│   └── autodeck/            Python package (src-layout; package name unchanged)
│       ├── cli.py            argparse entry points + wizard/doctor/cache dispatch
│       ├── wizard.py          numbered interactive front end (calls the same pipeline as cli.py)
│       ├── doctor.py          `autodeck doctor` health report
│       ├── cache.py           disposable hash-keyed cache/ utility
│       ├── config.py          config loading, precedence, validation
│       ├── paths.py           single source of truth for project-relative paths
│       ├── preflight.py       pre-run checks + run-summary printing
│       ├── pipeline.py        top-level analyze_scan/inspect_scan orchestration
│       ├── analysis_pipeline.py  the full mesh → candidates → CAM → export pipeline
│       ├── segmentation.py, orientation.py, candidates.py, boundaries.py  deckable-surface detection
│       ├── development.py, flattening.py  surface unrolling (RigidPlanarDevelopment, LSCM/ARAP)
│       ├── curve_fit.py, robust_reference.py, polyarc.py  CAM LINE/ARC reconstruction
│       ├── patterns.py        teak/diamond/hex pattern generation
│       ├── dxf_export.py, rhino_export.py, v03_export.py  file output
│       └── ...
│
├── config/
│   ├── default.yaml           the one authoritative default configuration
│   └── local.yaml             optional, git-ignored override (not checked in)
│
├── inputs/
│   └── boats/<name>/scan.obj  real boat scans -- read-only, not committed
│
├── outputs/
│   └── runs/<run-id>/         generated results, one directory per run
│
├── cache/                     disposable, regenerable -- always safe to delete
│
├── reference-data/
│   └── legacy/{keywest,seapro}/  irreplaceable pre-V0.3.6 real-boat analysis
│                                  data -- NOT cache, see reference-data/legacy/README.md
│
├── tests/
│   ├── unit/                  fast pure-function tests
│   ├── geometry/               synthetic-geometry construction/fitting tests
│   ├── integration/            full-pipeline tests
│   ├── regression/             reserved for real-boat regression (empty until a raw scan exists)
│   └── fixtures/synthetic-cockpit/  the one synthetic test mesh
│
├── docs/
│   ├── (this file and its siblings)
│   └── history/                superseded version-specific docs + dev prototype scripts
│
├── scripts/
│   └── clean_workspace.py     --dry-run/--apply cleanup of this project's own disposable data
│
├── pyproject.toml
├── .python-version
├── .gitignore
└── README.md
```

## Why this split

The organizing principle is separating **code**, **config**, **input**,
**output**, **cache**, and **irreplaceable legacy data** into distinct
top-level directories, so it's obvious at a glance what's safe to delete
(`cache/`), what's generated and prunable (`outputs/runs/`), what's source
material never to modify (`inputs/`), and what's genuinely irreplaceable and
protected (`reference-data/legacy/`).

`src/autodeck/` keeps the existing src-layout rather than moving to a
root-level `autodeck/` package directory — that would be a purely cosmetic
change with no import-path benefit, not worth the risk during a
geometry-critical release (see `docs/V0_3_6_MIGRATION_LOG.md`).

## A completed run

```
outputs/runs/<run-id>/
    run_report.md                    (or equivalent status report)
    verification.3dm                 RAW_3D + CONDITIONED_3D + CAM_BACKPROJECTED_3D
    flattened_preview.3dm            FLAT_RAW_DEVELOPED + ROBUST_REFERENCE + CAM_POLYARC
    flattened_curves_polyarc.dxf     preferred VCarve perimeter (LINE/ARC + bulge)
    flattened_curves_linearc.dxf     native LINE/ARC debug entities
    pattern_only.dxf                 (if a pattern was selected)
    flattened_with_pattern.dxf       (if a pattern was selected)
    curve_fit_report.md
    development_report.md
    manufacturing_report.md
    polyarc_report.md
    pattern_report.md                (if a pattern was selected)
    debug/
```

See `docs/CAM_GEOMETRY.md` and `docs/SURFACE_DEVELOPMENT.md` for what each
of these represents.
