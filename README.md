# AutoDeck 5.3

Scan in → deck outline, pattern and auto-fit CAM geometry out, reviewed in a
browser and downloaded as DXF for VCarve.

The page you open says **AutoDeck 5.3** in the tab and in the top-left corner.
If it says anything else, an older copy is still running on that port.

## Open it

**Double-click `AutoDeck.bat`** (Windows) or `AutoDeck.app` / `AutoDeck.command`
(macOS). A console window shows progress and your browser opens at
<http://127.0.0.1:8765/>.

Leave the console window open — closing it stops AutoDeck.

There is also a **AutoDeck** shortcut on your Desktop that does the same thing.

## Before the first launch

**Python 3.12 or newer must be installed**, from
<https://www.python.org/downloads/> — tick **"Add python.exe to PATH"** on the
first screen of the installer.

> If typing `python` opens the Microsoft Store, that is a Windows placeholder,
> not real Python. Install from python.org as above. The launcher detects the
> placeholder and tells you the same thing.

The **first launch needs the internet once.** It creates a private Python
environment in `.venv/` and downloads the libraries (a few minutes). Every
launch after that is offline and fast.

## What is in this folder

```
AutoDeck.bat        Windows: double-click this            <- the button
AutoDeck.command    macOS: double-click this
AutoDeck.app        macOS: same thing, as an app icon

app/                AutoDeck 5.3 review UI - the browser app you see
engine/             AutoDeck2: outline, patterns, auto-fit, ingest
engine-v1/          AutoDeck v1: the analysis engine engine/ runs on

.venv/              created on first launch; delete it to force a clean reinstall
```

The three parts are found **relative to this folder**, so you can move, rename
or copy the whole `AutoDeck` folder anywhere and it still works. Nothing is
installed system-wide beyond Python itself.

## Using it

1. **Scan** — drop an `.obj` on the page, or **Open a scan folder…** to bring
   the `.mtl` and texture images with it. A textured scan is shown with its real
   photographic surface, and the colour is also used as extra evidence when
   finding the deck edge (see *Texture* below). The file is used in place;
   nothing is copied.
2. **Outline** — pick units, layout (`nest` or `boat-plan`) and a pattern
   (teak / diamond stitch / hexagons). Runs the engine and draws the raw wall
   line, obstacles, seams and pattern over the mesh.
3. **Auto-fit** — lines first, then the biggest tangent arcs that fit. Writes
   `final_auto.dxf` and `auto_cam.3dm`.
4. **Seams & sheets** — pick a **Direction** (Vertical along the boat,
   Horizontal across it, or Diagonal at an angle you type), press **Place
   seam**, then move the pointer over the deck. The seam you would get is drawn
   live, trimmed to the panel edges and broken around cut-outs, with its length
   and panel named. Click to lock it in; the tool stays armed for the next one,
   and Escape stops. It works on the 3D boat and on the flat layout, and the
   flat layout is turned **bow up** so the deck reads the way it sits in the
   water. Each seam splits the panel leaving a 6 mm gap between neighbouring
   pieces. **Find the best seam layout** works the seams out for you — see
   below. The *Sheet layout* tab nests the pieces onto 40 × 80 inch sheets and
   **Export sheet DXFs** writes one `sheet_NN.dxf` per sheet for VCarve.
5. **Files** — download `final_auto.dxf` for VCarve, the per-sheet DXFs, or
   `outline.3dm` to draw on in Rhino and ingest back.

## Seams, grain and sheets

Reflex TruGrain is directional, so the **80 inch sheet dimension must run along
the length of the boat**. Pieces are therefore only ever rotated **0° or 180°**
when nesting — never 90°, which would run the grain across the boat, and never
mirrored. 180° is free and is what lets an L-shaped piece tuck into its
neighbour's notch.

| Setting | Default | Meaning |
|---|---|---|
| Sheet | 40 × 80 in (1016 × 2032 mm) | the physical sheet |
| Largest part | 39 × 79 in (990.6 × 2006.6 mm) | leaves a 12.7 mm margin all round |
| Seam gap | 6.0 mm | between two pieces that meet at a seam |
| Piece gap | 20.0 mm | minimum clear distance between nested pieces |
| Grain angle | auto | blank uses the detected boat axis; set it to override |

**Check the grain angle before cutting.** The boat axis is detected with a
confidence, and when that confidence is low the sheet report says so. Cutting a
directional material across the grain ruins the sheet, so if the preview looks
wrong, type the angle in.

Anything still too big for a sheet after seams is listed by name with how much
it is over and which way another seam is needed.

### The boat direction is what everything is squared to

One direction is measured once, when the outline runs with a pattern, and it is
then used for three things: the plank lines are drawn along it, the 80 inch
sheet dimension runs along it, and every seam is squared to it. So a **long
seam is exactly parallel to the teak lines** and a **short seam is exactly 90°
to them**, right through to the piece edges in the sheet DXF — no piece comes
out wedge shaped.

A seam placed with the direction chooser is exact from the moment it is drawn
and needs no correcting. An older seam, or one placed at an angle, is
straightened onto the nearer of those two directions if it is within 20° of it
(Settings → *Straighten anything within __°*), and then allowed to slide
sideways onto a fitted console or hatch edge that runs the same way. The seam
list says in plain words what was done to each one.

A run made with **Pattern: None** has no boat direction. The seam tool says so
and refuses to guess, and **Find the best seam layout** is switched off, until
you type a grain angle under Settings.

### Find the best seam layout

One button. It works out where the seams should go on its own — full-width cuts
along and across the boat only, because that is what actually gets cut and
because the material is directional — aiming for, in this order: every piece
inside the 39 × 79 inch envelope, then the fewest sheets, then the least waste,
then the fewest joins.

It searches for about a minute and a half, then **re-checks the winner with the
exact production pipeline** and reports that number, not the search's own
estimate. It is a bounded search, so it reports "best found", never "optimal" —
you can move or remove any seam it placed. The seams you had are copied to
`seams_previous.json` first (older copies are kept as `seams_previous_1.json`
and so on) and there is an **Undo** button beside the result.

## Texture

If the scan folder has an `.mtl` pointing at a diffuse map, AutoDeck uses it two
ways:

- **Display** — a *Texture (scan photo)* shading mode, on by default, so you can
  see the fitted outline sitting on the real surface.
- **Detection** — the colour change between deck and gelcoat is added to the
  boundary evidence. It is measured on *chromaticity*, which is unchanged when a
  pixel is merely brighter or darker, so baked shadows and exposure drift are
  largely ignored; and on *region-averaged* colour, so deliberate surface
  pattern (teak caulk lines 63.5 mm apart) does not read as a boundary.

Colour can only **reinforce** a boundary the geometry already suspects — it is
excluded from the "decisive evidence" test, so a stain or a scuff can never
invent a wall line on its own. Turn it off with `texture.enabled: false` in
`engine-v1/config/default.yaml`.

## How much of the computer it uses

AutoDeck defaults to about **half the logical CPUs** (12 of 24 here). Panels are
developed in parallel worker processes — processes rather than threads because
the libigl calls that dominate that stage hold the GIL.

Change it in `engine/config/default.yaml` under `parallel` (`cpu_fraction`, or
`max_workers` for an exact count), or per run with the `AUTODECK_WORKERS`
environment variable. The worker count deliberately does **not** affect the
engine cache key: the same scan gives byte-identical geometry at any setting.

Full detail: [`app/README.md`](app/README.md) for the UI,
[`engine/README.md`](engine/README.md) for the fitter and drawing rules,
[`engine-v1/README.md`](engine-v1/README.md) for the analysis pipeline.

## Where things are written

Runs go to `engine/outputs/runs/<run-id>/` — the UI, the AutoDeck2 wizard and
the command line all share them, so a run made one way is visible from the
others. Uploaded scans go to `app/inputs/`, decimated previews to
`app/cache/preview/`.

**Manufacturing approval is always `TEST_ONLY`** until a human has physically
verified a cut.

## Keep this folder out of OneDrive and iCloud

`.venv/` is thousands of small files. Cloud sync can evict them and cause
launches that hang with no explanation. This folder lives at
`C:\Users\marcu\AutoDeck`, which is outside OneDrive — keep it there.

## Command line

The launcher builds one shared environment at `.venv/`. From it:

```
.venv\Scripts\python -m autodeck_app [--port 8765] [--no-browser]   the UI
.venv\Scripts\python -m autodeck2                                    AutoDeck2 wizard
.venv\Scripts\python -m autodeck                                     v1 wizard
.venv\Scripts\python -m autodeck2 doctor                             health check
```

Environment overrides: `AUTODECK_PORT`, `AUTODECK_RUNS_DIR`,
`AUTODECK_INPUTS_DIR`, `AUTODECK_PREVIEW_FACES`,
`AUTODECK_PREVIEW_TEXTURE_MAX`, `AUTODECK_WORKERS`, `AUTODECK2_ROOT`,
`AUTODECK_V1_ROOT`.

Development tools in `tools/`:

```
python tools/make_test_boat.py --out inputs/testboat      textured synthetic scan
python tools/profile_run.py --scan <obj> --no-cache       per-stage wall clock
python tools/render_sheets.py --run <run dir>             PNG of the sheet DXFs
python tools/ui_smoke.py --run-id <run>                   drive the page in a browser
```

## If something goes wrong

| Symptom | Fix |
|---|---|
| "Python 3.12 is not installed" | Install from python.org with "Add python.exe to PATH" ticked |
| Setup fails partway | Delete `.venv/` and double-click `AutoDeck.bat` again |
| Port 8765 busy | The app picks the next free port and prints it |
| Launch hangs with no output | Make sure this folder is not inside OneDrive |
