# Segmentation

## Philosophy

AutoDeck reasons primarily from 3-D geometry: surface orientation,
continuity, walls, floor-to-wall transitions, steps, molded relief,
console/T-top/leaning-post footprints, and other hard geometric evidence.

It deliberately does **not** depend on visual nonskid texture. Some real
scans (grit-style nonskid, faint paint) have no geometrically resolvable
nonskid signal at all. When that's true, the correct output is that there is
no detectable signal — not a hallucinated boundary from whatever happens to
be the highest-percentile response. The same applies to seams: if a boat's
deck genuinely has no geometrically visible seam, finding none is correct,
not a detector failure.

## Orientation mode (production default)

`config/default.yaml`'s `segmentation.mode` defaults to `"orientation"`,
read by `analysis_pipeline.py` with the same `"orientation"` fallback if
unset. The algorithm (`orientation.py::calculate_orientation_field`):

1. An upward-facing face with slope ≤ `orientation.core_slope_deg`
   (default 22°) is "core."
2. Hysteresis expands the core through connected faces with slope ≤
   `orientation.max_slope_deg` (default 25°).
3. Connected components are extracted from the resulting mask.
4. Physical-area filtering (`segmentation.minimum_candidate_area_mm2` and
   related) discards implausibly small components.

This has reliably isolated real cockpit floors and is the mode the wizard
and CLI use by default.

## Structural-experimental mode (diagnostic only)

A separate structural-analysis path exists and can still provide useful
wall/ridge/hard-feature evidence and corner-confidence diagnostics. As a
*segmentation* source, though, it historically fragmented a real cockpit
floor into hundreds of thousands of tiny components (one specific run:
255,847 components) — clearly not usable as a production candidate source.

It is selectable via `--segmentation-mode structural-experimental` (or the
deprecated alias `advanced`) and is clearly labeled at every surface that
exposes it:

- `cli.py`'s `--segmentation-mode` help text calls it "the
  fragmentation-prone structural-experimental A/B diagnostic."
- `analysis_pipeline.py` emits an explicit warning when it's selected:
  *"boundary crossing can heavily fragment the accepted orientation floor.
  Compare with orientation mode; do not treat this as the production
  result."*

Do not make this the default, and do not tune it to try to match texture-
based expectations — its purpose is A/B comparison and wall/corner
diagnostics, not primary candidate discovery.

## Nonskid and seams

Both are evaluated the same way: only where the mesh actually contains
geometric evidence (molded relief boundaries, physically visible transition
edges). `NO_DETECTABLE_NONSKID_SIGNAL` (and the equivalent for seams) is a
correct, expected result on many real scans — not a bug to be tuned away.
