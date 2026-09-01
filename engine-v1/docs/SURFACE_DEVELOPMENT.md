# Surface Development

## Development is not top-view projection

Flattening a deck panel means **developing (unrolling) the surface** — not
dropping Z and calling it flat. A 1000 mm edge on a panel tilted 7° from
horizontal projects to about 992.5 mm in a naive top-view drop, which is the
wrong length for physical foam. A correctly developed panel preserves its
true ~1000 mm surface length. This distinction is why `development.py`
exists as a separate, deliberate stage rather than a coordinate drop.

## Two development strategies

`development.py` implements both:

- **`RigidPlanarDevelopment`** — for sufficiently planar patches. Fits a
  best-fit 3-D plane and projects onto the plane's own in-plane (u, v) axes.
  Because points already lie in the fitted plane, in-plane distance equals
  true 3-D edge length by construction — not an approximation.
- **`IntrinsicMeshDevelopment`** — for non-planar patches, using `igl.lscm`
  (Least Squares Conformal Maps) followed by ARAP (As-Rigid-As-Possible)
  refinement (`igl.arap_precomputation` / `igl.arap_solve`).

`flattening.py` additionally has a `PlanarFlattening` path (also a
best-fit-plane projection, used earlier in the pipeline for the raw
boundary) and a `MeshSurfaceFlattening` stub that intentionally raises —
there is no untested/half-working LSCM path silently substituting for the
real one; the working intrinsic-development implementation lives only in
`development.py`.

## Distortion is measured, never assumed

`development_distortion()` compares 3-D source edge length against
developed (u, v) edge length across the mesh, plus area strain, angle
change, and triangle-flip count. `_development_status()` turns the p95
strain into `GOOD` / `NEEDS_REVIEW` / `INVALID`.

**Never claim zero distortion on compound-curved geometry unless the
measurement actually supports it.** A shallow, nearly-developable patch
(e.g. a gentle cylindrical section) should report negligible strain; a
genuinely compound-curved patch should report — and keep reporting — real,
nonzero, unavoidable distortion. If a future change makes compound curvature
report near-zero distortion, that is very likely a sign the measurement
broke, not that the geometry got easier.

## Patterns live in developed space

Pattern dimensions (teak/diamond/hex — see `docs/PATTERNS.md`) are always
measured on the flat *developed* foam, never computed in world/top-view XY
and mapped afterward. A 6-inch pattern dimension stays exactly 6 inches on
the flat panel even when the source boat surface was tilted.
