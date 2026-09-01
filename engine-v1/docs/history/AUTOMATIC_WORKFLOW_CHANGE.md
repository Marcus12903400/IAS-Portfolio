# Automatic cockpit workflow change

> Historical V0.1 implementation note. Real-cockpit validation and the current V0.2 feature workflow are documented in `V0_2_FEATURE_EXTRACTION.md`.

Date: 2026-08-22

## What changed

- `analyze` now defaults to automatic candidate discovery and requires no XYZ seed.
- The input-intent gate measures local slope from Rhino world X-Y by comparing repaired/smoothed normals with world +Z.
- Configurable 22° core and 25° maximum thresholds implement graph hysteresis; the core expands through connected fringe, with a documented all-fringe fallback for a consistently tilted ≤25° mesh.
- Local normal smoothing is prevented from crossing strong structural edges.
- A bounded graph-majority recovery fills isolated threshold-noise faces up to the configured margin without admitting a broad genuine 30° surface.
- The default orientation baseline forms candidates from orientation connectivity and physical area without structural crossing barriers. Strong internal structural edges remain available as `INTERNAL_BOUNDARY` refinement/debug curves.
- The separate advanced mode applies conformability and structural crossing before candidate creation.
- Every run records exact connected-component statistics at orientation-only Stage A, conformability Stage B, and structural-crossing Stage C.
- A requested 2.5 mm physical analysis representation is used for broad classification; the original scan stays untouched and curve points map back to original vertices.
- Empty geometry is a segmentation/export failure, not a successful file-writing run.
- The largest region is labeled `PRIMARY_CANDIDATE`; every candidate records area, face count, slope statistics, bounds, centroid, confidence, loops, and open-cut contact.
- Open mesh-cut boundaries now cause review warnings instead of being treated as physical decking truth.
- `slope_bands.ply` and `orientation_scores.npz` expose the complete orientation decision field.
- Rhino export uses candidate-oriented layers and keeps true and display-lifted curves separate.
- Manual and marker modes remain available via `--seed-mode manual|marker`.
- The double-click launcher no longer requests seed coordinates.

## Configuration defaults

- `orientation.core_slope_deg`: 22° from horizontal
- `orientation.max_slope_deg`: 25° from horizontal
- `segmentation.minimum_candidate_area_mm2`: 10,000 mm²
- `segmentation.mode`: orientation
- `mesh.analysis_resolution_mm`: 2.5 mm
- `mesh.default_input_units`: mm, used only with a prominent warning when OBJ metadata and `--units` are absent

These are V0.1 intent defaults, not permanent marine-deckability constraints. The general curvature, relief, conformability, boundary-cost, and optional seeded systems remain intact for future gunwale/sloped-surface workflows.

## Evidence and limits

The original V0.1 suite passed 23 deterministic tests. The current V0.2 suite and real-cockpit results supersede that initial validation state.
