# AutoDeck V0.3.1 manufacturing-curve hotfix

V0.3.1 is intentionally limited to manufacturing-curve quality. It does not redesign orientation segmentation, candidate discovery, top-view feature extraction, protected-corner detection, development, or RAW_3D preservation.

## Why V0.3 accepted zero KeyWest splines

The prior spline path first reduced each logical span to interpolation fit points, then rejected the spline when that point count exceeded `maximum_spline_control_points = 48`. Five long KeyWest spans required 112–231 points at that stage. The fitter therefore crossed a hard complexity cliff and emitted dense degree-1 polylines. The other spline attempts were not adequately instrumented, so the output exposed only the fallback result rather than the model-by-model cause.

V0.3.1 replaces that behavior with:

- LINE → endpoint-constrained ARC → cubic B-spline → bounded fallback priority;
- centripetal parameterization and adaptive control growth;
- exact protected span endpoints;
- a second-difference fairness objective;
- hard linearized safe-side inequalities solved as a constrained quadratic objective;
- independent dense polygon, self-intersection, deviation, and join validation;
- selection of the lowest-complexity safe candidate inside the configured corridor;
- complete LINE/ARC/SPLINE/POLYLINE attempt evidence for every logical span.

The analytic LINE/ARC absolute limit remains 1.50 mm. The KeyWest cubic-spline target remains 0.75 mm. A separate 7.00 mm absolute review ceiling was measured from the clean real scan: the first smooth, hard-safe fit at one isolated 5 mm-development-mesh raster spike is 6.97 mm. This is not a production tolerance, and any candidate that uses it is `NEEDS_REVIEW`.

## Native and compatibility geometry

`flattened_curves_native.dxf` contains true LINE/ARC/CIRCLE/SPLINE entities (plus explicit LWPOLYLINE only where a non-primary curve genuinely falls back). The primary KeyWest perimeter contains nine SPLINE entities and no fallback entities.

`flattened_curves_polyline.dxf` is compatibility-only. It uses adaptive 0.10 mm chord-error tessellation and a 250 mm maximum-segment safety cap; the cap no longer forces 1 mm segments.

`flattened_preview.3dm` separates:

- `FLAT_RAW`
- `CAM_POLYLINE_OLD_STYLE`
- `CAM_NATIVE_SMOOTH`
- `PROTECTED_CORNERS`
- `CAM_POLYLINE_FALLBACK_NEEDS_REVIEW`

The smooth layer contains native Rhino line/NURBS objects. Fallback polylines are never mislabeled as smooth geometry.

## Reprocessing reproducibility

Development mappings now preserve float64 barycentric coordinates and exact float64 flat points. Earlier float32 archive rounding could move a borderline hard-constraint decision during `reprocess-manufacturing`. New saved artifacts reproduce the in-run manufacturing reference exactly.

## Verification

The complete suite passes 68 tests. New V0.3.1 coverage includes noisy organic spline approximation, a noisy two-metre LINE, a shallow non-circular SPLINE, exact hard-corner endpoints, forbidden-notch preservation, adaptive compatibility tessellation, native Rhino NURBS objects, distinct subsystem reports, and perimeter-length lineage.
