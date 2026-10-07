"""Experiment 3: determinism (second 60 s run vs the saved main run), short budgets
(15 s, 8 s, 5 s): what changes, is the even-band fallback used, and do the fallback
bands stay inside the envelope (checked arithmetically AND through sheetjob.plan).

Re-run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_02_determinism.py"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (AXIS_RUN, HAND_PLACED, SCRATCH, dump, jsonable, masters_for,  # noqa: E402
                    plan_summary, stage, summarise_metrics, timed_optimise)
from autodeck2 import seamplan, sheetjob, sheets  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

OUT = SCRATCH / "out"
OUT.mkdir(exist_ok=True)


def objective(metrics):
    return (len(metrics["oversize"]), metrics["sheet_count"], round(metrics["waste_percent"], 2),
            metrics["seam_count"])


def seams_of(result):
    return [s.to_dict() if isinstance(s, sheets.Seam) else s for s in result["seams"]]


def main():
    config = load_config()
    options = sheets.settings(config)
    main_saved = json.loads((OUT / "main_result.json").read_text(encoding="utf-8"))["result"]
    main_seams = main_saved["seams"]
    print("main (saved): evaluated", main_saved["report"]["candidates_evaluated"],
          "objective", objective(main_saved["report"]["after"]))

    # ---------------------------------------------------------------- determinism at 60 s
    work = stage("det60", AXIS_RUN, HAND_PLACED)
    again, messages, wall = timed_optimise(work, config, 60.0, "det-60s")
    same_seams = seams_of(again) == main_seams
    print("identical seams to main run:", same_seams)
    if not same_seams:
        for a, b in zip(seams_of(again), main_seams):
            if a != b:
                print("  differs:", a, "\n      vs ", b)
    print("identical evaluated count:", again["report"]["candidates_evaluated"] == main_saved["report"]["candidates_evaluated"],
          "identical objective:", objective(again["report"]["after"]) == objective(main_saved["report"]["after"]))
    print("after:", summarise_metrics(again["report"]["after"]))
    dump(OUT / "det60_result.json", {"result": again, "messages": messages, "wall_s": wall})

    # ---------------------------------------------------------------- short budgets
    short = {}
    for budget in (15.0, 8.0, 5.0):
        work = stage(f"budget{int(budget)}", AXIS_RUN, HAND_PLACED)
        result, messages, wall = timed_optimise(work, config, budget, f"budget-{int(budget)}s")
        report = result["report"]
        fallback_lines = [m for m in messages if "out of time" in m or "even split" in m]
        print(f"  budget {budget}: wall {wall:.1f}s over-budget-by {wall - budget:.1f}s; status {result['status']}; "
              f"budget_exhausted={report['budget_exhausted']} evaluated={report['candidates_evaluated']} "
              f"confirmed={report['candidates_confirmed']} search_elapsed={report['search_elapsed_s']}")
        print("   after:", summarise_metrics(report["after"]))
        print("   reason:", result["reason"])
        print("   fallback messages:", fallback_lines or "none")
        print("   panel shortlist messages:", [m for m in messages if m.startswith("Panel")])
        print("   same seams as 60 s run:", seams_of(result) == main_seams)
        for seam in result["seams"]:
            print("    ", seam.seam_id, seam.mode, [round(v, 1) for v in (seam.x1, seam.y1, seam.x2, seam.y2)])
        fresh = sheetjob.plan(work, config, seams=list(result["seams"]), write_files=False)
        summary = plan_summary(fresh, options)
        print("   fresh plan:", {k: summary[k] for k in ("status", "sheet_count", "piece_count", "seam_count",
                                                          "oversize", "waste_percent")})
        short[budget] = {"result": result, "messages": messages, "wall_s": wall, "fresh": summary}
    dump(OUT / "short_budgets.json", short)

    # ---------------------------------------------------------------- the fallback itself
    print("\n== _fallback_candidates bands (arithmetic) and production plan ==")
    work = stage("fallback", AXIS_RUN, HAND_PLACED)
    frame, masters = masters_for(work, options)
    rotation = sheets.sheet_transform(frame.axis)
    loops, _p, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(work))
    panels = seamplan._read_panels(loops, rotation, options, seamplan._nest_offsets(work))
    gap = float(options["seam_gap_mm"])
    cuts = {}
    for panel in panels:
        candidate = seamplan._fallback_candidates(panel, options)[0]
        cuts[panel.panel_id] = candidate
        for direction in (seamplan.ALONG, seamplan.ACROSS):
            lo, hi = seamplan._extent(panel, direction)
            offsets = sorted(c.offset_mm for c in candidate if c.direction == direction)
            widths = seamplan._band_widths(lo, hi, offsets, gap)
            usable = seamplan._usable(direction, options)
            print(f"  P{panel.panel_id} {direction:<6} span={hi - lo:8.1f} cuts={[round(o, 1) for o in offsets]} "
                  f"bands={[round(w, 1) for w in widths]} usable={usable} margin-ok={all(w <= usable - seamplan.BAND_MARGIN_MM for w in widths)}")
    layout = seamplan._Layout(cuts=cuts, oversize=[], sheets=0, waste=0.0, seam_count=sum(len(c) for c in cuts.values()),
                              seam_length_mm=0.0, piece_count=0, shape=seamplan._Shape(None, None, None, None),
                              tidiness_penalty_percent=0.0)
    seams = seamplan._seams_for(panels, layout, masters)
    for seam in seams:
        print("   fallback seam", seam.seam_id, [round(v, 1) for v in (seam.x1, seam.y1, seam.x2, seam.y2)])
    fresh = sheetjob.plan(work, config, seams=seams, write_files=False)
    summary = plan_summary(fresh, options)
    print("  fallback production plan:", {k: summary[k] for k in ("status", "sheet_count", "piece_count", "seam_count",
                                                                   "oversize", "unplaced", "waste_percent", "utilisation")})
    for piece in summary["pieces"]:
        if not piece["inside_envelope"]:
            print("   OUTSIDE ENVELOPE:", piece)
    print("  oversize detail:", summary["oversize_detail"])
    dump(OUT / "fallback_plan.json", {"seams": seams, "plan": summary})


if __name__ == "__main__":
    main()
