"""The local HTTP API behind the review page."""

from __future__ import annotations

import math
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from . import __version__, bridge, meshview, settings
from . import jobs as jobs_module
from .jobs import JobManager

STATIC = Path(__file__).parent / "static"
SAFE_FILES = {"outline.3dm", "outline.dxf", "auto_cam.3dm", "final_auto.dxf", "final.dxf", "outline_report.md",
              "autofit_report.md", "final_report.md", "calibration_report.md", "run.json", "autofit.json", "panels.json",
              "sheets.json", "sheet_report.md", "seams.json",
              # The copies the seam optimiser takes before it replaces anything.
              # They have to be downloadable or a hand-placed seam set that the
              # button improved on cannot be got back -- and that goes for the
              # numbered older copies and the first hand-placed set exactly as
              # much as for the newest one.
              "seams_previous.json", "seams_hand.json"}
# seams_previous_N.json -- the older backups, stepped aside by later presses.
_SEAMS_BACKUP_FILE = re.compile(r"^seams_previous_\d{1,3}\.json$")
_SHEET_DXF = re.compile(r"^sheet_\d{2,3}\.dxf$")
_PICKER = (
    "import tkinter as tk, tkinter.filedialog as fd\n"
    "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)\n"
    "print(fd.askopenfilename(title='Choose a 3D scan (OBJ)', filetypes=[('OBJ mesh', '*.obj'), ('All files', '*')]))\n"
)
_FOLDER_PICKER = (
    "import tkinter as tk, tkinter.filedialog as fd\n"
    "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)\n"
    "print(fd.askdirectory(title='Choose a scan folder (OBJ + MTL + textures)'))\n"
)


# Every sheet setting the page may override, with the range that is worth
# cutting with.  The upper bounds are not fussiness: `seam_snap_max_move_mm` is
# the guard that stops a snap teleporting a seam, `seam_gap_mm` is a real gap
# between two real pieces, and a number a thousand times too big in either
# silently produces a wrong cut file rather than an error.
_SHEET_NUMBERS: dict[str, tuple[float, float]] = {
    # The piece gap floor is 1 mm rather than 0: the nester treats two touching
    # pieces as a collision anyway, so a 0 the user can type is a number the
    # program cannot honour and DXFs that quietly come out tighter than asked.
    # The real floor is the router bit diameter plus clearance, which only the
    # shop knows; 1 mm only stops the lie.
    "part_spacing_mm": (1.0, 500.0),
    # A seam gap below 2 mm is a kerf no blade and no router bit makes, and a
    # 0 used to take the whole seam tool down with a 400 on every hover --
    # including remove and Clear all, the two controls that must always work.
    "seam_gap_mm": (2.0, 100.0),
    "nest_step_mm": (0.5, 200.0),
    # A direction, so any angle is legal; outside a turn it is a typo.
    "grain_angle_deg": (-360.0, 360.0),
    # How far off square a seam may be drawn and still be taken as meant to be
    # square. The two masters are ninety degrees apart, so no seam is ever more
    # than forty-five from the NEARER of them: at forty-five every seam is
    # already claimed, and a bigger number cannot reach further -- it could only
    # be read as reaching past the halfway line to the wrong master, which is
    # how "60" here used to turn an almost-across seam into an along-boat one.
    "seam_axis_snap_deg": (0.0, 45.0),
    "seam_snap_angle_deg": (0.0, 90.0),
    "seam_snap_offset_mm": (0.0, 1000.0),
    "seam_snap_reach_mm": (0.0, 10000.0),
    "seam_snap_min_ref_length_mm": (0.0, 1000.0),
    "seam_snap_min_ref_radius_mm": (0.0, 1000.0),
    "seam_snap_max_move_mm": (0.0, 1000.0),
}

_SHEET_FLAGS = ("allow_180_rotation", "seam_snap_enabled", "seam_axis_priority",
                "seam_snap_use_axis")

# Enough points for a whole drag path in one call, and few enough that a stray
# request cannot make the server chew through a megabyte of picks.
MAX_PICK_POINTS = 64

# A deck this program has ever seen has under a dozen seams, and the seam list
# is written straight to seams.json.  Ten thousand of them took twenty-five
# seconds and left a 2.8 MB file behind, which is not a seam set, it is a stuck
# client.
MAX_SEAMS = 500

# Nothing on any real boat is a kilometre from the origin, and a coordinate that
# is (or is a NaN, or an infinity) reaches the geometry as a number no rule can
# reject and comes back out as the bare token NaN in seams.json -- which is not
# JSON, so every later read of that run fails in the browser and the page cannot
# even write the file back to repair it.  Caught here, once, before anything
# stores it.
MAX_SEAM_COORDINATE_MM = 1e6

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def _as_number(name: str, value: Any) -> float:
    """A finite float, or a 400 saying which field was wrong.

    `bool` is rejected even though Python would happily float() it: a checkbox
    arriving where a millimetre belongs is a bug in the caller, and silently
    reading it as 1 mm would be worse than saying so.
    """

    if isinstance(value, bool) or isinstance(value, (list, dict)):
        abort(400, f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        abort(400, f"{name} must be a number")
    if not math.isfinite(number):
        abort(400, f"{name} must be a real number")
    return number


def _as_flag(name: str, value: Any) -> bool:
    """A real yes/no.

    Plain bool() is not usable here: bool("false") is True, so a checkbox sent
    as a string would turn snapping ON when the user turned it off.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        if value.strip().lower() in _TRUE:
            return True
        if value.strip().lower() in _FALSE:
            return False
    abort(400, f"{name} must be true or false")


def _as_points(value: Any, width: int, name: str, single: bool = False) -> list[list[float]]:
    """A list of xy or xyz points, checked before any of it reaches numpy.

    numpy would turn a ragged list into an object array and fail somewhere deep
    with a 500; the caller gets a sentence naming the field instead.
    """

    if single:
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        abort(400, f"{name} must be a non-empty list of {width}-number points")
    if len(value) > MAX_PICK_POINTS:
        abort(400, f"{name} is limited to {MAX_PICK_POINTS} points per call")
    points: list[list[float]] = []
    for index, item in enumerate(value):
        if not isinstance(item, (list, tuple)) or len(item) != width:
            abort(400, f"{name}[{index}] must be {width} numbers")
        points.append([_as_number(f"{name}[{index}]", coordinate) for coordinate in item])
    return points


_SEAM_MODES = ("", "along", "across", "angle")


def checked_seams(value: Any) -> list[dict[str, Any]]:
    """The posted seam list, with every field proved fit to store.

    This endpoint writes what it is given straight to seams.json, so a coordinate
    that is not a finite number does not merely produce a bad seam: `json.dump`
    writes the bare token `NaN`, which is not valid JSON, and from then on the
    page cannot read the run at all -- not even to write a repaired file back.
    The run has to be fixed by hand in a text editor.  One check here is the
    difference between a 400 and a bricked job.

    The rest of the table is every way a bad row used to be stored and then
    misbehaved downstream: a duplicate id let one seam overwrite another when a
    reply was merged by id; a seam bound to a panel that does not exist cuts
    nothing and confuses the corrector's panel filter; a zero-length seam is
    dropped by everything downstream while still showing in the list; a word in
    `snap` reads as true through `bool()`; and an angle that is not a number
    breaks the hover for the seam's whole run.
    """

    if not isinstance(value, list):
        abort(400, "seams must be a list")
    if len(value) > MAX_SEAMS:
        abort(400, f"that is {len(value)} seams; {MAX_SEAMS} is the most one deck may have")
    seen_ids: set[str] = set()
    for index, seam in enumerate(value):
        if not isinstance(seam, dict):
            abort(400, f"seams[{index}] must be an object")
        seam_id = str(seam.get("seam_id") or "")
        if seam_id:
            if seam_id in seen_ids:
                abort(400, f"seams[{index}] repeats seam_id {seam_id!r}; each seam needs its own name")
            seen_ids.add(seam_id)
        for key in ("x1", "y1", "x2", "y2"):
            if key not in seam:
                abort(400, f"seams[{index}] is missing {key}")
            number = _as_number(f"seams[{index}].{key}", seam[key])
            if abs(number) > MAX_SEAM_COORDINATE_MM:
                abort(400, f"seams[{index}].{key} is {number:g} mm, which is off the boat")
        if (float(seam["x1"]) == float(seam["x2"]) and float(seam["y1"]) == float(seam["y2"])):
            abort(400, f"seams[{index}] has zero length -- a seam has to run somewhere")
        if "panel_id" in seam and seam["panel_id"] not in (None, ""):
            panel = seam["panel_id"]
            if isinstance(panel, bool) or not isinstance(panel, (int, float)) or int(panel) != panel or int(panel) < 0:
                abort(400, f"seams[{index}].panel_id must be a whole panel number")
        if "snap" in seam and seam["snap"] is not None and not isinstance(seam["snap"], bool):
            # The words are read by the FILE reader, which has to tolerate old
            # files; a request from this page has no excuse and gets the 400.
            if not (isinstance(seam["snap"], str) and seam["snap"].strip().lower() in _TRUE | _FALSE):
                abort(400, f"seams[{index}].snap must be true or false")
        mode = str(seam.get("mode") or "")
        if mode not in _SEAM_MODES:
            abort(400, f"seams[{index}].mode must be along, across, angle or empty")
        if mode == "angle":
            if seam.get("angle_deg") in (None, ""):
                abort(400, f"seams[{index}] is an angle seam but carries no angle_deg")
            _as_number(f"seams[{index}].angle_deg", seam["angle_deg"])
        elif "angle_deg" in seam and seam["angle_deg"] not in (None, ""):
            _as_number(f"seams[{index}].angle_deg", seam["angle_deg"])
        raw = seam.get("raw")
        if raw is None:
            continue
        # `raw` is the line the user drew, and every later correction is
        # recomputed from it -- so it reaches the file just as surely as the
        # endpoints do and needs exactly the same guard.
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            abort(400, f"seams[{index}].raw must be four numbers")
        for position, coordinate in enumerate(raw):
            number = _as_number(f"seams[{index}].raw[{position}]", coordinate)
            if abs(number) > MAX_SEAM_COORDINATE_MM:
                abort(400, f"seams[{index}].raw[{position}] is {number:g} mm, which is off the boat")
    return value


def body(request_obj: Any) -> dict[str, Any]:
    """The request's JSON as an object, whatever arrived.

    A JSON array, string, number or `true` is truthy, so `get_json() or {}` let
    it straight through to `.get(...)` and every POST answered a hostile body
    with a 500 and a traceback instead of a sentence.
    """

    data = request_obj.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        abort(400, "the request body must be a JSON object")
    return data


def sheet_options(data: dict[str, Any]) -> dict[str, Any]:
    """Per-request overrides for the sheet settings the page exposes.

    Every value is range checked here rather than deeper down, because a number
    that is merely absurd -- a 400 mm seam gap, a snap allowed to teleport a seam
    a hundred metres -- does not raise anywhere: it produces a plausible looking
    cut file that is wrong.  Anything unusable is a 400 naming the field, never a
    500 out of the geometry.

    A key sent as null or "" means "leave it at the default", which is how the
    page clears the manual grain angle; a key sent as a word is a 400.

    Module level, not a closure inside `create_app`, so the whole validation
    table can be tested without standing a server up and opening a run.
    """

    overrides: dict[str, Any] = {}
    for key, (low, high) in _SHEET_NUMBERS.items():
        if data.get(key) in (None, ""):
            continue
        value = _as_number(key, data[key])
        if not low <= value <= high:
            abort(400, f"{key} must be between {low:g} and {high:g}")
        overrides[key] = value
    for key in _SHEET_FLAGS:
        if data.get(key) is None:
            continue
        overrides[key] = _as_flag(key, data[key])
    return overrides


def resolve_scan_folder(folder: Path) -> Path:
    """The OBJ to load from a scan folder.

    A photogrammetry export is a folder -- mesh, .mtl and one or more texture
    images -- so the whole folder is the natural unit to open.  Picks the
    largest .obj, which is the mesh rather than any stray decimated proxy, and
    searches one level down for the common `<name>/<name>.obj` layout.
    """

    candidates = sorted(folder.glob("*.obj"))
    if not candidates:
        candidates = sorted(folder.glob("*/*.obj"))
    if not candidates:
        abort(400, f"no .obj file in {folder}")
    return max(candidates, key=lambda p: p.stat().st_size)


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 ** 3
    jobs = JobManager()
    lock = threading.Lock()
    state: dict[str, Any] = {"scan": None, "run_id": None, "view": None, "overlays": None}

    # ------------------------------------------------------------------ helpers
    def scan_summary() -> dict[str, Any] | None:
        scan = state["scan"]
        if scan is None:
            return None
        return {k: v for k, v in scan.items() if k != "preview" or v is None or isinstance(v, dict)}

    def register_scan(path: Path) -> tuple[dict[str, Any], Any]:
        scan = {"path": str(path), "name": path.name, "size_bytes": path.stat().st_size, "sha256": None,
                "units_detected": None, "preview_ready": False, "preview": None}
        with lock:
            state["scan"] = scan

        def fn(log):
            sha = meshview.sha256_file(path, log)
            scan["sha256"] = sha
            meta = meshview.build_preview(path, sha, log)
            scan.update(preview=meta, units_detected=meta.get("units_detected"), preview_ready=True)
            return {"preview_faces": meta["preview_faces"], "units_detected": meta.get("units_detected")}

        job = jobs.start("preview", fn, {"scan": path.name})
        return scan, job

    def load_view(run_dir: Path, log) -> dict[str, Any]:
        view = bridge.load_run(run_dir, log)
        log("Building overlays")
        overlays = bridge.overlays(view, log)
        with lock:
            state.update(run_id=run_dir.name, view=view, overlays=overlays)
        # Built here, while a progress log is on screen, rather than on the first
        # pointer move: the search tree for a full-resolution deck panel takes
        # about a second, and paying for it during the seam hover would freeze
        # the pointer exactly when the user is trying to aim a seam.
        log("Preparing the deck for picking and seam placement")
        bridge.warm_picking(view)
        log(f"Run {run_dir.name} ready: {len(overlays['layers'])} layers")
        return overlays

    def ensure_preview_for(input_path: Path, sha_hint: str | None, log) -> None:
        scan = state["scan"]
        if scan is not None and scan["path"] == str(input_path) and scan.get("preview_ready"):
            return
        scan = {"path": str(input_path), "name": input_path.name, "size_bytes": input_path.stat().st_size, "sha256": sha_hint,
                "units_detected": None, "preview_ready": False, "preview": None}
        with lock:
            state["scan"] = scan
        sha = sha_hint or meshview.sha256_file(input_path, log)
        scan["sha256"] = sha
        meta = meshview.build_preview(input_path, sha, log)
        scan.update(preview=meta, units_detected=meta.get("units_detected"), preview_ready=True)

    def current_run_dir() -> Path:
        run_id = state["run_id"]
        if not run_id:
            abort(400, "no run is open")
        return settings.RUNS_DIR / run_id

    def busy_guard() -> None:
        active = jobs.active()
        if active is not None:
            abort(409, f"a job is already running ({active.kind})")

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/api/state")
    def api_state():
        active = jobs.active()
        run_dir = settings.RUNS_DIR / state["run_id"] if state["run_id"] else None
        return jsonify({
            "version": __version__, "scan": scan_summary(), "run_id": state["run_id"],
            "run_files": bridge.run_files(run_dir) if run_dir else {},
            "overlays_ready": state["overlays"] is not None,
            "active_job": active.to_dict(since=max(0, len(active.log) - 1)) if active else None,
            "runs_dir": str(settings.RUNS_DIR), "inputs_dir": str(settings.INPUTS_DIR),
        })

    # ------------------------------------------------------------------ scans
    @app.post("/api/scan/upload")
    def api_scan_upload():
        busy_guard()
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            abort(400, "no file")
        name = Path(upload.filename).name
        if not name.lower().endswith(".obj"):
            abort(400, "please upload an .obj mesh")
        settings.INPUTS_DIR.mkdir(parents=True, exist_ok=True)
        target = settings.INPUTS_DIR / name
        stem, suffix = target.stem, target.suffix
        counter = 1
        while target.exists():
            target = settings.INPUTS_DIR / f"{stem}-{counter}{suffix}"
            counter += 1
        upload.save(str(target))
        scan, job = register_scan(target)
        return jsonify({"scan": scan_summary(), "job_id": job.job_id})

    @app.post("/api/scan/path")
    def api_scan_path():
        busy_guard()
        data = body(request)
        path = Path(str(data.get("path", ""))).expanduser()
        if path.is_dir():
            path = resolve_scan_folder(path)
        if not path.is_file():
            abort(400, f"not a file: {path}")
        scan, job = register_scan(path)
        return jsonify({"scan": scan_summary(), "job_id": job.job_id})

    @app.post("/api/scan/pick")
    def api_scan_pick():
        busy_guard()
        folder = body(request).get("folder")
        script = _FOLDER_PICKER if folder else _PICKER
        try:
            proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            abort(500, f"file picker unavailable ({exc}); drop the file onto the page instead")
        chosen = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if proc.returncode != 0 or not chosen:
            if proc.returncode != 0:
                abort(500, "file picker unavailable on this Python (no Tk); drop the file onto the page instead")
            return jsonify({"cancelled": True})
        path = Path(chosen)
        if path.is_dir():
            path = resolve_scan_folder(path)
        if not path.is_file():
            abort(400, f"not a file: {path}")
        scan, job = register_scan(path)
        return jsonify({"scan": scan_summary(), "job_id": job.job_id})

    @app.get("/api/scan/mesh")
    def api_scan_mesh():
        scan = state["scan"]
        if scan is None or not scan.get("preview_ready") or not scan.get("sha256"):
            abort(404, "preview not ready")
        data = meshview.load_preview_binary(scan["sha256"])
        if data is None:
            abort(404, "preview not ready")
        return Response(data, mimetype="application/octet-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/scan/texture")
    def api_scan_texture():
        """The scan's own diffuse texture, already downscaled for the browser."""

        scan = state["scan"]
        if scan is None or not scan.get("sha256"):
            abort(404, "no scan loaded")
        path = meshview.texture_path(scan["sha256"])
        if not path.is_file():
            abort(404, "this scan has no texture")
        return send_file(str(path), mimetype="image/jpeg")

    # ------------------------------------------------------------------ jobs
    @app.get("/api/jobs/<job_id>")
    def api_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            abort(404, "unknown job")
        since = int(request.args.get("since", 0))
        return jsonify(job.to_dict(since=since))

    # ------------------------------------------------------------------ runs
    @app.get("/api/runs")
    def api_runs():
        return jsonify({"runs": bridge.list_runs()})

    @app.post("/api/run/open")
    def api_run_open():
        busy_guard()
        data = body(request)
        run_id = str(data.get("run_id", ""))
        run_dir = settings.RUNS_DIR / run_id
        if not run_id or not (run_dir / "run.json").is_file():
            abort(400, "unknown run")

        def fn(log):
            log(f"Opening run {run_id}")
            overlays = load_view(run_dir, log)
            view = state["view"]
            ensure_preview_for(Path(view.meta["input_path"]), view.meta.get("input_sha256"), log)
            return {"run_id": run_id, "layers": [layer["id"] for layer in overlays["layers"]]}

        job = jobs.start("open", fn, {"run_id": run_id})
        return jsonify({"job_id": job.job_id})

    @app.post("/api/run/outline")
    def api_run_outline():
        busy_guard()
        scan = state["scan"]
        if scan is None:
            abort(400, "load a scan first")
        data = body(request)
        units = str(data.get("units", "auto"))
        layout_mode = str(data.get("layout", "nest"))
        pattern = str(data.get("pattern", "teak")).lower()
        if pattern not in ("teak", "diamond", "hex", "none"):
            abort(400, "pattern must be teak, diamond, hex or none")
        try:
            pattern_size = float(data["pattern_size"]) if data.get("pattern_size") not in (None, "") else None
        except (TypeError, ValueError):
            abort(400, "pattern size must be a number (mm)")
        scan_path = Path(scan["path"])

        def fn(log):
            summary = bridge.job_outline(scan_path, None if units == "auto" else units, layout_mode, pattern, pattern_size, log)
            for warning in summary.get("warnings", []):
                log(f"warning: {warning}")
            load_view(Path(summary["run_dir"]), log)
            return summary

        job = jobs.start("outline", fn, {"scan": scan["name"]})
        return jsonify({"job_id": job.job_id})

    @app.post("/api/run/autofit")
    def api_run_autofit():
        busy_guard()
        run_dir = current_run_dir()

        def fn(log):
            summary = bridge.job_autofit(run_dir, log)
            load_view(run_dir, log)
            return summary

        job = jobs.start("autofit", fn, {"run_id": run_dir.name})
        return jsonify({"job_id": job.job_id})

    @app.post("/api/run/ingest")
    def api_run_ingest():
        busy_guard()
        run_dir = current_run_dir()
        upload = request.files.get("file")
        if upload is None or not upload.filename or not upload.filename.lower().endswith(".3dm"):
            abort(400, "upload the .3dm you drew on")
        target = run_dir / "drawings"
        target.mkdir(parents=True, exist_ok=True)
        drawing = target / f"{time.strftime('%Y%m%d-%H%M%S')}-{Path(upload.filename).name}"
        upload.save(str(drawing))

        def fn(log):
            summary = bridge.job_ingest(run_dir, drawing, log)
            load_view(run_dir, log)
            return summary

        job = jobs.start("ingest", fn, {"run_id": run_dir.name, "drawing": drawing.name})
        return jsonify({"job_id": job.job_id})

    # ------------------------------------------------------------------ sheets
    @app.get("/api/sheets")
    def api_sheets():
        """The current seams and, if geometry exists, the nested sheet layout."""

        run_dir = current_run_dir()
        result = bridge.sheet_preview(run_dir, {}, view=state["view"])
        return jsonify(result)

    @app.post("/api/sheets/seams")
    def api_sheets_seams():
        """Replace the seam set and re-plan. Cheap enough to call on every edit.

        Every coordinate is proved to be a real number BEFORE anything is
        planned, because this endpoint writes what it is given straight to
        seams.json -- see `checked_seams`.  That check is the difference between
        a 400 and a run the page can never open again.

        Guarded like the other engine jobs: a re-plan races the optimiser for
        the same seams.json, and an edit that lands mid-search used to lose the
        very seams it edited (the search overwrote them from a copy taken
        before the edit existed).  While a job runs, edits answer 409 and the
        page holds them -- "removing seams always works" is about the plan
        state, and the one thing it cannot mean is removing them into a file
        another job is about to clobber.

        `run_id` is echoed by the page so a re-plan queued before a run switch
        cannot write the previous boat's seams into the new run.
        """

        busy_guard()
        run_dir = current_run_dir()
        data = body(request)
        seams = checked_seams(data.get("seams"))
        posted_run = data.get("run_id")
        if posted_run and str(posted_run) != run_dir.name:
            abort(400, f"that request was for run {posted_run}, but {run_dir.name} is the open run")
        try:
            result = bridge.sheet_preview(run_dir, sheet_options(data), seams=seams,
                                          save=True, view=state["view"])
        except (ValueError, KeyError, TypeError) as exc:
            abort(400, f"bad seam data: {exc}")
        return jsonify(result)

    @app.post("/api/seam/hover")
    def api_seam_hover():
        """The seam that WOULD be placed under the pointer, trimmed to the part.

        Called on every pointer move, so it does as little as it can: the panel
        polygons and the resolved boat frame are cached on the open run, and only
        the clip and the lift happen per call.
        """

        view = state["view"]
        if view is None:
            abort(400, "no run is open")
        data = body(request)
        mode = str(data.get("mode") or "across").lower()
        if mode not in ("along", "across", "angle"):
            abort(400, "mode must be along, across or angle")
        angle = None
        if mode == "angle":
            if data.get("angle_deg") in (None, ""):
                abort(400, "angle_deg is required for a diagonal seam")
            angle = _as_number("angle_deg", data["angle_deg"])
            if not -360.0 <= angle <= 360.0:
                abort(400, "angle_deg must be between -360 and 360")
        flat = world = None
        if data.get("point_flat") is not None:
            flat = _as_points(data["point_flat"], 2, "point_flat", single=True)
        elif data.get("point_world") is not None:
            world = _as_points(data["point_world"], 3, "point_world", single=True)
        else:
            abort(400, "send point_flat [x, y] or point_world [x, y, z]")
        try:
            result = bridge.seam_hover(view, sheet_options(data), mode, angle,
                                       point_flat=flat, point_world=world,
                                       with_world=data.get("world", True) is not False)
        except (ValueError, KeyError, TypeError) as exc:
            abort(400, f"bad hover request: {exc}")
        return jsonify(result)

    @app.post("/api/sheets/export")
    def api_sheets_export():
        """Write one DXF per sheet."""

        busy_guard()
        run_dir = current_run_dir()
        data = body(request)
        options = sheet_options(data)

        def fn(log):
            return bridge.job_sheets(run_dir, options, log)

        job = jobs.start("sheets", fn, {"run_id": run_dir.name})
        return jsonify({"job_id": job.job_id})

    @app.post("/api/seams/optimise")
    def api_seams_optimise():
        """Work out the best seam positions for this run, on its own.

        A long job with a live log, started the same way as auto-fit and the
        sheet export, because it takes a minute or so and the page has to stay
        usable while it runs.  It copies the seams that are there to
        seams_previous.json before it writes anything, and writes nothing at all
        unless what it found is better than what is there -- see
        `bridge.job_optimise_seams`.
        """

        busy_guard()
        run_dir = current_run_dir()
        data = body(request)
        options = sheet_options(data)
        budget = bridge.OPTIMISE_BUDGET_S
        if data.get("time_budget_s") not in (None, ""):
            budget = _as_number("time_budget_s", data["time_budget_s"])
            # Under five seconds it cannot look at more than the first
            # arrangement it builds, and ten minutes is longer than anyone will
            # sit in front of it.
            if not 5.0 <= budget <= 600.0:
                abort(400, "time_budget_s must be between 5 and 600 seconds")

        def fn(log):
            return bridge.job_optimise_seams(run_dir, options, log, time_budget_s=budget)

        job = jobs.start("optimise_seams", fn, {"run_id": run_dir.name})
        return jsonify({"job_id": job.job_id})

    @app.get("/api/overlays")
    def api_overlays():
        overlays = state["overlays"]
        if overlays is None:
            abort(404, "no run is open")
        return jsonify(overlays)

    @app.get("/api/file/<run_id>/<name>")
    def api_file(run_id: str, name: str):
        run_dir = settings.RUNS_DIR / run_id
        if (name not in SAFE_FILES
                and not (name.startswith("autofit_panel") and name.endswith(".png"))
                and not _SHEET_DXF.match(name)
                and not _SEAMS_BACKUP_FILE.match(name)):
            abort(404)
        path = run_dir / name
        if not path.is_file() or ".." in run_id:
            abort(404)
        inline = request.args.get("inline") == "1"
        if inline and name.endswith((".md", ".json")):
            return Response(path.read_text(encoding="utf-8"), mimetype="text/plain")
        return send_file(str(path), as_attachment=not inline, download_name=name)

    @app.errorhandler(jobs_module.JobBusy)
    def api_job_busy(error):
        # `JobManager.start` refuses under its own lock when a job is already
        # running; the pre-route guard and the start itself are two steps, and
        # two requests arriving together could once slip between them.
        return jsonify({"error": str(error)}), 409

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(500)
    def api_error(error):
        return jsonify({"error": getattr(error, "description", str(error))}), getattr(error, "code", 500)

    return app
