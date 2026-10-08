# AutoDeck2 — the engine behind AutoDeck 5.4

Scan in → a Rhino file with the **raw** detected deck outline to draw on →
your hand-drawn lines/arcs back in → validated `final.dxf` for VCarve plus
calibration measurements.

Phase 1 has no automatic fitter: you draw the primitives, AutoDeck2 checks and
measures them. Phase 2 (`autofit`) is the fitter trained on that first Key
West drawing — it draws the way you did: corners first, lines where the wall
is straight, then the biggest tangent arcs that fit, no more than three arcs
between two lines.

AutoDeck2 reuses the proven scan → segmentation → surface-development engine
of AutoDeck v1 (`../engine-v1`) as an installed dependency
through one adapter (`autodeck2/v1compat.py`). It never copies or edits v1.

## Run it

This engine has no launcher of its own any more. Use the AutoDeck UI
(`AutoDeck.bat` / `AutoDeck.command` one level up), which creates the shared
`.venv` on its first run, or start the wizard from that same environment:
`.venv\Scripts\python -m autodeck2`. Then:

```
[1] Make a raw outline from a scan
[2] Auto-fit the CAM outline (lines first, then tangent arcs) -> final_auto.dxf
[3] Ingest my drawn outline.3dm -> final.dxf + calibration
[4] Check the environment
```

Command line: `python -m autodeck2 outline --input scan.obj [--layout nest|boat-plan] [--pattern teak|diamond|hex|none]`,
`python -m autodeck2 autofit --run outputs/runs/<id>`,
`python -m autodeck2 ingest --run outputs/runs/<id> --drawing <file.3dm>`,
`python -m autodeck2 doctor`, `python -m autodeck2 cache status|clean`.

## Auto-fit (Phase 2): `outputs/runs/<id>/final_auto.dxf`

`autofit` reads the run's developed panels (from the cache, ~25 s for the
Key West boat) and fits every outer loop and obstacle the way the first hand
drawing was made:

1. **Corners first** — a turn that is already complete within ±10 mm and
   whose flanks meet at a point the contour reaches (notch ends, feet, jogs,
   cleat necks). Fillets, rounds and kinks inside a round are not corners.
2. **Lines** wherever a straight run fits the band (3.5 mm at the 95th
   percentile, isolated spikes tolerated to 6.5 mm) and is genuinely straight,
   not a chord of a shallow curve.
3. **Big arcs** — a gently curved side (the 8 m-radius bow sides) is one arc,
   refitted tangent to its neighbouring line or through the corner it ends on.
4. **Tangent bridges** between the lines and arcs: one fillet if it fits, else
   two, else three arcs (exact G1, 0.10°). Line ends are trimmed or extended
   to the tangent points, like a Rhino tangent-tangent arc.
5. **Safe side (outer loops)** — the fit is centred `edge_bias_mm` (1.5)
   INSIDE the detected border and every line/arc/bridge may cut up to
   `inward_extra_mm` (3.0) deeper into the panel than it may stick out toward
   the wall. A wall pocket is skipped with one straight line; a wall bulge is
   never cut through; a panel slightly too small fits the boat, slightly too
   big does not. Obstacle cut-outs are fitted symmetrically (their signed
   deviations vs RAW are in the report). A biased fit that would
   self-intersect falls back to the symmetric fit automatically.

Outputs: `auto_cam.3dm` (your `outline.3dm` plus unlocked
`AUTO_CAM::PANEL_n` and `AUTO_CORNERS` layers), `final_auto.dxf` for every
panel that validates (closed LWPOLYLINEs with bulges, mm — plus, when the run
has a pattern, its groove LINEs clipped to the fitted loops on
`PATTERN_<KIND>__PANEL_n` layers: CAM layers = profile cut, pattern layers =
groove pass; `final.dxf` from ingest gets the same), `autofit_report.md`
+ `autofit.json`, and `autofit_panel<n>.png` previews. Status `VALID`,
`VALID_WITH_FLAGS` (DXF written; the report lists spots that sit outside the
band or were joined with a small kink declared as a corner — look at them in
Rhino), `PARTIAL` (a panel failed validation and is left out) or
`NEEDS_REVIEW`. To correct a spot: move/redraw on `AUTO_CAM::PANEL_n` (or
copy to `USER_CAM::PANEL_n`) and run **ingest** on that file — the same strict
checks then produce `final.dxf`.

Key West result (2026-08-31, safe-side fitter): main deck 35 primitives
(16 lines, 19 arcs, 10 corners) against 31 in the hand drawing (16L/15A/9C),
bow sides as single multi-metre arcs, ALL five panels valid — status
VALID_WITH_FLAGS, 36 flags total (was 49). Tuning lives in
`config/default.yaml` → `autofit` (band, corner angle, minimum line length,
arcs per connection, `edge_bias_mm`, `inward_extra_mm`).

## What you get: `outputs/runs/<id>/outline.3dm`

Millimetres. Panels laid out flat and not overlapping (`nest` mode; `boat-plan`
keeps true relative positions). Per panel `n` (1 = main deck):

| Layer | Content | Locked |
|---|---|---|
| `RAW::PANEL_n::OUTER` | the detected floor-to-wall boundary, dense and untouched — this is the wall line | yes |
| `RAW::PANEL_n::OBSTACLES` | raw obstacle / void contours on that panel | yes |
| `RAW::PANEL_n::FEATURES` | seams / hatches / nonskid edges (confident) | yes |
| `RAW::PANEL_n::HINTS` | low-confidence seam / nonskid / obstacle candidates (dimmed) | yes |
| `REF::PANEL_n::ROBUST` | the de-noised reference curve, grey guide | yes |
| `TEAK::PANEL_n` | teak lines, 63.5 mm on centre, one global boat frame (with `--pattern diamond` / `hex` this layer is `PATTERN_DIAMOND::PANEL_n` / `PATTERN_HEX::PANEL_n`: diamond stitch 152.4 × 76.2 mm, hexagons 152.4 mm across flats) | yes |
| `USER_CAM::PANEL_n` | **empty — draw here** | no |
| `USER_CORNERS` | **empty — drop a Point where a sharp corner is intended** | no |
| `HINTS::UNASSIGNED` | features whose panel could not be decided (world position) | yes |
| `LABELS` | panel id / role / boat position | yes |

Also `outline.dxf`, `panels.json`, `run.json`, `outline_report.md`.

## Drawing rules (Rhino)

- Use **Line** and **Arc** tools only, on `USER_CAM::PANEL_n`. Any order, any
  direction; AutoDeck2 chains them itself (endpoints snapped within 0.5 mm).
- One closed outer loop per panel; obstacle cut-outs as separate closed loops
  inside it.
- Tangent joins are checked to 0.10°. A sharp corner is fine — mark it with a
  Point on `USER_CORNERS` and it is reported as intentional.
- Branches, duplicated segments and real gaps are rejected with the location.

## Ingest output

`final.dxf` (closed LWPOLYLINEs with true arc bulges, mm, one layer per
panel) only when everything validates strictly (0.5 mm snap, closed loops,
tangent joins); always `calibration.json`, `calibration_report.md`,
`final_report.md`. A **loose analysis pass** (5 mm snap, open chains allowed,
each chain matched to its nearest RAW boundary) runs regardless, so an
unsnapped or unfinished drawing still yields calibration measurements —
primitive counts and lengths, arc radii, join classes (marked corner /
tangent / near-tangent / unmarked corner), signed deviation. Deviation is
**signed**: positive = into usable deck area, negative = beyond the detected
wall (or into an obstacle). Manufacturing approval stays TEST_ONLY.

Findings from real drawings live in `docs/calibration/` — start with
`KEYWEST_1_FINDINGS.md`, which states the rules the Phase 2 fitter must follow.

## Layout

```
autodeck2/    program        config/   default.yaml (+ git-ignored local.yaml)
inputs/boats/ your scans     outputs/runs/<id>/   results
cache/        disposable     tests/    pytest
```

Cache entries are keyed by scan SHA-256 + config hash + v1 version + v1
source fingerprint + schema version; any mismatch is a miss.

## Tests

```
source .venv/bin/activate
export PYTHONPATH="$PWD/../engine-v1/src:$PWD"
pytest                       # fast suite
AUTODECK2_REAL_SCAN=1 pytest tests/test_real_scan.py   # real Key West run (minutes)
```
