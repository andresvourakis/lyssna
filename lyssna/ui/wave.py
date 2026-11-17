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

import math
from collections.abc import Callable

import objc
import Quartz
from AppKit import NSView
from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

from . import theme
from .widgets import _loop

WAVE_W, WAVE_H = 14.0, 36.0
CYCLES = 1.5           # sine cycles over the meter's height
STROKE = 2.5
AMP_MIN, AMP_MAX = 1.0, 5.5   # sideways swing in points, silence to full level
ATTACK, RELEASE = 0.06, 0.25  # seconds: the swing grows fast and settles slowly
SPEED = 1.2            # cycles per second the phase scrolls while recording
WORK_AMP, WORK_SPEED = 2.5, 0.5
BREATHE_LOW, BREATHE_SECS = 0.45, 1.4  # working: opacity 0.45 to 1.0 and back over 1.4 s
FPS = 30.0
STEPS = 48             # line segments in the path


def target_amplitude(level: float) -> float:
    """The swing for a microphone level in 0..1."""
    level = min(1.0, max(0.0, level))
    return AMP_MIN + (AMP_MAX - AMP_MIN) * level


def ease(current: float, target: float, dt: float) -> float:
    """Move `current` toward `target` over `dt` seconds: quickly when rising (ATTACK), more
    slowly when falling (RELEASE), the way a level meter does."""
    tau = ATTACK if target > current else RELEASE
    return current + (target - current) * (1.0 - math.exp(-max(0.0, dt) / tau))


def wave_points(amplitude: float, phase: float, width: float = WAVE_W, height: float = WAVE_H,
                cycles: float = CYCLES, steps: int = STEPS) -> list[tuple[float, float]]:
    """Points of the wave, top to bottom of a width x height area, centred sideways. The
    ends sit half a stroke in from the edges so the round caps stay inside."""
    top, span = STROKE / 2, height - STROKE
    cx = width / 2
    return [(cx + amplitude * math.sin(2 * math.pi * (cycles * i / steps + phase)), top + span * i / steps)
            for i in range(steps + 1)]


class WaveView(NSView):
    """The meter. set_mode("recording" | "working"), start() / stop() its display timer."""

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
        for i, (x, y) in enumerate(wave_points(self.amplitude, self.phase, w or WAVE_W, h or WAVE_H)):
            if i == 0:
                Quartz.CGPathMoveToPoint(path, None, x, y)
            else:
                Quartz.CGPathAddLineToPoint(path, None, x, y)
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        self.shape.setPath_(path)
        Quartz.CATransaction.commit()
