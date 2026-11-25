"""The player's animations, stepped at the display's own rate.

Why: Qt steps every QVariantAnimation from one timer every 16 ms, 60 steps a second, whatever the
screen. The legacy Mac player animated on the render server, which draws at the display's rate:
120 a second on a ProMotion screen. Measured on an M3 Pro, the Qt pill grew and shrank in 60 Hz
steps (a picture held for two refreshes now and then) beside the legacy one's 120 Hz.

PacedAnimation is a QVariantAnimation (same values, easing, valueChanged and finished) that,
where a screen refreshes faster than 60 times a second, is stepped by one shared precise timer at
that screen's rate instead of by Qt's. The timer runs only while such an animation runs, so
nothing ticks at rest. With every screen at 60 Hz or slower, or its rate unknown, it is a plain
QVariantAnimation, stepped by Qt exactly as before. Each step sets the animation's time from
the clock, so a late tick never slows the motion; it only skips a picture, as Qt does."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QGuiApplication

log = logging.getLogger("lyssna.ui.motion")

QT_STEP_MS = 16  # Qt's own animation timer (QUnifiedTimer's interval)


def display_rate() -> float:
    """The fastest refresh rate of the connected screens, in Hz; 0 when none says."""
    try:
        return max((float(s.refreshRate()) for s in QGuiApplication.screens()), default=0.0)
    except Exception:
        log.debug("could not read the screens' refresh rates", exc_info=True)
        return 0.0


def step_ms(rate: float) -> int | None:
    """The pacer's interval for a display refreshing `rate` times a second, or None when Qt's own
    16 ms step is as fast (a 60 Hz screen, or an unknown rate)."""
    if rate <= 0:
        return None
    ms = max(1, round(1000.0 / rate))
    return ms if ms < QT_STEP_MS else None


class _Pacer(QObject):
    """The one timer that steps every running PacedAnimation."""

    def __init__(self) -> None:
        super().__init__()
        self.running: list[PacedAnimation] = []
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)

    def add(self, anim: PacedAnimation, interval: int) -> None:
        if anim not in self.running:
            self.running.append(anim)
        self.timer.setInterval(interval)
        if not self.timer.isActive():
            self.timer.start()

    def remove(self, anim: PacedAnimation) -> None:
        if anim in self.running:
            self.running.remove(anim)
        if not self.running:
            self.timer.stop()

    def tick(self) -> None:
        now = time.monotonic()
        for anim in list(self.running):
            try:
                anim._step(now)
            except Exception:
                log.exception("an animation step failed")
                self.remove(anim)


_pacer: _Pacer | None = None


def _get_pacer() -> _Pacer:
    global _pacer
    if _pacer is None:
        _pacer = _Pacer()
    return _pacer


class PacedAnimation(QVariantAnimation):
    """A QVariantAnimation stepped at the display's rate where that is faster than Qt's 60 Hz
    (see the module docstring). Only start() and stop() differ, and only on such a display."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._started: float | None = None  # time.monotonic() at start, while the pacer steps it

    def start(self) -> None:
        interval = step_ms(display_rate())
        if interval is None:
            super().start()
            return
        self._started = time.monotonic()
        self.setCurrentTime(0)
        _get_pacer().add(self, interval)

    def stop(self) -> None:
        if self._started is not None:
            self._started = None
            _get_pacer().remove(self)
            return
        super().stop()

    def paced(self) -> bool:
        """Is the pacer stepping it now (rather than Qt, or nothing)?"""
        return self._started is not None

    def _step(self, now: float) -> None:
        if self._started is None:
            return
        elapsed = round((now - self._started) * 1000)
        duration = self.duration()
        if elapsed < duration:
            self.setCurrentTime(elapsed)
            return
        self._started = None
        _get_pacer().remove(self)
        self.setCurrentTime(duration)
        self.finished.emit()
