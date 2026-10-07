# START_HERE_AI.md — AutoDeck

**Read this entire file before you touch anything.** It was written for an AI
assistant picking this project up on a different machine and a different Claude
account, with no memory of how it got here.

Written: 2026-09-02. Project version at that moment: **5.3** (`__version__ = "5.3.0"`).

Everything below is marked either as a **confirmed fact** (verified by reading
the code or running it on 2026-09-02) or as an **assumption / Needs owner
confirmation**. Where something is genuinely unknown it says so rather than
guessing.

---

## 0. STOP — read this first

### 0.1 A build was still running when this project was archived

**Confirmed.** At the moment this file was written, an automated multi-agent
build was mid-flight. The last phase to finish was a verification pass; a
"Ship" phase (set the version everywhere, cold-start the launcher, final
sweep) and a "Docs" phase were still executing or had just been interrupted by
the archive.

**What this means for you:**

- The working tree may contain a partially completed edit.
- `README.md`, `app/README.md`, `engine/README.md` and
  `docs/v5-build-notes.html` were being rewritten and may describe things
  slightly ahead of, or behind, the code.
- The test suites were run to completion partway through the final phase
  (297 + 116 passed — see §5), but not after the very last edits.

**Your first action must be to establish ground truth**, not to trust this
file's claims about test counts:

```bash
cd /c/Users/marcu/AutoDeck                      # Git Bash on Windows
git status --short
git stash list
PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m pytest engine/tests -q -m "not slow"
PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m pytest engine-v1/tests -q
```

### 0.2 Nothing is committed

**Confirmed.** The last commit is `ecb7b30` ("Final numbers: 781.7s -> 317.0s
on the real Key West scan"), on branch `master`, with **no git remote
configured**. Every piece of v5.1 / v5.2 / v5.3 work — which is a large amount
— is **uncommitted** in the working tree:

```
Modified:  README.md  app/README.md  engine/README.md  docs/v5-build-notes.html
           app/autodeck_app/__init__.py  bridge.py  server.py
           app/autodeck_app/static/app.js  index.html  style.css
           app/pyproject.toml
           engine/autodeck2/nesting.py  sheetjob.py  sheets.py
           tools/ui_smoke.py
Untracked: engine/autodeck2/seamplace.py  seamplan.py  seamsnap.py
           engine/tests/test_nesting_speed.py  test_overlay_geometry.py
           test_seamplan.py  test_seamsnap.py  test_sheet_seams.py
```

**Do not `git checkout`, `git reset`, `git clean` or `git stash` anything
without asking the owner.** There is no remote and no backup. A careless
reset destroys days of work permanently.

**Needs owner confirmation:** whether to commit this work, and whether the
project should be pushed to a remote (GitHub was explicitly declined earlier —
the owner chose "Skip GitHub for now").

### 0.3 Run data is not in git and is not recoverable

**Confirmed.** `engine/outputs/runs/*`, `inputs/`, `app/inputs/*` and all
`cache/` folders are gitignored. Files in there — including hand-placed seam
sets in `seams.json` — exist in exactly one place. During development an agent
overwrote a `seams.json` containing seams the owner had drawn by hand; it was
only recovered by grepping conversation transcripts. **Back up any
`seams.json` before running anything that writes it**, and verify the restore.

---

## 1. What this software is and why it exists

**Confirmed.**

AutoDeck turns a **3D photogrammetry scan of a boat's cockpit floor** into
**DXF cut files for VCarve**, so a CNC can cut synthetic marine decking panels
that fit the boat.

The owner is a marine decking fabricator (`dynastycustoms21@gmail.com`). The
material is **Reflex TruGrain**, which comes on **40 × 80 inch sheets** and is
**directional** — the grain must run along the length of the boat.

The problem it replaces: templating a deck by hand with cardboard and a
straightedge, which is slow and imprecise on a curved, obstacle-filled surface.

The hard part is not the CNC. It is that a boat deck is a **curved, non-planar
surface**, so the outline cannot simply be projected flat — it must be truly
*developed* (unrolled) so the cut part is the right size when laid back down.

---

## 2. The final goal and intended finished product

**Confirmed from the owner's stated requirements across development.**

A single application the fabricator double-clicks, into which they drop a scan,
and out of which come sheet-by-sheet DXF files ready to load into VCarve —
with the deck split into pieces that fit the sheet, are laid out with minimum
waste, respect the grain direction, and have seams that look deliberate rather
than accidental.

Explicitly *not* a goal: a general CAD package. It does one job for one
material on one class of product.

**Needs owner confirmation:** whether this is ever intended to ship to anyone
other than the owner's own shop (packaging, licensing, updates, multi-user).

---

## 3. Main features and the intended user workflow

**Confirmed** — this is the numbered flow in the UI's left column.

1. **Scan.** Drop an `.obj`, or **Open a scan folder…** to bring the `.mtl` and
   texture images too. A textured scan is displayed with its real photographic
   surface, and colour is used as extra evidence when finding the deck edge.
   The file is used **in place**; nothing is copied.

2. **Outline.** Choose units, layout mode (`nest` or `boat-plan`) and a pattern
   (teak lines / diamond stitch / hexagons / none). The engine segments the
   deck into panels, truly develops (unrolls) each one, and draws the raw wall
   line, obstacles, seams and pattern over the mesh.

3. **Auto-fit** — turns the raw traced outline into clean LINE/ARC geometry:
   straight lines first, then the largest tangent arcs that fit. Kept 1.5 mm
   *inside* the detected border and only ever simplifies inward (slightly small
   fits the boat; slightly big does not). Writes `final_auto.dxf`.

   **Or — Ingest a drawn `.3dm`.** Download `outline.3dm`, draw the cut line by
   hand in Rhino on the unlocked `USER_CAM::PANEL_n` layer (Line and Arc only),
   mark intentional sharp corners as Points on `USER_CORNERS`, save, and ingest
   it. Strict validation (0.5 mm endpoint snap, 0.10° tangency) produces
   `final.dxf`. **`final.dxf` takes precedence over `final_auto.dxf`** for
   everything downstream.

4. **Seams & sheets.** Place seams, split the panels, nest the pieces onto
   40 × 80″ sheets, export one DXF per sheet.

5. **Files.** Download everything.

### The seam interaction (this is the most recently built part)

Pick a direction — **Vertical** (along the boat, bow to stern), **Horizontal**
(across it), or **Diagonal** with an angle off the centreline — press **Place
seam**, then move the pointer over the deck. The seam you *would* get is drawn
live, trimmed to the panel's fitted edges, broken around cut-outs, with its
length and panel named. Click locks it in. Works over the 3D boat and over the
flat layout. Escape leaves the mode.

Supporting controls: **Recalculate**, **Find the best seam layout** (automatic),
**Clear all seams**, per-seam remove and a per-seam "as placed" override.

---

## 4. Architecture and technology stack

**Confirmed.** Three layers, strictly separated:

```
C:\Users\marcu\AutoDeck\
├── AutoDeck.bat          Windows launcher  <- the button; the shortcuts point here
├── AutoDeck.command      macOS launcher
├── AutoDeck.app          macOS app bundle
├── AutoDeck.lnk          shortcut (also on Desktop and in the Start menu)
├── AutoDeck.ico
├── README.md             owner-facing
├── docs/v5-build-notes.html
├── app/                  the browser UI (Flask + three.js)
├── engine/               AutoDeck2 — the v2 engine
├── engine-v1/            AutoDeck v1 — the analysis engine v2 runs on
├── tools/                dev scripts, not part of the app
├── inputs/               generated test scans (gitignored, 258 MB)
└── .venv/                created by the launcher on first run
```

**Layer 1 — `app/autodeck_app/` (the UI).** Flask server plus a single-file
browser app. No framework, no build step, no bundler.

| File | Role |
|---|---|
| `__main__.py` | `python -m autodeck_app [--port N] [--no-browser]` |
| `server.py` | all HTTP routes |
| `bridge.py` | the glue to the engine; overlay geometry; the flat↔world lift |
| `meshview.py` | decimates the scan into a binary mesh stream for the browser |
| `jobs.py` | background job runner (long operations return a job id) |
| `settings.py` | every path, all overridable by environment variable |
| `static/app.js` | the whole UI — one IIFE, ~1500 lines |
| `static/index.html`, `static/style.css` | markup and styling |
| `static/vendor/` | three.js r128 + OrbitControls, vendored |

**Layer 2 — `engine/autodeck2/` (the v2 engine).** Pure Python, no UI.

| Module | Role |
|---|---|
| `pipeline.py` | stage orchestration; `run_outline` |
| `engine.py` | the cached engine run (scan → developed panels) |
| `assign.py`, `layout.py` | panel assignment; laying panels out (`nest` / `boat-plan`) |
| `autofit.py`, `fitter.py` | raw outline → LINE/ARC CAM geometry |
| `ingest.py`, `outline3dm.py`, `calibration.py` | the Rhino round trip |
| `teak.py` | pattern generation; also where the **boat axis** comes from |
| `sheets.py` | `Seam`, splitting panels, sheet frame, fitted-DXF reading |
| `seamsnap.py` | **corrects a placed seam** — boat-axis-first (see §7) |
| `seamplace.py` | seam geometry: trims a seam to the panel it crosses |
| `seamplan.py` | **the automatic seam optimiser** |
| `nesting.py` | bottom-left first-fit nesting onto sheets |
| `sheetjob.py` | ties it together; `plan()`, `preview()`, axis resolution |
| `cache.py` | content-keyed engine cache |
| `v1compat.py` | **the only module allowed to import v1** |

**Layer 3 — `engine-v1/src/autodeck/` (v1).** The analysis engine: mesh I/O,
curvature, segmentation, surface development (LSCM/ARAP + exact geodesics),
relief/boundary fields, patterns. Pinned at version **0.3.6**.

### The v1 boundary is a hard contract — do not casually cross it

**Confirmed, and important.** `engine/autodeck2/v1compat.py` is the *only*
module in v2 that imports the v1 `autodeck` package. It wraps every v1 function
v2 needs under a stable v2-side name, and `check_compatibility()` runs at
startup and in `engine/tests/test_contract.py`. It:

- pins `V1_REQUIRED_VERSION = "0.3.6"`,
- fingerprints the v1 **source**, because v1 is an editable install with no git
  history, so edits there must invalidate caches,
- and **verifies every wrapped signature**, failing loudly rather than silently
  drifting.

If you change anything in `engine-v1/`, expect the contract test to fail, and
treat that as the system working correctly. Update `v1compat.py` deliberately.

### Stack

- **Python ≥ 3.12** (confirmed requirement in both `pyproject.toml` files)
- numpy `>=1.26,<3`, scipy `>=1.13,<2`
- **shapely == 2.1.1**, **ezdxf == 1.4.4**, **libigl == 2.6.1** (all pinned)
- rhino3dm `>=8.0`
- Flask `>=3.0,<4` (app only)
- three.js **r128**, vendored — not from a CDN
- **matplotlib is NOT installed** and should not be added

---

## 5. Install, run and test on a new computer

**Confirmed** from `AutoDeck.bat` and the READMEs.

### Normal use

1. Install **Python 3.12+** from python.org, ticking *"Add python.exe to PATH"*.
   (The Microsoft Store `python` stub does not count; the launcher detects it
   and says so.)
2. Double-click **`AutoDeck.bat`**. The **first launch needs the internet once**
   — it creates `.venv/` and installs the libraries, which takes a few minutes.
   Every launch after that is offline.
3. The browser opens at **<http://127.0.0.1:8765/>**. The console window must
   stay open; closing it stops AutoDeck.

The three parts are located **relative to the folder**, so the whole `AutoDeck`
folder can be moved, renamed or copied anywhere and still work. Nothing is
installed system-wide beyond Python itself.

**Confirmed:** the Desktop shortcut, the Start-menu shortcut and
`AutoDeck.lnk` all target `C:\Users\marcu\AutoDeck\AutoDeck.bat` with working
directory `C:\Users\marcu\AutoDeck`.

### Running it by hand (what you will actually do)

```bash
cd /c/Users/marcu/AutoDeck
PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m autodeck_app --port 8790 --no-browser
```

### Tests

```bash
cd /c/Users/marcu/AutoDeck
PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m pytest engine/tests -q -m "not slow"
PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe -m pytest engine-v1/tests -q
```

`-m "not slow"` deselects real-scan runs that take minutes.

**Test counts, measured 2026-09-02 with the exact commands above:**

```
engine/tests   297 passed, 1 deselected   (409.75 s)
engine-v1      116 passed                 (47.32 s)
```

For comparison, the pre-v5.1 baseline was **83 passed, 1 deselected**, so the
seam, sheet, nesting and overlay work added roughly 214 tests.

**One caveat, and it matters:** that run was started while the final build was
still executing, so it may not reflect the very last edits. It is a real
measurement, not an agent's claim, but re-run both suites yourself before
relying on them. The engine suite takes about seven minutes.

### Ad-hoc scripting against the engine

Put these three at the front of `sys.path`: `engine-v1/src`, `engine`, `app`.

### Environment variables (names only — none are secrets)

`AUTODECK2_ROOT`, `AUTODECK_V1_ROOT`, `AUTODECK_INPUTS_DIR`,
`AUTODECK_PREVIEW_CACHE`, `AUTODECK_RUNS_DIR`, `AUTODECK_HOST`,
`AUTODECK_PORT`, `AUTODECK_PREVIEW_FACES`, `AUTODECK_PREVIEW_TEXTURE_MAX`,
`AUTODECK2_IGNORE_LOCAL_CONFIG`.

**This project uses no passwords, API keys or tokens of any kind.** It is a
wholly local application with no network calls except the one-time pip install.

### Platform notes that have caused real bugs

- The shell in use was **Git Bash on Windows**. PowerShell is also available.
- **Every source file uses LF endings.** `Path.write_text()` on Windows
  converts to CRLF and has silently damaged files here before. Pass
  `newline="\n"` or use an editor tool.
- Windows uses `spawn`, not `fork`, for `ProcessPoolExecutor`, so any payload
  must be picklable per worker.

---

## 6. What is complete and confirmed working

**Confirmed by running it on 2026-09-02 unless noted.**

- **The full pipeline**: scan → panels → developed surfaces → raw curves →
  auto-fit → `final_auto.dxf` → seams → nested sheets → `sheet_NN.dxf`.
- **The Rhino round trip**: `outline.3dm` out, hand-drawn `.3dm` in, strict
  validation, `final.dxf` out, and a calibration report either way.
- **The engine cache.** Content-keyed on the v2 engine config sections, the
  full v1 config, the scan sha256, the v1 version and a v1 source fingerprint.
  Reopening a cached run takes ~2.3 s instead of minutes.
- **Performance work (v5).** The real Key West scan went **781.7 s → 317.0 s**
  (this is what commit `ecb7b30` records).
- **Nesting speedup (v5.3).** `nest()` **7.88 s → 0.18 s** on the axis run,
  `plan()` ~8.3 s → 0.41 s; full polygon collision tests **175,235 → 3**.
  Verified **bit-identical** placements against `git show
  ecb7b30:engine/autodeck2/nesting.py` across 40 configurations — zero
  differences in offsets, rotations, sheet assignment or order.
- **Smooth 3D overlays (v5.3).** See §7.
- **`pick_flat`** (screen/world → placed frame): round-trip median error
  **0.01 mm**, max 0.09 mm; correctly returns `panel_id: None` off the deck.

---

## 7. Important technical and product decisions already made

These are decided. Do not silently reverse them; each was chosen after
measurement or an explicit instruction from the owner.

### 7.1 The boat axis is the master reference for seams

**Owner's instruction, confirmed implemented.** One single direction —
`run.json → teak.frame.longitudinal_axis`, produced by the pattern stage —
orients three things: the **teak lines**, the **80″ sheet dimension**, and now
the **seams**.

- A **long seam** ends up *exactly* parallel to the boat centreline and
  therefore to the teak lines.
- A **short seam** ends up *exactly* 90° to it.
- This **overrules** lining a seam up with a nearby edge. The owner's words:
  *"the horizontle short seams are always going to be 90 degrees or perfectly
  side to side with the boat over ruling the in line with other features"*.
  The reason is that a long seam a degree or two off fans open against the
  plank lines — the owner calls the result *"pie shape things"*.
- Only **diagonal** seams (outside the capture angle on both masters) get the
  older feature snapping: exactly collinear with a straight edge, or exactly
  tangent to an arc.
- Capture angle is a **separate, wider setting** (`seam_axis_snap_deg`,
  default **20°**) than feature snapping (`seam_snap_angle_deg`, default 5°),
  because the owner said these seams are *always* square and 5° would let a
  roughly placed line stay crooked.

Everything routes through `sheetjob.resolve_axis()`, the same call `plan()` and
`preview()` use, so a manual grain override re-squares every seam. **Do not
read `run.json` directly for the axis.**

Verification that exists: a snapped long seam was compared against the actual
teak plank segments in `final_auto.dxf` — worst deviation **< 0.05°**.

### 7.2 Bow direction is known, not guessed

**Confirmed.** `engine-v1/src/autodeck/patterns.py` computes `bow_sign` and
`bow_confidence` from cross-boat width slices (the narrow end is the bow) and
stores them in `run.json → teak.frame`. On the reference run: `bow_sign = 1`,
`bow_confidence = 0.824`, `axis_angle_degrees = 3.680`. The seam tab uses this
to draw the boat **bow up, stern down**.

Consequence worth remembering: **"square" on that boat is 93.68°, not 90°.**
Any test asserting exactly 0 or 90 is testing the wrong thing.

### 7.3 The 3D overlay is a *smoothed rendering*; the flat view and the DXFs are exact

**Confirmed, and this distinction must never be blurred in the UI.**

The problem: the scan mesh carries sub-millimetre photogrammetry noise on ~5 mm
triangles, so evaluating one triangle per point painted that noise into every
drawn line. Measured on panel 1: turn angle p90 **26.03°** where the flat DXF
was 0.94°.

The fix: `bridge._make_lifter()` is a **degree-1 moving-least-squares plane
fit** over the neighbourhood (cKDTree over uv vertices, K = 24, adaptive
bandwidth, tricube weights, ridged 3×3 normal equations). Result: turn p90
**26.03° → 0.96°**, median position change **0.01 mm**, p95 2.56 mm. Genuine
sharp corners survive because it is a property of the *surface*, not a filter
on the *curve*.

Alternatives that were tried and **rejected with measurements** — do not
re-propose them:

| Approach | Why rejected |
|---|---|
| Exact `igl` point location | Changed nothing (median 0.00 mm). Point location was never the problem. |
| Laplacian smoothing of the mesh | Moves deck vertices 0.5–7.7 mm median, up to 25 mm. It shrinks the boat. |
| Moving average along the curve | Rounds genuine sharp corners by up to 9.4 mm. |

`bridge._triangle_lifter()` is kept as the unsmoothed regression reference.
Overlays are also offset ~1.5 mm along the outward surface normal so they float
proud of the deck instead of dipping through it near panel edges (24–66 % of a
fitted CAM loop lands off the meshed footprint, because the fitted outline is a
fair curve while the scan's border is ragged).

### 7.4 Nesting output is frozen

**Confirmed.** The nesting speedup is fast because a blocked position is cheap
to *prove* — a point of the piece inside the occupied region means overlap — so
it filters candidate positions with vectorised point tests before any polygon
is built. **The exact test is the unmodified original call**, so the filter can
only ever *remove* positions, never declare one free.

If you touch `nesting.py`, you must re-prove bit-identical placements. A faster
nester that moves parts is a regression, not an improvement.

### 7.5 Other settled decisions

- **Sheets**: 1016 × 2032 mm; usable envelope 990.6 × 2006.6 mm (39 × 79″);
  6 mm seam gap; 20 mm minimum spacing between nested pieces; rotation **0° or
  180° only**, because the material is directional.
- **Arcs survive the cut.** Splitting a panel refits runs of sampled points
  back to single bulges rather than degrading arcs into dense polylines.
- **DXF**: `bulge = tan(θ/4)`, LWPOLYLINE `(x, y, bulge)`, `$INSUNITS = 4` (mm).
- **`final.dxf` beats `final_auto.dxf`** everywhere downstream.
- **The automatic seam layout is a bounded heuristic search.** It must report
  *"best found"* and never *"optimal"*, and every number shown must come from
  the production pipeline, never the coarse search proxy.
- **Consolidated out of OneDrive** to `C:\Users\marcu\AutoDeck` deliberately.
  The old `C:\Users\marcu\OneDrive\Documents\AutoDeck_Portable` folder still
  exists with stale copies — **it is not the live project.**
- **GitHub was explicitly declined** ("Skip GitHub for now").

---

## 8. Current limitations and known problems

### 8.1 `seam_gap_mm = 0` silently made every seam a no-op — fixed, verify it

**Confirmed root cause of a real bug the owner hit.** A seam cuts by
subtracting a kerf; `LineString.buffer(0)` is **empty**, so
`panel.difference(empty)` returns the panel whole. Measured: gap 6 → 10 pieces,
gap 1e-12 → 11 pieces, gap **0 → 1 piece**. The optimiser then reported
"pieces" of 2058 × 3994, 616 × 2413.2 and 182 × 2392.3 mm — **uncut panels**,
and exactly the oversize sizes the owner reported seeing.

Fixed in `sheets.settings()`, which now refuses a non-positive gap once, rather
than defending against it in five places downstream. **Verify this fix
survived the interrupted build.**

### 8.2 An equal-split fallback could exceed the envelope by up to 1.75 mm

**Confirmed and reportedly fixed.** Dividing a span into equal parts is not the
same as leaving equal *finished* bands — the outer bands lose kerf on one side
only. Swept every span 500–8000 mm at 0.1 mm: the naive split overflowed on 388
of them, worst case 1.75 mm. This is the path that runs when the time budget
beats a panel. **Verify.**

### 8.3 Seam edits could be resurrected by a stale response

**Confirmed as a real mechanism; the fix was in flight when archived.**
`refreshSheets()` GETs `/api/sheets` and overwrites `state.seams` from the
response — and switching to the Sheet layout tab calls it. Delete a seam,
switch tabs, and the deletion could be undone by an older in-flight response.
Separately, `replanSheets()` returned early on
`if (!state.seamsLoaded && !state.seams.length) return;`, so deleting the
**last** seam could never be sent at all.

The owner's requirement: *"I dont mind the 'wont fit on sheet' error but i dont
like that it was stopping me from deleting bad seams."* **Removing seams is the
way out of a bad layout, so it must always work**, whatever state the plan is
in. Verify that no seam-editing control is disabled because the plan failed.

### 8.4 Texture-assisted edge detection is unvalidated on real data

**Confirmed gap.** The colour/chromaticity evidence path was built and unit
tested (region averaging took false firing on teak caulk lines from 11.0 % to
0.3 %), but **the real Key West scan has no `.mtl`**, so it has never been
validated against a genuine photogrammetry atlas. Treat it as experimental.

### 8.5 Deliberately deferred performance work

**Confirmed, documented, not started:** OBJ parser vectorisation (~44 s
available) and top-view raster loops (~53 s available).

### 8.6 Disk usage

**Confirmed.** `app/inputs/` holds **five ~807 MB copies** of essentially the
same scan (≈4 GB). `engine/outputs/` is **919 MB** across 16 runs. All
gitignored. **Needs owner confirmation** before deleting anything — some of
those runs are reference fixtures the tests use by name.

### 8.7 Test fixtures are pinned by name and are mutable

**Confirmed.** Tests reference specific run folders:

- `21kwcockpit-1-20260901-180939` — **has a boat axis** (pattern `teak`,
  `axis_confidence` 0.649). Use this for anything axis-related.
- `21kwcockpit-1-20260901-172519` — **has no boat axis** (pattern `none`,
  `teak.frame` is `null`). It is deliberately kept as the no-axis fallback
  fixture. Any axis test pointed at this run passes vacuously.

An earlier version of the tests picked *whichever run was newest on disk*,
which meant they silently re-aimed at data created minutes earlier. That was
fixed by pinning. **Do not reintroduce "newest run" fixture selection.**

### 8.8 Things that are not known

- Whether the interrupted Ship and Docs phases completed. **Needs owner
  confirmation / verify yourself.**
- Whether the automatic seam layout's symmetry and rectangularity work landed
  intact. **Verify.**
- macOS launchers (`AutoDeck.command`, `AutoDeck.app`) exist but there is no
  evidence in this history that they were ever run. **Needs owner
  confirmation.**
- Licensing, distribution and any commercial intent. **Needs owner
  confirmation.**

---

## 9. Recommended next steps, in priority order

1. **Establish ground truth.** Run both test suites. Read `git status`. Do not
   trust §6's numbers.
2. **Finish or redo the interrupted Ship pass.** Confirm the version reads
   `5.3` in `app/autodeck_app/__init__.py`, the page `<title>`, the `.brand`
   span and `/api/state`; then cold-start `AutoDeck.bat` exactly as the
   shortcut does and confirm the page loads with a clean browser console.
3. **Verify the three fixes from the owner's last test session** (§8.1, §8.2,
   §8.3) by reproducing each complaint, not by reading the code. In particular:
   place seams that produce oversize pieces, then delete them one at a time,
   delete the last one, and clear them all — switching tabs in between.
4. **Get the work committed.** There is no remote and no backup. Ask the owner
   first, then commit in coherent pieces.
5. **Validate texture-assisted edge detection** against a scan folder that
   actually has an `.mtl` and texture images (§8.4).
6. **Then, and only then**, consider the deferred performance work (§8.5).

**Needs owner confirmation** before starting anything beyond step 4.

---

## 10. What you must do before modifying anything

1. **Read this entire file.** It is long because the traps here are not
   guessable from the code.
2. **Inspect the actual current code.** This file describes the project as of
   2026-09-02, mid-build. The code is the truth; this file is a map.
3. **Check for uncommitted work** — `git status --short` and `git stash list`.
   Assume everything uncommitted is precious and unbacked-up, because it is.
4. **Back up any `seams.json` you might touch**, restore it afterwards, and
   verify the restore by reading it back.
5. **Explain your understanding to the owner before you change anything** — in
   your own words: what the project is, what state you found it in, what you
   intend to change, and what you are unsure about. Wait for confirmation.
6. **When you report results, report them honestly.** This project's history is
   full of measurements that overturned confident assumptions — the "obvious"
   cause of the overlay roughness was wrong, the "obvious" nesting bottleneck
   measurement was taken on a degenerate case, and a fixture that looked
   unrecoverable was recovered. Measure before you conclude, say what you
   actually verified, and say plainly what you did not.

### House style, if you write code here

- Comments explain **why**, in full sentences, and are frequent at the top of
  modules and around non-obvious blocks.
- numpy-vectorised throughout; dataclasses for value types;
  `from __future__ import annotations`.
- Do **not** add dependencies. Do **not** reformat code you are not changing.
- Preserve **LF** line endings.
- UI wording is for a **fabricator, not a programmer**: short, concrete, no
  jargon.
