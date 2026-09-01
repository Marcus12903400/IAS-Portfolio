# V0.2 real-cockpit result

Validation date: 2026-08-23  
Input: `seaproautotestrotated1.obj`  
Final conditioning/flattening run: `output/real-v02-conditioning-20260823-r2`

## Outcome

The run satisfies the implemented V0.1 and extended V0.2 export contracts. Its overall panel status remains `NEEDS_REVIEW` because accepted floor geometry touches manually cut/open mesh boundaries and the mesh contains non-manifold evidence. The flattened product is explicitly **TEST / VERIFICATION GEOMETRY — NOT CNC READY**.

Upstream detection remained exactly stable:

- primary accepted surface: 5,550,273.098 mm², 1,827,992 faces, mean slope 3.008°;
- secondary accepted surfaces: 114,191.644 and 111,507.680 mm²;
- original / 2.5 mm analysis triangles: 10,267,350 / 2,238,043;
- raw `proposed_boundaries.obj`, `feature_boundaries.obj`, candidate masks, and top-view NPZ have identical SHA-256 hashes to validated r6.

This proves conditioning did not mutate or re-run detection geometry differently.

## Conditioned 3-D result

Thirty-five useful raw curves produced 35 conditioned curves: 35 `GOOD`, 0 `NEEDS_REVIEW`, and 0 `INVALID`. The set comprises one primary, two secondaries, twenty medium nonskid envelopes, five primary obstacles, and seven high-confidence open seams.

For the primary cockpit perimeter:

- raw → resampled → conditioned points: 2,015 → 8,995 → 1,231;
- raw 3-D → conditioned 3-D length: 22,484.729 → 12,349.533 mm;
- resampled-to-final point reduction: 86.315%;
- X/Y deviation RMS / P95 / maximum: 0.226 / 0.545 / 1.006 mm;
- raw-to-conditioned Z deviation RMS / P95 / maximum: 8.470 / 16.837 / 38.401 mm;
- conditioned-to-reconstructed-surface Z RMS / P95 / maximum: 0.071 / 0.141 / 0.997 mm;
- local floor-fit residual RMS / P95 / maximum: 0.501 / 1.046 / 2.111 mm;
- accepted floor-side projection success: 100%.

The large Z correction is expected evidence from the real scan: the raw raster contour followed noisy height samples near open/wall transitions. The conditioned curve reconstructs Z from accepted floor-side surface fits while moving X/Y by at most about 1 mm. Rhino side view is the important operator check; the change must not be interpreted as automatic approval.

`verification.3dm` contains 35 hidden `RAW_3D` curves, 35 default-visible matching `SMOOTH_3D` curves, 175 hidden deviation connectors, and the existing 88 calibration plus 10 debug curves. Direct 3DM inspection counted 343 objects.

## Planar flattening result

The robust accepted-interior plane is:

- origin: `[93.057732, 1365.783679, -25.444316]` mm;
- U: `[0.999939257, 0.000024511, 0.011021901]`;
- V: `[0.0, 0.999997527, -0.002223814]`;
- normal: `[-0.011021929, 0.002223678, 0.999936784]`;
- rotation from world X-Y: 0.644248°;
- right-handed determinant: 1.0;
- U·world+X / V·world+Y: 0.999939 / 0.999998.

No mirroring was detected. Planarity and neighboring-sample strain were measured rather than assumed:

- surface-to-plane distance RMS / P50 / P95 / maximum: 3.844 / 2.894 / 7.104 / 34.278 mm;
- broad normal variation RMS / P95 / maximum: 5.547° / 10.785° / 79.749°;
- mean / RMS strain: -0.294% / 2.061%;
- P95 / maximum absolute strain: 0.733% / 84.475%.

The very high maximum strain comes from isolated adjacent raster-height discontinuities; it is retained, not clipped out of reporting. Although P95 is below the 1% default, RMS and maximum exceed conservative warning levels. Planar flattening therefore reports `WARN`, and all 35 flat curves are `NEEDS_REVIEW`. TEST output remains enabled by configuration.

The primary conditioned 3-D / flat 2-D lengths are 12,349.533 / 12,315.015 mm, a -0.2795% change.

## Flat artifacts and VCarve test

- `flattened_preview.3dm`: 35 curves, 28 closed and 7 open, all Z exactly 0;
- `flattened_curves.dxf`: 35 `LWPOLYLINE` entities on five feature-class layers, `$INSUNITS=4` (millimetres);
- direct flat-geometry audit: zero exact duplicate curves and zero invalid/self-intersecting exported curves;
- flat X/Y bounding box: `[-1160.995, -1809.751]` to `[1065.826, 2025.174]` mm;
- `scale_check.dxf`: separate 100 mm square;
- calibration, debug, and scale-check geometry are not mixed into `flattened_curves.dxf`;
- no manufacturing offsets were applied.

Polyline output is appropriate for this real geometry under the 0.5 mm primitive-fit tolerance; none of the full real curves qualified as a single line/circle. The synthetic noisy-circle regression confirms primitive fitting when its error criterion is met.

## Rhino layers to compare

First inspect default-visible:

- `AUTODECK::SMOOTH_3D::DECK_PRIMARY_OUTER`
- `AUTODECK::SMOOTH_3D::DECK_SECONDARY`
- `AUTODECK::SMOOTH_3D::OBSTACLES_PRIMARY`
- `AUTODECK::SMOOTH_3D::NONSKID_MEDIUM`
- `AUTODECK::SMOOTH_3D::SEAMS_HIGH_CONFIDENCE`

Then toggle the matching `AUTODECK::RAW_3D::*` layers. Use side views to judge Z chatter removal and top view to confirm broad shape/corners stayed aligned. `AUTODECK::DEBUG::SMOOTHING_DEVIATION` is hidden evidence, not manufacturing geometry.

## Known limitations

- Nonskid regions remain experimental and fragmented; clean conditioning does not validate their semantics.
- No closed hatch candidate survived detection.
- The planar warning is material. Validate known physical dimensions before relying on VCarve scale or dimensional behavior.
- No tested libigl/LSCM/ARAP dependency is installed. Experimental nonplanar mesh flattening is explicitly unavailable; no custom solver or fake pointwise surface flatten was substituted.
- No kerf, gap, border, foam offset, or panel approval is produced.

## Runtime

- total: 752.350 s (12 min 32 s);
- input load/inspection: 58.385 / 124.892 s;
- broad preprocessing/geometry/structural response: 63.952 / 299.673 / 37.313 s;
- fine raster and feature stages: 25.586 s raster, 7.221 s nonskid, 2.950 s seams, 1.263 s obstacles;
- curve conditioning: 47.488 s;
- flattening plus Rhino/DXF export: 2.299 s;
- verification export: 1.587 s.

The source OBJ and validated r6/r1 experiments remain untouched. All 37 automated tests pass.
