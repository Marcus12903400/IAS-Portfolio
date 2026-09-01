# AutoDeck

AutoDeck converts a prepared 3-D boat scan into marine-decking CAM geometry:
it finds deckable surfaces, develops (unrolls) them to true flat dimensions,
reconstructs the noisy scanned edge as clean tangent LINE/ARC geometry, and
optionally lays down a decking pattern (teak, diamond, or hexagon) — ready
for review in Rhino and cutting in VCarve.

**Manufacturing approval is always `TEST_ONLY`** until a human has physically
verified a cut. Mathematically valid geometry is not the same thing as
CNC-ready geometry — see `docs/CAM_GEOMETRY.md`.

## Requirements

- Python 3.12 (see `.python-version`)
- macOS or Linux; no cloud/network access required for normal operation

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test,rhino,markers]"
```

`rhino` and `markers` are optional extras (Rhino `.3dm` export, ArUco marker
detection). `test` is needed to run the test suite.

This engine has no launcher of its own any more. Use the AutoDeck UI
(`AutoDeck.bat` / `AutoDeck.command` two levels up), or run the v1 wizard from
the shared environment: `.venvScriptspython -m autodeck`.

> **If the project lives in iCloud Drive (e.g. under `~/Documents` with
> "Optimize Mac Storage"), macOS can offload the `.venv` library files and the
> first launch stalls for minutes with a blank window while they re-download.**
> The launcher now detects this and downloads them with a visible message, but
> the durable fix is to keep the AutoDeck folder out of iCloud Drive (e.g.
> `~/AutoDeck`) or right-click the folder in Finder and choose **Keep
> Downloaded**. On first run
it creates `.venv` and installs everything automatically (needs internet
once); after that it just launches the wizard. Requires Python 3.12+
installed from python.org with "Add python.exe to PATH" checked.

## Running AutoDeck

The normal way to run AutoDeck is the numbered wizard — it's also what runs
if you invoke `autodeck` with no arguments:

```bash
autodeck
# or: python -m autodeck
```

It asks a short series of numbered questions (scan, units, up axis,
segmentation mode, pattern), prints a confirmation summary, and then runs.

For scripted/advanced use, the same functionality is available as explicit
subcommands (`analyze`, `reprocess-manufacturing`, `pattern`, `inspect`) —
run `autodeck --help` or see `docs/WORKFLOW.md`. The wizard and the
`analyze` subcommand call the same underlying pipeline function; there is
only one implementation of AutoDeck's processing.

Other useful commands:

```bash
autodeck doctor         # environment/version/path health check
autodeck cache status   # cache/ size and entry count
autodeck cache clean    # clear cache/ (always safe -- see below)
```

## Project layout

```
autodeck/            program (src/autodeck/)
config/               default.yaml + optional local.yaml override
inputs/boats/         your boat scans go here (read-only, not committed)
outputs/runs/         generated results, one directory per run
cache/                disposable, regenerable -- always safe to delete
reference-data/       irreplaceable legacy analysis data -- NOT cache
tests/                unit / geometry / integration / regression
docs/                 architecture, workflow, and reference docs
```

See `docs/PROJECT_STRUCTURE.md` for the full breakdown.

## Inputs

Put a prepared OBJ scan under `inputs/boats/<name>/scan.obj` (or point the
wizard/CLI at any path). AutoDeck never writes into `inputs/`. See
`docs/WORKFLOW.md` for the Vega → Rhino → AutoDeck preparation steps.

## Outputs

Every run gets its own directory under `outputs/runs/<run-id>/` — reports,
`verification.3dm`, flattened DXFs, and (if a pattern was selected)
pattern DXFs. See `docs/PROJECT_STRUCTURE.md` for the full file list.

## Cache

`cache/` holds disposable, hash-keyed intermediate data. **Deleting it is
always safe** — it only makes the next run slower, never destroys input,
config, code, or approved output. Use `autodeck cache clean` instead of
deleting by hand if you want a status report first.

## Tests

```bash
pytest
```

96+ tests across `tests/unit/`, `tests/geometry/`, `tests/integration/`.
`tests/regression/` is reserved for real-boat regression once a raw scan is
available (see `reference-data/legacy/README.md` for why none is checked in
yet). See `docs/TESTING.md`.

## Documentation

- `docs/ARCHITECTURE.md` — how the pipeline stages fit together
- `docs/WORKFLOW.md` — the end-to-end user workflow, Vega through VCarve
- `docs/SEGMENTATION.md` — how deckable surfaces are found
- `docs/SURFACE_DEVELOPMENT.md` — flattening/unrolling, not top-view projection
- `docs/CAM_GEOMETRY.md` — the LINE/ARC reconstruction philosophy and guarantees
- `docs/PATTERNS.md` — teak/diamond/hex pattern generation
- `docs/CONFIG_REFERENCE.md` — configuration precedence and field reference
- `docs/PROJECT_STRUCTURE.md` — full directory/file reference
- `docs/TESTING.md` — test organization
- `docs/history/` — superseded version-specific docs and dev prototypes,
  kept for reference
