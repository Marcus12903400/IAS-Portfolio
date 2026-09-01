# Synthetic validation

Validation date: 2026-08-22

The suite uses deterministic meshes in millimeters. Passing a synthetic test proves the implementation behaves as stated on that controlled geometry; it does not prove a real Vega scan has adequate resolution or cleanliness.

| Case | Expected invariant | Result |
|---|---|---|
| No seed | automatic candidates are produced without coordinates | Pass |
| Slope definition | +Z normal is 0°; a 30° normal is 30° from horizontal | Pass |
| Inconsistent/downward winding | connected faces are repaired and oriented toward +Z | Pass |
| Automatic flat floor | largest upward connected surface becomes primary | Pass |
| 15°, 20°, and 24° surfaces | remain orientation-eligible | Pass |
| 30° surface | rejected by the V0.1 orientation gate | Pass |
| ~23° noisy surface | isolated raw >25° triangles are recovered without holes | Pass |
| Automatic wall transition | floor candidate does not climb near-vertical wall | Pass |
| Automatic hatch seam | structural cost splits horizontal regions and creates an internal loop | Pass |
| Orientation baseline seam | parent region remains connected while seam is retained as internal refinement geometry | Pass |
| 2.5 mm analysis representation | dense mesh is reduced by >75% while resolvable hatch relief remains above the structural threshold | Pass |
| Multi-scale neighborhoods | curved surface produces distinct increasing results at 5/15/40/100 mm | Pass |
| Stage diagnostics | exact Stage A/B/C component fields are emitted to JSON and Markdown | Pass |
| Zero candidate with 160,000 mm² eligible | explicit segmentation failure and orientation-only diagnostic OBJ | Pass |
| Non-empty success contract | candidate, primary, area, OBJ geometry, and 3DM object counts are all required | Pass |
| Flat plane | seed reaches complete connected surface | Pass |
| Sloped plane | orientation alone does not split region | Pass |
| Seed relocation | selected faces and outline are unchanged | Pass |
| 300 mm-radius bend | gradual single-axis curvature continues | Pass |
| 20 mm-radius bend | concentrated rotation stops growth | Pass |
| 90-degree wall | floor does not leak onto wall | Pass |
| 5 mm recessed seam | seam creates a high-cost barrier | Pass |
| Raised obstruction | surrounding region remains; internal loop produced | Pass |
| Temporary raised paper | explicit overlay suppression removes false internal outline | Pass |
| Unknown OBJ units | configured fallback is used with a prominent warning | Pass |
| End-to-end artifacts | OBJ/PLY/JSON/NPZ/report produced in original coordinates | Pass |
| Exact raster RLE | component labeling preserves the input pixels without run-end expansion | Pass |
| Clean feature envelopes | an interior raster gap stays numeric and does not create another Rhino contour | Pass |
| V0.2 top-view hypotheses | loose/medium/strict masks differ and floor-projected nonskid stays below obstacle tops | Pass |
| Long shallow seam | a coherent 240 mm relief line produces a >100 mm seam curve | Pass |
| Up-axis auto assessment | a rotated -Y-up plane is identified before expensive processing | Pass |

Command:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Current result: **28/28 tests passed**.

The test suite also verifies that proposed and approved geometry remain separate data fields. The optional native 3DM path is exercised by the end-to-end test, which requires at least one exported curve object rather than accepting file creation alone. The dependency remains an optional project extra; Rhino-compatible 3-D OBJ curves are still emitted when it is unavailable, but such a run does not satisfy the complete V0.1 success contract.

## Preserved end-to-end run

`output/synthetic-ab-orientation/` and `output/synthetic-ab-advanced/` contain the current no-seed A/B artifact sets. See `docs/RERUN_STATUS.md` for the measured comparison. This is generated data, not evidence about a Vega scan.

## Remaining validation limits

- independent labeled ground truth for real nonskid, seams, and obstacles;
- real ArUco/AprilTag detection and multi-material UV mapping;
- marker-paper invariance on a scan containing physically opaque paper;
- curve alignment after importing alongside the original scan in Rhino;
- true min-cut/closed-contour optimization beyond the implemented local boundary-coherence refinement.
