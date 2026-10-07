"""Drive the AutoDeck page in a real browser and check the seam tool works.

A UI feature is not done because the endpoints return 200 -- the JavaScript has
to actually run.  This loads the page, fails on any console error or uncaught
exception, exercises the three tabs, and then works the seam tool the way a
person does: choose a direction, hover the deck, watch the preview appear, click
to lock it in.  It also checks the two things a screenshot cannot show --

  * the bow-up view's screen <-> model mapping round trips exactly.  Get that
    backwards and every seam lands somewhere other than where it was clicked,
    and the picture still looks plausible.
  * a seam placed by hovering needs NO straightening afterwards, because the
    direction was chosen before the line existed.
  * the seam column still reads as the four steps of the job, with the set-once
    knobs folded away in Settings.  A tidy column is a requirement of this
    release, and it is the first thing that decays.

It then works the seam list the way somebody does when a layout has gone wrong,
because that is the case the fabricator got stuck in: remove a seam and switch
tab while the save is still on the wire, remove them one at a time down to the
last one, clear the lot, undo, and do it all again with the save failing.  Every
one of those has to leave the page and seams.json saying the same thing, and
none of them may be blocked by the sheet plan -- taking seams away is how a
layout that will not fit is got out of.

-- and writes PNGs to look at.

    python tools/ui_smoke.py --url http://127.0.0.1:8765 --out shots/
    python tools/ui_smoke.py --run-id 21kwcockpit-1-20260901-180939 --optimise

Opening a run with `--run-id` copies its seams.json aside and puts it back at
the end, checking that the restore actually stuck: this test PLACES seams,
REMOVES them and CLEARS them, and a seam layout somebody put there by hand is
not something a smoke test may lose.  `--keep-changes` turns that off, and with
it off the steps that erase the whole set are skipped rather than run without a
copy to put back.  `--optimise` is opt in for the same reason -- the auto-layout
button replaces the whole seam set.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from pathlib import Path


# What the page must say it is.  The shortcut starts this version and the
# fabricator has been told to look for it, so it is checked in the title and in
# the header, which are the two places anyone actually reads it.
VERSION = "5.3"
# The four steps of section 4, in the order the work happens.  Checked as a list
# rather than by presence, because "all four headings exist" is also true of the
# pile they replaced.
SEAM_STEPS = ["Place a seam", "Seams on this deck", "Let the computer try", "Cut files"]
# Every control that has to be behind the Settings disclosure.  These are set
# once on a machine and never touched again; in the main flow they buried the
# four things a person does on every job.
SETTINGS_IDS = ["opt-axis-priority", "opt-axis-snap", "opt-snap-enabled", "opt-snap-angle",
                "opt-spacing", "opt-seamgap", "opt-grain", "opt-rot180"]

# Placing a seam re-plans the sheets, and the nesting takes about a second.
SETTLE_MS = 2500
# The "find the best seam layout" button searches for about ninety seconds and
# then confirms its answer with the exact pipeline, so give it a wide margin.
OPTIMISE_TIMEOUT_S = 300
# HOVER_MS in app.js: how often a hover may go out while the pointer moves.
HOVER_THROTTLE_MS = 40


# The seam colour from style.css, so the pixel count below is looking for the
# thing it means to find and not for any red on the page.
SEAM_RGB = (0xFF, 0x37, 0x5F)


def count_canvas_pixels(page, rgb: tuple[int, int, int], tolerance: int = 40) -> int | None:
    """How many pixels of the 3D canvas are close to `rgb`, with every overlay
    layer switched off first so only the seams are left to find.

    Returns None when Pillow is not installed rather than failing the run: the
    rest of the smoke test does not need it.
    """

    try:
        from PIL import Image
    except ImportError:
        return None
    import io

    page.evaluate("""() => { const ui = window.AutoDeckUI;
        ui.state.__layersWere = ui.state.overlays.layers.map((l) => l.on);
        for (const l of ui.state.overlays.layers) l.on = false;
        for (const id in ui.state.layers) ui.state.layers[id].obj.visible = false; }""")
    page.wait_for_timeout(900)
    image = Image.open(io.BytesIO(page.locator("#gl").screenshot())).convert("RGB")
    page.evaluate("""() => { const ui = window.AutoDeckUI;
        const were = ui.state.__layersWere || [];
        ui.state.overlays.layers.forEach((l, i) => { l.on = were[i] !== undefined ? were[i] : l.on; });
        for (const id in ui.state.layers) {
          ui.state.layers[id].obj.visible = !!ui.state.layers[id].layer.on;
        } }""")
    pixels = image.load()
    width, height = image.size
    return sum(1 for y in range(height) for x in range(width)
               if all(abs(pixels[x, y][i] - rgb[i]) <= tolerance for i in range(3)))


def count_changed_pixels(before: bytes, after: bytes) -> int | None:
    """How many pixels differ between two screenshots of the same element.

    An SVG element in the document is not a line on the screen: a zero-length
    chord, a bad transform or a stroke the colour of the ground is all present
    and correct in the DOM and invisible to the person looking at it.  Counting
    elements cannot see that; taking the view with the hover preview showing and
    again with the pointer off the deck can, and it needs no assumption about
    what colour the preview happens to be.

    Returns None when Pillow is not installed rather than failing the run.
    """

    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    import io

    a = Image.open(io.BytesIO(before)).convert("RGB")
    b = Image.open(io.BytesIO(after)).convert("RGB")
    if a.size != b.size:
        return None
    difference = ImageChops.difference(a, b).convert("L")
    return sum(1 for value in difference.getdata() if value > 24)


def find_run_dir(url: str, run_id: str) -> Path | None:
    """Where the run lives, so its seams.json can be put back afterwards.

    Asked of the SERVER, because the server is the thing that will be writing
    that file and it is the only party that knows for certain where its runs
    are.  Working it out from this process's own environment was wrong twice
    over: `AUTODECK2_ROOT` is set by the launcher and is usually absent from a
    shell that starts the test by hand, and even when it is set there is nothing
    saying the server was started with the same one.  Guessing wrong found no
    directory, took no copy, and then placed seams on the run anyway -- which is
    the exact thing this file promises not to do.  Falls back to the environment
    only if the server does not say.
    """

    import json as _json
    import os
    from urllib.request import urlopen

    root: Path | None = None
    try:
        with urlopen(f"{url.rstrip('/')}/api/state", timeout=5) as response:
            reported = _json.load(response).get("runs_dir")
        if reported:
            root = Path(reported)
    except Exception:                       # noqa: BLE001 -- fall back below
        root = None
    if root is None:
        root = Path(os.environ.get("AUTODECK_RUNS_DIR")
                    or (Path(os.environ.get("AUTODECK2_ROOT", Path.home() / "AutoDeck2")) / "outputs" / "runs"))
    candidate = root.expanduser() / run_id
    return candidate if candidate.is_dir() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--out", type=Path, default=Path("ui-shots"))
    parser.add_argument("--run-id", default=None, help="open this run before shooting")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep-changes", action="store_true",
                        help="do NOT put the run's seams.json back afterwards")
    parser.add_argument("--optimise", action="store_true",
                        help="also press the auto seam layout button -- it takes a couple of "
                             "minutes and REPLACES every seam on the run")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    # The page reports sizes in inches with a " that a cp1252 console cannot encode.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args.out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    checks: list[tuple[bool, str]] = []

    def check(ok: bool, message: str) -> bool:
        checks.append((bool(ok), message))
        print(f"  {'ok  ' if ok else 'FAIL'} {message}")
        return bool(ok)

    saved_seams: Path | None = None
    run_dir = find_run_dir(args.url, args.run_id) if (args.run_id and not args.keep_changes) else None
    if args.run_id and not args.keep_changes:
        # No copy, no test. This run is about to have seams placed on it, and
        # without somewhere to put the originals back from they are simply gone
        # -- which is worse than not running the test at all. It used to carry
        # on regardless, silently, whenever the run directory could not be
        # found, and that is how the cached boat lost its hand-placed layout.
        if run_dir is None:
            print(f"ERROR: cannot find the directory for run {args.run_id}, so its seams could not be "
                  f"copied aside. Point AUTODECK_RUNS_DIR at the runs folder, or pass --keep-changes "
                  f"if losing this run's seams is acceptable.")
            return 2
        if not (run_dir / "seams.json").is_file():
            print(f"run {args.run_id} has no seams.json yet; nothing to keep")
        else:
            saved_seams = run_dir / "seams.json.uismoke"
            shutil.copyfile(run_dir / "seams.json", saved_seams)
            print(f"kept a copy of {run_dir / 'seams.json'}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            page = browser.new_page(viewport={"width": 1680, "height": 1000})
            # Headless chromium has no GPU: it draws the deck through
            # SwiftShader at about 50 ms a frame, which keeps the main thread
            # busy enough that Playwright's default 30 s can run out while
            # waiting for a control on the 3D tab. This is the software
            # rasteriser, not the page -- on a real GPU the same frame is a
            # couple of milliseconds.
            page.set_default_timeout(60000)
            # /api/overlays answers 404 until a run is open, and the page is
            # built to shrug that off -- so it is noted, not counted as a fault.
            def on_console(message):
                if message.type not in ("error", "warning"):
                    return
                known = message.type == "error" and "404" in message.text and not opened["run"]
                (notes if known else problems).append(f"console.{message.type}: {message.text}")

            opened = {"run": False}
            notes: list[str] = []
            page.on("console", on_console)
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            # The auto-layout button asks before it replaces a seam set.
            page.on("dialog", lambda d: d.accept())

            # Not networkidle: the page polls /api/state, so the network never idles.
            page.goto(args.url, wait_until="load")
            page.wait_for_timeout(3500)

            check(page.title().endswith(VERSION), f"page title is {page.title()!r}")
            check(page.inner_text(".brand").strip().endswith(VERSION), f"brand shows {VERSION}")

            # ------------------------------------------ the column reads as steps
            # Section 4 used to be a direction chooser, an angle box, a place
            # button, three snap controls, a seam list, recalculate, auto layout
            # and export in one undifferentiated column. It now has to read as the
            # sequence the work happens in, with the set-once knobs folded away.
            print("\nthe seam column reads as steps")
            column = page.evaluate(
                """(ids) => {
                    const section = Array.from(document.querySelectorAll('#left section'))
                        .find((s) => s.querySelector('#btn-seam'));
                    const details = document.getElementById('seam-settings');
                    return {
                      heads: Array.from(section.querySelectorAll('h3')).map((h) => h.textContent.trim()),
                      order: ['btn-seam', 'seam-list', 'btn-optimise', 'btn-sheets'].map(
                          (id) => Array.prototype.indexOf.call(
                              section.querySelectorAll('*'), document.getElementById(id))),
                      open: details.open,
                      inside: ids.map((id) => !!document.getElementById(id).closest('#seam-settings')),
                      // A control inside a shut <details> is not rendered at all,
                      // which is the whole point -- and is why the checks further
                      // down have to open it before they can touch anything.
                      reachable: ids.every((id) => !!document.getElementById(id)),
                    };
                }""", SETTINGS_IDS)
            check(column["heads"] == SEAM_STEPS,
                  f"section 4 is the four steps in order: {column['heads']}")
            check(column["order"] == sorted(column["order"]) and min(column["order"]) >= 0,
                  f"place -> the seams you have -> let the computer try -> export, in that order "
                  f"{column['order']}")
            check(not column["open"], "the set-once knobs start folded away in Settings")
            check(all(column["inside"]) and column["reachable"],
                  f"and every one of them is in there {dict(zip(SETTINGS_IDS, column['inside']))}")

            if args.run_id:
                page.evaluate(
                    """async (id) => {
                        await fetch('/api/run/open', {method:'POST',
                            headers:{'Content-Type':'application/json'},
                            body: JSON.stringify({run_id: id})});
                    }""", args.run_id)
                # wait for the job to finish and overlays to arrive
                for _ in range(120):
                    page.wait_for_timeout(1000)
                    busy = page.evaluate(
                        "fetch('/api/state').then(r=>r.json()).then(s=>!!s.active_job)")
                    if not busy:
                        break
                page.wait_for_timeout(5000)
                opened["run"] = True

            shots = []
            for tab, name in (("3d", "3d"), ("flat", "flat"), ("sheets", "sheets")):
                page.click(f'#tabs button[data-tab="{tab}"]')
                page.wait_for_timeout(2500)
                path = args.out / f"{name}.png"
                page.screenshot(path=str(path))
                shots.append(path)
                print(f"  shot {path}")

            # ---------------------------------------------------------- chooser
            # On the flat tab, because a toolbar in a hidden view is not
            # clickable -- which is the point of the hidden guard in the CSS.
            print("\ndirection chooser")
            page.click('#tabs button[data-tab="flat"]')
            page.wait_for_timeout(800)
            page.select_option("#seam-dir-side", "angle")
            page.wait_for_timeout(150)
            mirrored = page.evaluate(
                """() => [document.getElementById('seam-dir-flat').value,
                          document.getElementById('seam-dir-3d').value,
                          !document.getElementById('seam-angle-row-flat').hidden]""")
            check(mirrored == ["angle", "angle", True],
                  f"picking Diagonal in the panel mirrors into both toolbars and shows the angle box {mirrored}")
            page.select_option("#seam-dir-flat", "across")
            page.wait_for_timeout(150)
            mirrored = page.evaluate(
                """() => [document.getElementById('seam-dir-side').value,
                          document.getElementById('seam-angle-row-side').hidden,
                          window.AutoDeckUI.state.seamDir.mode]""")
            check(mirrored == ["across", True, "across"],
                  f"picking Horizontal in a toolbar mirrors back and hides the angle box {mirrored}")

            # ------------------------------------------------- bow-up transform
            print("\nseam view, bow up")
            frame = page.evaluate(
                """() => { const ui = window.AutoDeckUI;
                    return {theta: ui.flat.theta, bow: ui.flat.bow,
                            panels: Object.keys(ui.flat.rings).length,
                            offsets: ui.flat.offsets,
                            boat: ui.state.boat}; }""")
            has_axis = bool(frame["boat"] and frame["boat"].get("axis"))
            if has_axis and frame["bow"]:
                bow = frame["bow"]
                want = -90.0 - math.degrees(math.atan2(-bow[1], bow[0]))
                check(abs(frame["theta"] - want) < 1e-9,
                      f"rotation is -90 - atan2(-by, bx) = {frame['theta']:.4f}° for bow {bow}")
                # The real test of "bow up": a point one metre further forward
                # has to draw HIGHER on the screen, and SVG y grows downward.
                up = page.evaluate(
                    """() => { const ui = window.AutoDeckUI, b = ui.flat.bow;
                        const a = ui.modelToFlat(0, 0, null);
                        const f = ui.modelToFlat(b[0] * 1000, b[1] * 1000, null);
                        return {dy: f.y - a.y, dx: f.x - a.x}; }""")
                check(up["dy"] < -999.0 and abs(up["dx"]) < 1e-6,
                      f"a metre towards the bow draws {-up['dy']:.1f} units up the screen, {up['dx']:.2e} sideways")
                marks = page.evaluate(
                    "() => Array.from(document.querySelectorAll('#flat .boat-mark')).map(t => t.textContent)")
                check(any("BOW" in m for m in marks) and any("STERN" in m for m in marks),
                      f"the view is marked {marks}")
                check(any(any(abs(v) > 1e-6 for v in off) for off in frame["offsets"].values()),
                      "panels are laid out where they are on the boat, not nested")
            else:
                check(abs(frame["theta"]) < 1e-12,
                      "no boat direction on this run, so the view is left unrotated")
                note = page.evaluate("() => document.getElementById('boat-axis-note').textContent")
                check("no boat direction" in note, f"and it says why: {note[:80]}…")

            # ------------------------------------------- the mapping round trip
            print("\nscreen <-> model round trip")
            trip = page.evaluate(
                """() => {
                    const ui = window.AutoDeckUI;
                    const svg = document.getElementById('flat');
                    const ctm = svg.getScreenCTM();
                    const out = [];
                    for (const panel of (ui.state.overlays ? ui.state.overlays.panels : [])) {
                        const bb = panel.bbox_flat; if (!bb) continue;
                        let found = null;
                        for (let i = 1; i < 24 && !found; i++) {
                            for (let j = 1; j < 24 && !found; j++) {
                                const x = bb[0][0] + (bb[1][0] - bb[0][0]) * i / 24;
                                const y = bb[0][1] + (bb[1][1] - bb[0][1]) * j / 24;
                                if (ui.panelForPlacedPoint(x, y) === panel.id) found = [x, y];
                            }
                        }
                        if (!found) continue;
                        // model -> svg user units -> model, which is the page's
                        // own maths and has to be exact...
                        const q = ui.modelToFlat(found[0], found[1], panel.id);
                        const pure = ui.flatToModel(q.x, q.y);
                        // ...and the whole way a pointer event actually travels,
                        // out through the browser's screen matrix and back.
                        const pt = svg.createSVGPoint(); pt.x = q.x; pt.y = q.y;
                        const client = pt.matrixTransform(ctm);
                        const local = svg.createSVGPoint();
                        local.x = client.x; local.y = client.y;
                        const back = local.matrixTransform(ctm.inverse());
                        const m = ui.flatToModel(back.x, back.y);
                        out.push({panel: panel.id, got: m.panel_id,
                                  pdx: pure.x - found[0], pdy: pure.y - found[1],
                                  dx: m.x - found[0], dy: m.y - found[1],
                                  client: [client.x, client.y]});
                    }
                    return out;
                }""")
            check(len(trip) >= 1, f"found an interior point on {len(trip)} panel(s)")
            pure = max((max(abs(t["pdx"]), abs(t["pdy"])) for t in trip), default=None)
            check(pure is not None and pure < 1e-9,
                  f"model -> flat -> model is exact (worst {pure:.3e} mm)")
            # The screen matrix is the browser's, and Chromium keeps it in single
            # precision, so this leg cannot be exact. A ten-thousandth of a
            # millimetre is a thousand times finer than the API's own rounding.
            worst = max((max(abs(t["dx"]), abs(t["dy"])) for t in trip), default=None)
            check(worst is not None and worst < 0.001,
                  f"and through the browser's own screen matrix too (worst {worst:.3e} mm)")
            check(all(t["got"] == t["panel"] for t in trip),
                  f"and lands back on the same panel {[(t['panel'], t['got']) for t in trip]}")

            # --------------------------------------------- hover, then click
            print("\nplace a seam by hovering")
            before = page.evaluate("() => window.AutoDeckUI.state.seams.length")
            page.select_option("#seam-dir-flat", "across")
            page.click("#btn-seam-flat")
            armed = page.evaluate("() => window.AutoDeckUI.state.placing")
            check(armed, "Place seam arms the hover mode")

            target = trip[0]["client"] if trip else None
            if target and not has_axis:
                # No boat direction means there is no along or across the boat to
                # place a seam on. The tool has to say that, not guess an axis.
                page.mouse.move(target[0], target[1])
                page.wait_for_timeout(900)
                refusal = page.evaluate(
                    """() => ({seam: window.AutoDeckUI.state.hoverSeam,
                               drawn: document.querySelectorAll('#flat .seam-preview').length,
                               hint: document.getElementById('seam-hint-flat').textContent})""")
                check(not refusal["seam"] and refusal["drawn"] == 0
                      and "no boat direction" in refusal["hint"],
                      f"with no boat direction it refuses and says why: {refusal['hint'][:110]}")
                target = None
            if target:
                page.mouse.move(target[0], target[1])
                page.wait_for_timeout(700)
                preview = page.evaluate(
                    """() => { const s = window.AutoDeckUI.state.hoverSeam;
                        return {drawn: document.querySelectorAll('#flat .seam-preview').length,
                                seam: s, hint: document.getElementById('seam-hint-flat').textContent}; }""")
                check(preview["drawn"] == 1 and preview["seam"],
                      f"hovering draws one live preview: {preview['hint']}")
                if preview["seam"]:
                    check("mm on panel" in preview["hint"],
                          "the toolbar shows the length and the panel while hovering")
                page.screenshot(path=str(args.out / "flat-hover.png"))
                shots.append(args.out / "flat-hover.png")

                # One element in the DOM is not one line on the screen. Take the
                # view with the preview showing, then with the pointer off the
                # deck -- pointerleave clears it and nothing else in the picture
                # moves -- and everything that changed IS the preview.
                with_preview = page.locator("#flat").screenshot()
                page.mouse.move(150, 500)            # the left column, off the SVG
                page.wait_for_timeout(500)
                without_preview = page.locator("#flat").screenshot()
                painted = count_changed_pixels(with_preview, without_preview)
                if painted is None:
                    print("  --   Pillow is not installed, so the flat preview pixels were not counted")
                else:
                    check(painted > 30,
                          f"the preview is really painted on the flat view: {painted} pixels "
                          f"change when the pointer leaves the deck")
                check(page.evaluate("() => window.AutoDeckUI.state.hoverSeam === null"),
                      "and taking the pointer off the deck clears it")
                page.mouse.move(target[0], target[1])
                page.wait_for_timeout(800)

                # Hovering fires on every pointer move. One request in flight at
                # a time and no faster than the throttle, or the preview ends up
                # drawing an answer from where the pointer used to be.
                inflight = {"now": 0, "peak": 0, "sent": 0}

                def on_request(request):
                    if request.url.endswith("/api/seam/hover"):
                        inflight["sent"] += 1
                        inflight["now"] += 1
                        inflight["peak"] = max(inflight["peak"], inflight["now"])

                def on_response(response):
                    if response.url.endswith("/api/seam/hover"):
                        inflight["now"] -= 1

                page.on("request", on_request)
                page.on("response", on_response)
                sweep = 60
                started = time.time()
                for i in range(sweep):
                    page.mouse.move(target[0] - 60 + i * 2, target[1] - 40 + i)
                    page.wait_for_timeout(16)     # about one move per frame
                elapsed = time.time() - started
                page.wait_for_timeout(1200)
                page.remove_listener("request", on_request)
                page.remove_listener("response", on_response)
                rate = inflight["sent"] / max(elapsed, 1e-3)
                check(inflight["peak"] <= 1,
                      f"never more than one hover on the wire (peak {inflight['peak']})")
                # The throttle is 40 ms, so 25 a second; allow half again for
                # timer jitter and the one trailing request that always goes.
                check(inflight["sent"] < sweep and rate <= 1000 / HOVER_THROTTLE_MS * 1.5,
                      f"{sweep} pointer moves in {elapsed:.1f} s coalesced into "
                      f"{inflight['sent']} request(s), {rate:.0f}/s")

                page.mouse.click(target[0], target[1])
                page.wait_for_timeout(SETTLE_MS)
                after = page.evaluate(
                    """() => { const s = window.AutoDeckUI.state;
                        return {n: s.seams.length, last: s.seams[s.seams.length - 1],
                                placing: s.placing,
                                lines: document.querySelectorAll('#flat .seam').length}; }""")
                check(after["n"] == before + 1, f"clicking locks it in ({before} -> {after['n']} seams)")
                check(after["placing"], "and the mode stays armed for the next one")
                # The click only pushes the seam; the answer that says what the
                # corrector made of it comes back from the re-plan.
                for _ in range(30):
                    if page.evaluate("""() => { const s = window.AutoDeckUI.state.seams;
                        return s.length && s[s.length - 1].snap_applied !== undefined; }"""):
                        break
                    page.wait_for_timeout(1000)
                after = page.evaluate(
                    """() => { const s = window.AutoDeckUI.state;
                        return {n: s.seams.length, last: s.seams[s.seams.length - 1],
                                placing: s.placing,
                                lines: document.querySelectorAll('#flat .seam').length}; }""")
                last = after["last"] or {}
                check(last.get("mode") == "across", f"the seam remembers how it was placed: {last.get('mode')!r}")
                # The whole point of choosing the direction first: the corrector
                # finds nothing to do, because the line was already square.
                check(last.get("snap_applied") is False,
                      f"it needed no straightening (snap_applied={last.get('snap_applied')}, "
                      f"note={last.get('snap_note')!r})")
                check(after["lines"] >= 1, f"{after['lines']} seam line(s) drawn on the flat view")

                world = page.evaluate(
                    """() => { const s = window.AutoDeckUI.state;
                        return {entries: s.seamsWorld.length,
                                points: s.seamsWorld.reduce((n, e) => n + (e.world || [])
                                    .reduce((m, w) => m + w.length, 0), 0)}; }""")
                check(world["points"] > 0,
                      f"the seams came back lifted onto the deck: {world['entries']} seam(s), {world['points']} points")

                # The third choice, the only one with a number of its own. The
                # box is labelled "off the boat centreline", so that is what is
                # measured -- not an angle on the screen, which changes the
                # moment the view is turned bow up.
                page.select_option("#seam-dir-flat", "angle")
                page.fill("#seam-angle-flat", "30")
                page.dispatch_event("#seam-angle-flat", "change")
                # Away and back, because the pointer is still sitting on `target`
                # from the click and moving it to where it already is fires no
                # pointermove at all. Back to `target` exactly, not a few pixels
                # off it: one screen pixel is about five millimetres at this
                # zoom, so a nudge walks off the deck and the tool then rightly
                # previews nothing.
                page.mouse.move(target[0] + 70, target[1] + 45)
                page.wait_for_timeout(150)
                page.mouse.move(target[0], target[1])
                page.wait_for_timeout(900)
                diagonal = page.evaluate(
                    """() => ({seam: window.AutoDeckUI.state.hoverSeam,
                               drawn: document.querySelectorAll('#flat .seam-preview').length,
                               dir: window.AutoDeckUI.state.seamDir,
                               hint: document.getElementById('seam-hint-flat').textContent})""")
                check(diagonal["dir"] == {"mode": "angle", "angle": 30},
                      f"the angle box feeds the tool {diagonal['dir']}")
                if has_axis:
                    check(bool(diagonal["seam"]) and diagonal["drawn"] == 1,
                          f"a diagonal previews like the other two: {diagonal['hint']}")
                if has_axis and diagonal["seam"]:
                    s = diagonal["seam"]
                    drawn = math.degrees(math.atan2(s["y2"] - s["y1"], s["x2"] - s["x1"])) % 180.0
                    gap = abs(drawn - frame["boat"]["axis_deg"]) % 180.0
                    gap = min(gap, 180.0 - gap)
                    check(abs(gap - 30.0) < 0.05,
                          f"and it is {gap:.3f}° off the boat centreline, which is what the box asks for "
                          f"({drawn:.2f}° in the layout, boat {frame['boat']['axis_deg']}°)")
                page.select_option("#seam-dir-flat", "across")

            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            check(page.evaluate("() => !window.AutoDeckUI.state.placing"), "Escape leaves the mode")

            # ----------------------------------------------- hovering in 3D
            print("\nplace a seam by hovering the 3D boat")
            page.click('#tabs button[data-tab="3d"]')
            # Wait for the toolbar rather than a fixed pause: the WebGL view can
            # take a moment to come back and a timeout here reads like a missing
            # control when it is only a slow frame.
            page.wait_for_selector("#seam-dir-3d", state="visible", timeout=15000)
            page.wait_for_timeout(1200)
            page.select_option("#seam-dir-3d", "along")
            page.click("#btn-seam-3d")
            orbit_off = page.evaluate("() => window.AutoDeckUI.state.placing")
            box = page.locator("#gl").bounding_box()
            hit3d = None
            if box:
                for fx, fy in ((0.5, 0.5), (0.45, 0.55), (0.55, 0.45), (0.5, 0.6), (0.4, 0.5)):
                    page.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
                    page.wait_for_timeout(500)
                    hit3d = page.evaluate("() => window.AutoDeckUI.state.hoverSeam")
                    if hit3d:
                        break
            if has_axis:
                check(bool(hit3d), f"hovering the 3D boat previews a seam {hit3d and round(hit3d['length_mm'])} mm"
                                   f" on panel {hit3d and hit3d['panel_id']}")
                check(page.evaluate("() => window.AutoDeckUI.state.hoverWorld ? "
                                    "window.AutoDeckUI.state.hoverWorld.length : 0") > 1,
                      "and the preview follows the curve of the deck in 3D")
            else:
                check(not hit3d, "with no boat direction the 3D view previews nothing either")
            page.screenshot(path=str(args.out / "3d-hover.png"))
            shots.append(args.out / "3d-hover.png")
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            check(page.evaluate("() => window.AutoDeckUI.state.placing === false"),
                  "and Escape gives the orbit control back" if orbit_off else "the mode toggles off")

            layer = page.evaluate("() => !!document.getElementById('layer-seams3d')")
            check(layer, "the layer panel has its own switch for the seams on the deck")

            # Are the seams actually PAINTED on the deck? Being in the scene
            # graph is not the same thing: a line drawn with the depth test off
            # never writes depth, and the mesh -- a transparent material, so it
            # renders after everything opaque -- then covers it. That bug looks
            # perfect from the console and shows nothing on screen, so this
            # counts pixels of the seam's own colour with the other layers off.
            seam_pixels = count_canvas_pixels(page, SEAM_RGB)
            if seam_pixels is None:
                print("  --   Pillow is not installed, so the 3D seam pixels were not counted")
            else:
                check(seam_pixels > 20,
                      f"the seams are really drawn on the deck: {seam_pixels} seam-coloured pixels")

            # ------------------------------------ the per-seam opt out and the
            #                                      straightening controls
            print("\nsnap controls")
            page.click('#tabs button[data-tab="flat"]')
            page.wait_for_timeout(600)
            if page.evaluate("() => document.querySelectorAll('.seam-item').length"):
                page.evaluate("() => document.querySelector('.seam-item .as-placed input').click()")
                # Ticking the box sets `snap` on the page in the same tick, so
                # waiting for that alone waits for nothing: it read the seam
                # mid-flight, with the box already clear and the straightened
                # coordinates still in it, and called that a fault. Whether the
                # seam really sits where it was put is the SERVER's answer --
                # `snap_applied` false, with the re-plan finished -- and on a
                # deck with eight seams that is several seconds behind the tick.
                for _ in range(40):
                    page.wait_for_timeout(1000)
                    if page.evaluate(
                            """() => { const s = window.AutoDeckUI.state.seams;
                                return !document.getElementById('btn-replan').disabled
                                    && s.length && s[0].snap === false
                                    && s[0].snap_applied === false; }"""):
                        break
                kept = page.evaluate("() => window.AutoDeckUI.state.seams[0]")
                same = (kept.get("raw") is None
                        or max(abs(kept["raw"][i] - kept[k]) for i, k in
                               enumerate(("x1", "y1", "x2", "y2"))) < 1e-9)
                check(kept["snap"] is False and same,
                      "'as placed' leaves a seam exactly where it was put")
                page.evaluate("() => document.querySelector('.seam-item .as-placed input').click()")
                page.wait_for_timeout(SETTLE_MS)

                # And it has to survive being ticked while a re-plan for the
                # PREVIOUS edit is still on the wire, which on a deck with eight
                # seams is most of the time. That reply knows nothing about the
                # tick, and taking its row as a "correction" undid it: the box
                # stayed clear on screen while the seam went back to being
                # straightened and moved 3.3 mm off the line it was put on -- in
                # the run as well as on the page.
                def as_placed_settled():
                    for _ in range(40):
                        page.wait_for_timeout(1000)
                        if page.evaluate(
                                """() => !document.getElementById('btn-replan').disabled"""):
                            return

                as_placed_settled()
                page.click("#btn-replan")
                page.wait_for_timeout(120)          # the re-plan is now on the wire
                page.evaluate("() => document.querySelector('.seam-item .as-placed input').click()")
                as_placed_settled()
                page.wait_for_timeout(2000)
                raced = page.evaluate(
                    """() => ({page: window.AutoDeckUI.state.seams[0],
                               box: document.querySelector('.seam-item .as-placed input').checked})""")
                run_seam = page.evaluate(
                    "fetch('/api/sheets').then(r => r.json()).then(s => s.seams[0])")
                drift = (0.0 if not raced["page"].get("raw") else
                         max(abs(raced["page"]["raw"][i] - raced["page"][k])
                             for i, k in enumerate(("x1", "y1", "x2", "y2"))))
                check(raced["page"]["snap"] is False and raced["page"]["snap_applied"] is False
                      and raced["box"] and run_seam["snap"] is False and drift < 1e-9,
                      f"and ticking it under a re-plan already on the wire still sticks "
                      f"(page snap={raced['page']['snap']}, run snap={run_seam['snap']}, "
                      f"moved {drift:.3f} mm)")
                page.evaluate("() => document.querySelector('.seam-item .as-placed input').click()")
                as_placed_settled()
                page.wait_for_timeout(SETTLE_MS)

            # The straightening controls live inside the Settings disclosure now,
            # and a control in a shut <details> is not rendered -- so this only
            # passes if the disclosure really opens.
            page.click("#seam-settings > summary")
            page.wait_for_timeout(250)
            check(page.evaluate("() => document.getElementById('seam-settings').open"),
                  "Settings opens on its summary")
            page.wait_for_selector("#opt-axis-priority", state="visible", timeout=5000)
            page.click("#opt-axis-priority")
            page.wait_for_timeout(300)
            check(page.evaluate("() => document.getElementById('btn-replan').disabled"),
                  "changing how seams are straightened re-plans on the spot")
            for _ in range(30):
                page.wait_for_timeout(1000)
                if not page.evaluate("() => document.getElementById('btn-replan').disabled"):
                    break
            page.click("#opt-axis-priority")           # put it back on
            page.wait_for_timeout(SETTLE_MS)
            page.click("#seam-settings > summary")     # and fold it away again
            page.wait_for_timeout(200)
            check(not page.evaluate("() => document.getElementById('seam-settings').open"),
                  "and folds away again")

            # ------------------------------------------------- recalculate
            print("\nrecalculate")
            page.click("#btn-replan")
            page.wait_for_timeout(200)
            busy = page.evaluate(
                """() => [document.getElementById('btn-replan').disabled,
                          document.getElementById('btn-optimise').disabled]""")
            check(busy[0], "Recalculate goes busy and cannot be pressed twice")
            # The search reads seams.json and writes it back; a re-plan on the
            # wire writes the same file. Starting one under the other would let
            # the old seams land on top of the answer with nothing to say so.
            check(busy[1], "and the seam search waits for it rather than racing it")
            page.wait_for_timeout(SETTLE_MS + 2000)
            check(not page.evaluate("() => document.getElementById('btn-replan').disabled"),
                  "and comes back")
            page.click('#tabs button[data-tab="sheets"]')
            page.wait_for_timeout(1500)
            pieces = page.evaluate("() => document.querySelectorAll('.sheet-piece').length")
            head = page.evaluate("() => document.getElementById('sheets-head').textContent")
            check(pieces > 0, f"the sheet layout redrew: {pieces} piece(s) -- {head}")
            # "Does it all fit yet" is the one question the seam list cannot
            # answer, and it is the reason to place another seam. It has to be
            # readable beside the seams, not only on a tab nobody is on.
            tally = page.evaluate(
                """() => { const el = document.getElementById('seam-tally');
                    return {hidden: el.hidden, text: el.textContent,
                            red: el.className.includes('bad-note')}; }""")
            over = "too big" in head
            check(not tally["hidden"] and "piece" in tally["text"] and "sheet" in tally["text"],
                  f"the seam list says how the job stands: {tally['text']}")
            check(tally["red"] == over,
                  f"and says it in red only when something still will not fit (red={tally['red']}, "
                  f"oversize={over})")
            page.screenshot(path=str(args.out / "sheets-after-seam.png"))
            shots.append(args.out / "sheets-after-seam.png")

            # ------------------------------------ removing a seam always wins
            # The fault the fabricator hit, driven the way they hit it.
            #
            # Removing a seam splices it out of the page's list and posts the
            # shortened list; switching to the Sheet layout tab GETs
            # /api/sheets, which reads seams.json.  Do the second within a
            # tenth of a second of the first and the GET reads the file BEFORE
            # the save lands: four seams, answering a page that has three, and
            # it used to be adopted whole.  The removed seam came back on screen
            # with the shortened list already on disk, and the next edit posted
            # the resurrected four straight back over it.
            #
            # Checked against the SERVER as well as against the page, because
            # the two agreeing is the whole point: a page that shows three and
            # a file that holds four is the state the user was cutting from.
            print("\nremoving a seam wins over an answer already on the wire")

            def disk_seams():
                return page.evaluate(
                    "fetch('/api/sheets').then(r => r.json()).then(s => (s.seams || []).length)")

            def settled():
                """Wait out the re-plan and anything it queued behind it."""
                for _ in range(40):
                    page.wait_for_timeout(700)
                    if not page.evaluate("() => document.getElementById('btn-replan').disabled"):
                        return

            page.click('#tabs button[data-tab="flat"]')
            page.wait_for_timeout(600)
            settled()
            # Everything from here to the end of the seam-editing checks takes
            # this run's seams apart, so the set is kept and put back before the
            # rest of the file runs. Not politeness: a run left with no seams
            # nests in a twentieth of the time one with five does, and the
            # checks further down are timed against a re-plan that takes about a
            # second.
            kept_seams = page.evaluate(
                "() => window.AutoDeckUI.state.seams.map((s) => Object.assign({}, s))")
            before = page.evaluate("() => window.AutoDeckUI.state.seams.length")
            if before:
                page.evaluate("() => document.querySelector('.seam-item .linkbtn').click()")
                page.wait_for_timeout(60)          # the save is now on the wire
                page.click('#tabs button[data-tab="sheets"]')
                settled()
                page.wait_for_timeout(1500)
                stayed = page.evaluate("() => window.AutoDeckUI.state.seams.length")
                check(stayed == before - 1,
                      f"remove one, switch tab at once, and it stays removed ({before} -> {stayed})")
                check(disk_seams() == before - 1,
                      f"and the run holds the same {before - 1} the page shows")
                page.click('#tabs button[data-tab="flat"]')
                page.wait_for_timeout(500)
            else:
                check(False, "no seams on this run to remove -- nothing was tested")

            # The same thing at the source, which is where it can be made to
            # happen every time rather than only when the timing is right. The
            # stamp is taken BEFORE the edit, exactly as the real GET's is, and
            # the reply carries the seam list as it was before the edit.
            stale = page.evaluate(
                """() => {
                    const ui = window.AutoDeckUI, s = ui.state;
                    const was = s.seams.map((x) => Object.assign({}, x));
                    if (!was.length) return null;
                    // stamped the way refreshSheets stamps a GET: told = false
                    const stamp = ui.seamStamp(false);
                    s.seams = was.slice(1);
                    s.seamsRevision++; s.seamsPending = s.seamsRevision;
                    ui.applySheets({available: false, reason: 'test', seams: was}, stamp);
                    const after = s.seams.length;
                    s.seams = was; s.seamsPending = 0; s.seamsRevision++;
                    return {was: was.length, after: after};
                }""")
            if stale:
                check(stale["after"] == stale["was"] - 1,
                      f"a GET answered from the old file cannot put it back "
                      f"({stale['was']} on the wire, {stale['after']} kept)")
            settled()

            # -------------------------- nothing about editing is disabled by
            #                            the state of the sheet plan
            # "I dont mind the wont fit on sheet error but i dont like that it
            # was stopping me from deleting bad seams." A layout that will not
            # fit is got out of by taking seams away, so the controls that take
            # them away are the ones that must never be switched off because it
            # will not fit.
            print("\nthe sheet plan never disables seam editing")
            page.evaluate(
                """() => {
                    const ui = window.AutoDeckUI;
                    ui.state.sheets = {available: true, piece_count: 5, files: [], oversize: [
                        {piece_id: 'P1-2', panel_id: 1, width_mm: 900.4, length_mm: 2472.2,
                         over_width_mm: 0.0, over_length_mm: 439.6,
                         hint: 'needs a seam across the boat'},
                        {piece_id: 'P2', panel_id: 2, width_mm: 1104.0, length_mm: 2413.2,
                         over_width_mm: 88.0, over_length_mm: 380.6,
                         hint: 'needs a seam across the boat and along the boat'}],
                      preview: {sheet_width_mm: 1016, sheet_length_mm: 2032,
                                usable_width_mm: 990.6, usable_length_mm: 2006.6, sheets: []}};
                    ui.renderSeamTally(); ui.renderSheetNotes();
                }""")
            page.wait_for_timeout(200)
            live = page.evaluate(
                """() => {
                    const ids = ['btn-seam', 'btn-seam-flat', 'btn-seam-3d', 'seam-dir-side',
                                 'seam-dir-flat', 'seam-dir-3d', 'seam-angle-side',
                                 'btn-clear-seams', 'btn-replan'];
                    const out = {off: []};
                    for (const id of ids) if (document.getElementById(id).disabled) out.off.push(id);
                    out.removes = document.querySelectorAll('.seam-item .linkbtn').length;
                    out.deadRemoves = Array.from(document.querySelectorAll('.seam-item .linkbtn'))
                        .filter((b) => b.disabled).length;
                    out.deadKeeps = Array.from(document.querySelectorAll('.seam-item .as-placed input'))
                        .filter((b) => b.disabled).length;
                    return out;
                }""")
            check(not live["off"] and live["deadRemoves"] == 0 and live["deadKeeps"] == 0,
                  f"with 2 pieces too big every way of editing a seam still works "
                  f"(disabled: {live['off'] or 'none'}, {live['deadRemoves']}/{live['removes']} "
                  f"remove links dead)")

            # ---------------------------- and the error says what to do about it
            # The engine already knows which pieces, by how much, and which way
            # a seam would have to run to fix each one. Saying "5 pieces still
            # will not fit" and keeping the rest to itself is what made the
            # error something to be stuck behind rather than something to act
            # on.
            print("\nthe wont-fit message names the pieces and the fix")
            told = page.evaluate(
                """() => ({tally: document.getElementById('seam-tally').textContent,
                           notes: document.getElementById('sheets-notes').textContent,
                           notesShown: !document.getElementById('sheets-notes').hidden})""")
            for where, text in (("beside the seams", told["tally"]), ("on the sheet tab", told["notes"])):
                check("P1-2 on panel 1" in text and "2472" in text and "440 mm too long" in text
                      and "needs a seam across the boat" in text,
                      f"{where}: names the piece, its size, how much over, and what would fix it")
            check(told["notesShown"], "and the sheet tab shows it without being asked")
            # Put the real layout back: everything above was drawn from a
            # made-up one, and what follows reads the page's own numbers.
            page.evaluate("() => window.AutoDeckUI.replanSheets()")
            settled()
            page.wait_for_timeout(800)

            # ------------------------------------------- clear all seams
            # Asked for by name: "I want to be able to delete the seams or clear
            # them if the auto seam doesnt work and place them manually."
            print("\nclear all seams, and undo")
            if saved_seams is None:
                print("  --   no copy of this run's seams was taken, so the clear was skipped")
            else:
                settled()
                had = page.evaluate("() => window.AutoDeckUI.state.seams.length")
                if not had:
                    check(False, "no seams to clear -- nothing was tested")
                else:
                    page.click("#btn-clear-seams")          # the dialog handler accepts
                    settled()
                    page.wait_for_timeout(1500)
                    cleared = page.evaluate(
                        """() => ({n: window.AutoDeckUI.state.seams.length,
                                   source: document.getElementById('seam-source').textContent,
                                   undo: (document.querySelector('#clear-result .linkbtn') || {})
                                             .textContent,
                                   off: document.getElementById('btn-clear-seams').disabled})""")
                    on_run = disk_seams()
                    check(cleared["n"] == 0 and on_run == 0,
                          f"Clear all seams empties the page and the run "
                          f"({had} -> {cleared['n']} on the page, {on_run} in the run)")
                    check("cleared them all" in cleared["source"],
                          f"and says so: {cleared['source']}")
                    check(cleared["off"], "the button switches itself off with nothing left to clear")
                    # Beside the button that did it. Two headings down, under
                    # the seam search's own result, it is an undo nobody looking
                    # at the thing they just cleared will find.
                    check(bool(cleared["undo"]) and "put your" in cleared["undo"],
                          f"with an undo beside the button that did it: {cleared['undo']!r}")
                    page.evaluate("() => document.querySelector('#clear-result .linkbtn').click()")
                    settled()
                    page.wait_for_timeout(1500)
                    back = page.evaluate(
                        """() => ({n: window.AutoDeckUI.state.seams.length,
                                   source: document.getElementById('seam-source').textContent})""")
                    on_run = disk_seams()
                    check(back["n"] == had and on_run == had,
                          f"and the undo puts all {had} back, on the page and in the run "
                          f"({back['n']} / {on_run})")
                    check("put back" in back["source"], f"and says so: {back['source']}")
                    # An undo that has stopped meaning undo is worse than none:
                    # left standing while new seams are placed, pressing it
                    # would swap them for the old set without asking.
                    page.evaluate(
                        """() => { const s = window.AutoDeckUI.state;
                            s.seams = s.seams.slice(0, -1);
                            return window.AutoDeckUI.seamsChanged('placed'); }""")
                    settled()
                    page.wait_for_timeout(900)
                    check(page.evaluate("() => document.getElementById('clear-result').hidden"),
                          "and the offer goes away as soon as the seams are edited again")
                    # The seam this took off goes back with the rest at the end
                    # of the section, from the copy taken before any of it.

                # ------------------------- one at a time, down to the last one
                # Deleting the LAST seam used to be dropped on the floor in any
                # state where the run's own seams had not arrived yet: the guard
                # that stops an empty list erasing a hand-placed layout could not
                # tell "I do not know what this run has" from "take the last one
                # away". They are not the same thing.
                print("\nremove them one at a time, right down to the last")
                settled()
                left = page.evaluate("() => window.AutoDeckUI.state.seams.length")
                removed = 0
                while left and removed < 12:
                    page.evaluate("() => document.querySelector('.seam-item .linkbtn').click()")
                    settled()
                    page.wait_for_timeout(900)
                    now = page.evaluate("() => window.AutoDeckUI.state.seams.length")
                    if now != left - 1:
                        break
                    left = now
                    removed += 1
                on_run = disk_seams()
                check(left == 0, f"removed {removed}, {left} left on the page")
                check(on_run == 0, f"and the last one really went: the run now holds {on_run}")
                source = page.evaluate("() => document.getElementById('seam-source').textContent")
                check("removed them all" in source, f"and the page says which set it is showing: {source}")

            # --------------------- a save that fails keeps the edit and says so
            # The old catch only wrote a line into the progress log. The seam
            # list went on showing the edit, seams.json did not have it, and
            # nothing anywhere said the two had parted company -- which is the
            # same fault as the resurrection above, arrived at from the other
            # end.
            print("\na re-plan that fails keeps the edit and says so")
            page.click('#tabs button[data-tab="flat"]')
            page.wait_for_timeout(600)
            page.select_option("#seam-dir-flat", "across")
            placed_for_failure = False
            if target:
                page.click("#btn-seam-flat")
                page.mouse.move(target[0] + 40, target[1] + 30)
                page.wait_for_timeout(200)
                page.mouse.move(target[0], target[1])
                page.wait_for_timeout(900)
                if page.evaluate("() => !!window.AutoDeckUI.state.hoverSeam"):
                    page.mouse.click(target[0], target[1])
                    settled()
                    page.wait_for_timeout(1200)
                    placed_for_failure = True
                page.keyboard.press("Escape")
            if not placed_for_failure or not page.evaluate(
                    "() => window.AutoDeckUI.state.seams.length"):
                print("  --   could not place a seam to fail the save on; skipped")
            else:
                page.evaluate(
                    """() => { window.__realFetch = window.fetch;
                        window.fetch = function (url) {
                          if (String(url).indexOf('/api/sheets/seams') === 0) {
                            return Promise.reject(new Error('the shop network dropped out'));
                          }
                          return window.__realFetch.apply(this, arguments); }; }""")
                held = page.evaluate("() => window.AutoDeckUI.state.seams.length")
                page.evaluate("() => document.querySelector('.seam-item .linkbtn').click()")
                page.wait_for_timeout(2500)
                failed = page.evaluate(
                    """() => {
                        const ids = ['btn-seam', 'btn-seam-flat', 'btn-seam-3d', 'seam-dir-side',
                                     'seam-dir-flat', 'seam-dir-3d'];
                        const s = window.AutoDeckUI.state;
                        const clear = document.getElementById('btn-clear-seams');
                        return {n: s.seams.length, pending: s.seamsPending,
                                rows: document.querySelectorAll('.seam-item').length,
                                shown: !document.getElementById('seam-trouble').hidden,
                                text: document.getElementById('seam-trouble').textContent,
                                retry: (document.querySelector('#seam-trouble .linkbtn') || {})
                                           .textContent,
                                off: ids.filter((id) => document.getElementById(id).disabled),
                                deadRemoves: Array.from(
                                    document.querySelectorAll('.seam-item .linkbtn'))
                                    .filter((b) => b.disabled).length,
                                clearOff: clear.disabled, clearWhy: clear.title};
                    }""")
                check(failed["n"] == held - 1 and failed["rows"] == held - 1,
                      f"the edit stays on screen when the save fails ({held} -> {failed['n']})")
                check(failed["shown"] and "not saved" in failed["text"]
                      and "dropped out" in failed["text"],
                      f"and it says so in plain words: {failed['text'][:130]}")
                check(bool(failed["retry"]), f"with a way to send it again: {failed['retry']!r}")
                # Nothing is switched off by the failure. Clear may be off, but
                # only ever because there is nothing left to clear -- which is
                # the one reason that cannot trap anybody, since what they would
                # be trapped from doing is removing seams that are not there.
                check(not failed["off"] and failed["deadRemoves"] == 0
                      and failed["clearOff"] == (failed["n"] == 0),
                      f"and every way of editing a seam still works after the failure "
                      f"(disabled: {failed['off'] or 'none'}; clear off={failed['clearOff']} "
                      f"with {failed['n']} seam(s): {failed['clearWhy'] or 'no reason given'})")
                check(bool(failed["pending"]), "and the page knows the run has not got it yet")
                page.evaluate("() => { window.fetch = window.__realFetch; }")
                page.evaluate("() => document.querySelector('#seam-trouble .linkbtn').click()")
                settled()
                page.wait_for_timeout(1500)
                mended = page.evaluate(
                    """() => ({hidden: document.getElementById('seam-trouble').hidden,
                               pending: window.AutoDeckUI.state.seamsPending,
                               n: window.AutoDeckUI.state.seams.length})""")
                on_run = disk_seams()
                check(mended["hidden"] and not mended["pending"] and on_run == mended["n"],
                      f"try again saves it and clears the warning "
                      f"(page {mended['n']}, run {on_run})")

            # Put back what these checks took apart, through the page's own edit
            # path, so the run is the one the rest of the file expects.
            if kept_seams:
                page.evaluate(
                    """(rows) => { window.AutoDeckUI.state.seams = rows;
                                   return window.AutoDeckUI.seamsChanged('placed'); }""", kept_seams)
                settled()
                page.wait_for_timeout(1200)
                check(page.evaluate("() => window.AutoDeckUI.state.seams.length") == len(kept_seams)
                      and disk_seams() == len(kept_seams),
                      f"and the {len(kept_seams)} seam(s) these checks worked on went back on the run")

            # ---------------------- the picture and the settings box agree
            # GET /api/sheets re-plans with the ENGINE's defaults. The page used
            # to fetch it on every tab switch, so with a grain angle typed in it
            # redrew the nesting for a DIFFERENT grain -- while the box still
            # said 45 and the exported DXFs would have been cut to 45. Anything
            # set by hand has to survive a trip to the Sheet layout tab.
            print("\nthe sheet picture matches the settings box")

            def layout_fingerprint():
                return page.evaluate(
                    """() => { const s = window.AutoDeckUI.state.sheets;
                        if (!s || !s.preview) return '';
                        return s.preview.sheets.map((sheet) => sheet.rings.map(
                            (r) => `${r.piece_id}:${r.rotation_deg}:${Math.round(r.points[0][0])}`
                        ).join(',')).join('|'); }""")

            def settle():
                """Wait for the re-plan to start and then to finish."""
                busy = "() => document.getElementById('btn-replan').disabled"
                for _ in range(10):                       # it has to go busy first
                    page.wait_for_timeout(200)
                    if page.evaluate(busy):
                        break
                for _ in range(40):
                    if not page.evaluate(busy):
                        return
                    page.wait_for_timeout(700)

            page.click('#tabs button[data-tab="flat"]')
            page.wait_for_timeout(500)
            page.evaluate("() => { document.getElementById('seam-settings').open = true; }")
            plain = layout_fingerprint()
            if plain:
                page.fill("#opt-grain", "45")
                page.dispatch_event("#opt-grain", "change")
                settle()
                turned = layout_fingerprint()
                check(turned and turned != plain,
                      "a grain angle typed by hand re-nests the sheets")
                page.click('#tabs button[data-tab="sheets"]')
                settle()
                page.wait_for_timeout(700)
                check(layout_fingerprint() == turned,
                      "and the Sheet layout tab keeps it instead of quietly redrawing the default")
                page.fill("#opt-grain", "")
                page.dispatch_event("#opt-grain", "change")
                settle()
                check(layout_fingerprint() == plain, "and clearing it puts the nesting back")
            page.evaluate("() => { document.getElementById('seam-settings').open = false; }")
            # Folding Settings away blurs the grain box, and a blurred box whose
            # value was changed fires its own `change` -- so one more re-plan
            # goes out after settle() has already said the page is quiet. The
            # made-up layout below is drawn straight into state.sheets, and that
            # re-plan landing on top of it reads as the page failing to draw a
            # cut-out. Wait it out.
            settle()
            page.wait_for_timeout(600)

            # ------------------------------------------- a hole is drawn as a hole
            # Driven with a made-up layout, because whether a nested piece has a
            # cut-out in it depends on which pieces the nester happened to place
            # and no fixed run can promise one. Drawn like the part it is cut
            # from, a cut-out reads as a second, smaller part nested inside the
            # first -- nine blue shapes for eight pieces, and the fabricator
            # goes looking for a part that does not exist.
            print("\na cut-out in a nested piece")
            page.evaluate(
                """() => {
                    const ring = (id, pts, hole) =>
                        ({piece_id: id, panel_id: 1, rotation_deg: 0, hole: hole, points: pts});
                    const box = (x, y, w, h) => [[x,y],[x+w,y],[x+w,y+h],[x,y+h],[x,y]];
                    window.AutoDeckUI.state.sheets = {
                      available: true, piece_count: 2, oversize: [], files: [],
                      preview: {sheet_width_mm: 1016, sheet_length_mm: 2032,
                        usable_width_mm: 990.6, usable_length_mm: 2006.6,
                        sheets: [{sheet: 1, utilisation: 0.5, rings: [
                          ring('P1-1', box(30, 30, 900, 900), false),
                          ring('P1-1', box(300, 300, 300, 300), true),
                          ring('P1-2', box(30, 1000, 900, 900), false)]}]}};
                    window.AutoDeckUI.renderSheets();
                }""")
            page.wait_for_timeout(300)
            drawn = page.evaluate(
                """() => ({classes: Array.from(document.querySelectorAll('#sheets polygon'))
                                        .map((p) => p.getAttribute('class')),
                           titles: Array.from(document.querySelectorAll('#sheets polygon title'))
                                        .map((t) => t.textContent)})""")
            # Holes last, so a part drawn after one cannot fill it back in.
            check(drawn["classes"] == ["sheet-piece", "sheet-piece", "sheet-hole"],
                  f"two parts and one cut-out, cut-outs drawn last: {drawn['classes']}")
            check(any(t.startswith("cut-out in ") for t in drawn["titles"]),
                  f"and it says so when you point at it: {drawn['titles']}")
            page.click('#tabs button[data-tab="sheets"]')
            settle()

            # ------------------------------------ what the auto layout reports
            # Driven with a made-up job result, so the wording is checked on
            # every run instead of only on the slow opt-in path. What it must
            # never claim matters more than any number in it.
            print("\nthe auto layout report")
            page.evaluate(
                """() => window.AutoDeckUI.renderOptimiseResult({
                    improved: true, seams_written: 3, seams_replaced: 4, backup: 'seams_previous.json',
                    candidates_evaluated: 94, elapsed_s: 27.4, budget_exhausted: false,
                    before: {seam_count: 4, sheet_count: 2, waste_percent: 42.0,
                             oversize: ['P1-1', 'P2'], oversize_detail: []},
                    after: {seam_count: 3, sheet_count: 2, waste_percent: 18.4, oversize: ['P2'],
                            oversize_detail: [{piece_id: 'P2', panel_id: 2,
                                               hint: 'needs a seam across the boat'}]}})""")
            report = page.evaluate(
                """() => ({text: document.getElementById('optimise-result').textContent,
                           bad: (document.querySelector('#optimise-result .bad-note') || {}).textContent,
                           undo: !!document.querySelector('#optimise-result .linkbtn')})""")
            # The "was" clause has to carry its own SHEET COUNT. The two waste
            # figures are shares of different amounts of material, and side by
            # side without them "39% (was 53%)" reads as a saving even when the
            # answer took two more sheets to get there. The fabricator buys
            # sheets.
            check("3 seams · 2 sheets · 18% waste" in report["text"]
                  and "was 2 sheets at 42% waste, 2 pieces too big" in report["text"],
                  f"one plain line: {report['text'][:110]}")
            check("best it found" in report["text"] and "optimal" not in report["text"].lower()
                  and "best possible" not in report["text"].lower(),
                  "it says best found, never optimal or best possible")
            check(report["bad"] and "P2 on panel 2" in report["bad"],
                  f"and names what is still too big, in red: {report['bad']}")
            check(report["undo"], "with an undo that puts the old seams back")
            # ...and none when there was nothing to put back. The backup file is
            # written even for a run that had no seams, and the page cannot post
            # an empty seam set, so an undo button there is a dead lie.
            page.evaluate(
                """() => window.AutoDeckUI.renderOptimiseResult({
                    improved: true, seams_written: 2, seams_replaced: 0, backup: 'seams_previous.json',
                    candidates_evaluated: 12, elapsed_s: 9.0, budget_exhausted: false,
                    before: {seam_count: 0, sheet_count: 3, waste_percent: 60.0,
                             oversize: [], oversize_detail: []},
                    after: {seam_count: 2, sheet_count: 2, waste_percent: 30.0,
                            oversize: [], oversize_detail: []}})""")
            check(not page.evaluate("() => !!document.querySelector('#optimise-result .linkbtn')"),
                  "and no undo offered when there were no seams to replace")
            # Fewer oversize pieces outranks every other objective, so the answer
            # can legitimately cost MORE sheets than the layout it replaced. That
            # is what the fabricator buys, so it has to be said in sheets and not
            # left to be worked out from two percentages.
            page.evaluate(
                """() => window.AutoDeckUI.renderOptimiseResult({
                    improved: true, seams_written: 6, seams_replaced: 4, backup: 'seams_previous.json',
                    candidates_evaluated: 94, elapsed_s: 24.0, budget_exhausted: false,
                    before: {seam_count: 4, sheet_count: 2, waste_percent: 53.0,
                             oversize: ['P1-1', 'P2'], oversize_detail: []},
                    after: {seam_count: 6, sheet_count: 4, waste_percent: 39.2,
                            oversize: [], oversize_detail: []}})""")
            cost = page.evaluate(
                "() => (document.querySelector('#optimise-result .warn-note') || {}).textContent")
            check(bool(cost) and "2 sheets more material" in cost and "could not be cut" in cost,
                  f"an answer that costs more sheets says so in sheets: {cost}")

            # -------------------------------------------- best seam layout
            if args.optimise:
                print("\nfind the best seam layout")
                page.click("#btn-optimise")
                page.wait_for_timeout(500)
                check(page.evaluate("() => document.getElementById('btn-optimise').disabled"),
                      "the button is disabled while it searches")
                deadline = time.time() + OPTIMISE_TIMEOUT_S
                while time.time() < deadline:
                    page.wait_for_timeout(2000)
                    if not page.evaluate("() => !!window.AutoDeckUI.state.job"):
                        break
                # The job going quiet is not the end of it: three views redraw
                # and the layout is re-planned with the page's own settings.
                page.wait_for_timeout(SETTLE_MS * 4)
                result = page.evaluate(
                    """() => ({text: document.getElementById('optimise-result').textContent,
                               seams: window.AutoDeckUI.state.seams.length,
                               undo: !!document.querySelector('#optimise-result .linkbtn')})""")
                check(bool(result["text"]) and "arrangement" in result["text"],
                      f"it reported back: {result['text'][:160]}")
                check("best it found" in result["text"] and "optimal" not in result["text"].lower(),
                      "and calls it the best it found, never optimal")
                check(result["seams"] > 0, f"the run now has {result['seams']} seam(s)")
                page.screenshot(path=str(args.out / "auto-layout.png"))
                shots.append(args.out / "auto-layout.png")

                # WHOSE seams are these? The search replaces the whole set, and
                # the misreading that started all of this was a hand-placed
                # layout taken for the machine's answer -- so the seam list and
                # the sheet tab both have to say, in words, which set is up.
                whose = page.evaluate(
                    """() => ({seams: document.getElementById('seam-source').textContent,
                               sheets: document.getElementById('sheets-source').textContent,
                               shown: !document.getElementById('sheets-source').hidden})""")
                check("automatic layout" in whose["seams"],
                      f"the seam list says whose seams these are: {whose['seams']}")
                check(whose["shown"] and whose["sheets"] == whose["seams"],
                      f"and the sheet tab says the same thing: {whose['sheets']}")

                # And one of ITS seams comes off as easily as one of yours.
                page.click('#tabs button[data-tab="flat"]')
                page.wait_for_timeout(800)
                had = page.evaluate("() => window.AutoDeckUI.state.seams.length")
                page.evaluate("() => document.querySelector('.seam-item .linkbtn').click()")
                page.wait_for_timeout(80)
                page.click('#tabs button[data-tab="sheets"]')
                for _ in range(40):
                    page.wait_for_timeout(700)
                    if not page.evaluate("() => document.getElementById('btn-replan').disabled"):
                        break
                page.wait_for_timeout(1500)
                left = page.evaluate("() => window.AutoDeckUI.state.seams.length")
                on_run = page.evaluate(
                    "fetch('/api/sheets').then(r => r.json()).then(s => (s.seams || []).length)")
                check(left == had - 1 and on_run == left,
                      f"and one of the automatic layout's own seams comes off and stays off "
                      f"({had} -> {left} on the page, {on_run} in the run)")

            # Park the page before the browser goes: every edit posts a replan
            # that WRITES seams.json, and one still on the wire would land after
            # the restore below and quietly undo it.
            try:
                page.evaluate("() => { for (let i = 1; i < 20000; i++) clearInterval(i); }")
                page.wait_for_timeout(1200)
            except Exception as exc:                      # noqa: BLE001 - best effort
                print(f"  (could not stop the page polling before closing: {exc})")
            browser.close()
    finally:
        if saved_seams and run_dir:
            live = run_dir / "seams.json"
            wanted = saved_seams.read_bytes()
            # Restore, then read it back and restore again if anything landed on
            # top. A seam layout the fabricator placed by hand is not something
            # a smoke test may lose, so "I copied the file" is not good enough.
            for attempt in range(5):
                shutil.copyfile(saved_seams, live)
                time.sleep(0.5)
                if live.read_bytes() == wanted:
                    break
                print(f"  something rewrote seams.json; restoring again ({attempt + 1})")
            ok = live.read_bytes() == wanted
            saved_seams.unlink()
            n = len(json.loads(wanted.decode("utf-8")).get("seams", []))
            print(f"\nput the run's original {n} seam(s) back" if ok
                  else f"\nWARNING: could not put the run's original {n} seam(s) back -- "
                       f"a copy is at {saved_seams}")
            if not ok:
                saved_seams.write_bytes(wanted)

    failed = [m for ok, m in checks if not ok]
    if problems:
        print(f"\n{len(problems)} console problem(s):")
        for item in dict.fromkeys(problems):
            print(f"  {item[:200]}")
    else:
        print("\nno console errors or warnings")
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    for message in failed:
        print(f"  FAILED: {message}")
    broke = any(p.startswith("pageerror") or "console.error" in p for p in problems)
    return 1 if (failed or broke) else 0


if __name__ == "__main__":
    sys.exit(main())
