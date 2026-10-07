"""Shared paths + helpers for the probe scripts.

READ-ONLY on D:/AutoDeck; every copy and every output lives under SCRATCH.
"""
from pathlib import Path
import json

SCRATCH = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/probe-plan-real-seams")
OUT = SCRATCH / "out"
PNG = SCRATCH / "png"
RUNS = Path("D:/AutoDeck/engine/outputs/runs")
AXIS_RUN = RUNS / "21kwcockpit-1-20260901-180939"
RUN3 = RUNS / "21kwcockpit-3-20260902-024834"

# set name -> (source run, seam file to stage as seams.json)
SETS = {
    "A": (AXIS_RUN, AXIS_RUN / "seams.json"),
    "B": (AXIS_RUN, AXIS_RUN / "seams_previous.json"),
    "C": (AXIS_RUN, AXIS_RUN / "seams_optimiser_run.json.bak"),
    "D": (RUN3, RUN3 / "seams.json"),
}


def copy_dir(name: str) -> Path:
    p = SCRATCH / "runs" / f"set_{name}"
    # belt and braces: the copy must be under the scratch folder, never the repo
    assert "scratchpad" in str(p) and not str(p).lower().startswith("d:"), p
    return p


def resplit(work, plan_result, options):
    """Re-split the copy exactly the way sheetjob.preview() does: production
    read_fitted_dxf + classify_loops + split_panel with the AS-CUT seams that
    plan() reported.  Returns everything the audits need."""
    from autodeck2 import sheetjob, sheets
    source = sheetjob.source_dxf(work)
    loops, pattern, kind = sheets.read_fitted_dxf(source)
    frame, _w = sheetjob.resolve_frame(work, options)
    rotation = sheets.sheet_transform(frame.axis)
    seam_list = [sheets.Seam.from_dict(s) for s in plan_result["seams"]]
    step = float(options["sample_step_mm"])
    panels, pieces, warnings = {}, {}, []
    for pid in sorted(loops):
        outer, holes = sheets.classify_loops(loops[pid], step)
        if outer is None:
            continue
        panels[pid] = (outer, holes, sheets.loop_polygon(outer, holes, step))
        ps, w = sheets.split_panel(pid, outer, holes, seam_list, options)
        warnings += w
        for p in ps:
            pieces[p.piece_id] = p
    return dict(loops=loops, pattern=pattern, kind=kind, frame=frame, rotation=rotation,
                seams=seam_list, panels=panels, pieces=pieces, split_warnings=warnings)


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))
