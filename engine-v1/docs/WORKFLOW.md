# Workflow: Scan to VCarve

1. **Scan the boat** with the Einstar Vega (or equivalent).
2. **Open the scan in Rhino** and clean it up as needed.
3. **Orient the primary deck plane roughly to the X-Y plane**, with **+Z up**.
   AutoDeck sanity-checks this and warns if it looks wrong, but does not
   redesign around arbitrary scan orientation — this one-time Rhino step is
   deliberate and simple.
4. **Preserve wall-floor transition geometry** — don't simplify away the
   edges AutoDeck needs to detect deckable surfaces and obstacles.
5. **Export OBJ in millimeters.** The scanner's native unit is mm, and
   AutoDeck's internal geometry is consistently mm end to end.
6. **Ensure Rhino's Z → OBJ Y remapping is OFF** for the normal workflow —
   AutoDeck expects the OBJ's up axis to already be +Z, matching step 3.
7. **Put the scan under `inputs/boats/<name>/scan.obj`**, or just point the
   wizard/CLI at wherever it is — a path outside `inputs/` works too.
8. **Launch AutoDeck**: run `autodeck` (or `python -m autodeck`) with no
   arguments.
9. **Answer the numbered prompts**: select the scan, confirm units
   (normally mm), confirm up axis (normally +Z), pick a segmentation mode
   (normally "orientation" — see `docs/SEGMENTATION.md`), and select a
   pattern:
   ```
   PATTERN:
   [1] None
   [2] Teak Lines
   [3] Diamond
   [4] Hexagon
   ```
   Selection is by number only — no manual pattern-angle entry (the boat's
   longitudinal axis is detected automatically, see `docs/PATTERNS.md`).
10. **Wait.** AutoDeck prints a confirmation summary (input, units, up axis,
    segmentation, pattern, cache status, output path) before starting the
    expensive work, so a mistake is caught in a second, not forty minutes in.
11. **Open `verification.3dm`** in Rhino. Compare the RAW detected geometry
    against the CAM (reconstructed LINE/ARC) geometry — the raw layer is
    never removed, so you can always see whether AutoDeck actually followed
    the physical boat (see `docs/CAM_GEOMETRY.md`).
12. **Open the flattened DXF** (`flattened_curves_polyarc.dxf`, the
    preferred perimeter file; `flattened_with_pattern.dxf` if a pattern was
    selected) in VCarve.
13. **Do not cut until physically verified.** AutoDeck's manufacturing
    approval is always `TEST_ONLY` — mathematically valid geometry is not
    the same claim as "safe to cut." A human must verify a real test cut
    before treating any output as production-ready.

## Advanced / scripted use

The wizard and the `autodeck analyze` subcommand call the same
`analyze_scan()` pipeline function — the subcommand form takes explicit
flags instead of interactive prompts, for scripting or CI:

```bash
autodeck analyze --input inputs/boats/keywest/scan.obj \
  --output outputs/runs/keywest-manual --units mm --pattern teak
```

`autodeck reprocess-manufacturing` and `autodeck pattern` re-run only the
CAM-fitting/pattern stages from a previous run's saved artifacts, without
re-scanning or re-developing the mesh. Both re-decide `final.dxf` at the run
root exactly as a fresh run does (point `--output` at either the run root or
its `processing_and_debug/` directory). Run `autodeck --help` for the full
flag reference.

## What `final.dxf` means

`final.dxf` is written only when the primary perimeter passes every hard
check (closure, ≤0.10° tangent at smooth joins, 3 mm corridor, signed
safety, no self-intersection) and its own `ezdxf` round-trip. It is a copy
of `processing_and_debug/flattened_curves_polyarc.dxf`: one closed
LWPOLYLINE (lines + true arc bulges) per contour, in millimetres, including
obstacle cut-outs. If it is absent, `FINAL_NOT_READY.txt` names the reason.
An obstacle cut-out can still carry a `SHARP_CORNER_REQUIRES_REVIEW` kink
while the primary passes — check `processing_and_debug/polyarc_report.md`
before cutting. Manufacturing approval remains `TEST_ONLY` regardless.
