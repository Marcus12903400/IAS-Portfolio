"""Prototype broad-regime LINE/ARC fit for the cached primary reference."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from autodeck.polyarc import (
    PolyarcPrimitive,
    _angle_mismatch_deg,
    _fit_endpoint_arc,
    _sample_primitives,
)
from autodeck.robust_reference import _smooth_open_span


def point_to_polyline(points: np.ndarray, polyline: np.ndarray) -> np.ndarray:
    starts = polyline[:-1]
    vectors = polyline[1:] - starts
    denominator = np.sum(vectors * vectors, axis=1)
    result = []
    for point in np.asarray(points):
        relative = point - starts
        parameter = np.divide(
            np.sum(relative * vectors, axis=1),
            denominator,
            out=np.zeros_like(denominator),
            where=denominator > 1e-12,
        )
        parameter = np.clip(parameter, 0.0, 1.0)
        nearest = starts + parameter[:, None] * vectors
        result.append(float(np.min(np.linalg.norm(point - nearest, axis=1))))
    return np.asarray(result)


def free_line(source: np.ndarray, start: int, end: int, span: int) -> PolyarcPrimitive:
    center = np.mean(source, axis=0)
    _u, _s, vh = np.linalg.svd(source - center, full_matrices=False)
    direction = vh[0]
    if np.dot(direction, source[-1] - source[0]) < 0.0:
        direction = -direction
    parameters = (source - center) @ direction
    return PolyarcPrimitive(
        "LINE",
        center + parameters[0] * direction,
        center + parameters[-1] * direction,
        start,
        end,
        span,
    )


def free_arc(source: np.ndarray, start: int, end: int, span: int):
    if len(source) < 3:
        return None
    matrix = np.column_stack((2.0 * source[:, 0], 2.0 * source[:, 1], np.ones(len(source))))
    rhs = np.sum(source * source, axis=1)
    solution = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
    center = solution[:2]
    radii = np.linalg.norm(source - center, axis=1)
    radius = float(np.median(radii))
    if not np.isfinite(radius) or radius <= 1e-6 or radius > 1e7:
        return None
    angles = np.unwrap(np.arctan2(source[:, 1] - center[1], source[:, 0] - center[0]))
    sweep = float(angles[-1] - angles[0])
    if abs(sweep) <= 1e-7 or abs(sweep) > math.pi + 1e-7:
        return None
    start_point = center + radius * np.array([math.cos(angles[0]), math.sin(angles[0])])
    end_point = center + radius * np.array([math.cos(angles[-1]), math.sin(angles[-1])])
    return PolyarcPrimitive(
        "ARC", start_point, end_point, start, end, span,
        center=center, radius_mm=radius, signed_sweep_rad=sweep,
    )


def broad_reference(points: np.ndarray, anchors: list[int]) -> np.ndarray:
    result = points.copy()
    for span, start in enumerate(anchors):
        end = anchors[(span + 1) % len(anchors)]
        if end <= start:
            indices = np.r_[np.arange(start, len(points)), np.arange(0, end + 1)]
        else:
            indices = np.arange(start, end + 1)
        smoothed, _scales = _smooth_open_span(points[indices], [20.0, 40.0, 80.0, 160.0])
        result[indices] = smoothed
    result[anchors] = points[anchors]
    return result


def candidate(points: np.ndarray, start: int, end: int, span: int, tolerance: float):
    source = points[start : end + 1]
    line = free_line(source, start, end, span)
    options = [line]
    arc = free_arc(source, start, end, span)
    if arc is not None:
        options.append(arc)
    valid = []
    for primitive in options:
        if primitive.kind == "LINE":
            delta = primitive.end - primitive.start
            length = float(np.linalg.norm(delta))
            direction = delta / max(length, 1e-12)
            local = source - primitive.start
            projection = np.clip(local @ direction, 0.0, length)
            nearest = primitive.start + projection[:, None] * direction
            point_max = float(np.max(np.linalg.norm(source - nearest, axis=1)))
        else:
            point_max = float(
                np.max(
                    np.abs(
                        np.linalg.norm(source - np.asarray(primitive.center), axis=1)
                        - float(primitive.radius_mm)
                    )
                )
            )
        if point_max > tolerance:
            continue
        sampled = primitive.sample(3.0)
        reverse_max = float(np.max(point_to_polyline(sampled, source)))
        maximum = max(point_max, reverse_max)
        if maximum <= tolerance:
            rms = float(np.sqrt(np.mean(point_to_polyline(source, sampled) ** 2)))
            valid.append((primitive, {"maximum_mm": maximum, "rms_mm": rms}))
    if not valid:
        return None
    return min(valid, key=lambda item: (0 if item[0].kind == "LINE" else 1, item[1]["rms_mm"]))


def fit_span(points: np.ndarray, span: int, tolerance: float):
    stride = 2
    nodes = list(range(0, len(points) - 1, stride)) + [len(points) - 1]
    count = len(nodes)
    costs = [math.inf] * count
    choices = [None] * count
    costs[-1] = 0
    for start_node in range(count - 2, -1, -1):
        for end_node in range(start_node + 1, count):
            if not math.isfinite(costs[end_node]):
                continue
            selected = candidate(
                points, nodes[start_node], nodes[end_node], span, tolerance
            )
            if selected is None:
                continue
            primitive, metrics = selected
            cost = 1.0 + costs[end_node]
            tie = (
                cost == costs[start_node]
                and choices[start_node] is not None
                and primitive.kind == "LINE"
                and choices[start_node][0][0].kind != "LINE"
            )
            if cost < costs[start_node] or tie:
                costs[start_node] = cost
                choices[start_node] = (selected, end_node)
    result = []
    cursor_node = 0
    while cursor_node < count - 1:
        if choices[cursor_node] is None:
            raise RuntimeError((span, cursor_node))
        selected, next_node = choices[cursor_node]
        primitive, _metrics = selected
        result.append(primitive)
        cursor_node = next_node
    return result


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    run = repo / "output" / "key-west-v033-orientation"
    with np.load(run / "primary_reference_diagnostic.npz") as archive:
        points = archive["points"]
        anchors = archive["anchors"].astype(int).tolist()
    original_points = points.copy()
    points = broad_reference(points, anchors)
    np.savez_compressed(
        run / "primary_broad_reference_diagnostic.npz",
        points=points,
        original_points=original_points,
        anchors=np.asarray(anchors, dtype=np.int64),
    )
    displacement = np.linalg.norm(points - original_points, axis=1)
    print(
        "broad reference shift",
        "rms", float(np.sqrt(np.mean(displacement * displacement))),
        "p95", float(np.percentile(displacement, 95)),
        "max", float(np.max(displacement)),
    )
    for tolerance in (2.95,):
        primitives = []
        for span, start in enumerate(anchors):
            end = anchors[(span + 1) % len(anchors)]
            if end <= start:
                local = np.vstack([points[start:], points[: end + 1]])
            else:
                local = points[start : end + 1]
            local_primitives = fit_span(local, span, tolerance)
            for primitive in local_primitives:
                primitive.source_start_index = start + primitive.source_start_index
                primitive.source_end_index = start + primitive.source_end_index
            primitives.extend(local_primitives)
        sampled = _sample_primitives(primitives, 0.5)
        if np.linalg.norm(sampled[-1] - sampled[0]) > 1e-8:
            sampled = np.vstack([sampled, sampled[0]])
        mismatches = [
            _angle_mismatch_deg(a.tangent_end(), b.tangent_start())
            for a, b in zip(primitives, primitives[1:] + primitives[:1])
            if a.logical_span_index == b.logical_span_index
        ]
        print(
            "tolerance", tolerance,
            "count", len(primitives),
            "lines", sum(p.kind == "LINE" for p in primitives),
            "arcs", sum(p.kind == "ARC" for p in primitives),
            "max_tangent", max(mismatches, default=0.0),
            "mismatch>1", sum(value > 1.0 for value in mismatches),
        )
        extended = np.vstack([points, points])
        connected = []
        segment_maxima = []
        join_points = [
            0.5 * (previous.end + current.start)
            for previous, current in zip(primitives[-1:] + primitives[:-1], primitives)
        ]
        for index, primitive in enumerate(primitives):
            start_index = int(primitive.source_start_index)
            end_index = int(primitive.source_end_index)
            if end_index <= start_index:
                end_index += len(points)
            source = extended[start_index : end_index + 1]
            start_point = join_points[index].copy()
            end_point = join_points[(index + 1) % len(primitives)].copy()
            previous = primitives[index - 1]
            following = primitives[(index + 1) % len(primitives)]
            if previous.logical_span_index != primitive.logical_span_index:
                start_point = source[0].copy()
            if primitive.logical_span_index != following.logical_span_index:
                end_point = source[-1].copy()
            fitting_source = source.copy()
            fitting_source[0] = start_point
            fitting_source[-1] = end_point
            if primitive.kind == "LINE":
                fitted = PolyarcPrimitive(
                    "LINE", start_point, end_point, start_index, end_index,
                    primitive.logical_span_index,
                )
            else:
                fitted = _fit_endpoint_arc(
                    fitting_source, start_index, end_index, primitive.logical_span_index
                )
                if fitted is None:
                    fitted = PolyarcPrimitive(
                        "LINE", source[0], source[-1], start_index, end_index,
                        primitive.logical_span_index,
                    )
            sampled_segment = fitted.sample(1.0)
            maximum = max(
                float(np.max(point_to_polyline(source, sampled_segment))),
                float(np.max(point_to_polyline(sampled_segment, source))),
            )
            segment_maxima.append(maximum)
            connected.append(fitted)
        connected_mismatch = [
            _angle_mismatch_deg(a.tangent_end(), b.tangent_start())
            for a, b in zip(connected, connected[1:] + connected[:1])
            if a.logical_span_index == b.logical_span_index
        ]
        print(
            "endpoint-connected",
            "lines", sum(p.kind == "LINE" for p in connected),
            "arcs", sum(p.kind == "ARC" for p in connected),
            "max", max(segment_maxima),
            "over3", sum(value > 3.0 for value in segment_maxima),
            "max_tangent", max(connected_mismatch, default=0.0),
        )
        (run / f"prototype_manual_{tolerance:.2f}.json").write_text(
            json.dumps([p.to_dict() for p in primitives], indent=2), encoding="utf-8"
        )
        minimum = np.min(np.vstack([points, sampled]), axis=0)
        size = np.ptp(np.vstack([points, sampled]), axis=0)
        width = 1400
        height = max(500, int(width * size[1] / size[0]))
        scale = min((width - 60) / size[0], (height - 60) / size[1])
        def mapped(values):
            result = (values - minimum) * scale + 30
            result[:, 1] = height - result[:, 1]
            return result
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        draw.line([tuple(v) for v in mapped(np.vstack([points, points[0]]))], fill=(180, 180, 180), width=3)
        palette = [(0, 90, 220), (220, 60, 0), (0, 150, 80), (150, 0, 180)]
        for index, primitive in enumerate(primitives):
            curve = mapped(primitive.sample(0.75))
            draw.line([tuple(v) for v in curve], fill=palette[index % len(palette)], width=2)
        image.save(run / f"prototype_manual_{tolerance:.2f}.png")


if __name__ == "__main__":
    main()
