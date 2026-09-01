"""Run the V0.3.3 assertions when the bundled pytest launcher is unavailable."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "test_v033_manual_patterns", repo / "tests" / "test_v033_manual_patterns.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load V0.3.3 test module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    simple = [
        module.test_noise_spike_is_suppressed_but_raw_is_preserved_and_cam_is_one_line,
        module.test_persistent_three_mm_feature_survives_robust_reference,
        module.test_broad_regime_solver_recovers_manual_line_arc_complexity_without_vertex_chatter,
        module.test_rotated_boat_axis_is_detected_without_pattern_angle,
        module.test_teak_uses_centerline_spacing_and_clips_off_center_obstacle,
        module.test_manual_tangent_corner_remains_line_arc_line,
    ]
    for test in simple:
        test()
        print(f"PASS {test.__name__}", flush=True)
    with tempfile.TemporaryDirectory(prefix="autodeck-v033-test-") as directory:
        root = Path(directory)
        module.test_diamond_and_hex_dimensions_are_developed_mm_and_shared_edges_are_unique(
            root / "patterns"
        )
        print(
            "PASS test_diamond_and_hex_dimensions_are_developed_mm_and_shared_edges_are_unique",
            flush=True,
        )
        module.test_cached_pattern_rerun_does_not_repeat_scan_or_development(
            root / "cached"
        )
        print(
            "PASS test_cached_pattern_rerun_does_not_repeat_scan_or_development",
            flush=True,
        )


if __name__ == "__main__":
    main()
