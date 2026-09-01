# CAM Geometry

## Philosophy

The raw detector outline is accurate but messy — it follows scan triangles,
raster cells, and local mesh noise. The final manufacturing geometry should
not follow every wiggle; it should mimic what a person does manually in
VCarve: recognize a fundamentally straight or circular region, draw one long
primitive through the middle of the noisy band, and connect regimes with
true circular arcs.

Preferred shape: `LONG LINE -> TANGENT ARC -> LONG LINE -> TANGENT ARC -> ...`

Splines remain useful internally (`curve_fit.py` will fall back to a native
spline before falling back further to a dense polyline), but they are not
the preferred final VCarve geometry — LINE/ARC is prioritized first
(`curve_fit.py::fit_manufacturing_curve` tries `LINE` then `ARC`/`CIRCLE`
before `SPLINE`).

## Continuity over primitive count

**Zero forbidden-geometry violations, zero self-intersections, zero join
gaps, and near-perfect G1 tangent joins come before minimizing primitive
count.** 20 lines + 30 arcs with perfect joints beats 14 lines + 18 arcs
with a 5 mm gap and a 46° tangent mismatch — a real historical failure mode
this codebase specifically guards against now (see "Historical bugs and
their fixes" below).

## Fit corridor

`polyarc_fit.absolute_fit_tolerance_mm` is hard-capped at 3.000 mm
(`fit_polyarc_curve` raises `ValueError` if a caller tries to configure it
higher) — 3 mm maximum deviation from the robust physical reference, a 6 mm
conceptual band. This is not automatically loosened to make a primitive fit;
if one long primitive can't fit inside the corridor, the fitter adds more
tangent primitives instead.

## Signed safety: raster noise is not a wall

Signed safety is judged against the **raw** detected contour: for an outer
deck boundary the wall side is forbidden; for an obstacle its interior is.
But the raw contour carries raster stair-steps and scanner spikes of about
1-2 mm, and a curve drawn through the middle of that noise band — the whole
point of the manufacturing fit — necessarily sits on the wall side of every
inward noise notch.

Historically the check allowed only 0.35 mm of penetration, which treated
every notch as a physical wall. Measured on the real Key West scan, that
produced 8,105 "violations" whose penetration was p50 0.7 mm / p99 2.4 mm /
max 3.2 mm — **not one real crossing** — and made low-count LINE/ARC
geometry impossible on any real scan.

`polyarc_fit.signed_safety_penetration_allowance_mm` (default **2.0**) is
the penetration depth that counts as noise. Anything deeper is a real
crossing and remains a hard failure. Buffering the raw polygon by this
allowance also fills inward notches narrower than twice the allowance
regardless of depth — the same "isolated spike vs persistent feature"
distinction the robust reference makes. A wide, deep feature (a wall step, a
console edge) still produces violations and still fails.

The fit corridor (`absolute_fit_tolerance_mm`, 3.0 mm) bounds every sample
to the physical reference *independently* of this check, so the allowance
does not widen the corridor. Every result also reports
`metrics.wall_side_penetration` (p50 / p95 / max depth beyond raw, and the
count beyond the allowance) so a VALID curve still says how far into the
noise band it actually sits. On Key West at the default allowance the
accepted geometry peaks at 1.99 mm.

Do not raise the allowance to lower primitive counts. The sweep on Key West
showed 3.0 mm yields fewer primitives (97 vs 154) only by pressing to
2.99 mm into the wall band; that is a manufacturing decision for the user,
not a default.

## Two constructions, count-aware selection

For a closed primary perimeter two independent constructions are attempted:

1. **Manual broad-regime fit** (`_manual_broad_regime_fit`): multi-scale
   smoothed reference, then a dynamic program choosing the minimum number of
   whole LINE/ARC regimes per protected span, followed by local join repair
   (`_repair_manual_joins`: trim the two neighbours, bridge with an exact
   tangent biarc). Since V0.3.6 the regime candidates are safety-aware —
   a regime on the wall side of the raw contour is rejected so the DP splits
   only where raw demands — and the join trim escalates (5 → 10 → 15 → 20 mm,
   `manual_join_trim_schedule_multipliers`) before a join is declared
   unbridgeable.
2. **Exact tangent-state chain** (`_fit_longest_valid_chain`): safe-side
   corridor targets, longest valid LINE/ARC/biarc interval at each shared
   tangent state.

When both pass, the one with fewer primitives is used
(`compare_manual_with_tangent_chain`, default true);
`metrics.manual_broad_regime_fit.selected_construction` records which. On
Key West the chain currently wins (154 primitives, 8 L + 146 A) because
the manual proposal's per-join biarc connectors more than double its count.

## Status vocabulary

| `PolyarcCurve.status` | Meaning | `final.dxf`? |
|---|---|---|
| `INVALID` | a hard check failed: corridor, forbidden crossing, self-intersection, closure, join gap, tangent over 0.10° | no |
| `REVIEW` | hard checks pass but the curve contains preserved raw geometry at a `SHARP_CORNER_REQUIRES_REVIEW` location | no |
| `TEST_GEOMETRY` | hard checks pass; remaining warnings are advisory (short connector primitives, tangent above the 0.05° target but within 0.10°) | yes (TEST_ONLY) |

Before V0.3.6 any warning at all — including "N primitives shorter than
10 mm" — forced `REVIEW` and blocked `final.dxf`, contradicting
"continuity outranks primitive count". Advisory warnings are still reported.

## Target continuity

| | Target | Hard maximum |
|---|---|---|
| In-memory join gap | ~machine precision | `1e-7` mm (`hard_failures` gate in `polyarc.py`) |
| Smooth-join tangent mismatch | 0.05° (`polyarc_fit.tangent_target_deg`) | 0.10° (`polyarc_fit.tangent_max_deg`) |

Exceeding the hard maximum sets `PolyarcCurve.status = "INVALID"`
(`polyarc.py`'s `hard_failures` check) — unless the join is explicitly
flagged `sharp_corner_review` (see below), in which case it's surfaced as
`REVIEW`, not silently accepted and not a hard failure either.

## True physical corners vs. solver failure

Where a genuine sharp physical feature can safely be represented with a
tangent fillet inside the fit corridor, the fitter uses it. Where no
tangent-preserving LINE/ARC/biarc candidate can be found for a location that
requires a smooth join — after exhausting the interval search, the
free-end-tangent escape-arc repair, and the soft-join tangent-projection
repair — the fitter does **not** silently accept a kinked chord. It raises
internally (`polyarc.SharpCornerRequiresReview`), and the span-level catch
in `fit_polyarc_curve` preserves the raw, un-simplified geometry for that
span (tagged `sharp_corner_review=True` on the affected primitives/joins),
adds a `SHARP_CORNER_REQUIRES_REVIEW` warning, and forces the curve's status
away from a clean pass. A solver failure is never silently reclassified as
"this must be a physical corner" — it's flagged for a human to look at.

## Historical bugs and their fixes (verified in this repository)

A prior audit found a real production run with visually clean-looking
output (~14 lines + 18 arcs) that was actually invalid: up to ~5 mm join
gaps and up to ~46° tangent mismatches. Two root causes were identified and
are fixed in this codebase (with regression tests in
`tests/geometry/test_v032_polyarc.py`):

1. **Tangent-losing fallback.** `_fit_recursive`'s base case used to emit a
   bare chord between adjacent samples whenever no valid candidate was
   found — even when a smooth join was required — with a comment claiming
   "the whole-curve audit still rejects any invalid outcome" that referred
   to diagnostic counters (`adjacent_fallback_count`, `depth_fallback_count`)
   nothing ever read. Fixed: the chord fallback is now only used when
   neither side requires tangent continuity (a genuine hard-corner
   boundary); otherwise it raises `SharpCornerRequiresReview` and the caller
   substitutes flagged raw geometry instead (see above). Tests:
   `test_fit_recursive_raises_review_instead_of_untangented_chord_when_smooth_join_required`,
   `test_sharp_corner_fallback_flags_review_instead_of_silent_kink`.

2. **Hard-corner index aliasing.** Three sites (`robust_reference.py`,
   `polyarc.py`, `v032_reports.py`) computed hard-corner indices against one
   point array, then reused those indices against an independently
   recollapsed/resampled array via `% len(...)` — silently wrapping onto an
   unrelated point whenever the two arrays' lengths diverged. Fixed: a
   shared `curve_fit.map_points_to_indices()` helper resolves the actual
   corner *coordinates* to the correct index in the target array by nearest
   match, and raises `ValueError` loudly if no sufficiently close match
   exists, instead of wrapping. Tests:
   `test_map_points_to_indices_resolves_by_coordinate_not_modulo`,
   `test_map_points_to_indices_fails_loudly_when_no_match_within_tolerance`,
   `test_landmark_dimension_audit_rejects_mismatched_length_arrays`.

3. **Unsafe "least-bad" connectors and a disabled safety net.**
   `_repair_manual_joins` used to rank connector candidates by violation
   count and pick the *least unsafe* one when none was safe — admitting
   physical interference to keep the join count low — and the adaptive
   safety net that re-audits the manual proposal
   (`adaptive_refine_invalid_manual_primary`) shipped disabled. Fixed: an
   unsafe connector is not a candidate at all, and the safety net is on by
   default.

4. **Raster noise treated as a wall** (the signed-safety allowance). See
   "Signed safety: raster noise is not a wall" above. Tests:
   `test_raster_noise_notches_are_not_treated_as_a_wall`,
   `test_penetration_allowance_separates_noise_from_crossing`,
   `test_wide_deep_feature_is_still_a_hard_forbidden_crossing`.

5. **Obstacle corridor targets drifting into the obstacle.**
   `curve_fit._corridor_targets` smooths the safe-side targets and then
   re-projects any that left the corridor — but it tested `not
   corridor.covers(point)` for both sides. For an outer deck the corridor is
   the shrunk deck polygon, so that is right; for an obstacle the corridor is
   the *grown* obstacle polygon, so a target dragged inside the obstacle was
   still "covered" and never corrected. The fitter then aimed into the
   forbidden side at those spots (measured on the Key West console: a target
   2.018 mm inside). Fixed with a side-aware test; unit tests
   `tests/unit/test_corridor_targets.py`.

6. **Whole-span raw fallback on one bad interval.** When the chain fitter
   could not bridge a single adjacent-sample interval it surrendered the
   entire logical span to raw geometry (32 kinked joins on the console).
   The repair is now local: the chain stays gap-free by routing through the
   two reference samples of just that interval (flagged
   `sharp_corner_review`) and keeps fitting the rest of the span. Test:
   `test_chain_fitter_localizes_an_unfittable_interval_instead_of_dropping_the_span`.

## Per-joint reporting

`PolyarcJoin` (built by `_build_join_table`) records, per joint: type
(`HARD_CORNER` / `SMOOTH_G1`), gap, tangent mismatch, and whether it's
`sharp_corner_review`-flagged. `manufacturing_report.md`/`polyarc_report.md`
fold these into aggregate metrics (`maximum_join_gap_mm`,
`maximum_smooth_tangent_mismatch_deg`,
`sharp_corner_review_join_count`/`sharp_corner_review_maximum_tangent_mismatch_deg`).
A dedicated per-joint `cam_joint_report.md` table (ID, type, incoming/
outgoing primitive, gap, tangent mismatch, status) was not built in this
pass — see `docs/V0_3_6_RELEASE_REPORT.md` for scope notes.
