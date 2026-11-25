"""The little play button that pops up next to the cursor after you select text, the Windows twin
of legacy/ui/selection_button.py.

It lands exactly where the Mac one does (layout/button.place). On Windows the point and the
selection's rectangle come from the mouse hook and UI Automation in PHYSICAL pixels, so they are
converted to Qt logical coordinates first; place() then works in its Mac-style space (y up),
which the panel turns back into Qt's. Like every FloatingPanel it shows without taking focus, so
the user's selection in the other app stays selected; clicking it hides it, then reads.

Edge opens its own selection toolbar right where the button lands, a moment after mouse-up.
Where host.window can find such pop-ups (DETECTS_POPUPS), the button looks a few times while it
is showing and, only when one covers it, moves to the first clear spot (layout/button.clear_spot).
With nothing found it never moves."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

from PySide6.QtWidgets import QWidget

from .. import host
from . import coords, theme
from .layout.button import clear_spot, place
from .mainthread import later, on_main
from .panel import FloatingPanel
from .widgets import IconButton

ICON = 13.0  # the play symbol's point size, as on the Mac
POPUP_CHECKS = (0.12, 0.35, 0.7)  # seconds after showing: Edge's toolbar opens a moment after mouse-up


def mac_avoid(avoid):
    """`avoid` as layout/button.place wants it: a Mac-style rect with .origin and .size. Accepts
    that already, or a Qt logical rect (x, y, w, h, a QRect(F)), as FloatingPanel.frame() gives."""
    if avoid is None or hasattr(avoid, "origin"):
        return avoid
    x, y, w, h = coords.mac_rect_from_qt(coords.as_rect(avoid))
    return SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=h))


def _qt_avoid(avoid):
    """`avoid` as a Qt logical rect (x, y, w, h), from either form present() takes."""
    if avoid is None:
        return None
    if hasattr(avoid, "origin"):
        return coords.qt_rect_from_mac((avoid.origin.x, avoid.origin.y, avoid.size.width, avoid.size.height))
    return coords.as_rect(avoid)


class SelectionButton(FloatingPanel):
    width = 34.0
    height = 34.0
    corner_radius = 17.0

    def __init__(self) -> None:
        self.on_play: Callable[[], None] | None = None
        self.timeout = 4.0
        self._timer = None
        self._checks: list = []  # pending pop-up checks for the current presentation
        self._shown = 0           # counts presentations, so a stale check does nothing
        self._spot = None         # what the checks need: default corner, cursor, selection, avoid (Qt logical)
        super().__init__()
        # A solid content-coloured circle: ink on light, cream on dark.
        self.fill_color = theme.content
        self.no_border = True

    def build(self, content: QWidget) -> None:
        # The Mac's button is a bare symbol button on the panel's disc: no hover light, and
        # pressed only its icon takes the pressed tint (IconButton does exactly that).
        self.button = IconButton("play.fill", content, diameter=self.width, fill=theme.content, glow=None,
                                 icon_size=ICON, icon_color=theme.surface, clicked=self._play)
        self.button.move(0, 0)

    def _play(self) -> None:
        self.hide()  # also cancels the pop-up checks
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

    def hide(self) -> None:
        self._cancel_checks()
        super().hide()

    def _present(self, qx: float, qy: float, avoid=None, selection=None) -> None:
        if self._timer:
            self._timer.cancel()
        self._cancel_checks()
        lx, ly = coords.physical_to_logical(qx, qy)
        sel = coords.physical_rect_to_logical(selection) if selection is not None else None
        # place() takes the cursor and selection with y growing down from the primary screen's
        # top (Qt logical is exactly that) and answers a Mac-style bottom-left corner.
        x, y = place(lx, ly, (self.width, self.height), coords.primary_height(), mac_avoid(avoid), sel)
        self.button.set_hover(False, animate=False)
        self.show_at_mac(x, y, screen=coords.screen_at(lx, ly))
        self._timer = later(self.timeout, self.hide)
        if getattr(host.window, "DETECTS_POPUPS", False):
            self._shown += 1
            self._spot = (self.frame()[:2], lx, ly, sel, _qt_avoid(avoid))
            self._checks = [later(delay, self._check_popups, self._shown) for delay in POPUP_CHECKS]

    def _cancel_checks(self) -> None:
        for t in self._checks:
            t.cancel()
        self._checks = []

    def _check_popups(self, shown: int) -> None:
        """Move off another app's pop-up when one covers the button. Only for the presentation
        that scheduled it, and only while it is still showing."""
        if shown != self._shown or not self.visible() or self._spot is None:
            return
        x, y, w, h = self.frame()
        px, py, pw, ph = coords.logical_rect_to_physical((x, y, w, h))
        popups = host.window.popups_over((px, py, px + pw, py + ph))
        if not popups:
            return
        default, lx, ly, sel, avoid = self._spot
        obstacles = [coords.physical_rect_to_logical((left, top, right - left, bottom - top))
                     for left, top, right, bottom in popups]
        if avoid is not None:
            obstacles.append(avoid)
        nx, ny = clear_spot(default, (w, h), lx, ly, obstacles, sel)
        if (nx, ny) != (x, y):
            self.show_at(nx, ny, screen=coords.screen_at(lx, ly))


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------


def _preview(hover: bool = False) -> QWidget:
    b = SelectionButton()
    b.button.set_hover(hover, animate=False)
    return b.panel  # the window keeps its owner alive


PREVIEWS = {
    "selection-button": lambda: _preview(),
    "selection-button.hover": lambda: _preview(hover=True),
}
