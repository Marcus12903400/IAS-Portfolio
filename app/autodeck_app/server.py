"""The local HTTP API behind the review page."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from . import __version__, bridge, meshview, settings
from .jobs import JobManager

STATIC = Path(__file__).parent / "static"
SAFE_FILES = {"outline.3dm", "outline.dxf", "auto_cam.3dm", "final_auto.dxf", "final.dxf", "outline_report.md",
              "autofit_report.md", "final_report.md", "calibration_report.md", "run.json", "autofit.json", "panels.json",
              "sheets.json", "sheet_report.md", "seams.json"}
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
        data = request.get_json(silent=True) or {}
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
        folder = (request.get_json(silent=True) or {}).get("folder")
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
        data = request.get_json(silent=True) or {}
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
        data = request.get_json(silent=True) or {}
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
    def sheet_options(data: dict[str, Any]) -> dict[str, Any]:
        """Per-request overrides for the sheet settings the page exposes."""

        overrides: dict[str, Any] = {}
        for key, cast in (("part_spacing_mm", float), ("seam_gap_mm", float),
                          ("grain_angle_deg", float), ("nest_step_mm", float)):
            if data.get(key) not in (None, ""):
                try:
                    overrides[key] = cast(data[key])
                except (TypeError, ValueError):
                    abort(400, f"{key} must be a number")
        if data.get("allow_180_rotation") is not None:
            overrides["allow_180_rotation"] = bool(data["allow_180_rotation"])
        return overrides

    @app.get("/api/sheets")
    def api_sheets():
        """The current seams and, if geometry exists, the nested sheet layout."""

        run_dir = current_run_dir()
        result = bridge.sheet_preview(run_dir, {})
        return jsonify(result)

    @app.post("/api/sheets/seams")
    def api_sheets_seams():
        """Replace the seam set and re-plan. Cheap enough to call on every edit."""

        run_dir = current_run_dir()
        data = request.get_json(silent=True) or {}
        seams = data.get("seams")
        if not isinstance(seams, list):
            abort(400, "seams must be a list")
        try:
            result = bridge.sheet_preview(run_dir, sheet_options(data), seams=seams, save=True)
        except (ValueError, KeyError, TypeError) as exc:
            abort(400, f"bad seam data: {exc}")
        return jsonify(result)

    @app.post("/api/sheets/export")
    def api_sheets_export():
        """Write one DXF per sheet."""

        busy_guard()
        run_dir = current_run_dir()
        data = request.get_json(silent=True) or {}
        options = sheet_options(data)

        def fn(log):
            return bridge.job_sheets(run_dir, options, log)

        job = jobs.start("sheets", fn, {"run_id": run_dir.name})
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
                and not _SHEET_DXF.match(name)):
            abort(404)
        path = run_dir / name
        if not path.is_file() or ".." in run_id:
            abort(404)
        inline = request.args.get("inline") == "1"
        if inline and name.endswith((".md", ".json")):
            return Response(path.read_text(encoding="utf-8"), mimetype="text/plain")
        return send_file(str(path), as_attachment=not inline, download_name=name)

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(500)
    def api_error(error):
        return jsonify({"error": getattr(error, "description", str(error))}), getattr(error, "code", 500)

    return app
