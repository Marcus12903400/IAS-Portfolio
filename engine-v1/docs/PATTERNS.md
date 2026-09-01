# Decking Patterns

Selected by number only — no manual angle entry:

```
PATTERN:
[1] None
[2] Teak Lines
[3] Diamond
[4] Hexagon
```

All three implemented patterns live in `src/autodeck/patterns.py`, with
their literal dimensions pulled from `config/default.yaml`'s `pattern`
section (not hardcoded in `patterns.py`):

| Pattern | Dimension | Value |
|---|---|---|
| Teak | on-center spacing | `teak_spacing_mm`: 63.500 mm (2.500 in) |
| Diamond | long diagonal (follows boat length) | `diamond_long_diagonal_mm`: 152.400 mm (6.000 in) |
| Diamond | short diagonal (transverse) | `diamond_short_diagonal_mm`: 76.200 mm (3.000 in) |
| Hexagon | across flats | `hex_across_flats_mm`: 152.400 mm (6.000 in) |

Hexagon side length is derived mathematically (`side = across_flats /
sqrt(3)`), not an independently rounded constant.

## Boat axis detection

`detect_boat_frame()` (`patterns.py`) automatically detects the boat's
longitudinal axis, independent of user-supplied orientation:

1. PCA on the primary outer perimeter's point covariance gives an initial
   longitudinal axis.
2. Cross-width slicing (`_slice_widths`) with a robust least-squares fit
   refines the axis and origin.
3. Bow/stern sign is inferred separately, by comparing median width at each
   end.

Teak/diamond/hex are all invariant under a 180° axis reversal, so only the
longitudinal-axis confidence matters for pattern correctness — bow/stern
sign confidence is tracked separately and matters only for display/labeling.

This axis detection is currently local to `patterns.py` (not shared with
segmentation/orientation, which detect a different axis — the mesh's
vertical up direction).

## Everything is measured in developed space

Pattern dimensions are computed and applied in the flat *developed* surface
space (`generate_pattern`'s output is explicitly tagged
`"coordinate_space": "developed physical millimeters"`), never in world/
top-view XY and mapped afterward. See `docs/SURFACE_DEVELOPMENT.md`.

## One global pattern lattice

A single `BoatFrame` (origin + axis) is computed once from the primary outer
perimeter and reused for every panel/domain in the run — patterns are not
independently restarted per panel. `generate_pattern`'s `development`
parameter is reserved (currently unused) for per-patch affine phase transfer
in a future intrinsic multi-panel run; today's rigid-planar-dominant use
case doesn't need it.

## Clipping

Pattern domain is the final valid CAM outer boundary minus final valid CAM
obstacle holes — patterns never cross a detected obstacle (console, T-top
feet, leaning post, etc.), and pattern generation never treats an invalid
CAM profile as if it were valid.

## Outputs

When a pattern is selected, in addition to the geometry-only DXFs:

- `pattern_only.dxf`
- `flattened_with_pattern.dxf`
- `pattern_report.md`

on their own DXF layers, distinct from the perimeter layers.
