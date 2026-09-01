# AutoDeck V0.3 real Key West cockpit result

Date: 2026-08-23

Input: `21kwcockpit.obj`, 10,267,826 original triangles, SHA-256 `b655dcec8fdc91efdac429c8f9f4072006a5d1d5f8d0811d03073de67b477221`.

All geometry remains **TEST / VERIFICATION GEOMETRY**, not CNC-ready. No manufacturing offset was applied.

## Orientation production run

Output: `output/key-west-v03-orientation`

- Runtime: 621.280 s.
- Candidates: 5.
- Primary: 4,849,931.765 mm², average slope 2.653°, P95 slope 8.679°.
- Stage A: 1,761 components; largest 4,849,931.765 mm².
- Stage B: 5,074 components; largest 4,725,686.283 mm².
- Stage C diagnostic: 255,847 components; largest 1,361,380.670 mm².
- Primary development: `intrinsic-lscm-arap`, 194,553 vertices / 384,710 triangles, 21 preserved holes, zero open chains, zero flipped/collapsed triangles.
- Primary absolute edge strain: P50 0.0666%, P95 0.6148%, P99 1.0234%. The 64.842% isolated maximum is reported rather than hidden; robust percentiles and zero flips govern the current V0.3 status.
- Primary CAM fit: `NEEDS_REVIEW`, 9 protected corners (0.799/m), 9 polyline fallback spans, 0.3394 mm maximum fit deviation, zero forbidden-side violations, no self-intersection, and −0.0219% length change.
- Numerical cleanup removed 598 consecutive sub-0.10 mm duplicate/backtracking samples before fitting. This is recorded separately and does not move the retained samples.
- Nonskid: `NO_DETECTABLE_SIGNAL`; zero normal-visible nonskid regions. Calibration masks remain.
- Seams: `NO_DETECTABLE_SIGNAL`; zero high-confidence/hatch curves. Four low-confidence calibration traces remain off by default.
- Native DXF: 74 entities (14 LINE, 60 LWPOLYLINE), millimetres, zero auditor errors, 0.0125 mm maximum round-trip discrepancy.
- Polyline DXF: 74 LWPOLYLINE entities, millimetres, zero auditor errors, 0.0 mm measured round-trip discrepancy.
- `verification.3dm`: 149 objects after cache reprocessing, including 10 `CAM_BACKPROJECTED_3D` curves.
- `flattened_preview.3dm`: 10 `FLAT_RAW`, 10 `FLAT_CAM_FIT`, 72 protected-corner points, and 74 hidden fit-span debug curves.
- Final V0.3 contract: `successful_v03_run = true`; overall review status remains `NEEDS_REVIEW`.

The old 1,486 primary anchors are not reproduced. The physical coarse-copy detector retains nine primary anchors.

## Structural-experimental A/B diagnostic

Output: `output/key-west-v03-structural-experimental`

- Runtime: 617.720 s.
- Candidates / development patches: 38 / 38.
- Primary: only 1,361,380.670 mm².
- Stage A and B are numerically identical to orientation mode.
- Stage C fragments the eligible floor into 255,847 components.
- Primary development: `INVALID` because its dedicated mesh has an open boundary chain.
- Primary CAM fit: `NOT_EVALUATED`.
- DXF audit: `GOOD` only for secondary/debug curves; 454 native entities after the final cached manufacturing pass. This does not rescue the missing primary.
- Final V0.3 contract: `successful_v03_run = false`; overall `FEATURE_EXTRACTION_FAILURE`.

## Decision

Orientation remains the production segmentation baseline. Structural response remains refinement/debug evidence. It cannot be used as a hard connectivity barrier for this cockpit because it destroys the large physical floor region and prevents a valid primary development/CAM chain.
