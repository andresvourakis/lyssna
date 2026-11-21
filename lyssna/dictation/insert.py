"""Paste dictated text at the cursor in whatever app is in front.

There is no general "insert text here" call that every app honours, so we do what a person
does: put the text on the clipboard, press the paste shortcut, and then put the previous
clipboard back. Our clipboard entry is marked so that clipboard managers neither record nor
display the dictated text.

Whether to paste at all is decided by one question: is there a focused element that takes
text? "text" and "maybe" paste; only "none" (the desktop, a list, a button) leaves the text on
the clipboard for the user to paste by hand ("copied").

The system's pieces (the clipboard writes, the paste key, the focused-element question) come
from host.insert, looked up when a paste happens: lyssna/mac/insert.py on the Mac.

The text is pasted exactly as given: no trailing space is added."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .. import host

log = logging.getLogger("lyssna.dictation.insert")

RESTORE_DELAY = 0.5  # seconds before the previous clipboard comes back; slow apps read the paste lazily


class Inserter:
    """paste(text) -> "pasted" | "copied". Every machine call is injectable for tests."""

    def __init__(
        self,
        pasteboard=None,
        focused: Callable[[], object] | None = None,
        target: Callable[[object], str] | None = None,
        send_key: Callable[[int, int], None] | None = None,
        wait_for_modifiers: Callable[[], None] | None = None,
        later: Callable[[float, Callable[[], None]], None] | None = None,
        restore_delay: float = RESTORE_DELAY,
    ):
        # Left out, the machine calls are host.insert's, looked up when a paste happens, so an
        # Inserter can be built on a system that has no paste pieces yet.
        self._pasteboard = pasteboard
        self.focused, self.target = focused, target
        self.send_key = send_key
        self.wait_for_modifiers = wait_for_modifiers
        self.later = later or _later
        self.restore_delay = restore_delay

    def __call__(self, text: str) -> str:
        return self.paste(text)

    def paste(self, text: str) -> str:
        system = host.require("insert")
        pb = self._pasteboard or system.pasteboard()
        kind = (self.target or system.text_target)((self.focused or system.focused_element)())
        if kind == "none":
            system.put_text(pb, text, transient=False)  # the user pastes it themselves: a normal clipboard entry
            log.info("no text field in front; %d chars left on the clipboard", len(text))
            return "copied"
        previous = system.snapshot(pb)
        count = system.put_text(pb, text, transient=True)
        (self.wait_for_modifiers or system.wait_for_modifiers)()
        (self.send_key or system.send_key)(system.KEY_V, system.paste_flags())
        self.later(self.restore_delay, lambda: system.restore_unless_changed(pb, previous, count))
        log.info("pasted %d chars (target %s)", len(text), kind)
        return "pasted"


def _later(delay: float, fn: Callable[[], None]) -> None:
    def run() -> None:
        time.sleep(delay)
        try:
            fn()
        except Exception:
            log.exception("restoring the clipboard failed")

    threading.Thread(target=run, daemon=True, name="lyssna-paste-restore").start()


_default: Inserter | None = None


def paste(text: str) -> str:
    """Paste `text` at the cursor; "pasted", or "copied" when there is no text field in front."""
    global _default
    if _default is None:
        _default = Inserter()
    return _default.paste(text)
