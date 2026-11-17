"""The Lyssna mark (an "l" stem followed by one wave), drawn as vectors so it stays crisp
at any size and takes any colour. Ported from scripts/make_icon.py draw_mark, which is
not shipped with the app; keep the two geometries in step."""

from __future__ import annotations

import math

import AppKit
import objc
from AppKit import NSBezierPath, NSView
from Foundation import NSMakeRect

from . import theme

# The mark's bounding box on the 512-unit design canvas (y down), stroke included.
MARK_X, MARK_Y, MARK_W, MARK_H = 110, 136, 292, 240


def draw_mark(rect, color) -> None:
    """Fill the mark into `rect` (AppKit coordinates, y up) with `color`, as large as it
    fits and centred. Call inside a drawing context, e.g. drawRect_."""
    (rx, ry), (rw, rh) = rect
    s = min(rw / MARK_W, rh / MARK_H)
    left = rx + (rw - MARK_W * s) / 2
    top = ry + (rh + MARK_H * s) / 2

    def pt(x: float, y: float) -> tuple[float, float]:
        return (left + (x - MARK_X) * s, top - (y - MARK_Y) * s)

    color.set()
    sx, sy = pt(110, 376)
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(sx, sy, 48 * s, 240 * s), 24 * s, 24 * s).fill()

    wave = NSBezierPath.bezierPath()
    wave.moveToPoint_(pt(210, 256))
    steps = 160
    for i in range(1, steps + 1):
        t = i / steps
        wave.lineToPoint_(pt(210 + 170 * t, 256 - 52 * math.sin(2 * math.pi * t)))
    wave.setLineWidth_(44 * s)
    wave.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    wave.setLineJoinStyle_(AppKit.NSLineJoinStyleRound)
    wave.stroke()


class MarkView(NSView):
    """The mark in CONTENT. Drawn in drawRect_, so the dynamic colour re-resolves when the
    appearance flips."""

    @classmethod
    @objc.python_method
    def make(cls, height: float) -> MarkView:
        return cls.alloc().initWithFrame_(NSMakeRect(0, 0, round(height * MARK_W / MARK_H), height))

    def drawRect_(self, dirty):
        draw_mark(self.bounds(), theme.CONTENT)

    def isAccessibilityElement(self):
        return False  # decoration: the headline next to it says the name
