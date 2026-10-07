# Audit scripts (2026-10-07 seam / autonest diagnosis)

These are the probe and verification scripts behind `docs/seam-audit-2026-10-07.md`,
copied verbatim from the audit's temporary scratch folder. They are evidence and
reproduction aids, not part of the application, and the test suite does not run them.

How they were run (Git Bash on Windows, from the repo root):

    cd /d/AutoDeck
    PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" .venv/Scripts/python.exe tools/audit/<folder>/<script>.py

Most scripts stage a COPY of a run folder before calling anything that writes
(`sheetjob.plan`, `bridge.job_sheets`, `bridge.job_optimise_seams`, `sheets.write_seams`
all write into the run directory). They expect a scratch folder next to them (`runs/`,
`out/`) and some hard-code the old scratch path
`C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/<session>/scratchpad/...`;
edit that to a folder outside the repo before re-running. Never point them at the real
`engine/outputs/runs/*` folders: those are the only copies of the owner's seam layouts.

Folders:

- `h1/` the lead's first reproduction (hovered chord vs whole-line cut).
- `wf/probe-hover-snap/`, `wf/probe-optimiser/`, `wf/seamsnap-seamplace/` hover, snap and
  optimiser probes (finding ids F01-F50).
- `wf2/probe-plan-real-seams/`, `wf2/probe-nesting/`, `wf2/probe-api-flow/`, `wf2/tests/`,
  `wf2/sheets/`, `wf2/seamplan/`, `wf2/nesting/`, `wf2/sheetjob-bridge/` production-plan,
  nesting, HTTP-flow and reader probes (finding ids G01-G61).
- `lead_notes.md` the lead's ground-truth notes; `engine_tests_2026-10-07.log` the suite run
  (297 passed, 1 deselected).
