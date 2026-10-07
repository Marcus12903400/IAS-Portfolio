import json, math
from pathlib import Path
from autodeck2 import sheets

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
def attempt(label, payload):
    try:
        s = sheets.Seam.from_dict(payload)
        print(f"OK   {label}: {s}")
    except Exception as e:
        print(f"FAIL {label}: {type(e).__name__}: {e}")
attempt("old schema", {"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "panel_id": None})
attempt("strings for numbers", {"seam_id": "s1", "x1": "1.5", "y1": "2", "x2": "3", "y2": "4", "panel_id": "2", "snap": "false", "angle_deg": "12"})
attempt("missing y2", {"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3})
attempt("mode uppercase", {"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "mode": "ALONG"})
attempt("mode None", {"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "mode": None})
attempt("raw 3 values", {"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "raw": [1, 2, 3]})
attempt("raw strings", {"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "raw": ["1", "2", "3", "4"]})
attempt("NaN coordinate", {"seam_id": "s1", "x1": float("nan"), "y1": 2, "x2": 3, "y2": 4})
attempt("panel_id float 1.0", {"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "panel_id": 1.0})
attempt("panel_id 'abc'", {"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "panel_id": "abc"})
attempt("seam_id None", {"seam_id": None, "x1": 1, "y1": 2, "x2": 3, "y2": 4})
attempt("seam_id int", {"seam_id": 7, "x1": 1, "y1": 2, "x2": 3, "y2": 4})
# round trip through json text
s = sheets.Seam("s1", 110.61724090576172, -1005.9564819335938, 86.17920684814453, -453.04376220703125, None, True, (0.1, 0.2, 1/3, 2/3), "angle", 12.345678901234567)
txt = json.dumps({"seams": [s.to_dict()]}, indent=2)
back = sheets.Seam.from_dict(json.loads(txt)["seams"][0])
print("json round trip identical:", back == s)
# moved_to preserves raw as the drawing
m = s.moved_to(1, 2, 3, 4)
print("moved_to raw:", m.raw, "drawn:", m.drawn)
old = sheets.Seam.from_dict({"seam_id": "s1", "x1": 1, "y1": 2, "x2": 3, "y2": 4})
print("old-schema seam moved_to raw:", old.moved_to(9, 9, 9, 9).raw)
# write_seams: bytes/newlines/encoding
d = S / "ws"; d.mkdir(exist_ok=True)
p = sheets.write_seams(d, [s])
data = p.read_bytes()
print("write_seams bytes:", len(data), "CRLF count:", data.count(b"\r\n"), "LF count:", data.count(b"\n"), "trailing newline:", data.endswith(b"\n"))
# truncated file read
(d / "seams.json").write_bytes(data[: len(data) // 2])
try:
    sheets.read_seams(d)
except Exception as e:
    print("read_seams on half-written file:", type(e).__name__, "isinstance ValueError:", isinstance(e, ValueError))
# empty file
(d / "seams.json").write_bytes(b"")
try:
    sheets.read_seams(d)
except Exception as e:
    print("read_seams on empty file:", type(e).__name__)
# top-level list
(d / "seams.json").write_text("[]", encoding="utf-8")
try:
    print("read_seams on top-level list:", sheets.read_seams(d))
except Exception as e:
    print("read_seams on top-level list:", type(e).__name__, e)
# BOM
(d / "seams.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"seams": []}).encode())
try:
    print("read_seams with UTF-8 BOM:", sheets.read_seams(d))
except Exception as e:
    print("read_seams with UTF-8 BOM:", type(e).__name__, e)
# duplicate seam ids: what does to_dict/from_dict do? nothing; just note
# settings validation gaps
from autodeck2.config import load_config
config = load_config()
for key, val in (("sample_step_mm", 0.0), ("sample_step_mm", -1.0), ("nest_step_mm", 0.0), ("part_spacing_mm", -5.0), ("min_piece_area_mm2", -1.0), ("seam_gap_mm", 1e-12), ("max_part_width_mm", -5.0)):
    try:
        sheets.settings({**config, "sheets": {**(config.get("sheets") or {}), key: val}})
        print(f"settings accepts {key}={val}")
    except Exception as e:
        print(f"settings rejects {key}={val}: {e}")
print("extended() of zero-length seam:", sheets.Seam("z", 1, 1, 1, 1).extended(100).wkt, "buffered area:", sheets.Seam("z", 1, 1, 1, 1).extended(100).buffer(3, cap_style=2, join_style=2).area)
