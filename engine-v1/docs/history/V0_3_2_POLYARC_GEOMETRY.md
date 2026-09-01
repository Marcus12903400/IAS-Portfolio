# AutoDeck V0.3.2 polyarc geometry

V0.3.2 is a downstream manufacturing-geometry stage. It does not alter orientation, segmentation, obstacle discovery, conditioning, or development. The V0.3.1 spline is retained as `CAM_SPLINE_V031`; the new `CAM_POLYARC_V032` is independently fitted to `FLAT_RAW_DEVELOPED`.

## Method basis

The implementation follows established biarc/polyarc practice:

- Piegl and Tiller, *Biarc approximation of NURBS curves* (Computer-Aided Design 34, 2002), DOI `10.1016/S0010-4485(01)00160-9`: tolerance-bounded subdivision followed by biarc approximation for NC use.
- Meek and Walton, *Biarc approximation of polygons within asymmetric tolerance bands* (Computer-Aided Design 37, 2005), DOI `10.1016/j.cad.2004.06.001`: tangent-continuous polygon approximation within symmetric/asymmetric bands and with low primitive count.
- Bertolazzi and Frego, *A Note on Robust Biarc Computation*, arXiv `1711.00935`: robust algebraic G1 biarc construction and treatment of degenerate cases.
- Sir et al., *Biarc interpolation for piecewise G1 curves* (2006): G1 Hermite data represented by biarc splines.

AutoDeck constructs the standard one-parameter biarc family. The equal-tangent-distance member is tried first, followed by bounded asymmetric distance ratios when the equal member is a major-arc or safe-side degeneracy. A shared tangent state makes every non-hard join G1. Stable straight/curved transitions are recognized before the general longest-valid-chain search so an exact line–tangent-arc–line input remains three primitives.

## Locked validation

- Absolute bidirectional developed-plane deviation: `3.000 mm`; it cannot be configured higher.
- Smooth-join tangent target / maximum: `0.05° / 0.10°`.
- Signed forbidden-side violations: zero. Outer deck fits may not cross outward; obstacle fits may not shrink into the exclusion.
- Closed-loop gap: numerical zero.
- Self-intersections: zero.
- Final primitive types: `LINE` and true circular `ARC` only.
- Protected hard corners: exact coordinates and intentional G0 joins.

Repeated local fit checks use exact Shapely buffered-region predicates and conservative interval sampling. A completed curve receives a separate full-density, bidirectional deviation audit. The preferred DXF is reopened and its signed bulges are independently reconstructed into geometry; the alternate LINE/ARC and V0.3.1 spline-reference DXFs are also reopened and audited.

## Selection policy

The order is lexicographic:

1. zero forbidden-side, self-intersection, closure, and primitive-type failures;
2. maximum deviation at or below 3.000 mm;
3. protected-corner preservation;
4. primitive count;
5. short- and micro-primitive penalties;
6. RMS deviation and physical span length.

The preferred primitive length is 25 mm, with warnings below 10 mm and explicit micro-primitive evidence below 3 mm. A micro-primitive is never hidden: `polyarc_report.md` identifies its primitive number, logical span, source interval, length, and the hard corridor/safety reason the longest-valid-chain search retained it.

## Export semantics

- `flattened_curves_polyarc.dxf`: one closed LWPOLYLINE with bulges per accepted closed perimeter.
- `flattened_curves_linearc.dxf`: separate native LINE/ARC entities.
- `flattened_curves_spline_reference.dxf`: V0.3.1 reference only.
- `flattened_preview.3dm`: raw, spline-reference, visible polyarc, protected-corner, soft-join, corridor, and maximum-deviation layers.
- `verification.3dm`: smooth 3-D display curves plus hidden dense back-projection samples.

All outputs are test verification geometry. They are not CNC-ready and require manual Rhino/VCarve overlay review.

