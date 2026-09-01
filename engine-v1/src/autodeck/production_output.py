from __future__ import annotations

"""Compact user-facing run layout with all evidence retained underneath it."""

from pathlib import Path
import shutil
from typing import Any


PROCESSING_DIRECTORY = "processing_and_debug"
VERIFICATION_FILE = "verification.3dm"
FINAL_DXF = "final.dxf"
FAILURE_NOTICE = "FINAL_NOT_READY.txt"


def processing_directory(run_directory: Path) -> Path:
    """Return the artifact directory for either a legacy or compact run."""

    root = Path(run_directory)
    nested = root / PROCESSING_DIRECTORY
    if (nested / "debug_metrics.json").exists():
        return nested
    return root


def _add_layer(model: Any, rhino3dm: Any, source_layer: Any, prefix: str) -> int:
    name = f"{prefix}::{source_layer.Name}"
    for index, layer in enumerate(model.Layers):
        if layer.Name == name:
            return index
    layer = rhino3dm.Layer()
    layer.Name = name
    layer.Visible = bool(source_layer.Visible)
    try:
        layer.Color = source_layer.Color
    except (AttributeError, TypeError):
        pass
    return model.Layers.Add(layer)


def _append_model(target: Any, source_path: Path, rhino3dm: Any, prefix: str) -> int:
    source = rhino3dm.File3dm.Read(str(source_path))
    if source is None:
        return 0
    layer_map = {
        index: _add_layer(target, rhino3dm, layer, prefix)
        for index, layer in enumerate(source.Layers)
    }
    count = 0
    for item in source.Objects:
        attributes = rhino3dm.ObjectAttributes()
        attributes.LayerIndex = layer_map.get(int(item.Attributes.LayerIndex), 0)
        attributes.Name = item.Attributes.Name
        if target.Objects.Add(item.Geometry, attributes):
            count += 1
    return count


def _remove_prefixed_objects(model: Any, prefix: str) -> None:
    layer_indices = {
        index
        for index, layer in enumerate(model.Layers)
        if layer.Name.startswith(prefix + "::")
    }
    object_ids = [
        item.Attributes.Id
        for item in model.Objects
        if int(item.Attributes.LayerIndex) in layer_indices
    ]
    for object_id in object_ids:
        model.Objects.Delete(object_id)


def merge_verification_geometry(
    verification_path: Path,
    flattened_preview_path: Path | None,
    pattern_preview_path: Path | None = None,
) -> dict[str, Any]:
    """Add flat CAM/reference/pattern objects to the existing 3-D review file."""

    try:
        import rhino3dm  # type: ignore
    except ImportError:
        return {"written": False, "warning": "rhino3dm unavailable"}
    verification_path = Path(verification_path)
    model = (
        rhino3dm.File3dm.Read(str(verification_path))
        if verification_path.exists() else rhino3dm.File3dm()
    )
    if model is None:
        return {"written": False, "warning": "verification model could not be opened"}
    flat_count = 0
    pattern_count = 0
    _remove_prefixed_objects(model, "AUTODECK_FLAT")
    _remove_prefixed_objects(model, "AUTODECK_PATTERN_FLAT")
    if flattened_preview_path is not None and Path(flattened_preview_path).exists():
        flat_count = _append_model(
            model, Path(flattened_preview_path), rhino3dm, "AUTODECK_FLAT"
        )
    if pattern_preview_path is not None and Path(pattern_preview_path).exists():
        pattern_count = _append_model(
            model, Path(pattern_preview_path), rhino3dm, "AUTODECK_PATTERN_FLAT"
        )
    written = bool(model.Write(str(verification_path), 8))
    return {
        "written": written,
        "object_count": len(model.Objects) if written else 0,
        "flattened_object_count_added": flat_count,
        "pattern_object_count_added": pattern_count,
    }


def _move_into_processing(entry: Path, destination: Path) -> None:
    target = destination / entry.name
    if entry.is_dir():
        if target.exists():
            for child in list(entry.iterdir()):
                _move_into_processing(child, target)
            entry.rmdir()
        else:
            shutil.move(str(entry), str(target))
        return
    if target.exists():
        target.unlink()
    shutil.move(str(entry), str(target))


def finalize_production_output(
    run_directory: Path,
    preferred_dxf: Path | None,
    final_geometry_valid: bool,
    failure_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Leave exactly three useful root entries while retaining every artifact."""

    root = Path(run_directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifacts = processing_directory(root)
    verification = root / VERIFICATION_FILE
    source_verification = artifacts / VERIFICATION_FILE
    if not verification.exists() and source_verification.exists():
        shutil.copy2(source_verification, verification)
    flat_preview = artifacts / "flattened_preview.3dm"
    pattern_preview = artifacts / "pattern_preview.3dm"
    merge_metrics = merge_verification_geometry(
        verification,
        flat_preview if flat_preview.exists() else None,
        pattern_preview if pattern_preview.exists() else None,
    )

    processing = root / PROCESSING_DIRECTORY
    processing.mkdir(parents=True, exist_ok=True)
    final_path = root / FINAL_DXF
    notice_path = root / FAILURE_NOTICE
    preferred = None if preferred_dxf is None else Path(preferred_dxf)
    if preferred is not None and not preferred.is_absolute():
        # A relative run directory (e.g. `--output outputs/runs/x`) makes the
        # pipeline pass a relative preferred path; resolve it against the
        # working directory first and only fall back to a bare file name
        # inside the artifact directory.  Treating it as artifact-relative
        # unconditionally silently withheld final.dxf from valid geometry.
        resolved = preferred.resolve()
        preferred = resolved if resolved.is_file() else artifacts / preferred.name
    final_written = bool(
        final_geometry_valid and preferred is not None and preferred.is_file()
    )
    if final_geometry_valid and not final_written:
        failure_reasons = list(failure_reasons or []) + [
            f"Geometry validated but the preferred DXF was not found at {preferred}."
        ]
    if final_written:
        shutil.copy2(preferred, final_path)
        if notice_path.exists():
            _move_into_processing(notice_path, processing)
    else:
        if final_path.exists():
            _move_into_processing(final_path, processing)
        reasons = failure_reasons or [
            "The CAM perimeter did not pass closure, tangency, fit, safety, and export validation."
        ]
        notice_path.write_text(
            "FINAL DXF NOT CREATED\n\n"
            "AutoDeck retained the complete review geometry and diagnostics, but it did not "
            "expose an unsafe or invalid file as final.dxf.\n\n"
            + "\n".join(f"- {reason}" for reason in reasons)
            + "\n",
            encoding="utf-8",
        )

    keep = {VERIFICATION_FILE, FINAL_DXF if final_written else FAILURE_NOTICE, PROCESSING_DIRECTORY}
    for entry in list(root.iterdir()):
        if entry.name not in keep:
            _move_into_processing(entry, processing)

    root_items = sorted(path.name for path in root.iterdir())
    return {
        "contract": "THREE_ITEM_PRODUCTION_OUTPUT_V1",
        "processing_directory": PROCESSING_DIRECTORY,
        "verification_file": VERIFICATION_FILE if verification.exists() else None,
        "final_dxf": FINAL_DXF if final_written else None,
        "failure_notice": None if final_written else FAILURE_NOTICE,
        "preferred_source_dxf": None if preferred is None else preferred.name,
        "final_geometry_valid": bool(final_geometry_valid),
        "final_dxf_written": final_written,
        "root_item_count": len(root_items),
        "root_items": root_items,
        "verification_merge": merge_metrics,
    }
