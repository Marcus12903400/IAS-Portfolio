"""Developer diagnostic for the cached Key West primary developed outline."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString, Polygon

from autodeck.config import load_config
from autodeck.curve_fit import fit_manufacturing_curves
from autodeck.reprocess import _load_development, _reconstruct_flat_curves
from autodeck.robust_reference import fit_robust_reference_curves


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    run = repo / "output" / "key-west-v033-orientation"
    metrics = json.loads((run / "debug_metrics.json").read_text(encoding="utf-8"))
    detail = json.loads(
        (run / "debug" / "development" / "development_metrics.json").read_text(
            encoding="utf-8"
        )
    )
    development = _load_development(run, metrics)
    flat, _ = _reconstruct_flat_curves(run, detail["flat_raw_curves"], development)
    manufacturing = fit_manufacturing_curves(flat, load_config())
    references = fit_robust_reference_curves(
        manufacturing, load_config()["robust_reference"]
    )
    spline = next(
        item
        for item in manufacturing
        if "DECK_PRIMARY" in item.source.source.raw_curve.layer.upper()
    )
    index = manufacturing.index(spline)
    reference = references[index]
    points = np.asarray(reference.points, dtype=float)[:, :2]
    np.savez_compressed(
        run / "primary_reference_diagnostic.npz",
        points=points,
        raw_points=np.asarray(reference.raw_points, dtype=float)[:, :2],
        anchors=np.asarray(reference.hard_corner_indices, dtype=np.int64),
    )
    closed = np.vstack([points, points[0]])
    print("points", len(points))
    print("bbox", np.ptp(points, axis=0).tolist())
    print("perimeter", float(LineString(closed).length))
    print("area", float(abs(Polygon(closed).area)))
    print("anchors", reference.hard_corner_indices)
    for tolerance in (0.5, 1.0, 1.5, 2.0, 2.5, 2.9):
        simplified = LineString(closed).simplify(tolerance, preserve_topology=True)
        print("simplify", tolerance, "segments", len(simplified.coords) - 1)
    anchors = reference.hard_corner_indices
    for number, start in enumerate(anchors):
        end = anchors[(number + 1) % len(anchors)]
        if end <= start:
            span = np.vstack([points[start:], points[: end + 1]])
        else:
            span = points[start : end + 1]
        print(
            "span",
            number,
            start,
            end,
            len(span),
            float(LineString(span).length),
            np.ptp(span, axis=0).tolist(),
        )
    minimum = np.min(points, axis=0)
    size = np.ptp(points, axis=0)
    view_width = 1400.0
    view_height = max(500.0, view_width * float(size[1] / max(size[0], 1e-9)))
    scale = min((view_width - 60.0) / size[0], (view_height - 60.0) / size[1])
    mapped = (points - minimum) * scale + 30.0
    mapped[:, 1] = view_height - mapped[:, 1]
    path = " ".join(
        [f"M {mapped[0, 0]:.2f},{mapped[0, 1]:.2f}"]
        + [f"L {x:.2f},{y:.2f}" for x, y in mapped[1:]]
        + ["Z"]
    )
    labels = []
    for number, anchor in enumerate(anchors):
        x, y = mapped[anchor]
        labels.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="red"/>')
        labels.append(f'<text x="{x + 8:.2f}" y="{y - 8:.2f}" font-size="18">{number}</text>')
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{view_width:.0f}" '
        f'height="{view_height:.0f}" viewBox="0 0 {view_width:.0f} {view_height:.0f}">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<path d="{path}" fill="none" stroke="black" stroke-width="1.2"/>'
        + "".join(labels)
        + "</svg>"
    )
    (run / "primary_reference_diagnostic.svg").write_text(svg, encoding="utf-8")
    raster = Image.new("RGB", (int(view_width), int(view_height)), "white")
    draw = ImageDraw.Draw(raster)
    draw.line([tuple(value) for value in np.vstack([mapped, mapped[0]])], fill="black", width=2)
    for number, anchor in enumerate(anchors):
        x, y = mapped[anchor]
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="red")
        draw.text((x + 8, y - 18), str(number), fill="red")
    raster.save(run / "primary_reference_diagnostic.png")


if __name__ == "__main__":
    main()
