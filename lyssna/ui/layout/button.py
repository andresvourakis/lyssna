"""Where the little play button goes beside a selection (from legacy/ui/selection_button.py). Shared
so the Mac and Windows buttons land in the same spot.

The selection and the cursor come in Quartz coordinates (y growing downward from the top of
the primary screen); the result, and `avoid`, are in the Mac's Cocoa coordinates (y growing
upward), converted with the primary screen's height."""

from __future__ import annotations

OFFSET = 10.0       # gap between the button and the selection / cursor line
LINE_HEIGHT = 22.0  # fallback: assume the cursor sits on a line about this tall


def _intersects(rect, ns_rect) -> bool:
    x, y, w, h = rect
    ox, oy = ns_rect.origin.x, ns_rect.origin.y
    ow, oh = ns_rect.size.width, ns_rect.size.height
    return x < ox + ow and x + w > ox and y < oy + oh and y + h > oy


def place(qx: float, qy: float, size: tuple[float, float], screen_h: float, avoid=None, selection=None,
          offset: float = OFFSET) -> tuple[float, float]:
    """The button's bottom-left corner (x, y) for a button `size` (w, h): above the selection
    when `selection` (x, y, w, h, Quartz) is known, centred on the cursor (qx, qy, Quartz) and
    kept within it; otherwise just above the line the cursor ended on. When it would cover
    `avoid` (a rect with .origin and .size, Cocoa) it flips below. `screen_h` is the primary
    screen's height."""
    width, height = size
    H = screen_h
    if selection is not None:
        sx, sy, sw, sh = selection
        top = sy                                   # top edge of the selected text (Quartz)
        x = min(max(qx, sx), sx + sw) - width / 2  # centred on the cursor, kept within the selection
    else:
        top = qy - LINE_HEIGHT / 2
        x = qx - width / 2
    y = H - top + offset                            # bottom of the button sits `offset` above the selection
    if avoid is not None and _intersects((x, y, width, height), avoid):
        y = H - (top + (sh if selection else LINE_HEIGHT)) - offset - height  # flip below
    return x, y


def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def clear_spot(default: tuple[float, float], size: tuple[float, float], qx: float, qy: float, obstacles,
               selection=None, offset: float = OFFSET) -> tuple[float, float]:
    """Where the button goes when another app's pop-up (Edge's selection toolbar) covers it.

    Everything is in one y-down space (Quartz on the Mac, Qt logical on Windows): `default` is
    the button's top-left corner where it already sits, `size` its (w, h), (qx, qy) the cursor,
    `selection` the selected text's (x, y, w, h) when known, and `obstacles` a list of
    (x, y, w, h) rects to stay clear of. The answer is the top-left corner of the first spot
    that touches no obstacle, tried in this order: the default; below the selection; above the
    selection just left of the obstacle the default hits; above it just right of that obstacle.
    When none is clear, the default."""
    width, height = size
    obstacles = list(obstacles)
    x, y = default

    def clear(px: float, py: float) -> bool:
        return not any(_overlaps((px, py, width, height), o) for o in obstacles)

    if clear(x, y):
        return default
    if selection is not None:
        _, top, _, sh = selection
    else:
        top, sh = qy - LINE_HEIGHT / 2, LINE_HEIGHT
    above = top - offset - height
    spots = [(x, top + sh + offset)]
    hit = next(o for o in obstacles if _overlaps((x, y, width, height), o))
    ox, _, ow, _ = hit
    spots += [(ox - offset - width, above), (ox + ow + offset, above)]
    return next(((px, py) for px, py in spots if clear(px, py)), default)
