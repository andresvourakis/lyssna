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
With nothing found it never moves.

So that it does not visibly jump each time, the button remembers the apps where a pop-up covered
it (layout/popup_wait). In such an app it shows nothing at first: it looks for the pop-up over
its spot every few hundredths of a second and shows once, clear of it, the moment it is there, or
at its usual spot when the app's short deadline passes. Anything going wrong in that wait shows it
at its usual spot at once. Every other app shows it at once, as before.

The learned apps are kept in STATE_DIR/popup_apps.json (MEMORY_FILE), read once when the button
is made and written, atomically, only when they change, so an app jumps once ever, not once per
launch. Only where pop-ups can be found: elsewhere the file is never read or written. A file that
is missing or cannot be read means no learned apps, and a failed write is logged and ignored."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from types import SimpleNamespace

from PySide6.QtWidgets import QWidget

from .. import config, host
from . import coords, theme
from .layout.button import clear_spot, place
from .layout.popup_wait import POLL, PopupMemory
from .mainthread import later, on_main
from .panel import FloatingPanel
from .widgets import IconButton

ICON = 13.0  # the play symbol's point size, as on the Mac
POPUP_CHECKS = (0.12, 0.35, 0.7)  # seconds after showing: Edge's toolbar opens a moment after mouse-up
MEMORY_FILE = "popup_apps.json"  # in STATE_DIR: the apps whose pop-up covers the button (layout/popup_wait)
MEMORY_MAX_BYTES = 64 * 1024  # a bigger file is not one the button wrote, so it is not read

log = logging.getLogger("lyssna.ui.selection_button")


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
        self._memory = PopupMemory()  # the apps whose pop-up covers the button, and how long to wait there
        self._wait = None         # the presentation waiting for its app's pop-up, if any
        self._presented = None    # the presentation showing now, for learning from the checks
        self._saved = None        # the memory as last read from or written to MEMORY_FILE
        self._load_memory()
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
        self.hide()  # also cancels the pop-up checks and any wait
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
        screen = coords.screen_at(lx, ly)
        if not getattr(host.window, "DETECTS_POPUPS", False):
            self._show(x, y, screen)
            return
        self._shown += 1
        p = SimpleNamespace(shown=self._shown, x=x, y=y, screen=screen, lx=lx, ly=ly, sel=sel, avoid=_qt_avoid(avoid),
                            app=self._front_app(), start=time.monotonic(), waited=False, found=False, settled=False)
        if self._memory.knows(p.app) and self._start_wait(p):
            return
        self._show(x, y, screen, p)

    def _show(self, x: float, y: float, screen, p=None, clear=None) -> None:
        """Show at the Mac-style corner (x, y), or at `clear` (a Qt logical top-left corner) when
        the wait found a pop-up first. The auto-hide counts from here. With a presentation `p`
        (where pop-ups can be found) the checks after showing start too."""
        if clear is None:
            self.show_at_mac(x, y, screen=screen)
        else:
            self.show_at(*clear, screen=screen)
        self._timer = later(self.timeout, self.hide)
        if p is not None:
            self._presented = p
            self._spot = (p.default if clear is not None else self.frame()[:2], p.lx, p.ly, p.sel, p.avoid)
            self._checks = [later(delay, self._check_popups, p.shown) for delay in POPUP_CHECKS]

    def _front_app(self) -> str | None:
        try:
            return host.window.front_app()
        except Exception:
            log.debug("could not ask which app is in front", exc_info=True)
            return None

    # -- waiting for a pop-up in an app that has one (layout/popup_wait) -------------------------

    def _start_wait(self, p) -> bool:
        """Show nothing yet: look for the app's pop-up over the would-be spot every POLL seconds
        and show once, clear of it, when it is there, or at the usual spot at the deadline.
        False when the wait could not start; then the caller shows the button at once."""
        try:
            self.hide()  # an older offer still showing goes now; this one shows once, when its spot is known
            w, h = self._size
            p.default = self._default_corner(p.x, p.y, p.screen)
            left, top, pw, ph = coords.logical_rect_to_physical((*p.default, w, h))
            p.rect = (left, top, left + pw, top + ph)
            p.deadline = self._memory.deadline(p.app)
            self._wait = p
            self._poll(p)
            return True
        except Exception:
            log.warning("the play button could not wait for a pop-up; showing it now", exc_info=True)
            self._wait = None
            self._cancel_checks()
            return False

    def _default_corner(self, x: float, y: float, screen) -> tuple[float, float]:
        """The Qt logical top-left corner show_at_mac(x, y, screen) would give, without showing."""
        w, h = self._size
        qx, qy, _, _ = coords.qt_rect_from_mac((x, y, w, h))
        vf = coords.visible_frame(screen or coords.screen_at(qx, qy))
        if vf[2] > 0 and vf[3] > 0:
            qx, qy, _, _ = coords.clamp_to((qx, qy, w, h), vf)
        return qx, qy

    def _poll(self, p) -> None:
        if p is not self._wait:
            return  # a new selection, a dismiss or a hide came first
        try:
            elapsed = time.monotonic() - p.start
            popups = host.window.popups_over(p.rect)
            if popups:
                self._end_wait(p, elapsed, self._clear_of(p.default, popups, p.lx, p.ly, p.sel, p.avoid))
            elif elapsed >= p.deadline:
                self._end_wait(p, None, None)
            else:
                self._checks = [later(min(POLL, p.deadline - elapsed), self._poll, p)]
        except Exception:
            log.warning("the play button's wait for a pop-up failed; showing it at its usual spot", exc_info=True)
            self._wait = None
            self._cancel_checks()
            if not self.visible():
                self._show(p.x, p.y, p.screen, p)

    def _end_wait(self, p, arrival: float | None, clear) -> None:
        """Show once: at `clear` when a pop-up arrived `arrival` seconds after the selection, else
        at the usual spot. One log line per such presentation, without the selected text."""
        p.waited = True
        try:
            if arrival is not None:
                p.found = True
                self._memory.covered(p.app, arrival)
            log.info("play button in %s: pop-up %s, waited up to %d ms", p.app,
                     "none" if arrival is None else f"after {arrival * 1000:.0f} ms", round(p.deadline * 1000))
        except Exception:
            log.debug("could not note the pop-up's timing", exc_info=True)
        self._wait = None
        self._show(p.x, p.y, p.screen, p, clear)
        self._save_memory()  # after showing, so the file never delays the button

    def _settle(self) -> None:
        """The current presentation is over: when it waited the whole deadline and nothing came,
        neither then nor in the checks after showing, it counts towards forgetting the app."""
        p, self._presented = self._presented, None
        if p is None or p.settled:
            return
        p.settled = True
        if p.waited and not p.found:
            if self._memory.missed(p.app):
                log.info("play button in %s: no pop-up three times in a row, it shows at once there again", p.app)
            self._save_memory()

    def _cancel_checks(self) -> None:
        self._wait = None
        for t in self._checks:
            t.cancel()
        self._checks = []
        try:
            self._settle()
        except Exception:
            log.debug("could not settle the play button's presentation", exc_info=True)

    # -- the learned apps between launches (MEMORY_FILE) --------------------------------------

    def _load_memory(self) -> None:
        """Read the learned apps once, where pop-ups can be found. Never raises: a missing,
        unreadable, oversized or wrong-shaped file leaves the memory empty."""
        try:
            if not getattr(host.window, "DETECTS_POPUPS", False):
                return
            try:
                with open(config.STATE_DIR / MEMORY_FILE, "rb") as f:
                    raw = f.read(MEMORY_MAX_BYTES + 1)
            except FileNotFoundError:
                raw = None
            if raw is not None and len(raw) > MEMORY_MAX_BYTES:
                log.warning("the apps with a pop-up were not read: the file is too big")
            elif raw is not None:
                self._memory.load(json.loads(raw.decode("utf-8")))
        except Exception as e:
            log.warning("the apps with a pop-up could not be read (%s); starting with none", type(e).__name__)
            self._memory = PopupMemory()
        try:
            self._saved = self._memory.export()
        except Exception:
            self._saved = None

    def _save_memory(self) -> None:
        """Write the learned apps when they changed since the last read or write, through a
        temporary file so a reader never sees half of it. Never raises: a failed write is
        logged, and the apps stay in memory."""
        try:
            if not getattr(host.window, "DETECTS_POPUPS", False):
                return
            state = self._memory.export()
            if state == self._saved:
                return
            path = config.STATE_DIR / MEMORY_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            self._saved = state
        except Exception as e:
            log.warning("the apps with a pop-up could not be saved (%s); they stay in memory", type(e).__name__)

    def _clear_of(self, default, popups, lx, ly, sel, avoid) -> tuple[float, float]:
        """The first spot clear of `popups` (physical l, t, r, b) and of `avoid`, from `default`."""
        obstacles = [coords.physical_rect_to_logical((left, top, right - left, bottom - top))
                     for left, top, right, bottom in popups]
        if avoid is not None:
            obstacles.append(avoid)
        return clear_spot(default, self._size, lx, ly, obstacles, sel)

    def _learn(self) -> None:
        """A check after showing found a pop-up over the button: learn the app, or, when it
        waited and nothing came in time, let this late arrival raise its deadline."""
        p = self._presented
        if p is None or p.app is None or p.found:
            return
        p.found = True
        arrival = time.monotonic() - p.start
        if self._memory.covered(p.app, arrival if p.waited else None):
            log.info("play button in %s: a pop-up covered it, it waits for one there from now on", p.app)
        elif p.waited:
            log.info("play button in %s: pop-up after %.0f ms, later than the wait", p.app, arrival * 1000)
        self._save_memory()

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
        try:
            self._learn()
        except Exception:
            log.debug("could not learn the app's pop-up", exc_info=True)
        default, lx, ly, sel, avoid = self._spot
        nx, ny = self._clear_of(default, popups, lx, ly, sel, avoid)
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
