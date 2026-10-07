from werkzeug.exceptions import HTTPException
from flask import Flask, jsonify
from autodeck_app import server

def try_(label, fn):
    try:
        print(f"{label}: OK -> {fn()}")
    except HTTPException as e:
        print(f"{label}: HTTP {e.code} '{e.description}'")

try_("sheet_options seam_gap_mm=0", lambda: server.sheet_options({"seam_gap_mm": 0}))
try_("sheet_options seam_gap_mm='0'", lambda: server.sheet_options({"seam_gap_mm": "0"}))
try_("sheet_options seam_snap_enabled='maybe'", lambda: server.sheet_options({"seam_snap_enabled": "maybe"}))
try_("sheet_options grain ''", lambda: server.sheet_options({"grain_angle_deg": ""}))
odd = [{"x1": 1, "y1": 2, "x2": 3, "y2": 4, "seam_id": {"a": 1}, "panel_id": "abc", "mode": "diag", "snap": "maybe"}]
try_("checked_seams odd types", lambda: [list(s.keys()) for s in server.checked_seams(odd)])
dup = [{"x1": 1, "y1": 2, "x2": 3, "y2": 4, "seam_id": "s4"}, {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "seam_id": "s4"}]
try_("checked_seams duplicate ids", lambda: len(server.checked_seams(dup)))
try_("checked_seams raw None ok", lambda: len(server.checked_seams([{"x1": 1, "y1": 2, "x2": 3, "y2": 4, "raw": None}])))

# What an unhandled exception inside a route looks like through the same errorhandler the app registers.
app = Flask("probe")
@app.get("/boom")
def boom():
    raise RuntimeError("shapely blew up")
@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(500)
def api_error(error):
    return jsonify({"error": getattr(error, "description", str(error))}), getattr(error, "code", 500)
with app.test_client() as c:
    r = c.get("/boom")
    print("unhandled exception ->", r.status_code, r.get_json())
