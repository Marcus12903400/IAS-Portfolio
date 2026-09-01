import numpy as np

from autodeck2.calibration import Segment
from autodeck2.config import load_config
from autodeck2.ingest import analyze_drawing, chain_segments
from helpers import fillet_rectangle


def _unsnapped_unfinished():
    segments = fillet_rectangle(400.0, 200.0, 30.0)
    # snap off: jitter every endpoint by up to ~2 mm; unfinished: drop one line
    rng = np.random.default_rng(5)
    jittered = []
    for s in segments[:-1]:
        s.start = s.start + rng.uniform(-1.5, 1.5, 2)
        s.end = s.end + rng.uniform(-1.5, 1.5, 2)
        jittered.append(s)
    return jittered


def test_chain_segments_tolerates_gaps_and_reports_open_ends():
    chains, opens, errors = chain_segments(_unsnapped_unfinished(), 5.0, 0.05)
    assert errors == []
    assert len(chains) == 1 and chains[0].closed is False
    assert len(chains[0].segments) == 7
    assert len(opens) == 1 and opens[0]["closing_gap_mm"] > 20.0
    assert len(chains[0].joins()) == 6  # open chain: one join fewer than segments


def test_analyze_drawing_gives_training_summary_for_unfinished_drawing():
    reference = {1: {"outer": np.array([[-2.0, -2.0], [402.0, -2.0], [402.0, 202.0], [-2.0, 202.0]]), "obstacles": []}}
    analysis = analyze_drawing({1: _unsnapped_unfinished()}, np.empty((0, 2)), reference, load_config())
    panel = analysis["panels"][0]
    assert panel["open_chain_count"] == 1
    t = panel["training_summary"]
    assert t["primitives"] == 7 and t["arcs"] == 3 and t["lines"] == 4  # the dropped segment is the last fillet arc
    assert 25.0 < t["arc_radius_mm"]["median"] < 35.0
    # jittered joins: some read as near-tangent (intent), none as marked corners
    assert t["joins"]["marked_corner"] == 0
    assert t["joins"]["tangent"] + t["joins"]["near_tangent"] + t["joins"]["unmarked_corner"] == 6
    d = t["signed_deviation_mm"]
    assert d["median"] > 0  # drawn inside the raw outline
    assert panel["chains"][0]["reference"] == "RAW outer"


def test_analysis_picks_nearest_reference_per_chain():
    outer = fillet_rectangle(400.0, 200.0, 30.0)
    hole = fillet_rectangle(60.0, 40.0, 10.0, origin=(100.0, 80.0))
    reference = {1: {"outer": np.array([[-1.0, -1.0], [401.0, -1.0], [401.0, 201.0], [-1.0, 201.0]]),
                     "obstacles": [np.array([[99.0, 79.0], [161.0, 79.0], [161.0, 121.0], [99.0, 121.0]])]}}
    analysis = analyze_drawing({1: outer + hole}, np.empty((0, 2)), reference, load_config())
    refs = {c["reference"] for c in analysis["panels"][0]["chains"]}
    assert refs == {"RAW outer", "RAW obstacle 1"}
    corner_case = [Segment("LINE", np.array([0.0, 0.0]), np.array([100.0, 0.0])), Segment("LINE", np.array([100.0, 0.0]), np.array([100.0, 100.0]))]
    marked = analyze_drawing({1: corner_case}, np.array([[100.0, 0.0]]), {}, load_config())["panels"][0]["training_summary"]["joins"]
    assert marked["marked_corner"] == 1 and marked["unmarked_corner"] == 0
    unmarked = analyze_drawing({1: corner_case}, np.empty((0, 2)), {}, load_config())["panels"][0]["training_summary"]["joins"]
    assert unmarked["unmarked_corner"] == 1
