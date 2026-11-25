"""Where a titled window (Setup, History, What's New) opens, without any drawing: each one's rule
from the legacy Mac windows, which place the whole window frame, title bar included.

Coordinates: Qt's, y growing down from the top of the primary screen. `vf` is the visible frame
of the screen under the mouse (x, y, w, h) and the size is the whole frame's."""

from __future__ import annotations

# How the room left above and below a window is shared out: (the share above it, and a further
# lift, a share of the screen's height). The Mac writes these y up from the bottom.
CENTRED = (0.5, 0.0)        # History: the middle
LIFTED = (0.5, 0.08)        # Setup: the middle, raised by 8 % of the screen ("a touch above centre")
UPPER = (0.4, 0.0)          # What's New: 60 % of the room below it, 40 % above


def open_at(vf: tuple[float, float, float, float], size: tuple[float, float], rule: tuple[float, float] = LIFTED
            ) -> tuple[int, int]:
    """The top-left corner of a window frame `size` (w, h) on the visible frame `vf` by `rule`,
    rounded to whole points as the Mac rounds the origin, and never above the visible frame."""
    x, y, w, h = vf
    fw, fh = size
    share, lift = rule
    top = y + share * (h - fh) - lift * h
    return round(x + (w - fw) / 2), round(max(y, top))
