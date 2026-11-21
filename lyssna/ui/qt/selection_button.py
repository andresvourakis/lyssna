"""The little play button that pops up next to the cursor after you select text, the Windows twin
of ui/selection_button.py.

It lands exactly where the Mac one does (shared/button.place). On Windows the point and the
selection's rectangle come from the mouse hook and UI Automation in PHYSICAL pixels, so they are
converted to Qt logical coordinates first; place() then works in its Mac-style space (y up),
which the panel turns back into Qt's. Like every FloatingPanel it shows without taking focus, so
the user's selection in the other app stays selected; clicking it hides it, then reads."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

from PySide6.QtWidgets import QWidget

from ..shared.button import place
from . import coords, theme
from .mainthread import later, on_main
from .panel import FloatingPanel
from .widgets import IconButton

ICON = 13.0  # the play symbol's point size, as on the Mac


def mac_avoid(avoid):
    """`avoid` as shared/button.place wants it: a Mac-style rect with .origin and .size. Accepts
    that already, or a Qt logical rect (x, y, w, h, a QRect(F)), as FloatingPanel.frame() gives."""
    if avoid is None or hasattr(avoid, "origin"):
        return avoid
    x, y, w, h = coords.mac_rect_from_qt(coords.as_rect(avoid))
    return SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=h))


class SelectionButton(FloatingPanel):
    width = 34.0
    height = 34.0
    corner_radius = 17.0

    def __init__(self) -> None:
        self.on_play: Callable[[], None] | None = None
        self.timeout = 4.0
        self._timer = None
        super().__init__()
        # A solid content-coloured circle: ink on light, cream on dark.
        self.fill_color = theme.content
        self.no_border = True

    def build(self, content: QWidget) -> None:
        self.button = IconButton("play.fill", content, diameter=self.width, fill=theme.content, glow=theme.white_glow,
                                 icon_size=ICON, icon_color=theme.surface, clicked=self._play)
        self.button.move(0, 0)

    def _play(self) -> None:
        self.hide()
        if self.on_play:
            self.on_play()

    # -- called from any thread ----------------------------------------------------------------

    def present(self, qx: float, qy: float, avoid=None, selection=None) -> None:
        """Show above the selection. (qx, qy) is the cursor and `selection` the selected text's
        screen rect (x, y, w, h) when known, both in physical pixels; without it the button
        floats just above the line the cursor ended on. `avoid` is a rect to stay clear of (the
        player's frame, Qt logical, or a Mac-style rect)."""
        on_main(self._present, qx, qy, avoid, selection)

    def dismiss(self) -> None:
        on_main(self.hide)

    # -- on the GUI thread ---------------------------------------------------------------------

    def _present(self, qx: float, qy: float, avoid=None, selection=None) -> None:
        if self._timer:
            self._timer.cancel()
        lx, ly = coords.physical_to_logical(qx, qy)
        sel = coords.physical_rect_to_logical(selection) if selection is not None else None
        # place() takes the cursor and selection with y growing down from the primary screen's
        # top (Qt logical is exactly that) and answers a Mac-style bottom-left corner.
        x, y = place(lx, ly, (self.width, self.height), coords.primary_height(), mac_avoid(avoid), sel)
        self.button.set_hover(False, animate=False)
        self.show_at_mac(x, y, screen=coords.screen_at(lx, ly))
        self._timer = later(self.timeout, self.hide)


# -- previews (python -m lyssna.ui.qt.preview) -------------------------------------------------


def _preview(hover: bool = False) -> QWidget:
    b = SelectionButton()
    b.button.set_hover(hover, animate=False)
    return b.panel  # the window keeps its owner alive


PREVIEWS = {
    "selection-button": lambda: _preview(),
    "selection-button.hover": lambda: _preview(hover=True),
}
