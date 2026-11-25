"""The pill's rules without any drawing: its sizes and timings, which form it shows, where the
dictation forms sit, the auto-hide and resting-wave rules, and the geometry of the three docks
(from legacy/ui/mini_player.py). Shared so the Mac and Windows pills follow the same rules.

Coordinates: the geometry helpers work in the Mac's coordinates. Rectangles are (x, y, w, h),
or ((x, y), (w, h)) for frames, with y growing upward from the bottom of the screen. The Qt
interface flips y at its boundary and reuses these functions unchanged."""

from __future__ import annotations

from .wave import BAR_H, CAPSULE_INSET

MARGIN = 16.0
PAD = 18.0

SCRUB_H = 22.0    # hit area around the 4pt bar
PLAY = 50.0
SKIP = 34.0
BADGE = 32.0
PILL_W, PILL_H = 56.0, 30.0
CLOSE = 26.0      # close button hit area
CLOSE_TOP = 10.0  # inset from the card's top edge; right edge aligns with the content column (PAD)
CLOSE_GAP = 10.0  # room between the close disc and the progress bar

# Open dock (a stack hugging the screen edge): a close disc, an expand disc, then the main
# capsule with prev / play / next and a sliver of progress. Sizes below are named as on a side
# edge (W across, H along); on the bottom edge the same shapes lie flat.
DOCK_W = 46.0
DOCK_PLAY = 34.0
DOCK_SKIP = 26.0
DOCK_BAR = 24.0   # length of the little progress bar
DISC = 30.0       # the small stand-alone buttons
DOCK_GAP_V = 6.0  # between the discs and the capsule
CAPSULE_MAIN_H = 12 + BAR_H + 10 + DOCK_SKIP + 4 + DOCK_PLAY + 4 + DOCK_SKIP + 10  # 130
DOCK_H = CAPSULE_MAIN_H + (DOCK_GAP_V + DISC) * 2  # 202
DOCK_GAP = 10.0   # distance from the screen edge when the control pill is open
SNAP = 48.0       # drop within this many points of a screen edge to dock
DOCKS = ("left", "right", "bottom")

HOVER_SECS = 0.18
HOVER_OUT_DELAY = 0.08  # seconds the cursor must be away before the open pill shrinks back
HOVER_POLL = 0.03

CAP_W = 34.0      # the dictation capsule's thickness, and the dictate item's in the hover menu

# Auto-hide (ui.auto_hide): the resting sliver stays out of sight until the cursor is near it.
REVEAL_REACH = 100.0      # points around the sliver's spot that count as near
HIDE_DELAY = 0.5          # seconds the cursor must be away before the sliver slides out
LINGER_SECS = 2.0         # shown for any reason but the cursor, it stays this long at least
SLIDE_SECS = HOVER_SECS   # the slide in or out of the edge
AUTO_HIDE_POLL = 0.1      # cursor check while idle with auto-hide on

# The resting wave waves (Sliver.wave, WAVE_SECS long) about this often while it sits in view.
WAVE_EVERY = 10.0
WAVE_TOLERANCE = 1.0      # lets the system fold the timer's wakeups in with others

# Idle: at rest the slim sliver (flush with the edge); hovered, a stack of a dictate capsule
# and two discs, DOCK_GAP in from the edge. MENU_W is across, MENU_H along.
MENU_MIC_W, MENU_MIC_H = CAP_W, 52.0  # as thick as the dictation capsule
MENU_DISC = 28.0
MENU_W = MENU_MIC_W
MENU_H = MENU_MIC_H + (DOCK_GAP_V + MENU_DISC) * 2  # 120
# Dictating: a capsule CAP_W thick (set above), CAPSULE_INSET from the screen edge.
COMPACT_H = 60.0            # the dictate capsule while holding the key, and while working
FREE_H = 118.0              # hands-free: with cancel at the top (left) and finish at the bottom (right)
CANCEL_D, CANCEL_TOP = 22.0, 6.0
FINISH_D, FINISH_BOTTOM = 24.0, 5.0
BUTTON_HIT = 28.0           # the click area of cancel and finish, longer than their discs
GROW_SECS = 0.18
TIP_DELAY = 0.4             # hover this long before the tooltip pill shows
DICTATING = ("recording", "locked", "working")


def _fmt(seconds: float) -> str:
    seconds = max(0, round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def pick_form(reading: str, dictation: str, docked: bool, idle_pill: bool) -> str:
    """The form the pill shows: "dictate" while a dictation records or works (it wins over a
    reading), "card" or "sliver" during a reading, else the resting sliver "rest" (which opens
    into the idle menu on hover), or "" for nothing at all."""
    if dictation in DICTATING:
        return "dictate"
    if reading != "idle":
        return "sliver" if docked else "card"
    return "rest" if idle_pill else ""


def dictation_spot(layout: dict, own: dict) -> tuple[str, float]:
    """Which edge and position along it (a fraction, -1 for the middle) the dictation forms
    use: the docked player's, else the idle pill's own saved spot, else the bottom centre."""
    if layout.get("dock") in DOCKS:
        return layout["dock"], float(layout.get("y", -1.0))
    edge = own.get("dock")
    if edge in DOCKS:
        return edge, float(own.get("y", -1.0))
    return "bottom", -1.0


def near_frame(px: float, py: float, frame, reach: float = REVEAL_REACH) -> bool:
    """Is the point (px, py) within `reach` points of the frame ((x, y), (w, h))? The resting
    sliver's frame is flush with its edge, so this is a band along the edge around its spot."""
    (x, y), (w, h) = frame
    return x - reach <= px <= x + w + reach and y - reach <= py <= y + h + reach


def hover_inside(px: float, py: float, frame, margin: float = 6.0, edge: str = "", coord: float = 0.0) -> bool:
    """Does the point (px, py) count as on the open form with frame (x, y, w, h)? It must be on
    the frame or within `margin` of it. A form docked on `edge` (whose edge itself is at
    `coord`) also owns the strip between it and that edge: the open form sits DOCK_GAP in from
    the edge, so without the strip a cursor pressed against the edge would close it, the closed
    sliver would come back under the cursor and open it again, over and over."""
    x, y, w, h = frame
    left, right, bottom, top = x - margin, x + w + margin, y - margin, y + h + margin
    if edge == "left":
        left = min(left, coord - margin)
    elif edge == "right":
        right = max(right, coord + margin)
    elif edge == "bottom":
        bottom = min(bottom, coord - margin)
    return left <= px <= right and bottom <= py <= top


def tuck_due(near: bool, now: float, away_since: float | None, linger_until: float,
             delay: float = HIDE_DELAY) -> tuple[bool, float | None]:
    """One step of the auto-hide rule for a shown resting sliver: (slide out now, when the
    cursor left). It goes once the cursor has been away for `delay`, and never before
    `linger_until`; near, the clock resets."""
    if near:
        return False, None
    away = now if away_since is None else away_since
    return now >= max(away + delay, linger_until), away


def may_wave(form: str, visible: bool, tucked: bool, sliding: bool, dragging: bool, reading: bool,
             dictation: str, reduce_motion: bool, enabled: bool) -> bool:
    """May the resting wave wave now? Only when it is switched on (ui.wave) and on screen and
    still: the rest form, shown, not hidden or sliding under auto-hide, not in the hand,
    nothing being read or dictated, and the system's Reduce Motion off."""
    return (enabled and form == "rest" and visible and not tucked and not sliding and not dragging
            and not reading and dictation not in DICTATING and not reduce_motion)


# -- geometry: one set of rules for the three docks -------------------------------------------

def is_flat(edge: str) -> bool:
    """Forms on the bottom edge lie flat; on the side edges they stand upright."""
    return edge == "bottom"


def oriented(along: float, across: float, edge: str) -> tuple[float, float]:
    """(w, h) of a form `along` long beside the edge and `across` deep away from it."""
    return (along, across) if is_flat(edge) else (across, along)


def lay(along: float, length: float, across: float, thickness: float, total: float, flat: bool,
        flip: bool = False):
    """A rect inside a form, from its place on the form's long axis (`along`, `length`, the
    form being `total` long) and across it (`across`, `thickness`). Upright, along runs up
    from the bottom (y) and across is x. Flat, along runs along x from the left, or from the
    right with `flip` (for forms whose upright top-to-bottom order should read left to right),
    and across is y."""
    if flat:
        x = total - along - length if flip else along
        return ((x, across), (length, thickness))
    return ((across, along), (thickness, length))


def _axis(vf: tuple[float, float, float, float], edge: str) -> tuple[float, float]:
    """Start and length of the visible frame along `edge`."""
    vx, vy, vw, vh = vf
    return (vx, vw) if is_flat(edge) else (vy, vh)


def edge_coord(vf: tuple[float, float, float, float], edge: str) -> float:
    """Where the edge itself is: an x for the side edges, a y for the bottom."""
    vx, vy, vw, _ = vf
    return {"left": vx, "right": vx + vw, "bottom": vy}[edge]


def along_centre(vf, edge: str, frac: float, span: float = DOCK_H) -> float:
    """The centre along `edge` that a saved fraction stands for (the middle when below zero).
    The fraction spans the room an open DOCK_H pill has, so docked forms line up."""
    lo, n = _axis(vf, edge)
    return lo + frac * (n - span) + span / 2 if frac >= 0 else lo + n / 2


def along_fraction(vf, edge: str, centre: float, span: float = DOCK_H) -> float:
    """The inverse of along_centre: a centre along the edge as the saved fraction, 0..1."""
    lo, n = _axis(vf, edge)
    return min(1.0, max(0.0, (centre - span / 2 - lo) / max(1.0, n - span)))


def docked_frame(edge: str, coord: float, along: float, length: float, across: float, gap: float):
    """Frame ((x, y), (w, h)) of a form `length` long and `across` deep, centred at `along`,
    `gap` points in from the edge at `coord`."""
    if is_flat(edge):
        return ((along - length / 2, coord + gap), (length, across))
    x = coord + gap if edge == "left" else coord - gap - across
    return ((x, along - length / 2), (across, length))


def anchor_of(frame, edge: str, gap: float = 0.0) -> tuple[float, float]:
    """The inverse of docked_frame: (edge coord, centre along) of a docked frame."""
    (x, y), (w, h) = frame
    if is_flat(edge):
        return (y - gap, x + w / 2)
    return ((x - gap) if edge == "left" else (x + w + gap), y + h / 2)


def edge_frame(vf: tuple[float, float, float, float], edge: str, frac: float, length: float, across: float,
               inset: float = CAPSULE_INSET):
    """Frame of a form `length` long and `across` deep on `edge` of the visible frame vf
    (x, y, w, h), `inset` in from the edge (0 for the resting sliver, whose window is flush
    with the edge and draws its capsule CAPSULE_INSET in), at the saved fraction `frac`
    along the edge, kept on screen."""
    lo, n = _axis(vf, edge)
    centre = min(max(along_centre(vf, edge, frac), lo + length / 2), lo + n - length / 2)
    return docked_frame(edge, edge_coord(vf, edge), centre, length, across, inset)


def nearest_edge(vf, cx: float, cy: float) -> str:
    """The dock edge nearest a point (a side wins a tie with the bottom)."""
    vx, vy, vw, _ = vf
    d = {"left": cx - vx, "right": vx + vw - cx, "bottom": cy - vy}
    side = "left" if d["left"] <= d["right"] else "right"
    return "bottom" if d["bottom"] < d[side] else side


def snap_edge(vf, frame, snap: float = SNAP) -> str:
    """The dock edge a dragged frame is close enough to to dock on, "" for none."""
    vx, vy, vw, _ = vf
    (x, y), (w, _h) = frame
    d = {"left": x - vx, "right": vx + vw - (x + w), "bottom": y - vy}
    near = [e for e in DOCKS if d[e] < snap]
    return min(near, key=lambda e: d[e]) if near else ""


def pill_main_len(flat: bool) -> float:
    """The open pill's main capsule length: flat, the progress bar lies along it."""
    return CAPSULE_MAIN_H - BAR_H + DOCK_BAR if flat else CAPSULE_MAIN_H


def pill_len(flat: bool) -> float:
    return pill_main_len(flat) + (DOCK_GAP_V + DISC) * 2
