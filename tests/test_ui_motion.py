"""The player's animations step at the display's rate where that is faster than Qt's 60 Hz, and
exactly as before elsewhere (lyssna/ui/motion.py). Offscreen, with the display's rate faked."""

import time
from itertools import pairwise

import pytest
from PySide6.QtCore import QCoreApplication, QVariantAnimation

from lyssna.ui import motion


def run(anim, seconds: float = 1.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and (anim.paced() or anim.state() == QVariantAnimation.State.Running):
        QCoreApplication.processEvents()
        time.sleep(0.001)


def test_the_step_follows_the_display_and_leaves_60_hz_to_qt():
    assert motion.step_ms(120.0) == 8 and motion.step_ms(144.0) == 7 and motion.step_ms(90.0) == 11
    assert motion.step_ms(60.0) is None and motion.step_ms(59.94) is None and motion.step_ms(0.0) is None


def _anim(seen, done):
    a = motion.PacedAnimation()
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setDuration(120)
    a.valueChanged.connect(lambda v: seen.append((time.monotonic(), float(v))))
    a.finished.connect(lambda: done.append(1))
    return a


def test_on_a_120_hz_display_the_pacer_steps_it_about_every_8_ms(qapp, monkeypatch):
    monkeypatch.setattr(motion, "display_rate", lambda: 120.0)
    seen, done = [], []
    a = _anim(seen, done)
    a.start()
    assert a.paced() and motion._get_pacer().timer.interval() == 8
    run(a)
    assert done == [1] and seen[-1][1] == 1.0 and not a.paced()
    assert not motion._get_pacer().timer.isActive()  # nothing ticks at rest
    values = [v for _t, v in seen]
    assert values == sorted(values) and len(values) >= 8  # far more than Qt's 16 ms steps would give
    gaps = sorted(b[0] - a_[0] for a_, b in pairwise(seen))
    assert gaps[len(gaps) // 2] < 0.013


def test_stopping_a_paced_animation_ends_it_without_finishing(qapp, monkeypatch):
    monkeypatch.setattr(motion, "display_rate", lambda: 120.0)
    seen, done = [], []
    a = _anim(seen, done)
    a.start()
    QCoreApplication.processEvents()
    a.stop()
    assert not a.paced() and not motion._get_pacer().timer.isActive()
    run(a, 0.2)
    assert done == []


@pytest.mark.parametrize("rate", [60.0, 0.0])
def test_on_a_60_hz_display_it_is_qts_own_animation(qapp, monkeypatch, rate):
    monkeypatch.setattr(motion, "display_rate", lambda: rate)
    seen, done = [], []
    a = _anim(seen, done)
    a.start()
    assert not a.paced() and a.state() == QVariantAnimation.State.Running
    run(a)
    assert done == [1] and seen[-1][1] == 1.0
