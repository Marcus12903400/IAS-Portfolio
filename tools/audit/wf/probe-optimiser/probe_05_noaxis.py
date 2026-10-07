"""Experiment 6: the NO-AXIS run (pattern none, teak.frame null) with and without a
grain_angle_deg: 0.0 override.  Without the override the optimiser should refuse
(NO_AXIS); with it, it should run and every seam should be exactly on the 0 deg masters.

Re-run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_05_noaxis.py"
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import (HAND_PLACED, NO_AXIS_RUN, SCRATCH, config_with, dump, forbidden_words,  # noqa: E402
                    masters_for, panel_polygons, chord_report, plan_summary, stage,
                    summarise_metrics, timed_optimise)
from autodeck2 import sheetjob, sheets  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

OUT = SCRATCH / "out"
OUT.mkdir(exist_ok=True)


def main():
    meta = json.loads((NO_AXIS_RUN / "run.json").read_text(encoding="utf-8"))
    print("NO-AXIS run teak:", meta.get("teak"))
    print("panels placement nest offsets:", [(p["panel_id"], (p.get("placement") or {}).get("nest_offset_mm"))
                                             for p in meta.get("panels") or []])
    rows = {}

    # ---- no override: expect a clean refusal
    config = load_config()
    work = stage("noaxis_plain", NO_AXIS_RUN, HAND_PLACED)
    result, messages, wall = timed_optimise(work, config, 60.0, "noaxis-no-override")
    print("status:", result["status"])
    print("reason:", result["reason"])
    print("warnings:", result["report"]["warnings"])
    print("seams:", result["seams"], "before/after:", result["report"]["before"], result["report"]["after"])
    print("log:", messages)
    rows["plain"] = {"result": result, "messages": messages, "wall_s": wall}

    # ---- override grain 0.0, hand seams staged, and again with no seams
    for label, seams in (("noaxis_grain0_hand", HAND_PLACED), ("noaxis_grain0_empty", [])):
        config = config_with(grain_angle_deg=0.0)
        options = sheets.settings(config)
        work = stage(label, NO_AXIS_RUN, seams)
        print(f"\n== {label} ==")
        try:
            result, messages, wall = timed_optimise(work, config, 60.0, label)
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            rows[label] = {"traceback": traceback.format_exc()}
            continue
        report = result["report"]
        print("status:", result["status"])
        print("reason:", result["reason"])
        print("before:", summarise_metrics(report["before"]))
        print("after: ", summarise_metrics(report["after"]))
        print("improved:", report["improved"], "evaluated:", report["candidates_evaluated"],
              "confirmed:", report["candidates_confirmed"], "budget_exhausted:", report["budget_exhausted"])
        print("centreline_known:", report["centreline_known"], "boat_frame:", report["boat_frame"])
        print("warnings:", report["warnings"])
        print("tidiness_note:", report["tidiness_note"])
        print("panels:", report["panels"])
        frame, masters = masters_for(work, options)
        along, across = masters
        rotation = sheets.sheet_transform(frame.axis)
        material, outer_only, holes = panel_polygons(work, float(options["sample_step_mm"]))
        for seam in result["seams"]:
            unit = np.array([seam.x2 - seam.x1, seam.y2 - seam.y1]); unit /= np.hypot(*unit)
            master = along if seam.mode == "along" else across
            row = chord_report(seam, material[seam.panel_id], outer_only[seam.panel_id], holes[seam.panel_id], masters, rotation)
            print(f"   {seam.seam_id} {seam.mode} dot-master-1={abs(abs(float(np.dot(unit, master))) - 1.0):.2e} "
                  f"len={row['seam_length_mm']} outer-span={row['outer_span_edge_to_edge_mm']} material={row['material_chord_mm']} "
                  f"segs={row['material_segments']} frac-of-best={row['chord_as_fraction_of_best']} pos={row['offset_fraction_across_panel']}")
        print("log:")
        for line in messages:
            print("  |", line)
        fresh = sheetjob.plan(work, config, seams=list(result["seams"]), write_files=False)
        summary = plan_summary(fresh, options)
        print("fresh plan:", {k: summary[k] for k in ("status", "sheet_count", "piece_count", "seam_count", "oversize", "waste_percent", "utilisation")})
        print("fresh plan warnings:", summary["warnings"])
        print("forbidden words:", forbidden_words(result["reason"], report["search_note"], report["tidiness_note"], *report["warnings"], *messages) or "none")
        rows[label] = {"result": result, "messages": messages, "wall_s": wall, "fresh": summary}
    dump(OUT / "noaxis.json", rows)


if __name__ == "__main__":
    main()
