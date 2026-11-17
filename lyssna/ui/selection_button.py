"""The little play button that pops up next to the cursor after you select text."""

from __future__ import annotations

from collections.abc import Callable

import objc

from . import theme
from .mainthread import later, on_main
from .panel import FloatingPanel, TintedView, screen_at_quartz, screen_height, symbol_button

OFFSET = 10.0       # gap between the button and the selection / cursor line
LINE_HEIGHT = 22.0  # fallback: assume the cursor sits on a line about this tall


def _intersects(rect, ns_rect) -> bool:
    x, y, w, h = rect
    ox, oy = ns_rect.origin.x, ns_rect.origin.y
    ow, oh = ns_rect.size.width, ns_rect.size.height
    return x < ox + ow and x + w > ox and y < oy + oh and y + h > oy


class SelectionButton(FloatingPanel):
    width = 34.0
    height = 34.0
    corner_radius = 17.0

    def init(self):
        self = objc.super(SelectionButton, self).init()
        if self is None:
            return None
        self.on_play: Callable[[], None] | None = None
        self.timeout = 4.0
        self._timer = None
        return self

    @objc.python_method
    def make_surface(self, rect, radius):
        """A solid content-coloured circle: ink on light, cream on dark."""
        return TintedView.make(rect, theme.CONTENT, radius)

    @objc.python_method
    def build(self, content):
        btn = symbol_button("play.fill", self, "play:", size=13.0, tint=theme.SURFACE)
        btn.setFrame_(((0, 0), (self.width, self.height)))
        content.addSubview_(btn)

    def play_(self, sender):
        self.hide()
        if self.on_play:
            self.on_play()

    # -- called from any thread ----------------------------------------------

    @objc.python_method
    def present(self, qx: float, qy: float, avoid=None, selection=None) -> None:
        """Show above the selection. `selection` is the selected text's screen rect
        (x, y, w, h, Quartz coordinates) when known; otherwise the button floats just above
        the line the cursor ended on. `avoid` is an NSRect (Cocoa coords) to stay clear of."""
        on_main(self._present, qx, qy, avoid, selection)

    @objc.python_method
    def dismiss(self) -> None:
        on_main(self.hide)

    @objc.python_method
    def _present(self, qx: float, qy: float, avoid=None, selection=None) -> None:
        if self._timer:
            self._timer.cancel()
        H = screen_height()
        if selection is not None:
            sx, sy, sw, sh = selection
            top = sy                                   # top edge of the selected text (Quartz)
            x = min(max(qx, sx), sx + sw) - self.width / 2  # centred on the cursor, kept within the selection
        else:
            top = qy - LINE_HEIGHT / 2
            x = qx - self.width / 2
        y = H - top + OFFSET                            # bottom of the button sits OFFSET above the selection
        if avoid is not None and _intersects((x, y, self.width, self.height), avoid):
            y = H - (top + (sh if selection else LINE_HEIGHT)) - OFFSET - self.height  # flip below
        self.show_at(x, y, screen=screen_at_quartz(qx, qy))
        self._timer = later(self.timeout, self.hide)
