"""Experiment 5: optimise on a copy whose seams.json is {"seams": []} and on a copy
with no seams.json at all (60 s each), AXIS run.

Re-run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_04_seams_none.py"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (AXIS_RUN, SCRATCH, dump, forbidden_words, plan_summary, stage,  # noqa: E402
                    summarise_metrics, timed_optimise)
from autodeck2 import sheetjob, sheets  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

OUT = SCRATCH / "out"
OUT.mkdir(exist_ok=True)


def main():
    config = load_config()
    options = sheets.settings(config)
    main_saved = json.loads((OUT / "main_result.json").read_text(encoding="utf-8"))["result"]
    rows = {}
    for label, seams in (("emptyseams", []), ("noseams", None)):
        work = stage(label, AXIS_RUN, seams)
        print(f"\n== {label}: seams.json exists: {(work / 'seams.json').is_file()} ==")
        try:
            result, messages, wall = timed_optimise(work, config, 60.0, label)
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            rows[label] = {"traceback": traceback.format_exc()}
            continue
        report = result["report"]
        print("reason:", result["reason"])
        print("before:", summarise_metrics(report["before"]))
        print("after: ", summarise_metrics(report["after"]))
        print("improved:", report["improved"], "evaluated:", report["candidates_evaluated"],
              "confirmed:", report["candidates_confirmed"], "budget_exhausted:", report["budget_exhausted"])
        print("warnings:", report["warnings"])
        print("same seams as main (hand-seam-staged) run:", [s.to_dict() for s in result["seams"]] == main_saved["seams"])
        for seam in result["seams"]:
            print("   ", seam.seam_id, seam.mode, [round(v, 1) for v in (seam.x1, seam.y1, seam.x2, seam.y2)])
        print("log:")
        for line in messages:
            print("  |", line)
        fresh = sheetjob.plan(work, config, seams=list(result["seams"]), write_files=False)
        summary = plan_summary(fresh, options)
        print("fresh plan:", {k: summary[k] for k in ("status", "sheet_count", "piece_count", "seam_count", "oversize", "waste_percent", "utilisation")})
        print("forbidden words:", forbidden_words(result["reason"], report["search_note"], report["tidiness_note"], *messages) or "none")
        print("seams.json on the copy after optimise (must be untouched by optimise itself):",
              (work / "seams.json").read_text(encoding="utf-8") if (work / "seams.json").is_file() else "absent")
        rows[label] = {"result": result, "messages": messages, "wall_s": wall, "fresh": summary}
    dump(OUT / "seams_none.json", rows)


if __name__ == "__main__":
    main()
