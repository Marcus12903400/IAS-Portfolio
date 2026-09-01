"""Drive the AutoDeck page in a real browser and screenshot each tab.

A UI feature is not done because the endpoints return 200 -- the JavaScript has
to actually run.  This loads the page, fails on any console error or uncaught
exception, exercises the three tabs and the seam editor, and writes PNGs to
look at.

    python tools/ui_smoke.py --url http://127.0.0.1:8765 --out shots/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--out", type=Path, default=Path("ui-shots"))
    parser.add_argument("--run-id", default=None, help="open this run before shooting")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    # The page reports sizes in inches with a ″ that a cp1252 console cannot encode.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args.out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})
        page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}")
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        # Not networkidle: the page polls /api/state, so the network never idles.
        page.goto(args.url, wait_until="load")
        page.wait_for_timeout(3500)

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
            page.wait_for_timeout(4000)

        shots = []
        for tab, name in (("3d", "3d"), ("flat", "flat"), ("sheets", "sheets")):
            page.click(f'#tabs button[data-tab="{tab}"]')
            page.wait_for_timeout(2500)
            path = args.out / f"{name}.png"
            page.screenshot(path=str(path))
            shots.append(path)
            print(f"  shot {path}")

        # exercise the seam tool: switch to flat, arm it, drag a line
        page.click('#tabs button[data-tab="flat"]')
        page.wait_for_timeout(600)
        page.click("#btn-seam")
        box = page.locator("#flat").bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] * 0.42, box["y"] + box["height"] * 0.25)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.42, box["y"] + box["height"] * 0.75, steps=12)
            page.mouse.up()
            page.wait_for_timeout(4000)
        path = args.out / "flat-seam.png"
        page.screenshot(path=str(path))
        shots.append(path)
        print(f"  shot {path}")

        seam_count = page.evaluate("document.querySelectorAll('.seam').length")
        seam_rows = page.evaluate("document.querySelectorAll('.seam-row').length")
        print(f"\nseams drawn: {seam_count}, seam rows listed: {seam_rows}")

        page.click('#tabs button[data-tab="sheets"]')
        page.wait_for_timeout(3000)
        path = args.out / "sheets-after-seam.png"
        page.screenshot(path=str(path))
        shots.append(path)
        print(f"  shot {path}")
        pieces = page.evaluate("document.querySelectorAll('.sheet-piece').length")
        head = page.evaluate("document.getElementById('sheets-head').textContent")
        print(f"sheet pieces drawn: {pieces}")
        print(f"sheets header: {head}")

        browser.close()

    if problems:
        print(f"\n{len(problems)} console problem(s):")
        for item in dict.fromkeys(problems):
            print(f"  {item[:200]}")
    else:
        print("\nno console errors or warnings")
    return 1 if any(p.startswith("pageerror") or "console.error" in p for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
