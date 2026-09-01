# AutoDeck

Scan in → deck outline, pattern and auto-fit CAM geometry out, reviewed in a
browser and downloaded as DXF for VCarve.

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

app/                v0.4.2 review UI - the browser app you see
engine/             AutoDeck2: outline, patterns, auto-fit, ingest
engine-v1/          AutoDeck v1: the analysis engine engine/ runs on

.venv/              created on first launch; delete it to force a clean reinstall
```

The three parts are found **relative to this folder**, so you can move, rename
or copy the whole `AutoDeck` folder anywhere and it still works. Nothing is
installed system-wide beyond Python itself.

## Using it

1. **Scan** — drop an `.obj` on the page, or *Browse on this computer*. The file
   is used in place; nothing is copied. A large scan takes about a minute to
   read, then it is cached.
2. **Outline** — pick units, layout (`nest` or `boat-plan`) and a pattern
   (teak / diamond stitch / hexagons). Runs the engine and draws the raw wall
   line, obstacles, seams and pattern over the mesh.
3. **Auto-fit** — lines first, then the biggest tangent arcs that fit. Writes
   `final_auto.dxf` and `auto_cam.3dm`.
4. **Files** — download `final_auto.dxf` for VCarve, or `outline.3dm` to draw on
   in Rhino and ingest back.

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
`AUTODECK_INPUTS_DIR`, `AUTODECK_PREVIEW_FACES`, `AUTODECK2_ROOT`,
`AUTODECK_V1_ROOT`.

## If something goes wrong

| Symptom | Fix |
|---|---|
| "Python 3.12 is not installed" | Install from python.org with "Add python.exe to PATH" ticked |
| Setup fails partway | Delete `.venv/` and double-click `AutoDeck.bat` again |
| Port 8765 busy | The app picks the next free port and prints it |
| Launch hangs with no output | Make sure this folder is not inside OneDrive |
