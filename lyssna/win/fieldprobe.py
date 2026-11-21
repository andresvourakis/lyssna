"""The reads lyssna.dictation.learn.EditWatcher makes of the text field a dictation was pasted
into, on Windows: through UI Automation, on the capture module's UI Automation thread, each
with a short timeout so a busy app never holds us up. lyssna.host hands this out as
host.field_probe; lyssna/mac/fieldprobe.py is its Mac twin.

COM objects never leave the UI Automation thread. focused() keeps the element there and hands
out an opaque handle; value() and same() look the element up by that handle. Only the last
few handles are kept (the watcher reads its own element on every poll, which keeps it), and
an element that has been let go reads as unknown.

Every failure looks the way the watcher expects from the Mac probe, so it stops quietly:
focused() None, value() None, same() False, front_pid() None. Our own windows are never
read, and neither are console windows: a terminal's text is its whole scrollback, which moves
with every line of output, and there is no dictated text to learn from in it."""

from __future__ import annotations

import itertools
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass

from . import capture

log = logging.getLogger("lyssna.win.fieldprobe")

_ANCESTOR_LEVELS = 2  # how far up from the focused element its text may be held (as in lyssna.win.insert)


@dataclass(frozen=True)
class _Field:
    """The handle focused() hands out for an element kept on the UI Automation thread."""

    key: int


class FieldProbe:
    """The UI Automation reads EditWatcher needs, in one place so tests can replace them."""

    timeout = 0.2  # seconds a UI Automation read may take before we give up on it
    max_chars = 200_000  # EditWatcher.MAX_CHARS: a longer text is read as unknown, on the UI Automation thread
    keep = 8  # elements kept on the UI Automation thread at once

    def __init__(self):
        self._elements: OrderedDict[int, dict] = OrderedDict()  # touched only on the UI Automation thread
        self._keys = itertools.count(1)

    def _call(self, fn):
        return capture.ui_automation(fn, self.timeout)

    def focused(self) -> _Field | None:
        try:
            if capture._console_in_front():
                return None
            return self._call(self._focus)
        except Exception as e:
            log.debug("focused element read failed: %s", type(e).__name__)
            return None

    def value(self, element) -> str | None:
        if not isinstance(element, _Field):
            return None
        try:
            return self._call(lambda UIA, automation: self._read(UIA, automation, element.key))
        except Exception as e:
            log.debug("field value read failed: %s", type(e).__name__)
            return None

    def same(self, a, b) -> bool:
        if not isinstance(a, _Field) or not isinstance(b, _Field):
            return False
        if a.key == b.key:
            return True
        try:
            return bool(self._call(lambda UIA, automation: self._compare(automation, a.key, b.key)))
        except Exception as e:
            log.debug("element comparison failed: %s", type(e).__name__)
            return False

    def front_pid(self) -> int | None:
        return capture.front_app_pid()

    # -- on the UI Automation thread ------------------------------------------------------

    def _entry(self, key: int) -> dict | None:
        entry = self._elements.get(key)
        if entry is not None:
            self._elements.move_to_end(key)  # in use: kept
        return entry

    def _focus(self, UIA, automation) -> _Field | None:
        element = automation.GetFocusedElement()
        if not element or int(element.CurrentProcessId) == os.getpid():
            return None
        key = next(self._keys)
        self._elements[key] = {"element": element, "reader": None}
        while len(self._elements) > self.keep:
            self._elements.popitem(last=False)  # released here, on the thread that made it
        return _Field(key)

    def _read(self, UIA, automation, key: int) -> str | None:
        entry = self._entry(key)
        if entry is None:
            return None
        if entry["reader"] is None:
            entry["reader"] = _reader(UIA, automation, entry["element"]) or (lambda limit: None)
        return entry["reader"](self.max_chars)

    def _compare(self, automation, a: int, b: int) -> bool:
        first, second = self._entry(a), self._entry(b)
        if first is None or second is None:
            return False
        return bool(automation.CompareElements(first["element"], second["element"]))


def _reader(UIA, automation, element):
    """How to read this element's text: its Value pattern when it is not read-only, else its
    Text pattern's whole document, from the element or a near ancestor. None when it has
    neither. The reader takes the character cap and returns None past it."""
    walker = automation.ControlViewWalker
    for _ in range(_ANCESTOR_LEVELS + 1):
        unknown = element.GetCurrentPattern(UIA.UIA_ValuePatternId)
        if unknown:
            value = unknown.QueryInterface(UIA.IUIAutomationValuePattern)
            if not value.CurrentIsReadOnly:
                return lambda limit: _capped(value.CurrentValue, limit)
        unknown = element.GetCurrentPattern(UIA.UIA_TextPatternId)
        if unknown:
            text = unknown.QueryInterface(UIA.IUIAutomationTextPattern)
            # At most one character past the cap crosses from the app: enough to know it is too long.
            return lambda limit: _capped(text.DocumentRange.GetText(limit + 1), limit)
        element = walker.GetParentElement(element)
        if not element:
            return None
    return None


def _capped(text, limit: int) -> str | None:
    if text is None:
        return None
    text = str(text)
    return None if len(text) > limit else text
