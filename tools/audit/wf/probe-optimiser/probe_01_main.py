"""Experiments 1, 2, 7, 8: one 60 s optimise on the AXIS run (hand seams staged),
chord analysis of every proposed seam, production re-plan of the winner, wording.
Also re-plans the 6-seam result saved in the real run's seams_optimiser_run.json.bak
(on a scratch copy) to see what its 40 mm / 26 mm across seams actually cut.

Re-run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_01_main.py"
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (AXIS_RUN, HAND_PLACED, SCRATCH, chord_report, dump, forbidden_words,  # noqa: E402
                    jsonable, masters_for, panel_polygons, plan_summary, stage,
                    summarise_metrics, timed_optimise)
from autodeck2 import sheetjob, sheets  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

OUT = SCRATCH / "out"
OUT.mkdir(exist_ok=True)


def chords_for(work, seams, options, label):
    frame, masters = masters_for(work, options)
    rotation = sheets.sheet_transform(frame.axis)
    material, outer_only, holes = panel_polygons(work, float(options["sample_step_mm"]))
    rows = []
    for seam in seams:
        pid = seam.panel_id
        if pid is None:
            # hand seams: pick the panel the drawn line crosses the most of
            from shapely.geometry import LineString
            drawn = LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)])
            pid = max(material, key=lambda p: material[p].intersection(drawn).length)
            seam = sheets.Seam(**{**seam.to_dict(), "panel_id": pid, "raw": None})
        rows.append(chord_report(seam, material[pid], outer_only[pid], holes[pid], masters, rotation))
    print(f"\n== chord analysis: {label} ==")
    for row in rows:
        print(f"  {row['seam_id']:<18} P{row['panel_id']} {row['mode'] or row['geometric_direction']:<7} "
              f"len={row['seam_length_mm']:>8} outer-span={row['outer_span_edge_to_edge_mm']!s:>8} "
              f"material={row['material_chord_mm']:>8} segs={row['material_segments']} "
              f"best-chord={row['best_material_chord_in_this_direction_mm']:>8} "
              f"frac-of-best={row['chord_as_fraction_of_best']} "
              f"angle(along,across)=({row['angle_to_along_deg']:.2e},{row['angle_to_across_deg']:.2e}) "
              f"offset={row['sheet_frame_offset_mm']} in {row['panel_extent_in_that_axis_mm']} "
              f"(pos {row['offset_fraction_across_panel']}) holes={row['line_crosses_cutouts']} "
              f"end-dist-to-outer={row['endpoint_dist_to_outer_boundary_mm']}")
    return rows


def main():
    config = load_config()
    options = sheets.settings(config)
    print("options:", {k: options[k] for k in ("sheet_width_mm", "sheet_length_mm", "max_part_width_mm",
                                               "max_part_length_mm", "seam_gap_mm", "part_spacing_mm",
                                               "nest_step_mm", "seam_tidiness_weight", "grain_angle_deg",
                                               "seam_snap_offset_mm", "seam_axis_snap_deg")})

    # ---------------------------------------------------------------- (1)
    work = stage("main", AXIS_RUN, HAND_PLACED)
    result, messages, wall = timed_optimise(work, config, 60.0, "main-60s")
    dump(OUT / "main_result.json", {"result": result, "messages": messages, "wall_s": wall})

    report = result["report"]
    print("\n== result ==")
    print("status:", result["status"])
    print("reason:", result["reason"])
    print("before:", summarise_metrics(report["before"]))
    print("after: ", summarise_metrics(report["after"]))
    print("improved:", report["improved"], "evaluated:", report["candidates_evaluated"],
          "confirmed:", report["candidates_confirmed"], "search_elapsed_s:", report["search_elapsed_s"],
          "elapsed_s:", report["elapsed_s"], "budget_exhausted:", report["budget_exhausted"])
    print("search_note:", report["search_note"])
    print("tidiness_note:", report["tidiness_note"])
    print("centreline_known:", report["centreline_known"], "tidiness_weight:", report["tidiness_weight"])
    print("boat_frame:", report["boat_frame"])
    print("warnings:", report["warnings"])
    print("panels:")
    for row in report["panels"]:
        print("  ", row)
    print("seams:")
    for seam in result["seams"]:
        print("  ", json.dumps(seam.to_dict()))
    print("\n== progress log (what the fabricator sees) ==")
    for line in messages:
        print("  |", line)

    # ---------------------------------------------------------------- (2)
    rows = chords_for(work, result["seams"], options, "proposed seams (production 1 mm sampling)")
    dump(OUT / "main_chords.json", rows)

    # ---------------------------------------------------------------- (7)
    print("\n== production re-plan of the winner ==")
    t0 = time.monotonic()
    fresh = sheetjob.plan(work, config, seams=list(result["seams"]), write_files=False)
    t_plan = time.monotonic() - t0
    summary = plan_summary(fresh, options)
    print(f"plan(seams=...) took {t_plan:.1f}s:", {k: summary[k] for k in
          ("status", "sheet_count", "piece_count", "seam_count", "oversize", "unplaced", "waste_percent", "utilisation")})
    after = report["after"]
    mismatches = []
    for key in ("status", "sheet_count", "piece_count", "seam_count", "waste_percent"):
        if summary[key] != after[key]:
            mismatches.append((key, after[key], summary[key]))
    if summary["oversize"] != after["oversize"]:
        mismatches.append(("oversize", after["oversize"], summary["oversize"]))
    if [round(u, 4) for u in summary["utilisation"]] != [round(u, 4) for u in after["utilisation"]]:
        mismatches.append(("utilisation", after["utilisation"], summary["utilisation"]))
    print("mismatches vs report['after']:", mismatches or "none")

    # Now the way the app stores them: write seams.json on the COPY and plan from disk, files written.
    sheets.write_seams(work, list(result["seams"]))
    t0 = time.monotonic()
    from_disk = sheetjob.plan(work, config, write_files=True)
    t_disk = time.monotonic() - t0
    disk_summary = plan_summary(from_disk, options)
    print(f"plan(from seams.json on the copy, write_files=True) took {t_disk:.1f}s:",
          {k: disk_summary[k] for k in ("status", "sheet_count", "piece_count", "seam_count", "oversize",
                                        "unplaced", "refused", "waste_percent", "utilisation")})
    print("files written into the COPY:", [f["name"] for f in from_disk["files"]])
    print("pieces:")
    for piece in disk_summary["pieces"]:
        print("  ", piece)
    print("seam snaps (what the corrector did to the optimiser's seams):")
    for snap in from_disk["seam_snaps"]:
        print("  ", {k: snap.get(k) for k in ("seam_id", "applied", "kind", "reference_label", "moved_mm",
                                               "angle_change_deg", "note")})
    print("plan warnings:", from_disk["warnings"])
    dump(OUT / "main_plan_from_disk.json", disk_summary)
    dump(OUT / "main_plan_direct.json", summary)

    # ---------------------------------------------------------------- (8)
    print("\n== wording ==")
    texts = [result["reason"], report["search_note"], report["tidiness_note"], *report["warnings"], *messages]
    print("forbidden words found:", forbidden_words(*texts) or "none")
    print("'best found' present in reason:", "best found" in result["reason"].lower())

    # ---------------------------------------------------------------- .bak investigation
    print("\n== the saved optimiser result in the real run (seams_optimiser_run.json.bak) ==")
    bak = json.loads((AXIS_RUN / "seams_optimiser_run.json.bak").read_text(encoding="utf-8"))["seams"]
    bak_work = stage("bak", AXIS_RUN, bak)
    bak_seams = sheets.read_seams(bak_work)
    bak_rows = chords_for(bak_work, bak_seams, options, "saved .bak seams")
    bak_plan = sheetjob.plan(bak_work, config, write_files=True)
    bak_summary = plan_summary(bak_plan, options)
    print("bak plan:", {k: bak_summary[k] for k in ("status", "sheet_count", "piece_count", "seam_count",
                                                     "oversize", "unplaced", "waste_percent", "utilisation")})
    for piece in bak_summary["pieces"]:
        if piece["panel_id"] in (2, 3):
            print("  ", piece)
    print("bak plan warnings:", bak_plan["warnings"])
    dump(OUT / "bak_chords.json", bak_rows)
    dump(OUT / "bak_plan.json", bak_summary)


if __name__ == "__main__":
    main()
