from __future__ import annotations

from pathlib import Path

import rhino3dm

from autodeck.production_output import finalize_production_output


def _write_line_model(path: Path, layer_name: str, z: float) -> None:
    model = rhino3dm.File3dm()
    layer = rhino3dm.Layer()
    layer.Name = layer_name
    layer_index = model.Layers.Add(layer)
    attributes = rhino3dm.ObjectAttributes()
    attributes.LayerIndex = layer_index
    attributes.Name = layer_name.lower()
    model.Objects.AddLine(
        rhino3dm.Point3d(0.0, 0.0, z),
        rhino3dm.Point3d(100.0, 0.0, z),
        attributes,
    )
    assert model.Write(str(path), 8)


def test_successful_run_has_exact_three_item_contract_and_combined_verification(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    _write_line_model(run / "verification.3dm", "RAW_3D", 10.0)
    _write_line_model(run / "flattened_preview.3dm", "FLAT_CAM_LINEARC", 0.0)
    (run / "flattened_curves_polyarc.dxf").write_bytes(b"valid dxf payload")
    (run / "debug_metrics.json").write_text("{}", encoding="utf-8")
    debug = run / "debug"
    debug.mkdir()
    (debug / "evidence.json").write_text("{}", encoding="utf-8")

    result = finalize_production_output(
        run,
        run / "flattened_curves_polyarc.dxf",
        True,
    )

    assert set(path.name for path in run.iterdir()) == {
        "processing_and_debug",
        "verification.3dm",
        "final.dxf",
    }
    assert result["root_item_count"] == 3
    assert result["final_dxf_written"]
    assert (run / "final.dxf").read_bytes() == b"valid dxf payload"
    assert (run / "processing_and_debug" / "debug_metrics.json").exists()
    assert (run / "processing_and_debug" / "flattened_preview.3dm").exists()
    verification = rhino3dm.File3dm.Read(str(run / "verification.3dm"))
    assert verification is not None
    assert len(verification.Objects) == 2
    assert "AUTODECK_FLAT::FLAT_CAM_LINEARC" in {
        layer.Name for layer in verification.Layers
    }


def test_invalid_run_keeps_review_evidence_but_does_not_expose_final_dxf(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    _write_line_model(run / "verification.3dm", "RAW_3D", 10.0)
    (run / "flattened_curves_polyarc.dxf").write_bytes(b"invalid review dxf")
    (run / "debug_metrics.json").write_text("{}", encoding="utf-8")

    result = finalize_production_output(
        run,
        run / "flattened_curves_polyarc.dxf",
        False,
        ["Join gaps remain.", "Tangency remains outside tolerance."],
    )

    assert set(path.name for path in run.iterdir()) == {
        "processing_and_debug",
        "verification.3dm",
        "FINAL_NOT_READY.txt",
    }
    assert not (run / "final.dxf").exists()
    assert not result["final_dxf_written"]
    notice = (run / "FINAL_NOT_READY.txt").read_text(encoding="utf-8")
    assert "Join gaps remain" in notice
    assert (run / "processing_and_debug" / "flattened_curves_polyarc.dxf").exists()
