"""The little play button that pops up next to the cursor after you select text."""

from __future__ import annotations

from collections.abc import Callable

import objc

from . import theme
from .mainthread import later, on_main
from .panel import FloatingPanel, TintedView, screen_at_quartz, screen_height, symbol_button
from .shared.button import (
    LINE_HEIGHT,  # noqa: F401  (re-exported)
    OFFSET,  # noqa: F401  (re-exported)
    _intersects,  # noqa: F401  (re-exported)
    place,
)


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
        x, y = place(qx, qy, (self.width, self.height), screen_height(), avoid, selection)
        self.show_at(x, y, screen=screen_at_quartz(qx, qy))
        self._timer = later(self.timeout, self.hide)
