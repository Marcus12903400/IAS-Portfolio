"""Write the outline deliverable: outline.3dm (Rhino), outline.dxf, panels.json.

Layer contract (per panel n; 1 = primary):
  RAW::PANEL_n::OUTER       locked  dense original detected boundary
  RAW::PANEL_n::OBSTACLES   locked  raw obstacle / void contours on this panel
  RAW::PANEL_n::FEATURES    locked  raw seams / hatches / nonskid edges
  RAW::PANEL_n::HINTS       locked  low-confidence families (dimmed)
  REF::PANEL_n::ROBUST      locked  v1 conditioned (de-noised) curve, grey
  TEAK::PANEL_n             locked  teak lines
  USER_CAM::PANEL_n         UNLOCKED, empty -- draw lines/arcs here
  HINTS::UNASSIGNED         locked  features whose panel could not be decided (world position)
  LABELS                    locked  text dots
  USER_CORNERS              UNLOCKED, empty -- mark intentional sharp corners
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import rhino3dm

from .engine import EngineResult, MappedCurve
from .layout import PanelPlacement, overlaps

COLORS = {
    "OUTER": (0, 0, 0, 255),
    "OBSTACLES": (220, 30, 30, 255),
    "FEATURES": (30, 90, 220, 255),
    "HINTS": (190, 190, 190, 255),
    "ROBUST": (120, 120, 120, 255),
    "TEAK": (150, 100, 40, 255),
    "PATTERN_DIAMOND": (40, 150, 160, 255),
    "PATTERN_HEX": (200, 80, 120, 255),
    "USER_CAM": (0, 170, 60, 255),
    "USER_CORNERS": (200, 0, 200, 255),
    "LABELS": (0, 0, 0, 255),
    "UNASSIGNED": (255, 140, 0, 255),
}

FAMILY_LAYER = {"OUTER": "OUTER", "OBSTACLE": "OBSTACLES", "FEATURE": "FEATURES", "HINT": "HINTS"}


def dxf_layer_name(name: str) -> str:
    """DXF layer names cannot contain ':'; Rhino's '::' becomes '__'."""

    return name.replace("::", "__")


def layer_name(panel_id: int, kind: str) -> str:
    if kind == "ROBUST":
        return f"REF::PANEL_{panel_id}::ROBUST"
    if kind in ("TEAK", "PATTERN_DIAMOND", "PATTERN_HEX"):
        return f"{kind}::PANEL_{panel_id}"
    if kind == "USER_CAM":
        return f"USER_CAM::PANEL_{panel_id}"
    return f"RAW::PANEL_{panel_id}::{kind}"


def pattern_layer_kind(pattern: str) -> str:
    """3dm/dxf layer family for a pattern name ('teak' keeps its v1-era TEAK name)."""

    return {"teak": "TEAK", "diamond": "PATTERN_DIAMOND", "hex": "PATTERN_HEX"}.get(pattern, "TEAK")


class _Model:
    def __init__(self) -> None:
        self.model = rhino3dm.File3dm()
        self.model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Millimeters
        self.model.Settings.ModelAbsoluteTolerance = 0.01
        self.layers: dict[str, int] = {}
        self.counts: dict[str, int] = {}

    def layer(self, name: str, color: tuple[int, int, int, int], locked: bool) -> int:
        if name in self.layers:
            return self.layers[name]
        layer = rhino3dm.Layer()
        layer.Name = name
        layer.Color = color
        layer.Locked = locked
        layer.Visible = True
        index = self.model.Layers.Add(layer)
        self.layers[name] = index
        self.counts.setdefault(name, 0)
        return index

    def attributes(self, layer: str, name: str, user: dict[str, str] | None = None) -> rhino3dm.ObjectAttributes:
        attrs = rhino3dm.ObjectAttributes()
        attrs.LayerIndex = self.layers[layer]
        attrs.Name = name
        for key, value in (user or {}).items():
            attrs.SetUserString(key, str(value))
        return attrs

    def polyline(self, layer: str, points_xy: np.ndarray, closed: bool, name: str, user: dict[str, str] | None = None) -> bool:
        pts = np.asarray(points_xy, dtype=float)[:, :2]
        if len(pts) < 2:
            return False
        if closed and np.linalg.norm(pts[0] - pts[-1]) > 1e-9:
            pts = np.vstack([pts, pts[:1]])
        points = [rhino3dm.Point3d(float(x), float(y), 0.0) for x, y in pts]
        if self.model.Objects.AddPolyline(points, self.attributes(layer, name, user)):
            self.counts[layer] = self.counts.get(layer, 0) + 1
            return True
        return False

    def line(self, layer: str, start: np.ndarray, end: np.ndarray, name: str, user: dict[str, str] | None = None) -> None:
        a = rhino3dm.Point3d(float(start[0]), float(start[1]), 0.0)
        b = rhino3dm.Point3d(float(end[0]), float(end[1]), 0.0)
        self.model.Objects.AddLine(a, b, self.attributes(layer, name, user))
        self.counts[layer] = self.counts.get(layer, 0) + 1

    def dot(self, layer: str, text: str, point: np.ndarray, name: str) -> None:
        self.model.Objects.AddTextDot(text, rhino3dm.Point3d(float(point[0]), float(point[1]), 0.0), self.attributes(layer, name))
        self.counts[layer] = self.counts.get(layer, 0) + 1

    def write(self, path: Path) -> bool:
        return bool(self.model.Write(str(path), 8))


def _placed(curve: MappedCurve, placement: PanelPlacement) -> np.ndarray:
    return placement.apply(curve.flat_points_mm)


def write_outline(
    run_dir: Path,
    result: EngineResult,
    placements: dict[int, PanelPlacement],
    teak: dict[int, list[tuple[np.ndarray, np.ndarray]]] | None,
    config: dict[str, Any],
    pattern: str = "teak",
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    model = _Model()
    dxf = ezdxf.new("R2010", setup=True)
    dxf.header["$INSUNITS"] = 4
    dxf_space = dxf.modelspace()
    dxf_counts: dict[str, int] = {}

    def dxf_layer(name: str, color: tuple[int, int, int, int]) -> None:
        safe = dxf_layer_name(name)
        if safe not in dxf.layers:
            layer = dxf.layers.add(safe)
            layer.rgb = color[:3]

    def dxf_polyline(name: str, pts: np.ndarray, closed: bool) -> None:
        pts = np.asarray(pts, dtype=float)[:, :2]
        if len(pts) < 2:
            return
        dxf_space.add_lwpolyline([(float(x), float(y)) for x, y in pts], format="xy", close=bool(closed),
                                 dxfattribs={"layer": dxf_layer_name(name)})
        dxf_counts[name] = dxf_counts.get(name, 0) + 1

    pattern_kind = pattern_layer_kind(pattern)
    panel_ids = sorted(result.panels)
    # Layers in a deliberate order so Rhino's layer panel reads top-down.
    for pid in panel_ids:
        for kind in ("OUTER", "OBSTACLES", "FEATURES", "HINTS"):
            model.layer(layer_name(pid, kind), COLORS[kind], True)
            dxf_layer(layer_name(pid, kind), COLORS[kind])
        model.layer(layer_name(pid, "ROBUST"), COLORS["ROBUST"], True)
        dxf_layer(layer_name(pid, "ROBUST"), COLORS["ROBUST"])
        model.layer(layer_name(pid, pattern_kind), COLORS[pattern_kind], True)
        dxf_layer(layer_name(pid, pattern_kind), COLORS[pattern_kind])
        model.layer(layer_name(pid, "USER_CAM"), COLORS["USER_CAM"], False)
        dxf_layer(layer_name(pid, "USER_CAM"), COLORS["USER_CAM"])
    model.layer("HINTS::UNASSIGNED", COLORS["UNASSIGNED"], True)
    model.layer("LABELS", COLORS["LABELS"], True)
    model.layer("USER_CORNERS", COLORS["USER_CORNERS"], False)
    dxf_layer("HINTS::UNASSIGNED", COLORS["UNASSIGNED"])
    dxf_layer("LABELS", COLORS["LABELS"])
    dxf_layer("USER_CORNERS", COLORS["USER_CORNERS"])

    primary_world = result.panels[result.primary_id].world_centroid_mm
    curve_index: list[dict[str, Any]] = []
    for curve in result.curves:
        user = {"curve_id": curve.curve_id, "family": curve.family, "source_layer": curve.source_layer,
                "panel_id": "" if curve.panel_id is None else str(curve.panel_id), "confidence": f"{curve.confidence:.2f}"}
        if curve.panel_id is None or curve.panel_id not in placements:
            # No developed frame to place it in: show it at its boat-plan world
            # position relative to the primary so it is still visible.
            pts = curve.world_points_mm[:, :2] - primary_world
            model.polyline("HINTS::UNASSIGNED", pts, curve.closed, curve.curve_id, {**user, "placement": "world-relative"})
            dxf_polyline("HINTS::UNASSIGNED", pts, curve.closed)
            curve_index.append({**curve.metadata(), "layer": "HINTS::UNASSIGNED"})
            continue
        placement = placements[curve.panel_id]
        pts = _placed(curve, placement)
        name = layer_name(curve.panel_id, "ROBUST" if curve.family == "ROBUST" else FAMILY_LAYER[curve.family])
        model.polyline(name, pts, curve.closed, curve.curve_id, user)
        dxf_polyline(name, pts, curve.closed)
        curve_index.append({**curve.metadata(), "layer": name})

    teak = teak or {}
    for pid, lines in teak.items():
        name = layer_name(pid, pattern_kind)
        for index, (start, end) in enumerate(lines):
            model.line(name, start, end, f"{pattern}-{pid}-{index:04d}", {"panel_id": str(pid)})
            dxf_space.add_line((float(start[0]), float(start[1])), (float(end[0]), float(end[1])), dxfattribs={"layer": dxf_layer_name(name)})
            dxf_counts[name] = dxf_counts.get(name, 0) + 1

    for pid in panel_ids:
        panel = result.panels[pid]
        placement = placements[pid]
        outline = placement.placed_outline
        anchor = outline.mean(axis=0) if outline is not None and len(outline) else placement.translation
        text = (f"PANEL {pid} | {panel.role} | {panel.area_mm2 / 1e6:.2f} m2 | boat XY "
                f"({panel.world_centroid_mm[0]:.0f}, {panel.world_centroid_mm[1]:.0f}) mm"
                + (" | moved to row" if placement.moved_to_row else "")
                + (f" | nested in {placement.nested_in}" if placement.nested_in is not None else ""))
        model.dot("LABELS", text, anchor, f"label-{pid}")
        dxf_space.add_text(text, dxfattribs={"layer": "LABELS", "height": float(config["outline"].get("label_font_height_mm", 40.0))}).set_placement((float(anchor[0]), float(anchor[1])))
        dxf_counts["LABELS"] = dxf_counts.get("LABELS", 0) + 1

    three_dm = run_dir / "outline.3dm"
    if not model.write(three_dm):
        raise RuntimeError(f"rhino3dm could not write {three_dm}")
    dxf_path = run_dir / "outline.dxf"
    if bool(config["outline"].get("write_dxf", True)):
        dxf.saveas(dxf_path)

    overlap_pairs = overlaps(placements)
    panels_payload = {
        "primary_panel_id": result.primary_id,
        "panels": [
            {**result.panels[pid].metadata(), "placement": placements[pid].to_dict()} for pid in panel_ids
        ],
        "overlaps": [{"panel_a": a, "panel_b": b, "area_mm2": area} for a, b, area in overlap_pairs],
        "curves": curve_index,
        "layers": {name: {"locked": name.startswith(("RAW::", "REF::", "TEAK::", "PATTERN_", "HINTS::", "LABELS")),
                          "object_count": model.counts.get(name, 0)} for name in model.layers},
    }
    (run_dir / "panels.json").write_text(json.dumps(panels_payload, indent=2), encoding="utf-8")
    return {
        "outline_3dm": str(three_dm), "outline_dxf": str(dxf_path) if dxf_path.exists() else None,
        "layer_counts": dict(model.counts), "dxf_counts": dxf_counts, "overlaps": overlap_pairs,
        "panel_count": len(panel_ids),
    }


def read_outline_summary(path: Path) -> dict[str, Any]:
    """Independent read-back of an outline.3dm: layer names, lock flags,
    object counts, and every RAW OUTER polyline's points."""

    model = rhino3dm.File3dm.Read(str(path))
    if model is None:
        raise RuntimeError(f"could not read {path}")
    layers = {layer.Name: {"locked": bool(layer.Locked), "index": index, "count": 0} for index, layer in enumerate(model.Layers)}
    index_to_name = {info["index"]: name for name, info in layers.items()}
    outers: dict[int, np.ndarray] = {}
    for obj in model.Objects:
        name = index_to_name.get(int(obj.Attributes.LayerIndex))
        if name is None:
            continue
        layers[name]["count"] += 1
        if name.startswith("RAW::PANEL_") and name.endswith("::OUTER"):
            pid = int(name.split("PANEL_")[1].split("::")[0])
            geometry = obj.Geometry
            polyline = geometry.ToPolyline() if hasattr(geometry, "ToPolyline") else None
            if polyline is not None:
                outers[pid] = np.array([[p.X, p.Y] for p in polyline])
    return {"units": str(model.Settings.ModelUnitSystem), "layers": layers, "outers": outers}
