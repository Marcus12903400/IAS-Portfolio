# AutoDeck V0.2 curve conditioning

The detector's `RAW_3D` curves are immutable. Conditioning creates new curve objects and records explicit `raw_curve_id → smoothed_curve_id → flattened_curve_id` lineage. A failed stage retains the last valid geometry and records a warning; it never overwrites raw evidence.

## Explicit stages

1. Validate finite coordinates, closure, winding, micro-segments, and self-intersection.
2. Parameterize the original 3-D traversal by physical arc length.
3. Uniformly resample at the configured spacing (2.5 mm default; 1, 2, 2.5, and 5 mm documented).
4. Measure turning at 5, 10, 20, and 40 mm; retain corners persistent at multiple scales and suppress clusters of nearby zigzag responses.
5. Smooth X/Y separately with a bounded physical-window Gaussian filter. Persistent anchors are fixed and filtering is piecewise between them.
6. Determine the accepted-floor side at 3, 5, 8, 10, and 15 mm offsets from the local tangent.
7. Fit a distance-weighted, two-pass Huber-reweighted local plane to accepted deck raster samples only. Evaluate that fit at the actual boundary X/Y.
8. Filter the reconstructed Z by physical distance, piecewise at corner anchors. This retains crown, drainage, and gradual rise while removing scan-boundary chatter.
9. Validate topology again, then apply bounded RDP simplification while preserving anchors. An invalid simplification falls back to the valid pre-simplification curve.

The selected numerical filter is deterministic and dependency-free. Unlike an opaque three-coordinate spline, it keeps X/Y corridor limits independent from Z surface reconstruction and exposes metrics after every important stage.

## Feature profiles

Default maximum X/Y displacement is 1.5 mm for primary/secondary deck envelopes, 1.0 mm for nonskid, 0.75 mm for obstacles, and 0.5 mm for seams. Deck envelopes use stronger Z filtering; obstacles and seams use lighter X/Y filtering and retain stronger corner control. All values live in `config/default.yaml` in physical millimetres.

Per-curve reports include raw/resampled/smooth point counts and lengths; X/Y mean, RMS, P95, and maximum displacement; raw-adjacent, smooth-adjacent, and raw-to-smooth Z distributions; local surface-fit residual; projection success; corner anchors; topology; and `GOOD`, `NEEDS_REVIEW`, or `INVALID` status.

## Rhino comparison

`verification.3dm` stays in the original 3-D scan frame. `AUTODECK::SMOOTH_3D::*` is visible by default, matching `AUTODECK::RAW_3D::*` layers are hidden, and sparse raw-to-smooth connectors are hidden under `AUTODECK::DEBUG::SMOOTHING_DEVIATION`.

Synthetic regressions cover a noisy flat rectangle, a noisy circle, persistent corners, gradual Z change, a floor beside a wall, nested holes/obstacles, handedness, and distortion outliers.
