"""Render the sheet DXFs to a PNG so the nesting can be eyeballed.

Reads the files exactly as VCarve would -- arcs reconstructed from bulges --
so what you see is what would be cut, not a re-render of in-memory geometry.

    python tools/render_sheets.py --run engine/outputs/runs/<id> --out sheets.png
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np


def sample_lwpolyline(entity, step: float = 2.0) -> np.ndarray:
    points = list(entity.get_points("xyb"))
    closed = bool(entity.closed)
    out: list[np.ndarray] = []
    count = len(points)
    last = count if closed else count - 1
    for i in range(last):
        x1, y1, bulge = points[i]
        x2, y2, _ = points[(i + 1) % count]
        p0 = np.array([x1, y1]); p1 = np.array([x2, y2])
        chord = float(np.hypot(*(p1 - p0)))
        if abs(bulge) < 1e-12 or chord < 1e-12:
            out.append(np.linspace(p0, p1, max(2, int(chord / step)), endpoint=False))
            continue
        theta = 4.0 * math.atan(bulge)
        radius = chord / (2.0 * abs(math.sin(theta / 2.0)))
        mid = (p0 + p1) / 2.0
        height = math.sqrt(max(radius * radius - (chord / 2.0) ** 2, 0.0))
        normal = np.array([-(p1 - p0)[1], (p1 - p0)[0]]) / chord
        sign = 1.0 if theta > 0 else -1.0
        if abs(theta) > math.pi:
            sign = -sign
        centre = mid + normal * height * sign
        start = math.atan2(p0[1] - centre[1], p0[0] - centre[0])
        n = max(4, int(abs(theta) * radius / step))
        angles = np.linspace(start, start + theta, n, endpoint=False)
        out.append(centre + radius * np.column_stack([np.cos(angles), np.sin(angles)]))
    out.append(np.array([points[0][:2] if closed else points[-1][:2]], dtype=float))
    return np.vstack(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--scale", type=float, default=0.28, help="pixels per mm")
    args = parser.parse_args()

    import ezdxf
    from PIL import Image, ImageDraw

    files = sorted(Path(args.run).glob("sheet_*.dxf"))
    if not files:
        print(f"no sheet_*.dxf in {args.run}")
        return 1

    scale = args.scale
    pad = 30
    docs = [ezdxf.readfile(str(p)) for p in files]
    sheet_w = sheet_h = 0.0
    for doc in docs:
        for e in doc.modelspace().query("LWPOLYLINE"):
            if str(e.dxf.layer) == "SHEET__OUTLINE":
                pts = np.array([(x, y) for x, y, *_ in e.get_points("xy")])
                sheet_w = max(sheet_w, pts[:, 0].max())
                sheet_h = max(sheet_h, pts[:, 1].max())

    tile_w = int(sheet_w * scale) + pad
    tile_h = int(sheet_h * scale) + pad + 24
    image = Image.new("RGB", (tile_w * len(files) + pad, tile_h + pad), (250, 249, 246))
    draw = ImageDraw.Draw(image)

    for index, (path, doc) in enumerate(zip(files, docs)):
        ox = pad + index * tile_w
        oy = pad + 20

        def to_px(points: np.ndarray) -> list[tuple[float, float]]:
            return [(ox + p[0] * scale, oy + (sheet_h - p[1]) * scale) for p in points]

        draw.text((ox, oy - 16), f"{path.name}   ({sheet_w:.0f} x {sheet_h:.0f} mm)", fill=(40, 40, 40))
        for e in doc.modelspace():
            layer = str(e.dxf.layer)
            if e.dxftype() == "LWPOLYLINE":
                pts = to_px(sample_lwpolyline(e))
                if layer == "SHEET__OUTLINE":
                    draw.polygon(pts, outline=(150, 150, 150), fill=(255, 255, 255))
                elif layer == "SHEET__USABLE":
                    draw.line(pts + [pts[0]], fill=(215, 215, 215), width=1)
                elif layer.startswith("CAM__"):
                    draw.polygon(pts, outline=(20, 90, 190), fill=(214, 231, 250))
            elif e.dxftype() == "LINE" and layer.startswith("PATTERN_"):
                s, t = e.dxf.start, e.dxf.end
                draw.line(to_px(np.array([[s.x, s.y], [t.x, t.y]])), fill=(190, 150, 90), width=1)
        # redraw cut outlines on top of the pattern grooves
        for e in doc.modelspace().query("LWPOLYLINE"):
            if str(e.dxf.layer).startswith("CAM__"):
                draw.line(to_px(sample_lwpolyline(e)), fill=(20, 90, 190), width=2)

    out = args.out or Path(args.run) / "sheets_preview.png"
    image.save(out)
    print(f"wrote {out}  ({image.width} x {image.height} px, {len(files)} sheets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
