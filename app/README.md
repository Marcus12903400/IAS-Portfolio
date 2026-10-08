# AutoDeck 5.4 — the review app

Double-click to open. Drop in a 3D scan, see it in 3D, run the outline /
pattern / auto-fit stages and see every result drawn **over the mesh** and in
the **flat panel layout**. Download `final_auto.dxf` for VCarve, per-sheet DXFs
for the nested pieces, or `outline.3dm` to draw on in Rhino and ingest back.

New in 5.3 — the seam work:

- **Seams are placed by direction, not by dragging.** Pick **Vertical** (along
  the boat, bow to stern), **Horizontal** (across it) or **Diagonal** and type
  the angle off the centreline; press **Place seam**; move the pointer over the
  deck. The seam you would get is drawn live — trimmed to the panel's fitted
  edges, broken around cut-outs, with its length and panel named — and a click
  locks it in. The mode stays armed so seams can be placed one after another,
  and Escape stops. Works over the 3D boat and over the flat layout.
- **Every seam is squared to the boat.** The direction the pattern stage
  measured is the master reference: a long seam ends up exactly parallel to the
  centreline and therefore to the teak lines, a short one exactly 90° to it, all
  the way through to the piece edges in the sheet DXF. A seam placed by hovering
  is exact by construction; an older or hand-drawn one is straightened onto the
  nearer master if it is within 20° of it, and only then allowed to slide
  sideways onto a fitted edge that runs the same way. Each seam's row says what
  was done to it in plain words.
- **The seam tab is drawn bow up**, with the panels moved back to where they sit
  in the boat rather than nested for cutting, and the ends marked ▲ BOW and
  STERN. If which end is the bow is only a guess, it says so.
- **Find the best seam layout** — one button that works the seams out for you:
  full-width cuts along and across the boat, ranked on zero oversize pieces,
  then fewest sheets, then least waste, then fewest joins. The winner is
  re-checked with the exact production pipeline before any number is shown, and
  it reports "best found", never "optimal". Your seams are copied aside first
  and there is an Undo.
- **Seams & flat layout** — each seam splits the panel leaving a 6 mm gap
  between neighbouring pieces, and the fitted arcs survive the cut instead of
  being flattened to polylines.

From v5:

- **Open a scan folder** — the `.obj`, its `.mtl` and the texture images come in
  together, and the deck is shown with its real photographic surface under a new
  *Texture (scan photo)* shading mode. The colour also feeds the deck-edge
  detection as extra evidence; the root README explains how that is kept from
  making a good geometric result worse.
- **Sheet layout** — a third tab nesting the pieces onto 40 × 80 inch sheets with
  the grain along the boat (0° or 180° rotation only), a minimum piece gap you
  can set, and one `sheet_NN.dxf` per sheet for VCarve.
- **Faster.** A cold run of the 807 MB Key West scan went from 781.7 s to
  317.0 s (2.47×), mostly from vectorising the curvature stage (223.6 s → 8.4 s),
  building each panel's search tree once instead of once per curve
  (69.5 s → 1.7 s) and vectorising the OBJ statistics (142.5 s → 52.4 s). The
  output geometry is byte-identical throughout. AutoDeck now uses about half the
  machine's logical CPUs by default instead of exactly one core.

From v0.4.2:

- **Mesh shading controls** (right panel): Smooth / **Flat (facets)** /
  **Slope — walls dark** / **Height colors**, plus **light angle** and **light
  height** sliders. Flat shading with a low light rakes the surface so creases,
  dents and the wall line stand out against the fitted outline; Slope darkens
  the walls; Height colors the deck by elevation.
- **Exact-file verification layer**: `final_auto.dxf` (and `final.dxf`) are
  read back from the file itself — arcs reconstructed from bulges exactly as
  VCarve does — and drawn as their own white layer over the mesh and in the
  flat layout, so what you verify is literally what gets cut.

From v0.4.1:

- **Patterns**: teak lines, **diamond stitch** and **hexagons** — pick one and
  a size before running the outline; the pattern populates over the mesh and
  in the flat layout, continuous across panels in one boat frame.
- **Crisper auto-fit**: straight edges and the biggest tangent arcs that fit.
  The outer CAM outline is centred 1.5 mm *inside* the detected border and
  only ever simplifies further inward — a panel slightly too small still fits
  the boat; slightly too big does not. On the Key West scan every panel now
  validates into `final_auto.dxf`.
- **Dark viewer** so the light mesh and the coloured overlays read clearly.
- **Panels · flattening** box: per panel, whether it was truly unrolled or
  measured in its own (possibly tilted) plane, its tilt, and how far the flat
  drawing is from the 3D surface (area change / edge strain). Angled panels
  are never just "projected to CPlane" — proportions are preserved.

The engine is AutoDeck2 (`../engine`, which uses AutoDeck v1 in `../engine-v1` underneath).
This folder only adds the app; it never copies the engine, so fitter fixes made
in AutoDeck2 show up here immediately. Runs are shared with AutoDeck2
(`../engine/outputs/runs`), so the wizard, the command line and the app all
see the same runs — including the Key West run you drew on.

## Open it

- macOS: double-click **`AutoDeck.app`** (first time: right-click → Open, it is
  not signed) or `AutoDeck.command` in the folder above. A Terminal window shows progress and
  the browser opens at `http://127.0.0.1:8765/`. Leave the Terminal window open;
  closing it stops the app.
- Windows: double-click **`AutoDeck.bat`** in the folder above.
- First launch creates `.venv` and installs the engine (internet once, a few
  minutes). Needs Python 3.12 and the two engine folders (`../engine`,
  `../engine-v1`); set `AUTODECK2_ROOT` / `AUTODECK_V1_ROOT` if
  they live elsewhere.

## Using it

1. **Scan** — drop an `.obj` onto the page, choose a file, or *Browse on this
   computer* (opens a normal file dialog; the file is used in place, nothing is
   copied). The scan is read once (an 800 MB scan takes about a minute), decimated
   to ~250k faces for the preview and cached (`cache/preview/`). The full mesh is
   what the engine uses.
2. **Outline** — units (auto reads the `# units:` comment in the OBJ), layout
   `nest` (panels side by side) or `boat-plan`, teak on/off. Runs the AutoDeck2
   engine: a new scan takes a few minutes, cached afterwards. Layers appear:
   raw wall line, obstacles, seams, hints, robust reference, teak lines.
3. **Auto-fit** — lines first, then tangent arcs; adds the *Auto-fit CAM
   outline* and its corners; writes `final_auto.dxf` and `auto_cam.3dm`.
   **Ingest** takes a `.3dm` you drew on (`USER_CAM::PANEL_n` /
   `USER_CORNERS`) and shows *Your drawn CAM outline* plus `final.dxf`.
4. **Files** — download links; reports open in a new tab.

**Previous runs** lists every run; click one to reopen it (instant when its
engine result is cached). The layer panel toggles each family in both views;
*Show lines through the mesh* draws overlays without depth so a line hidden
behind a wall stays visible; *Fit view* recentres.

3D view: drag to orbit, right-drag to pan, wheel to zoom. Z is up, the scan is
scaled to millimetres so overlays (always mm) sit on the surface. Teak lines
and CAM loops are computed in the flat (unrolled) panel space and lifted back
onto the mesh through each panel's development, so what you see over the mesh
is exactly what the DXF contains.

## Layout

```
(launchers live one level up: AutoDeck.bat / AutoDeck.command / AutoDeck.app)
autodeck_app/        server.py (API)  bridge.py (engine glue, overlays, flat->3D lift)
                     meshview.py (fast OBJ read, decimation, preview cache)  jobs.py
                     static/ (index.html, app.js, style.css, vendor/three.js r128)
inputs/              uploaded scans       cache/preview/   decimated previews
```

Command line: `python -m autodeck_app [--port 8765] [--no-browser]` from the
`.venv` with `PYTHONPATH` set to the two engine trees (the launchers do this).
Environment: `AUTODECK_PORT`, `AUTODECK_RUNS_DIR`, `AUTODECK_INPUTS_DIR`,
`AUTODECK_PREVIEW_FACES`.
