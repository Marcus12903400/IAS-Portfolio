# AutoDeck V0.3 manufacturing-geometry architecture

Date: 2026-08-23

This document freezes the architecture before V0.3 implementation. AutoDeck remains a proposal-and-verification system. Every flat/CAM artifact is **TEST / VERIFICATION GEOMETRY**, not CNC-ready, and V0.3 applies no manufacturing offset.

## Baseline audit

The current Key West files are `output/click-run-20900` (orientation) and `output/click-run-6193` (advanced structural). Both use `21kwcockpit.obj` with SHA-256 `b655dcec…7221`.

- Orientation Stage A retains a 4,849,931.765 mm² connected primary.
- Conformability Stage B retains 4,725,686.283 mm².
- Structural Stage C creates 255,847 components and reduces the largest to 1,361,380.670 mm²; advanced returns 38 candidates. Structural crossing is therefore experimental evidence, never the default production candidate.
- The orientation primary has 1,486 V0.2 corner anchors. The exact cause is a permissive 2-of-4 vote over 5/10/20/40 mm turning measured directly on the stair-stepped resampled contour, followed by only local suppression. Neighboring staircase responses survive as independent anchors.
- The current DXF writer is hand-generated ASCII. It initializes ARC counts but implements no span-level arc fitting and no spline fitting. It only accepts a whole closed curve as one circle or a whole open curve as one line; all real compound curves necessarily fall back to one LWPOLYLINE each.
- Key West high-confidence seam count is already zero. Percentile-only nonskid produces 20 loose/medium/strict regions despite the known weak grit surface, demonstrating the need for abstention.

## Frozen segmentation and feature policy

`orientation` remains the default and sole source of production candidates. `advanced` remains a backward-compatible alias but prints a fragmentation warning and is documented as `structural-experimental`. Structural response remains available for wall/obstacle/corner confidence and debug.

Nonskid and seams are optional. Detection requires relative rank plus absolute/effect-size evidence and reports `DETECTED`, `WEAK_SIGNAL`, `NO_DETECTABLE_SIGNAL`, or `NOT_EVALUATED`. An abstention cannot invalidate hard deck/obstacle output. Calibration masks remain available even when normal-visible feature output abstains.

## Immutable evidence and lineage

The transformations are separate objects:

```text
raw_3d_id
  -> conditioned_3d_id
  -> development_patch_id + triangle_id + barycentric_coordinates
  -> flat_raw_id
  -> cam_fit_id
  -> native/polyline DXF entity IDs
  -> backprojected_3d_id
```

`RAW_3D` is never modified. V0.3 keeps `CONDITIONED_3D`, adds `CAM_BACKPROJECTED_3D`, and keeps calibration/debug layers.

## Development surface and patches

The accepted deck raster is converted to one physical development patch per connected primary/secondary surface. A configurable, coarser physical grid constructs a dedicated triangle mesh; only cells fully supported by accepted deck remain, so holes and obstacle voids are preserved. Each mesh records degenerate removal, connected components, boundaries, nonmanifold edges, area change, and topology changes. No hole is silently filled.

The development base Z is a robust physical-scale low-pass of accepted floor height. It preserves slope, crown, and gradual curvature while suppressing sub-millimeter scan/microtexture. Displacement, curvature proxy, and area changes are measured against the accepted surface.

Every source curve sample is associated with a development triangle and barycentric coordinates. Both forward mapping and CAM back-projection use that association; arbitrary independent XYZ projection is forbidden.

## Development strategies

`SurfaceDevelopmentStrategy` has two implementations:

1. `RigidPlanarDevelopment` fits a local plane per patch and rigidly rotates the patch into U/V. Selection is based on residual planarity, never world angle. A planar rectangle tilted 7° must recover 1000 × 500 mm within numerical tolerance.
2. `IntrinsicMeshDevelopment` uses pinned `libigl==2.6.1` LSCM initialization followed by 2-D ARAP refinement for nonplanar disk-like manifold patches. Version 2.6.1 is the latest stable CPython 3.12 Windows wheel available in the target environment; stable 2.6.2 has no compatible Windows wheel. The two retained ARAP pins use libigl exact-geodesic distance, avoiding the chord-length scale error on developable cylinders. `libigl` is the selected established geometry dependency because its official NumPy API exposes LSCM, boundary loops, exact geodesics, and ARAP; V0.3 does not implement a custom parameterization solver. Unsupported topology or an unavailable binding produces an explicit invalid development, not a global-plane disguise.

Development metrics include robust edge strain after a configured minimum source-edge length, P50/P95/P99/maximum absolute strain, signed mean/RMS strain, triangle area strain, angle distortion, flip count, boundary-length change, and local scale/orientation. A flip is invalid. Compound curvature may legitimately require `NEEDS_REVIEW` and a later panel split.

## Flat-raw mapping and forbidden side

Conditioned 3-D geometry is conservative: it fixes wall/floor Z hopping and keeps X/Y close to detection. Final manufacturing fitting occurs after development.

Each flat raw closed curve defines a one-sided corridor. Outer deck fits must remain on/in the accepted polygon side; obstacle fits must not shrink through the detected obstacle interior. Candidate lines/arcs/splines are densely sampled for nearest-reference distance, polygon-side violations, self-intersection, closure, and topology. Any violation forces fallback and review.

## Corner system and fitting

A coarse diagnostic copy—not manufacturing geometry—is used for corners. Deck profiles use 10/20/40/80 mm turning, require 3-of-4 persistence, cluster responses physically, and enforce feature-specific separation. Small obstacles use 3/6/12/24 mm and tighter clustering. Anchor density is reported per metre and excessive density marks `OVER_ANCHORED_CURVE`.

Between hard anchors, model selection tries the simplest valid form:

1. LINE;
2. circular ARC/CIRCLE;
3. bounded low-control-point cubic B-spline using `ezdxf.math.fit_points_to_cad_cv`, with the exact control frame reused for native DXF export;
4. bounded-error simplified polyline;
5. raw developed reference as `NEEDS_REVIEW`.

Hard corners require G0 only. Smooth joins prefer G1. Every span reports error and corridor evidence. Motion proxies count short spans, tangent joins, direction discontinuities, and primitive reduction; they are not acceleration predictions.

## DXF decision

The handwritten writer is replaced by pinned `ezdxf==1.4.4`. `flattened_curves_native.dxf` uses LINE/ARC/CIRCLE/SPLINE/polyline fallback. `flattened_curves_polyline.dxf` tessellates the same CAM geometry using chord error plus minimum-span control. Both are R2010+, millimetres, omit empty feature layers, and are reopened with `ezdxf.readfile()`, audited, reconstructed, and geometrically compared with source chains. Failure makes the output invalid.

The selected dependency versions are documented in `pyproject.toml`; the Windows launcher installs/loads local `vendor_packages` like existing Rhino support.

## Output and status contract

V0.3 adds `development_preview.3dm`, native and compatibility DXFs, `development_report.md`, `curve_fit_report.md`, compact mesh/mapping NPZ artifacts, and `debug/development`, `debug/curve_fit`, and `debug/structural`. The flat preview contains `FLAT_RAW`, `FLAT_CAM_FIT`, protected corners, and fit deviation. Verification contains raw, conditioned, and CAM-backprojected 3-D curves.

Segmentation, feature evidence, development, manufacturing fit, DXF audit, and human panel status remain independent. Files existing is never success.

## Incremental validation order

Implementation follows the specification's phases: freeze segmentation/abstention; development mesh/base; exact local planar development; libigl intrinsic development; barycentric curve mapping; coarse clustered corners; forbidden-side fields; model fitting; CAM back-projection; ezdxf rewrite/audit; reports; synthetic suite; then one real Key West orientation run and a diagnostic structural A/B comparison.
