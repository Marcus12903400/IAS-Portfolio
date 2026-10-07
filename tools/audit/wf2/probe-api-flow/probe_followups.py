"""Follow-up probes after probe_api_flow.py.  Re-run with:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
     "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/probe-api-flow/probe_followups.py"

Imports the environment set-up, helpers and assertions from probe_api_flow (same scratch
runs dir, same redirected caches).  Writes out/followups.json.
"""
from __future__ import annotations

import copy
import json
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_api_flow as P  # noqa: E402  (sets env + asserts before importing the app)

OUT = P.OUT / "followups.json"
R: dict = {}


def out(msg):
    P.out("[followup] " + msg)


# ---------------------------------------------------------------- faithful port of app.js adoptSeams
SEAM_INTENT = ["snap", "mode", "angle_deg"]


def adopt_seams(local: list[dict], rows: list[dict], answers_current: bool) -> list[dict]:
    fresh = [{
        "seam_id": s.get("seam_id"), "x1": s["x1"], "y1": s["y1"], "x2": s["x2"], "y2": s["y2"],
        "panel_id": s.get("panel_id"), "snap": s.get("snap") is not False, "raw": s.get("raw") or None,
        "mode": s.get("mode") or "", "angle_deg": s.get("angle_deg", None),
        "length_mm": s.get("length_mm"), "direction_deg": s.get("direction_deg"),
        "snap_note": s.get("snap_note") or "", "snap_applied": bool(s.get("snap_applied")),
    } for s in rows]
    if answers_current:
        return fresh
    by_id = {}
    for row in fresh:
        if row["seam_id"]:
            by_id[row["seam_id"]] = row          # JS Map.set: the later row wins
    merged = []
    for seam in local:
        row = by_id.get(seam.get("seam_id")) if seam.get("seam_id") else None
        if not row:
            merged.append(seam)
            continue
        merged.append(row if all(row.get(k) == seam.get(k) for k in SEAM_INTENT) else seam)
    return merged


def main() -> int:
    P.reset_run(keep_backups=True)
    app = P.create_app()
    client = app.test_client()
    st, data, _ = P.req(client, "POST", "/api/run/open", {"run_id": P.RUN_ID})
    P.wait_job(client, data["job_id"], timeout=600)

    # ---- F1: hover and re-plan with a 0 mm seam gap (the Settings box allows min=0)
    body = dict(P.PAGE_SETTINGS, mode="across", angle_deg=None, world=False, point_flat=[700.0, -800.0], seam_gap_mm="0")
    st, d, _ = P.req(client, "POST", "/api/seam/hover", body, note="[F1 hover seam_gap_mm 0]")
    R["F1_hover_seam_gap_0"] = {"status": st, "body": d if st != 200 else "200"}
    body = dict(P.PAGE_SETTINGS, mode="across", angle_deg=None, world=False, point_flat=[700.0, -800.0], seam_gap_mm="0.5")
    st, d, _ = P.req(client, "POST", "/api/seam/hover", body, note="[F1 hover seam_gap_mm 0.5]")
    R["F1_hover_seam_gap_0.5"] = {"status": st, "error": d.get("error") if isinstance(d, dict) else d}
    st, base, _ = P.req(client, "GET", "/api/sheets", note="[F1 baseline]")
    four = P.page_seams(base["seams"])
    st, d, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(four), seam_gap_mm="0"), note="[F1 replan seam_gap_mm 0]")
    R["F1_replan_seam_gap_0"] = {"status": st, "error": d.get("error") if isinstance(d, dict) else d}
    st, d, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(four), part_spacing_mm="0"), note="[F1 replan part_spacing 0]")
    R["F1_replan_part_spacing_0"] = {"status": st, "error": d.get("error") if isinstance(d, dict) else None, "plan": P.plan_summary(d).get("status") if st == 200 else None}
    out("F1: " + P.trunc({k: v for k, v in R.items() if k.startswith("F1")}, 900))

    # ---- F2: duplicate id after "remove then place", and what the page's merge would do with it
    #  Step 1: user places B (across) at x=-800  -> server assigns s5
    B = {"x1": -800.0, "y1": -1000.0, "x2": -800.0, "y2": 1000.0, "panel_id": 1, "mode": "across", "angle_deg": None, "snap": True,
         "raw": [-800.0, -1000.0, -800.0, 1000.0]}
    st, d1, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(four) + [B]), note="[F2 place B]")
    page = adopt_seams([], d1["seams"], True)
    ids1 = [s["seam_id"] for s in page]
    #  Step 2: user removes seam 2 (index 1) -> POST without it
    del page[1]
    st, d2, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(page)), note="[F2 remove s2]")
    page = adopt_seams(page, d2["seams"], True)
    ids2 = [s["seam_id"] for s in page]
    #  Step 3: user places C (across) at x=-1200 -> what id does the server give it?
    C = dict(B, x1=-1200.0, x2=-1200.0, raw=[-1200.0, -1000.0, -1200.0, 1000.0])
    local = copy.deepcopy(page) + [C]
    st, d3, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(local)), note="[F2 place C]")
    ids3 = [s["seam_id"] for s in d3["seams"]]
    #  Step 4a: reply taken whole (stamp current) -> page holds two seams with the same id
    whole = adopt_seams(local, d3["seams"], True)
    #  Step 4b: the same reply landing after ANOTHER edit (user clicked again, stamp not current):
    #  app.js merges by seam_id -- simulate: local list = page + C + D (D placed meanwhile, no id)
    D = dict(B, x1=-400.0, x2=-400.0, raw=[-400.0, -1000.0, -400.0, 1000.0])
    local_after_D = copy.deepcopy(whole) + [D]
    # the next re-plan POSTs [.., s5(B), s5(C), D] -> reply ids?
    st, d4, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(local_after_D)), note="[F2 replan with B,C,D]")
    ids4 = [s["seam_id"] for s in d4["seams"]]
    # now simulate that reply d4 arriving while the user has already placed E (stamp NOT current): merge path
    E = dict(B, x1=-200.0, x2=-200.0, raw=[-200.0, -1000.0, -200.0, 1000.0])
    local_after_E = copy.deepcopy(local_after_D) + [E]
    merged = adopt_seams(local_after_E, d4["seams"], False)
    xs_before = [(s.get("seam_id"), round((s["x1"] + s["x2"]) / 2)) for s in local_after_E]
    xs_after = [(s.get("seam_id"), round((s["x1"] + s["x2"]) / 2)) for s in merged]
    # the queued re-plan then POSTs `merged`
    st, d5, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(merged)), note="[F2 queued re-plan posts the merged list]")
    disk_x = [(s.get("seam_id"), round((s["x1"] + s["x2"]) / 2)) for s in (P.disk_seams() or [])]
    R["F2_duplicate_id_flow"] = {
        "after_place_B_ids": ids1, "after_remove_s2_ids": ids2, "after_place_C_ids(server)": ids3,
        "after_replan_B_C_D_ids(server)": ids4,
        "merge_path_local_before(id, station x)": xs_before, "merge_path_local_after(id, station x)": xs_after,
        "queued_replan_disk(id, station x)": disk_x,
        "seam_lost": sorted(set(x for _, x in xs_before) - set(x for _, x in xs_after)),
    }
    out("F2: " + P.trunc(R["F2_duplicate_id_flow"], 1500))

    # ---- F3: geometry of the 1156 x 6 mm sliver: distance between the squared s1 and s2 lines
    st, base, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=[dict(s) for s in P.pristine_seams()]), note="[F3 pristine 4 seams]")
    s1, s2 = base["seams"][0], base["seams"][1]
    ang = math.radians(s1["direction_deg"])
    n = (math.cos(ang + math.pi / 2), math.sin(ang + math.pi / 2))
    off1 = n[0] * s1["x1"] + n[1] * s1["y1"]
    off2 = n[0] * s2["x1"] + n[1] * s2["y1"]
    gap = abs(off1 - off2)
    sliver = [o for o in base.get("oversize", []) if o.get("length_mm", 1e9) < 20 or o.get("width_mm", 1e9) < 20]
    R["F3_sliver_geometry"] = {"s1_direction": s1["direction_deg"], "s2_direction": s2["direction_deg"],
                               "perpendicular_distance_between_s1_and_s2_lines_mm": round(gap, 2),
                               "minus_6mm_kerf_mm": round(gap - 6.0, 2), "sliver_pieces_reported": sliver,
                               "pieces": P.plan_summary(base)["pieces"], "unplaced": P.plan_summary(base)["unplaced"],
                               "raw_s1": s1.get("raw"), "raw_s2": s2.get("raw")}
    out("F3: " + P.trunc(R["F3_sliver_geometry"], 900))

    # ---- F4: /api/file inline mimetype for the undo read
    r = client.get(f"/api/file/{P.RUN_ID}/seams_previous.json?inline=1")
    R["F4_undo_read"] = {"status": r.status_code, "mimetype": r.mimetype, "get_json_silent_is_none": r.get_json(silent=True) is None,
                         "parses_as_json": isinstance(json.loads(r.get_data(as_text=True)), dict)}
    r = client.get(f"/api/file/{P.RUN_ID}/seams_previous_1.json?inline=1")
    R["F4_numbered_backup_download"] = {"status": r.status_code, "note": "numbered backups are not in SAFE_FILES"}
    out("F4: " + P.trunc({k: v for k, v in R.items() if k.startswith("F4")}))

    # ---- F5: latency percentiles (6 optimiser seams, OK plan) for POST re-plan, GET and hover
    six = [dict(s) for s in P.optimiser_bak_seams()]
    post_t, get_t, hov_t = [], [], []
    for i in range(8):
        st, d, dt = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(six)), note=f"[F5 post {i}]")
        post_t.append(dt)
        st, d, dt = P.req(client, "GET", "/api/sheets", note=f"[F5 get {i}]")
        get_t.append(dt)
    for i in range(50):
        t0 = time.perf_counter()
        client.post("/api/seam/hover", json=dict(P.PAGE_SETTINGS, mode="across", angle_deg=None, world=True, point_flat=[700.0 - i * 10, -800.0]))
        hov_t.append(time.perf_counter() - t0)
    pct = lambda xs, q: round(sorted(xs)[min(len(xs) - 1, int(q * len(xs)))], 3)  # noqa: E731
    R["F5_latency_s"] = {"post_replan_6_seams": {"median": round(statistics.median(post_t), 3), "max": round(max(post_t), 3)},
                         "get_sheets_6_seams": {"median": round(statistics.median(get_t), 3), "max": round(max(get_t), 3)},
                         "hover_with_world_lift": {"median": round(statistics.median(hov_t), 4), "p95": pct(hov_t, 0.95), "max": round(max(hov_t), 4)}}
    out("F5: " + json.dumps(R["F5_latency_s"]))

    # ---- F6: GET /api/sheets with a manual grain override is NOT what GET gives (page's own-settings path)
    st, d0, _ = P.req(client, "GET", "/api/sheets", note="[F6 GET default]")
    st, d1, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(six), grain_angle_deg="0"), note="[F6 POST grain 0]")
    st, d2, _ = P.req(client, "GET", "/api/sheets", note="[F6 GET after POST grain 0]")
    R["F6_grain_override_vs_get"] = {
        "post_grain0": {"axis_deg": d1["boat"].get("axis_deg"), "sheets": len(d1["sheets"]), "status": d1["status"],
                        "seam_dirs": [s["direction_deg"] for s in d1["seams"]]},
        "get_after": {"axis_deg": d2["boat"].get("axis_deg"), "sheets": len(d2["sheets"]), "status": d2["status"],
                      "seam_dirs": [s["direction_deg"] for s in d2["seams"]]},
        "disk_seam_dirs_after_post_grain0": [round(math.degrees(math.atan2(s["y2"] - s["y1"], s["x2"] - s["x1"])) % 180, 3) for s in P.disk_seams()],
    }
    out("F6: " + P.trunc(R["F6_grain_override_vs_get"], 900))

    # ---- F7: the optimiser's seams re-posted by the page: does a second re-plan move anything (idempotence)?
    st, a, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=copy.deepcopy(six)), note="[F7 post six]")
    st, b, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=P.page_seams(a["seams"])), note="[F7 re-post adopted]")
    st, c, _ = P.req(client, "POST", "/api/sheets/seams", dict(P.PAGE_SETTINGS, seams=P.page_seams(b["seams"])), note="[F7 re-post adopted again]")
    drift = [round(max(abs(x["x1"] - y["x1"]), abs(x["y1"] - y["y1"]), abs(x["x2"] - y["x2"]), abs(x["y2"] - y["y2"])), 9)
             for x, y in zip(a["seams"], c["seams"])]
    R["F7_idempotence_drift_mm"] = drift
    R["F7_bytes_identical_2nd_vs_3rd"] = json.dumps(b["seams"], sort_keys=True) == json.dumps(c["seams"], sort_keys=True)
    out("F7: " + json.dumps({"drift": drift, "identical": R["F7_bytes_identical_2nd_vs_3rd"]}))

    OUT.write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
    out(f"written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
