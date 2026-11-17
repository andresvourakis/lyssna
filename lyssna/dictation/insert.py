"""Paste dictated text at the cursor in whatever app is in front.

macOS has no general "insert text here" call that every app honours, so we do what a person
does: put the text on the clipboard, press Cmd+V, and then put the previous clipboard back
(every item in every format, through the same helpers the Cmd+C fallback uses). Our clipboard
entry carries the nspasteboard.org markers `org.nspasteboard.TransientType` (it is about to be
replaced) and `org.nspasteboard.ConcealedType` (do not show or keep it), so clipboard managers
that follow the convention neither record nor display the dictated text.

Whether to paste at all is decided by one question: is there a focused element that takes
text? Accessibility answers it for native apps (an editable value, or a settable text
selection). Browsers and Electron apps often expose only a window, a group or a web area for
what is really a text editor, so a generic container counts as "maybe" and we paste anyway.
Only when the focused thing is clearly something else (the desktop, a list, a button) does
the text stay on the clipboard for the user to paste by hand ("copied").

The text is pasted exactly as given: no trailing space is added."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

from .. import capture

log = logging.getLogger("lyssna.dictation.insert")

KEY_V = 9
RESTORE_DELAY = 0.5  # seconds before the previous clipboard comes back; slow apps read the paste lazily

TRANSIENT_TYPE = "org.nspasteboard.TransientType"
CONCEALED_TYPE = "org.nspasteboard.ConcealedType"

# Roles that can stand for a text editor whose inside Accessibility does not show us (web
# pages, Electron apps, apps with Accessibility half switched on). Focus on one of these is
# not proof that there is no text field, so we paste.
CONTAINER_ROLES = {"AXWindow", "AXGroup", "AXWebArea", "AXUnknown", "AXApplication", "AXLayoutArea", "AXSplitGroup"}


def text_target(element) -> str:
    """"text" when the element takes typed text, "maybe" when we cannot tell, "none" when it
    clearly does not (a list, a button, the desktop)."""
    if element is None:
        return "maybe"  # nothing exposed: common in Electron apps and some browsers
    if capture.ax_settable(element, "AXValue") and capture.ax_text_value(element) is not None:
        return "text"
    if capture.ax_settable(element, "AXSelectedTextRange"):
        return "text"
    role = capture.ax_role(element)
    if role is None or role in CONTAINER_ROLES:
        return "maybe"
    return "none"


def _text_item(text: str, transient: bool):
    item = NSPasteboardItem.alloc().init()
    item.setString_forType_(text, NSPasteboardTypeString)
    if transient:
        item.setString_forType_("", TRANSIENT_TYPE)
        item.setString_forType_("", CONCEALED_TYPE)
    return item


def put_text(pb, text: str, transient: bool) -> int:
    """Replace the clipboard with `text`; returns the pasteboard's change count afterwards."""
    pb.clearContents()
    pb.writeObjects_([_text_item(text, transient)])
    return int(pb.changeCount())


def restore_unless_changed(pb, previous: list[dict[str, bytes]], our_count: int) -> bool:
    """Put the previous clipboard back, unless something else was copied since our paste."""
    if int(pb.changeCount()) != our_count:
        log.info("clipboard changed after the paste; not restoring it")
        return False
    capture.restore_pasteboard(pb, previous)
    return True


class Inserter:
    """paste(text) -> "pasted" | "copied". Every machine call is injectable for tests."""

    def __init__(
        self,
        pasteboard=None,
        focused: Callable[[], object] = capture.focused_element,
        target: Callable[[object], str] = text_target,
        send_key: Callable[[int, int], None] = capture.send_key,
        wait_for_modifiers: Callable[[], None] = capture._wait_for_modifiers_release,
        later: Callable[[float, Callable[[], None]], None] | None = None,
        restore_delay: float = RESTORE_DELAY,
    ):
        self._pasteboard = pasteboard
        self.focused, self.target = focused, target
        self.send_key, self.wait_for_modifiers = send_key, wait_for_modifiers
        self.later = later or _later
        self.restore_delay = restore_delay

    @property
    def pasteboard(self):
        return self._pasteboard or NSPasteboard.generalPasteboard()

    def __call__(self, text: str) -> str:
        return self.paste(text)

    def paste(self, text: str) -> str:
        pb = self.pasteboard
        kind = self.target(self.focused())
        if kind == "none":
            put_text(pb, text, transient=False)  # the user pastes it themselves: a normal clipboard entry
            log.info("no text field in front; %d chars left on the clipboard", len(text))
            return "copied"
        previous = capture.snapshot_pasteboard(pb)
        count = put_text(pb, text, transient=True)
        self.wait_for_modifiers()
        self.send_key(KEY_V, Quartz.kCGEventFlagMaskCommand)
        self.later(self.restore_delay, lambda: restore_unless_changed(pb, previous, count))
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
