"""The live behaviour harness's pure parts (scripts/behave), with made-up frames and facts: when a
picture first changes, when it settles, how many steps an animation shows, and how the report
sets two interfaces' facts side by side. The harness itself drives a real Mac."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.behave import frames as F
from scripts.behave import report as R


def _px(level: int) -> np.ndarray:
    """A 20 x 20 picture whose first `level` rows are white, the rest black."""
    p = np.zeros((20, 20, 4), dtype=np.uint8)
    p[:level] = 255
    return p


def _film(times_levels):
    return [F.Frame(t, _px(level)) for t, level in times_levels]


def test_changed_counts_pixels_past_the_delta():
    assert F.changed(_px(0), _px(0)) == 0
    assert F.changed(_px(0), _px(2)) == 40
    small = _px(0)
    small[0, 0, 0] = F.PIXEL_DELTA  # not past the delta
    assert F.changed(_px(0), small) == 0
    assert F.changed(_px(0), np.zeros((10, 10, 4), dtype=np.uint8)) == 400  # another size is all changed


def test_motion_times_an_animation_from_its_cause():
    film = _film([(0.00, 0), (0.01, 0), (0.02, 0), (0.03, 4), (0.04, 8), (0.05, 8), (0.06, 12), (0.07, 16), (0.08, 16),
                  (0.09, 16)])
    m = F.motion(film, 0.015, _px(0), _px(16))
    assert m["first_change_ms"] == 15.0
    assert m["settled_ms"] == 55.0
    assert m["animation_ms"] == 40.0
    assert m["frames_moved"] == 4  # 4, 8, 12, 16
    assert m["repeated_frames"] == 1  # the second 8
    assert m["largest_step_gap_ms"] == 20.0


def test_motion_with_no_change_and_settle_bounded_by_until():
    film = _film([(0.0, 0), (0.1, 0)])
    assert F.motion(film, 0.0, _px(0), _px(16)) == {"first_change_ms": None}
    film = _film([(0.0, 0), (0.1, 16), (0.2, 16), (0.3, 0)])  # opened, then closed again after `until`
    assert F.settled(film, 0.05, _px(16)) is None
    assert F.settled(film, 0.05, _px(16), until=0.25).t == 0.1


def test_flatten_keeps_medians_and_skips_noise():
    flat = R.flatten({"a": {"median": 3, "min": 1, "max": 5, "n": 4}, "trials": [1], "paint_open": 2,
                      "rows": [{"title": "Quit", "id": 7}]})
    assert flat == {"a.median": 3, "rows[0].title": "Quit"}


def test_same_allows_timing_slack_only():
    assert R.same("open_ms", 100.0, 112.0)
    assert not R.same("open_ms", 10.0, 30.0)
    assert R.same("close_ms", 300.0, 340.0)  # within 15 percent
    assert not R.same("count", 3, 4)
    assert R.same("median", 100.0, 104.0)  # within 5 percent
    assert not R.same("median", 100.0, 110.0)
    assert not R.same("flag", True, 1)
    assert R.same("dock", "left", "left")


def test_compare_verdicts():
    assert R.compare({"x": 1}, {"x": 1}) == ("same", [])
    assert R.compare({"x": 1}, {"x": 9}) == ("differs", [("x", 1, 9)])
    assert R.compare({"error": "no"}, {"x": 1})[0] == "could not be exercised"
    assert R.compare(None, {"x": 1})[0] == "could not be exercised"


def test_write_report(tmp_path):
    path = R.write_report(tmp_path, {"menu": {"legacy": {"rows": 3}, "new": {"rows": 4}}}, notes=["muted"], stopped=None)
    text = path.read_text()
    assert "| menu | differs | 1 |" in text
    assert "| rows | 3 | 4 |" in text
    assert "- muted" in text
