from __future__ import annotations

from pathlib import Path

from autodeck.production_output import finalize_production_output


def _make_run(tmp_path: Path) -> Path:
    run = tmp_path / "outputs" / "runs" / "boat-x"
    run.mkdir(parents=True)
    (run / "flattened_curves_polyarc.dxf").write_text("0\nSECTION\n0\nEOF\n", encoding="utf-8")
    return run


def test_relative_run_directory_still_exposes_final_dxf(tmp_path, monkeypatch):
    # Regression: `--output outputs/runs/x` (relative) used to make the
    # finalizer look for the preferred DXF under the artifact directory and
    # silently withhold final.dxf from validated geometry.
    run = _make_run(tmp_path)
    monkeypatch.chdir(tmp_path)
    relative_run = Path("outputs") / "runs" / "boat-x"
    result = finalize_production_output(
        relative_run, relative_run / "flattened_curves_polyarc.dxf", True, [],
    )
    assert result["final_dxf_written"] is True
    assert (run / "final.dxf").is_file()
    assert not (run / "FINAL_NOT_READY.txt").exists()


def test_bare_file_name_resolves_inside_artifacts(tmp_path):
    run = _make_run(tmp_path)
    result = finalize_production_output(run, Path("flattened_curves_polyarc.dxf"), True, [])
    assert result["final_dxf_written"] is True


def test_valid_geometry_with_missing_dxf_names_the_missing_file(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    result = finalize_production_output(run, run / "does_not_exist.dxf", True, [])
    assert result["final_dxf_written"] is False
    notice = (run / "FINAL_NOT_READY.txt").read_text(encoding="utf-8")
    assert "does_not_exist.dxf" in notice
    assert "validated but" in notice
