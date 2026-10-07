"""Empirical probe of the AutoDeck seam/sheet HTTP flow with the Flask test client.

Re-run with:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
     "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/probe-api-flow/probe_api_flow.py" \
     [--phases open,get,place,delete,validate,export,optimise,race,busy] [--optimise-budget 90]

Everything runs against a COPY of the AXIS run under this scratch folder.  The
environment is set before autodeck_app is imported so the app's runs dir, preview
cache and inputs dir all point into the scratch folder; the script asserts that
before the first POST.  Before and after, it snapshots the REAL run folder and the
engine cache (name/size/mtime) and reports any difference.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import shutil
import sys
import threading
import time
from pathlib import Path

SCRATCH = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/probe-api-flow")
REPO = Path("D:/AutoDeck")
RUN_ID = "21kwcockpit-1-20260901-180939"
PRISTINE = SCRATCH / "pristine" / RUN_ID
RUNS = SCRATCH / "runs"
RUN = RUNS / RUN_ID
OUT = SCRATCH / "out"
REAL_RUN = REPO / "engine" / "outputs" / "runs" / RUN_ID
ENGINE_CACHE = REPO / "engine" / "cache"
APP_PREVIEW_CACHE = REPO / "app" / "cache" / "preview"

# --- environment BEFORE importing the app ------------------------------------
os.environ["AUTODECK2_ROOT"] = str(REPO / "engine")
os.environ["AUTODECK_V1_ROOT"] = str(REPO / "engine-v1")
os.environ["AUTODECK_RUNS_DIR"] = str(RUNS)
os.environ["AUTODECK_PREVIEW_CACHE"] = str(SCRATCH / "cache" / "preview")
os.environ["AUTODECK_INPUTS_DIR"] = str(SCRATCH / "inputs")
for p in (REPO / "engine-v1" / "src", REPO / "engine", REPO / "app"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from autodeck_app import settings  # noqa: E402

assert settings.RUNS_DIR == RUNS, settings.RUNS_DIR
assert settings.PREVIEW_CACHE_DIR == SCRATCH / "cache" / "preview", settings.PREVIEW_CACHE_DIR
assert settings.INPUTS_DIR == SCRATCH / "inputs", settings.INPUTS_DIR
assert str(settings.RUNS_DIR).startswith(str(SCRATCH)), settings.RUNS_DIR

from autodeck_app.server import create_app  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)
LOG = open(OUT / "log.txt", "a", encoding="utf-8")
RESULTS: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "requests": [], "phases": {}}

# What app.js sheetSettings() sends with every hover / re-plan when the Settings
# box is at its shipped defaults (index.html values; boxNumber returns strings).
PAGE_SETTINGS = {
    "part_spacing_mm": "20", "seam_gap_mm": "6", "grain_angle_deg": None,
    "allow_180_rotation": True, "seam_axis_priority": True, "seam_axis_snap_deg": "20",
    "seam_snap_enabled": True, "seam_snap_angle_deg": "5",
}
SEAM_KEYS = ["seam_id", "x1", "y1", "x2", "y2", "panel_id", "snap", "raw", "mode", "angle_deg"]


def out(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def trunc(obj, n=900) -> str:
    text = json.dumps(obj, default=str)
    return text if len(text) <= n else text[:n] + f"...(+{len(text) - n} chars)"


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    snap = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def reset_run(keep_backups: bool = True) -> None:
    if RUN.exists():
        shutil.rmtree(RUN)
    shutil.copytree(PRISTINE, RUN)
    if not keep_backups:
        for p in RUN.glob("seams_previous*.json"):
            p.unlink()
    out(f"reset run copy from pristine (keep_backups={keep_backups}); files={len(list(RUN.iterdir()))}")


def disk_seams() -> list[dict] | None:
    p = RUN / "seams.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("seams")


def disk_bytes(name: str) -> bytes | None:
    p = RUN / name
    return p.read_bytes() if p.exists() else None


def pristine_seams() -> list[dict]:
    return json.loads((PRISTINE / "seams.json").read_text(encoding="utf-8"))["seams"]


def optimiser_bak_seams() -> list[dict]:
    return json.loads((PRISTINE / "seams_optimiser_run.json.bak").read_text(encoding="utf-8"))["seams"]


def page_seams(rows: list[dict]) -> list[dict]:
    """What app.js adoptSeams keeps and what the next POST therefore carries."""
    return [{
        "seam_id": s.get("seam_id"), "x1": s["x1"], "y1": s["y1"], "x2": s["x2"], "y2": s["y2"],
        "panel_id": s.get("panel_id"), "snap": s.get("snap") is not False, "raw": s.get("raw") or None,
        "mode": s.get("mode") or "", "angle_deg": s.get("angle_deg", None),
        "length_mm": s.get("length_mm"), "direction_deg": s.get("direction_deg"),
        "snap_note": s.get("snap_note") or "", "snap_applied": bool(s.get("snap_applied")),
    } for s in rows]


def req(client, method: str, url: str, payload=None, note: str = ""):
    t0 = time.perf_counter()
    if method == "GET":
        r = client.get(url)
    else:
        r = client.post(url, json=payload) if payload is not None else client.post(url)
    dt = time.perf_counter() - t0
    data = r.get_json(silent=True)
    if data is None:
        text = r.get_data(as_text=True)
        try:
            data = json.loads(text)
        except Exception:
            data = text
    rec = {"t": round(time.time(), 3), "method": method, "url": url, "status": r.status_code,
           "elapsed_s": round(dt, 3), "note": note}
    if isinstance(data, dict) and "error" in data:
        rec["error"] = data["error"]
    RESULTS["requests"].append(rec)
    out(f"{method} {url} -> {r.status_code} in {dt:.2f}s {note} "
        + (f"error={data.get('error')!r}" if isinstance(data, dict) and 'error' in data else ""))
    return r.status_code, data, dt


def wait_job(client, job_id: str, timeout: float = 900.0, every: float = 0.5) -> dict:
    t0 = time.time()
    since = 0
    lines: list[str] = []
    last = None
    while time.time() - t0 < timeout:
        r = client.get(f"/api/jobs/{job_id}?since={since}")
        j = r.get_json()
        lines.extend(j["log"])
        since = j["log_length"]
        last = j
        if j["status"] in ("done", "error"):
            break
        time.sleep(every)
    last = dict(last or {})
    last["log"] = lines
    last["wall_s"] = round(time.time() - t0, 2)
    out(f"job {job_id} {last.get('kind')} -> {last.get('status')} in {last['wall_s']}s "
        f"(elapsed_s={last.get('elapsed_s')}) error={last.get('error')}")
    for line in lines:
        out("    | " + line)
    return last


def plan_summary(data) -> dict:
    if not isinstance(data, dict):
        return {"raw": trunc(data, 300)}
    s = {k: data.get(k) for k in ("available", "status", "seam_count", "piece_count", "reason")}
    s["sheets"] = len(data.get("sheets") or [])
    s["utilisation"] = [sh.get("utilisation") for sh in (data.get("sheets") or [])]
    s["oversize"] = [(o.get("piece_id"), round(o.get("width_mm", 0)), round(o.get("length_mm", 0)))
                     for o in (data.get("oversize") or [])]
    s["unplaced"] = (data.get("summary") or {}).get("unplaced_piece_ids")
    s["waste_percent"] = (data.get("summary") or {}).get("waste_percent")
    s["files"] = [f.get("name") for f in (data.get("files") or [])]
    s["seams"] = [(x.get("seam_id"), x.get("mode"), x.get("length_mm"), x.get("direction_deg"),
                   x.get("snap_applied"), x.get("snap_note")) for x in (data.get("seams") or [])]
    s["pieces"] = [(p.get("piece_id"), round(p.get("area_mm2", 0) / 1e4, 1)) for p in (data.get("pieces") or [])]
    s["warnings"] = data.get("warnings")
    s["seams_world_count"] = len(data.get("seams_world") or []) if "seams_world" in data else None
    s["panels"] = len(data.get("panels") or []) if "panels" in data else None
    return s


def compare_disk(sent: list[dict], response_seams: list[dict]) -> dict:
    disk = disk_seams()
    resp_sub = [{k: s.get(k) for k in SEAM_KEYS} for s in response_seams]
    result = {"disk_count": None if disk is None else len(disk), "sent_count": len(sent),
              "response_count": len(response_seams),
              "disk_equals_response": disk == resp_sub,
              "disk_ids": None if disk is None else [d.get("seam_id") for d in disk]}
    deltas = []
    if disk is not None:
        for a, b in zip(sent, disk):
            deltas.append(round(max(abs(float(a["x1"]) - b["x1"]), abs(float(a["y1"]) - b["y1"]),
                                    abs(float(a["x2"]) - b["x2"]), abs(float(a["y2"]) - b["y2"])), 3))
    result["max_endpoint_delta_sent_vs_disk_mm"] = deltas
    return result


def chord_under(result: dict, point) -> int:
    """app.js segmentUnder: the chord whose parameter range brackets the point."""
    best, best_gap = -1, math.inf
    for i, s in enumerate(result.get("segments") or []):
        dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
        l2 = dx * dx + dy * dy
        if l2 < 1e-12:
            continue
        t = ((point[0] - s["x1"]) * dx + (point[1] - s["y1"]) * dy) / l2
        gap = (-t if t < 0 else (t - 1 if t > 1 else 0)) * math.sqrt(l2)
        if gap < best_gap:
            best_gap, best = gap, i
    return best if best_gap <= 5.0 else -1


# ============================================================================ phases

def phase_open(client) -> dict:
    ph: dict = {}
    st, data, _ = req(client, "POST", "/api/run/open", {"run_id": RUN_ID})
    ph["post"] = {"status": st, "body": data}
    if st != 200:
        return ph
    job = wait_job(client, data["job_id"], timeout=1200)
    ph["job"] = {k: job.get(k) for k in ("status", "wall_s", "elapsed_s", "error", "result")}
    ph["job_log"] = job["log"]
    st, state, _ = req(client, "GET", "/api/state")
    ph["state"] = {"run_id": state.get("run_id"), "overlays_ready": state.get("overlays_ready"),
                   "runs_dir": state.get("runs_dir"), "inputs_dir": state.get("inputs_dir"),
                   "scan": state.get("scan") and {k: state["scan"].get(k) for k in ("path", "preview_ready", "sha256")},
                   "run_files": state.get("run_files")}
    out("state: " + trunc(ph["state"], 700))
    return ph


def phase_get(client) -> dict:
    st, data, dt = req(client, "GET", "/api/sheets")
    ph = {"status": st, "elapsed_s": round(dt, 2), "summary": plan_summary(data),
          "boat": data.get("boat") if isinstance(data, dict) else None,
          "settings_subset": {k: (data.get("settings") or {}).get(k) for k in
                              ("sheet_width_mm", "sheet_length_mm", "max_part_width_mm", "max_part_length_mm",
                               "seam_gap_mm", "part_spacing_mm", "grain_angle_deg", "seam_snap_enabled")}
          if isinstance(data, dict) else None,
          "top_level_keys": sorted(data.keys()) if isinstance(data, dict) else None,
          "disk_seams_after_get": disk_seams(),
          "disk_sha_after_get": sha(RUN / "seams.json")}
    out("GET /api/sheets summary: " + trunc(ph["summary"], 1500))
    out("boat: " + trunc(ph["boat"], 600))
    return ph


def phase_place(client) -> dict:
    """Hover like app.js pumpHover, pick the chord under the pointer like segmentUnder,
    build the seam like placeHovered and POST it like replanSheets."""
    ph: dict = {"hovers": []}
    st, before, _ = req(client, "GET", "/api/sheets", note="(baseline before placing)")
    ph["before"] = plan_summary(before)
    probes = [
        ("A starboard beside console, across", "across", None, [700.0, -800.0]),
        ("B aft of console, across (full width)", "across", None, [-800.0, 0.0]),
        ("C starboard beside console, along", "along", None, [700.0, -800.0]),
        ("D bow extension, across", "across", None, [2000.0, 0.0]),
        ("E inside console cut-out, across", "across", None, [700.0, 0.0]),
        ("F off the deck", "across", None, [5000.0, 5000.0]),
        ("G angle 30", "angle", 30, [-800.0, 0.0]),
    ]
    chosen = None
    for label, mode, angle, point in probes:
        body = dict(PAGE_SETTINGS, mode=mode, angle_deg=angle if mode == "angle" else None,
                    world=False, point_flat=point)
        st, data, dt = req(client, "POST", "/api/seam/hover", body, note=label)
        idx = chord_under(data, point) if isinstance(data, dict) else -1
        rec = {"label": label, "status": st, "elapsed_s": round(dt, 3),
               "direction_deg": data.get("direction_deg") if isinstance(data, dict) else None,
               "reason": data.get("reason") if isinstance(data, dict) else data,
               "point_flat": data.get("point_flat") if isinstance(data, dict) else None,
               "segments": [(s["panel_id"], s["length_mm"], round(s["x1"], 1), round(s["y1"], 1),
                             round(s["x2"], 1), round(s["y2"], 1)) for s in (data.get("segments") or [])]
               if isinstance(data, dict) else None,
               "world_polylines": [len(w) for w in (data.get("world") or [])] if isinstance(data, dict) else None,
               "chord_under_pointer": idx}
        ph["hovers"].append(rec)
        out(f"hover {label}: " + trunc(rec, 700))
        if label.startswith("A") and idx >= 0:
            chosen = (data["segments"][idx], mode, angle)
    if chosen is None:
        ph["error"] = "no chord under pointer for probe A"
        return ph
    seg, mode, angle = chosen
    new_seam = {"x1": seg["x1"], "y1": seg["y1"], "x2": seg["x2"], "y2": seg["y2"],
                "panel_id": seg["panel_id"], "mode": mode,
                "angle_deg": angle if mode == "angle" else None, "snap": True,
                "raw": [seg["x1"], seg["y1"], seg["x2"], seg["y2"]]}
    sent = page_seams(before["seams"]) + [new_seam]
    ph["sent_new_seam"] = new_seam
    st, data, dt = req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=sent),
                       note="(4 existing + hovered chord A)")
    ph["post"] = {"status": st, "elapsed_s": round(dt, 2), "summary": plan_summary(data)}
    if st == 200:
        stored = data["seams"][-1]
        ph["stored_new_seam"] = {k: stored.get(k) for k in SEAM_KEYS + ["length_mm", "direction_deg", "snap_applied", "snap_note", "snap_moved_mm", "snap_angle_deg"]}
        ph["hover_vs_stored_delta_mm"] = round(max(abs(stored["x1"] - seg["x1"]), abs(stored["y1"] - seg["y1"]),
                                                   abs(stored["x2"] - seg["x2"]), abs(stored["y2"] - seg["y2"])), 4)
        ph["disk"] = compare_disk(sent, data["seams"])
        ph["pieces_before"] = ph["before"]["pieces"]
        ph["pieces_after"] = ph["post"]["summary"]["pieces"]
        out("POST summary: " + trunc(ph["post"]["summary"], 1500))
        out(f"stored new seam: {trunc(ph['stored_new_seam'])}; hover-vs-stored delta {ph['hover_vs_stored_delta_mm']} mm")
        out("disk check: " + trunc(ph["disk"]))
    return ph


def phase_delete(client) -> dict:
    """Remove seams one at a time, from the end of the list, down to zero."""
    ph: dict = {"steps": []}
    st, current, _ = req(client, "GET", "/api/sheets", note="(start of delete loop)")
    seams = page_seams(current["seams"])
    ph["start_ids"] = [s["seam_id"] for s in seams]
    while seams:
        removed = seams.pop()  # app.js: state.seams.splice(index, 1); seamsChanged()
        sent = copy.deepcopy(seams)
        st, data, dt = req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=sent),
                           note=f"(removed {removed['seam_id']}, {len(sent)} left)")
        step = {"removed": removed["seam_id"], "sent_count": len(sent), "status": st, "elapsed_s": round(dt, 2)}
        if st == 200:
            step["summary"] = plan_summary(data)
            step["disk"] = compare_disk(sent, data["seams"])
            seams = page_seams(data["seams"])
        else:
            step["body"] = data
        ph["steps"].append(step)
        out(f"delete step: {trunc(step, 1200)}")
    ph["final_disk_bytes"] = (disk_bytes("seams.json") or b"").decode("utf-8")
    return ph


def phase_validate(client) -> dict:
    ph: dict = {"cases": []}
    base_sent = [dict(s) for s in pristine_seams()]  # old schema, 4 seams
    st, base, _ = req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=base_sent), note="(baseline: 4 pristine seams)")
    ph["baseline"] = {"status": st, "summary": plan_summary(base)}
    base_page = page_seams(base["seams"])
    base_bytes = disk_bytes("seams.json")
    ph["baseline_disk_sha"] = sha(RUN / "seams.json")

    def case(name: str, body, expect_write: bool | None = None, extra=None):
        pre = disk_bytes("seams.json")
        st, data, dt = req(client, "POST", "/api/sheets/seams", body, note=f"[{name}]")
        post = disk_bytes("seams.json")
        rec = {"case": name, "status": st, "elapsed_s": round(dt, 2), "disk_changed": pre != post,
               "error": data.get("error") if isinstance(data, dict) else None}
        if st == 200 and isinstance(data, dict):
            rec["summary"] = {k: plan_summary(data).get(k) for k in ("status", "seam_count", "piece_count", "sheets", "seams", "warnings")}
            rec["disk_ids"] = [d.get("seam_id") for d in (disk_seams() or [])]
            rec["disk_seams"] = disk_seams()
        if extra:
            rec.update(extra(data) if callable(extra) else extra)
        ph["cases"].append(rec)
        out(f"case {name}: " + trunc(rec, 1400))
        if post != base_bytes:
            # put the baseline back the way the page would (re-POST the adopted list)
            req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=copy.deepcopy(base_page)), note="(restore baseline)")
        return rec

    S = lambda seams, **kw: dict(PAGE_SETTINGS, seams=seams, **kw)  # noqa: E731
    bp = lambda: copy.deepcopy(base_page)  # noqa: E731

    # 5a empty list
    case("empty list", S([]))
    # 5b duplicate seam ids (s2 renamed to s1)
    dup = bp(); dup[1]["seam_id"] = "s1"
    case("duplicate seam_ids s1,s1,s3,s4", S(dup))
    # 5b' missing id after a removal: [s1, s3, s4, new-without-id]  (remove s2 then Place seam)
    rem = bp(); del rem[1]
    newseam = {"x1": -800.0, "y1": -1000.0, "x2": -800.0, "y2": 1000.0, "panel_id": 1, "mode": "across",
               "angle_deg": None, "snap": True, "raw": [-800.0, -1000.0, -800.0, 1000.0]}
    case("remove s2 then place a new seam without seam_id", S(rem + [newseam]))
    # 5b'' two placed seams in one list without ids (two quick clicks)
    two = bp() + [dict(newseam), dict(newseam, x1=-1200.0, x2=-1200.0, raw=[-1200.0, -1000.0, -1200.0, 1000.0])]
    case("4 seams + 2 new without ids", S(two))
    # 5c zero-length seam
    zl = bp() + [{"x1": -800.0, "y1": 0.0, "x2": -800.0, "y2": 0.0, "panel_id": 1, "mode": "", "snap": True, "raw": None}]
    case("zero-length seam x1==x2,y1==y2 mode ''", S(zl))
    zl2 = bp() + [{"x1": -800.0, "y1": 0.0, "x2": -800.0, "y2": 0.0, "panel_id": 1, "mode": "across", "snap": True,
                   "raw": [-800.0, 0.0, -800.0, 0.0]}]
    case("zero-length seam mode across", S(zl2))
    zl3 = bp() + [{"x1": -800.0, "y1": 0.0, "x2": -800.0, "y2": 0.0, "panel_id": None, "mode": "", "snap": False, "raw": None}]
    case("zero-length seam snap false panel None", S(zl3))
    # 5d mode along with a wrong (across-ish) direction: s1 runs ~ along Y
    wrong = bp(); wrong[0]["mode"] = "along"
    case("s1 (direction ~92 deg) with mode along", S(wrong),
         extra=lambda d: {"s1_after": {k: (d["seams"][0].get(k) if isinstance(d, dict) and d.get("seams") else None)
                                       for k in ("x1", "y1", "x2", "y2", "direction_deg", "length_mm", "snap_note", "snap_angle_deg", "snap_moved_mm")},
                          "s1_before_direction": base_page[0]["direction_deg"]})
    wrong2 = bp(); wrong2[0]["mode"] = "along"; wrong2[0]["snap"] = False
    case("s1 mode along but snap false", S(wrong2),
         extra=lambda d: {"s1_after": {k: (d["seams"][0].get(k) if isinstance(d, dict) and d.get("seams") else None)
                                       for k in ("direction_deg", "snap_note", "snap_applied")}})
    # 5e panel_id variants
    p = bp(); p[0]["panel_id"] = "1"
    case("panel_id as string '1'", S(p), extra=lambda d: {"stored_panel_id": d["seams"][0].get("panel_id") if isinstance(d, dict) and d.get("seams") else None})
    p = bp(); p[0]["panel_id"] = "abc"
    case("panel_id 'abc'", S(p))
    p = bp(); p[0]["panel_id"] = 1.7
    case("panel_id 1.7", S(p), extra=lambda d: {"stored_panel_id": d["seams"][0].get("panel_id") if isinstance(d, dict) and d.get("seams") else None})
    p = bp(); p[0]["panel_id"] = 99
    case("panel_id 99 (no such panel)", S(p))
    # 5f snap variants
    p = bp(); p[0]["snap"] = False
    case("snap false (bool)", S(p), extra=lambda d: {"stored_snap": d["seams"][0].get("snap") if isinstance(d, dict) and d.get("seams") else None,
                                                     "note": d["seams"][0].get("snap_note") if isinstance(d, dict) and d.get("seams") else None})
    p = bp(); p[0]["snap"] = "false"
    case("snap 'false' (string)", S(p), extra=lambda d: {"stored_snap": d["seams"][0].get("snap") if isinstance(d, dict) and d.get("seams") else None})
    p = bp(); p[0]["snap"] = "maybe"
    case("snap 'maybe'", S(p), extra=lambda d: {"stored_snap": d["seams"][0].get("snap") if isinstance(d, dict) and d.get("seams") else None})
    p = bp(); p[0]["snap"] = 0
    case("snap 0", S(p), extra=lambda d: {"stored_snap": d["seams"][0].get("snap") if isinstance(d, dict) and d.get("seams") else None})
    # 5h coordinate variants
    p = bp(); p[0]["x1"] = "12.5"
    case("x1 as string '12.5'", S(p), extra=lambda d: {"stored_x1": d["seams"][0].get("x1") if isinstance(d, dict) and d.get("seams") else None})
    p = bp(); p[0]["x1"] = "NaN"
    case("x1 'NaN'", S(p))
    p = bp(); p[0]["x1"] = None
    case("x1 null", S(p))
    p = bp(); p[0]["x1"] = 1e7
    case("x1 1e7", S(p))
    p = bp(); p[0]["x1"] = True
    case("x1 true", S(p))
    p = bp(); del p[0]["y2"]
    case("missing y2", S(p))
    p = bp(); p[0]["raw"] = [1, 2, 3]
    case("raw with 3 numbers", S(p))
    # 5i mode / angle
    p = bp(); p[0]["mode"] = "diagonal"
    case("mode 'diagonal'", S(p))
    p = bp(); p[0]["mode"] = "angle"; p[0]["angle_deg"] = None
    case("mode angle with angle_deg null", S(p))
    p = bp(); p[0]["mode"] = "angle"; p[0]["angle_deg"] = "abc"
    case("mode angle with angle_deg 'abc'", S(p))
    p = bp(); p[0]["mode"] = "ALONG"
    case("mode 'ALONG' (upper case)", S(p))
    # 5k shape errors
    case("seams is a dict", S({"a": 1}))
    case("no seams key", dict(PAGE_SETTINGS))
    case("seams list with a number", S([1, 2]))
    st, data, _ = req(client, "POST", "/api/sheets/seams", [1, 2], note="[body is a JSON array]")
    ph["cases"].append({"case": "body is a JSON array", "status": st, "error": data.get("error") if isinstance(data, dict) else data})
    # 5l settings edge values
    case("seam_gap_mm 0", S(bp(), seam_gap_mm="0"))
    case("seam_gap_mm -1", S(bp(), seam_gap_mm="-1"))
    case("seam_gap_mm 'abc'", S(bp(), seam_gap_mm="abc"))
    case("grain_angle_deg ''", S(bp(), grain_angle_deg=""))
    case("grain_angle_deg 'abc'", S(bp(), grain_angle_deg="abc"))
    case("allow_180_rotation 'false' string", S(bp(), allow_180_rotation="false"))
    case("allow_180_rotation 'nope'", S(bp(), allow_180_rotation="nope"))
    case("501 seams", S([dict(bp()[0]) for _ in range(501)]))
    ph["disk_sha_end"] = sha(RUN / "seams.json")
    ph["disk_equals_baseline_at_end"] = disk_bytes("seams.json") == base_bytes
    return ph


def phase_export(client) -> dict:
    ph: dict = {"rounds": []}

    def export_round(label: str, seams: list[dict]):
        st, planned, _ = req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=seams), note=f"(seams for export: {label})")
        rec = {"label": label, "plan": plan_summary(planned) if st == 200 else {"status_code": st, "body": planned}}
        st, data, _ = req(client, "POST", "/api/sheets/export", dict(PAGE_SETTINGS), note=f"(export {label})")
        rec["export_post"] = {"status": st, "body": data}
        if st == 200:
            job = wait_job(client, data["job_id"], timeout=600)
            rec["job"] = {k: job.get(k) for k in ("status", "wall_s", "error", "result")}
            rec["job_log"] = job["log"]
        rec["disk_sheet_files"] = sorted(p.name for p in RUN.glob("sheet_*.dxf"))
        rec["disk_has_sheets_json"] = (RUN / "sheets.json").exists()
        rec["disk_has_report"] = (RUN / "sheet_report.md").exists()
        sj = json.loads((RUN / "sheets.json").read_text(encoding="utf-8")) if (RUN / "sheets.json").exists() else {}
        rec["sheets_json_files"] = [(f.get("name"), f.get("pieces"), f.get("refused") and [r.get("piece_id") for r in f["refused"]]) for f in sj.get("files") or []]
        rec["sheets_json_status"] = sj.get("status")
        st, after, _ = req(client, "GET", "/api/sheets", note=f"(after export {label})")
        rec["get_files"] = [(f.get("name"), f.get("sheet"), f.get("pieces"), f.get("exists")) for f in (after.get("files") or [])]
        rec["get_status"] = after.get("status")
        st, state, _ = req(client, "GET", "/api/state", note="(run_files after export)")
        rec["state_run_files_sheets"] = sorted(k for k, v in (state.get("run_files") or {}).items() if k.startswith("sheet_") and v)
        ph["rounds"].append(rec)
        out(f"export round {label}: " + trunc({k: v for k, v in rec.items() if k != "job_log"}, 1800))

    export_round("4 pristine hand seams", [dict(s) for s in pristine_seams()])
    export_round("6 optimiser seams from .bak", [dict(s) for s in optimiser_bak_seams()])
    export_round("no seams", [])
    export_round("6 optimiser seams again", [dict(s) for s in optimiser_bak_seams()])
    return ph


def phase_optimise(client, budget: float | None) -> dict:
    ph: dict = {}
    reset_run(keep_backups=True)
    pre_seams = disk_bytes("seams.json")
    pre_prev = disk_bytes("seams_previous.json")
    ph["pre_backup_files"] = sorted(p.name for p in RUN.glob("seams_previous*.json"))
    ph["pre_seams_sha"] = sha(RUN / "seams.json")
    st, before, _ = req(client, "GET", "/api/sheets", note="(page opens run: GET before optimise)")
    ph["before"] = plan_summary(before)
    ph["disk_unchanged_by_get"] = disk_bytes("seams.json") == pre_seams
    body = dict(PAGE_SETTINGS)
    if budget is not None:
        body["time_budget_s"] = budget
    st, data, _ = req(client, "POST", "/api/seams/optimise", body, note=f"(budget={'default' if budget is None else budget})")
    ph["post"] = {"status": st, "body": data}
    if st != 200:
        return ph
    job = wait_job(client, data["job_id"], timeout=900)
    ph["job"] = {k: job.get(k) for k in ("status", "wall_s", "elapsed_s", "error")}
    ph["result"] = job.get("result")
    ph["job_log"] = job["log"]
    out("optimise result: " + trunc(ph["result"], 2500))
    ph["post_backup_files"] = sorted(p.name for p in RUN.glob("seams_previous*.json"))
    ph["backup_equals_pre_seams_json"] = disk_bytes("seams_previous.json") == pre_seams
    ph["rotated_copy_equals_old_backup"] = {name: disk_bytes(name) == pre_prev for name in ph["post_backup_files"] if name != "seams_previous.json" and name not in ph["pre_backup_files"]}
    ph["after_disk_seams"] = disk_seams()
    st, after, _ = req(client, "GET", "/api/sheets", note="(after optimise)")
    ph["after"] = plan_summary(after)
    # ---- undo, exactly as app.js undoOptimise does
    st, saved, _ = req(client, "GET", f"/api/file/{RUN_ID}/seams_previous.json?inline=1", note="(undo: read backup)")
    ph["undo_read"] = {"status": st, "count": len(saved.get("seams") or []) if isinstance(saved, dict) else None,
                       "first": (saved.get("seams") or [None])[0] if isinstance(saved, dict) else trunc(saved, 200)}
    restored = [dict(s) for s in (saved.get("seams") or [])] if isinstance(saved, dict) else []
    st, undone, dt = req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=restored), note="(undo: POST backup as seam set)")
    ph["undo_post"] = {"status": st, "elapsed_s": round(dt, 2), "summary": plan_summary(undone) if st == 200 else undone}
    post_seams = disk_bytes("seams.json")
    ph["undo_bytes_identical_to_pre_optimise"] = post_seams == pre_seams
    ph["undo_disk_sha"] = sha(RUN / "seams.json")
    # semantic comparison: restored x1..y2 vs what the page showed before pressing (GET before), and vs the raw file
    deltas_vs_before_get, deltas_vs_file = [], []
    disk_after = disk_seams() or []
    pre_list = json.loads(pre_seams.decode("utf-8"))["seams"] if pre_seams else []
    for a, b in zip(before.get("seams") or [], disk_after):
        deltas_vs_before_get.append(round(max(abs(a["x1"] - b["x1"]), abs(a["y1"] - b["y1"]), abs(a["x2"] - b["x2"]), abs(a["y2"] - b["y2"])), 4))
    for a, b in zip(pre_list, disk_after):
        deltas_vs_file.append(round(max(abs(a["x1"] - b["x1"]), abs(a["y1"] - b["y1"]), abs(a["x2"] - b["x2"]), abs(a["y2"] - b["y2"])), 4))
    ph["undo_delta_vs_page_before_mm"] = deltas_vs_before_get
    ph["undo_delta_vs_file_before_mm"] = deltas_vs_file
    ph["undo_raw_equals_pre_file_xy"] = [tuple(b.get("raw") or []) == (a["x1"], a["y1"], a["x2"], a["y2"]) for a, b in zip(pre_list, disk_after)]
    ph["undo_disk_ids"] = [d.get("seam_id") for d in disk_after]
    ph["backup_files_after_undo"] = sorted(p.name for p in RUN.glob("seams_previous*.json"))
    out("undo check: " + trunc({k: ph[k] for k in ("undo_bytes_identical_to_pre_optimise", "undo_delta_vs_page_before_mm", "undo_delta_vs_file_before_mm", "undo_raw_equals_pre_file_xy", "undo_disk_ids", "backup_files_after_undo")}, 1200))
    return ph


def phase_race(app) -> dict:
    ph: dict = {"rounds": []}
    c_main = app.test_client()
    st, base, _ = req(c_main, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=[dict(s) for s in pristine_seams()]), note="(race baseline: 4 seams)")
    four = page_seams(base["seams"])
    three = four[:3]
    for rnd in range(3):
        sent = three if rnd % 2 == 0 else four
        res: dict = {"round": rnd, "post_count": len(sent), "gets": []}
        done = threading.Event()

        def poster():
            c = app.test_client()
            t0 = time.perf_counter()
            st, data, dt = req(c, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=copy.deepcopy(sent)), note=f"(race round {rnd} POST {len(sent)} seams)")
            res["post"] = {"status": st, "elapsed_s": round(dt, 3), "seam_count": len(data.get("seams") or []) if isinstance(data, dict) else None,
                           "plan_status": data.get("status") if isinstance(data, dict) else data, "started": t0}
            done.set()

        th = threading.Thread(target=poster)
        th.start()
        time.sleep(0.02)
        while not done.is_set():
            c = app.test_client()
            t0 = time.perf_counter()
            st, data, dt = req(c, "GET", "/api/sheets", note=f"(race round {rnd} GET during POST)")
            res["gets"].append({"status": st, "elapsed_s": round(dt, 3), "seam_count": len(data.get("seams") or []) if isinstance(data, dict) else None,
                                "plan_status": data.get("status") if isinstance(data, dict) else None,
                                "error": data.get("error") if isinstance(data, dict) else trunc(data, 200),
                                "post_finished_before_get_returned": done.is_set()})
            if len(res["gets"]) >= 4:
                break
        th.join()
        res["disk_count_after"] = len(disk_seams() or [])
        ph["rounds"].append(res)
        out(f"race round {rnd}: " + trunc(res, 1200))

    # tight loop: alternate POSTs of 3 and 4 seams while hammering GET, count non-200s
    stop = threading.Event()
    counts = {"get_200": 0, "get_other": 0, "post_200": 0, "post_other": 0, "errors": []}

    def hammer_get():
        c = app.test_client()
        while not stop.is_set():
            r = c.get("/api/sheets")
            if r.status_code == 200:
                counts["get_200"] += 1
            else:
                counts["get_other"] += 1
                counts["errors"].append(("GET", r.status_code, r.get_data(as_text=True)[:300]))

    def hammer_hover():
        c = app.test_client()
        n = 0
        while not stop.is_set():
            r = c.post("/api/seam/hover", json=dict(PAGE_SETTINGS, mode="across", angle_deg=None, world=False, point_flat=[700.0 - (n % 50), -800.0]))
            n += 1
            counts.setdefault("hover_200", 0)
            counts.setdefault("hover_other", 0)
            if r.status_code == 200:
                counts["hover_200"] += 1
            else:
                counts["hover_other"] += 1
                counts["errors"].append(("HOVER", r.status_code, r.get_data(as_text=True)[:300]))

    tg = threading.Thread(target=hammer_get)
    thv = threading.Thread(target=hammer_hover)
    tg.start(); thv.start()
    c = app.test_client()
    t0 = time.time()
    i = 0
    while time.time() - t0 < 25:
        sent = three if i % 2 == 0 else four
        r = c.post("/api/sheets/seams", json=dict(PAGE_SETTINGS, seams=copy.deepcopy(sent)))
        if r.status_code == 200:
            counts["post_200"] += 1
        else:
            counts["post_other"] += 1
            counts["errors"].append(("POST", r.status_code, r.get_data(as_text=True)[:300]))
        i += 1
    stop.set(); tg.join(); thv.join()
    ph["hammer_25s"] = counts
    ph["disk_count_end"] = len(disk_seams() or [])
    out("hammer: " + trunc(counts, 1500))
    return ph


def phase_busy(client, app) -> dict:
    ph: dict = {}
    reset_run(keep_backups=False)
    ph["pre_backup_files"] = sorted(p.name for p in RUN.glob("seams_previous*.json"))
    pre_seams = disk_bytes("seams.json")
    st, before, _ = req(client, "GET", "/api/sheets", note="(busy: page state before pressing the button)")
    four = page_seams(before["seams"])
    three = four[:3]
    st, data, _ = req(client, "POST", "/api/seams/optimise", dict(PAGE_SETTINGS, time_budget_s=25), note="(busy: optimise, 25 s budget)")
    ph["optimise_post"] = {"status": st, "body": data}
    job_id = data["job_id"]
    time.sleep(3.0)
    probes = {}
    st, d, dt = req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=copy.deepcopy(three)), note="(busy: remove a seam mid-search)")
    probes["POST /api/sheets/seams (3 seams)"] = {"status": st, "error": d.get("error") if isinstance(d, dict) else None, "seam_count": len(d.get("seams") or []) if isinstance(d, dict) and st == 200 else None, "elapsed_s": round(dt, 2)}
    probes["disk after mid-search POST"] = [s.get("seam_id") for s in (disk_seams() or [])]
    st, d, dt = req(client, "GET", "/api/sheets", note="(busy: GET mid-search)")
    probes["GET /api/sheets"] = {"status": st, "seam_count": len(d.get("seams") or []) if isinstance(d, dict) else None, "plan_status": d.get("status") if isinstance(d, dict) else None}
    st, d, dt = req(client, "POST", "/api/seam/hover", dict(PAGE_SETTINGS, mode="across", angle_deg=None, world=False, point_flat=[700.0, -800.0]), note="(busy: hover mid-search)")
    probes["POST /api/seam/hover"] = {"status": st, "segments": len(d.get("segments") or []) if isinstance(d, dict) else d}
    for url, body in (("/api/sheets/export", dict(PAGE_SETTINGS)), ("/api/run/open", {"run_id": RUN_ID}),
                      ("/api/seams/optimise", dict(PAGE_SETTINGS)), ("/api/run/autofit", {})):
        st, d, dt = req(client, "POST", url, body, note="(busy: mid-search)")
        probes[f"POST {url}"] = {"status": st, "error": d.get("error") if isinstance(d, dict) else d}
    st, d, _ = req(client, "GET", "/api/state", note="(busy: state mid-search)")
    probes["GET /api/state active_job"] = (d.get("active_job") or {}).get("kind") if isinstance(d, dict) else None
    ph["mid_search"] = probes
    out("mid-search probes: " + trunc(probes, 1500))
    job = wait_job(client, job_id, timeout=600)
    ph["job"] = {k: job.get(k) for k in ("status", "wall_s", "elapsed_s", "error")}
    ph["result"] = job.get("result")
    ph["job_log"] = job["log"]
    ph["post_backup_files"] = sorted(p.name for p in RUN.glob("seams_previous*.json"))
    backup = json.loads(disk_bytes("seams_previous.json").decode("utf-8")) if disk_bytes("seams_previous.json") else None
    ph["backup_seam_ids"] = [s.get("seam_id") for s in (backup or {}).get("seams", [])]
    ph["backup_count"] = len((backup or {}).get("seams", []))
    ph["backup_equals_pre_press_seams_json"] = disk_bytes("seams_previous.json") == pre_seams
    ph["after_disk_ids"] = [s.get("seam_id") for s in (disk_seams() or [])]
    ph["seams_replaced_reported"] = (job.get("result") or {}).get("seams_replaced")
    out("busy result: " + trunc({k: ph[k] for k in ("post_backup_files", "backup_seam_ids", "backup_equals_pre_press_seams_json", "after_disk_ids", "seams_replaced_reported")}, 800))
    # undo as the page would: restores the backup -> is the 4-seam set the user had when pressing recoverable?
    st, saved, _ = req(client, "GET", f"/api/file/{RUN_ID}/seams_previous.json?inline=1", note="(busy: undo read)")
    restored = [dict(s) for s in (saved.get("seams") or [])] if isinstance(saved, dict) else []
    st, undone, _ = req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=restored), note="(busy: undo POST)")
    ph["undo_restored_ids"] = [s.get("seam_id") for s in (undone.get("seams") or [])] if isinstance(undone, dict) else undone
    ph["undo_restored_count_vs_pre_press_4"] = (len(restored), 4)
    # second press from a state with an existing seams_previous.json: does it rotate to _1?
    st, _d, _ = req(client, "POST", "/api/sheets/seams", dict(PAGE_SETTINGS, seams=copy.deepcopy(four)), note="(busy: put the 4 seams back for a second press)")
    before_second = sorted(p.name for p in RUN.glob("seams_previous*.json"))
    prev_bytes = disk_bytes("seams_previous.json")
    st, data, _ = req(client, "POST", "/api/seams/optimise", dict(PAGE_SETTINGS, time_budget_s=20), note="(second press, 20 s)")
    if st == 200:
        job2 = wait_job(client, data["job_id"], timeout=600)
        ph["second_press"] = {"result_improved": (job2.get("result") or {}).get("improved"),
                              "seams_written": (job2.get("result") or {}).get("seams_written"),
                              "backup_files_before": before_second,
                              "backup_files_after": sorted(p.name for p in RUN.glob("seams_previous*.json")),
                              "seams_previous_1_equals_old_backup": disk_bytes("seams_previous_1.json") == prev_bytes,
                              "log_tail": job2["log"][-6:]}
        out("second press: " + trunc(ph["second_press"], 1000))
    return ph


# ============================================================================ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="open,get,place,delete,validate,export,optimise,race,busy")
    ap.add_argument("--optimise-budget", type=float, default=None, help="time_budget_s for the optimise phase (default: let the server use its own 90 s)")
    ap.add_argument("--no-reset", action="store_true", help="do not reset the run copy before opening")
    args = ap.parse_args()
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]

    out("=" * 78)
    out(f"probe start; phases={phases}; RUNS_DIR={settings.RUNS_DIR}")
    snaps_before = {"real_run": snapshot(REAL_RUN), "engine_cache": snapshot(ENGINE_CACHE), "app_preview_cache": snapshot(APP_PREVIEW_CACHE)}
    real_seams_sha_before = sha(REAL_RUN / "seams.json")
    if not args.no_reset:
        reset_run(keep_backups=True)

    app = create_app()
    client = app.test_client()
    st, runs, _ = req(client, "GET", "/api/runs")
    RESULTS["runs_listed"] = [(r.get("run_id"), r.get("run_dir"), r.get("input_exists")) for r in runs.get("runs", [])]
    assert all(str(r.get("run_dir", "")).startswith(str(RUNS)) for r in runs.get("runs", [])), RESULTS["runs_listed"]
    out("runs listed: " + trunc(RESULTS["runs_listed"]))

    t_all = time.time()
    for name in phases:
        out("-" * 78)
        out(f"PHASE {name}")
        t0 = time.time()
        try:
            if name == "open":
                RESULTS["phases"][name] = phase_open(client)
            elif name == "get":
                RESULTS["phases"][name] = phase_get(client)
            elif name == "place":
                RESULTS["phases"][name] = phase_place(client)
            elif name == "delete":
                RESULTS["phases"][name] = phase_delete(client)
            elif name == "validate":
                RESULTS["phases"][name] = phase_validate(client)
            elif name == "export":
                RESULTS["phases"][name] = phase_export(client)
            elif name == "optimise":
                RESULTS["phases"][name] = phase_optimise(client, args.optimise_budget)
            elif name == "race":
                RESULTS["phases"][name] = phase_race(app)
            elif name == "busy":
                RESULTS["phases"][name] = phase_busy(client, app)
            else:
                out(f"unknown phase {name}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            RESULTS["phases"][name] = {"EXCEPTION": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}
            out(f"PHASE {name} EXCEPTION: {exc}\n{traceback.format_exc()}")
        RESULTS["phases"].setdefault(name, {})["phase_wall_s"] = round(time.time() - t0, 1)
        (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1, default=str), encoding="utf-8")

    snaps_after = {"real_run": snapshot(REAL_RUN), "engine_cache": snapshot(ENGINE_CACHE), "app_preview_cache": snapshot(APP_PREVIEW_CACHE)}
    diff = {}
    for key in snaps_before:
        b, a = snaps_before[key], snaps_after[key]
        changed = sorted(set(k for k in set(b) | set(a) if b.get(k) != a.get(k)))
        diff[key] = {"files_before": len(b), "files_after": len(a), "changed": changed}
    RESULTS["repo_untouched_check"] = diff
    RESULTS["real_seams_sha_before_after"] = (real_seams_sha_before, sha(REAL_RUN / "seams.json"))
    RESULTS["total_wall_s"] = round(time.time() - t_all, 1)
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1, default=str), encoding="utf-8")
    out("repo untouched check: " + json.dumps(diff))
    out(f"real seams.json sha before/after: {RESULTS['real_seams_sha_before_after']}")
    out(f"done in {RESULTS['total_wall_s']}s; results at {OUT / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
