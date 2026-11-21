"""The Lyssna mark's geometry (an "l" stem followed by one wave), as numbers on the 512-unit
design canvas and a function that scales it into a target rect. Shared so the Mac (legacy/ui/mark.py)
and Windows interfaces fill and stroke exactly the same shapes. Ported from
scripts/make_icon.py draw_mark; keep the geometries in step.

mark_geometry works in the Mac's coordinates, y growing upward: the stem's (x, y) is its
bottom-left corner. An interface with y growing downward flips at its boundary."""

from __future__ import annotations

import math

# The mark's bounding box on the 512-unit design canvas (y down), stroke included.
MARK_X, MARK_Y, MARK_W, MARK_H = 110, 136, 292, 240

# The stem: a rounded bar whose bottom-left corner sits at (STEM_X, STEM_BOTTOM) on the canvas.
STEM_X, STEM_BOTTOM, STEM_W, STEM_H, STEM_RADIUS = 110, 376, 48, 240, 24
# The wave: one sine period WAVE_LEN long from (WAVE_X, WAVE_Y), WAVE_AMP high, stroked WAVE_STROKE wide with round caps and joins.
WAVE_X, WAVE_Y, WAVE_LEN, WAVE_AMP, WAVE_STROKE = 210, 256, 170, 52, 44
WAVE_STEPS = 160


def mark_geometry(rect, steps: int = WAVE_STEPS):
    """The mark scaled into `rect` ((x, y), (w, h)), as large as it fits and centred:
    (stem, wave, line_width), where stem is (x, y, w, h, corner_radius) of the rounded bar,
    wave the polyline [(x, y), ...] to stroke, and line_width the wave's stroke width."""
    (rx, ry), (rw, rh) = rect
    s = min(rw / MARK_W, rh / MARK_H)
    left = rx + (rw - MARK_W * s) / 2
    top = ry + (rh + MARK_H * s) / 2

    def pt(x: float, y: float) -> tuple[float, float]:
        return (left + (x - MARK_X) * s, top - (y - MARK_Y) * s)

    sx, sy = pt(STEM_X, STEM_BOTTOM)
    stem = (sx, sy, STEM_W * s, STEM_H * s, STEM_RADIUS * s)

    wave = [pt(WAVE_X, WAVE_Y)]
    for i in range(1, steps + 1):
        t = i / steps
        wave.append(pt(WAVE_X + WAVE_LEN * t, WAVE_Y - WAVE_AMP * math.sin(2 * math.pi * t)))
    return stem, wave, WAVE_STROKE * s
