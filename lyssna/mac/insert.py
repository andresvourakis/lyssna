"""The Mac pieces of pasting dictated text: the clipboard writes, the paste key and the question
"does the focused element take text?". lyssna.dictation.insert decides what to do with them
and reaches this module through lyssna.host.

Our clipboard entry carries the nspasteboard.org markers `org.nspasteboard.TransientType` (it
is about to be replaced) and `org.nspasteboard.ConcealedType` (do not show or keep it), so
clipboard managers that follow the convention neither record nor display the dictated text.
The previous clipboard is put back through the same helpers the Cmd+C fallback in
lyssna/mac/capture.py uses (every item in every format).

Whether to paste at all: Accessibility answers it for native apps (an editable value, or a
settable text selection). Browsers and Electron apps often expose only a window, a group or a
web area for what is really a text editor, so a generic container counts as "maybe" and we
paste anyway. Only when the focused thing is clearly something else (the desktop, a list, a
button) does the text stay on the clipboard for the user to paste by hand. AppKit and Quartz
load where they are used."""

from __future__ import annotations

import logging

from . import capture

log = logging.getLogger("lyssna.mac.insert")

KEY_V = 9

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


def focused_element():
    """The element that has keyboard focus in the app in front, or None."""
    return capture.focused_element()


def pasteboard():
    """The general pasteboard, the one Cmd+V reads."""
    from AppKit import NSPasteboard

    return NSPasteboard.generalPasteboard()


def _text_item(text: str, transient: bool):
    from AppKit import NSPasteboardItem, NSPasteboardTypeString

    item = NSPasteboardItem.alloc().init()
    item.setString_forType_(text, NSPasteboardTypeString)
    if transient:
        item.setString_forType_("", TRANSIENT_TYPE)
        item.setString_forType_("", CONCEALED_TYPE)
    return item


def put_text(pb, text: str, transient: bool) -> int:
    """Replace the clipboard with `text`; returns the pasteboard's change count afterwards.
    The change is noted as Lyssna's own, so it never passes for a selection the user copied."""
    before = int(pb.changeCount())
    pb.clearContents()
    pb.writeObjects_([_text_item(text, transient)])
    count = int(pb.changeCount())
    capture.note_own_changes(before, count)
    return count


def snapshot(pb) -> list[dict[str, bytes]]:
    """Every item on the clipboard, in every format, to put back after the paste."""
    return capture.snapshot_pasteboard(pb)


def restore_unless_changed(pb, previous: list[dict[str, bytes]], our_count: int) -> bool:
    """Put the previous clipboard back, unless something else was copied since our paste."""
    if int(pb.changeCount()) != our_count:
        log.info("clipboard changed after the paste; not restoring it")
        return False
    capture.restore_pasteboard(pb, previous)
    return True


def wait_for_modifiers() -> None:
    """Wait (briefly) until the user lets go of the modifier keys, so Cmd+V is not Cmd+Option+V."""
    capture._wait_for_modifiers_release()


def send_key(keycode: int, flags: int = 0) -> None:
    capture.send_key(keycode, flags)


def paste_flags() -> int:
    """The modifier flags of the paste shortcut: Command."""
    import Quartz

    return Quartz.kCGEventFlagMaskCommand
