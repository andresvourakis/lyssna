"""Reusable pieces of the floating player: hover-glow views, the progress and scrub bars,
the docked sliver with its living fill, and the Core Animation helpers they share.

Colors are resolved in updateLayer from the brand colours in theme.py, so everything
here follows light/dark mode on its own."""

from __future__ import annotations

import math
from collections.abc import Callable

import AppKit
import objc
import Quartz
from AppKit import NSColor, NSEvent

from . import theme
from .panel import TintedView

BAR_H = 4.0
KNOB = 14.0       # playhead knob shown while hovering / scrubbing the bar

# Resting capsule: a slim progress sliver flush with the screen edge. The window is a bit
# wider than the drawn capsule so a cursor pushed against the edge still hovers it.
SLIVER_W, SLIVER_H = 26.0, 64.0
CAPSULE_W, CAPSULE_H = 8.0, 56.0
CAPSULE_INSET = 10.0  # from the screen edge


def capsule_centre(edge: str) -> tuple[float, float]:
    """Centre of the drawn capsule inside a sliver view on `edge`, CAPSULE_INSET from the
    side that touches the screen edge (the view is SLIVER_H x SLIVER_W lying flat on the bottom)."""
    near = CAPSULE_INSET + CAPSULE_W / 2
    if edge == "bottom":
        return (SLIVER_H / 2, near)
    return (near if edge == "left" else SLIVER_W - near, SLIVER_H / 2)


def clear_glow():
    return theme.content(0.10)


def white_glow():
    return NSColor.colorWithWhite_alpha_(1.0, 0.16)


class HoverView(TintedView):
    """TintedView that lifts a translucent glow over itself while the cursor is inside, so the
    whole shape brightens softly on hover. Works as a transparent wrapper around a bare button too."""

    @classmethod
    @objc.python_method
    def make(cls, frame, color, radius: float = 0.0, border=None, glow=None):
        v = TintedView.make.__func__(cls, frame, color, radius, border)
        v.glow_fn = glow or clear_glow
        v.glow = AppKit.CALayer.layer()
        v.glow.setOpacity_(0.0)
        v.layer().addSublayer_(v.glow)
        opts = (AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveAlways | AppKit.NSTrackingInVisibleRect)
        v.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(v.bounds(), opts, v, None))
        return v

    def updateLayer(self):
        objc.super(HoverView, self).updateLayer()
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        self.glow.setFrame_(self.bounds())
        self.glow.setCornerRadius_(self.radius)
        self.glow.setBackgroundColor_(self.glow_fn().CGColor())
        Quartz.CATransaction.commit()

    def mouseEntered_(self, event):
        self.set_hover(True)

    def mouseExited_(self, event):
        self.set_hover(False)

    @objc.python_method
    def sync(self) -> None:
        """Set the glow from where the cursor actually is (enter/exit events can be lost
        when tracking areas are rebuilt around us)."""
        w = self.window()
        if w is None:
            return
        p = self.convertPoint_fromView_(w.convertPointFromScreen_(NSEvent.mouseLocation()), None)
        self.set_hover(AppKit.NSMouseInRect(p, self.bounds(), self.isFlipped()))

    @objc.python_method
    def set_hover(self, on: bool) -> None:
        if getattr(self, "_on", False) == on:
            return
        self._on = on
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setAnimationDuration_(0.14 if on else 0.22)
        self.glow.setOpacity_(1.0 if on else 0.0)
        Quartz.CATransaction.commit()
        on_hover = getattr(self, "on_hover", None)  # optional: the owner's tooltip timer
        if on_hover is not None:
            on_hover(on)


def clear():
    return NSColor.clearColor()


def _spark_image(size: int = 12):
    """A soft white dot for the emitter: bright centre, transparent rim."""
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(None, size, size, 8, size * 4, cs, Quartz.kCGImageAlphaPremultipliedLast)
    grad = Quartz.CGGradientCreateWithColorComponents(cs, [1, 1, 1, 1, 1, 1, 1, 0.55, 1, 1, 1, 0], [0.0, 0.35, 1.0], 3)
    c = (size / 2, size / 2)
    Quartz.CGContextDrawRadialGradient(ctx, grad, c, 0.0, c, size / 2, 0)
    return Quartz.CGBitmapContextCreateImage(ctx)


def _freeze(layer) -> None:
    """Pause every animation running inside `layer` (Apple's speed/timeOffset recipe)."""
    if layer.speed() == 0.0:
        return
    layer.setTimeOffset_(layer.convertTime_fromLayer_(Quartz.CACurrentMediaTime(), None))
    layer.setSpeed_(0.0)


def _thaw(layer) -> None:
    if layer.speed() != 0.0:
        return
    paused_at = layer.timeOffset()
    layer.setSpeed_(1.0)
    layer.setTimeOffset_(0.0)
    layer.setBeginTime_(0.0)
    layer.setBeginTime_(layer.convertTime_fromLayer_(Quartz.CACurrentMediaTime(), None) - paused_at)


def _loop(key: str, start, end, secs: float):
    """A slow ease-in-out ping-pong that never stops; several with unrelated periods
    layered together stop looking like a loop at all."""
    a = Quartz.CABasicAnimation.animationWithKeyPath_(key)
    a.setFromValue_(start)
    a.setToValue_(end)
    a.setDuration_(secs)
    a.setAutoreverses_(True)
    a.setRepeatCount_(1e9)
    a.setTimingFunction_(Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseInEaseOut))
    return a


class ProgressBar(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(ProgressBar, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(BAR_H / 2)
        self.layer().setMasksToBounds_(True)
        self.fill = AppKit.CALayer.layer()
        self.fill.setCornerRadius_(BAR_H / 2)
        self.layer().addSublayer_(self.fill)
        self.fraction = 0.0
        return self

    def updateLayer(self):
        self.layer().setBackgroundColor_(theme.content(0.15).CGColor())
        self.fill.setBackgroundColor_(theme.CONTENT.CGColor())
        w, h = self.bounds().size
        self.fill.setFrame_(((0, 0), (max(h, w * self.fraction), h)))

    def wantsUpdateLayer(self):
        return True

    @objc.python_method
    def set_fraction(self, f: float) -> None:
        self.fraction = max(0.0, min(1.0, f))
        self.setNeedsDisplay_(True)


class ScrubBar(AppKit.NSView):
    """Transparent strip holding a ProgressBar; click or drag anywhere on it to seek.
    Hovering (or scrubbing) shows a round knob at the playhead and thickens the bar, the
    way every media player does, so it's obvious the bar can be grabbed.
    `on_scrub(fraction)` fires while dragging (for the time preview), `on_seek(fraction)`
    on release."""

    consumes_mouse = True  # tell DragPanel this is a control, not a drag handle

    def initWithFrame_(self, frame):
        self = objc.super(ScrubBar, self).initWithFrame_(frame)
        if self is None:
            return None
        w, h = frame[1]
        self.setWantsLayer_(True)
        self.knob_size = KNOB
        self.bar = ProgressBar.alloc().initWithFrame_(((0, (h - BAR_H) / 2), (w, BAR_H)))
        self.bar.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.addSubview_(self.bar)
        self.knob = AppKit.CALayer.layer()
        self.knob.setHidden_(True)
        self.layer().addSublayer_(self.knob)
        self.on_scrub: Callable[[float], None] | None = None
        self.on_seek: Callable[[float], None] | None = None
        self.scrubbing = False
        self.hovering = False
        opts = (AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveAlways | AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(self.bounds(), opts, self, None))
        return self

    def wantsUpdateLayer(self):
        return True

    def updateLayer(self):
        active = self.hovering or self.scrubbing
        w, h = self.bounds().size
        bar_h = BAR_H * 1.5 if active else BAR_H
        self.bar.setFrame_(((0, (h - bar_h) / 2), (w, bar_h)))
        self.bar.layer().setCornerRadius_(bar_h / 2)
        self.bar.fill.setCornerRadius_(bar_h / 2)
        self.bar.setNeedsDisplay_(True)
        k = self.knob_size
        self.knob.setHidden_(not active)
        self.knob.setCornerRadius_(k / 2)
        self.knob.setBackgroundColor_(theme.CONTENT.CGColor())
        self.knob.setBorderWidth_(2.0)
        self.knob.setBorderColor_(theme.SURFACE.CGColor())
        self.knob.setShadowOpacity_(0.25)
        self.knob.setShadowRadius_(2.0)
        self.knob.setShadowOffset_((0, -1))
        x = min(max(w * self.bar.fraction, k / 2), w - k / 2) if w > 0 else 0
        self.knob.setFrame_(((x - k / 2, (h - k) / 2), (k, k)))

    def mouseEntered_(self, event):
        self.hovering = True
        self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        self.hovering = False
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _fraction(self, event) -> float:
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        w = self.bounds().size.width
        return max(0.0, min(1.0, p.x / w)) if w > 0 else 0.0

    def mouseDown_(self, event):
        self.scrubbing = True
        self._move(event)

    def mouseDragged_(self, event):
        self._move(event)

    def mouseUp_(self, event):
        f = self._fraction(event)
        self.scrubbing = False
        self.bar.set_fraction(f)
        self.setNeedsDisplay_(True)
        if self.on_seek:
            self.on_seek(f)

    @objc.python_method
    def _move(self, event) -> None:
        f = self._fraction(event)
        self.bar.set_fraction(f)
        self.setNeedsDisplay_(True)
        if self.on_scrub:
            self.on_scrub(f)

    @objc.python_method
    def set_fraction(self, f: float) -> None:
        if not self.scrubbing:
            self.bar.set_fraction(f)
            if self.hovering:
                self.setNeedsDisplay_(True)


class Sliver(AppKit.NSView):
    """The docked, resting form: a vertical capsule whose fill shows progress from the
    bottom up. The fill is alive while reading: a slowly drifting gradient body, tiny
    sparks rising through it, a breathing glow at the waterline and a small bloom each
    time a sentence lands. Paused, the sparks stop being born, the drift freezes and
    the whole fill dims. Transparent around the capsule.

At rest (set_rest(True), the idle pill when nothing plays) it is the same capsule with no fill
at all: the content colour at a low, calm alpha, and nothing animating.

On the bottom edge (edge "bottom") the view is SLIVER_H wide and SLIVER_W tall and the whole
capsule layer is turned a quarter clockwise, so the fill grows from the left and the sparks
drift right: the same layers, lying flat."""

    REST_ALPHA = 0.22  # the resting capsule's tint

    CAP = 7.0      # height of the waterline glow
    BLOOM = 22.0   # diameter of the sentence bloom

    def initWithFrame_(self, frame):
        self = objc.super(Sliver, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setWantsLayer_(True)
        self.capsule = AppKit.CALayer.layer()
        self.capsule.setCornerRadius_(CAPSULE_W / 2)
        self.capsule.setMasksToBounds_(True)
        self.capsule.setBorderWidth_(1.0)
        self.layer().addSublayer_(self.capsule)

        # clip: the fill's outline, everything below is clipped to it
        self.clip = AppKit.CALayer.layer()
        self.clip.setCornerRadius_(CAPSULE_W / 2)
        self.clip.setMasksToBounds_(True)
        self.capsule.addSublayer_(self.clip)

        # body: a full-height gradient the clip reveals from the bottom; its own drift
        # animations live here so they can be frozen as a unit while paused
        self.body = Quartz.CAGradientLayer.layer()
        self.body.setFrame_(((0, 0), (CAPSULE_W, CAPSULE_H)))
        self.body.setStartPoint_((0.0, 0.0))
        self.body.setEndPoint_((1.0, 1.0))
        self.body.setLocations_([0.0, 0.5, 1.0])
        self.clip.addSublayer_(self.body)

        # sparks: born along the bottom, drifting up, blooming in and fading out
        cell = Quartz.CAEmitterCell.emitterCell()
        cell.setContents_(_spark_image())
        cell.setBirthRate_(2.2)
        cell.setLifetime_(2.4)
        cell.setLifetimeRange_(0.8)
        cell.setVelocity_(11.0)
        cell.setVelocityRange_(6.0)
        cell.setEmissionLongitude_(1.5708)  # straight up
        cell.setEmissionRange_(0.35)        # with a little sway
        cell.setScale_(0.0)                 # grow from nothing…
        cell.setScaleSpeed_(0.34)
        cell.setScaleRange_(0.08)
        cell.setAlphaSpeed_(-0.42)          # …while fading, so each spark blooms then dissolves
        cell.setColor_(NSColor.colorWithWhite_alpha_(1.0, 0.95).CGColor())
        cell.setSpin_(0.0)
        self.sparks = Quartz.CAEmitterLayer.layer()
        self.sparks.setFrame_(((0, 0), (CAPSULE_W, CAPSULE_H)))
        self.sparks.setEmitterShape_(Quartz.kCAEmitterLayerLine)
        self.sparks.setEmitterMode_(Quartz.kCAEmitterLayerOutline)
        self.sparks.setEmitterPosition_((CAPSULE_W / 2, 2.0))
        self.sparks.setEmitterSize_((CAPSULE_W - 3.0, 0.0))
        self.sparks.setRenderMode_(Quartz.kCAEmitterLayerAdditive)
        self.sparks.setEmitterCells_([cell])
        self.sparks.setBirthRate_(0.0)
        self.clip.addSublayer_(self.sparks)

        # top: rides the waterline; holds the breathing cap glow and the bloom
        self.top = AppKit.CALayer.layer()
        self.clip.addSublayer_(self.top)
        self.cap = Quartz.CAGradientLayer.layer()
        self.cap.setFrame_(((0, 0), (CAPSULE_W, self.CAP)))
        self.cap.setStartPoint_((0.5, 0.0))
        self.cap.setEndPoint_((0.5, 1.0))
        self.cap.setColors_([NSColor.colorWithWhite_alpha_(1.0, 0.0).CGColor(), NSColor.colorWithWhite_alpha_(1.0, 0.42).CGColor()])
        self.cap.addAnimation_forKey_(_loop("opacity", 0.45, 1.0, 2.6), "breathe")
        self.top.addSublayer_(self.cap)
        self.bloom = AppKit.CALayer.layer()
        self.bloom.setBounds_(((0, 0), (self.BLOOM, self.BLOOM)))
        self.bloom.setCornerRadius_(self.BLOOM / 2)
        self.bloom.setBackgroundColor_(NSColor.colorWithWhite_alpha_(1.0, 1.0).CGColor())
        self.bloom.setOpacity_(0.0)
        self.top.addSublayer_(self.bloom)

        self.fraction = 0.0
        self.dim = False
        self.playing = False
        self.edge = "left"
        self.rest = False
        self._accent = None
        return self

    @objc.python_method
    def set_rest(self, on: bool) -> None:
        self.rest = bool(on)
        self.setNeedsDisplay_(True)

    def wantsUpdateLayer(self):
        return True

    @objc.python_method
    def _fill_h(self) -> float:
        return max(CAPSULE_W, CAPSULE_H * self.fraction)

    @objc.python_method
    def _tint(self) -> None:
        """Re-derive the gradient's tones from the content colour and restart its drift.
        Only runs when that colour actually changes (an appearance flip)."""
        accent = theme.CONTENT.colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
        key = tuple(round(c, 3) for c in accent.getRed_green_blue_alpha_(None, None, None, None))
        if key == self._accent:
            return
        self._accent = key
        white, black = NSColor.whiteColor(), NSColor.blackColor()
        light = accent.blendedColorWithFraction_ofColor_(0.28, white).CGColor()
        base = accent.CGColor()
        deep = accent.blendedColorWithFraction_ofColor_(0.22, black).CGColor()
        self.body.removeAllAnimations()
        self.body.setColors_([light, base, deep])
        # three drifts with unrelated periods: the light wanders, the split shifts, the ends swap
        self.body.addAnimation_forKey_(_loop("colors", [light, base, deep], [base, light, base], 6.7), "hue")
        self.body.addAnimation_forKey_(_loop("locations", [0.0, 0.32, 1.0], [0.0, 0.72, 1.0], 5.3), "split")
        self.body.addAnimation_forKey_(_loop("startPoint", (0.0, 0.0), (1.0, 0.15), 8.1), "swing")
        self.body.addAnimation_forKey_(_loop("endPoint", (1.0, 1.0), (0.0, 0.85), 8.1), "swing2")

    def updateLayer(self):
        h = self._fill_h()
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        # bounds + position + transform (never frame, which is undefined under a transform)
        self.capsule.setBounds_(((0, 0), (CAPSULE_W, CAPSULE_H)))
        self.capsule.setPosition_(capsule_centre(self.edge))
        self.capsule.setTransform_(Quartz.CATransform3DMakeRotation(-math.pi / 2, 0, 0, 1) if self.edge == "bottom"
                                   else Quartz.CATransform3DIdentity)
        self.capsule.setBorderColor_(theme.HAIRLINE.CGColor())
        self.clip.setHidden_(self.rest)
        if self.rest:
            self.capsule.setBackgroundColor_(theme.content(self.REST_ALPHA).CGColor())
            Quartz.CATransaction.commit()
            _freeze(self.body)
            _freeze(self.cap)
            self.sparks.setBirthRate_(0.0)
            return
        self.capsule.setBackgroundColor_(theme.surface(0.97).CGColor())
        self._tint()
        Quartz.CATransaction.commit()
        # the waterline itself eases up (or jumps back on a seek)
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setAnimationDuration_(0.35)
        Quartz.CATransaction.setAnimationTimingFunction_(Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseOut))
        self.clip.setFrame_(((0, 0), (CAPSULE_W, h)))
        self.top.setFrame_(((0, h - self.CAP), (CAPSULE_W, self.CAP)))
        self.bloom.setPosition_((CAPSULE_W / 2, self.CAP))
        self.clip.setOpacity_(0.45 if self.dim else 1.0)
        Quartz.CATransaction.commit()
        if self.playing:
            _thaw(self.body)
            _thaw(self.cap)
            self.sparks.setBirthRate_(1.0)
        else:
            _freeze(self.body)
            _freeze(self.cap)
            self.sparks.setBirthRate_(0.0)  # sparks already aloft finish their drift

    @objc.python_method
    def set_state(self, fraction: float, dim: bool, edge: str, playing: bool = False) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        self.dim = dim
        self.edge = edge
        self.playing = playing
        self.setNeedsDisplay_(True)

    @objc.python_method
    def pulse(self) -> None:
        """A sentence landed: a soft bloom swells out of the waterline and dissolves."""
        if not self.playing:
            return
        grow = Quartz.CABasicAnimation.animationWithKeyPath_("transform.scale")
        grow.setFromValue_(0.25)
        grow.setToValue_(1.0)
        fade = Quartz.CABasicAnimation.animationWithKeyPath_("opacity")
        fade.setFromValue_(0.7)
        fade.setToValue_(0.0)
        group = Quartz.CAAnimationGroup.animation()
        group.setAnimations_([grow, fade])
        group.setDuration_(0.75)
        group.setTimingFunction_(Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseOut))
        self.bloom.addAnimation_forKey_(group, "bloom")
