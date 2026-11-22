"""The docked progress capsule and the idle squiggle, the twin of Sliver in legacy/ui/widgets.py.

Docked during a reading, the sliver is a slim capsule flush with the screen edge whose fill shows
progress from the bottom up. The fill is alive while reading: a slowly drifting gradient body,
soft white sparks rising through it, a breathing glow at the waterline and a small bloom each
time a sentence lands. Paused, no new sparks are born, the drift stops and the fill dims. On the
bottom edge the whole capsule lies flat, turned a quarter clockwise, so the fill grows from the
left and the sparks drift right.

At rest (set_rest(True), the idle pill when nothing plays) there is no capsule, only the wave from
the Lyssna logo: a dark grey line over a slightly wider soft white halo, the same in light and dark
mode. It is still but for wave(): one ripple running along it, when the owner asks.

The Mac builds this from Core Animation layers: a gradient layer with three looping drifts, a
particle emitter and a keyframed path. Qt has none of these, so everything is painted here from a
few numbers (the same numbers the Mac's layers use) and one QTimer drives them all. The timer runs
only while the view is on screen and something moves (a reading playing, sparks still aloft, the
waterline easing, a bloom, a ripple), so a paused or hidden sliver costs nothing. With
theme.reduce_motion() the view paints the still state.

Sizes and the squiggle's points come from layout/wave.py in the Mac's y-up coordinates; they are
flipped here into Qt's y-down widget coordinates."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient, QTransform
from PySide6.QtWidgets import QWidget

from . import theme
from .layout.wave import (
    CAPSULE_H,
    CAPSULE_W,
    SLIVER_H,
    SLIVER_W,
    SQUIGGLE_HALO,
    SQUIGGLE_STROKE,
    WAVE_CYCLES,
    WAVE_SECS,
    capsule_centre,
    squiggle_points,
    squiggle_size,
)

CAP = 7.0              # height of the waterline glow
BLOOM = 22.0           # diameter of the sentence bloom
BLOOM_SECS = 0.75
WATERLINE_SECS = 0.35  # the waterline eases up (or back on a seek) over this long
DIM = 0.45             # the fill's opacity while paused

# The drifts, each a slow ease-in-out ping-pong (seconds one way). Unrelated periods layered
# together stop looking like a loop at all.
HUE_SECS = 6.7         # the light wanders
SPLIT_SECS = 5.3       # the split between the tones shifts
SWING_SECS = 8.1       # the ends of the gradient swap
BREATHE_SECS = 2.6     # the waterline glow breathes
BREATHE_LOW = 0.45
STILL_BREATHE = 0.72   # the glow's opacity when motion is reduced

# The sparks: the Mac emitter cell's numbers.
SPARK_RATE = 2.2                      # born per second while playing
SPARK_LIFE, SPARK_LIFE_RANGE = 2.4, 0.8
SPARK_SPEED, SPARK_SPEED_RANGE = 11.0, 6.0
SPARK_SWAY = 0.35                     # radians of the cone they rise in
SPARK_SCALE_SPEED, SPARK_SCALE_RANGE = 0.34, 0.08
SPARK_ALPHA, SPARK_FADE = 0.95, 0.42  # start opacity, and how much it loses per second
SPARK_SIZE = 12.0                     # the dot at scale 1

FPS = 30.0       # sparks and drifts
WAVE_FPS = 60.0  # the ripple, which moves faster


def bezier(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    """A cubic-bezier timing curve, as Core Animation names its timing functions."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


EASE_OUT = bezier(0.0, 0.0, 0.58, 1.0)      # kCAMediaTimingFunctionEaseOut
EASE_IN = bezier(0.42, 0.0, 1.0, 1.0)       # kCAMediaTimingFunctionEaseIn
EASE_IN_OUT = bezier(0.42, 0.0, 0.58, 1.0)  # kCAMediaTimingFunctionEaseInEaseOut
EASE_DEFAULT = bezier(0.25, 0.1, 0.25, 1.0)  # kCAMediaTimingFunctionDefault: implicit layer animations, window fades


def drift(t: float, secs: float) -> float:
    """Where an autoreversing ease-in-out loop of `secs` one way is at `t` seconds: 0 to 1 and back."""
    u = (t % (2 * secs)) / secs
    return EASE_IN_OUT.valueForProgress(u if u <= 1.0 else 2.0 - u)


def blend(a: QColor, b: QColor, f: float) -> QColor:
    """`a` blended toward `b` by `f` (NSColor.blendedColorWithFraction:ofColor:)."""
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * f, a.greenF() + (b.greenF() - a.greenF()) * f,
                           a.blueF() + (b.blueF() - a.blueF()) * f, a.alphaF() + (b.alphaF() - a.alphaF()) * f)


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _encoded(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * max(0.0, c) ** (1 / 2.4) - 0.055


def blend_linear(a: QColor, b: QColor, f: float) -> QColor:
    """`a` blended toward `b` by `f` in linear light: how Core Animation moves a layer's colours
    from one value to another (its midpoints lighter than a blend of the sRGB numbers)."""
    rgb = [_encoded(_linear(x) + (_linear(y) - _linear(x)) * f)
           for x, y in ((a.redF(), b.redF()), (a.greenF(), b.greenF()), (a.blueF(), b.blueF()))]
    return QColor.fromRgbF(*rgb, a.alphaF() + (b.alphaF() - a.alphaF()) * f)


def ripple_phase(age: float) -> float:
    """The squiggle's phase `age` seconds into a ripple: eased in and out, 0 at both ends (the
    continuous form of layout.wave.wave_phases)."""
    u = min(1.0, max(0.0, age / WAVE_SECS))
    return (WAVE_CYCLES * (1 - math.cos(math.pi * u)) / 2) % 1.0


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    r = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    path.addRoundedRect(rect, r, r)
    return path


class _Spark:
    """One soft white dot rising through the fill, in the upright capsule's coordinates (origin
    at its centre, y down)."""

    __slots__ = ("age", "life", "s0", "vx", "vy", "x0", "y0")

    def __init__(self, x0: float, y0: float, vx: float, vy: float, life: float, s0: float):
        self.x0, self.y0, self.vx, self.vy, self.life, self.s0 = x0, y0, vx, vy, life, s0
        self.age = 0.0

    def alpha(self) -> float:
        return max(0.0, SPARK_ALPHA - SPARK_FADE * self.age)

    def pos(self) -> tuple[float, float]:
        return self.x0 + self.vx * self.age, self.y0 + self.vy * self.age

    def size(self) -> float:
        return SPARK_SIZE * (self.s0 + SPARK_SCALE_SPEED * self.age)


class Sliver(QWidget):
    """The docked capsule (reading) or the idle squiggle (set_rest(True)). set_state() sets the
    progress, the paused look, the edge and whether it plays; pulse() blooms; wave() ripples the
    squiggle. `slide` pushes the squiggle out past its edge and fades it (auto-hide); the view
    clips it, so it never shows past the screen edge. on_enter() hears the cursor arrive."""

    def __init__(self, parent: QWidget | None = None, edge: str = "left", seed: int | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fraction = 0.0
        self.dim = False
        self.playing = False
        self.edge = edge
        self.rest = False
        self.slide = 0.0  # points the squiggle is pushed out past the edge, 0..SLIVER_W
        self.on_enter: Callable[[], None] | None = None
        self._shown = 0.0         # the fraction drawn now (it eases toward `fraction`)
        self._shown_dim = 1.0     # the fill's opacity drawn now
        self._from = (0.0, 1.0)   # where the waterline tween started
        self._tween: float | None = None  # seconds into the waterline tween
        self._t = 0.0             # the drift clock: runs only while playing (the Mac's freeze and thaw)
        self._sparks: list[_Spark] = []
        self._births = 0.0
        self._rng = random.Random(seed)
        self._bloom: float | None = None  # seconds into a bloom
        self._ripple: float | None = None  # seconds into a ripple
        self._last: float | None = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        w, h = (SLIVER_H, SLIVER_W) if edge == "bottom" else (SLIVER_W, SLIVER_H)
        self.resize(round(w), round(h))

    # -- state --------------------------------------------------------------------------------

    def set_rest(self, on: bool) -> None:
        self.rest = bool(on)
        self._ensure_timer()
        self.update()

    def set_state(self, fraction: float, dim: bool, edge: str, playing: bool = False) -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        if fraction != self.fraction or bool(dim) != self.dim:
            if self.isVisible() and not theme.reduce_motion():
                self._from = (self._shown, self._shown_dim)
                self._tween = 0.0
            else:
                self._tween = None
        self.fraction = fraction
        self.dim = bool(dim)
        self.edge = edge
        self.playing = bool(playing)
        if self._tween is None:
            self._shown, self._shown_dim = fraction, (DIM if self.dim else 1.0)
        self._ensure_timer()
        self.update()

    def set_slide(self, points: float) -> None:
        self.slide = max(0.0, min(SLIVER_W, float(points)))
        self.update()

    def pulse(self) -> None:
        """A sentence landed: a soft bloom swells out of the waterline and dissolves."""
        if not self.playing or theme.reduce_motion():
            return
        self._bloom = 0.0
        self._ensure_timer()

    def wave(self) -> None:
        """At rest: one ripple along the squiggle, ending on the resting shape."""
        if not self.rest:
            return
        self._ripple = 0.0
        self._ensure_timer()

    def stop_wave(self) -> None:
        if self._ripple is not None:
            self._ripple = None
            self.update()
        self._ensure_timer()

    def waving(self) -> bool:
        return self._ripple is not None

    def animating(self) -> bool:
        """Is the frame timer running?"""
        return self._timer.isActive()

    def sparks(self) -> int:
        return len(self._sparks)

    # -- the frame timer ----------------------------------------------------------------------

    def _needs_timer(self) -> bool:
        if not self.isVisible() or theme.reduce_motion():
            return False
        if self.rest:
            return self._ripple is not None
        return self.playing or bool(self._sparks) or self._tween is not None or self._bloom is not None

    def _ensure_timer(self) -> None:
        if self._needs_timer():
            interval = round(1000 / (WAVE_FPS if self.rest else FPS))
            if self._timer.interval() != interval:
                self._timer.setInterval(interval)
            if not self._timer.isActive():
                self._last = None
                self._timer.start()
        elif self._timer.isActive():
            self._timer.stop()

    def showEvent(self, ev) -> None:
        self._last = None
        self._ensure_timer()
        super().showEvent(ev)

    def hideEvent(self, ev) -> None:
        self._timer.stop()  # every animation stops with the view
        super().hideEvent(ev)

    def enterEvent(self, ev) -> None:
        if self.on_enter is not None:
            self.on_enter()
        super().enterEvent(ev)

    def _tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        dt = 0.0 if self._last is None else min(0.1, max(0.0, now - self._last))  # a stalled loop must not jump
        self._last = now
        self.advance(dt)
        self.update()
        self._ensure_timer()

    def advance(self, dt: float) -> None:
        """Move every animation on by `dt` seconds (the timer calls this; so do the previews)."""
        if self.rest:
            if self._ripple is not None:
                self._ripple += dt
                if self._ripple >= WAVE_SECS:
                    self._ripple = None
            return
        if self.playing:
            self._t += dt
            self._births += dt * SPARK_RATE
            while self._births >= 1.0:
                self._births -= 1.0
                self._spawn()
        for s in self._sparks:
            s.age += dt
        self._sparks = [s for s in self._sparks if s.age < s.life and s.alpha() > 0.0]
        if self._tween is not None:
            self._tween += dt
            u = min(1.0, self._tween / WATERLINE_SECS)
            e = EASE_OUT.valueForProgress(u)
            target_dim = DIM if self.dim else 1.0
            self._shown = self._from[0] + (self.fraction - self._from[0]) * e
            self._shown_dim = self._from[1] + (target_dim - self._from[1]) * e
            if u >= 1.0:
                self._tween = None
        if self._bloom is not None:
            self._bloom += dt
            if self._bloom >= BLOOM_SECS:
                self._bloom = None

    def _spawn(self) -> None:
        r = self._rng
        half = (CAPSULE_W - 3.0) / 2
        speed = r.uniform(SPARK_SPEED - SPARK_SPEED_RANGE, SPARK_SPEED + SPARK_SPEED_RANGE)
        a = r.uniform(-SPARK_SWAY / 2, SPARK_SWAY / 2)
        self._sparks.append(_Spark(r.uniform(-half, half), CAPSULE_H / 2 - 2.0, speed * math.sin(a), -speed * math.cos(a),
                                   r.uniform(SPARK_LIFE - SPARK_LIFE_RANGE, SPARK_LIFE + SPARK_LIFE_RANGE),
                                   r.uniform(0.0, SPARK_SCALE_RANGE)))

    def preview_moment(self, t: float = 3.4, seed: int = 7, bloom: float | None = None) -> None:
        """Freeze a representative moment for a screenshot: the drift `t` seconds in, a few
        seconds of sparks aloft, optionally a bloom `bloom` seconds old."""
        self._rng = random.Random(seed)
        self._sparks, self._births, self._tween = [], 0.0, None
        self._shown, self._shown_dim = self.fraction, (DIM if self.dim else 1.0)
        playing, self.playing = self.playing, True
        start = max(0.0, t - 4.0)
        self._t = start
        steps = round((t - start) * FPS)
        for _ in range(steps):
            self.advance(1 / FPS)
        self.playing = playing
        self._t = t
        self._bloom = bloom
        self.update()

    # -- geometry -----------------------------------------------------------------------------

    def _centre(self) -> tuple[float, float]:
        """The capsule's centre in widget coordinates (capsule_centre is y-up)."""
        x, y = capsule_centre(self.edge)
        height = SLIVER_W if self.edge == "bottom" else SLIVER_H
        return x, height - y

    def _transform(self) -> QTransform:
        """From the upright capsule's own coordinates (origin at its centre) to the widget's."""
        cx, cy = self._centre()
        t = QTransform().translate(cx, cy)
        return t.rotate(90) if self.edge == "bottom" else t

    def shadow_path(self) -> QPainterPath:
        """What casts the panel's shadow: the capsule; nothing at rest (the halo is enough)."""
        if self.rest:
            return QPainterPath()
        return self._transform().map(_rounded(QRectF(-CAPSULE_W / 2, -CAPSULE_H / 2, CAPSULE_W, CAPSULE_H), CAPSULE_W / 2))

    # -- painting -----------------------------------------------------------------------------

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.rest:
            self._paint_squiggle(p)
        else:
            self._paint_capsule(p)
        p.end()

    def _tones(self) -> list[QColor]:
        """The gradient's three tones, from the content colour, drifted to the clock."""
        accent = theme.content()
        light = blend(accent, QColor(255, 255, 255), 0.28)
        deep = blend(accent, QColor(0, 0, 0), 0.22)
        e = 0.0 if theme.reduce_motion() else drift(self._t, HUE_SECS)
        return [blend_linear(a, b, e) for a, b in zip((light, accent, deep), (accent, light, accent), strict=True)]

    def _paint_capsule(self, p: QPainter) -> None:
        W, H = CAPSULE_W, CAPSULE_H
        still = theme.reduce_motion()
        p.save()
        p.setTransform(self._transform(), True)
        body = QRectF(-W / 2, -H / 2, W, H)
        p.fillPath(_rounded(body, W / 2), theme.surface(0.97))

        # The fill, painted into its own little image and cut to the waterline, so the sparks,
        # the glow and the bloom stay inside it the way the Mac's clip layer keeps them.
        dpr = max(1.0, p.device().devicePixelRatioF())
        img = QImage(max(1, math.ceil(W * dpr)), max(1, math.ceil(H * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        q.translate(W / 2, H / 2)

        e_split = 0.0 if still else drift(self._t, SPLIT_SECS)
        e_swing = 0.0 if still else drift(self._t, SWING_SECS)
        # A CAGradientLayer works out its gradient in the layer's unit square (y up) and then
        # stretches that square over its bounds, so on this tall, thin capsule a gradient from
        # (0, 0) to (1, 1) runs from corner to corner rather than at 45 degrees. Drawn the same
        # way here: in the unit square, under a transform that stretches it over the body.
        grad = QLinearGradient(QPointF(0.0 + 1.0 * e_swing, 0.0 + 0.15 * e_swing),
                               QPointF(1.0 - 1.0 * e_swing, 1.0 - 0.15 * e_swing))
        tones = self._tones()
        grad.setColorAt(0.0, tones[0])
        grad.setColorAt(0.32 + (0.72 - 0.32) * e_split, tones[1])
        grad.setColorAt(1.0, tones[2])
        q.save()
        q.translate(-W / 2, H / 2)
        q.scale(W, -H)
        q.fillRect(QRectF(0.0, 0.0, 1.0, 1.0), grad)
        q.restore()

        fill_h = max(W, H * self._shown)
        top = H / 2 - fill_h
        if self._sparks and not still:
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)  # additive, like the emitter
            for s in self._sparks:
                size, alpha = s.size(), s.alpha()
                if size <= 0.2 or alpha <= 0.0:
                    continue
                x, y = s.pos()
                r = size / 2
                dot = QRadialGradient(QPointF(x, y), r)
                dot.setColorAt(0.0, QColor.fromRgbF(1, 1, 1, alpha))
                dot.setColorAt(0.35, QColor.fromRgbF(1, 1, 1, 0.55 * alpha))
                dot.setColorAt(1.0, QColor.fromRgbF(1, 1, 1, 0.0))
                q.setPen(Qt.PenStyle.NoPen)
                q.setBrush(dot)
                q.drawEllipse(QPointF(x, y), r, r)
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        breathe = STILL_BREATHE if still else BREATHE_LOW + (1.0 - BREATHE_LOW) * drift(self._t, BREATHE_SECS)
        cap = QLinearGradient(QPointF(0, top), QPointF(0, top + CAP))
        cap.setColorAt(0.0, QColor.fromRgbF(1, 1, 1, 0.42 * breathe))
        cap.setColorAt(1.0, QColor.fromRgbF(1, 1, 1, 0.0))
        q.fillRect(QRectF(-W / 2, top, W, CAP), cap)

        if self._bloom is not None and not still:
            e = EASE_OUT.valueForProgress(min(1.0, self._bloom / BLOOM_SECS))
            r = BLOOM / 2 * (0.25 + 0.75 * e)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor.fromRgbF(1, 1, 1, 0.7 * (1.0 - e)))
            q.drawEllipse(QPointF(0, top), r, r)

        outside = QPainterPath()
        outside.addRect(body.adjusted(-1, -1, 1, 1))
        outside = outside.subtracted(_rounded(QRectF(-W / 2, top, W, fill_h), W / 2))
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        q.fillPath(outside, QColor(0, 0, 0))
        q.end()
        p.setOpacity(self._shown_dim)
        p.drawImage(body, img)
        p.setOpacity(1.0)

        p.setPen(QPen(theme.hairline(), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_rounded(body.adjusted(0.5, 0.5, -0.5, -0.5), W / 2 - 0.5))
        p.restore()

    def squiggle_path(self) -> QPainterPath:
        """The squiggle as drawn now, upright, in the capsule's own coordinates (origin at its
        centre, y down)."""
        sw, sh = squiggle_size()
        phase = 0.0 if self._ripple is None else ripple_phase(self._ripple)
        path = QPainterPath()
        for i, (x, y) in enumerate(squiggle_points(phase)):
            pt = QPointF(x - sw / 2, sh / 2 - y)  # the Mac's y-up layer into y-down
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        return path

    def _paint_squiggle(self, p: QPainter) -> None:
        d = self.slide
        opacity = max(0.0, 1.0 - d / SLIVER_W)
        if opacity <= 0.0:
            return
        if opacity >= 1.0:
            self._draw_squiggle(p, d)
            return
        # The Mac fades the squiggle's layer, halo and line as one picture; faded one by one the
        # halo would show through the line. So: drawn whole into an image, and the image faded.
        dpr = max(1.0, p.device().devicePixelRatioF())
        img = QImage(max(1, math.ceil(self.width() * dpr)), max(1, math.ceil(self.height() * dpr)),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_squiggle(q, d)
        q.end()
        p.save()
        p.setOpacity(opacity)
        p.drawImage(QRectF(0, 0, self.width(), self.height()), img)
        p.restore()

    def _draw_squiggle(self, p: QPainter, d: float) -> None:
        """The halo and the line, pushed `d` out past the edge, at full strength."""
        out = {"left": (-d, 0.0), "right": (d, 0.0), "bottom": (0.0, d)}[self.edge]
        p.save()
        p.translate(*out)
        p.setTransform(self._transform(), True)
        path = self.squiggle_path()
        for color, width in ((theme.squiggle_outline(), SQUIGGLE_STROKE + SQUIGGLE_HALO), (theme.squiggle_fill(), SQUIGGLE_STROKE)):
            pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.restore()
