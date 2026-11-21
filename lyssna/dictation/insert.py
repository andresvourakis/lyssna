"""Paste dictated text at the cursor in whatever app is in front.

There is no general "insert text here" call that every app honours, so we do what a person
does: put the text on the clipboard, press the paste shortcut, and then put the previous
clipboard back. Our clipboard entry is marked so that clipboard managers (and on Windows the
clipboard history and cloud sync) neither record nor display the dictated text.

Whether to paste at all is decided by one question: is there a focused element that takes
text? "text" and "maybe" paste; only "none" (the desktop, a list, a button) leaves the text on
the clipboard as a normal entry for the user to paste by hand ("copied").

The system's pieces come from host.insert (lyssna/mac/insert.py or lyssna/win/insert.py),
looked up when a paste happens. Both offer the same five operations:
text_target() -> "text" | "maybe" | "none", put_text(text, transient) -> token,
snapshot() -> saved, restore_unless_changed(saved, token), and paste().

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
    """paste(text) -> "pasted" | "copied". The system's pieces and the delayed restore are
    injectable for tests."""

    def __init__(
        self,
        system=None,
        later: Callable[[float, Callable[[], None]], None] | None = None,
        restore_delay: float = RESTORE_DELAY,
    ):
        self._system = system  # host.insert unless a test passes a fake, looked up at paste time
        self.later = later or _later
        self.restore_delay = restore_delay

    def __call__(self, text: str) -> str:
        return self.paste(text)

    def paste(self, text: str) -> str:
        system = self._system or host.insert
        kind = system.text_target()
        if kind == "none":
            system.put_text(text, transient=False)  # the user pastes it themselves: a normal clipboard entry
            log.info("no text field in front; %d chars left on the clipboard", len(text))
            return "copied"
        saved = system.snapshot()
        token = system.put_text(text, transient=True)
        system.paste()
        self.later(self.restore_delay, lambda: system.restore_unless_changed(saved, token))
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
