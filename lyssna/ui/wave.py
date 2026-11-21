"""The dictation meter: Lyssna's wave (the one in the logo) running down the capsule, the twin
of legacy/ui/wave.py.

A single sine line, 1.5 cycles over the meter's height, whose sideways swing follows the
microphone level and whose phase scrolls while recording. While the speech is being turned into
text it swings a little, scrolls slowly and breathes in opacity (0.45 to 1.0 and back over
1.4 s) instead: no spinner.

The microphone level arrives on a worker thread about 20 times a second. Nothing here hops to
the GUI thread per update: the owner stores the latest value and `level_fn` reads it from a
30 fps QTimer that runs only while the meter is on screen (start() and stop()). The motion
maths are layout/wave.py's; its points are y-up like the Mac's, flipped here into y-down."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme
from .layout.wave import (
    AMP_MIN,
    BREATHE_LOW,
    BREATHE_SECS,
    FPS,
    SPEED,
    STROKE,
    WAVE_H,
    WAVE_W,
    WORK_AMP,
    WORK_SPEED,
    ease,
    target_amplitude,
    wave_points,
)


def breathe(t: float) -> float:
    """The working meter's opacity `t` seconds into its breath: BREATHE_LOW to 1.0 and back over
    BREATHE_SECS, eased in and out (the Mac's autoreversing ease-in-out loop)."""
    u = (t % BREATHE_SECS) / BREATHE_SECS
    tri = 1.0 - abs(2.0 * u - 1.0)  # 0 -> 1 -> 0
    return BREATHE_LOW + (1.0 - BREATHE_LOW) * (1.0 - math.cos(math.pi * tri)) / 2


class WaveView(QWidget):
    """The meter. set_mode("recording" | "working"), start() / stop() its display timer.
    `horizontal` lays it along a flat capsule (a WAVE_H x WAVE_W widget)."""

    def __init__(self, parent: QWidget | None = None, horizontal: bool = False):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.level_fn: Callable[[], float] = lambda: 0.0
        self.horizontal = horizontal
        self.mode = "recording"
        self.amplitude = AMP_MIN
        self.phase = 0.0
        self.opacity = 1.0
        self._breath_start = 0.0
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(round(1000 / FPS))
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.tick)
        w, h = (WAVE_H, WAVE_W) if horizontal else (WAVE_W, WAVE_H)
        self.resize(round(w), round(h))
        self._path = QPainterPath()
        self._build()

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self._breath_start = time.monotonic()
        self.opacity = 1.0 if mode != "working" else breathe(0.0)
        self.update()

    def reset(self) -> None:
        """A new dictation: start from a calm line."""
        self.amplitude = AMP_MIN
        self.phase = 0.0
        self._build()

    def start(self) -> None:
        if self._timer.isActive():
            return
        self._last = time.monotonic()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def running(self) -> bool:
        return self._timer.isActive()

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        dt = min(0.1, max(0.0, now - self._last))  # a stalled event loop must not make the wave jump
        self._last = now
        if self.mode == "working":
            target, speed = WORK_AMP, WORK_SPEED
            self.opacity = breathe(now - self._breath_start)
        else:
            try:
                level = float(self.level_fn())
            except Exception:
                level = 0.0
            target, speed = target_amplitude(level), SPEED
            self.opacity = 1.0
        self.amplitude = ease(self.amplitude, target, dt)
        self.phase = (self.phase + speed * dt) % 1.0
        self._build()

    def path(self) -> QPainterPath:
        """The line as drawn now, widget coordinates."""
        return QPainterPath(self._path)

    def resizeEvent(self, ev) -> None:
        self._build()
        super().resizeEvent(ev)

    def _build(self) -> None:
        w, h = float(self.width()), float(self.height())
        default = (WAVE_H, WAVE_W) if self.horizontal else (WAVE_W, WAVE_H)
        w, h = w or default[0], h or default[1]
        points = wave_points(self.amplitude, self.phase, w, h, horizontal=self.horizontal)
        path = QPainterPath()
        for i, (x, y) in enumerate(points):
            pt = QPointF(x, h - y)  # the Mac's y-up into Qt's y-down
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        self._path = path
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(self.opacity)
        pen = QPen(theme.content(), STROKE)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._path)
        p.end()


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------


def _preview_wave() -> QWidget:
    """The meter in both modes and both orientations, a few frames in, on dictation capsules."""
    from .layout.pill import CAP_W, COMPACT_H
    from .preview import card
    from .widgets import HoverView

    col = 90.0
    c = card(16 + 4 * col + 16, 120)
    x = 16.0
    for mode, horizontal in (("recording", False), ("working", False), ("recording", True), ("working", True)):
        cw, ch = (COMPACT_H, CAP_W) if horizontal else (CAP_W, COMPACT_H)
        cap = HoverView(c, fill=lambda: theme.surface(0.97), radius=None, border=theme.hairline)
        cap.setGeometry(round(x + (col - cw) / 2), round(18 + (COMPACT_H - ch) / 2), round(cw), round(ch))
        wv = WaveView(cap, horizontal=horizontal)
        wv.move(round((cw - wv.width()) / 2), round((ch - wv.height()) / 2))
        wv.level_fn = lambda: 0.8
        wv.set_mode(mode)
        wv._last = wv._breath_start = 0.0
        for i in range(1, 13):  # a few frames in, so the line swings
            wv.tick(i / 30)
        c.caption(mode, x, 92, col, 14, Qt.AlignmentFlag.AlignHCenter)
        x += col
    return c


PREVIEWS = {"wave": _preview_wave}
