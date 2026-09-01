"""Real Key West scan run. Minutes long; only runs when AUTODECK2_REAL_SCAN=1
and the scan is present."""

import os
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from autodeck2.config import load_config
from autodeck2.outline3dm import read_outline_summary
from autodeck2.pipeline import run_outline

SCAN = Path.home() / "Downloads" / "21kwcockpit.obj"

pytestmark = pytest.mark.slow


@pytest.mark.skipif(not (os.environ.get("AUTODECK2_REAL_SCAN") and SCAN.is_file()), reason="set AUTODECK2_REAL_SCAN=1 with the Key West scan present")
def test_key_west_outline_has_no_overlaps_and_assigns_features(tmp_path):
    summary = run_outline(SCAN, load_config(), units="mm", layout_mode="nest", teak_enabled=True, run_dir=tmp_path / "run", cache_root=tmp_path / "cache")
    assert summary["status"] == "OK"
    outline = read_outline_summary(tmp_path / "run" / "outline.3dm")
    polys = {pid: Polygon(pts) for pid, pts in outline["outers"].items()}
    for a in polys:
        for b in polys:
            if a < b:
                assert polys[a].intersection(polys[b]).area <= 1.0
    assert outline["layers"]["USER_CAM::PANEL_1"]["count"] == 0
    second = run_outline(SCAN, load_config(), units="mm", layout_mode="nest", teak_enabled=True, run_dir=tmp_path / "run2", cache_root=tmp_path / "cache")
    assert second["cache_hit"] is True
