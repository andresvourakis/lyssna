"""The Lyssna mark (an "l" stem followed by one wave), drawn as vectors so it stays crisp
at any size and takes any colour. Ported from scripts/make_icon.py draw_mark, which is
not shipped with the app; keep the two geometries in step. The geometry itself lives in
shared/mark.py, so the Windows interface draws the same shapes."""

from __future__ import annotations

import AppKit
import objc
from AppKit import NSBezierPath, NSView
from Foundation import NSMakeRect

from . import theme
from .shared.mark import (
    MARK_H,
    MARK_W,
    MARK_X,  # noqa: F401  (re-exported)
    MARK_Y,  # noqa: F401  (re-exported)
    mark_geometry,
)


def draw_mark(rect, color) -> None:
    """Fill the mark into `rect` (AppKit coordinates, y up) with `color`, as large as it
    fits and centred. Call inside a drawing context, e.g. drawRect_."""
    (sx, sy, sw, sh, radius), points, line_width = mark_geometry(rect)

    color.set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(sx, sy, sw, sh), radius, radius).fill()

    wave = NSBezierPath.bezierPath()
    wave.moveToPoint_(points[0])
    for p in points[1:]:
        wave.lineToPoint_(p)
    wave.setLineWidth_(line_width)
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
