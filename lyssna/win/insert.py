"""The Windows pieces of pasting dictated text: the question "does the focused element take
text?", the clipboard writes, and the paste key. lyssna.dictation.insert decides what to do
with them and reaches this module through lyssna.host; lyssna/mac/insert.py is its Mac twin,
with the same five functions.

Typing is the clipboard and Ctrl+V sent with SendInput, as on the Mac with Cmd+V: there is no
call every Windows app honours for inserting text, and typing the text key by key is slow and
is changed by keyboard layouts and autocomplete. Our clipboard entry carries the formats that
keep it out of the clipboard history (Win+V), out of cloud clipboard sync, and away from
clipboard monitors that honour the convention (lyssna.win.clipboard.set_text with transient).

The token is the clipboard sequence number once it has settled after our write: the number
moves once per format, and another program may add formats to a new clipboard just after it
appears, so we wait for it to stand still the way the Ctrl+C route in lyssna/win/capture.py
does. The saved clipboard goes back only if the number is still that token after the paste,
so a copy the user made in between is never overwritten.

Whether to paste at all is asked of UI Automation, on the capture module's UI Automation
thread and never about our own windows (see _classify for the rules). Console and terminal
windows count as "maybe": Ctrl+V pastes in Windows Terminal and in the console host of
Windows 10 and 11, and UI Automation says little about their input line.

Nothing Windows-only is imported when this module loads, so the tests run on any system with
the clipboard, SendInput and UI Automation faked."""

from __future__ import annotations

import logging
import os

from . import capture, clipboard, hotkeys

log = logging.getLogger("lyssna.win.insert")

KEY_V = 0x56  # the V key's virtual-key code

TARGET_TIMEOUT = 0.5  # seconds the text-field question may take before we paste anyway
_ANCESTOR_LEVELS = 2  # how far up from the focused element a text field may sit (a caret inside an editor)

# UI Automation control type ids (UIA_<name>ControlTypeId).
BUTTON, CALENDAR, CHECK_BOX, COMBO_BOX, EDIT, HYPERLINK, IMAGE, LIST_ITEM, LIST = range(50000, 50009)
MENU, MENU_BAR, MENU_ITEM, PROGRESS_BAR, RADIO_BUTTON, SCROLL_BAR, SLIDER = range(50009, 50016)
STATUS_BAR, TAB, TAB_ITEM, TEXT, TOOL_BAR, TOOL_TIP, TREE, TREE_ITEM = 50017, 50018, 50019, 50020, 50021, 50022, 50023, 50024
THUMB, DOCUMENT, SPLIT_BUTTON, HEADER, HEADER_ITEM, TITLE_BAR, SEPARATOR = 50027, 50030, 50031, 50034, 50035, 50037, 50038

# Control types whose Text pattern is a place to type (a read-only page's text is not).
EDITABLE_TYPES = frozenset({EDIT, DOCUMENT, COMBO_BOX})
# Control types that are clearly not a text field: with one of these focused the text is left
# on the clipboard. Anything else (a pane, a window, a group, a custom control) may be a
# text editor UI Automation cannot see into, so we paste.
NOT_TEXT_TYPES = frozenset({
    BUTTON, CALENDAR, CHECK_BOX, HYPERLINK, IMAGE, LIST_ITEM, LIST, MENU, MENU_BAR, MENU_ITEM, PROGRESS_BAR,
    RADIO_BUTTON, SCROLL_BAR, SLIDER, STATUS_BAR, TAB, TAB_ITEM, TEXT, TOOL_BAR, TOOL_TIP, TREE, TREE_ITEM, THUMB,
    SPLIT_BUTTON, HEADER, HEADER_ITEM, TITLE_BAR, SEPARATOR,
})


def text_target() -> str:
    """"text" when the focused element in the front app takes typed text, "maybe" when we
    cannot tell (then we paste anyway), "none" when it clearly does not (a list, a button,
    the desktop)."""
    try:
        if capture._console_in_front():
            return "maybe"  # Ctrl+V pastes in Windows Terminal and current console hosts
        return capture.ui_automation(_classify, TARGET_TIMEOUT)
    except PermissionError:
        return "maybe"  # our own window is in front: never inspected, and Ctrl+V works in our fields
    except Exception as e:
        log.debug("text field question failed: %s", type(e).__name__)
        return "maybe"


def _takes_text(UIA, element) -> bool:
    """Does this element take typed text: a Value pattern that is not read-only, or a Text
    pattern on an editable control type? A read-only Value (a web page's address) is never text."""
    unknown = element.GetCurrentPattern(UIA.UIA_ValuePatternId)
    if unknown:
        value = unknown.QueryInterface(UIA.IUIAutomationValuePattern)
        return not bool(value.CurrentIsReadOnly)
    return bool(element.GetCurrentPattern(UIA.UIA_TextPatternId)) and int(element.CurrentControlType) in EDITABLE_TYPES


def _classify(UIA, automation) -> str:
    """Runs on the UI Automation thread. The focused element, then its near ancestors."""
    focused = automation.GetFocusedElement()
    if not focused:
        return "maybe"  # UI Automation gives no focused element: common in custom-drawn apps
    if int(focused.CurrentProcessId) == os.getpid():
        return "maybe"  # focus moved to our own window after the gate: never inspected
    if automation.CompareElements(focused, automation.GetRootElement()):
        return "none"  # the desktop itself: nothing has keyboard focus
    if _takes_text(UIA, focused):
        return "text"
    if int(focused.CurrentControlType) in NOT_TEXT_TYPES:
        return "none"
    walker = automation.ControlViewWalker
    element = focused
    for _ in range(_ANCESTOR_LEVELS):
        element = walker.GetParentElement(element)
        if not element:
            break
        if _takes_text(UIA, element):
            return "text"
    return "maybe"


def put_text(text: str, transient: bool) -> int:
    """Replace the clipboard with `text`; returns the settled clipboard sequence number, the
    token restore_unless_changed() compares against. The changes are noted as Lyssna's own, so
    they never pass for a selection the user copied. Raises OSError when the clipboard could
    not be written, so the old clipboard is never pasted in place of the dictation."""
    before = int(clipboard.sequence())
    if not clipboard.set_text(text, transient=transient):
        raise OSError("the clipboard could not be written")
    token = capture._settled_sequence(int(clipboard.sequence()))
    capture.note_own_changes(before, token)
    return token


def snapshot() -> list | None:
    """Every format on the clipboard that can be saved, to put back after the paste. None when
    the clipboard could not be opened: then nothing is put back."""
    saved = clipboard.snapshot()
    if saved is None:
        log.info("clipboard could not be saved; it will hold the dictation after the paste")
    return saved


def restore_unless_changed(saved: list | None, token: int) -> bool:
    """Put the saved clipboard back, unless something else changed the clipboard since our
    write (or there is nothing saved)."""
    if saved is None:
        return False
    if int(clipboard.sequence()) != token:
        log.info("clipboard changed after the paste; not restoring it")
        return False
    capture.restore_pasteboard(None, saved)
    return True


def paste() -> None:
    """Press Ctrl+V in the front app, once the user has let go of the modifier keys (Ctrl and
    the Windows key are still down right after the dictation key)."""
    capture._wait_for_modifiers_release()
    capture.send_key(KEY_V, hotkeys.CTRL)
