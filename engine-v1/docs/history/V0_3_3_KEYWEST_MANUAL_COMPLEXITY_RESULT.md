# AutoDeck V0.3.3 Key West manual-complexity result

> **TEST / VERIFICATION GEOMETRY — NOT CNC-READY.** The current primary proposal is intentionally `INVALID` until connection, tangency, and signed-safety checks pass.

## Result

The cached Key West developed primary perimeter now matches the supplied manual drafting estimate exactly:

| Metric | Previous local fitter | Broad-regime fitter |
|---|---:|---:|
| Lines | 12 | 14 |
| True circular arcs | 444 | 18 |
| Total primitives | 456 | 32 |
| Maximum reference deviation | 2.998 mm | 2.926 mm |
| P95 reference deviation | 2.566 mm | 2.264 mm |
| Median primitive length | 16.445 mm | 219.242 mm |
| Longest primitive | 475.222 mm | 1888.180 mm |
| Self-intersection | no | no |

The low-count geometry was obtained without increasing the 3.000 mm fit tolerance. The method uses the existing 5/10/20/40 mm robust reference as evidence, forms a separate 20/40/80/160 mm broad physical-regime consensus between the nine protected landmarks, and runs a whole-span minimum-primitive LINE/ARC search. The broad consensus moved 0.771 mm RMS and 1.657 mm at P95 from the earlier robust reference; its isolated maximum shift was 3.502 mm. Raw geometry remains unchanged.

## Why the result is not accepted

The 32 pieces are presently independent regime fits. They visually describe the intended deck, but they are not yet one valid machining chain:

- maximum join gap: **5.563 mm**;
- closed-loop seam error: **1.970 mm**;
- maximum proposed smooth-join mismatch: **46.895°**, versus the 0.100° hard maximum;
- signed outer-boundary violations: **8,108 sampled points**;
- one short primitive: **4.537 mm**;
- `successful_v033_run`: **false**.

These failures are retained in `debug_metrics.json`, `polyarc_report.md`, and `v033_manual_reconstruction_report.md`. The program exits the cached manufacturing reprocess as `INVALID`; file creation is not presented as success.

## Required next solver step

Do not reduce the area threshold or loosen the 3 mm corridor. The next reconstruction step is a globally constrained join solve over the established broad regimes:

1. preserve the selected long line and circle supports as the initial model;
2. solve common join points rather than fitting each interval independently;
3. use tangent circular fillets or constrained biarcs only at genuinely smooth transitions;
4. retain persistent notches/corners as explicit G0 landmarks;
5. impose the signed inner safe-set constraint during optimization, not only afterward;
6. reject any solution with a gap, false G1 label, outside-floor sample, or micro-primitive.

The full scan and development do not need to be rerun for that work. The saved development and curve maps are sufficient.

## Verification

- Complete automated suite: **87 passed**.
- Windows click-launcher smoke test: **AutoDeck launcher OK — version 0.3.3**.
- Cached Key West output: `output/key-west-v033-orientation`.
