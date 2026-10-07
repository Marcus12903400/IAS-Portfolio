"""Experiment 4: seam_tidiness_weight 0.0 and 1.0 (60 s each) against the saved
0.35 main run.  Checks that tidiness never adds a sheet or an oversize piece, and
re-plans each winner through sheetjob.plan.

Re-run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_03_tidiness.py"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (AXIS_RUN, HAND_PLACED, SCRATCH, config_with, dump, plan_summary,  # noqa: E402
                    stage, summarise_metrics, timed_optimise, forbidden_words)
from autodeck2 import sheetjob, sheets  # noqa: E402

OUT = SCRATCH / "out"
OUT.mkdir(exist_ok=True)


def main():
    main_saved = json.loads((OUT / "main_result.json").read_text(encoding="utf-8"))["result"]
    rows = {0.35: {"after": main_saved["report"]["after"], "seams": main_saved["seams"],
                   "evaluated": main_saved["report"]["candidates_evaluated"],
                   "confirmed": main_saved["report"]["candidates_confirmed"],
                   "reason": main_saved["reason"], "note": main_saved["report"]["tidiness_note"]}}
    for weight in (0.0, 1.0):
        config = config_with(seam_tidiness_weight=weight)
        options = sheets.settings(config)
        work = stage(f"tidy{weight}", AXIS_RUN, HAND_PLACED)
        result, messages, wall = timed_optimise(work, config, 60.0, f"tidy-{weight}")
        report = result["report"]
        fresh = sheetjob.plan(work, config, seams=list(result["seams"]), write_files=False)
        summary = plan_summary(fresh, options)
        rows[weight] = {"after": report["after"], "seams": [s.to_dict() for s in result["seams"]],
                        "evaluated": report["candidates_evaluated"], "confirmed": report["candidates_confirmed"],
                        "reason": result["reason"], "note": report["tidiness_note"], "wall_s": wall,
                        "messages": messages, "fresh": summary, "before": report["before"]}
        print(f"  weight {weight}: reason: {result['reason']}")
        print(f"  weight {weight}: note: {report['tidiness_note']}")
        print(f"  weight {weight}: before: {summarise_metrics(report['before'])}")
        print(f"  weight {weight}: after:  {summarise_metrics(report['after'])}")
        print(f"  weight {weight}: fresh plan: ",
              {k: summary[k] for k in ("status", "sheet_count", "piece_count", "seam_count", "oversize", "waste_percent", "utilisation")})
        print(f"  weight {weight}: forbidden words:", forbidden_words(result["reason"], report["search_note"], report["tidiness_note"], *messages) or "none")
        for seam in result["seams"]:
            print("    ", seam.seam_id, seam.mode, [round(v, 1) for v in (seam.x1, seam.y1, seam.x2, seam.y2)])
        print("   log tail:", messages[-3:])
    print("\n== comparison across the dial ==")
    print(f"{'weight':>6} {'oversize':>8} {'sheets':>6} {'waste%':>7} {'cost%':>7} {'seams':>5} {'sym':>5} {'rect':>5} {'align':>5} {'joins':>5} {'eval':>5}")
    for weight in (0.0, 0.35, 1.0):
        after = rows[weight]["after"]
        shape = after["shape"]
        print(f"{weight:>6} {len(after['oversize']):>8} {after['sheet_count']:>6} {after['waste_percent']:>7} "
              f"{after['cost_percent']:>7} {after['seam_count']:>5} {shape['symmetry']!s:>5} {shape['rectangularity']!s:>5} "
              f"{shape['alignment']!s:>5} {shape['joins']!s:>5} {rows[weight]['evaluated']:>5}")
    base = rows[0.0]["after"]
    for weight in (0.35, 1.0):
        after = rows[weight]["after"]
        print(f"weight {weight}: sheets {after['sheet_count']} vs {base['sheet_count']} at 0.0 -> "
              f"{'OK' if after['sheet_count'] <= base['sheet_count'] else 'ADDED A SHEET'}; "
              f"oversize {len(after['oversize'])} vs {len(base['oversize'])} -> "
              f"{'OK' if len(after['oversize']) <= len(base['oversize']) else 'ADDED OVERSIZE'}")
    print("seams identical 0.0 vs 0.35:", rows[0.0]["seams"] == rows[0.35]["seams"])
    print("seams identical 0.35 vs 1.0:", rows[0.35]["seams"] == rows[1.0]["seams"])
    dump(OUT / "tidiness.json", rows)


if __name__ == "__main__":
    main()
