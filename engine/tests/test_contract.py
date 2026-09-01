import pytest

from autodeck2 import V1_REQUIRED_VERSION, v1compat


def test_v1_contract_holds():
    info = v1compat.check_compatibility()
    assert info["v1_version"] == V1_REQUIRED_VERSION
    assert len(info["v1_source_fingerprint"]) == 16
    assert info["v1_source_dir"].endswith("autodeck")


def test_signature_drift_is_detected(monkeypatch):
    fn, params = v1compat._EXPECTED_SIGNATURES["development.develop_patch"]
    monkeypatch.setitem(v1compat._EXPECTED_SIGNATURES, "development.develop_patch", (fn, params + ["bogus"]))
    with pytest.raises(v1compat.V1CompatibilityError, match="develop_patch signature changed"):
        v1compat.check_compatibility()


def test_version_drift_is_detected(monkeypatch):
    monkeypatch.setattr(v1compat, "v1_version", lambda: "9.9.9")
    with pytest.raises(v1compat.V1CompatibilityError, match="requires"):
        v1compat.check_compatibility()


def test_fingerprint_changes_with_source(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(v1compat, "v1_source_dir", lambda: tmp_path)
    first = v1compat.v1_source_fingerprint()
    (tmp_path / "a.py").write_text("x = 2\n")
    assert v1compat.v1_source_fingerprint() != first
