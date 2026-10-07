# Lead notes (session 2026-10-07) -- AutoDeck seam/sheet audit

## Ground truth
- Repo now at D:\AutoDeck. git status: 16 modified tracked files + 9 untracked (seamplace/seamplan/seamsnap + 5 test files + START_HERE_AI.md). HEAD ecb7b30, no remote. Matches START_HERE_AI.md §0.2.
- engine tests: 297 passed, 1 deselected, 710 s (log: scratchpad/engine_tests.log). The lastfailed test name no longer exists / no longer fails.
- Backups of every seams*.json / sheets.json / sheet_report.md from all runs: scratchpad/backup_runs/<run>/ (verified identical).

## H1 CONFIRMED (lead, scratchpad/h1/h1_extension.py)
Hovered chord vs cut disagree. sheets.split_panel extends EVERY seam by bbox diagonal+10 before kerf subtraction (sheets.py ~545: `s.extended(reach)`).
- A: across chord 447 mm in starboard strip (747,967)-(775,521) -> plan gives 2 pieces from panel 1 (3,687,778 + 1,131,148 mm2). Chord-only cut would give 1 piece (C shape).
- B: along chord 989 mm in bow extension (1535,5)-(2522,68) -> plan gives 2 pieces (port/starboard halves 2,635,040 + 2,175,031). Chord-only would give 1 piece.
- C: control across seam aft of console, full width 2020 mm -> 2 pieces either way.
- Interaction: seamplan._span deliberately returns first-entry..last-exit of all chords (spans across the console), so optimiser seams are unaffected by a chord-only cut. Legacy free-drawn seams (mode "") rely on the extension ("draw roughly").
- Proposed rule: seams with mode set (placed by direction tool, exact chord, or optimiser) cut only their own chord (+ small overshoot ~ seam_gap to sever cleanly); mode "" seams keep full extension. Also make the hover preview honest: either show every chord the line will cut, or (preferred) cut only the chord.
- Supporting evidence of intent: seam_references lets a second hover land exactly on the first seam's line "to continue it across the console" -- only meaningful if the cut is chord-limited.

## Observations (not bugs)
- Optimiser's 26-40 mm across seams on panels 2/3 are legit: those panels are 25-40 mm wide gunwale strips 2.4 m long.
- seam_through returns chords across ALL panels on the infinite line (nested placed frame); app.js segmentUnder picks the one under the pointer with a grace distance.
- Nester: bottom-left first-fit, largest-area first, 0 then 180, first sheet with room; occupancy = union of round-buffered placed pieces.

## Workflows
- wf_18af7dca-a9a (task wa3zl7245): 14 agents, 8 died on ECONNRESET at first call: read:seamplan, read:sheets, read:nesting, read:sheetjob-bridge, read:tests, probe-plan-real-seams, probe-nesting, probe-api-flow. Survivors: read:seamsnap-seamplace, read:ui-server-appjs, read:engine-diff, read:app-diff, probe-optimiser, probe-hover-snap.
- wf_a1bb0717-31d (task wzfvl88r6): re-runs the 8 with retry, dedups + verifies (ids G01..). No synthesis; lead merges both.
