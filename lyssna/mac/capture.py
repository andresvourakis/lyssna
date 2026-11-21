"""Grab the text the user has selected in whatever app is in front.

Strategy: ask the Accessibility API for the focused element's selected text first; that
touches no clipboard and works in most native apps and browsers. Only where an app doesn't
expose it do we send Cmd+C, watch the pasteboard change counter, read the new string, then
quietly put the previous clipboard back: every item with every type (rich text, images,
files), or an empty clipboard if it was empty. Last resort is whatever is on the clipboard.

Every clipboard change Lyssna itself causes (the Cmd+C here and the restore after it, the
paste of a dictation) is noted by its pasteboard change count, so the selection watcher can
tell a change the user's app made (a terminal that copies on select) from one of ours.

Also here: the probes the selection watcher uses (the selection with its range and bounds,
clipboard changes), and the request that makes a silent app report its selection."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict, deque

import Quartz
from AppKit import NSApp, NSPasteboard, NSPasteboardItem, NSPasteboardTypeString, NSRunningApplication, NSWorkspace

from ..hotkeys import MODIFIER_MASK
from ..selection import AXSelection

log = logging.getLogger("lyssna.mac.capture")

_KEY_C = 8


_AX_ERROR_NO_VALUE = -25212  # kAXErrorNoValue: the attribute is supported but nothing is selected
_AX_ERROR_ATTRIBUTE_UNSUPPORTED = -25205  # kAXErrorAttributeUnsupported


# The rule: never make an Accessibility client call that can land in our own process from a
# background thread. Accessibility answers questions about our own app in-process, on the
# calling thread, and AppKit's accessibility code is not thread-safe: a worker-thread call
# racing the main thread (a click on our status item, say) crashes the app. Every AX read
# below goes through these checks; hopping to the main thread instead would stall the UI.


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def _own_app_in_front() -> bool:
    """Is our app frontmost or active? Then the system-wide focused element is ours."""
    own = os.getpid()
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is not None and int(app.processIdentifier()) == own:
            return True
    except Exception:
        log.debug("front app lookup failed", exc_info=True)
        return True  # can't tell: assume the unsafe case
    try:
        ns_app = NSApp()
        return ns_app is not None and bool(ns_app.isActive())
    except Exception:
        log.debug("active app lookup failed", exc_info=True)
        return True


def _focus_unsafe() -> bool:
    """True when reading the system-wide focused element from here could reach our own app."""
    return not _on_main_thread() and _own_app_in_front()


def _own_element(element) -> bool:
    """True when `element` belongs to our process and we are off the main thread: reading
    any attribute of it would run AppKit accessibility code here. AXUIElementGetPid does not
    call into AppKit, so this check itself is safe."""
    if _on_main_thread():
        return False
    pid = ax_pid(element)
    return pid is None or pid == os.getpid()


def _focused_element():
    """The system-wide focused UI element (in whatever app is in front), or None. None too
    when, off the main thread, the focus is (or may be) in our own app."""
    from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCreateSystemWide, kAXFocusedUIElementAttribute

    if _focus_unsafe():
        return None
    err, focused = AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(), kAXFocusedUIElementAttribute, None)
    if err or focused is None or _own_element(focused):
        return None  # focus may have moved to our app between the check and the call
    return focused


def focused_element():
    """The focused UI element in the front app, or None when Accessibility gives none."""
    try:
        return _focused_element()
    except Exception:
        log.debug("AX focused element lookup failed", exc_info=True)
        return None


def ax_attribute(element, name: str):
    """One Accessibility attribute of an element, or None if it is missing or the call fails."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue

        if _own_element(element):
            return None
        err, value = AXUIElementCopyAttributeValue(element, name, None)
        return None if err else value
    except Exception:
        log.debug("AX attribute read failed", exc_info=True)
        return None


def ax_settable(element, name: str) -> bool:
    """Can this attribute be written (for AXValue: is the element editable)?"""
    try:
        from ApplicationServices import AXUIElementIsAttributeSettable

        if _own_element(element):
            return False
        err, settable = AXUIElementIsAttributeSettable(element, name, None)
        return not err and bool(settable)
    except Exception:
        log.debug("AX settable check failed", exc_info=True)
        return False


def ax_role(element) -> str | None:
    role = ax_attribute(element, "AXRole")
    return str(role) if role is not None else None


def ax_text_value(element) -> str | None:
    """The element's value when it is text (a text field's content), else None."""
    value = ax_attribute(element, "AXValue")
    return str(value) if isinstance(value, str) else None


def ax_pid(element) -> int | None:
    try:
        from ApplicationServices import AXUIElementGetPid

        err, pid = AXUIElementGetPid(element, None)
        return int(pid) if not err and pid else None
    except Exception:
        log.debug("AX pid lookup failed", exc_info=True)
        return None


def ax_set_timeout(element, seconds: float) -> None:
    """Bound how long Accessibility calls on this element may block (a busy app answers slowly)."""
    try:
        from ApplicationServices import AXUIElementSetMessagingTimeout

        AXUIElementSetMessagingTimeout(element, seconds)
    except Exception:
        log.debug("AX timeout not set", exc_info=True)


def _rect(value) -> tuple[float, float, float, float] | None:
    """An AXValue holding a CGRect as (x, y, w, h), Quartz top-left origin; None if empty."""
    from ApplicationServices import AXValueGetValue, kAXValueTypeCGRect

    ok, rect = AXValueGetValue(value, kAXValueTypeCGRect, None)
    if not ok or rect.size.width <= 0 or rect.size.height <= 0:
        return None
    return (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)


def _plain_selection(element) -> AXSelection | None:
    """The plain route (AXSelectedText, AXSelectedTextRange, AXBoundsForRange) that native
    text views and text fields answer. None when the element can't answer it."""
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCopyParameterizedAttributeValue,
        AXValueGetValue,
        kAXBoundsForRangeParameterizedAttribute,
        kAXSelectedTextAttribute,
        kAXSelectedTextRangeAttribute,
        kAXValueTypeCFRange,
    )

    err, text = AXUIElementCopyAttributeValue(element, kAXSelectedTextAttribute, None)
    if err == _AX_ERROR_NO_VALUE:
        text = ""
    elif err or not isinstance(text, str):
        return None
    text = str(text) if text.strip() else ""
    rng = bounds = None
    err, value = AXUIElementCopyAttributeValue(element, kAXSelectedTextRangeAttribute, None)
    if not err and value is not None:
        ok, cf_range = AXValueGetValue(value, kAXValueTypeCFRange, None)
        if ok:
            location, length = tuple(cf_range)[:2]
            rng = ("plain", int(location), int(length))
        if text:
            err, rect = AXUIElementCopyParameterizedAttributeValue(element, kAXBoundsForRangeParameterizedAttribute, value, None)
            if not err and rect is not None:
                bounds = _rect(rect)
    return AXSelection(True, text, rng, bounds)


def _marker_identity(markers) -> tuple[str, bytes, bytes] | None:
    """Something comparable for a text marker range: the bytes of its start and end markers.
    A marker is the app's own opaque record of a position (in Chrome and WebKit: which node,
    which offset), so the same selection read twice gives the same bytes and a different one
    gives different bytes. None when the bytes can't be read; then the text alone tells two
    readings apart."""
    try:
        from ApplicationServices import (
            AXTextMarkerGetBytePtr,
            AXTextMarkerGetLength,
            AXTextMarkerRangeCopyEndMarker,
            AXTextMarkerRangeCopyStartMarker,
            AXTextMarkerRangeGetTypeID,
        )
        from CoreFoundation import CFGetTypeID

        if CFGetTypeID(markers) != AXTextMarkerRangeGetTypeID():
            return None
        parts = []
        for marker in (AXTextMarkerRangeCopyStartMarker(markers), AXTextMarkerRangeCopyEndMarker(markers)):
            length = int(AXTextMarkerGetLength(marker))
            parts.append(bytes(AXTextMarkerGetBytePtr(marker)[:length]) if length > 0 else b"")
        if not any(parts):
            return None
        return ("marker", parts[0], parts[1])
    except Exception:
        log.debug("text marker bytes unavailable", exc_info=True)
        return None


def _marker_selection(element) -> AXSelection | None:
    """The text-marker route (AXSelectedTextMarkerRange, AXStringForTextMarkerRange,
    AXBoundsForTextMarkerRange) that web content answers: Chrome, Safari and Electron apps
    often give no AXSelectedText on a web area. None when the element can't answer it."""
    from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCopyParameterizedAttributeValue

    err, markers = AXUIElementCopyAttributeValue(element, "AXSelectedTextMarkerRange", None)
    if err or markers is None:
        return None
    err, text = AXUIElementCopyParameterizedAttributeValue(element, "AXStringForTextMarkerRange", markers, None)
    text = str(text) if not err and isinstance(text, str) and text.strip() else ""
    bounds = None
    if text:
        err, rect = AXUIElementCopyParameterizedAttributeValue(element, "AXBoundsForTextMarkerRange", markers, None)
        if not err and rect is not None:
            bounds = _rect(rect)
    return AXSelection(True, text, _marker_identity(markers), bounds)


def _read_selection(element) -> AXSelection:
    """One element's selection: the plain route first, the text-marker route when the plain
    one has no text. The caller has made sure the element is not ours (see the rule above)."""
    plain = _plain_selection(element)
    if plain is not None and plain.text:
        return plain
    marker = _marker_selection(element)
    if marker is not None and (marker.text or plain is None):
        return marker
    return plain or AXSelection(False, "")


def ax_selection() -> AXSelection:
    """The focused element's selection, with its range and screen bounds, telling "nothing
    selected" apart from "this app can't say" (no focused element, or neither route answers)."""
    try:
        focused = _focused_element()
        if focused is None:
            return AXSelection(False, "")
        selection = _read_selection(focused)
        if selection.supported:
            _note_answered(ax_pid(focused))
        return selection
    except Exception:
        log.debug("AX selection lookup failed", exc_info=True)
        return AXSelection(False, "")


def ax_selected_text() -> str | None:
    """Ask the Accessibility API for the focused element's selection. Works in most native
    apps and browsers, returns None where the app doesn't expose it (then we fall back to ⌘C)."""
    return ax_selection().text or None


def front_app_pid() -> int | None:
    """Process id of the frontmost application, or None."""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else None
    except Exception:
        log.debug("front app lookup failed", exc_info=True)
        return None


# Chrome and Electron apps keep their accessibility tree off until an assistive client asks
# for it: until then they report no focused element and so no selection. One rule for every
# app: an app with no focused element is asked once per process. AXManualAccessibility comes first
# because AXEnhancedUserInterface, the older switch, also changes how some apps animate window
# moves; it is only tried when the first is unsupported. Keyed on pid plus launch date, so a
# reused pid is asked again, and bounded, so the record stays small.
_asked: OrderedDict[tuple[int, float], None] = OrderedDict()
_warming: dict[int, float] = {}  # pid -> when it was asked, until it first answers
_asked_lock = threading.Lock()
_ASKED_MAX = 64
_WAKE_TIMEOUT = 1.0  # seconds a hung app may hold a request


def _launch_key(pid: int) -> tuple[int, float]:
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        date = app.launchDate() if app is not None else None
        return (pid, float(date.timeIntervalSince1970()) if date is not None else 0.0)
    except Exception:
        log.debug("launch date lookup failed", exc_info=True)
        return (pid, 0.0)


def _app_answers(app) -> bool:
    """Does the app report a focused element at all? That is the sign its accessibility is on.
    Whether that element holds text is beside the point: Finder's file list can't report a
    selection either, and asking it would switch AXEnhancedUserInterface on in nearly every
    native app for nothing."""
    from ApplicationServices import AXUIElementCopyAttributeValue, kAXFocusedUIElementAttribute

    err, focused = AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute, None)
    return not err and focused is not None


def _note_answered(pid: int | None) -> None:
    """Log how long an app we asked took to start answering, once."""
    if pid is None:
        return
    with _asked_lock:
        asked_at = _warming.pop(pid, None)
    if asked_at is not None:
        log.info("pid %d answers Accessibility %.2f s after it was asked", pid, time.monotonic() - asked_at)


def wake_accessibility(pid: int | None) -> None:
    """Ask app `pid` to turn its accessibility on, if it can't answer and was never asked.
    Never our own process. Called on the main thread it hands itself to a worker thread: a
    slow app must not stall the UI or the mouse tap."""
    if not pid or pid == os.getpid():
        return
    if _on_main_thread():
        threading.Thread(target=wake_accessibility, args=(pid,), daemon=True, name="lyssna-wake").start()
        return
    key = _launch_key(pid)
    with _asked_lock:
        if key in _asked:
            return
    try:
        from ApplicationServices import AXUIElementCreateApplication, AXUIElementSetAttributeValue

        app = AXUIElementCreateApplication(pid)
        ax_set_timeout(app, _WAKE_TIMEOUT)
        if _app_answers(app):
            return  # not recorded: if it falls silent later (a web view gets focus), ask then
        with _asked_lock:
            if key in _asked:
                return
            _asked[key] = None
            while len(_asked) > _ASKED_MAX:
                (old_pid, _), _ = _asked.popitem(last=False)
                _warming.pop(old_pid, None)
            _warming[pid] = time.monotonic()
        attribute = "AXManualAccessibility"
        err = AXUIElementSetAttributeValue(app, attribute, True)
        if err == _AX_ERROR_ATTRIBUTE_UNSUPPORTED:
            attribute = "AXEnhancedUserInterface"
            # Its error code is only logged: Chrome answers -25208 yet turns it on.
            err = AXUIElementSetAttributeValue(app, attribute, True)
        log.info("asked pid %d to turn on Accessibility: %s, error %d", pid, attribute, err)
    except Exception:
        log.debug("accessibility request failed", exc_info=True)


def clipboard_text() -> str:
    pb = NSPasteboard.generalPasteboard()
    return pb.stringForType_(NSPasteboardTypeString) or ""


# Change counts of the general pasteboard that Lyssna produced. A deque, not a set, so it
# stays small; a few dozen covers every write still inside any selection check's window.
_own_counts: deque[int] = deque(maxlen=64)
_own_lock = threading.Lock()


def note_own_changes(before: int, after: int) -> None:
    """Every pasteboard change count after `before` up to `after` is ours. Called around each
    clipboard write Lyssna makes, and around the Cmd+C it sends (the copy the app makes then
    is ours too). A change the user makes in between is counted as ours: a rare miss."""
    with _own_lock:
        _own_counts.extend(range(int(before) + 1, int(after) + 1))


def own_change(count: int) -> bool:
    """Did Lyssna produce this pasteboard change count?"""
    with _own_lock:
        return int(count) in _own_counts


def clipboard_change_count() -> int | None:
    """The general pasteboard's change count (cheap, any thread), or None if it can't be read."""
    try:
        return int(NSPasteboard.generalPasteboard().changeCount())
    except Exception:
        log.debug("pasteboard change count unavailable", exc_info=True)
        return None


def copied_since(count: int | None) -> str:
    """The clipboard's text when someone other than Lyssna changed the clipboard after change
    count `count`; "" when it did not change, Lyssna changed it, or it holds no text."""
    if count is None:
        return ""
    try:
        pb = NSPasteboard.generalPasteboard()
        now = int(pb.changeCount())
        if now == count or own_change(now):
            return ""
        return pb.stringForType_(NSPasteboardTypeString) or ""
    except Exception:
        log.debug("pasteboard read failed", exc_info=True)
        return ""


def _wait_for_modifiers_release(timeout: float = 1.5) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState)
        if not (flags & MODIFIER_MASK):
            return
        time.sleep(0.02)


def send_key(keycode: int, flags: int = 0) -> None:
    """Post one synthetic key press and release (e.g. V with Cmd) to the front app."""
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
    down = Quartz.CGEventCreateKeyboardEvent(src, keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(src, keycode, False)
    Quartz.CGEventSetFlags(down, flags)
    Quartz.CGEventSetFlags(up, flags)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def _send_copy() -> None:
    send_key(_KEY_C, Quartz.kCGEventFlagMaskCommand)


def snapshot_pasteboard(pb) -> list[dict[str, bytes]]:
    """Every item on the pasteboard, as {type: data} with every type it offers. An empty
    list means an empty clipboard."""
    items = []
    for item in pb.pasteboardItems() or []:
        data = {}
        for kind in item.types() or []:
            value = item.dataForType_(kind)
            if value is not None:
                data[str(kind)] = bytes(value)
        items.append(data)
    return items


def _new_item():
    return NSPasteboardItem.alloc().init()


def restore_pasteboard(pb, items: list[dict[str, bytes]], new_item=_new_item) -> None:
    """Put back what snapshot_pasteboard() saw. An empty snapshot leaves the clipboard empty."""
    before = pb.changeCount()
    pb.clearContents()
    restored = []
    for data in items:
        if not data:
            continue
        item = new_item()
        for kind, value in data.items():
            item.setData_forType_(value, kind)
        restored.append(item)
    if restored:
        pb.writeObjects_(restored)
    note_own_changes(before, pb.changeCount())


def _restore_later(previous: list[dict[str, bytes]], delay: float = 0.4) -> None:
    def _restore() -> None:
        time.sleep(delay)
        restore_pasteboard(NSPasteboard.generalPasteboard(), previous)

    threading.Thread(target=_restore, daemon=True).start()


def selected_text(timeout: float = 0.8, restore_clipboard: bool = True) -> str | None:
    """Return the current selection, or None if nothing was copied."""
    pb = NSPasteboard.generalPasteboard()
    before_count = pb.changeCount()
    before = snapshot_pasteboard(pb) if restore_clipboard else []
    _wait_for_modifiers_release()
    _send_copy()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pb.changeCount() != before_count:
            time.sleep(0.05)  # give the source app a moment to finish writing all types
            note_own_changes(before_count, pb.changeCount())  # the app copied because we pressed Cmd+C
            text = pb.stringForType_(NSPasteboardTypeString) or ""
            if restore_clipboard:
                _restore_later(before)
            return text if text.strip() else None
        time.sleep(0.03)
    return None


def selection_or_clipboard() -> tuple[str, str]:
    """Returns (text, source) where source is 'selection' or 'clipboard'."""
    text = ax_selected_text()
    if text:
        return text, "selection (accessibility)"
    text = selected_text()
    if text:
        return text, "selection (copy)"
    return clipboard_text(), "clipboard"
