"""The wave maths of the pill: the dictation meter's sizes and motion (from ui/wave.py) and the
resting sliver, capsule and idle squiggle (from ui/widgets.py). Plain numbers and functions of
plain numbers, shared so the Mac and Windows interfaces draw the same points.

Points are in the view's own coordinates as the Mac draws them, y growing upward."""

from __future__ import annotations

import math

# -- the dictation meter (ui/wave.py) -------------------------------------------------------------

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
                cycles: float = CYCLES, steps: int = STEPS, horizontal: bool = False) -> list[tuple[float, float]]:
    """Points of the wave along a width x height area: up its height, or with `horizontal`
    left to right across its width, swinging about the middle of the other side. The ends
    sit half a stroke in so the round caps stay inside."""
    length, depth = (width, height) if horizontal else (height, width)
    start, span = STROKE / 2, length - STROKE
    mid = depth / 2
    points = []
    for i in range(steps + 1):
        along = start + span * i / steps
        swing = mid + amplitude * math.sin(2 * math.pi * (cycles * i / steps + phase))
        points.append((along, swing) if horizontal else (swing, along))
    return points


# -- the resting sliver and the idle squiggle (ui/widgets.py) -------------------------------------

BAR_H = 4.0
KNOB = 14.0       # playhead knob shown while hovering / scrubbing the bar

# Resting capsule: a slim progress sliver flush with the screen edge. The window is a bit
# wider than the drawn capsule so a cursor pushed against the edge still hovers it.
SLIVER_W, SLIVER_H = 26.0, 64.0
CAPSULE_W, CAPSULE_H = 8.0, 56.0
CAPSULE_INSET = 10.0  # from the screen edge
# Idle mark: one wave of the logo, slimmed, centred where the capsule is (about 10 x 38 drawn).
SQUIGGLE_LEN = 34.0     # one full wave, along the edge
SQUIGGLE_AMP = 3.2      # peak height either side of the centre line
SQUIGGLE_STROKE = 3.6
SQUIGGLE_HALO = 2.0     # the halo under it is this much wider than the line
# Its wave now and then: one slow ripple runs along it and it settles back at rest. A detail, not a nudge.
WAVE_SECS = 2.8
WAVE_CYCLES = 1.0
WAVE_FRAMES = 72        # key paths per wave; the render server blends between them


def capsule_centre(edge: str) -> tuple[float, float]:
    """Centre of the drawn capsule inside a sliver view on `edge`, CAPSULE_INSET from the
    side that touches the screen edge (the view is SLIVER_H x SLIVER_W lying flat on the bottom)."""
    near = CAPSULE_INSET + CAPSULE_W / 2
    if edge == "bottom":
        return (SLIVER_H / 2, near)
    return (near if edge == "left" else SLIVER_W - near, SLIVER_H / 2)


def squiggle_size() -> tuple[float, float]:
    """The idle wave's layer, across x along, with room for the halo's round caps."""
    pad = SQUIGGLE_STROKE + SQUIGGLE_HALO
    return (2 * SQUIGGLE_AMP + pad, SQUIGGLE_LEN + pad)


def squiggle_points(phase: float = 0.0) -> list[tuple[float, float]]:
    """The points of the logo's wave (mark.py) standing up in the squiggle layer: one sine
    period from bottom to top, first out to the left, so turned a quarter clockwise it rises
    first, then dips. `phase` (in cycles) moves the crests up the line, so turned flat they run
    left to right; whole cycles give the resting shape back. The amplitude never changes: the
    layer is exactly as wide as the wave plus its round caps, so a taller wave would poke out."""
    w, h = squiggle_size()
    x0, y0 = w / 2, (h - SQUIGGLE_LEN) / 2
    steps = 64
    return [(x0 - SQUIGGLE_AMP * math.sin(2 * math.pi * (i / steps - phase)), y0 + SQUIGGLE_LEN * (i / steps))
            for i in range(steps + 1)]


def wave_phases(frames: int = WAVE_FRAMES, cycles: float = WAVE_CYCLES) -> list[float]:
    """The phase of each key path of one wave, evenly spaced in time: eased in and out (a half
    cosine), so the ripple gathers speed and settles. The ends are exactly 0, the resting
    shape, so the wave starts and stops without a jump."""
    return [(cycles * (1 - math.cos(math.pi * i / frames)) / 2) % 1.0 for i in range(frames + 1)]
