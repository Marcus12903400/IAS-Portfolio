# Multi-scale normal-neighborhood audit

## Finding

The identical 15 mm, 40 mm, and 100 mm statistics from the first cockpit run were caused by an implementation shortcut with clamping behavior. The previous approximation converted physical radius to adjacency-diffusion iterations, then capped the iteration count at 64. On a sufficiently dense mesh, all three requested radii reached that same cap and therefore executed the same neighborhood calculation.

This was not considered an expected geometric result.

## Correction

`multiscale_normal_variation` now constructs a separate physical spatial grid for every requested radius. Face-centroid normals are averaged within cells of the requested millimeter size using two half-cell-shifted grids to reduce cell-boundary artifacts. There is no shared iteration cap, so a 100 mm request cannot silently become the same computation as 15 mm or 40 mm.

The values remain a deterministic neighborhood-scale diagnostic, not a claim of exact geodesic curvature.

## Regression test

`test_multiscale_normal_neighborhoods_are_physically_distinct` evaluates a synthetic cylindrical strip at 5, 15, 40, and 100 mm. It requires:

- four distinct 95th-percentile normal-variation values; and
- increasing normal variation as physical neighborhood scale increases.

The test fails if two or more requested scales accidentally reuse the same effective neighborhood/result.
