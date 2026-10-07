from pathlib import Path
import ezdxf
import numpy as np
from autodeck2 import sheets

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
path = S / "layers_probe.dxf"
doc = ezdxf.new("R2010", setup=True)
msp = doc.modelspace()
def layer(name):
    if name not in doc.layers: doc.layers.add(name)
    return {"layer": name}
sq = [(0, 0), (100, 0), (100, 100), (0, 100)]
# (ezdxf refuses ":" in a layer name, so the AUTO_CAM::PANEL_n form cannot occur in a DXF)
msp.add_lwpolyline(sq, format="xy", close=True, dxfattribs=layer("CAM__PANEL_12"))          # id above 9
msp.add_lwpolyline(sq, format="xy", close=True, dxfattribs=layer("CAM_USER__PANEL_3"))
msp.add_circle((50, 50), 20, dxfattribs=layer("CAM_USER__PANEL_3"))                          # CIRCLE entity as cut-out
msp.add_lwpolyline([(0, 0), (100, 0), (100, 100)], format="xy", close=False, dxfattribs=layer("CAM_USER__PANEL_4"))  # open 3-vertex
msp.add_lwpolyline([(0, 0, 0, 0, 1.0), (100, 0, 0, 0, 0.0)], format="xyseb", close=True, dxfattribs=layer("CAM_USER__PANEL_5"))  # D shape: one bulge
msp.add_lwpolyline(sq, format="xy", close=True, dxfattribs=layer("NOTES__PANEL_1"))          # any prefix with the suffix
msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], format="xy", close=True, dxfattribs=layer("CAM_USER__PANEL_1_OUTER"))
msp.add_line((0, 10), (100, 10), dxfattribs=layer("PATTERN_HEX__PANEL_12"))
msp.add_line((0, 20), (100, 20), dxfattribs=layer("PATTERN_TEAK__PANEL_3"))
msp.add_arc((50, 50), 30, 0, 180, dxfattribs=layer("CAM_USER__PANEL_6"))                     # ARC entity
msp.add_lwpolyline([(0, 0), (100, 0)], format="xy", close=True, dxfattribs=layer("CAM_USER__PANEL_7"))  # 2 straight verts
doc.saveas(path)
loops, pattern, kind = sheets.read_fitted_dxf(path)
print("kind:", kind)
for pid in sorted(loops):
    print("panel", pid, "loops", len(loops[pid]), "vertex counts", [len(l.vertices) for l in loops[pid]])
print("pattern panels:", {k: len(v) for k, v in pattern.items()})
print("NOT read: AUTO_CAM::PANEL_1 (colons)?", 1 in loops and len(loops[1]), "| CIRCLE on panel 3 counted?", len(loops.get(3, [])), "| open polyline panel 4:", len(loops.get(4, [])), "| D-shape panel 5:", len(loops.get(5, [])), "| ARC panel 6:", len(loops.get(6, [])), "| 2 straight verts panel 7:", len(loops.get(7, [])))
