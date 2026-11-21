"""The dictation meter: Lyssna's wave (the one in the logo) running down the capsule.

A single sine line, 1.5 cycles over the meter's height, whose sideways swing follows the
microphone level and whose phase scrolls while recording. While the speech is being turned
into text it swings a little, scrolls slowly and breathes in opacity instead: no spinner.

The microphone level arrives on a worker thread about 20 times a second. Nothing here hops
to the main thread per update: the owner stores the latest value and `level_fn` reads it from
a 30 fps display timer that runs on the main thread, and only while the meter is on screen.
The path is rebuilt on each tick with implicit animations off; the colour is resolved in
updateLayer, so it follows light and dark mode like every other layer colour in the app.

The motion maths are plain functions (target_amplitude, ease, wave_points) so they are
tested without a window."""

from __future__ import annotations

from collections.abc import Callable

import objc
import Quartz
from AppKit import NSView
from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

from . import theme
from .shared.wave import (
    AMP_MAX,  # noqa: F401  (re-exported)
    AMP_MIN,
    ATTACK,  # noqa: F401  (re-exported)
    BREATHE_LOW,
    BREATHE_SECS,
    CYCLES,  # noqa: F401  (re-exported)
    FPS,
    RELEASE,  # noqa: F401  (re-exported)
    SPEED,
    STEPS,  # noqa: F401  (re-exported)
    STROKE,
    WAVE_H,
    WAVE_W,
    WORK_AMP,
    WORK_SPEED,
    ease,
    target_amplitude,
    wave_points,
)
from .widgets import _loop


class WaveView(NSView):
    """The meter. set_mode("recording" | "working"), start() / stop() its display timer.
    `horizontal` lays it along a flat capsule (a WAVE_H x WAVE_W frame)."""

    def initWithFrame_(self, frame):
        self = objc.super(WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setWantsLayer_(True)
        self.shape = Quartz.CAShapeLayer.layer()
        self.shape.setLineWidth_(STROKE)
        self.shape.setLineCap_(Quartz.kCALineCapRound)
        self.shape.setLineJoin_(Quartz.kCALineJoinRound)
        self.shape.setFillColor_(None)
        self.layer().addSublayer_(self.shape)
        self.level_fn: Callable[[], float] = lambda: 0.0
        self.horizontal = False
        self.mode = "recording"
        self.amplitude = AMP_MIN
        self.phase = 0.0
        self._timer = None
        self._last = 0.0
        self._draw()
        return self

    def wantsUpdateLayer(self):
        return True

    def updateLayer(self):
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        self.shape.setFrame_(self.bounds())
        self.shape.setStrokeColor_(theme.CONTENT.CGColor())
        Quartz.CATransaction.commit()

    @objc.python_method
    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "working":
            self.shape.addAnimation_forKey_(_loop("opacity", BREATHE_LOW, 1.0, BREATHE_SECS / 2), "breathe")
        else:
            self.shape.removeAnimationForKey_("breathe")

    @objc.python_method
    def reset(self) -> None:
        """A new dictation: start from a calm line."""
        self.amplitude = AMP_MIN
        self.phase = 0.0
        self._draw()

    @objc.python_method
    def start(self) -> None:
        if self._timer is not None:
            return
        self._last = Quartz.CACurrentMediaTime()
        self._timer = NSTimer.timerWithTimeInterval_repeats_block_(1.0 / FPS, True, lambda _t: self.tick())
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    @objc.python_method
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    @objc.python_method
    def running(self) -> bool:
        return self._timer is not None

    @objc.python_method
    def tick(self, now: float | None = None) -> None:
        now = Quartz.CACurrentMediaTime() if now is None else now
        dt = min(0.1, max(0.0, now - self._last))  # a stalled run loop must not make the wave jump
        self._last = now
        if self.mode == "working":
            target, speed = WORK_AMP, WORK_SPEED
        else:
            try:
                level = float(self.level_fn())
            except Exception:
                level = 0.0
            target, speed = target_amplitude(level), SPEED
        self.amplitude = ease(self.amplitude, target, dt)
        self.phase = (self.phase + speed * dt) % 1.0
        self._draw()

    @objc.python_method
    def _draw(self) -> None:
        w, h = self.bounds().size
        path = Quartz.CGPathCreateMutable()
        default = (WAVE_H, WAVE_W) if self.horizontal else (WAVE_W, WAVE_H)
        points = wave_points(self.amplitude, self.phase, w or default[0], h or default[1], horizontal=self.horizontal)
        for i, (x, y) in enumerate(points):
            if i == 0:
                Quartz.CGPathMoveToPoint(path, None, x, y)
            else:
                Quartz.CGPathAddLineToPoint(path, None, x, y)
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        self.shape.setPath_(path)
        Quartz.CATransaction.commit()
