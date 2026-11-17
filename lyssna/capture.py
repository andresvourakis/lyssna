"""Grab the text the user has selected in whatever app is in front.

Strategy: ask the Accessibility API for the focused element's selected text first; that
touches no clipboard and works in most native apps and browsers. Only where an app doesn't
expose it do we send Cmd+C, watch the pasteboard change counter, read the new string, then
quietly put the previous clipboard back: every item with every type (rich text, images,
files), or an empty clipboard if it was empty. Last resort is whatever is on the clipboard.

Also here: the probes the selection watcher uses (cursor shape, window owner under the
mouse, frontmost app, selection bounds)."""

from __future__ import annotations

import logging
import os
import threading
import time

import Quartz
from AppKit import NSApp, NSCursor, NSPasteboard, NSPasteboardItem, NSPasteboardTypeString, NSWorkspace

from .hotkeys import MODIFIER_MASK
from .selection import AXSelection

log = logging.getLogger("lyssna.capture")

_KEY_C = 8


_AX_ERROR_NO_VALUE = -25212  # kAXErrorNoValue: the attribute is supported but nothing is selected


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


def ax_selection() -> AXSelection:
    """The focused element's selection, telling "nothing selected" apart from "this app
    can't say" (no focused element, or the attribute isn't exposed)."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue, kAXSelectedTextAttribute

        focused = _focused_element()
        if focused is None:
            return AXSelection(False, "")
        err, text = AXUIElementCopyAttributeValue(focused, kAXSelectedTextAttribute, None)
        if err == _AX_ERROR_NO_VALUE:
            return AXSelection(True, "")
        if err or not isinstance(text, str):
            return AXSelection(False, "")
        return AXSelection(True, str(text) if text.strip() else "")
    except Exception:
        log.debug("AX selection lookup failed", exc_info=True)
        return AXSelection(False, "")


def ax_selected_text() -> str | None:
    """Ask the Accessibility API for the focused element's selection. Works in most native
    apps and browsers, returns None where the app doesn't expose it (then we fall back to ⌘C)."""
    return ax_selection().text or None


# Standard cursors that never mean "text here". Selectors missing on this macOS are skipped.
_NON_TEXT_CURSORS = (
    "arrowCursor", "crosshairCursor", "pointingHandCursor", "openHandCursor", "closedHandCursor",
    "resizeLeftCursor", "resizeRightCursor", "resizeLeftRightCursor",
    "resizeUpCursor", "resizeDownCursor", "resizeUpDownCursor",
    "operationNotAllowedCursor", "dragCopyCursor", "dragLinkCursor", "contextualMenuCursor",
    "disappearingItemCursor", "columnResizeCursor", "rowResizeCursor", "zoomInCursor", "zoomOutCursor",
)

_non_text_refs: dict[tuple[float, float, float, float], list[bytes]] | None = None


def _cursor_key(cursor) -> tuple[float, float, float, float]:
    hot, size = cursor.hotSpot(), cursor.image().size()
    return (hot.x, hot.y, size.width, size.height)


def _non_text_references() -> dict[tuple[float, float, float, float], list[bytes]]:
    """TIFF bytes of the standard non-text cursors, keyed by hotspot + size, built once.
    Needs NSApplication to exist (the app has it)."""
    global _non_text_refs
    if _non_text_refs is None:
        refs: dict[tuple[float, float, float, float], list[bytes]] = {}
        for name in _NON_TEXT_CURSORS:
            try:
                c = getattr(NSCursor, name)()
                refs.setdefault(_cursor_key(c), []).append(bytes(c.image().TIFFRepresentation()))
            except Exception:
                log.debug("cursor reference %s unavailable", name)
        _non_text_refs = refs
    return _non_text_refs


def cursor_is_standard_non_text() -> bool:
    """True when the on-screen cursor (in whatever app is under it) is exactly one of the
    standard non-text NSCursors (arrow, hand, resize, crosshair...). Custom cursors, I-beams
    included, give False, as does any error. Main thread only (NSCursor needs it)."""
    try:
        cur = NSCursor.currentSystemCursor()
        if cur is None or cur.image() is None:
            return False
        # Cheap hotspot+size filter first; only a candidate pays for the TIFF comparison.
        candidates = _non_text_references().get(_cursor_key(cur))
        return bool(candidates) and bytes(cur.image().TIFFRepresentation()) in candidates
    except Exception:
        log.debug("cursor probe failed", exc_info=True)
        return False


def front_app_pid() -> int | None:
    """Process id of the frontmost application, or None."""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else None
    except Exception:
        log.debug("front app lookup failed", exc_info=True)
        return None


def _on_screen_windows() -> list:
    """Every on-screen window, front to back (CGWindowListCopyWindowInfo is thread-safe)."""
    return Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    ) or []


def _topmost_window_pid_at(windows, x: float, y: float, skip_own: bool) -> int | None:
    own = os.getpid()
    for w in windows:  # front to back
        pid = w.get(Quartz.kCGWindowOwnerPID)
        if pid is None or (skip_own and pid == own):
            continue
        if w.get(Quartz.kCGWindowLayer, 0) < 0 or w.get(Quartz.kCGWindowAlpha, 1.0) <= 0:
            continue
        b = w.get(Quartz.kCGWindowBounds) or {}
        bx, by = b.get("X", 0), b.get("Y", 0)
        if bx <= x < bx + b.get("Width", 0) and by <= y < by + b.get("Height", 0):
            return int(pid)
    return None


def window_owner_pid_at(x: float, y: float) -> int | None:
    """Process id owning what's on screen at (x, y), Quartz coordinates (top-left origin).
    When the topmost window there is ours (status item, menu, panels, windows) the answer is
    our pid, without asking Accessibility (see the rule above). Otherwise asks Accessibility,
    then falls back to the topmost visible window containing the point (ignoring our own).
    During a ⌘⇧4 drag this is the Screenshot process."""
    own = os.getpid()
    try:
        windows = _on_screen_windows()
    except Exception:
        log.debug("window list failed", exc_info=True)
        return None  # unknown owner; skip the hit test, which might land in our own app
    if _topmost_window_pid_at(windows, x, y, skip_own=False) == own:
        return own
    try:
        from ApplicationServices import (
            AXUIElementCopyElementAtPosition,
            AXUIElementCreateSystemWide,
            AXUIElementGetPid,
        )

        err, element = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), x, y, None)
        if not err and element is not None:
            err, pid = AXUIElementGetPid(element, None)
            if not err and pid:
                return int(pid)
    except Exception:
        log.debug("AX hit-test failed", exc_info=True)
    try:
        return _topmost_window_pid_at(windows, x, y, skip_own=True)
    except Exception:
        log.debug("window list hit-test failed", exc_info=True)
    return None


def ax_selection_bounds() -> tuple[float, float, float, float] | None:
    """Screen rectangle (x, y, w, h; Quartz top-left origin) of the focused element's
    selected text, or None if the app doesn't expose it."""
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCopyParameterizedAttributeValue,
            AXValueGetValue,
            kAXBoundsForRangeParameterizedAttribute,
            kAXSelectedTextRangeAttribute,
            kAXValueTypeCGRect,
        )

        focused = _focused_element()
        if focused is None:
            return None
        err, rng = AXUIElementCopyAttributeValue(focused, kAXSelectedTextRangeAttribute, None)
        if err or rng is None:
            return None
        err, bounds = AXUIElementCopyParameterizedAttributeValue(focused, kAXBoundsForRangeParameterizedAttribute, rng, None)
        if err or bounds is None:
            return None
        ok, rect = AXValueGetValue(bounds, kAXValueTypeCGRect, None)
        if not ok or rect.size.width <= 0 or rect.size.height <= 0:
            return None
        return (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)
    except Exception:
        log.debug("AX selection bounds lookup failed", exc_info=True)
        return None


def clipboard_text() -> str:
    pb = NSPasteboard.generalPasteboard()
    return pb.stringForType_(NSPasteboardTypeString) or ""


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
