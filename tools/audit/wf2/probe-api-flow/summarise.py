"""Compact view of out/results.json.  Usage: python summarise.py [phase ...]"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "results.json"
R = json.loads(OUT.read_text(encoding="utf-8"))
want = sys.argv[1:] or list(R["phases"])


def p(*a):
    print(*a, flush=True)


def short(d, keys):
    return {k: d.get(k) for k in keys if k in d}


for name in want:
    ph = R["phases"].get(name)
    if ph is None:
        p(f"## {name}: (not run)")
        continue
    p(f"\n## {name}  ({ph.get('phase_wall_s')} s)")
    if "EXCEPTION" in ph:
        p("EXCEPTION:", ph["EXCEPTION"])
        p(ph.get("trace", "")[-1500:])
        continue
    if name == "open":
        p("job:", ph.get("job"))
        p("state:", short(ph.get("state") or {}, ["run_id", "overlays_ready", "runs_dir"]))
    elif name == "get":
        s = ph["summary"]
        p("status", ph["status"], "elapsed", ph["elapsed_s"], "plan", s["status"], "seams", s["seam_count"], "pieces", s["piece_count"], "sheets", s["sheets"], "util", s["utilisation"])
        p("oversize", s["oversize"]); p("unplaced", s["unplaced"]); p("pieces", s["pieces"])
        p("seams", s["seams"])
        p("warnings:"); [p("   -", w) for w in (s["warnings"] or [])]
        p("keys", ph["top_level_keys"]); p("settings", ph["settings_subset"]); p("disk sha after GET", ph["disk_sha_after_get"])
    elif name == "place":
        for h in ph["hovers"]:
            p("hover", h["label"], "->", h["status"], h["elapsed_s"], "s dir", h["direction_deg"], "reason", h["reason"], "under", h["chord_under_pointer"])
            p("      segments", h["segments"])
        p("sent new seam", ph.get("sent_new_seam"))
        p("stored new seam", ph.get("stored_new_seam"))
        p("hover vs stored delta mm", ph.get("hover_vs_stored_delta_mm"))
        po = ph.get("post", {})
        p("POST", po.get("status"), po.get("elapsed_s"), "s;", short(po.get("summary", {}), ["status", "seam_count", "piece_count", "sheets", "utilisation", "oversize", "unplaced"]))
        p("pieces before", ph.get("pieces_before")); p("pieces after ", ph.get("pieces_after"))
        p("disk", ph.get("disk"))
        p("warnings after:"); [p("   -", w) for w in (po.get("summary", {}).get("warnings") or [])]
    elif name == "delete":
        p("start ids", ph["start_ids"])
        for s in ph["steps"]:
            sm = s.get("summary", {})
            p(f"removed {s['removed']}: sent {s['sent_count']} -> {s['status']} in {s['elapsed_s']}s; plan {sm.get('status')} pieces {sm.get('piece_count')} sheets {sm.get('sheets')} oversize {sm.get('oversize')} unplaced {sm.get('unplaced')}")
            p("     disk:", s.get("disk"), "" if s.get("status") == 200 else s.get("body"))
            if s["sent_count"] == 0:
                p("     zero-seam response seams:", sm.get("seams"), "warnings:", sm.get("warnings"))
        p("final seams.json bytes:", repr(ph.get("final_disk_bytes")))
    elif name == "validate":
        p("baseline:", ph["baseline"]["status"], short(ph["baseline"]["summary"], ["status", "seam_count", "piece_count", "sheets"]))
        for c in ph["cases"]:
            extra = {k: v for k, v in c.items() if k not in ("case", "status", "elapsed_s", "disk_changed", "error", "summary", "disk_seams", "disk_ids")}
            sm = c.get("summary") or {}
            p(f"- {c['case']!r}: {c['status']} disk_changed={c.get('disk_changed')} error={c.get('error')!r}"
              + (f" plan={sm.get('status')} seams={sm.get('seam_count')} pieces={sm.get('piece_count')} ids={c.get('disk_ids')}" if c["status"] == 200 else "")
              + (f" extra={extra}" if extra else ""))
            if c["status"] == 200 and sm.get("seams"):
                p("      seams:", sm.get("seams"))
            if c["status"] == 200 and sm.get("warnings"):
                p("      warnings:", [w for w in sm["warnings"] if "seam" in w.lower() and "piece" not in w.lower()][:6])
        p("disk equals baseline at end:", ph.get("disk_equals_baseline_at_end"))
    elif name == "export":
        for r in ph["rounds"]:
            p(f"- round {r['label']!r}: plan {short(r['plan'], ['status', 'seam_count', 'piece_count', 'sheets', 'oversize', 'unplaced'])}")
            p("     export POST", r["export_post"]["status"], "job", short(r.get("job") or {}, ["status", "wall_s", "error"]), "result", (r.get("job") or {}).get("result"))
            p("     disk sheet files", r["disk_sheet_files"], "sheets.json status", r["sheets_json_status"])
            p("     sheets.json files", r["sheets_json_files"])
            p("     GET files", r["get_files"], "GET status", r["get_status"])
            p("     /api/state run_files sheet_*", r["state_run_files_sheets"])
            p("     job log:"); [p("        |", l) for l in r.get("job_log", [])]
    elif name == "optimise":
        p("pre backup files", ph.get("pre_backup_files"), "pre seams sha", ph.get("pre_seams_sha"))
        p("before:", short(ph.get("before", {}), ["status", "seam_count", "piece_count", "sheets", "oversize"]), "disk unchanged by GET:", ph.get("disk_unchanged_by_get"))
        p("POST", ph.get("post", {}).get("status"), ph.get("post", {}).get("body"))
        p("job", ph.get("job"))
        res = ph.get("result") or {}
        p("result:", {k: res.get(k) for k in ("status", "reason", "improved", "seams_written", "seams_replaced", "backup", "candidates_evaluated", "candidates_confirmed", "elapsed_s", "budget_exhausted", "search_note")})
        p("before metrics:", res.get("before")); p("after metrics:", res.get("after"))
        p("post backup files", ph.get("post_backup_files"), "backup==pre seams.json:", ph.get("backup_equals_pre_seams_json"), "rotated copy == old backup:", ph.get("rotated_copy_equals_old_backup"))
        p("after disk seams:", [(s.get("seam_id"), s.get("mode"), s.get("panel_id"), round(s["x1"], 1), round(s["y1"], 1), round(s["x2"], 1), round(s["y2"], 1)) for s in (ph.get("after_disk_seams") or [])])
        p("after GET:", short(ph.get("after", {}), ["status", "seam_count", "piece_count", "sheets", "utilisation", "oversize", "unplaced", "waste_percent"]))
        p("after GET seams:", ph.get("after", {}).get("seams"))
        p("undo read:", ph.get("undo_read")); p("undo POST:", ph.get("undo_post", {}).get("status"), short(ph.get("undo_post", {}).get("summary", {}) or {}, ["status", "seam_count", "piece_count", "sheets", "oversize"]))
        p("undo bytes identical to pre-optimise seams.json:", ph.get("undo_bytes_identical_to_pre_optimise"))
        p("undo delta vs page-before (mm):", ph.get("undo_delta_vs_page_before_mm"), "vs file-before:", ph.get("undo_delta_vs_file_before_mm"))
        p("undo raw == pre file xy:", ph.get("undo_raw_equals_pre_file_xy"), "ids", ph.get("undo_disk_ids"), "backup files after undo", ph.get("backup_files_after_undo"))
        p("job log:"); [p("   |", l) for l in ph.get("job_log", [])]
    elif name == "race":
        for r in ph["rounds"]:
            p(f"- round {r['round']}: POST {r['post_count']} seams ->", r.get("post"))
            for g in r["gets"]:
                p("     GET during POST ->", g)
            p("     disk count after:", r["disk_count_after"])
        p("hammer 25 s:", ph.get("hammer_25s")); p("disk count end:", ph.get("disk_count_end"))
    elif name == "busy":
        p("pre backup files", ph.get("pre_backup_files"))
        p("optimise POST", ph.get("optimise_post"))
        for k, v in (ph.get("mid_search") or {}).items():
            p("   mid-search", k, "->", v)
        p("job", ph.get("job"))
        res = ph.get("result") or {}
        p("result:", {k: res.get(k) for k in ("status", "improved", "seams_written", "seams_replaced", "backup", "elapsed_s")})
        p("post backup files", ph.get("post_backup_files"), "backup ids", ph.get("backup_seam_ids"), "backup==pre-press seams.json:", ph.get("backup_equals_pre_press_seams_json"))
        p("after disk ids", ph.get("after_disk_ids"), "seams_replaced reported", ph.get("seams_replaced_reported"))
        p("undo restored ids", ph.get("undo_restored_ids"), "restored count vs 4:", ph.get("undo_restored_count_vs_pre_press_4"))
        p("second press:", ph.get("second_press"))
        p("job log:"); [p("   |", l) for l in ph.get("job_log", [])]

p("\nrepo untouched check:", R.get("repo_untouched_check"))
p("real seams.json sha before/after:", R.get("real_seams_sha_before_after"))
p("total wall s:", R.get("total_wall_s"))
