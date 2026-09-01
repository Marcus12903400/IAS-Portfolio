from __future__ import annotations

from dataclasses import dataclass
import math
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np

from .models import (
    AcceptedSurfaceGrid,
    Adjacency,
    AnalysisMesh,
    BoundaryField,
    CandidateRegion,
    FeatureCurve,
    GeometryFields,
    Mesh,
    TopViewResult,
)


@dataclass(slots=True)
class _Grid:
    x_min: float
    y_min: float
    resolution: float
    width: int
    height: int

    @property
    def x_max(self) -> float:
        return self.x_min + self.width * self.resolution

    @property
    def y_max(self) -> float:
        return self.y_min + self.height * self.resolution

    def pixels(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        col = np.floor((xy[:, 0] - self.x_min) / self.resolution).astype(np.int64)
        row = np.floor((xy[:, 1] - self.y_min) / self.resolution).astype(np.int64)
        valid = (col >= 0) & (col < self.width) & (row >= 0) & (row < self.height)
        return row, col, valid


@dataclass(slots=True)
class _RasterComponent:
    area_pixels: int
    bbox: tuple[int, int, int, int]
    runs: list[tuple[int, int, int]]
    touches_border: bool


def _box_sum(values: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return values.astype(np.float32, copy=True)
    padded = np.pad(values.astype(np.float32, copy=False), radius, mode="constant")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(0, dtype=np.float64).cumsum(1)
    size = 2 * radius + 1
    result = (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )
    return result.astype(np.float32)


def _normalized_box(values: np.ndarray, valid: np.ndarray, radius: int) -> np.ndarray:
    numerator = _box_sum(np.where(valid, values, 0.0), radius)
    denominator = _box_sum(valid.astype(np.float32), radius)
    return np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    return _box_sum(mask.astype(np.float32), radius) > 0.0


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask.copy()
    area = float((2 * radius + 1) ** 2)
    return _box_sum(mask.astype(np.float32), radius) >= area


def _close(mask: np.ndarray, radius: int) -> np.ndarray:
    return _erode(_dilate(mask, radius), radius)


def _components(mask: np.ndarray) -> list[_RasterComponent]:
    """Eight-connected run-length component labeling without SciPy."""
    parent: list[int] = []
    records: list[tuple[int, int, int, int]] = []

    def make_label() -> int:
        label = len(parent); parent.append(label); return label

    def find(label: int) -> int:
        while parent[label] != label:
            parent[label] = parent[parent[label]]
            label = parent[label]
        return label

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    previous: list[tuple[int, int, int]] = []
    for row_index in range(mask.shape[0]):
        padded = np.pad(mask[row_index], (1, 1), constant_values=False)
        starts = np.flatnonzero(~padded[:-1] & padded[1:])
        ends = np.flatnonzero(padded[:-1] & ~padded[1:])
        current: list[tuple[int, int, int]] = []
        previous_cursor = 0
        for start, end in zip(starts, ends, strict=True):
            # ``ends`` is the first false pixel after a run.  Store inclusive
            # endpoints because the remainder of the RLE implementation uses
            # inclusive ranges.  Treating this as inclusive used to add one
            # phantom pixel to every run and could merge nearby hypotheses.
            start_i, end_i = int(start), int(end) - 1
            while previous_cursor < len(previous) and previous[previous_cursor][1] < start_i - 1:
                previous_cursor += 1
            overlaps: list[int] = []
            cursor = previous_cursor
            while cursor < len(previous) and previous[cursor][0] <= end_i + 1:
                overlaps.append(previous[cursor][2]); cursor += 1
            label = overlaps[0] if overlaps else make_label()
            for other in overlaps[1:]:
                union(label, other)
            current.append((start_i, end_i, label))
            records.append((row_index, start_i, end_i, label))
        previous = current

    grouped: dict[int, list[tuple[int, int, int]]] = {}
    for row, start, end, label in records:
        grouped.setdefault(find(label), []).append((row, start, end))
    result: list[_RasterComponent] = []
    height, width = mask.shape
    for runs in grouped.values():
        area = sum(end - start + 1 for _row, start, end in runs)
        rows = [row for row, _start, _end in runs]
        starts = [start for _row, start, _end in runs]
        ends = [end for _row, _start, end in runs]
        bbox = (min(rows), min(starts), max(rows), max(ends))
        touches = bbox[0] == 0 or bbox[1] == 0 or bbox[2] == height - 1 or bbox[3] == width - 1
        result.append(_RasterComponent(area, bbox, runs, touches))
    result.sort(key=lambda item: item.area_pixels, reverse=True)
    return result


def _mask_from_components(shape: tuple[int, int], components: list[_RasterComponent]) -> np.ndarray:
    result = np.zeros(shape, dtype=bool)
    for component in components:
        for row, start, end in component.runs:
            result[row, start:end + 1] = True
    return result


def _group_nearby_components(components: list[_RasterComponent], distance_pixels: int) -> list[list[_RasterComponent]]:
    groups: list[list[_RasterComponent]] = []
    for component in components:
        r0, c0, r1, c1 = component.bbox
        matching: list[int] = []
        for index, group in enumerate(groups):
            if any(
                not (
                    r1 + distance_pixels < other.bbox[0]
                    or other.bbox[2] + distance_pixels < r0
                    or c1 + distance_pixels < other.bbox[1]
                    or other.bbox[3] + distance_pixels < c0
                )
                for other in group
            ):
                matching.append(index)
        if not matching:
            groups.append([component]); continue
        first = matching[0]; groups[first].append(component)
        for index in reversed(matching[1:]):
            groups[first].extend(groups.pop(index))
    return groups


def _component_encloses_hole(component: _RasterComponent) -> bool:
    r0, c0, r1, c1 = component.bbox
    crop = np.zeros((r1 - r0 + 3, c1 - c0 + 3), dtype=bool)
    for row, start, end in component.runs:
        crop[row - r0 + 1, start - c0 + 1:end - c0 + 2] = True
    return any(not item.touches_border and item.area_pixels > 4 for item in _components(~crop))


def _component_physical_metrics(
    component: _RasterComponent,
    grid: _Grid,
    floor_height: np.ndarray,
    raised_height: np.ndarray,
    raw_void_mask: np.ndarray,
) -> dict[str, Any]:
    r0, c0, r1, c1 = component.bbox
    crop = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=bool)
    for row, start, end in component.runs:
        crop[row - r0, start - c0:end - c0 + 1] = True
    above = np.zeros_like(crop); above[1:] = crop[:-1]
    below = np.zeros_like(crop); below[:-1] = crop[1:]
    left = np.zeros_like(crop); left[:, 1:] = crop[:, :-1]
    right = np.zeros_like(crop); right[:, :-1] = crop[:, 1:]
    perimeter_pixels = int(
        np.count_nonzero(crop & ~above) + np.count_nonzero(crop & ~below)
        + np.count_nonzero(crop & ~left) + np.count_nonzero(crop & ~right)
    )
    area_mm2 = component.area_pixels * grid.resolution ** 2
    perimeter_mm = perimeter_pixels * grid.resolution
    floor_values = np.concatenate([
        floor_height[row, start:end + 1] for row, start, end in component.runs
    ])
    raised_values = np.concatenate([
        raised_height[row, start:end + 1] for row, start, end in component.runs
    ])
    raw_count = sum(
        int(np.count_nonzero(raw_void_mask[row, start:end + 1]))
        for row, start, end in component.runs
    )
    contained_z = floor_values + raised_values
    return {
        "area_mm2": float(area_mm2),
        "perimeter_mm": float(perimeter_mm),
        "equivalent_diameter_mm": float(math.sqrt(4.0 * area_mm2 / math.pi)),
        "bbox_width_mm": float((c1 - c0 + 1) * grid.resolution),
        "bbox_height_mm": float((r1 - r0 + 1) * grid.resolution),
        "bbox_mm": [
            float(grid.x_min + c0 * grid.resolution), float(grid.y_min + r0 * grid.resolution),
            float(grid.x_min + (c1 + 1) * grid.resolution), float(grid.y_min + (r1 + 1) * grid.resolution),
        ],
        "compactness": float(4.0 * math.pi * area_mm2 / max(perimeter_mm ** 2, 1e-12)),
        "minimum_distance_to_surrounding_floor_mm": float(grid.resolution),
        "cleanup_persistence_fraction": float(raw_count / max(component.area_pixels, 1)),
        "local_floor_z_min_mm": float(np.min(floor_values)),
        "local_floor_z_median_mm": float(np.median(floor_values)),
        "contained_geometry_z_min_mm": float(np.min(contained_z)),
        "contained_geometry_z_median_mm": float(np.median(contained_z)),
        "contained_geometry_z_max_mm": float(np.max(contained_z)),
        "height_above_floor_median_mm": float(np.median(raised_values)),
        "height_above_floor_max_mm": float(np.max(raised_values)),
    }


def _rdp(points: np.ndarray, tolerance: float) -> np.ndarray:
    if len(points) <= 2:
        return points
    line = points[-1] - points[0]
    length = float(np.linalg.norm(line))
    if length <= 1e-12:
        distances = np.linalg.norm(points[1:-1] - points[0], axis=1)
    else:
        offsets = points[1:-1] - points[0]
        distances = np.abs(offsets[:, 0] * line[1] - offsets[:, 1] * line[0]) / length
    if not len(distances) or float(distances.max()) <= tolerance:
        return points[[0, -1]]
    split = int(np.argmax(distances)) + 1
    return np.vstack((_rdp(points[:split + 1], tolerance)[:-1], _rdp(points[split:], tolerance)))


def _simplify_closed(points: np.ndarray, tolerance: float) -> np.ndarray:
    if len(points) <= 5:
        return points
    ring = points[:-1] if np.allclose(points[0], points[-1]) else points
    split = int(np.argmax(np.linalg.norm(ring - ring[0], axis=1)))
    first = _rdp(ring[:split + 1], tolerance)
    second = _rdp(np.vstack((ring[split:], ring[0])), tolerance)
    combined = np.vstack((first[:-1], second))
    if not np.allclose(combined[0], combined[-1]):
        combined = np.vstack((combined, combined[0]))
    return combined


def _mask_contours(mask: np.ndarray, grid: _Grid, tolerance_mm: float) -> list[np.ndarray]:
    if not np.any(mask):
        return []
    below = np.zeros_like(mask); below[1:] = mask[:-1]
    above = np.zeros_like(mask); above[:-1] = mask[1:]
    left = np.zeros_like(mask); left[:, 1:] = mask[:, :-1]
    right = np.zeros_like(mask); right[:, :-1] = mask[:, 1:]
    edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for row, col in np.argwhere(mask & ~below):
        edges.append(((int(col), int(row)), (int(col + 1), int(row))))
    for row, col in np.argwhere(mask & ~right):
        edges.append(((int(col + 1), int(row)), (int(col + 1), int(row + 1))))
    for row, col in np.argwhere(mask & ~above):
        edges.append(((int(col + 1), int(row + 1)), (int(col), int(row + 1))))
    for row, col in np.argwhere(mask & ~left):
        edges.append(((int(col), int(row + 1)), (int(col), int(row))))
    outgoing: dict[tuple[int, int], list[tuple[int, int]]] = {}
    unused = set(edges)
    for start, end in edges:
        outgoing.setdefault(start, []).append(end)
    contours: list[np.ndarray] = []
    while unused:
        first = next(iter(unused)); unused.remove(first)
        chain = [first[0], first[1]]; current = first[1]
        while current != chain[0]:
            choices = [end for end in outgoing.get(current, []) if (current, end) in unused]
            if not choices:
                break
            edge = (current, choices[0]); unused.remove(edge); current = edge[1]; chain.append(current)
        if len(chain) < 4 or chain[-1] != chain[0]:
            continue
        pixel_points = np.asarray(chain, dtype=float)
        xy = np.column_stack((
            grid.x_min + pixel_points[:, 0] * grid.resolution,
            grid.y_min + pixel_points[:, 1] * grid.resolution,
        ))
        contours.append(_simplify_closed(xy, tolerance_mm))
    contours.sort(key=lambda points: abs(_polygon_area(points)), reverse=True)
    return contours


def _polygon_area(points: np.ndarray) -> float:
    if len(points) < 3:
        return 0.0
    return 0.5 * float(np.sum(points[:-1, 0] * points[1:, 1] - points[1:, 0] * points[:-1, 1]))


def _fill_polygon(shape: tuple[int, int], pixel_points: np.ndarray) -> np.ndarray:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - Pillow is a V0.2 runtime dependency.
        raise RuntimeError("Pillow is required for V0.2 top-view rasterization") from exc
    image = Image.new("1", (shape[1], shape[0]), 0)
    ImageDraw.Draw(image).polygon([tuple(point) for point in pixel_points], fill=1)
    return np.asarray(image, dtype=bool)


def _robust01(values: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    samples = values[valid & np.isfinite(values)]
    if not len(samples):
        return np.zeros_like(values, dtype=np.float32), {name: 0.0 for name in ("p50", "p75", "p90", "p95", "p99")}
    p50, p75, p90, p95, p99 = np.percentile(samples, [50, 75, 90, 95, 99])
    normalized = np.clip((values - p50) / max(p99 - p50, 1e-12), 0.0, 1.0).astype(np.float32)
    return normalized, {"p50": float(p50), "p75": float(p75), "p90": float(p90), "p95": float(p95), "p99": float(p99)}


def classify_nonskid_evidence(
    residual_distribution: dict[str, float],
    density_distribution: dict[str, float],
    config: dict[str, Any],
    evaluated: bool = True,
) -> tuple[str, dict[str, float]]:
    """Classify optional texture evidence without percentile-forced positives."""
    residual_p95 = float(residual_distribution.get("p95", 0.0))
    density_p50 = float(density_distribution.get("p50", 0.0))
    density_p95 = float(density_distribution.get("p95", 0.0))
    density_contrast = density_p95 - density_p50
    density_contrast_ratio = density_contrast / max(
        density_p50, float(config.get("density_background_floor_per_mm", 0.05))
    )
    residual_minimum = float(config.get("absolute_residual_p95_minimum_mm", 0.40))
    density_p95_minimum = float(config.get("absolute_density_p95_minimum_per_mm", 0.12))
    contrast_ratio_minimum = float(config.get("density_contrast_ratio_minimum", 1.50))
    evidence_ratio = min(
        residual_p95 / max(residual_minimum, 1e-12),
        density_p95 / max(density_p95_minimum, 1e-12),
        density_contrast_ratio / max(contrast_ratio_minimum, 1e-12),
    )
    if not evaluated:
        status = "NOT_EVALUATED"
    elif evidence_ratio >= 1.0:
        status = "DETECTED"
    elif evidence_ratio >= float(config.get("weak_signal_fraction", 0.80)):
        status = "WEAK_SIGNAL"
    else:
        status = "NO_DETECTABLE_SIGNAL"
    return status, {
        "residual_p95_mm": residual_p95,
        "required_residual_p95_mm": residual_minimum,
        "density_p50_per_mm": density_p50,
        "density_p95_per_mm": density_p95,
        "required_density_p95_per_mm": density_p95_minimum,
        "density_p95_minus_p50_per_mm": density_contrast,
        "density_contrast_over_background": density_contrast_ratio,
        "required_density_contrast_over_background": contrast_ratio_minimum,
        "minimum_evidence_ratio": evidence_ratio,
    }


def _project_to_floor(points_xy: np.ndarray, floor_z: np.ndarray, grid: _Grid, mm_per_unit: float) -> np.ndarray:
    row, col, valid = grid.pixels(points_xy)
    row = np.clip(row, 0, grid.height - 1); col = np.clip(col, 0, grid.width - 1)
    z = floor_z[row, col]
    points_mm = np.column_stack((points_xy[:, 0], points_xy[:, 1], z))
    if not np.all(valid):
        points_mm[~valid, 2] = float(np.nanmedian(floor_z))
    return points_mm / mm_per_unit


def _skeletonize(mask: np.ndarray, maximum_iterations: int = 100) -> np.ndarray:
    result = mask.copy()
    for _ in range(maximum_iterations):
        changed = False
        for first_pass in (True, False):
            padded = np.pad(result, 1, constant_values=False)
            p2 = padded[:-2, 1:-1]; p3 = padded[:-2, 2:]; p4 = padded[1:-1, 2:]
            p5 = padded[2:, 2:]; p6 = padded[2:, 1:-1]; p7 = padded[2:, :-2]
            p8 = padded[1:-1, :-2]; p9 = padded[:-2, :-2]
            neighbors = p2.astype(np.uint8) + p3 + p4 + p5 + p6 + p7 + p8 + p9
            transitions = (
                (~p2 & p3).astype(np.uint8) + (~p3 & p4) + (~p4 & p5) + (~p5 & p6)
                + (~p6 & p7) + (~p7 & p8) + (~p8 & p9) + (~p9 & p2)
            )
            if first_pass:
                removable = result & (neighbors >= 2) & (neighbors <= 6) & (transitions == 1) & ~(p2 & p4 & p6) & ~(p4 & p6 & p8)
            else:
                removable = result & (neighbors >= 2) & (neighbors <= 6) & (transitions == 1) & ~(p2 & p4 & p8) & ~(p2 & p6 & p8)
            if np.any(removable):
                result[removable] = False; changed = True
        if not changed:
            break
    return result


def _trace_skeleton(mask: np.ndarray) -> list[np.ndarray]:
    pixels = {tuple(int(value) for value in point) for point in np.argwhere(mask)}
    if not pixels:
        return []
    neighbor_map: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for row, col in pixels:
        neighbor_map[(row, col)] = [
            (row + dr, col + dc)
            for dr in (-1, 0, 1) for dc in (-1, 0, 1)
            if (dr or dc) and (row + dr, col + dc) in pixels
        ]
    unused = {
        tuple(sorted((point, neighbor)))
        for point, neighbors in neighbor_map.items() for neighbor in neighbors
    }
    paths: list[np.ndarray] = []

    def walk(start: tuple[int, int], neighbor: tuple[int, int]) -> list[tuple[int, int]]:
        path = [start, neighbor]; previous, current = start, neighbor
        unused.discard(tuple(sorted((previous, current))))
        while len(neighbor_map[current]) == 2:
            next_point = neighbor_map[current][0] if neighbor_map[current][1] == previous else neighbor_map[current][1]
            edge = tuple(sorted((current, next_point)))
            if edge not in unused:
                break
            unused.remove(edge); path.append(next_point); previous, current = current, next_point
        return path

    nodes = [point for point, neighbors in neighbor_map.items() if len(neighbors) != 2]
    for node in nodes:
        for neighbor in neighbor_map[node]:
            edge = tuple(sorted((node, neighbor)))
            if edge in unused:
                paths.append(np.asarray(walk(node, neighbor), dtype=float))
    while unused:
        edge = next(iter(unused)); paths.append(np.asarray(walk(edge[0], edge[1]), dtype=float))
    return paths


def _seam_curves_from_components(
    components: list[_RasterComponent],
    response: np.ndarray,
    floor_z: np.ndarray,
    grid: _Grid,
    mm_per_unit: float,
    layer: str,
    name_prefix: str,
    minimum_length_mm: float,
    closed_endpoint_gap_mm: float,
    simplify_tolerance_mm: float,
    confidence: float,
    classify_closed: bool,
) -> list[FeatureCurve]:
    curves: list[FeatureCurve] = []
    for component_index, component in enumerate(components):
        r0, c0, r1, c1 = component.bbox
        crop = np.zeros((r1 - r0 + 3, c1 - c0 + 3), dtype=bool)
        for row, start, end in component.runs:
            crop[row - r0 + 1, start - c0 + 1:end - c0 + 2] = True
        for path_index, pixels in enumerate(_trace_skeleton(_skeletonize(crop))):
            global_row = pixels[:, 0] + r0 - 1; global_col = pixels[:, 1] + c0 - 1
            xy = np.column_stack((
                grid.x_min + (global_col + 0.5) * grid.resolution,
                grid.y_min + (global_row + 0.5) * grid.resolution,
            ))
            length = float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum())
            if length < minimum_length_mm:
                continue
            closed = bool(classify_closed and np.linalg.norm(xy[0] - xy[-1]) <= closed_endpoint_gap_mm)
            simplified = _rdp(xy, simplify_tolerance_mm)
            if closed and not np.allclose(simplified[0], simplified[-1]):
                simplified = np.vstack((simplified, simplified[0]))
            row, col, _valid = grid.pixels(xy); row = np.clip(row, 0, grid.height - 1); col = np.clip(col, 0, grid.width - 1)
            mean_response = float(np.mean(response[row, col]))
            curve_layer = "AUTODECK::HATCH_OR_CLOSED_SEAM" if closed else layer
            curves.append(FeatureCurve(
                curve_layer,
                f"{name_prefix}_{component_index + 1:03d}_{path_index + 1:03d}",
                _project_to_floor(simplified, floor_z, grid, mm_per_unit),
                closed,
                confidence,
                {"length_mm": length, "mean_response": mean_response, "point_count": int(len(simplified))},
            ))
    return curves


def _boundary_pixels(mask: np.ndarray) -> np.ndarray:
    return mask & ~_erode(mask, 1)


def _save_png(path: Path, values: np.ndarray, grid: _Grid, kind: str = "heat") -> None:
    from PIL import Image, ImageDraw, PngImagePlugin

    finite = np.isfinite(values)
    samples = values[finite]
    if len(samples):
        low, high = np.percentile(samples, [2, 98])
    else:
        low, high = 0.0, 1.0
    normalized = np.clip((values - low) / max(high - low, 1e-12), 0.0, 1.0)
    if kind == "mask":
        rgb = np.where(values[..., None] > 0, np.asarray([235, 235, 235]), np.asarray([20, 20, 20])).astype(np.uint8)
    elif kind == "residual":
        magnitude = np.clip(np.abs(values) / max(float(np.percentile(np.abs(samples), 98)) if len(samples) else 1.0, 1e-12), 0, 1)
        rgb = np.zeros((*values.shape, 3), dtype=np.uint8)
        rgb[..., 0] = np.where(values >= 0, 255 * magnitude, 25).astype(np.uint8)
        rgb[..., 2] = np.where(values < 0, 255 * magnitude, 25).astype(np.uint8)
        rgb[..., 1] = (40 * (1.0 - magnitude)).astype(np.uint8)
    else:
        rgb = np.zeros((*values.shape, 3), dtype=np.uint8)
        rgb[..., 0] = (255 * normalized).astype(np.uint8)
        rgb[..., 1] = (255 * np.sqrt(normalized)).astype(np.uint8)
        rgb[..., 2] = (90 * (1.0 - normalized)).astype(np.uint8)
    image = Image.fromarray(np.flipud(rgb), "RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, image.height - 31, 245, image.height - 5), fill=(0, 0, 0))
    draw.text((10, image.height - 27), f"+X ->   +Y ^   {grid.resolution:g} mm/px", fill=(255, 255, 255))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("pixel_resolution_mm", str(grid.resolution))
    metadata.add_text("x_extent_mm", f"{grid.x_min},{grid.x_max}")
    metadata.add_text("y_extent_mm", f"{grid.y_min},{grid.y_max}")
    metadata.add_text("orientation", "orthographic top view; image top is +Y; image right is +X")
    image.save(path, pnginfo=metadata)


def _save_combined(path: Path, primary: np.ndarray, secondary: np.ndarray, nonskid: np.ndarray, seams: np.ndarray, obstacles: np.ndarray, grid: _Grid) -> None:
    from PIL import Image, ImageDraw, PngImagePlugin

    rgb = np.zeros((*primary.shape, 3), dtype=np.uint8); rgb[:] = [18, 18, 24]
    rgb[primary] = [65, 65, 70]
    rgb[_boundary_pixels(primary)] = [255, 145, 0]
    rgb[_boundary_pixels(secondary)] = [35, 145, 255]
    rgb[_boundary_pixels(nonskid)] = [40, 230, 80]
    rgb[seams] = [185, 60, 255]
    rgb[_boundary_pixels(obstacles)] = [255, 40, 40]
    image = Image.fromarray(np.flipud(rgb), "RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, image.height - 31, 385, image.height - 5), fill=(0, 0, 0))
    draw.text((10, image.height - 27), f"orange primary | blue secondary | green nonskid | purple seam | red obstacle | {grid.resolution:g} mm/px", fill=(255, 255, 255))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("pixel_resolution_mm", str(grid.resolution))
    metadata.add_text("x_extent_mm", f"{grid.x_min},{grid.x_max}")
    metadata.add_text("y_extent_mm", f"{grid.y_min},{grid.y_max}")
    metadata.add_text("orientation", "orthographic top view; image top is +Y; image right is +X")
    image.save(path, pnginfo=metadata)


def _filter_region_mask(mask: np.ndarray, minimum_area_mm2: float, grid: _Grid, maximum: int) -> tuple[np.ndarray, list[_RasterComponent]]:
    minimum_pixels = max(1, int(math.ceil(minimum_area_mm2 / (grid.resolution ** 2))))
    accepted = [component for component in _components(mask) if component.area_pixels >= minimum_pixels][:maximum]
    return _mask_from_components(mask.shape, accepted), accepted


def _curves_from_mask(
    mask: np.ndarray,
    layer: str,
    name_prefix: str,
    floor_z: np.ndarray,
    grid: _Grid,
    mm_per_unit: float,
    tolerance: float,
    confidence: float,
) -> list[FeatureCurve]:
    curves: list[FeatureCurve] = []
    # Export one envelope per accepted region.  Contours of texture-scale holes
    # inside a nonskid/obstacle hypothesis remain in the packed raster but are
    # not useful CAD boundaries and previously multiplied 20 regions into more
    # than a thousand Rhino curves.
    envelopes: list[np.ndarray] = []
    for component in _components(mask):
        component_mask = _mask_from_components(mask.shape, [component])
        contours = _mask_contours(component_mask, grid, tolerance)
        if contours:
            envelopes.append(contours[0])
    envelopes.sort(key=lambda points: abs(_polygon_area(points)), reverse=True)
    for index, contour in enumerate(envelopes):
        area = abs(_polygon_area(contour))
        if area <= grid.resolution ** 2:
            continue
        curves.append(FeatureCurve(
            layer, f"{name_prefix}_{index + 1:03d}",
            _project_to_floor(contour, floor_z, grid, mm_per_unit), True, confidence,
            {"area_mm2": area, "point_count": int(len(contour))},
        ))
    return curves


def build_topview_features(
    output_dir: Path,
    original: Mesh,
    analysis: AnalysisMesh,
    adjacency: Adjacency,
    fields: GeometryFields,
    boundary: BoundaryField,
    primary: CandidateRegion,
    config: dict[str, Any],
    candidates: list[CandidateRegion] | None = None,
) -> TopViewResult:
    started = time.perf_counter()
    raster_started = started
    stage_timings: dict[str, float] = {}
    warnings: list[str] = []
    cfg = config["topview"]
    resolution = float(cfg["raster_resolution_mm"])
    supported = [float(value) for value in cfg["supported_resolutions_mm"]]
    if resolution not in supported:
        warnings.append(f"Top-view resolution {resolution:g} mm is experimental; documented calibration values are {supported}")
    margin = float(cfg["margin_mm"])
    overlay_candidates = candidates or [primary]
    bbox_min = np.min(np.vstack([candidate.bbox_min_mm for candidate in overlay_candidates]), axis=0)
    bbox_max = np.max(np.vstack([candidate.bbox_max_mm for candidate in overlay_candidates]), axis=0)
    x_min = math.floor((bbox_min[0] - margin) / resolution) * resolution
    y_min = math.floor((bbox_min[1] - margin) / resolution) * resolution
    x_max = math.ceil((bbox_max[0] + margin) / resolution) * resolution
    y_max = math.ceil((bbox_max[1] + margin) / resolution) * resolution
    grid = _Grid(x_min, y_min, resolution, int(round((x_max - x_min) / resolution)), int(round((y_max - y_min) / resolution)))

    selected_indices = np.flatnonzero(primary.selected_faces)
    selected_centroids = fields.face_centroids[selected_indices]
    row, col, valid = grid.pixels(selected_centroids[:, :2]); row = row[valid]; col = col[valid]
    flat = row * grid.width + col
    pixel_count = grid.height * grid.width
    direct_count = np.bincount(flat, minlength=pixel_count).reshape(grid.height, grid.width).astype(np.float32)
    direct_z = np.bincount(flat, weights=selected_centroids[valid, 2], minlength=pixel_count).reshape(grid.height, grid.width).astype(np.float32)
    direct_hit = direct_count > 0
    direct_height = np.divide(direct_z, direct_count, out=np.zeros_like(direct_z), where=direct_hit)

    selected_face_areas = fields.face_areas[selected_indices]
    sampling_radius_mm = math.sqrt(float(np.percentile(selected_face_areas, 75))) if len(selected_face_areas) else 0.0
    splat = max(
        1,
        int(math.ceil(float(cfg["analysis_splat_radius_mm"]) / resolution)),
        int(math.ceil(sampling_radius_mm / resolution)),
    )
    # The accepted connected face component is the source of truth for the
    # V0.2 envelope.  A scan cut or non-manifold vertex can fragment its exact
    # edge graph into hundreds of open chains, so a single closed graph loop is
    # not a safe raster clip.  Raster-closing face-centroid samples at a physical
    # radius reconstructs the selected surface while retaining larger voids.
    closed_support = _close(direct_hit, splat)
    support_components = _components(closed_support)
    primary_mask = _mask_from_components(
        closed_support.shape, support_components[:1]
    ) if support_components else closed_support
    background_components = _components(~primary_mask)
    exterior_components = [component for component in background_components if component.touches_border]
    exterior = _mask_from_components(primary_mask.shape, exterior_components)
    envelope = ~exterior

    secondary_mask = np.zeros_like(primary_mask)
    secondary_masks_by_id: dict[int, np.ndarray] = {}
    for candidate in overlay_candidates:
        if candidate.is_primary:
            continue
        indices = np.flatnonzero(candidate.selected_faces)
        centroids = fields.face_centroids[indices]
        secondary_row, secondary_col, secondary_valid = grid.pixels(centroids[:, :2])
        hits = np.zeros_like(primary_mask)
        hits[secondary_row[secondary_valid], secondary_col[secondary_valid]] = True
        areas = fields.face_areas[indices]
        secondary_radius = max(
            splat,
            int(math.ceil(math.sqrt(float(np.percentile(areas, 75))) / resolution)) if len(areas) else splat,
        )
        support = _close(hits, secondary_radius)
        components = _components(support)
        if components:
            candidate_mask = _mask_from_components(support.shape, components[:1])
            secondary_masks_by_id[candidate.candidate_id] = candidate_mask
            secondary_mask |= candidate_mask

    expected_z = direct_height.copy(); known = direct_hit.copy()
    for physical_radius in (5.0, 15.0, 40.0, 100.0):
        radius = max(1, int(round(physical_radius / resolution)))
        estimate = _normalized_box(expected_z, known, radius)
        additions = envelope & ~known & (_box_sum(known.astype(np.float32), radius) > 0)
        expected_z[additions] = estimate[additions]; known[additions] = True
    expected_z[~known] = float(np.median(selected_centroids[:, 2]))

    # Give every accepted secondary region its own expected deck height before
    # the original-resolution sampling pass.  This produces real wing
    # perimeters instead of falling back to a tiny accidental graph loop.
    for candidate in overlay_candidates:
        candidate_mask = secondary_masks_by_id.get(candidate.candidate_id)
        if candidate_mask is None:
            continue
        indices = np.flatnonzero(candidate.selected_faces)
        centroids = fields.face_centroids[indices]
        secondary_row, secondary_col, secondary_valid = grid.pixels(centroids[:, :2])
        secondary_flat = secondary_row[secondary_valid] * grid.width + secondary_col[secondary_valid]
        secondary_count = np.bincount(secondary_flat, minlength=pixel_count).reshape(grid.height, grid.width).astype(np.float32)
        secondary_z = np.bincount(
            secondary_flat, weights=centroids[secondary_valid, 2], minlength=pixel_count
        ).reshape(grid.height, grid.width).astype(np.float32)
        secondary_known = secondary_count > 0
        secondary_expected = np.divide(
            secondary_z, secondary_count, out=np.zeros_like(secondary_z), where=secondary_known
        )
        for physical_radius in (5.0, 15.0, 40.0, 100.0):
            radius = max(1, int(round(physical_radius / resolution)))
            estimate = _normalized_box(secondary_expected, secondary_known, radius)
            additions = candidate_mask & ~secondary_known & (_box_sum(secondary_known.astype(np.float32), radius) > 0)
            secondary_expected[additions] = estimate[additions]
            secondary_known[additions] = True
        secondary_expected[~secondary_known] = float(np.median(centroids[:, 2]))
        expected_z[candidate_mask] = secondary_expected[candidate_mask]

    best_distance = np.full(pixel_count, np.inf, dtype=np.float32)
    floor_height_flat = expected_z.ravel().astype(np.float32, copy=True)
    raised_flat = np.zeros(pixel_count, dtype=np.float32)
    normal_sum = np.zeros((3, pixel_count), dtype=np.float32)
    normal_count = np.zeros(pixel_count, dtype=np.float32)
    original_vertices_mm = (
        original.vertices if analysis.mm_per_input_unit == 1.0
        else original.vertices * analysis.mm_per_input_unit
    )
    face_chunk = int(cfg["fine_face_chunk_size"])
    floor_band = float(cfg["floor_sample_band_mm"])
    sample_envelope = envelope | secondary_mask
    for start in range(0, len(original.faces), face_chunk):
        faces = original.faces[start:start + face_chunk]
        triangles = original_vertices_mm[faces]
        centroids = triangles.mean(axis=1)
        chunk_row, chunk_col, in_grid = grid.pixels(centroids[:, :2])
        if not np.any(in_grid):
            continue
        indices = np.flatnonzero(in_grid)
        chunk_flat = chunk_row[indices] * grid.width + chunk_col[indices]
        in_envelope = sample_envelope.ravel()[chunk_flat]
        indices = indices[in_envelope]; chunk_flat = chunk_flat[in_envelope]
        if not len(indices):
            continue
        expected = expected_z.ravel()[chunk_flat]
        delta = centroids[indices, 2] - expected
        np.maximum.at(raised_flat, chunk_flat, np.maximum(delta, 0.0).astype(np.float32))
        area_vectors = np.cross(triangles[indices, 1] - triangles[indices, 0], triangles[indices, 2] - triangles[indices, 0])
        lengths = np.linalg.norm(area_vectors, axis=1)
        normals = area_vectors / np.maximum(lengths[:, None], 1e-12)
        normals[normals[:, 2] < 0] *= -1.0
        floor_like = (np.abs(delta) <= floor_band) & (normals[:, 2] >= math.cos(math.radians(70.0)))
        if not np.any(floor_like):
            continue
        use_flat = chunk_flat[floor_like]; use_delta = np.abs(delta[floor_like]); use_z = centroids[indices[floor_like], 2]
        order = np.lexsort((use_delta, use_flat)); sorted_flat = use_flat[order]
        unique = np.r_[True, sorted_flat[1:] != sorted_flat[:-1]]; chosen = order[unique]
        chosen_flat = use_flat[chosen]; chosen_distance = use_delta[chosen]
        better = chosen_distance < best_distance[chosen_flat]
        best_distance[chosen_flat[better]] = chosen_distance[better].astype(np.float32)
        floor_height_flat[chosen_flat[better]] = use_z[chosen[better]].astype(np.float32)
        accepted_normals = normals[floor_like].astype(np.float32)
        np.add.at(normal_sum[0], use_flat, accepted_normals[:, 0])
        np.add.at(normal_sum[1], use_flat, accepted_normals[:, 1])
        np.add.at(normal_sum[2], use_flat, accepted_normals[:, 2])
        np.add.at(normal_count, use_flat, 1.0)

    floor_height = floor_height_flat.reshape(grid.height, grid.width)
    floor_sample_valid = np.isfinite(best_distance.reshape(grid.height, grid.width)) & sample_envelope
    floor_valid = floor_sample_valid & primary_mask
    floor_height[~floor_sample_valid & sample_envelope] = expected_z[~floor_sample_valid & sample_envelope]
    raised_height = raised_flat.reshape(grid.height, grid.width)
    mean_normal_length = np.linalg.norm(normal_sum, axis=0) / np.maximum(normal_count, 1.0)
    normal_variation = np.degrees(np.arccos(np.clip(mean_normal_length, 0.0, 1.0))).reshape(grid.height, grid.width).astype(np.float32)

    raw_edge_length = np.zeros(pixel_count, dtype=np.float32)
    tensor_xx = np.zeros(pixel_count, dtype=np.float32); tensor_xy = np.zeros(pixel_count, dtype=np.float32); tensor_yy = np.zeros(pixel_count, dtype=np.float32)
    edge_chunk = int(cfg["fine_face_chunk_size"])
    internal = primary.selected_faces[adjacency.face_a] & primary.selected_faces[adjacency.face_b]
    internal_indices = np.flatnonzero(internal)
    internal_turn = boundary.normal_turn_deg_per_mm[internal_indices]
    turn_p50, turn_p95 = np.percentile(internal_turn, [50, 95]) if len(internal_turn) else (0.0, 1.0)
    for start in range(0, len(internal_indices), edge_chunk):
        edge_indices = internal_indices[start:start + edge_chunk]
        u = adjacency.edge_u[edge_indices]; v = adjacency.edge_v[edge_indices]
        p0 = analysis.mesh_mm.vertices[u]; p1 = analysis.mesh_mm.vertices[v]
        midpoint = 0.5 * (p0 + p1)
        edge_row, edge_col, edge_valid = grid.pixels(midpoint[:, :2])
        if not np.any(edge_valid):
            continue
        local = np.flatnonzero(edge_valid); edge_indices = edge_indices[local]
        edge_flat = edge_row[local] * grid.width + edge_col[local]
        on_primary = primary_mask.ravel()[edge_flat]
        local = local[on_primary]; edge_indices = edge_indices[on_primary]; edge_flat = edge_flat[on_primary]
        if not len(edge_indices):
            continue
        direction = p1[local, :2] - p0[local, :2]
        length = np.linalg.norm(direction, axis=1)
        unit = direction / np.maximum(length[:, None], 1e-12)
        strong = boundary.refined_cost[edge_indices] > float(config["segmentation"]["max_boundary_cost"])
        np.add.at(raw_edge_length, edge_flat[strong], length[strong].astype(np.float32))
        turn = np.clip((boundary.normal_turn_deg_per_mm[edge_indices] - turn_p50) / max(turn_p95 - turn_p50, 1e-12), 0.0, 1.0)
        relief = np.maximum(boundary.ridge_strength[edge_indices], boundary.valley_strength[edge_indices])
        weight = length * np.clip(0.65 * turn + 0.35 * relief, 0.0, 1.0)
        np.add.at(tensor_xx, edge_flat, (weight * unit[:, 0] ** 2).astype(np.float32))
        np.add.at(tensor_xy, edge_flat, (weight * unit[:, 0] * unit[:, 1]).astype(np.float32))
        np.add.at(tensor_yy, edge_flat, (weight * unit[:, 1] ** 2).astype(np.float32))
    raw_edge_length = raw_edge_length.reshape(grid.height, grid.width)

    base_radius = max(1, int(round(float(cfg["base_surface_radius_mm"]) / resolution)))
    base_height = _normalized_box(floor_height, primary_mask, base_radius)
    height_residual = np.where(primary_mask, floor_height - base_height, 0.0).astype(np.float32)
    stage_timings["fine_topview_raster_generation_seconds"] = time.perf_counter() - raster_started
    nonskid_started = time.perf_counter()
    nonskid_cfg = config["nonskid"]
    residual_radius = max(1, int(round(float(nonskid_cfg["residual_activity_radius_mm"]) / resolution)))
    residual_activity = _normalized_box(np.abs(height_residual), primary_mask, residual_radius)
    residual_norm, residual_distribution = _robust01(residual_activity, primary_mask)
    normal_norm, normal_distribution = _robust01(normal_variation, primary_mask)

    densities: dict[str, np.ndarray] = {}
    density_distributions: dict[str, dict[str, float]] = {}
    normalized_density: list[np.ndarray] = []
    for scale in [float(value) for value in nonskid_cfg["density_scales_mm"]]:
        radius = max(1, int(round(0.5 * scale / resolution)))
        physical_area = ((2 * radius + 1) * resolution) ** 2
        density = _box_sum(raw_edge_length, radius) / physical_area
        density[~primary_mask] = 0.0
        densities[str(scale)] = density.astype(np.float32)
        normalized, distribution = _robust01(density, primary_mask)
        normalized_density.append(normalized); density_distributions[str(scale)] = distribution
    density_score = np.mean(normalized_density, axis=0).astype(np.float32)
    local_radius = max(1, int(round(float(nonskid_cfg["local_normalization_radius_mm"]) / resolution)))
    local_mean = _normalized_box(density_score, primary_mask, local_radius)
    local_square = _normalized_box(density_score ** 2, primary_mask, local_radius)
    local_std = np.sqrt(np.maximum(local_square - local_mean ** 2, 1e-8))
    local_positive_z = np.clip((density_score - local_mean) / (3.0 * local_std), 0.0, 1.0)
    nonskid_score = np.clip(0.50 * density_score + 0.30 * residual_norm + 0.10 * normal_norm + 0.10 * local_positive_z, 0.0, 1.0)
    score_samples = nonskid_score[primary_mask]
    thresholds = {
        name: float(np.quantile(score_samples, float(nonskid_cfg[f"{name}_quantile"]))) if len(score_samples) else 1.0
        for name in ("loose", "medium", "strict")
    }
    close_radius = max(1, int(round(float(nonskid_cfg["close_gap_mm"]) / resolution)))
    nonskid_masks: dict[str, np.ndarray] = {}
    nonskid_components: dict[str, list[_RasterComponent]] = {}
    for name in ("loose", "medium", "strict"):
        raw_mask = _close((nonskid_score >= thresholds[name]) & primary_mask, close_radius)
        filtered, components = _filter_region_mask(
            raw_mask, float(nonskid_cfg["minimum_region_area_mm2"]), grid,
            int(nonskid_cfg["maximum_regions_per_layer"]),
        )
        nonskid_masks[name] = filtered; nonskid_components[name] = components

    # Percentile thresholds alone always select a fixed fraction of any surface,
    # even a featureless one.  Normal-visible output therefore also requires
    # absolute/effect-size evidence.  Calibration masks remain serialized.
    density_reference = density_distributions.get(str(float(nonskid_cfg["density_scales_mm"][1])), {})
    nonskid_detection_status, nonskid_evidence = classify_nonskid_evidence(
        residual_distribution, density_reference, nonskid_cfg, bool(np.any(primary_mask))
    )
    if nonskid_detection_status != "DETECTED":
        warnings.append(
            "NONSKID ABSTENTION: optional texture evidence was classified as "
            f"{nonskid_detection_status}; no normal-visible nonskid curve was emitted."
        )

    stage_timings["nonskid_analysis_seconds"] = time.perf_counter() - nonskid_started
    obstacle_started = time.perf_counter()
    obstacle_cfg = config["obstacles"]
    obstacle_close = max(1, int(round(float(obstacle_cfg["close_gap_mm"]) / resolution)))
    cleaned_primary = _close(primary_mask, obstacle_close)
    raw_void_mask = envelope & ~primary_mask
    envelope_edge = envelope & ~_erode(envelope, 1)
    void_components = []
    for component in _components(envelope & ~cleaned_primary):
        touches_outer = any(np.any(envelope_edge[row, start:end + 1]) for row, start, end in component.runs)
        if not component.touches_border and not touches_outer:
            void_components.append(component)
    void_areas = np.asarray([component.area_pixels * resolution ** 2 for component in void_components], dtype=float)
    if len(void_areas):
        void_area_distribution = {
            name: float(value) for name, value in zip(
                ("p50", "p75", "p90", "p95", "p99"), np.percentile(void_areas, [50, 75, 90, 95, 99]), strict=True
            )
        }
    else:
        void_area_distribution = {name: 0.0 for name in ("p50", "p75", "p90", "p95", "p99")}
    obstacle_components: dict[str, list[_RasterComponent]] = {}
    obstacle_masks: dict[str, np.ndarray] = {}
    for name in ("loose", "medium", "strict"):
        minimum_area = float(obstacle_cfg[f"{name}_minimum_area_mm2"])
        accepted: list[_RasterComponent] = []
        for component in void_components:
            area = component.area_pixels * resolution ** 2
            if area < minimum_area:
                continue
            values = [raised_height[row, start:end + 1] for row, start, end in component.runs]
            maximum_height = max((float(np.max(value)) for value in values if len(value)), default=0.0)
            if name == "strict" and maximum_height < float(obstacle_cfg["height_confidence_mm"]):
                continue
            accepted.append(component)
        accepted = accepted[:int(obstacle_cfg["maximum_hypotheses_per_layer"])]
        obstacle_components[name] = accepted; obstacle_masks[name] = _mask_from_components(primary_mask.shape, accepted)
    merge_distance = max(1, int(round(float(obstacle_cfg["merge_nested_distance_mm"]) / resolution)))
    obstacle_groups = _group_nearby_components(obstacle_components["loose"], merge_distance)
    medium_ids = {id(component) for component in obstacle_components["medium"]}
    primary_footprints: list[_RasterComponent] = []
    obstacle_alternatives: list[_RasterComponent] = []
    for group in obstacle_groups:
        eligible = [component for component in group if id(component) in medium_ids]
        if not eligible:
            continue
        eligible.sort(key=lambda item: item.area_pixels, reverse=True)
        primary_footprints.append(eligible[0]); obstacle_alternatives.extend(eligible[1:])
    obstacle_components["medium"] = primary_footprints
    obstacle_masks["medium"] = _mask_from_components(primary_mask.shape, primary_footprints)
    obstacle_alternatives_mask = _mask_from_components(primary_mask.shape, obstacle_alternatives)

    group_ids: dict[int, int] = {}
    group_sizes: dict[int, int] = {}
    for group_index, group in enumerate(obstacle_groups, start=1):
        for component in group:
            group_ids[id(component)] = group_index
            group_sizes[id(component)] = len(group)
    loose_ids = {id(component) for component in obstacle_components["loose"]}
    medium_component_ids = {id(component) for component in obstacle_components["medium"]}
    strict_ids = {id(component) for component in obstacle_components["strict"]}
    primary_ids = {id(component) for component in primary_footprints}
    alternative_ids = {id(component) for component in obstacle_alternatives}
    obstacle_records = []
    for index, component in enumerate(void_components, start=1):
        component_id = id(component)
        record = _component_physical_metrics(
            component, grid, floor_height, raised_height, raw_void_mask
        )
        record.update({
            "void_id": index,
            "accepted_loose": component_id in loose_ids,
            "accepted_medium": component_id in medium_component_ids,
            "accepted_strict": component_id in strict_ids,
            "nested_or_nearby_group_id": group_ids.get(component_id),
            "group_member_count": group_sizes.get(component_id, 0),
            "selected_primary_footprint": component_id in primary_ids,
            "preserved_as_debug_alternative": component_id in alternative_ids,
            "micro_void_rejected": component_id not in loose_ids,
        })
        obstacle_records.append(record)

    stage_timings["obstacle_analysis_seconds"] = time.perf_counter() - obstacle_started
    seam_started = time.perf_counter()
    seam_cfg = config["seams"]
    coherence_radius = max(1, int(round(float(seam_cfg["coherence_radius_mm"]) / resolution)))
    xx = _box_sum(tensor_xx.reshape(grid.height, grid.width), coherence_radius)
    xy = _box_sum(tensor_xy.reshape(grid.height, grid.width), coherence_radius)
    yy = _box_sum(tensor_yy.reshape(grid.height, grid.width), coherence_radius)
    coherence = np.sqrt((xx - yy) ** 2 + 4.0 * xy ** 2) / np.maximum(xx + yy, 1e-9)
    activity_norm, activity_distribution = _robust01(xx + yy, primary_mask)
    broad_density = normalized_density[-1] if normalized_density else np.zeros_like(activity_norm)
    line_contrast = np.clip(activity_norm - 0.35 * broad_density, 0.0, 1.0)
    seam_response = (line_contrast * coherence * (1.0 - 0.35 * nonskid_score)).astype(np.float32)
    seam_samples = seam_response[primary_mask]
    seam_thresholds = {
        "high": float(np.quantile(seam_samples, float(seam_cfg["high_response_quantile"]))) if len(seam_samples) else 1.0,
        "low": float(np.quantile(seam_samples, float(seam_cfg["low_response_quantile"]))) if len(seam_samples) else 1.0,
    }
    seam_close = max(1, int(round(float(seam_cfg["close_gap_mm"]) / resolution)))
    seam_masks: dict[str, np.ndarray] = {}
    seam_components: dict[str, list[_RasterComponent]] = {}
    for name in ("high", "low"):
        mask = _close(
            (seam_response >= seam_thresholds[name])
            & (coherence >= float(seam_cfg["minimum_coherence"])) & primary_mask,
            seam_close,
        )
        minimum_length = float(seam_cfg[f"minimum_{name}_confidence_length_mm"])
        components = [
            component for component in _components(mask)
            if max(component.bbox[2] - component.bbox[0] + 1, component.bbox[3] - component.bbox[1] + 1) * resolution >= minimum_length
        ][:int(seam_cfg["maximum_curves_per_layer"])]
        seam_components[name] = components; seam_masks[name] = _mask_from_components(mask.shape, components)
    stage_timings["seam_analysis_seconds"] = time.perf_counter() - seam_started
    contour_started = time.perf_counter()
    feature_curves: list[FeatureCurve] = []
    primary_outer_curve: FeatureCurve | None = None
    secondary_outer_curves: dict[int, FeatureCurve] = {}
    primary_contours = _mask_contours(
        primary_mask, grid, float(config["boundary_curve"]["simplify_tolerance_mm"])
    )
    if primary_contours:
        outer_contour = primary_contours[0]
        outer_points_input = _project_to_floor(
            outer_contour, floor_height, grid, analysis.mm_per_input_unit
        )
        primary_outer_curve = FeatureCurve(
            "AUTODECK::DECK_PRIMARY_OUTER",
            "deck_primary_outer_raster",
            outer_points_input,
            True,
            primary.confidence,
            {
                "projected_area_mm2": abs(_polygon_area(outer_contour)),
                "length_mm": float(
                    np.linalg.norm(np.diff(outer_points_input, axis=0), axis=1).sum()
                    * analysis.mm_per_input_unit
                ),
                "point_count": int(len(outer_contour)),
                "source": "accepted_orientation_component_raster",
            },
        )
    secondary_outer_statistics: list[dict[str, Any]] = []
    for candidate in overlay_candidates:
        candidate_mask = secondary_masks_by_id.get(candidate.candidate_id)
        if candidate_mask is None:
            continue
        contours = _mask_contours(
            candidate_mask, grid, float(config["boundary_curve"]["simplify_tolerance_mm"])
        )
        if not contours:
            continue
        contour = contours[0]
        points_input = _project_to_floor(
            contour, floor_height, grid, analysis.mm_per_input_unit
        )
        length_mm = float(
            np.linalg.norm(np.diff(points_input, axis=0), axis=1).sum()
            * analysis.mm_per_input_unit
        )
        metrics = {
            "candidate_id": candidate.candidate_id,
            "projected_area_mm2": abs(_polygon_area(contour)),
            "length_mm": length_mm,
            "point_count": int(len(contour)),
            "source": "accepted_orientation_component_raster",
        }
        secondary_outer_curves[candidate.candidate_id] = FeatureCurve(
            "AUTODECK::DECK_SECONDARY", f"deck_secondary_{candidate.candidate_id:03d}_raster",
            points_input, True, candidate.confidence, metrics,
        )
        secondary_outer_statistics.append(metrics)
    for name, layer, confidence in (
        ("loose", "AUTODECK_CALIBRATION::NONSKID_LOOSE", 0.35),
        ("medium", "AUTODECK::NONSKID_MEDIUM", 0.65),
        ("strict", "AUTODECK_CALIBRATION::NONSKID_STRICT", 0.90),
    ):
        if name == "medium" and nonskid_detection_status != "DETECTED":
            continue
        feature_curves.extend(_curves_from_mask(
            nonskid_masks[name], layer, f"nonskid_{name}", floor_height, grid,
            analysis.mm_per_input_unit, float(nonskid_cfg["contour_simplify_tolerance_mm"]), confidence,
        ))
    for name, layer, confidence in (
        ("loose", "AUTODECK_CALIBRATION::OBSTACLE_LOOSE", 0.35),
        ("medium", "AUTODECK::OBSTACLES_PRIMARY", 0.70),
        ("strict", "AUTODECK_CALIBRATION::OBSTACLE_STRICT", 0.90),
    ):
        feature_curves.extend(_curves_from_mask(
            obstacle_masks[name], layer, f"obstacle_{name}", floor_height, grid,
            analysis.mm_per_input_unit, float(obstacle_cfg["contour_simplify_tolerance_mm"]), confidence,
        ))
    feature_curves.extend(_curves_from_mask(
        obstacle_alternatives_mask, "AUTODECK_DEBUG::OBSTACLE_ALTERNATIVES", "obstacle_alternative",
        floor_height, grid, analysis.mm_per_input_unit,
        float(obstacle_cfg["contour_simplify_tolerance_mm"]), 0.30,
    ))
    high_seam_curves = _seam_curves_from_components(
        seam_components["high"], seam_response, floor_height, grid, analysis.mm_per_input_unit,
        "AUTODECK::SEAMS_HIGH_CONFIDENCE", "seam_high",
        float(seam_cfg["minimum_high_confidence_length_mm"]),
        float(seam_cfg["closed_endpoint_gap_mm"]), float(seam_cfg["simplify_tolerance_mm"]), 0.85, True,
    )
    low_seam_curves = _seam_curves_from_components(
        seam_components["low"], seam_response, floor_height, grid, analysis.mm_per_input_unit,
        "AUTODECK_CALIBRATION::SEAMS_LOW_CONFIDENCE", "seam_low",
        float(seam_cfg["minimum_low_confidence_length_mm"]),
        float(seam_cfg["closed_endpoint_gap_mm"]), float(seam_cfg["simplify_tolerance_mm"]), 0.50, False,
    )
    seam_detection_status = "DETECTED" if high_seam_curves else (
        "NO_DETECTABLE_SIGNAL" if np.any(primary_mask) else "NOT_EVALUATED"
    )
    feature_curves.extend(high_seam_curves); feature_curves.extend(low_seam_curves)
    stage_timings["feature_contour_generation_seconds"] = time.perf_counter() - contour_started

    diagnostic_started = time.perf_counter()
    debug_dir = output_dir / "debug"; debug_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths = {
        "primary_mask_png": "debug/topview_primary_mask.png",
        "height_residual_png": "debug/topview_height_residual.png",
        "raw_feature_density_png": "debug/topview_raw_feature_density.png",
        "nonskid_loose_png": "debug/topview_nonskid_loose.png",
        "nonskid_medium_png": "debug/topview_nonskid_medium.png",
        "nonskid_strict_png": "debug/topview_nonskid_strict.png",
        "seam_response_png": "debug/topview_seam_response.png",
        "obstacle_mask_png": "debug/topview_obstacle_mask.png",
        "combined_overlay_png": "debug/topview_combined_overlay.png",
        "numeric_maps_npz": "topview_features.npz",
        "topview_debug_directory": "debug/topview",
    }
    _save_png(debug_dir / "topview_primary_mask.png", primary_mask.astype(np.float32), grid, "mask")
    _save_png(debug_dir / "topview_height_residual.png", height_residual, grid, "residual")
    _save_png(debug_dir / "topview_raw_feature_density.png", densities[str(float(nonskid_cfg["density_scales_mm"][1]))], grid)
    for name in ("loose", "medium", "strict"):
        _save_png(debug_dir / f"topview_nonskid_{name}.png", nonskid_masks[name].astype(np.float32), grid, "mask")
    _save_png(debug_dir / "topview_seam_response.png", seam_response, grid)
    _save_png(debug_dir / "topview_obstacle_mask.png", obstacle_masks["medium"].astype(np.float32), grid, "mask")
    _save_combined(debug_dir / "topview_combined_overlay.png", primary_mask, secondary_mask, nonskid_masks["medium"], seam_masks["high"], obstacle_masks["medium"], grid)
    nested_topview_dir = debug_dir / "topview"; nested_topview_dir.mkdir(parents=True, exist_ok=True)
    for diagnostic in debug_dir.glob("topview_*.png"):
        shutil.copy2(diagnostic, nested_topview_dir / diagnostic.name)
    np.savez_compressed(
        output_dir / "topview_features.npz",
        primary_mask=np.packbits(primary_mask, bitorder="little"),
        primary_mask_shape=np.asarray(primary_mask.shape, dtype=np.int32),
        secondary_mask=np.packbits(secondary_mask, bitorder="little"),
        floor_height_mm=floor_height.astype(np.float32),
        height_residual_mm=height_residual,
        raw_feature_edge_length_mm=raw_edge_length,
        feature_density=densities[str(float(nonskid_cfg["density_scales_mm"][1]))],
        nonskid_score=nonskid_score,
        nonskid_loose=np.packbits(nonskid_masks["loose"], bitorder="little"),
        nonskid_medium=np.packbits(nonskid_masks["medium"], bitorder="little"),
        nonskid_strict=np.packbits(nonskid_masks["strict"], bitorder="little"),
        seam_response=seam_response,
        obstacle_height_mm=raised_height,
        x_min_mm=np.asarray([grid.x_min]), y_min_mm=np.asarray([grid.y_min]), resolution_mm=np.asarray([resolution]),
    )
    stage_timings["topview_diagnostic_serialization_seconds"] = time.perf_counter() - diagnostic_started

    statistics: dict[str, Any] = {
        "pixel_resolution_mm": resolution,
        "grid_width_pixels": grid.width,
        "grid_height_pixels": grid.height,
        "physical_extent_mm": [grid.x_max - grid.x_min, grid.y_max - grid.y_min],
        "bbox_mm": [grid.x_min, grid.y_min, grid.x_max, grid.y_max],
        "primary_occupancy_area_mm2": float(np.count_nonzero(primary_mask) * resolution ** 2),
        "analysis_sampling_close_radius_mm": float(splat * resolution),
        "primary_occupancy_to_surface_area_ratio": float(
            np.count_nonzero(primary_mask) * resolution ** 2 / max(primary.area_mm2, 1e-12)
        ),
        "primary_outer_projected_area_mm2": float(
            primary_outer_curve.metrics["projected_area_mm2"] if primary_outer_curve else 0.0
        ),
        "primary_outer_length_mm": float(
            primary_outer_curve.metrics["length_mm"] if primary_outer_curve else 0.0
        ),
        "secondary_occupancy_area_mm2": float(np.count_nonzero(secondary_mask) * resolution ** 2),
        "secondary_outer_curves": secondary_outer_statistics,
        "original_floor_sample_pixel_count": int(np.count_nonzero(floor_valid)),
        "raw_strong_edge_length_mm": float(raw_edge_length.sum()),
        "feature_density_distributions_per_mm": density_distributions,
        "height_residual_activity_distribution_mm": residual_distribution,
        "normal_variation_distribution_deg": normal_distribution,
        "nonskid": {
            "detection_status": nonskid_detection_status,
            "evidence": nonskid_evidence,
            "normal_visible_region_count": len(nonskid_components["medium"]) if nonskid_detection_status == "DETECTED" else 0,
            **{name: {
                "threshold": thresholds[name],
                "region_count": len(nonskid_components[name]),
                "total_area_mm2": float(sum(component.area_pixels for component in nonskid_components[name]) * resolution ** 2),
            } for name in ("loose", "medium", "strict")},
        },
        "seams": {
            "detection_status": seam_detection_status,
            "response_thresholds": seam_thresholds,
            "activity_distribution": activity_distribution,
            "high_confidence_count": sum(curve.layer == "AUTODECK::SEAMS_HIGH_CONFIDENCE" for curve in high_seam_curves),
            "low_confidence_count": len(low_seam_curves),
            "hatch_or_closed_seam_count": sum(curve.layer == "AUTODECK::HATCH_OR_CLOSED_SEAM" for curve in high_seam_curves),
            "total_high_confidence_length_mm": float(sum(curve.metrics["length_mm"] for curve in high_seam_curves)),
        },
        "obstacles": {
            "raw_enclosed_void_count": len(void_components),
            "meaningful_void_count": len(obstacle_components["loose"]),
            "void_area_distribution_mm2": void_area_distribution,
            "loose_count": len(obstacle_components["loose"]),
            "medium_count": len(obstacle_components["medium"]),
            "strict_count": len(obstacle_components["strict"]),
            "nested_group_count": sum(len(group) > 1 for group in obstacle_groups),
            "primary_footprint_count": len(primary_footprints),
            "alternative_footprint_count": len(obstacle_alternatives),
            "micro_void_rejection_count": len(void_components) - len(obstacle_components["loose"]),
            "void_records": obstacle_records,
        },
        "feature_curve_count": len(feature_curves),
        "timings": stage_timings,
        "runtime_seconds": time.perf_counter() - started,
    }
    return TopViewResult(
        feature_curves, primary_outer_curve, secondary_outer_curves,
        statistics, warnings, artifact_paths,
        AcceptedSurfaceGrid(
            grid.x_min,
            grid.y_min,
            resolution,
            primary_mask | secondary_mask,
            floor_height,
            {primary.candidate_id: primary_mask.copy()} | {
                candidate_id: mask.copy() for candidate_id, mask in secondary_masks_by_id.items()
            },
        ),
    )
