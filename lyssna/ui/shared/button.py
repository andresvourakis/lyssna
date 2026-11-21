"""Where the little play button goes beside a selection (from ui/selection_button.py). Shared
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
