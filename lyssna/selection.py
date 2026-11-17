"""Decide whether a finished mouse gesture really selected text, and only then show the button.

A drag or double-click alone proves nothing: screenshot drags, file drags, window moves and
Finder double-clicks look the same to the mouse tap. So at mouse-down we note whether the
cursor was a standard non-text one (arrow, hand, resize...), which process owns the window
under the press, and the Accessibility selection; after the gesture we ask AX again and compare
the press owner with the frontmost app. `offers_button` decides from those facts, blocking only
on evidence: unknowns count in favour of showing.

Threading: press() and gesture() run in the mouse tap callback on the main thread, which must
stay fast. Only the cursor probe (cheap, and main-thread only) runs there; every AX and window
call runs on a worker thread. The press owner is looked up at press time, while an overlay
such as the screenshot tool still exists. A generation counter, bumped on each press, lets a newer press cancel a
pending check, and the after-check waits a bounded time for the before-snapshot."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from .events import Point, spawn

log = logging.getLogger("lyssna.selection")

Bounds = tuple[float, float, float, float]


class AXSelection(NamedTuple):
    """What Accessibility says about the focused element's selection. `supported` is False
    when the app can't tell us (no focused element, attribute unsupported); then `text` is ""."""

    supported: bool
    text: str


_WORD = re.compile(r"\w\w")


def offers_button(
    before: AXSelection, after: AXSelection, non_text_cursor: bool, pressed_in_front_app: bool
) -> bool:
    """Should a finished selection gesture show the play button?

    `before` is the selection at mouse-down, `after` the one after the gesture.
    `non_text_cursor`: the press began on a standard non-text cursor (arrow, hand, resize...).
    `pressed_in_front_app`: the press landed on a window of the frontmost app (False for
    overlays such as the screenshot tool). We block only on evidence:
    1. press on another app's window -> no;
    2. AX can answer: a word must be selected, and it must be new unless the cursor was
       not a standard non-text one;
    3. AX can't answer: show unless the cursor was a standard non-text one."""
    if not pressed_in_front_app:
        return False
    if after.supported:
        if not _WORD.search(after.text):
            return False
        changed = not before.supported or before.text != after.text
        return changed or not non_text_cursor
    return not non_text_cursor


@dataclass
class _Press:
    generation: int
    non_text_cursor: bool
    before: AXSelection | None = None
    owner_pid: int | None = None
    ready: threading.Event = field(default_factory=threading.Event)


class SelectionWatcher:
    def __init__(
        self,
        probe_cursor: Callable[[], bool],
        probe_selection: Callable[[], AXSelection],
        probe_press_owner: Callable[[Point], int | None],
        probe_front_pid: Callable[[], int | None],
        present: Callable[[Point, Bounds | None], None],
        enabled: Callable[[], bool],
        probe_bounds: Callable[[], Bounds | None] | None = None,
        delays: tuple[float, ...] = (0.05, 0.25),
        before_timeout: float = 0.5,
        own_pid: int | None = None,
    ):
        self.probe_cursor = probe_cursor
        self.probe_selection = probe_selection
        self.probe_press_owner = probe_press_owner
        self.probe_front_pid = probe_front_pid
        self.present = present
        self.enabled = enabled
        if probe_bounds is None:
            from . import capture

            probe_bounds = capture.ax_selection_bounds
        self.probe_bounds = probe_bounds
        self.delays = delays
        self.before_timeout = before_timeout
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self._lock = threading.Lock()
        self._generation = 0
        self._press: _Press | None = None

    def press(self, p: Point) -> None:
        """Mouse-down outside our UI (tap thread): note the cursor; owner and AX off-thread.
        `probe_cursor` answers "standard non-text cursor?" and must run here (main thread)."""
        non_text_cursor = self.probe_cursor()
        with self._lock:
            self._generation += 1
            rec = _Press(self._generation, non_text_cursor)
            self._press = rec
        spawn(self._snapshot_before, rec, p, name="lyssna-selection")

    def gesture(self, p: Point) -> None:
        """A selection-like gesture finished (tap thread): check AX off-thread."""
        with self._lock:
            rec = self._press
        if rec is None:
            return
        spawn(self._check, rec, p, name="lyssna-selection")

    def _current(self, rec: _Press) -> bool:
        with self._lock:
            return self._generation == rec.generation

    def _snapshot_before(self, rec: _Press, p: Point) -> None:
        try:
            rec.owner_pid = self.probe_press_owner(p)
            rec.before = self.probe_selection()
        finally:
            rec.ready.set()

    def _check(self, rec: _Press, p: Point) -> None:
        before_ready = rec.ready.wait(self.before_timeout)
        for attempt, delay in enumerate(self.delays, 1):
            if delay:
                time.sleep(delay)
            if not self._current(rec):
                log.debug("selection check: superseded by a newer press")
                return
            if not self.enabled():
                log.debug("selection check: button disabled")
                return
            after = self.probe_selection()
            front_pid = self.probe_front_pid()
            # Without a before-snapshot we can't tell a new selection from an old one, so treat
            # it as unchanged: then only a non-standard cursor can vouch for it.
            before = rec.before if before_ready and rec.before is not None else after
            # Unknown owner or front app counts as the same app: block only on evidence.
            owner = rec.owner_pid if before_ready else None
            # A press on our own UI (status item, menu, windows) never offers the button.
            in_front = owner != self.own_pid and (owner is None or front_pid is None or owner == front_pid)
            show = offers_button(before, after, rec.non_text_cursor, in_front)
            # Shown buttons log at INFO so a false pop-up leaves a trace in the normal log.
            log.log(
                logging.INFO if show else logging.DEBUG,
                "selection check %d/%d: before=%s after=%s changed=%s non_text_cursor=%s owner=%s"
                " front=%s before_ready=%s -> %s",
                attempt, len(self.delays), before.supported, after.supported, before.text != after.text,
                rec.non_text_cursor, owner, front_pid, before_ready, "show" if show else "no",
            )
            if show:
                bounds = self.probe_bounds()
                if self._current(rec):
                    self.present(p, bounds)
                return
