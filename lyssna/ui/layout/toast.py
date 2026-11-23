"""Layout of the notice panels beside the pill (from legacy/ui/toast.py): the toast's and the tip's
sizes and timings, the toast's width, where a toast and a tip go. Shared so the Mac and Windows
notices are laid out the same way. Coordinates are the Mac's, y growing upward."""

from __future__ import annotations

TOAST_H = 44.0
PAD_L = 18.0           # text from the left edge (and from the right when there is no action)
ACTION_H = 28.0
ACTION_PAD = 12.0      # inside the action pill, either side of its label
ACTION_RIGHT = 8.0     # the action pill from the right edge
ACTION_GAP = 12.0      # between the text and the action pill
BOTTOM = 28.0          # above the bottom of the screen's visible frame
TOAST_GAP = 12.0       # between the pill and the toast
MAX_W = 640.0
FADE_IN, FADE_OUT = 0.15, 0.25

TIP_H = 30.0
TIP_PAD = 12.0
TIP_GAP = 8.0          # between the button and the tip


def toast_width(text_w: float, action_w: float) -> float:
    """Width of a toast whose text is text_w wide; action_w is the action label's width, 0 for none."""
    if action_w <= 0:
        return PAD_L + text_w + PAD_L
    return PAD_L + text_w + ACTION_GAP + (action_w + 2 * ACTION_PAD) + ACTION_RIGHT


def toast_origin(vf: tuple[float, float, float, float], width: float,
                 anchor: tuple[tuple[float, float, float, float], str] | None = None) -> tuple[float, float]:
    """Where a toast `width` wide goes in the visible frame vf (x, y, w, h). `anchor` is the
    pill's frame (x, y, w, h) and the edge it is on, or None with no pill on screen. With no
    pill, and with the pill on the bottom edge, at the bottom centre of the screen, kept
    TOAST_GAP above a bottom pill. Beside a pill docked on a side: TOAST_GAP from it on the
    inner side, centred on it, kept inside vf."""
    vx, vy, vw, vh = vf
    x = round(vx + (vw - width) / 2)
    y = vy + BOTTOM
    if anchor is None:
        return x, y
    (px, py, pw, ph), edge = anchor
    if edge == "bottom":
        return x, max(y, py + ph + TOAST_GAP)
    x = px + pw + TOAST_GAP if edge == "left" else px - TOAST_GAP - width
    y = py + (ph - TOAST_H) / 2
    x = min(max(x, vx), vx + vw - width)
    y = min(max(y, vy), vy + vh - TOAST_H)
    return round(x), round(y)


def tip_origin(anchor: tuple[float, float, float, float], edge: str, width: float) -> tuple[float, float]:
    """Where a tip `width` wide goes beside `anchor` (x, y, w, h in screen points): on the
    inner side of the screen edge the pill is on, centred on the anchor; above it on the
    bottom edge."""
    x, y, w, h = anchor
    if edge == "bottom":
        return x + (w - width) / 2, y + h + TIP_GAP
    left = x - TIP_GAP - width if edge == "right" else x + w + TIP_GAP
    return left, y + (h - TIP_H) / 2
