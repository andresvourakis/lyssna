"""Decide whether a finished mouse gesture really selected text, and only then show the button.

A drag or double-click alone proves nothing: screenshot drags, file drags, window moves,
slider drags and Finder double-clicks look the same to the mouse tap. So the button needs
proof, never a guess. There are two kinds:

A. Accessibility. After the gesture the focused element reports selected text with a word in
   it, and that selection differs (in text or in range) from the snapshot taken at
   mouse-down. A selection that was already there before the press proves nothing: the drag
   may have been a screenshot or a slider over old highlighted text.
B. Clipboard. During the gesture someone other than Lyssna put text with a word on the
   clipboard. Some programs select without telling Accessibility: a terminal program that
   draws its own selection copies it on select, while the terminal truthfully says nothing is
   selected. Lyssna's own clipboard writes never count (capture notes their change counts).
   A button shown on that proof reads the clipboard when pressed, with no Cmd+C sent.

Everything else means no button: Accessibility can't answer, the snapshot at mouse-down is
missing, the selection did not change, or it is empty. Unknown means no. An app that neither
reports its selection nor copies on select gets no button; the Read Selection hotkey still
works there. Earlier versions guessed from the cursor's shape and the window under the press
when Accessibility was silent, and those guesses are what made the button pop up after
screenshot drags; capture now asks silent apps to turn their accessibility on instead.

A double or triple click is compared with the snapshot from the first press of the sequence:
by the second press some apps already show the word selected.

Threading: press() and gesture() run in the mouse tap callback on the main thread, which must
stay fast. Only the clipboard change count (one cheap call) is read there; every
Accessibility call runs on a worker thread. A generation counter, bumped on each press, lets
a newer press cancel a pending check, and the after-check waits a bounded time for the
before-snapshot."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import NamedTuple

from .events import Point, spawn

log = logging.getLogger("lyssna.selection")

Bounds = tuple[float, float, float, float]


class AXSelection(NamedTuple):
    """What Accessibility says about the focused element's selection. `supported` is False
    when the app can't tell us (no focused element, neither selection route answers); then
    `text` is "". `range` identifies where the selection is, so two readings can be compared
    (None when the app gave no range); `bounds` is its screen rectangle, when known."""

    supported: bool
    text: str
    range: Hashable | None = None
    bounds: Bounds | None = None


_WORD = re.compile(r"\w\w")


def has_word(text: str) -> bool:
    return bool(_WORD.search(text))


def selection_proof(before: AXSelection | None, after: AXSelection) -> bool:
    """Proof A: after the gesture a selection with a word is there, and it is not the one the
    snapshot at mouse-down saw (`before`, None when that snapshot is missing). An empty
    selection never counts, even when its range moved: a terminal's caret moves while output
    scrolls."""
    if before is None or not has_word(after.text):
        return False
    return before.text != after.text or before.range != after.range


def offers_button(before: AXSelection | None, after: AXSelection, copied: bool = False) -> bool:
    """Should a finished gesture show the play button? Only on proof: a new Accessibility
    selection (see selection_proof) or `copied`, text with a word that someone other than
    Lyssna put on the clipboard during the gesture."""
    return selection_proof(before, after) or copied


@dataclass
class _Press:
    generation: int
    clipboard: int | None = None  # the clipboard's change count at mouse-down
    first: _Press | None = None  # the first press of a double or triple click
    before: AXSelection | None = None
    ready: threading.Event = field(default_factory=threading.Event)


class SelectionWatcher:
    def __init__(
        self,
        probe_selection: Callable[[], AXSelection],
        present: Callable[[Point, Bounds | None, str], None],
        enabled: Callable[[], bool],
        probe_clipboard: Callable[[], int | None] = lambda: None,
        probe_copied: Callable[[int | None], str] = lambda count: "",
        wake: Callable[[], None] = lambda: None,
        delays: tuple[float, ...] = (0.05, 0.25),
        before_timeout: float = 0.5,
    ):
        self.probe_selection = probe_selection
        self.present = present
        self.enabled = enabled
        self.probe_clipboard = probe_clipboard  # the clipboard's change count; cheap, at press time
        self.probe_copied = probe_copied  # text someone else put on the clipboard since that count, or ""
        self.wake = wake  # asks the front app to report its selection, if it was never asked (worker thread)
        self.delays = delays
        self.before_timeout = before_timeout
        self._lock = threading.Lock()
        self._generation = 0
        self._press: _Press | None = None

    def press(self, p: Point, click_state: int = 1) -> None:
        """Mouse-down outside our UI (tap thread): note the clipboard's change count, which must
        be read before the gesture copies; the Accessibility snapshot runs off-thread.
        `click_state` 2 or more is a later click of a double or triple click."""
        clipboard = self.probe_clipboard()
        with self._lock:
            self._generation += 1
            prev = self._press
            first = (prev.first or prev) if click_state >= 2 and prev is not None else None
            rec = _Press(self._generation, clipboard, first)
            self._press = rec
        spawn(self._snapshot_before, rec, name="lyssna-selection")
        # Its own thread: a slow app must not hold up the before-snapshot.
        spawn(self.wake, name="lyssna-selection")

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

    def _snapshot_before(self, rec: _Press) -> None:
        try:
            if rec.first is not None:
                # By this press the app may already show the new word selected; the first
                # press of the sequence saw the state before the clicks.
                if rec.first.ready.wait(self.before_timeout):
                    rec.before = rec.first.before
            else:
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
            # Without a before-snapshot a new selection can't be told from an old one: no proof.
            before = rec.before if before_ready else None
            copied_text = self.probe_copied(rec.clipboard)
            copied = has_word(copied_text)
            # The button reads the selection when Accessibility proved it, else the clipboard.
            by_selection = selection_proof(before, after)
            show = offers_button(before, after, copied=copied)
            source = "selection" if by_selection else "clipboard"
            # Shown buttons log at INFO so a false pop-up leaves a trace in the normal log.
            # Lengths and booleans only: never the text.
            log.log(
                logging.INFO if show else logging.DEBUG,
                "selection check %d/%d: before=%s after=%s after_len=%d changed=%s"
                " copied=%d chars, word=%s -> %s",
                attempt, len(self.delays),
                "missing" if before is None else before.supported, after.supported, len(after.text),
                before is not None and (before.text != after.text or before.range != after.range),
                len(copied_text), copied, f"show ({source})" if show else "no",
            )
            if show:
                bounds = after.bounds if by_selection else None
                if self._current(rec):
                    self.present(p, bounds, source)
                return
