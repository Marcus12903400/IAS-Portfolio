# AutoDeck V0.3.1 real KeyWest orientation result

Input: `21kwcockpit.obj`  
Mode: orientation baseline, +Z, automatic  
Analysis resolution: 2.5 mm  
Run status: `NEEDS_REVIEW`  
V0.3.1 success contract: passed

The clean full run completed in 638.80 seconds. It retained the established large cockpit region and the nine protected physical corners. After the clean run measured the last hard-safe review bound, only the manufacturing stage was reprocessed from its saved development artifacts with the final 7.00 mm ceiling; segmentation and development were not rerun or changed.

## Broad geometry result

| Metric | Result |
|---|---:|
| Original triangles | 10,145,186 |
| Analysis triangles | 2,009,101 |
| Triangle reduction | 80.1965% |
| Surface-area change | -0.07890% |
| Preprocessing runtime | 30.80 s |
| Orientation-eligible faces | 1,712,876 |
| Orientation-eligible area | 5,193,063.65 mm² |
| Stage A components | 1,761 |
| Largest Stage A / primary area | 4,849,931.77 mm² |
| Candidate regions | 5 |

## Primary manufacturing curve

| Metric | Previous V0.3 | Final V0.3.1 |
|---|---:|---:|
| Protected corners | 9 | 9 |
| Logical physical spans | 9 | 9 |
| Approx. controls/vertices | 946 | 316 |
| LINE / ARC / SPLINE / POLYLINE | 0 / 0 / 0 / 9 | 0 / 0 / 9 / 0 |
| Polyline fallback fraction | 1.0 | 0.0 |
| Maximum / P95 deviation | not reported | 6.7210 / 4.1824 mm |
| Forbidden-side violations | not reported | 0 |
| Self-intersections | not reported | 0 |
| Maximum join gap | not reported | 0.000000 mm |
| Logical path closed | n/a | true |

The primary native DXF contains exactly nine SPLINE entities. Its ezdxf round-trip maximum deviation is 0.01866 mm. The compatibility file contains nine LWPOLYLINE entities and 1,198 primary vertices at a measured 0.07946 mm round-trip deviation; it is not the manufacturing smoothness authority.

The result is deliberately `NEEDS_REVIEW` because it uses the measured absolute corridor above the 0.75 mm target. It is smooth and hard-safe, but no production/CNC approval is implied.

## Perimeter length diagnosis

| Stage | Length (mm) | Transition |
|---|---:|---:|
| Conditioned 3-D | 11,748.758861 | — |
| Mapped source-mesh 3-D | 11,252.611564 | -4.22298% |
| Mapped development-base 3-D | 11,228.211786 | -0.21684% |
| Flat raw developed | 11,274.263992 | +0.41015% |
| CAM fit | 11,012.737270 | -2.31968% |

The previously observed ~4% discrepancy is introduced primarily by associating/snapping the conditioned perimeter to the 5 mm development mesh, before ARAP. Development-base to flat raw slightly increases length (+0.410%), so low local ARAP strain was not hiding the original loss. Development behavior was not changed in this hotfix.
