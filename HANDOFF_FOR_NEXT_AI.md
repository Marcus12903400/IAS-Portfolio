# HANDOFF_FOR_NEXT_AI.md - AutoDeck 5.3

Written 2026-10-07 for the next AI model (GLM-5.3) that will work on this project. It assumes
you have never seen the code, the history, the owner or the audit. Everything in it was checked
against the tree at `D:/AutoDeck` on 2026-10-07; where something could not be checked it says so.

Table of contents

- [0. Read me first](#0-read-me-first)
- [1. The product and the owner](#1-the-product-and-the-owner)
- [2. Repository map](#2-repository-map)
- [3. How the seam and sheet pipeline works](#3-how-the-seam-and-sheet-pipeline-works)
- [4. Rules that are decided](#4-rules-that-are-decided)
- [5. State of the audit](#5-state-of-the-audit)
- [6. The plan](#6-the-plan)
- [7. How to work here safely](#7-how-to-work-here-safely)
- [8. Glossary](#8-glossary)
- [9. Open questions and unknowns](#9-open-questions-and-unknowns)

---

## 0. Read me first

### 0.1 What this file is

The single entry point for an AI assistant picking up AutoDeck. It replaces `START_HERE_AI.md`
(a 2026-09-02 snapshot, kept for history; its paths and "nothing is committed" warnings are out
of date). The other documents you will need are `docs/seam-audit-2026-10-07.md` (the open
defects, 111 findings), `tools/audit/` (the probe scripts behind them), `README.md` (owner-facing),
`app/README.md`, `engine/README.md`, `engine-v1/README.md` and `docs/v5-build-notes.html`.

### 0.2 Who the owner is

The owner is a marine decking fabricator (email `dynastycustoms21@gmail.com`; refer to them as
"the owner"). They run the software in their own shop. They are not a programmer. They have
worked with AI assistants on this project since 2026-08 and have clear habits about how they
want that to go (section 1.4).

### 0.3 What the project does

AutoDeck turns a 3D photogrammetry scan of a boat's cockpit floor into DXF cut files for VCarve,
so a CNC can cut synthetic marine decking panels that fit the boat. The hard part is that a deck is
a curved surface, so each panel must be truly developed (unrolled), its ragged scanned edge refitted
as clean LINE/ARC geometry, and then split by seams into pieces that fit a 40 x 80 inch sheet of a
directional material and nested onto as few sheets as possible. It is one application the owner
double-clicks, not a general CAD package.

### 0.4 Current state, in six bullets

- Version **5.3.0** everywhere (`app/autodeck_app/__init__.py`, `app/pyproject.toml`, page
  title and brand span, `/api/state`, `tools/ui_smoke.py`).
- Repository `D:/AutoDeck`, branch `main`, remote `origin` =
  `https://github.com/Marcus12903400/IAS-Portfolio.git` (the name is deliberate; see 2.1).
  Commits on top of the 2026-09-02 baseline `958d8f0` ("Final numbers: 781.7s -> 317.0s"):
  `901feed` (engine v5.3), `b00ecd9` (app v5.3), `9b34ce9` (docs, audit report and probe
  scripts), `3785e52` (merge of the portfolio repo's four commits), `0801d16` (README and
  START_HERE_AI pointed at this file), then the commit that adds this file. The history was
  rewritten on 2026-10-07 to drop the generated `inputs/` scans (a 227 MB file blocked the
  push), so ids quoted in older documents differ: `ecb7b30` became `958d8f0`. Run
  `git log --oneline -8` for the exact list.
- Tests: engine suite **297 passed, 1 deselected** in 710 s on 2026-10-07 (machine busy; about
  7 minutes idle). engine-v1 suite **116 passed** on 2026-09-02, not re-run since.
- A full seam/sheet audit was completed on 2026-10-07. **Nothing from it has been fixed.** The
  code is exactly what the audit measured.
- The data is not in git and exists only on the owner's machine: every run folder under
  `engine/outputs/runs/` (913 MB, 16 runs, including hand-placed seam sets), the scans under
  `app/inputs/` (five 807 MB OBJ files) and the synthetic scans under `inputs/` (258 MB). Treat
  every `seams.json` as irreplaceable.
- **`D:` is a removable USB flash drive** (SanDisk 3.2 Gen1, 62 GB, FAT32). The whole project
  and the only copies of the run folders live on it. On 2026-10-07 it dropped out of Windows
  for several minutes while an agent was working and then came back. Treat it as fragile: check
  it is mounted before every session, and get the runs and scans copied to the internal drive
  (section 7.11) before doing anything else.

### 0.5 First 30 minutes

All commands are for Git Bash on Windows, from the repository root, exactly as written.

0. Confirm the USB drive is there: `ls /d/AutoDeck/README.md` in Git Bash, or
   `Test-Path D:\AutoDeck\README.md` in PowerShell. If it is not, stop and ask the owner to plug
   the stick back in. The folder `C:\Users\marcu\AutoDeck` is NOT the project (2.6).

1. Make git trust the folder (FAT32 does not record ownership). This is already set on
   the owner's machine; it is needed on any new clone location:

   ```bash
   git config --global --add safe.directory D:/AutoDeck
   cd /d/AutoDeck
   git status --short
   git log --oneline -8
   ```

2. Run the engine suite (about 7 minutes idle). `PYTHONIOENCODING=utf-8` is required: Windows
   defaults to cp1252 and the engine prints characters such as the right arrow.

   ```bash
   PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m pytest engine/tests -q -m "not slow"
   ```

   Expected: `297 passed, 1 deselected`. Then the v1 suite (about 1 minute; last known 116 passed):

   ```bash
   PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m pytest engine-v1/tests -q
   ```

3. Start the app by hand on a spare port (the owner's shortcut uses 8765 through
   `AutoDeck.bat`). The two root variables are what the launcher sets; without them
   `app/autodeck_app/settings.py` looks for the engine under `%USERPROFILE%\AutoDeck2`.

   ```bash
   AUTODECK2_ROOT="D:/AutoDeck/engine" AUTODECK_V1_ROOT="D:/AutoDeck/engine-v1" PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m autodeck_app --port 8790 --no-browser
   ```

   Open `http://127.0.0.1:8790/`. The tab must say "AutoDeck 5.3".

4. Before any experiment that calls `sheetjob.plan`, `bridge.job_sheets`,
   `bridge.job_optimise_seams`, `sheets.write_seams` or the app's seam endpoints, stage a copy of
   a run outside the repository and work on the copy. The two test fixtures are
   `21kwcockpit-1-20260901-180939` (has a boat axis) and `21kwcockpit-1-20260901-172519` (no
   axis). A run folder is about 5 MB.

   ```bash
   mkdir -p /d/AutoDeck-scratch/runs
   cp -r engine/outputs/runs/21kwcockpit-1-20260901-180939 /d/AutoDeck-scratch/runs/
   ```

   A plan on the copy, from Python (nothing in the repo is written):

   ```bash
   PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe - <<'EOF'
   from pathlib import Path
   from autodeck2 import sheetjob
   from autodeck2.config import load_config
   run = Path("D:/AutoDeck-scratch/runs/21kwcockpit-1-20260901-180939")
   result = sheetjob.plan(run, load_config(), write_files=False)
   print(result["status"], result["summary"]["sheet_count"], "sheets", result["piece_count"], "pieces")
   for piece in result["pieces"]:
       print(piece["piece_id"], "holes", piece["holes"], "area", piece["area_mm2"])
   EOF
   ```

   Expected today on that copy: `NEEDS_SEAMS 2 sheets 13 pieces`.

   To drive the app against the copy instead of the real runs, point it at the scratch folder
   (Windows-style path for the environment variable):

   ```bash
   AUTODECK_RUNS_DIR="D:/AutoDeck-scratch/runs" AUTODECK2_ROOT="D:/AutoDeck/engine" AUTODECK_V1_ROOT="D:/AutoDeck/engine-v1" PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m autodeck_app --port 8790 --no-browser
   ```

   Opening a run in the app re-reads the scan at `run.json -> input_path`. For every existing run
   that path is `C:\Users\marcu\AutoDeck\app\inputs\<name>.obj`, the old project location, which
   still exists on the owner's machine (section 2.6). If that folder is ever removed, runs will
   fail to open with "the scan this run was made from is not at ..." (`bridge.load_run`,
   `app/autodeck_app/bridge.py:783-785`).

5. Read section 1.4 (how the owner works) before you propose anything, and section 7 before you
   change anything.

---

## 1. The product and the owner

### 1.1 Goal

A fabricator drops a scan in and gets sheet-by-sheet DXF files out, ready for VCarve, with the
deck split into pieces that fit the sheet, laid out with little waste, with the grain running
along the boat, and with seams that look deliberate. Manufacturing approval is always `TEST_ONLY`
until a human has physically verified a cut (set in `engine/autodeck2/autofit.py`, `ingest.py`,
`sheetjob.py` and v1's `production_output.py`).

### 1.2 The material

Reflex TruGrain, 40 x 80 inch sheets (1016 x 2032 mm), directional: the 80 inch dimension must
run along the length of the boat. That is why pieces are only ever rotated 0 or 180 degrees on
the sheet, never 90, never mirrored, and why every seam is squared to the boat's own axis.

### 1.3 The fabricator's workflow in the UI (the numbered steps in the left column)

1. **Scan.** Drop an `.obj`, or "Open a scan folder" to bring the `.mtl` and textures. The file is
   used in place. The preview is decimated to about 250k faces and cached in `app/cache/preview/`.
2. **Outline.** Choose units, layout (`nest` or `boat-plan`) and a pattern (teak lines, diamond
   stitch, hexagons, none). The engine segments the deck into panels, develops each one, lays
   them out flat and writes `outline.3dm`, `outline.dxf`, `panels.json`, `run.json`,
   `outline_report.md` into a new run folder. The pattern stage is where the **boat axis** comes
   from; a run with pattern "none" has no axis.
3. **Auto-fit.** Refits the raw outline as lines first, then the largest tangent arcs, 1.5 mm
   inside the detected border, simplifying only inward. Writes `final_auto.dxf` and
   `auto_cam.3dm`. Alternatively the owner draws the outline by hand in Rhino on `outline.3dm`
   and ingests the `.3dm`; strict validation produces `final.dxf`, which beats `final_auto.dxf`
   everywhere downstream.
4. **Seams and sheets.** Pick a direction (Vertical = along the boat, Horizontal = across,
   Diagonal = typed angle off the centreline), press "Place seam", hover the deck in the 3D view
   or the flat view (drawn bow up), see the seam you would get drawn live with its length and
   panel, click to lock it in. Seams re-plan on every edit. "Find the best seam layout" is the
   optimiser (about 90 s). The "Sheet layout" tab shows the nest; "Export sheet DXFs" writes one
   `sheet_NN.dxf` per sheet.
5. **Files.** Download links for everything the run produced.

### 1.4 How the owner works, and how to talk to them

- They ask for **analysis first** and an **explicit pause before any code is changed**. Explain
  what you found, what you intend to change and what you are unsure of, then wait.
- They switch model effort between analysis and coding sessions. Expect "analyse this" sessions
  with no edits, followed by "now fix steps 1 to 3" sessions.
- They want **plain words**, **numbers from the real pipeline** (`sheetjob.plan`, never the
  optimiser's proxy), and **honesty about what was and was not verified**. The project's history
  has several confident assumptions overturned by measurement; say what you measured.
- UI wording is for a fabricator, not a programmer: short, concrete, no class names, no stack
  traces.
- They lost hand-placed seams once when an agent overwrote `seams.json`; they do not want that to
  happen again.

Vocabulary the owner uses:

| Word | Meaning here |
|---|---|
| along / across | parallel to the boat centreline (the planks) / at exactly 90 degrees to it. The UI says Vertical / Horizontal. |
| square | an across seam at exactly 90 degrees to the boat. On the reference boat the axis is 3.68 degrees in the placed frame, so "square" there is 93.68 degrees, not 90. |
| pie shapes | what a long seam a degree or two off the planks produces: the piece fans open against the plank lines. The reason the boat axis overrules nearby edges. Owner's words: "the horizontle short seams are always going to be 90 degrees or perfectly side to side with the boat over ruling the in line with other features". |
| bow up | the seam tab is drawn with the bow at the top, panels back where they sit in the boat. |
| best found | what the optimiser reports. Never "optimal". |
| too big / needs a seam | a piece that does not fit the 990.6 x 2006.6 mm usable envelope. |
| console, hatch, cut-outs | the obstacles in the deck: one large console (about 1125 mm wide on the reference boat), four 108 x 73 mm rectangles, one 206.7 mm round cut-out. |
| gunwale strips, toe rails | panels 2 and 3 on the reference boat: 25 to 40 mm wide, 2.4 m long. |
| wont fit on sheet | the `NEEDS_SEAMS` status. Owner's words: "I dont mind the 'wont fit on sheet' error but i dont like that it was stopping me from deleting bad seams." Removing seams must always work. |

---

## 2. Repository map

### 2.1 Top level

```
D:/AutoDeck/
  AutoDeck.bat            Windows launcher: builds .venv on first run, sets the roots, runs the app
  AutoDeck.command        macOS launcher (never known to have been run)
  AutoDeck.app/           macOS app bundle (same)
  AutoDeck.lnk            shortcut; runs D:\AutoDeck but takes its icon from C:\Users\marcu\AutoDeck (audit F50)
  AutoDeck.ico
  README.md               owner-facing
  START_HERE_AI.md        2026-09-02 AI snapshot, superseded by this file
  HANDOFF_FOR_NEXT_AI.md  this file
  index.html              a 56-line "Portfolio Carousel" page from the IAS-Portfolio repo's own
                          history, merged in rather than overwritten; unrelated to AutoDeck;
                          the owner may delete it
  .gitignore
  app/                    layer 1: the browser UI (Flask + three.js)
  engine/                 layer 2: AutoDeck2, the v2 engine (pure Python)
  engine-v1/              layer 3: AutoDeck v1 0.3.6, the analysis engine v2 runs on
  docs/                   seam-audit-2026-10-07.md, v5-build-notes.html
  tools/                  developer scripts, not part of the app; tools/audit/ holds the probes
  inputs/                 synthetic test scans (gitignored, regenerable)
  .venv/                  built by AutoDeck.bat (gitignored)
```

The GitHub repository is named `IAS-Portfolio` because the owner chose to reuse an existing empty
repo of that name; its four prior commits (a portfolio page from 2025) were merged in as
`3785e52` so that history was kept. The local branch was renamed from `master` to `main`. Older documents that say
`master` or `C:\Users\marcu\AutoDeck` are out of date.

### 2.2 Layer 1: `app/autodeck_app/`

| File | Lines | Role |
|---|---|---|
| `__main__.py` | 87 | `python -m autodeck_app [--port N] [--no-browser]`; Flask `threaded=True`; picks the next free port if another version owns 8765 |
| `server.py` | 670 | all HTTP routes (list in 3.9), input validation (`checked_seams` 150-186, `sheet_options` 205-235, `_SHEET_NUMBERS` 45-64), `busy_guard` 317 |
| `bridge.py` | 1548 | glue to the engine: `sheet_preview` 277, `seam_hover` 374, `_settled` 542, `job_sheets` 601, `job_optimise_seams` 652, `load_run` 780, the 3D lift `_make_lifter` 843, `pick_flat` 1504 |
| `jobs.py` | 78 | in-memory `JobManager`, one engine job at a time |
| `settings.py` | 38 | every path, overridable by environment variable |
| `meshview.py` | 485 | decimates the scan for the browser, preview cache |
| `static/app.js` | 2302 | the whole UI, one IIFE; key functions in 3.6 |
| `static/index.html` | 266 | markup; seam gap input at line 145 |
| `static/style.css` | 219 | |
| `static/vendor/` | | three.js r128 + OrbitControls, vendored |

There is no `app/tests/` directory. Page logic has no automated coverage except
`tools/ui_smoke.py`, which the suite does not run.

### 2.3 Layer 2: `engine/autodeck2/`

| Module | Lines | Role |
|---|---|---|
| `pipeline.py` | | stage orchestration, `run_outline` |
| `engine.py` | 547 | the cached engine run (scan to developed panels); cache identity = scan sha256 + config hash + v1 version + v1 source fingerprint |
| `assign.py`, `layout.py` | 221 (layout) | panel assignment; laying panels flat (`nest` / `boat-plan`), `nest_offset` |
| `autofit.py`, `fitter.py` | 244, 1905 | raw outline to LINE/ARC CAM geometry |
| `ingest.py`, `outline3dm.py`, `calibration.py` | | the Rhino round trip |
| `teak.py` | 204 | pattern generation; the pattern stage stores the boat axis in `run.json` |
| `sheets.py` | 755 | `Seam`, `Piece`, `split_panel`, ring rebuild, `sheet_transform`, `read_fitted_dxf`, `oversize_report`, `DEFAULTS` |
| `seamplace.py` | 288 | `direction_for`, `seam_through` (chords of a line through a point) |
| `seamsnap.py` | 1259 | the corrector: axis capture, slide onto edges, feature snapping; `SNAP_DEFAULTS` |
| `seamplan.py` | 2564 | the automatic seam optimiser |
| `nesting.py` | 662 | bottom-left first-fit nesting, sheet DXF writer, `_refuse` gate |
| `sheetjob.py` | 683 | `resolve_frame`, `apply_snap`, `plan`, `preview`, `exported_sheets` |
| `cache.py`, `config.py`, `paths.py`, `parallel.py` | | support |
| `v1compat.py` | | the only module allowed to import v1 |
| `cli.py` | | `python -m autodeck2 outline|autofit|ingest|doctor|cache` and the wizard |

Config: `engine/config/default.yaml` (JSON syntax inside a .yaml file), optional gitignored
`local.yaml`; `AUTODECK2_IGNORE_LOCAL_CONFIG=1` (set by `engine/tests/conftest.py`) ignores it.
The `sheets` block holds sheet size, usable envelope, seam gap, piece spacing, sampling and
`grain_angle_deg: null`. The seam snap keys (`seam_snap_*`, `seam_axis_*`,
`seam_tidiness_weight`) are not in the yaml; their defaults come from `sheets.DEFAULTS`
(`sheets.py:52-95`) merged with `seamsnap.SNAP_DEFAULTS` (`seamsnap.py:147-172`) and can be
overridden in the same `sheets` block.

Tests: `engine/tests/`, 17 test files plus `conftest.py` and `helpers.py`. The seam/sheet ones are `test_sheets.py`,
`test_sheet_seams.py`, `test_seamsnap.py`, `test_seamplan.py`, `test_nesting_speed.py`,
`test_overlay_geometry.py`. `test_contract.py` is the v1 boundary test. `test_real_scan.py` is the
one `slow` test that `-m "not slow"` deselects.

### 2.4 Layer 3: `engine-v1/src/autodeck/`

The v1 analysis engine, version pinned at `0.3.6` (`engine-v1/src/autodeck/__init__.py`): mesh
I/O, curvature, segmentation, surface development (LSCM/ARAP + exact geodesics), relief and
boundary fields, patterns (`patterns.py` computes the boat frame, `bow_sign` and
`bow_confidence` from cross-boat width slices, lines 197-202), texture evidence (`texture.py`).
Docs in `engine-v1/docs/` (ARCHITECTURE, WORKFLOW, SEGMENTATION, SURFACE_DEVELOPMENT,
CAM_GEOMETRY, PATTERNS, CONFIG_REFERENCE, TESTING). Config `engine-v1/config/default.yaml`.
Tests in `engine-v1/tests/{unit,geometry,integration,regression}`.

**The v1 boundary is a contract.** `engine/autodeck2/v1compat.py` is the only v2 module that
imports `autodeck`. `check_compatibility()` runs at startup and in `engine/tests/test_contract.py`:
it pins `V1_REQUIRED_VERSION = "0.3.6"`, fingerprints the v1 source (sha-256 over every `.py`, so
edits in v1 invalidate caches) and verifies every wrapped signature. If you change anything under
`engine-v1/`, expect `test_contract.py` to fail and treat that as the system working; update
`v1compat.py` deliberately.

### 2.5 What is gitignored, and which of it is irreplaceable

| Path | Size | Replaceable? |
|---|---|---|
| `engine/outputs/runs/*` | 913 MB, 16 runs | **No.** Contains every `seams.json`, `seams_previous*.json`, exported `sheet_*.dxf`, `final_auto.dxf`, `run.json`. |
| `app/inputs/*` | 5 x 807 MB | Only if the owner still has the original scans. The five files are copies of essentially the same Key West scan. |
| `inputs/` | 258 MB | Yes: `tools/make_test_boat.py --out inputs/testboat` regenerates from a seed. |
| `engine/cache/`, `app/cache/`, `engine-v1/cache/` | 12 MB, 7.5 MB | Yes, always safe to delete; the next run is slower. |
| `.venv/` | | Yes: delete it and double-click `AutoDeck.bat`. |
| `*/config/local.yaml` | | local overrides |

Run folders, as of 2026-10-07 (seams = entries in `seams.json`; "-" = no file):

| Run | Pattern / axis | Seams | Sheet DXFs | Note |
|---|---|---|---|---|
| `21kwcockpit-1-20260901-180939` | teak, has axis (3.68 deg, confidence 0.649) | 4 (old schema) + `seams_previous.json`, `_1`, `_2`, `_3`, `seams_optimiser_run.json.bak` | 0 | **AXIS fixture**, pinned by name in tests |
| `21kwcockpit-1-20260901-172519` | none, no axis | 8 | 1 | **NO-AXIS fixture**, pinned by name |
| `21kwcockpit-2-20260901-184924` | teak, axis | 8 | 0 | |
| `21kwcockpit-3-20260902-024834` | teak, axis | 12 (new schema, optimiser ids) | 0 | audit G56: optimiser set is incomplete, cause unknown |
| `21kwcockpit-4-20260902-032533` | none | - | 0 | |
| `21kwcockpit-20260831-215445` and four `21kwcockpit-20260901-04*` | teak, axis | - | 0 | earlier runs of the same scan |
| `scan-20260901-031249` | teak, axis | 1 | 3 | audit D1 example: three DXFs from an older 3-seam export beside a 1-seam `seams.json` |
| `scan-20260901-024404`, `-024930`, `-030456`, `-031346`, `-040011` | synthetic test boats | - | 0 | |

Backups of every `seams*.json`, `sheets.json` and `sheet_report.md` from five runs were taken
during the audit (17 files). Copies on 2026-10-07: `C:\Users\marcu\AutoDeck-backups\backup_runs_2026-10-07\`
(internal drive), `D:\AutoDeck-scratch\backup_runs_2026-10-07\` (USB stick) and the Claude
session's Temp folder. The audit report's step 0 says they are under `tools/audit/`; they are
not in the repo. Take fresh ones (section 7.1) before any experiment.

### 2.6 Other copies of the project on this machine

These exist and were listed, not inspected. None of them is the live project. Do not delete any
of them without the owner, because at least one is still load-bearing:

- `C:\Users\marcu\AutoDeck`: the previous location, a stale tree at the old `ecb7b30` baseline
  (pre-rewrite id) with uncommitted pre-5.3 modifications and no remote. **Do not work there.**
  Its one job today is holding the scans. **Every existing `run.json` points its `input_path` at this folder's
  `app/inputs/`**, so reopening a run in the app reads the scan from here (3.9, `load_run`).
- `C:\Users\marcu\IAS-Portfolio`: a plain clone of GitHub `main` made on 2026-10-07 while the USB
  stick was unplugged (code only, no `.venv`, no runs, one stray local edit). Delete it, or
  `git pull` there before using it as a spare checkout.
- `C:\Users\marcu\OneDrive\Documents\AutoDeck_Portable`: the old OneDrive bundle.
- `D:\AutoDeck2` (holds an `outputs/` folder) and `D:\AutoDeck_Portable` (old v0.4.2 and v1
  bundles with their own launchers).

### 2.7 Launchers

`AutoDeck.bat` (`cd` to its own folder; sets `AUTODECK2_ROOT=<root>\engine`,
`AUTODECK_V1_ROOT=<root>\engine-v1`, `PYTHONPATH=<v1>\src;<engine>;<app>`; on first run finds a
real Python 3.12+ via `py -3.12`, `py -3`, `python`, `python3`, creates `.venv`, then
`pip install -e engine-v1[rhino]`, `pip install --no-deps -e engine`,
`pip install libigl==2.6.1 -e app`; then `python -m autodeck_app %*`). `AutoDeck.command` is the
macOS equivalent; `AutoDeck.app` wraps it. The Desktop and Start-menu shortcuts and
`AutoDeck.lnk` target `AutoDeck.bat`.

Environment in the `.venv` on 2026-10-07: Python 3.12.10, numpy 2.5.2, scipy 1.18.1,
shapely 2.1.1, ezdxf 1.4.4, libigl 2.6.1, rhino3dm 8.32.1, Flask 3.1.3, pytest 9.1.1,
Pillow 12.3.0, playwright 1.62.0 (installed by hand for `tools/ui_smoke.py`; not declared in any
`pyproject.toml`; Chromium builds are present under `%LOCALAPPDATA%\ms-playwright`).
matplotlib is not installed and must not be added. Node v24.19.0 is on the machine (used during
the audit to replay `app.js` functions; optional).

Environment variables (none are secrets): `AUTODECK2_ROOT`, `AUTODECK_V1_ROOT`,
`AUTODECK_INPUTS_DIR`, `AUTODECK_PREVIEW_CACHE`, `AUTODECK_RUNS_DIR`, `AUTODECK_HOST`,
`AUTODECK_PORT`, `AUTODECK_PREVIEW_FACES`, `AUTODECK_PREVIEW_TEXTURE_MAX`, `AUTODECK_WORKERS`,
`AUTODECK2_IGNORE_LOCAL_CONFIG`.

### 2.8 Tools

| Script | What it does | Needs |
|---|---|---|
| `tools/make_test_boat.py --out <dir>` | deterministic textured synthetic scan | Pillow |
| `tools/profile_run.py --scan <obj> [--no-cache]` | per-stage wall clock of the engine | a scan |
| `tools/render_sheets.py --run <run dir> [--out x.png]` | PNG of the run's `sheet_*.dxf`, arcs rebuilt from bulges as VCarve does | ezdxf, Pillow, an export |
| `tools/ui_smoke.py --url ... --run-id ...` | drives the page in headless Chromium: tabs, seam tool, remove/clear/undo, failing save | playwright + Chromium, a running server |
| `tools/audit/` | the 2026-10-07 probe scripts (h1/, wf/, wf2/), `lead_notes.md`, `engine_tests_2026-10-07.log`, `README.md` | see 7.9 |

---

## 3. How the seam and sheet pipeline works

This is the part of the system the audit covered and the part you will most likely change.
Line numbers are for the tree at commit `9b34ce9` and were checked on 2026-10-07.

### 3.1 Frames

Five coordinate frames. Everything stored on disk is in frame 3.

1. **uv**: each panel's own development frame, mm, centred on the panel.
2. **boat-plan**: `placed = R(uv - mean(uv)) + world_centroid - primary_world_centroid`
   (`layout.py:8-17` docstring, `144-150`), world XY with the primary panel's centroid at the
   origin. True relative positions.
3. **placed** (= nest layout): boat-plan plus a per-panel `nest_offset` for panels that collided
   with another, which are moved into a row below the primary with `layout.nest_gap_mm` (150 mm)
   between them (`layout.py:180-205`). On the AXIS fixture panels 2, 4 and 5 are moved by
   (23.5, -2375.9), (983.0, -1776.5) and (2074.4, -1365.9) mm; panels 1 and 3 are not.
   `final_auto.dxf`, `final.dxf`, `seams.json`, every hover point, the boat axis, the snap
   reference pool and `split_panel` all live here. Consequence: nested panels are "near" each
   other at nest distances, not boat distances, and the corrector has no notion of panel.
4. **sheet**: `sheets.sheet_transform(axis)` (`sheets.py:633-649`) is the 2x2 rotation
   `[[ay, -ax], [ax, ay]]` that takes placed coordinates to a frame where the boat axis is +Y
   (the 80 inch, grain direction). `nesting.Placement.apply(xy, base)` (`nesting.py:34-54`) is
   `xy @ (R(rotation_deg) @ base).T - origin + offset` with `rotation_deg` 0 or 180, `origin` the
   rotated piece's bbox minimum and `offset = (x + 12.7, y + 12.7)` because the usable area is
   centred in the sheet. Sheet DXFs are written in this frame with the sheet corner at (0, 0).
5. **bow-up screen** (`app.js:361-396`): per panel subtract its nest offset (back to boat-plan),
   negate y, rotate by `theta = -90 - atan2(-bow_y, bow_x)` degrees so `boat.bow_direction`
   points up. `flatToModel` is the exact inverse and re-adds the offset of the panel containing
   the point. Without a known bow the raw nested layout is shown unturned.

**The boat axis.** Stored by the pattern stage in `run.json -> teak.frame.longitudinal_axis`
(AXIS fixture: `[0.99794, 0.06419]`, 3.680 degrees, `axis_confidence` 0.649, `bow_sign` 1,
`bow_confidence` 0.824). It is a direction, so translation between frames 2 and 3 does not change
it. Always obtain it through `sheetjob.resolve_frame(run_dir, options)` (`sheetjob.py:124-203`),
never by reading `run.json`: a `grain_angle_deg` override becomes `[cos, sin]` with confidence
1.0, the bow is carried over by the sign of its projection on the new axis and dropped below 0.25,
and a disagreement above 3 degrees with the pattern axis warns. `plan`, `preview`, the hover and
the optimiser all call it with the same `sheets.settings(config)`. The two master directions are
`along = unit(axis)` and `across = (along_y, -along_x)` (`seamsnap.master_directions`
`584-626`); `across` is bit-identical to row 0 of `sheet_transform` and is the opposite arrow of
`run.json`'s `transverse_axis` (documented in the code).

### 3.2 `seams.json`

`<run>/seams.json`, placed frame, `{"seams": [...]}`. `sheets.Seam` (`sheets.py:341-456`)
reads both schemas with `from_dict` and always writes the new one.

Old schema (the AXIS fixture's live file, written before 5.3):

```json
{"seam_id": "s1", "x1": 110.617, "y1": -1005.956, "x2": 86.179, "y2": -453.044, "panel_id": null}
```

Read as `mode ""` (free direction), `raw None` (the coordinates are the drawing), `snap true`.

New schema (written by every save since 5.3; this one from `21kwcockpit-3`):

```json
{"seam_id": "auto-1-across-2", "x1": 932.858, "y1": -840.181, "x2": 817.080, "y2": 959.746,
 "panel_id": 1, "snap": true,
 "raw": [932.858, -840.181, 817.080, 959.746], "mode": "across", "angle_deg": null}
```

- `x1..y2`: the seam **as it will be cut**, after straightening.
- `raw`: the line as placed. Every correction is recomputed from `raw`, never from `x1..y2`, so
  re-planning is idempotent (`moved_to` carries `raw` forward).
- `mode`: `""`, `"along"`, `"across"` or `"angle"` (with `angle_deg`); what the user asked for,
  so a later grain change re-aims the seam. Unknown or upper-case modes raise.
- `panel_id`: the panel the seam is bound to; `null` means "wherever the drawn segment crosses".
- `snap`: per-seam opt out ("as placed" in the UI); parsed through `_as_flag`, so the string
  `"false"` is false.

`read_seams` (`458-463`) and `write_seams` (`466-488`; deliberately non-atomic, in place, with
the reason measured and documented in its docstring). The optimiser keeps the previous set in
`seams_previous.json`, rotating older copies to `seams_previous_N.json`.

### 3.3 The production path: `sheetjob.plan()` and `preview()`

`plan(run_dir, config, seams=None, progress=None, write_files=True)` (`sheetjob.py:419-534`):

1. `options = sheets.settings(config)` (`sheets.py:98-126`; raises for `seam_gap_mm <= 0` and
   a tidiness weight outside 0..1).
2. `source_dxf` (`41-48`): `final.dxf` if present, else `final_auto.dxf`, else
   `FileNotFoundError`.
3. Seams: the list passed in, else `read_seams`.
4. `read_fitted_dxf` (`sheets.py:686-714`): every LWPOLYLINE on a layer containing `__PANEL_<n>`
   that does not start with `PATTERN_` becomes a `Loop` of `(x, y, bulge)` for panel n; pattern
   layers become groove lines. Circles are kept as two-vertex bulged loops.
5. `resolve_frame`, then `rotation = sheet_transform(axis)`.
6. `apply_snap(seam_list, loops, axis, options)` (`253-312`), see 3.5.
7. Per panel in id order: `classify_loops` (`717-730`, largest area = outer, the rest holes),
   then `split_panel` (`511-586`), see 3.4. Pieces are `P<n>` for an uncut panel and
   `P<n>-<k>` otherwise.
8. `oversize_report(pieces, rotation, options)` (`733+`): each piece's extent in sheet axes
   against 990.6 x 2006.6 (both rotations), with a hint ("needs a seam across the boat").
9. `nesting.nest(pieces, rotation, options)` (`nesting.py:235-320`), see 3.7.
10. If `write_files` and there are sheets: `nesting.write_sheet_dxfs` (`528-662`), see 3.8.
11. `status`: `NEEDS_SEAMS` if anything is oversize, unplaced or refused; `EMPTY` if no sheets;
    else `OK`.
12. Result dict: `status, source_dxf, seam_count, piece_count, oversize, refused, summary
    (sheet_count, piece_count, unplaced_piece_ids, utilisation, ...), files, warnings,
    max_arc_rebuild_error_mm, boat_axis, boat_axis_confidence, boat_frame, seams (as cut, full
    precision), seam_snaps, pieces (id, panel, area, from_seam, holes), sheets (placements)`.
    With `write_files` it also writes `sheets.json` and `sheet_report.md` into the run folder.

`preview(run_dir, config, seams=None)` (`537-609`) is `plan(write_files=False)` plus: re-split
every panel from exactly `result["seams"]` (the straightened list), sample each piece's outer and
holes at 4 mm, push them through `Placement.apply`, and return `preview.sheets[].rings[]` with a
`hole` flag. Then it replaces `files` with `exported_sheets(run_dir)` (`612-628`), which globs
`sheet_*.dxf` on disk and merges whatever `sheets.json` says about them, with no check that they
match the current seams. Measured: preview and plan agree exactly; the preview is about 0.6 s on
the AXIS run. Note that "preview equals cut" is true by construction, including when both are
wrong.

### 3.4 `split_panel` and the ring rebuild

`sheets.split_panel(panel_id, outer, holes, seams, options)` (`sheets.py:511-586`):

- Build the panel polygon with holes at `sample_step_mm` = 1 mm (`loop_polygon` `293-303`,
  `buffer(0)` if invalid).
- A seam is **relevant** if `seam.panel_id == panel_id`, or if `panel_id` is `None` and the drawn
  segment (its current `x1..y2`) intersects the panel (`533-540`).
- `reach = bbox diagonal + 10 mm` (`545`). Every relevant seam is extended by `reach` both ways,
  buffered by `seam_gap_mm / 2` = 3 mm with flat caps and mitre joins (`547`), unioned, and
  subtracted from the panel (`548`). **This is the whole-line cut**: a 450 mm chord becomes an
  infinite line through the panel (audit A1).
- Parts under `min_piece_area_mm2` (400) are dropped with a count-only warning (`553-557`); there
  is no width test (A2).
- Survivors are sorted by `(-area, bounds)` and named `P<n>-<index + 1>` from the sort index
  (`568`, `583`), so ids re-rank after every edit (B4).
- Each part is oriented CCW and its rings rebuilt by `_rebuild_ring` (`589-626`), which tags each
  ring edge by the original sampled point nearest its midpoint (within 0.75 mm), picks one
  "dominant" source loop per ring and retags edges from any other loop as introduced (`624-625`),
  then `rebuild_loop` (`224-290`) collapses runs of same-source arc edges into one bulge via a
  three-point fit, falls back to the sampled polyline when the fit deviates more than
  `arc_rebuild_tolerance_mm` (0.05) (`282-287`), and emits every introduced edge as its own
  straight vertex (`256-259`). `max_arc_error_mm` counts only arcs that passed.
- A rebuilt hole is kept only if it has 3 or more vertices (`576`); an outer ring under 3 vertices
  makes the piece "degenerated; skipped" (`579`). A full circle rebuilds to 2 vertices, so the
  207 mm round cut-out is dropped from every piece that contains it (B1).

### 3.5 Snapping rules (the corrector)

`seamsnap.snap_seam(x1, y1, x2, y2, references, options, axis=, direction_locked=)`
(`seamsnap.py:1067-1184`), in order:

0. Snapping disabled or length under `MIN_SEAM_LENGTH_MM` (5 mm): unchanged.
1. `direction_locked=True` (a seam with a mode): `_locked` (`1187-1221`), refinement only, one
   sideways shift, never a turn.
2. Otherwise up to `_MAX_ROUNDS` = 4 rounds: **axis step** (default `seam_axis_priority` true):
   the nearer master by undirected angle, captured if within
   `min(seam_axis_snap_deg = 20, 45)`; `_set_direction` swings the line about its drawn midpoint
   keeping each end's projected distance, so length is preserved exactly (the ends of a 2 m seam
   drawn 19.9 degrees off move 346 mm, exempt from the 60 mm guard by design). Then
   **`_refine_position`** (`892-965`): slide along the seam's normal onto the nearest `line` or
   `seam` reference whose direction is within `min(0.5 deg, atan(1 mm / half_length))` (so a
   console wall 0.145 degrees off only captures chords shorter than about 790 mm), within
   `seam_snap_reach_mm` 300, shift at most `seam_snap_offset_mm` 25 and
   `seam_snap_max_move_mm` 60; seam references that run alongside are skipped
   (`_runs_alongside` `312-340`). If no master claimed the seam, the **feature step**: collinear /
   parallel / perpendicular / tangent to fitted edges at `seam_snap_angle_deg` 5, 25 mm, 300 mm.
3. Stops when a step moves under 1e-6 mm; returns the original bytes when nothing changed.

References: `references_from_loops` (`543-577`) offers every fitted segment, straight ones of
chord at least 15 mm as kind `line`, bulged ones of radius at least 10 mm as kind `arc`; outer
and cut-out edges get the same kind and are labelled by panel; `axis_references` adds the two
masters. The reference `panel_id` is never tested in `_refine_position` (A6).

`sheetjob.apply_snap` (`253-312`) is the production loop, per seam in list order: `snap` false or
snapping off: the raw line, applied false, still added to the pool. Otherwise `_aimed`
(`377-408`) swings `raw` about its midpoint onto `seamplace.direction_for(axis, mode, angle)` for
a moded seam (lock dropped when the mode cannot be applied because there is no axis), then
`snap_seam(direction_locked=locked)`, then `_restated` (`315-357`) rewrites the note against the
drawing. Each cut seam of length at least 15 mm joins the pool for the seams after it.
`sheetjob.snapped_seams` and `seamsnap.snap_seams` are test-only duplicates of this loop.

Settings and defaults (all overridable under `config["sheets"]` and from the page's Settings
disclosure, validated by `server._SHEET_NUMBERS`):
`seam_snap_enabled` true, `seam_axis_snap_deg` 20, `seam_axis_priority` true,
`seam_snap_angle_deg` 5, `seam_snap_offset_mm` 25, `seam_snap_reach_mm` 300,
`seam_snap_min_ref_length_mm` 15, `seam_snap_min_ref_radius_mm` 10, `seam_snap_max_move_mm` 60,
`seam_snap_use_axis` true, `seam_tidiness_weight` 0.35.

### 3.6 The hover path (placing a seam)

1. Pointer move in either view -> `pumpHover` (`app.js:1125`, 40 ms throttle, one request in
   flight) -> `POST /api/seam/hover` with the placed-frame point (`flatToModel` from the flat
   view; `pick_flat` world -> nearest panel surface -> uv -> `placement.apply` from the 3D view,
   `bridge.py:1504`).
2. `bridge.seam_hover` (`374-456`): `hover_context` (settings + `BoatFrame`, cached on overrides
   and `run.json` mtime); `seamplace.direction_for(axis, mode, angle)` (`seamplace.py:74-122`:
   along, across, or along rotated by `angle` anticlockwise; 0 -> along, 90 -> across exactly);
   `seamplace.seam_through(point, unit, polygons, options)` (`156-212`): intersect the infinite
   line through the point with every panel polygon (holes included) and return **every chord of
   at least 5 mm on every panel**, oriented along `unit`, sorted along the line, with
   `length_mm`; then `_settled` (`542-589`): each chord is slid with the locked snap against
   `edge_references` (cached on the DXF mtime) + `seam_references` (seams.json **as stored**,
   cached on its mtime) and, if it moved, re-trimmed with `seam_through(panel_ids=[pid])` keeping
   the chord whose midpoint is nearest the slid midpoint (`583`). The settle note is discarded.
   Reply: `segments` (or a reason when the list is empty), `seams_world` for the 3D view.
3. `segmentUnder` (`app.js:1173-1186`) keeps the chord whose parameter range brackets the pointer
   (2 mm grace along the chord) and shows "N mm on panel k".
4. Click -> `placeHovered` (`1300-1320`) pushes `{x1..y2, panel_id, mode, angle_deg, snap: true,
   raw: the chord}` with **no `seam_id`** -> `seamsChanged` (`898`) bumps `seamsRevision`, sets
   `seamsPending` -> `pushSeams` -> `replanSheets` (`1535-1587`): `POST /api/sheets/seams` with
   the whole list plus `sheetSettings()` (`1009`), one request at a time, coalesced.
5. Server: `checked_seams` (`server.py:150-186`; finite numbers under 1e6, `raw` of length 4) and
   `sheet_options` (`205-235`) -> `bridge.sheet_preview(save=True)` (`277-322`): names id-less
   seams `s{index + 1}` (`292`), `sheetjob.preview`, then `write_seams` of the **cut** seams.
   Save happens only after the plan succeeds; a plan that raises leaves `seams.json` untouched
   and returns 400 (`server.py:534-536`). `NEEDS_SEAMS` is HTTP 200.
6. Reply handling: `seamStamp` (`1376`) = `{rev, told, clean}`; `stampIsCurrent` (`1381`) adopts
   the reply's whole list only when it is current, else `adoptSeams` (`1423`) merges per
   `seam_id`. This is what stops a stale reply resurrecting a deleted seam.

Remove, "as placed" and "Clear all" use the same POST (clear posts `[]`). Recalculate re-posts
with the page's settings. Opening a run, the Sheets tab and every job completion call
`refreshSheets` (`1508`): `GET /api/sheets` (engine defaults, writes nothing), then a POST when
the page's settings are non-default (two nests).

### 3.7 The optimiser ("Find the best seam layout")

`POST /api/seams/optimise` (busy-guarded) starts the job `bridge.job_optimise_seams`
(`bridge.py:652-754`, budget `OPTIMISE_BUDGET_S` = 90 s; the page never sends a budget) ->
`seamplan.optimise(run_dir, config, progress, time_budget_s)` (`seamplan.py:842-1020`):

1. `source_dxf`, `resolve_frame`, `master_directions` (none -> refusal `NO_AXIS` with wording
   that tells the user to set the grain angle or re-run with a pattern), `sheet_transform`.
2. `_read_panels` (`1141`): per panel a 4 mm proxy polygon, bounds in the sheet frame
   (u = across, v = along), area, nest offset.
3. Baseline: `_confirm` (`2406-2424`) = `sheetjob.plan(write_files=False)` on the existing seams,
   `_metrics` (`2498`).
4. `_plan_candidates` (`1645`): per panel, shortlists of full-span along/across cut sets.
   `_positions` (`1289`) mixes sheet-filling offsets, even splits, fitted-edge offsets within
   `EDGE_TOLERANCE_DEG` 2 (`_edge_offsets` `1251-1287`), mirrored copies about the centreline and
   a 50 mm sweep; `_cut_sets` (`1448`) keeps sets whose bands fit `usable - 0.5 mm` and are at
   least `MIN_BAND_MM` 100; a panel past the deadline gets `_fallback_candidates` (`1849`,
   kerf-aware equal bands).
5. `_search` (`2188`): coordinate descent one panel at a time, largest first, two sweeps;
   `_evaluate` (`2121`) nests proxy pieces with `nesting.nest` on a 25 mm grid; key =
   `(oversize, sheets, waste bucket incl. tidiness penalty, seams, cut length)`. Climb 1 on waste,
   climb 2 on the blended key. Tidiness terms: `_symmetry` (`670`), `_rectangularity` (`758`),
   `_alignment` (`706-728`, credits a cut within 25 mm of a fitted edge on the assumption that the
   corrector will slide it there), `_joins` (`750`); weights at `378-409`; `piece_count` is never
   ranked.
6. Confirmation: top 3 on waste + top 12 blended -> `_seams_for` (`2332`) builds real `Seam`s
   with `panel_id`, mode along/across, `snap=True`, `raw` = endpoints, ends from `_span`
   (`2373-2394`: first entry to last exit of the infinite line through the panel, anchored at the
   foot of the perpendicular from the placed-frame origin, C4) -> `_confirm`. `improved` =
   exact(after) < exact(before). Wording is "best found" everywhere.

Back in the bridge: nothing is written unless `improved` and the seam list is non-empty. Then
`_keep_older_backup` renames `seams_previous.json` to the first free `seams_previous_N.json`,
`seams.json` is copied to `seams_previous.json`, and the result goes through
`sheet_preview(save=True)`. The page polls `GET /api/jobs/<id>` every 700 ms (`trackJob`
`2106`) and renders `renderOptimiseResult` (`1760`). Undo (`undoOptimise` `1838`) fetches
`seams_previous.json` through `/api/file` and POSTs it as an ordinary edit.

Measured on the AXIS fixture (hand seams staged, 60 to 90 s): before `NEEDS_SEAMS`, 2 sheets,
13 pieces, 5 oversize, 52.97 % waste; after `OK`, 4 sheets, 17 pieces, 7 seams, 39.11 % waste;
295 arrangements, 12 confirmed, 51 to 68 s wall; deterministic at a fixed budget.

### 3.8 Nesting

`nesting.nest(pieces, base_rotation, options)` (`nesting.py:235-320`): each piece's outer and
holes are sampled at 1 mm, rotated by `R(angle) @ base_rotation` for angle in (0, 180), repaired
with `buffer(0)` if invalid, translated so the bbox minimum is (0, 0) (that minimum is kept as
`origin`). A piece whose bbox exceeds 990.6 x 2006.6 in both rotations is reported unplaced
("add a seam") and never searched. Order: `(-area_mm2, piece_id)` (`283`). For each piece,
sheets are tried in index order and the first sheet with a spot wins; otherwise a new sheet.
`_find_spot` (`323-368`) walks y then x over a 5 mm grid (`_grid_steps` `219-232`) and returns the
lexicographically lowest (y, x) over both rotations (180 only when strictly better).
`_first_free_x` (`371-402`) is the v5.3 speed-up: a vectorised probe-point prefilter that can only
remove candidate positions, followed by the unchanged exact test
`blocked.intersects(affinity.translate(variant.polygon, x, y))` (`400`). Occupancy per sheet
(`_commit` `405-424`) is the union of placed polygons buffered by `part_spacing_mm` 20 with round
joins (`422`); touching counts as a collision. Verified bit-identical to the `958d8f0` baseline nester on
every comparison made (section 5, theme D) and 20 to 87 times faster.

### 3.9 Export, routes, and what each action writes

**Export**: `POST /api/sheets/export` (busy-guarded) -> `bridge.job_sheets` (`601`) ->
`sheetjob.plan(write_files=True)` -> `nesting.write_sheet_dxfs` (`528-662`): one
`sheet_NN.dxf` per sheet, `$INSUNITS = 4` (mm), layers `CAM__<piece id with - as _>` (closed
LWPOLYLINEs with bulges), `PATTERN_<KIND>__<piece>` (grooves clipped per piece),
`SHEET__OUTLINE`, `SHEET__USABLE`; every file is re-read with ezdxf and checked closed;
`_refuse` (`481-525`, `EXPORT_TOLERANCE_MM` 0.1) measures each piece again as written and leaves
an over-envelope piece out of the file, reporting it under `files[].refused`. Older
`sheet_NN.dxf` with higher numbers are never removed (D1).

**HTTP routes** (`server.py`): `GET /`, `GET /api/state`, `POST /api/scan/upload`,
`POST /api/scan/path`, `POST /api/scan/pick`, `GET /api/scan/mesh`, `GET /api/scan/texture`,
`GET /api/jobs/<job_id>`, `GET /api/runs`, `POST /api/run/open`, `POST /api/run/outline`,
`POST /api/run/autofit`, `POST /api/run/ingest`, `GET /api/sheets`, `POST /api/sheets/seams`,
`POST /api/pick` (no caller), `POST /api/seam/hover`, `POST /api/sheets/export`,
`POST /api/seams/optimise`, `GET /api/overlays`, `GET /api/file/<run_id>/<name>` (names limited
to `SAFE_FILES`, lines 20-26, plus `sheet_NN.dxf`). `busy_guard` (409 while a job runs) is on
export, optimise, open, outline, autofit and ingest; the seams POST, the sheets GET and the hover
are not guarded (E2).

**Files written, by action**:

| Action | Writes |
|---|---|
| Outline job | new run folder: `run.json`, `panels.json`, `outline.3dm`, `outline.dxf`, `outline_report.md`; engine cache entry under `engine/cache/` |
| Auto-fit job | `final_auto.dxf`, `auto_cam.3dm`, `autofit.json`, `autofit_report.md`, `autofit_panel<n>.png` |
| Ingest job | `final.dxf` (if valid), `calibration.json`, `calibration_report.md`, `final_report.md` |
| Open run | preview cache under `app/cache/preview/`; a `debug/` folder in the run only on an engine cache miss |
| `GET /api/sheets` | nothing |
| `POST /api/sheets/seams` (place, remove, as placed, clear, recalculate, undo) | `seams.json`, after a successful plan (and even without fitted geometry, `bridge.py:298-300`) |
| Optimise job | only when improved: `seams_previous_N.json` (rename), `seams_previous.json` (copy), `seams.json` |
| Export job | `sheet_NN.dxf`, `sheets.json`, `sheet_report.md` |
| Seam pipeline | never `run.json`, never the fitted DXFs |

---

## 4. Rules that are decided

Each was chosen after measurement or an explicit instruction from the owner. Do not reverse any
silently; if one blocks you, raise it with the owner and say why.

1. **The boat axis is the master for seam directions.** A long seam is exactly parallel to the
   centreline and the planks, a short one exactly 90 degrees to it, overruling alignment with
   nearby edges. Reason: a seam a degree or two off fans open against the plank lines ("pie
   shapes").
2. **Resolve the axis only through `sheetjob.resolve_frame` / `resolve_axis`.** Reason: the
   preview once resolved it separately, ignored the manual grain override and drew pieces outside
   the sheet exactly when detection was unsure.
3. **Capture angle 20 degrees** (`seam_axis_snap_deg`), separate from the 5 degree feature
   tolerance. Reason: the owner said these seams are always square, and 5 degrees let a roughly
   placed line stay crooked.
4. **Bow direction is detected, not guessed** (`bow_sign`, `bow_confidence` from width slices);
   the seam tab is drawn bow up and says so when the bow is uncertain. Reason: the layout must be
   judged by eye the way the boat sits.
5. **Sheets 1016 x 2032 mm, usable 990.6 x 2006.6 mm, seam gap 6 mm, piece spacing 20 mm,
   rotation 0 or 180 only, never mirrored.** Reason: the material is directional and the owner
   gave these numbers.
6. **Arcs survive as bulges.** Splitting refits sampled runs back to single bulges rather than
   dense polylines. Reason: VCarve toolpaths and the fitted geometry's intent. (The audit shows
   the implementation falls short, B2/B3; the rule stands.)
7. **DXF conventions**: `bulge = tan(theta/4)`, LWPOLYLINE `(x, y, bulge)`, `$INSUNITS = 4`,
   outer rings CCW and holes CW. Reason: what VCarve and `ingest.py` round-trip.
8. **`final.dxf` beats `final_auto.dxf`** everywhere downstream. Reason: a hand-drawn, validated
   outline is the owner's final word.
9. **Nesting output is frozen.** `nesting.nest` must stay bit-identical to `958d8f0` (the 2026-09-02 baseline; `ecb7b30`
   before the 2026-10-07 history rewrite) on the same
   pieces (`engine/tests/test_nesting_speed.py`, compared with `==`). The table may be re-captured
   only when the pieces change, from the committed nester on the new pieces, with the old-vs-new
   diff as proof. Reason: a faster nester that moves parts is a regression; the table is the proof
   the speed-up is a pure filter.
10. **The optimiser is a bounded heuristic search**: it reports "best found", never "optimal", and
    every number shown comes from `sheetjob.plan`, never the proxy. Reason: the owner wants
    honest numbers.
11. **Removing seams must always work**, whatever state the plan is in. Reason: removing seams is
    the way out of a layout that will not fit; the owner was once blocked from doing so.
12. **The 3D overlay is a smoothed rendering; the flat view and the DXFs are exact.**
    `bridge._make_lifter` is a degree-1 moving-least-squares plane fit over the development
    (K = 24, tricube weights), offset 1.5 mm proud of the surface. Rejected alternatives, do not
    re-propose: exact igl point location (changed nothing), Laplacian mesh smoothing (shrinks the
    boat, 0.5 to 7.7 mm median), moving average along the curve (rounds real corners by up to
    9.4 mm). Reason: measured in `START_HERE_AI.md` section 7.3.
13. **v1 is imported only through `engine/autodeck2/v1compat.py`**, which pins version 0.3.6 and
    fingerprints the source. Reason: v1 is an editable install with no git history; drift must
    fail loudly.
14. **Test fixtures are pinned by name**, never "the newest run". Reason: a newest-run selection
    once re-aimed the tests at data created minutes earlier.
15. **The engine cache key excludes the worker count** (`parallel`). Reason: the same scan must
    give byte-identical geometry at any setting.
16. **LF line endings in every source file.** `Path.write_text()` on Windows converts to CRLF and
    has damaged files here before; pass `newline="\n"` or use an editor tool. `core.autocrlf` is
    `false`; `git ls-files --eol` shows 310 tracked files as LF and 3 as CRLF (all three are
    audit probe scripts under `tools/audit/`, copied verbatim; leave them alone).
17. **No new dependencies; matplotlib is not installed and must not be added.** Reason: the owner's
    machine is set up once by the launcher; the owner declined extra moving parts.
18. **Comments explain why**, in full sentences; numpy-vectorised code; dataclasses for value types;
    `from __future__ import annotations`; do not reformat code you are not changing.
19. **Texture evidence can only reinforce a boundary the geometry already suspects**, never create
    one (excluded from the decisive-evidence test). Reason: a stain must not invent a wall line.
20. **About half the logical CPUs by default** (`parallel.cpu_fraction` 0.5). Reason: owner's
    request ("lets shoot for 50%").
21. **The project stays out of OneDrive and iCloud.** Reason: cloud sync evicts `.venv` files and
    launches hang.

---

## 5. State of the audit

`docs/seam-audit-2026-10-07.md` is the authority: section 1 how it works, section 2 confirmed
defects by theme (A to E, with low-severity tables), section 3 nine disputed items, section 4
refuted sub-claims and non-bugs, section 5 fourteen test gaps, section 6 the fix order, section 7
fifteen owner questions. 111 findings (F01-F50, G01-G61), each independently reproduced and
challenged; every item there carries its finding ids, reproduction script, blast radius and effort.
The probe scripts are in `tools/audit/` (see `tools/audit/README.md`). Nothing has been fixed.

The lead's verdict: the nester is correct; nearly everything the fabricator experiences as glitchy
comes from `sheets.split_panel` and its ring rebuild plus the hover/snap glue around it; the
optimiser inherits those and adds alignment and objective gaps; the autonest's weakness is
quality, not correctness.

### Theme A: preview versus cut, placement and snapping

| Id | What the fabricator sees | Where | Cause in one line |
|---|---|---|---|
| **A1** (F02 lead H1, F13, G25, G28), High | Hover a 450 mm join beside the console, click, and the whole deck is split along that line (a 447 mm starboard chord gave 2 pieces of 3,687,778 + 1,131,148 mm2; a 989 mm bow chord split the deck into halves); flat tab shows one chord, 3D tab the whole line, the cut follows 3D | `sheets.py:545-547`, `535-540`; `bridge.py:241-275`; `app.js:1308-1316`, `705-721` | every relevant seam is extended by the bbox diagonal + 10 mm before the kerf; the page stores one chord |
| **A2** (G02, F35, G26), High | 1156 x 5.6 mm "toothpick" listed as too big; 5 to 10 mm wide half-metre parts nested and exported; a seam within 3 mm of an edge shaves the part with no join and no message | `sheets.py:553`, `65` | the only sliver test is area >= 400 mm2; no width test |
| A3 (F05), Medium | sweeping along the boat beside the console the preview vanishes over a 100 mm band ("not on a panel") | `bridge.py:581-583` | after a slide the re-trim keeps the chord nearest the slid midpoint, not the one under the pointer |
| A4 (F09, G60, F19), Medium | near a straight outline the preview jumps up to 25 mm onto the part's own edge and shaves 3 mm off it; on a hatch edge it widens the hatch | `seamsnap.py:936`, `574-576`, `956-959`; `sheets.py:547` | outer and cut-out edges are offered as slide targets; the kerf is centred on the seam |
| A5 (F12, G48), Medium | the line clicked is not quite the line cut (up to 0.87 mm) and the note says it moved although the preview had settled | `bridge._settled`, `sheetjob.apply_snap` | settle lands the midpoint on an edge 0.07 to 0.145 degrees off parallel, re-trims, and `apply_snap` re-lands it; not the length window at `seamsnap.py:930-932` (refuted) |
| A6 (F15, G58, F45, F34), Medium | an across join jumps up to 24 mm sideways onto a tiny seam on another panel; the first seam beside an old one jumps 16 mm at the click; deleting one seam moves another | `seamsnap.py:935-957`; `sheetjob.py:268, 308-310`; `bridge.py:535, 567` | the reference pool is one list for all panels in the nest frame; `seam_references` reads raw while `apply_snap` uses corrected positions |
| A7 (G16), Medium | an across seam clicked port and again starboard of the console gives two lines about 11 mm apart and 5.6 mm strips | `seamsnap.py:947`, `312-340` | references farther than 300 mm by closest approach are rejected, so a continuation across the 1125 mm console is out of reach |

Low items (table in the report): F36, F46/G51, F39, F14, F42, F27, F28, F26.

### Theme B: the ring rebuild in `split_panel`

| Id | What the fabricator sees | Where | Cause |
|---|---|---|---|
| **B1** (F01, G01, G50, F47, G29), High | every panel-1 piece cut after any seam comes off the router solid: the 207 mm round cut-out is missing from the sheet DXF, no warning, status OK; a round part cut by one seam is dropped as "degenerated" | `sheets.py:576`, `579-581`, `683`, `568`, `583`, `697-708` | a rebuilt hole is kept only with >= 3 vertices and a circle rebuilds to 2; `_encloses_area` needs both bulges non-zero so D-shaped loops are dropped too |
| **B2** (G04), High | console walls on cut pieces arrive in VCarve as 900 to 2500 one-millimetre nodes (6 to 32 % of the perimeter faceted) | `sheets.py:624-626`, `256-259` | one "dominant" source loop per ring; every edge from another loop is emitted as 1 mm straight vertices |
| B3 (F08, G09), Medium | fillets, slot ends and the sheer curve on split pieces, including untouched cut-outs, come back as hundreds of 1 mm chords; corners clipped by up to 1 mm; the reported arc error hides it | `sheets.py:610-615`, `191-198`, `268`, `282-287` | the junction chord is a nearest-sample tie, so the circle fit starts one sample into the neighbour and fails the 0.05 mm test |
| B4 (G17), Medium | "P1-3 needs a seam" points at a different piece after the next edit; download links and report names drift | `sheets.py:568`, `583`; `seamplan.py:2017`, `2034` | ids come from the area sort index |

### Theme C: the optimiser

| Id | What the fabricator sees | Where | Cause |
|---|---|---|---|
| **C1** (F10, G07, G32), Medium | seams the report calls "on fitted edges" sit 4 to 9 mm off the console wall | `seamplan.py:706-728`, `410-425`, `1251-1287`; `seamsnap.py:930-941` | `_alignment` credits a cut within 25 mm of an edge assuming the corrector will slide it there; the locked slide needs the edge parallel within 0.04 to 0.08 degrees for seams this long and moved 0 of 222 generated seams |
| C2 (F11), Medium | seam edges with 2.5, 8.5 and 22 mm notches where a small cut-out was sliced; at weight 0 a 17 mm crescent from the round cut-out | `_edge_offsets`, `_alignment` | hole edges >= 15 mm are offered as cut positions; nothing treats a cut-out as an obstacle |
| C3 (F17, G18), Medium | four extra fiddly parts for a 0.05-point waste change; the headline never mentions piece count | `seamplan.py:579-580`, `2184` | the key ranks oversize, sheets, waste, seams, cut length; `piece_count` is never ranked |
| C4 (G06), High by design, no symptom on this boat | a far-nested panel that needs a seam is never cut; a seam floats beside it | `seamplan.py:2079-2082`, `1984-1991`, `2381-2394` | trial cuts are centred on the foot of the perpendicular from the placed-frame origin |
| C5 (G19), Medium | a short budget evaluates one arrangement, reports "improved" and replaces the seams with a 5-sheet even split | `seamplan.py:932-935`, `1694`, `2284`; `bridge.py:713-737` | one shared deadline for candidates and search; any improvement is written |
| C6 (G20, F15), Medium | optimiser seams are re-snapped at confirm and export (6.91 mm measured) | `seamplan.py:1461`, `2366` | `_seams_for` emits `snap=True` |

Low items: G30, G31, G34, F24, F25, F44, G33.

### Theme D: autonest and export

Clean, measured: 80 seam sets, 1371 pieces, 0 outside the usable area, 0 overlaps, oversize
report equals the nester's unplaced list 80/80; min clearance 20.000 mm in the DXFs; DXF versus
placed outline within 0.006 mm; bit-identity with the `958d8f0` baseline nester on 20/20 fixture comparisons, 24 of
80 probe sets (the rest unfinished when reported), 40 synthetic nests and 106 `_find_spot` calls.

| Id | What the fabricator sees | Where | Cause |
|---|---|---|---|
| **D1** (G03, F18), High | after a smaller re-export the file list still offers older sheet DXFs with working links ("sheet 4 (0 pieces)") | `nesting.py:640-641`; `sheetjob.py:608`, `612-628`; `app.js:2002-2008` | higher-numbered files are never removed; `exported_sheets` globs `sheet_*.dxf` |
| D2 (G10), Medium | a 5th sheet bought for one gunwale strip at 2.3 %; lowering the piece gap from 20 to 15 mm adds a sheet; another order saves a sheet in 12/40 sets | `nesting.py:283`, `290-302` | first fit, one order, never revisited; a quality limit, not a bug |
| D3 (G11), Medium | pieces 20.02 to 25 mm apart when 20 is asked; two pieces that exactly fill 990.6 mm go on two sheets | `nesting.py:400-401`, `358`, `75-78`, `422` | touching counts as a collision; grid anchored at the sheet edge; `sin(pi)` noise in the 180 matrix |
| D4 (F38, G53), Low to medium | a refused piece is listed as placed in the report and counted in utilisation; plan warnings and `refused` never reach the page | `sheetjob.py:524-528`; `nesting.py:646-654`; `app.js:815-826`, `1646-1663`, `2115-2147` | placements serialised after the export refused; the page reads only `oversize` |

Low items: G52 (piece gap 0 accepted), G40, G41, G35, G36, G37/F41, G38, G49.

### Theme E: UI and server state, files

| Id | What the fabricator sees | Where | Cause |
|---|---|---|---|
| **E1** (F03, G05, F32, G27), High | after remove-then-place a seam jumps onto another's line; the list says "seam s4" twice; the cut files follow | `bridge.py:292`; `server.py:150-186`; `app.js:1432-1438` | id-less seams are named by list position; duplicates, bad panel ids, zero length and NaN angles are not rejected |
| **E2** (F04, G13, F40), High | remove a seam while the optimiser runs: undo puts back 3 of 4, the original set is gone, or a hand layout is labelled "from the automatic layout" | `server.py:519-537`; `bridge.py:677`, `731-737`; `app.js:1600-1626`, `2138-2141` | the seams POST has no busy guard; the backup is copied at the end of the job; editing stays live |
| E3 (F16), Medium | a run reopened quickly shows "No seams saved" and the first click overwrites the previous seams (4 -> 1) | `app.js:1551-1559`, `1451`, `1503` | placing before the GET returns defeats the `seamsLoaded` guard |
| E4 (F20), Medium | with "not saved to the run yet" showing, the optimiser ends with the hand seams listed under the optimiser's numbers; Export cuts from a file the page no longer matches | `app.js:2138-2145` | `seamsOrigin = "auto"` set before the refresh; optimise and export not blocked on `seamsPending` |
| E5 (F49), Medium | the previous boat's sheets drawn over the new one; worst case the new run's seams erased | `app.js:1376-1378`, `1442-1445`, `1563-1575`, `1872`; `server.py:529` | no run id in the stamp or the POST; the server writes into `current_run_dir()` |
| E6 (F06, G12), Medium | typing 0 in the Seam gap box kills every hover and edit, including remove and Clear all (400 "sheets.seam_gap_mm must be greater than zero") | `index.html:145`; `app.js:993-1007`; `server.py:47`; `sheets.py:115-119` | the box and the server allow 0, `settings()` refuses it |
| E7 (F07, G15), Medium | the log says "download seams_previous.json from the file list" but the list never shows it; older numbered backups 404; Undo after a second press restores the first optimiser result | `app.js:1995`, `1841`; `bridge.py:63-66`, `641-646`; `server.py:22-26` | `renderFiles` iterates a hard-coded list; `seams_previous_N.json` is not served |
| E8 (G23), Low to medium | after a crash during an edit the Sheets tab errors on open and the optimiser fails with a JSONDecodeError | `sheets.py:462`, `486-488` | unguarded `json.loads`; truncate-then-write |

Low items: F29, F30, F31, F23, G42, G24, F48, G54, G59, F33, F50, G61.

### Not bugs (do not re-report)

- The 26 to 41 mm across seams the optimiser places on panels 2 and 3: those panels are 25 to
  40 mm wide gunwale strips at that station; each seam is a full-width cut that splits its strip
  into two placeable pieces.
- Optimiser determinism at a fixed budget (a second 60 s run is byte-identical); every reported
  number comes from `sheetjob.plan`; the wording is "best found" everywhere.
- Nesting invariants and the bit-identity of the speed-up; export matches preview to 0.006 mm;
  hover chords land on the fitted outline to 2.4e-13 mm.
- The four legacy hand seams move 1.0 to 6.2 mm on first re-plan: axis capture, intended.
- Refuted mechanisms the report lists in section 4 (for example F12's length window, F08's
  boolean trigger, G36's magnitude, F04 as a race).

### Disputed items

Nine items reproduced as observed but their claimed product consequence did not hold: F22, F37,
F43, G14, G39, G44, G55, G56, G57. Section 3 of the report gives each claim, counter and how to
settle it. Two matter for the plan: G44 (tighten `test_seamplan.py:399`, which today only asserts
that an optimiser seam intersects its panel, before the A1 change) and G57 (which steps can move
the frozen nesting table).

---

## 6. The plan

The report's section 6, restated with everything you need to execute it. "Suite" means:

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m pytest engine/tests -q -m "not slow"
```

"Frozen table" means `EXPECTED` / `EXPECTED_SUMMARY` in `engine/tests/test_nesting_speed.py`
(lines 101-140), pinned on `PINNED_SEAMS` (old schema, no mode). Its own check:

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m pytest engine/tests/test_nesting_speed.py -q
```

Expected today: `12 passed`.

Probe scripts: run from the repo root with the prefix
`PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe`.
`tools/audit/h1/h1_extension.py` takes the scratch folder as its first argument; most of the
others hard-code `S = Path("C:/Users/marcu/AppData/Local/Temp/claude/.../scratchpad/...")` and
need that line edited to a folder outside the repo (54 scripts; `grep -rl scratchpad tools/audit`
lists them). Never point them at `engine/outputs/runs/`.

OWNER marks a step that needs a product decision first (6.2).

**Step 0. Back up and commit.** The code is committed and pushed (0.4). Still open: copy the audit's
seam backups (2.5) and the run folders somewhere durable; the owner decides where (Q15). Check:
`git status --short` shows only your work; suite 297 passed.

**Step 1. B1, hole loss.** Files: `sheets.py:576` and `579` (accept a rebuilt 2-vertex ring whose
bulges are both non-zero), `683` (`_encloses_area`: any non-zero bulge), `568-583` (number pieces
from the kept list, warn on skipped loops), `697-708` (`read_fitted_dxf`: honour the closed flag,
restrict to the CAM layer family, warn on skipped loops). Tests to add: a round and a filleted hole
inside a split band with hole and bulge counts asserted, a round trip through
`write_sheet_dxfs`, a round part split into two half-discs (report section 5 item 1; today every
`split_panel` test passes `holes=[]`). Proof: suite green; frozen table unchanged (predicted by
G57: the restored hole lands on oversize, unplaced pieces); on the staged AXIS copy the plan
snippet from 0.5 shows the panel-1 pieces reporting every cut-out no kerf touches, the 206.7 mm
round loop included (today the panel-1 pieces report 2 holes between them, `check_round_hole.py`);
`tools/audit/wf2/tests/holes_export.py` writes two CAM polylines for the hole-bearing piece. No
dependencies.

**Step 2. A1, chord versus cut, with C4.** OWNER: rule (a) chord-only or (b) whole line (Q1).
Files: `sheets.py:533-547`, `bridge.py:241-275` (`seam_world_polylines`), `app.js:705-721` and
`1196`, `seamplan.py:1984-1991`, `2079-2082`, `2381-2394` (anchor trial cuts at the bbox centre;
`_span` otherwise unchanged, so optimiser seams still span edge to edge). Under (a): moded seams
cut only their stored chord plus about one seam gap past the outline, mode `""` legacy seams keep
the extension, one shared chord function for hover, flat, 3D and list, and the `MIN_CHORD_MM`
relevance test applied to bound seams too. Tests to add: a non-spanning `seam_through` chord fed
to `split_panel` with piece count and areas asserted (item 2); optimiser endpoints on the outline
and `_seams_for` on a panel translated 3 m (item 10); tighten `test_seamplan.py:399` first (G44).
Proof under (a):

```bash
mkdir -p /d/AutoDeck-scratch/h1
PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe tools/audit/h1/h1_extension.py /d/AutoDeck-scratch/h1
```

Today: case A 2 pieces (3,687,778 + 1,131,148 mm2), case B 2 pieces, case C 2 pieces. After:
A and B give 1 piece, C still 2. Under (b): every collinear chord is drawn in the flat view and
the hint shows the combined length. Either way `test_seamplan` and the frozen table stay green
(pinned seams have no mode). Depends on step 1.

**Step 3. A3 to A7, hover and snap.** OWNER for the cut-out edge rule (Q3). Files:
`bridge.py:542-589` (`_settled`: keep the chord whose range brackets the pointer's foot; settle to
a fixed point and share one function with `apply_snap`), `seamsnap.py:892-965`
(`_refine_position`: same-panel references only or compare in boat-plan coordinates; exclude the
cut panel's own outer edges for locked seams; cut-out edges offset by half the gap or skipped),
`sheetjob.py:253-312` (`apply_snap`; build `seam_references` from the corrected list; re-aim
regardless of the slide flag, F42); under A1 rule (a) let a chord continue a parallel seam across
a cut-out regardless of reach when only a hole lies between (A7). Tests: `test_seamsnap` cases;
`test_sheet_seams.py:646` (today tolerates `seam_snap_offset_mm` = 25 mm of movement) tightened
to `moved_mm <= 1e-6` after the bridge's settle. Proof: `tools/audit/wf/probe-hover-snap/probe_hover.py --quick`
reports 0 vanish cases and 0 outer-edge landings (full grid today: 285 vanish points, 518 of
49,886 hovers slid, 360 onto outer edges); `probe_second_pass.py --quick` reports 0 second moves
(today 113 of 4,165, max 0.872 mm); frozen table unchanged. Depends on step 2 for A7.

**Step 4. A2 sliver guard and D4 warnings.** OWNER threshold (Q2). Files: `sheets.py:553`
(minimum usable width beside the area test, warning naming the seams; a seam whose kerf separates
nothing becomes a no-op with a warning), `sheetjob.py:653` (report), `app.js:815-826` and
`1630-1663` (render `warnings`, `refused` and `status`; mark refused placements "(not written)"
and exclude them from utilisation). Tests: item 5 (sliver width, edge-coincident and zero-length
seams). Proof: the plan on the staged AXIS copy drops P1-8 and P1-9 with a warning naming s1 and
s2. **Frozen table**: P1-9 is a placed row in `EXPECTED` (`test_nesting_speed.py:108` and `113`);
if the guard drops the pinned seams' P1-9 the table will change and must be re-captured from the
committed nester on the new pieces, with the diff recorded in the commit message (7.6). Depends on
steps 1 and 2.

**Step 5. E1 to E8, server and page.** OWNER on E2 (Q8) and E6 (Q9). Files: `bridge.py:292`
(unique ids: `crypto.randomUUID()` on the page or server max suffix + 1), `306-313` (save before
planning, so removal always works, G14), `629-649` and `731-742` (backup at job start; refuse the
job's write if `seams.json` changed since), `60-71`, `298-304`, `786-789`; `server.py:150-186`
(reject duplicates, bad panel ids, non-boolean snap, non-finite angles, zero length with a 400),
`519-537` (busy guard or queue during optimise; run id in the request), `47` (exclusive seam gap
bound), `22-27` (serve `seams_previous(_\d+)?.json`), `649-654`; `jobs.py:41-78` (`start` refuses
under its own lock); `sheets.py:458-488` (guarded read with `.bak`, utf-8-sig, write to a temp file
and replace); `app.js` per the E items (refuse POSTs until `seamsLoaded`, block optimise and export
while `seamsPending`, run id in the stamp, stop polling on 404, `renderFiles` from `run_files`,
plain error wording). Tests: Flask test-client tests for items 6 to 8 (POST `seams=[]` under
oversize, a raising plan and seam gap 0 returns 200 and writes `{"seams": []}`; seam ids unique
after remove-then-place and 400 on duplicates; POST during optimise returns 409 and the backup
equals the pre-press file). Proof: those tests; `GET /api/file/<run>/seams_previous_1.json`
returns 200; `tools/ui_smoke.py` passes (7.8). Independent of steps 1 to 4.

**Step 6. B2/B3, the ring rebuild.** Files: `sheets.py:589-633` and `224-290` (namespace segment
ids by (loop, segment) and rebuild per source loop; attribute an edge to a segment only when both
endpoints are consecutive samples of it, or exclude the chord adjacent to each original vertex;
count fallbacks in a report field). Tests: item 3 (`rounded_rect(1600, 900, 3.0)` with a seam at
x = 800 keeps four fillets; corner clip under 0.05 mm; the reported max error counts rejected
arcs). Proof: `tools/audit/wf2/probe-plan-real-seams/facet_count.py` on scratch DXFs reports facets
under 1 % of the perimeter (today 6 to 32 %); arc count after a split equals the original where no
kerf touches; frozen table re-captured with proof only if a row moves by float noise. Depends on
step 1.

**Step 7. The optimiser.** OWNER on C2 clearance (Q4), C3 price and threshold (Q2), C5 N (Q5).
Files: `seamplan.py:2366` (`snap=False`), `706-728` and `1251-1287` (credit alignment only within
about 1 mm of the edge offset or by the corrector's own test; never credit an edge shorter than
the chord; `EDGE_TOLERANCE_DEG` to 0.5 or weight by angle), `_positions` / `_cut_sets`
(cut-outs as obstacles; G30; G31), `579-580` (a priced piece-count term and piece count in the
headline), `932-935` and `bridge.py:713-737` (a separate slice for candidates; N evaluations
before "improved" is honoured), plus the theme C low items. Tests: item 10; re-verify
`test_seamplan` expectations. Proof: `tools/audit/wf/probe-optimiser/probe_07_features.py` shows
no hole remnant under the chosen clearance; `probe_08_alignment.py` shows credited seams within
1 mm of their edge (today 9.3 mm); the default layout still plans OK on 4 sheets or the change is
documented; a 5 s budget is not written. Frozen table unaffected. Depends on steps 2 and 3.

**Step 8. Export and nest reporting, no placement changes.** OWNER on the spacing floor (Q7).
Files: `sheetjob.py:482-485` and `608-628` (delete or rename every `sheet_*.dxf` not written this
export; list only files recorded in `sheets.json`; `stale: true` when `sheets.json`'s seams differ
from the live seams), `app.js:2002-2008` (label or hide stale links), `nesting.py:271` and
`sheets.py:744` (one shared tolerance; one decimal plus overage), `sheets.py:98-126` (validate
positivity of every numeric key, G24), `server.py:46` and `index.html:144` (spacing floor), the
theme D low items, `AutoDeck.lnk` icon. `nesting.py:271` and `196` are inside `nesting.nest`, so
the frozen rule applies: run `tools/audit/wf2/nesting/diff_fixture.py` (edit `S`) and require 0
differences before commit. Tests: item 9 plus a G24 table. Proof: an export with fewer sheets
leaves no stale file listed; an exact 990.6 x 2006.6 rectangle on the 3.68 degree axis nests
(G40); P2's two oversize lines print the same size (G41); `diff_fixture.py` 0 differences; frozen
table unchanged. Independent.

**Step 9. Test hygiene.** Items 11 to 14: assert each preview ring's id, sheet and bounds against
its placement; nesting invariants on real pieces (overlap, spacing >= 20 - 0.03, envelope,
DXF versus preview); an opt-in slow bit-identity oracle that loads the nester at `958d8f0` via
`git show` (`diff_fixture.py` already does this) and fails when the skip count is non-zero; pinned
copies instead of the live `seams.json` of the fixture runs (`test_seamsnap.py:1080`,
`test_sheet_seams.py:526-583`); a `final.dxf` precedence test; `pytest --cache-clear` for the
stale lastfailed entry. Proof: 0 skipped on the developer machine; the oracle passes against
`958d8f0`.

**Step 10. Nester changes.** OWNER decision (Q6, Q7); frozen-output rule. D2 multi-order search as
a labelled opt-in with the current order as the default (`nesting.py:283-302`); D3 exact 180
matrix, a deliberate touching rule and candidate x from the occupied extents (`75-78`, `358`,
`400-401`, `422`); G36 `quad_segs`. Tests: a new test for the opt-in path; `EXPECTED`
re-captured with the diff script as proof. Proof: the default path bit-identical on the frozen
pieces; the opt-in path never needs more sheets than the default on the 80 probe sets. Do last.

### 6.2 Owner decisions still open

Fifteen questions, from the report's section 7. The lead's recommendations are recommendations,
not decisions; none of these has been answered by the owner. Present them as choices.

1. **Chord-only or whole-line cut (A1)?** Recommendation: chord-only for seams placed with the
   direction tool or by the optimiser (any seam with a mode), with legacy free seams (mode `""`)
   keeping the extension so old runs behave as before.
2. **Narrowest strip and smallest piece worth cutting (A2 width floor, C3 piece term)?** Today
   400 mm2 and 5.6 mm wide pass. Recommendation: a minimum piece width around 40 to 50 mm as a
   default the owner can change in Settings.
3. **May a seam sit exactly on a hatch or console edge (A4)?** Recommendation: only with the kerf
   offset by half the gap into the deck, never centred on the fitted edge.
4. **Should the optimiser avoid the small cut-outs and the round one, and at what clearance
   (C2)?** Recommendation: a 30 to 40 mm clearance from cut-outs.
5. **Minimum number of arrangements before the optimiser may overwrite the seams (C5)?**
   Recommendation: at least 50 evaluated arrangements.
6. **Autonest: a "try other orders" option (D2)?** Recommendation: yes, as a labelled opt-in, with
   the frozen order as the default.
7. **Router bit diameter, to set the piece-gap floor (G52); is "at least 20 mm" acceptable or must
   it be exactly 20 (D3, which needs a table re-capture)?** No recommendation; needs the shop's
   number.
8. **During "Find the best seam layout", block seam edits or queue them (E2)?** Recommendation:
   block them while the job runs.
9. **Seam gap floor (E6)?** Recommendation: 2 mm.
10. **Keep the first hand-placed seam set under a stable name the optimiser never rotates (E7)?**
    Recommendation: yes.
11. **Grain angle entry: absolute angle in the unturned layout, or +/- degrees from the detected
    axis (G54)?** No recommendation.
12. **Run `21kwcockpit-3`: did the owner delete `auto-1-across-1` by hand, or did an earlier app
    version write it (G56)?** No recommendation; a fact only the owner knows.
13. **Do the 1 mm faceted console walls cause trouble in VCarve or on the CNC?** Sets the priority
    of B2/B3. No recommendation; needs a cut.
14. **Shortest join the owner would ever place (today chords down to 5 mm are offered, F35)?** No
    recommendation.
15. **Commit the tree now, and where should the off-machine copy live?** The commit and push are
    done. The off-machine copy of the data (runs and scans) is still open.

---

## 7. How to work here safely

### 7.1 Back up `seams.json` before anything that might write it, and verify the restore

```bash
RUN=engine/outputs/runs/21kwcockpit-1-20260901-180939
mkdir -p /d/AutoDeck-scratch/backup/$(basename $RUN)
cp $RUN/seams*.json $RUN/sheets.json $RUN/sheet_report.md /d/AutoDeck-scratch/backup/$(basename $RUN)/ 2>/dev/null
md5sum $RUN/seams.json /d/AutoDeck-scratch/backup/$(basename $RUN)/seams.json
```

After any experiment, compare again. The owner once lost hand-placed seams this way and they
were only recovered from a conversation transcript.

### 7.2 Copy runs before `plan()`; never let a probe touch the real run folders

`sheetjob.plan(write_files=True)`, `bridge.job_sheets`, `bridge.job_optimise_seams`,
`sheets.write_seams`, `POST /api/sheets/seams` and the export and optimise jobs all write into
the run directory. Stage a copy (0.5 step 4) and point everything at it. For the app, use
`AUTODECK_RUNS_DIR`. `sheetjob.plan(..., write_files=False)` and `sheetjob.preview` write nothing,
and `seamplan.optimise` itself writes nothing (the bridge does the writing).

### 7.3 Git

- Never `git checkout -- <file>`, `git reset`, `git clean` or `git stash` without asking the
  owner; the working tree may hold their work.
- Commit in coherent pieces with the measurements in the message. Ask before pushing.
- `git config --global --add safe.directory D:/AutoDeck` on a new checkout location.
- Remote `origin` is GitHub; `gh` is logged in as Marcus12903400 on this machine. Plain `git push`
  only; never force-push.
- The 2026-10-07 history rewrite left backups: the pre-rewrite history in
  `C:\Users\marcu\AutoDeck-backups\AutoDeck-git-backup-2026-10-07.bundle` (also on `D:\`,
  `git bundle verify` OK), plus local refs `refs/original/refs/heads/main` and the branch
  `main-filtered-backup` inside `D:\AutoDeck`. Delete those only with the owner's agreement.

### 7.4 Line endings, dependencies, platform

- LF endings in every file you touch. Do not use `Path.write_text()` without `newline="\n"`.
  Note that `sheets.write_seams` itself writes `seams.json` with CRLF on Windows (a data file;
  parsers do not care).
- No new dependencies. No matplotlib. Pillow, ezdxf, shapely and numpy are available for scripts.
- `PYTHONIOENCODING=utf-8` on every Python command, or cp1252 raises on non-ASCII output.
- Windows uses `spawn`, not `fork`, for `ProcessPoolExecutor`; payloads must be picklable per
  worker (`engine/autodeck2/parallel.py`).
- Flask runs `threaded=True`; the `JobManager` serialises engine jobs but not the seams POST, the
  sheets GET or the hover.

### 7.5 The v1 contract

If `engine/tests/test_contract.py` fails after you touched `engine-v1/`, that is the design
working. Update `V1_REQUIRED_VERSION`, `_EXPECTED_SIGNATURES` and the wrapped functions in
`engine/autodeck2/v1compat.py` deliberately, and expect every engine cache entry to be a miss
afterwards (the fingerprint is part of the key).

### 7.6 The frozen nesting re-capture procedure

Only when the **pieces** change (steps 2, 4, 6 may; step 10 changes the nester and needs the
owner first):

1. Confirm the failure is in the pieces, not the nester: run
   `tools/audit/wf2/nesting/diff_fixture.py` (edit `S` to a scratch folder; it imports the
   baseline nester from `nesting_old.py` beside it (a saved copy, so it does not depend on git), captures the real `Piece` objects by spying on
   `nesting.nest` inside `sheetjob.plan`, and compares layout tuples, summary, warnings and used
   area with `==`). It must print `identical: layout=True summary=True warnings=True
   used_area=True` for both fixtures.
2. Re-capture `EXPECTED` and `EXPECTED_SUMMARY` from the committed nester
   (`git show 958d8f0:engine/autodeck2/nesting.py`) on the new pieces, never from the current
   nester.
3. Put the old-versus-new diff output in the commit message. The table has been restated exactly
   once before (circles restored in `read_fitted_dxf`); the module docstring explains why it is
   compared with `==`.

### 7.7 Fixtures are mutable: pin data

The two fixture runs are the owner's live runs; placing a seam in the app rewrites their
`seams.json`. `test_nesting_speed.py` and `test_seamplan.py` pin their seams as data for this
reason; `test_seamsnap.py:1080` and `test_sheet_seams.py:526-583` still read the live file (a known
gap, plan step 9). Never select fixtures by "newest run on disk". If you add a fixture, pin it by
name and by content.

### 7.8 Running `tools/ui_smoke.py`

It needs a running server, Playwright with Chromium (both present on the owner's machine), Pillow,
and a run id. It places, removes and clears seams on that run, so run the server against a
scratch copy of the runs folder; by default it copies `seams.json` aside and restores it, and
`--keep-changes` disables that. `--optimise` runs the optimiser (opt in). `--out` defaults to
`ui-shots/` in the current directory, which is not gitignored, so pass a scratch path.

```bash
AUTODECK_RUNS_DIR="D:/AutoDeck-scratch/runs" AUTODECK2_ROOT="D:/AutoDeck/engine" AUTODECK_V1_ROOT="D:/AutoDeck/engine-v1" PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m autodeck_app --port 8790 --no-browser
```

then, in a second shell:

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe tools/ui_smoke.py --url http://127.0.0.1:8790 --run-id 21kwcockpit-1-20260901-180939 --out D:/AutoDeck-scratch/ui-shots
```

It was not run during the audit; whether it passes today is unknown (section 9).

### 7.9 Rendering and reading sheet DXFs

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe tools/render_sheets.py --run D:/AutoDeck-scratch/runs/21kwcockpit-1-20260901-180939 --out D:/AutoDeck-scratch/sheets.png
```

(needs `sheet_*.dxf` in that folder, so export first, on the copy). To read a sheet DXF the way
VCarve does:

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe - <<'EOF'
import ezdxf
doc = ezdxf.readfile("D:/AutoDeck-scratch/runs/21kwcockpit-1-20260901-180939/sheet_01.dxf")
print("units", doc.header.get("$INSUNITS"))
for e in doc.modelspace().query("LWPOLYLINE"):
    pts = list(e.get_points("xyb"))          # (x, y, bulge); arc sweep = 4 * atan(bulge)
    print(e.dxf.layer, "closed" if e.closed else "OPEN", len(pts), "vertices",
          sum(1 for p in pts if abs(p[2]) > 1e-12), "arcs")
EOF
```

A piece with a cut-out must appear as two `CAM__<piece>` polylines on the same layer. A 2-vertex
polyline with bulges +1/-1 is a full circle.

### 7.10 Measure instead of guess (the house habit)

This project's history is a list of confident assumptions overturned by measurement: the
"obvious" cause of the overlay roughness (point location) changed nothing; the nesting bottleneck
was first measured on a degenerate case; a fixture that looked unrecoverable was recovered; in the
audit, F12's mechanism and F08's trigger were both wrong until a verifier measured them. Before
you state a cause, reproduce it on a staged copy with the real pipeline (`sheetjob.plan`), print
the numbers, and say what you did not measure. When you report, separate "measured" from
"reasoned from the code".

### 7.11 The project lives on a USB stick

`D:` is a 62 GB SanDisk USB 3.2 Gen1 flash drive formatted FAT32 (`Get-Disk`, `Get-Volume`).
Consequences:

- It can disappear. On 2026-10-07 it was gone for several minutes (`Test-Path D:\` false, no `/d`
  mount) and returned with everything intact. Check for it at the start of every session and
  after any error that mentions a missing working directory. A running app loses its runs and
  caches the moment the stick drops.
- The only copies of `engine/outputs/runs/` (913 MB, every seam layout) and the scans (`app/inputs/`,
  about 4 GB; `inputs/`, regenerable) are on it. Ask the owner to let you copy `engine/outputs/runs/`
  to the internal drive (for example `C:\Users\marcu\AutoDeck-backups\runs_<date>\`) before any
  work; the code itself is safe on GitHub.
- FAT32: no ownership (hence `safe.directory`), no symlinks, 4 GB file limit, and 2-second
  timestamp resolution, which matters for anything keyed on mtimes (`bridge.hover_context` keys
  on `run.json` mtime, `edge_references` on the DXF mtime, `seam_references` on `seams.json`
  mtime): two writes within two seconds can look identical to those caches.
- The launcher and shortcuts find everything relative to the folder, so the project can be moved
  to the internal drive by copying the whole `AutoDeck` folder and re-running `AutoDeck.bat`
  (which rebuilds `.venv`); `run.json -> input_path` entries would still point at
  `C:\Users\marcu\AutoDeck\app\inputs\`. Whether to move it is the owner's call (section 9).

---

## 8. Glossary

- **kerf**: the 6 mm strip removed along a seam line (`seam_gap_mm`), produced by buffering the
  line by 3 mm each side with flat caps and subtracting it from the panel.
- **chord**: one straight run of a seam line inside a panel between two crossings of its boundary
  (a line crossing the console yields a chord either side). `seam_through` returns chords; the
  hover previews one; `split_panel` cuts the whole line (A1).
- **seam gap vs piece gap**: seam gap (6 mm) is between two pieces that meet at a seam; piece gap
  (`part_spacing_mm`, 20 mm) is the minimum clear distance between nested pieces on a sheet.
- **usable envelope**: 990.6 x 2006.6 mm (39 x 79 in), the largest part cut from a sheet, 12.7 mm
  margin all round. "Oversize" is measured against it in sheet axes.
- **placed frame**: the nest layout of `final_auto.dxf`; the frame of every stored coordinate.
- **nest offset**: the per-panel translation that moved a colliding panel to the row below the
  primary in `nest` layout mode; subtract it to get boat-plan coordinates.
- **boat-plan**: the layout mode and frame where panels sit at their true relative positions.
- **master directions**: `along` (unit boat axis) and `across` (its clockwise quarter turn).
- **capture angle**: `seam_axis_snap_deg` (20): how far off a master a free seam may be drawn
  and still be squared onto it.
- **refine / slide**: `_refine_position`, the sideways translation of an already-squared seam
  onto a parallel fitted edge or seam within 25 mm.
- **feature snapping**: the older rule for seams no master claimed: collinear, parallel,
  perpendicular or tangent to a fitted edge within 5 degrees.
- **bulge**: DXF arc encoding, `tan(sweep/4)`, stored on the vertex that starts the arc.
- **LWPOLYLINE**: the DXF entity every CAM loop is written as, `(x, y, bulge)` vertices, closed.
- **TEST_ONLY**: the manufacturing approval value every output carries until a human verifies a
  physical cut.
- **oversize**: a piece that fails the envelope test in `oversize_report`. **unplaced**: a piece
  the nester could not place (today always equal to oversize). **refused**: a piece the DXF writer
  measured again at the door and left out of the file (`_refuse`, 0.1 mm tolerance).
- **proxy**: the optimiser's coarse geometry (4 mm sampling, 25 mm nest grid, no arc rebuild);
  never reported. **confirm**: running a candidate through `sheetjob.plan`.
- **tidiness**: the optimiser's shape terms, weighted by `seam_tidiness_weight` (0.35):
  **symmetry** (port and starboard seams mirror about the centreline), **rectangularity** (piece
  area over bbox area), **alignment** (a cut credited for sitting near a fitted edge), **joins**
  (seam count against the minimum). It can never add a sheet or an oversize piece.
- **frozen table**: `EXPECTED` in `test_nesting_speed.py`, the bit-exact nesting result on the
  pinned fixture seams.
- **AXIS run / NO-AXIS run**: the two fixtures, `...-180939` (teak, frame) and `...-172519`
  (pattern none, no frame).
- **stamp**: `{rev, told, clean}` the page attaches to each sheets request so a stale reply cannot
  overwrite a newer edit.
- **sliver**: a thin part a seam shaves off; dropped under 400 mm2, kept otherwise regardless of
  width (A2).
- **grain angle**: `grain_angle_deg`, the manual override of the boat axis, degrees in the placed
  frame, blank = detected axis.
- **bow sign**: +1 or -1 along the axis, with a confidence; the narrow end is the bow.

---

## 9. Open questions and unknowns

Nobody has verified these. Do not state them as facts either way.

- **VCarve behaviour** on 1000 to 4500-vertex polylines (B2/B3) and on two-vertex bulge-1
  circles (the way circles are written). Only the DXF contents were checked, with ezdxf and
  `tools/render_sheets.py`. Nothing from this version has been cut on the CNC.
- **Texture-assisted edge detection on a real textured scan.** Built and unit tested on the
  synthetic boat; the real Key West scan has no `.mtl`, so it has only ever run geometry-only on
  real data.
- **macOS launchers** (`AutoDeck.command`, `AutoDeck.app`): no evidence they were ever run.
- **Cross-machine determinism**: the nester's exactly-touching `intersects` test and the cKDTree
  tie-break in `_rebuild_ring` (B3) could resolve differently on another SciPy/GEOS build; the set
  of flattened arcs may differ per machine.
- **`tools/ui_smoke.py` today**: not run during the audit; all page behaviour in the report was
  traced or replayed in Node, not observed in a browser.
- **Run `21kwcockpit-3`**: why its optimiser seam set lacks `auto-1-across-1` (G56) and how its
  seam `s8` came to be stored 23.73 mm from its drawing (the app version that wrote it is unknown).
- **Whether the 1 mm faceted console walls matter on the router** (Q13) and the shop's router bit
  diameter (Q7).
- **The engine-v1 suite** has not been run since 2026-09-02 (116 passed then).
- **The other project copies** (2.6): their contents were listed, not compared; whether anything in
  them is newer than the repo is unknown. The C: copy is what every run's `input_path` points to.
- **The audit's seam backups** were in the Claude session's Temp folder on 2026-10-07 (2.5); whether
  they still exist when you read this is unknown.
- **Deferred performance work** (never started): OBJ parser vectorisation (about 44 s available)
  and the top-view raster loops (about 53 s available). The owner has not asked for it.
- **The USB stick's health** and whether the project should move to the internal drive (7.11).
- **Distribution**: whether this ever ships to anyone other than the owner's shop (packaging,
  licensing, updates) has never been decided.
