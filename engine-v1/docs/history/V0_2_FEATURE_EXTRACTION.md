# AutoDeck V0.2 feature-extraction data flow

Date: 2026-08-22

## Evidence from the successful real run

`output/click-run-29966` analyzed `seaproautotestrotated1.obj` with the corrected X-Y deck / +Z-up orientation.

- Stage A primary area: 5,550,273.098 mm².
- Secondary areas: 114,191.644 and 111,507.680 mm².
- Primary outer loops: 1.
- Primary selection-hole loops: 6,896.
- Primary raw structural-edge chains: 247,596.
- Total primary `internal_boundaries`: 254,492.
- Visible verification curves: 532,650.
- OBJ boundary vertices: 2,952,284.
- `segmentation.json`: 125 MB; `verification.3dm`: 232 MB.

The spiderweb is not the primary outer contour. It is caused by `extract_internal_structural_boundaries`: every internal analysis edge with `refined_cost > 0.56` is chained, stored as an `internal_boundary`, and exported twice (true and lifted).

Inside the 5.55 m² primary region, 1,293,999 of 2,709,348 shared edges (47.76%) exceed that threshold. The structural score is heavily saturated: the median score of the strong subset is 0.95. A typical strong edge has only about 2.56° normal change across a 1.47 mm centroid spacing. Fine-scale strength remains zero through the 75th percentile of strong edges. This demonstrates that the present structural-crossing result is dominated by triangle-scale normal-turn sensitivity and coherence saturation, not a selective object/seam classifier.

The spatial concentration of that raw response may still contain useful physical-relief evidence. V0.2 therefore preserves it numerically and rasterizes it; it no longer promotes every strong edge to visible CAD geometry.

## V0.2 data flow

```text
unchanged original mesh
    |
    +-- sampled up-axis plausibility check
    |
    +-- 2.5 mm broad analysis mesh
            normals -> 22/25 degree hysteresis -> Stage A components
            |
            +-- primary outer contour (one normal-view curve)
            +-- secondary outer contours
            +-- primary face mask and significant void evidence
            +-- structural metrics retained as numeric evidence only
    |
    +-- original-resolution faces, cropped in X-Y to primary + margin
            |
            +-- floor-aware top-view height samples
            +-- local base-height estimate and fine relief residual
            +-- normal/ridge/valley/turn evidence
            +-- raised-object height evidence
            |
            +-- 1 mm top-view feature grid
                    +-- raw strong-edge length density at 10/20/40 mm
                    +-- locally normalized residual/density score
                    +-- loose/medium/strict nonskid region envelopes
                    +-- coherent narrow seam response and line traces
                    +-- enclosed persistent void/obstacle hypotheses
    |
    +-- floor-aware XY-to-original-surface projection
            |
            +-- clean normal Rhino layers
            +-- toggleable calibration layers
            +-- hidden debug layers / NPZ / PNG diagnostics
```

## Phase A export contract

Normal view contains only the primary outer loop, meaningful secondary outer loops, and accepted feature contours. Raw structural chains, microscopic holes, invalid fragments, and display duplicates are not normal-visible geometry. Raw arrays remain in NPZ and compact raster diagnostics.

Secondary contours retain analysis-to-original vertex mapping. The primary contour uses the accepted face raster and floor-aware original-resolution Z field so it remains robust at open/non-manifold scan cuts. Both paths use physical RDP simplification and original scan coordinates.

### Phase A real-cockpit result

The preserved `output/real-v02-phase-a-20260822` run retained the exact accepted regions (5,550,273.098, 114,191.644, and 111,507.680 mm²) while reducing the default Rhino output to three deck curves. `segmentation.json` fell from about 125 MB to 95 KB, `verification.3dm` from about 232 MB to 141 KB, and packed candidate masks required about 64 KB. The raw boundary evidence remains in NPZ instead of visible curve objects.

The first full top-view experiment is also preserved as `output/real-v02-features-20260822-r1`. It exposed a geometry handoff defect rather than a bad feature threshold: an accidental 1,198 mm² closed graph loop clipped the 5.55 m² accepted face component. The edge graph contained hundreds of open/non-manifold chains, so its largest available closed loop was not the cockpit perimeter. V0.2 now constructs the primary top-view support directly from the accepted connected face component, checks raster occupancy against accepted physical area, and derives the clean primary perimeter from that raster. A run-length endpoint defect that added one pixel to every component run was corrected and has a dedicated regression test.

## Top-view grid and floor selection

The grid uses a common X/Y bounding box and configurable physical resolution (default 1.0 mm/pixel). The broad primary mask establishes where deck membership is allowed. Original-resolution faces are processed in chunks only inside the primary envelope plus a small margin.

For each pixel, the expected deck Z comes from the accepted primary surface. Original samples nearest that expected Z provide the floor-height field. Higher overlapping geometry is retained separately as raised-object evidence; it is not allowed to become the projection target for nonskid contours.

The fine height residual is measured after a configurable local physical low-pass/base-surface estimate. This removes broad crown/drainage slope while retaining small relief.

## Feature hypotheses

### Nonskid

The raw orange phenomenon becomes edge length per neighborhood area, evaluated at 10, 20, and 40 mm. It is combined with fine height-residual activity and local normalization. Loose, medium, and strict masks use reported distribution quantiles plus graph/image coherence. Morphological cleanup, physical area filtering, hole filling, and contour simplification produce region envelopes—not individual texture ridges.

### Seams

Seam response uses local line-orientation coherence, ridge/valley/normal-turn evidence, narrowness, connected length, and persistence. Long open traces are seams. Closed or nearly closed coherent traces are `HATCH_OR_CLOSED_SEAM`. Repetitive area texture is penalized relative to narrow line evidence.

### Obstacles

Obstacle evidence begins with enclosed holes in the primary top-view mask. Each component records physical area, perimeter, equivalent diameter, dimensions, compactness, cleanup persistence, and raised-height evidence. Loose/medium/strict hypotheses expose calibration choices. Nearby/nested alternatives are grouped; the largest low-interface footprint is the initial primary estimate, with alternatives preserved only in debug output.

The compact per-void record also includes the X-Y bounding box, distance to surrounding accepted floor, local floor minimum/median Z, contained-geometry minimum/median/maximum Z, height-above-floor median/maximum, group membership, and whether the contour was selected as a primary footprint, retained as an alternative, or rejected as a micro-void.

## Persistence and performance

- Normal JSON stores summaries and accepted contours only.
- Dense selected-face indices and raw feature arrays are stored in compressed NPZ.
- PNG diagnostics share a fixed bounding box, pixel resolution, and orientation.
- Timings are reported separately for load, broad preprocessing, orientation, top-view rasterization, nonskid, obstacles, seams, contour generation, and export.
- The original mesh remains read-only.

## Safety limits

V0.2 remains a +Z / X-Y top-view feature prototype. Broad orientation supports explicit/automatic axis selection, but fine-feature extraction warns and is disabled for a non-Z up axis until a plane-local raster frame is implemented. No manufacturing offset is generated.

## Final real validation

The final `real-v02-features-20260822-r6` run exported 35 default-visible curves, including one primary and two substantive secondary deck contours, and passed the V0.2 contract in 677.993 seconds. Obstacles and long seams are promising; nonskid region boundaries remain exploratory and fragmented. Exact results and operator layer guidance are documented in `V0_2_REAL_COCKPIT_RESULT.md`.
