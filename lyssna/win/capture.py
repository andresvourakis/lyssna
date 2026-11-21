"""Grab the text the user has selected in whatever app is in front, on Windows.

Same public functions and the same order of attempts as lyssna/mac/capture.py, so the controller
and the selection watcher do not care which system they run on:

1. UI Automation. The focused element, or the nearest ancestor that offers the Text pattern,
   reports its selection, with a screen rectangle. This touches no clipboard and works in
   browsers, Office, Notepad, Windows Terminal and most modern apps.
2. Ctrl+C. Where UI Automation has nothing, we save the clipboard, send Ctrl+C with SendInput,
   wait for the clipboard sequence number to move, read the text, and put the saved clipboard
   back a moment later, but only if nothing else changed it in between. Never in a console
   window: there Ctrl+C with nothing selected stops the running command.
3. Whatever is on the clipboard.

Every clipboard change Lyssna causes is noted by its sequence number, so the selection watcher
can tell a copy the user's app made (a terminal that copies on select) from one of ours.

The Win32 and COM calls live in small private functions (`_foreground_window`, `_window_class`,
`_uia_selection`, `_send_inputs`, `_key_down`...) and the clipboard in lyssna/win/clipboard.py,
so the decisions here can be tested on any system with fakes. Nothing Windows-only is imported
when this module loads.

Privacy: logs record lengths, sources and timings, never the selected text."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from functools import partial

from ..selection import AXSelection, Bounds
from . import clipboard, hotkeys

log = logging.getLogger("lyssna.win.capture")

VK_C = 0x43
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
_MODIFIER_KEYS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
# Our modifier flags (lyssna.win.hotkeys) to the key pressed for each, in the order they go down.
_FLAG_KEYS = ((hotkeys.CTRL, VK_CONTROL), (hotkeys.ALT, VK_MENU), (hotkeys.SHIFT, VK_SHIFT), (hotkeys.WIN, VK_LWIN))
# Keys that need KEYEVENTF_EXTENDEDKEY: navigation keys, the Windows keys, right Ctrl and right Alt.
_EXTENDED_KEYS = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, VK_LWIN, VK_RWIN, 0xA3, 0xA5})
# Marks the key events Lyssna sends (dwExtraInfo), so our own keyboard hook can recognise them.
INJECTED_MARK = 0x4C59534E

# Foreground window classes of console and terminal windows. Ctrl+C there interrupts the
# running program when nothing is selected, so the copy route is never tried in them.
CONSOLE_CLASSES = frozenset({
    "ConsoleWindowClass",  # the classic console (cmd, PowerShell in conhost)
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
    "PseudoConsoleWindow",
    "mintty",  # Git Bash, Cygwin, MSYS2
    "VirtualConsoleClass",  # ConEmu, Cmder
    "org.wezfurlong.wezterm",
    "Alacritty",
})

_ANCESTOR_LEVELS = 4  # how far up from the focused element to look for the Text pattern
_UIA_TIMEOUT_MS = 1000  # UI Automation connection and transaction timeouts: a hung app gives up here
_CALL_TIMEOUT = 2.5  # seconds a caller waits for the UI Automation thread before saying "not supported"
_RESTORE_DELAY = 0.4  # seconds before the saved clipboard goes back; slow apps read the copy lazily
_SETTLE = 0.05  # the clipboard counts as written once its sequence number is still for this long
_SETTLE_MAX = 0.3  # but never wait longer than this for an app to finish writing every format


# The rule: never read our own windows through UI Automation. A UI Automation client call
# about our own process is answered by our own UI thread; from a worker thread that the UI
# thread may be waiting on, that deadlocks, and the Mac version crashed on the same pattern.
# So if the foreground window is ours, or we cannot tell, the accessibility route says
# "not supported".


_user32_dll = None


def _user32():
    """user32 with the argument types this module needs; our own WinDLL instance, so other
    modules declaring the same functions differently never clash with it."""
    global _user32_dll
    if _user32_dll is None:
        import ctypes
        from ctypes import wintypes

        dll = ctypes.WinDLL("user32", use_last_error=True)
        dll.GetForegroundWindow.argtypes, dll.GetForegroundWindow.restype = [], wintypes.HWND
        dll.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        dll.GetClassNameW.restype = ctypes.c_int
        dll.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        dll.GetWindowThreadProcessId.restype = wintypes.DWORD
        dll.GetAsyncKeyState.argtypes, dll.GetAsyncKeyState.restype = [ctypes.c_int], ctypes.c_short
        dll.MapVirtualKeyW.argtypes, dll.MapVirtualKeyW.restype = [wintypes.UINT, wintypes.UINT], wintypes.UINT
        dll.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        dll.SendInput.restype = wintypes.UINT
        _user32_dll = dll
    return _user32_dll


def _foreground_window() -> int | None:
    try:
        return _user32().GetForegroundWindow() or None
    except Exception:
        log.debug("foreground window lookup failed", exc_info=True)
        return None


def _window_class(hwnd: int) -> str:
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(256)
        n = _user32().GetClassNameW(hwnd, buf, len(buf))
        return buf.value[:n] if n > 0 else ""
    except Exception:
        log.debug("window class lookup failed", exc_info=True)
        return ""


def _window_pid(hwnd: int | None) -> int | None:
    if not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        pid = wintypes.DWORD(0)
        _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None
    except Exception:
        log.debug("window pid lookup failed", exc_info=True)
        return None


def _key_down(vk: int) -> bool:
    """Is this key held right now (GetAsyncKeyState high bit)?"""
    try:
        return bool(_user32().GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        log.debug("key state lookup failed", exc_info=True)
        return False


def _own_window_in_front() -> bool:
    """Is the foreground window ours? True too when there is none or its owner is unknown."""
    hwnd = _foreground_window()
    if not hwnd:
        return True
    pid = _window_pid(hwnd)
    return pid is None or pid == os.getpid()


def _console_in_front() -> bool:
    """Is the foreground window a console or terminal? True too when there is no foreground
    window: a Ctrl+C with no known target is not worth the risk."""
    hwnd = _foreground_window()
    if not hwnd:
        return True
    return _window_class(hwnd) in CONSOLE_CLASSES


# UI Automation runs on one dedicated thread. COM must be initialised on every thread that
# calls it, and Lyssna's callers are short-lived worker threads (one per mouse gesture), so
# initialising COM and building an automation object on each of them would leak an apartment
# per thread and repeat the setup every time. Instead one daemon thread initialises COM once
# (single-threaded apartment, which is what comtypes sets up when it is imported; our calls are
# all synchronous and outgoing, so the thread needs no message loop), keeps the one automation
# object, and runs every request in turn. COM pointers never leave that thread: a request
# returns a plain AXSelection. A caller waits at most _CALL_TIMEOUT; while a request is stuck
# past that, new ones are refused at once instead of queueing behind it.


class _ComThread:
    def __init__(self, init: Callable[[], None], timeout: float = _CALL_TIMEOUT):
        self._init = init
        self._timeout = timeout
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._busy_since: float | None = None

    def call(self, fn: Callable[[], object]):
        """Run `fn` on the COM thread and return its result. Raises TimeoutError when the
        thread does not answer in time, and whatever `fn` raised."""
        with self._lock:
            busy = self._busy_since
            if busy is not None and time.monotonic() - busy > self._timeout:
                raise TimeoutError("UI Automation thread is still stuck on an earlier request")
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="lyssna-uia")
                self._thread.start()
        box: dict = {}
        done = threading.Event()
        self._jobs.put((fn, box, done))
        if not done.wait(self._timeout):
            raise TimeoutError("UI Automation did not answer in time")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _run(self) -> None:
        init_error: BaseException | None = None
        try:
            self._init()
        except Exception as e:
            log.info("UI Automation could not start: %s", type(e).__name__)
            init_error = e
        while True:
            fn, box, done = self._jobs.get()
            with self._lock:
                self._busy_since = time.monotonic()
            try:
                if init_error is not None:
                    raise init_error
                box["value"] = fn()
            except Exception as e:
                box["error"] = e
            finally:
                with self._lock:
                    self._busy_since = None
                done.set()


_uia: dict = {}  # touched only on the COM thread: "UIA" (the generated module) and "automation"


def _com_init() -> None:
    """Runs once on the COM thread: COM, then the automation object with its timeouts set."""
    import comtypes  # its first import initialises COM on the importing thread
    import comtypes.client

    try:
        comtypes.CoInitializeEx()  # a no-op if the import above already did it on this thread
    except OSError:
        log.debug("COM was already initialised on this thread in another mode", exc_info=True)
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA

    try:
        automation = comtypes.client.CreateObject(UIA.CUIAutomation8, interface=UIA.IUIAutomation)
    except Exception:
        log.debug("CUIAutomation8 unavailable; using CUIAutomation", exc_info=True)
        automation = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    try:
        # IUIAutomation2 (Windows 8 and later): bound how long a hung app may hold a call.
        automation2 = automation.QueryInterface(UIA.IUIAutomation2)
        automation2.ConnectionTimeout = _UIA_TIMEOUT_MS
        automation2.TransactionTimeout = _UIA_TIMEOUT_MS
    except Exception:
        log.info("UI Automation timeouts could not be set; a hung app may hold a read up to %.1f s", _CALL_TIMEOUT)
    _uia["UIA"], _uia["automation"] = UIA, automation


_com = _ComThread(_com_init)


def _rects(doubles) -> list[Bounds]:
    """GetBoundingRectangles' flat (left, top, width, height, ...) list as rectangles, empty ones dropped."""
    values = list(doubles or ())
    out = []
    for i in range(0, len(values) - 3, 4):
        x, y, w, h = (float(v) for v in values[i:i + 4])
        if w > 0 and h > 0:
            out.append((x, y, w, h))
    return out


def _union(rects: list[Bounds]) -> Bounds | None:
    """The smallest rectangle around every line of the selection."""
    if not rects:
        return None
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    return (left, top, right - left, bottom - top)


def _text_pattern(UIA, automation, element):
    """The Text pattern of the element or its nearest ancestor that has one, or None."""
    walker = automation.ControlViewWalker
    for _ in range(_ANCESTOR_LEVELS + 1):
        unknown = element.GetCurrentPattern(UIA.UIA_TextPatternId)
        if unknown:
            return unknown.QueryInterface(UIA.IUIAutomationTextPattern)
        element = walker.GetParentElement(element)
        if not element:
            return None
    return None


def _endpoints(UIA, pattern, first, last):
    """Where the selection starts and ends, measured against the start of the document, so the
    same selection read twice compares equal even after a scroll. None if the app can't say."""
    try:
        doc = pattern.DocumentRange
        start = first.CompareEndpoints(UIA.TextPatternRangeEndpoint_Start, doc, UIA.TextPatternRangeEndpoint_Start)
        end = last.CompareEndpoints(UIA.TextPatternRangeEndpoint_End, doc, UIA.TextPatternRangeEndpoint_Start)
        return ("uia", int(start), int(end))
    except Exception:
        log.debug("selection endpoints unavailable", exc_info=True)
        return None


def _uia_read() -> AXSelection:
    """Runs on the COM thread: the focused element's selection through the Text pattern."""
    UIA, automation = _uia["UIA"], _uia["automation"]
    focused = automation.GetFocusedElement()
    if not focused or int(focused.CurrentProcessId) == os.getpid():
        return AXSelection(False, "")
    pattern = _text_pattern(UIA, automation, focused)
    if pattern is None:
        return AXSelection(False, "")
    ranges = pattern.GetSelection()
    count = int(ranges.Length) if ranges else 0
    if count == 0:
        return AXSelection(True, "")
    parts, rects = [], []
    for i in range(count):
        item = ranges.GetElement(i)
        parts.append(item.GetText(-1) or "")
        rects.extend(_rects(item.GetBoundingRectangles()))
    text = "\n".join(p for p in parts if p)
    text = text if text.strip() else ""
    bounds = _union(rects) if text else None
    rng = _endpoints(UIA, pattern, ranges.GetElement(0), ranges.GetElement(count - 1)) or ("uia", bounds)
    return AXSelection(True, text, rng, bounds)


def _uia_selection() -> AXSelection:
    """The focused element's selection, read on the COM thread. Any COM error or timeout is
    "not supported"."""
    try:
        return _com.call(_uia_read)
    except Exception as e:
        log.debug("UI Automation selection read failed: %s", type(e).__name__)
        return AXSelection(False, "")


def ax_selection() -> AXSelection:
    """The focused element's selection, with its range and screen bounds (physical pixels, top
    left origin), telling "nothing selected" apart from "this app can't say"."""
    try:
        if _own_window_in_front():
            return AXSelection(False, "")
        return _uia_selection()
    except Exception:
        log.debug("UI Automation selection lookup failed", exc_info=True)
        return AXSelection(False, "")


def ax_selected_text() -> str | None:
    """The selection UI Automation reports, or None where the app doesn't expose one (then we
    fall back to Ctrl+C)."""
    return ax_selection().text or None


def front_app_pid() -> int | None:
    """Process id of the app that owns the foreground window, or None."""
    return _window_pid(_foreground_window())


def wake_accessibility(pid: int | None) -> None:
    """Nothing to do on Windows. On the Mac, Chrome and Electron keep their accessibility tree
    off until a client flips a switch on the app. On Windows those apps turn it on by
    themselves when a UI Automation client first asks them for their tree, so the first read
    is the request."""
    return None


def clipboard_text() -> str:
    return clipboard.get_text()


# Clipboard sequence numbers that Lyssna produced. A deque, not a set, so it stays small; a few
# dozen covers every write still inside any selection check's window.
_own_counts: deque[int] = deque(maxlen=64)
_own_lock = threading.Lock()


def note_own_changes(before: int, after: int) -> None:
    """Every clipboard sequence number after `before` up to `after` is ours. Called around each
    clipboard write Lyssna makes, and around the Ctrl+C it sends (the copy the app makes then
    is ours too). A change the user makes in between is counted as ours: a rare miss."""
    with _own_lock:
        _own_counts.extend(range(int(before) + 1, int(after) + 1))


def own_change(count: int) -> bool:
    """Did Lyssna produce this clipboard sequence number?"""
    with _own_lock:
        return int(count) in _own_counts


def clipboard_change_count() -> int | None:
    """The clipboard sequence number (cheap, any thread), or None if it can't be read."""
    try:
        return int(clipboard.sequence()) or None  # 0 means this process may not read the clipboard
    except Exception:
        log.debug("clipboard sequence number unavailable", exc_info=True)
        return None


def copied_since(count: int | None) -> str:
    """The clipboard's text when someone other than Lyssna changed the clipboard after sequence
    number `count`; "" when it did not change, Lyssna changed it, or it holds no text."""
    if count is None:
        return ""
    try:
        now = int(clipboard.sequence())
        if now == count or own_change(now):
            return ""
        return clipboard.get_text()
    except Exception:
        log.debug("clipboard read failed", exc_info=True)
        return ""


def _wait_for_modifiers_release(timeout: float = 1.5) -> None:
    """Wait until the user lets go of Ctrl, Alt, Shift and the Windows keys (they are still
    down right after a hotkey, and would turn our Ctrl+C into another shortcut)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_key_down(vk) for vk in _MODIFIER_KEYS):
            return
        time.sleep(0.02)
    log.debug("modifier keys still held after %.1f s", timeout)


def _key_sequence(keycode: int, flags: int) -> list[tuple[int, bool]]:
    """(virtual key, down) events for one press: each modifier down, the key down and up, then
    the modifiers up in reverse order."""
    mods = [vk for bit, vk in _FLAG_KEYS if flags & bit]
    return [(vk, True) for vk in mods] + [(keycode, True), (keycode, False)] + [(vk, False) for vk in reversed(mods)]


_input_types: tuple | None = None


def _input_struct():
    """The INPUT structure with its full union, so its size matches what SendInput expects on
    32 and 64 bit."""
    global _input_types
    if _input_types is None:
        import ctypes
        from ctypes import wintypes

        ulong_ptr = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

        _input_types = (ctypes, INPUT)
    return _input_types


def _send_inputs(events: list[tuple[int, bool]]) -> None:
    """Send key events with one SendInput call, so nothing the user types lands in between."""
    ctypes, INPUT = _input_struct()
    INPUT_KEYBOARD, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 1, 0x0001, 0x0002
    user32 = _user32()
    inputs = (INPUT * len(events))()
    for slot, (vk, down) in zip(inputs, events, strict=True):
        slot.type = INPUT_KEYBOARD
        slot.ki.wVk = vk
        slot.ki.wScan = user32.MapVirtualKeyW(vk, 0) & 0xFFFF  # some apps read the scan code, not the key
        slot.ki.dwFlags = (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_KEYS else 0) | (0 if down else KEYEVENTF_KEYUP)
        slot.ki.dwExtraInfo = INJECTED_MARK
    sent = user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT))
    if sent != len(events):
        log.info("SendInput sent %d of %d key events (error %d)", sent, len(events), ctypes.get_last_error())


def send_key(keycode: int, flags: int = 0) -> None:
    """Press and release one key (a virtual-key code) with the modifiers in `flags`
    (lyssna.win.hotkeys bits) held, in the app in front."""
    _send_inputs(_key_sequence(keycode, flags))


def _send_copy() -> None:
    send_key(VK_C, hotkeys.CTRL)


def snapshot_pasteboard(pb) -> list | None:
    """Every format on the clipboard that can be saved (see lyssna.win.clipboard.snapshot).
    `pb` is ignored: Windows has one clipboard, and the name and argument match the Mac's so
    shared callers work unchanged. None when the clipboard could not be opened."""
    return clipboard.snapshot()


def restore_pasteboard(pb, items: list | None, new_item=None) -> None:
    """Put back what snapshot_pasteboard() saved; [] leaves the clipboard empty, None does
    nothing. `pb` and `new_item` are ignored (Mac-only arguments). The change is noted as ours."""
    if items is None:
        return
    before = clipboard.sequence()
    clipboard.restore(items)
    note_own_changes(before, clipboard.sequence())


def _later(delay: float, fn: Callable[[], None]) -> None:
    def run() -> None:
        try:
            fn()
        except Exception:
            log.exception("restoring the clipboard failed")

    timer = threading.Timer(delay, run)
    timer.daemon = True
    timer.start()


def _restore_unless_changed(previous: list, ours: int) -> bool:
    """Put the saved clipboard back, unless something changed the clipboard since our copy."""
    if int(clipboard.sequence()) != ours:
        log.info("clipboard changed after the copy; not restoring it")
        return False
    restore_pasteboard(None, previous)
    return True


def _settled_sequence(first: int) -> int:
    """After the clipboard first changes, wait until the app has written every format (the
    sequence number moves once per format) and return the final number."""
    last, still_since = first, time.monotonic()
    deadline = still_since + _SETTLE_MAX
    while time.monotonic() < deadline:
        time.sleep(0.02)
        now = int(clipboard.sequence())
        if now != last:
            last, still_since = now, time.monotonic()
        elif time.monotonic() - still_since >= _SETTLE:
            break
    return last


def selected_text(timeout: float = 0.8, restore_clipboard: bool = True) -> str | None:
    """The current selection through Ctrl+C, or None if nothing was copied. Never in a console
    window. With `restore_clipboard` the previous clipboard comes back after a moment, unless
    something else changed the clipboard first."""
    if _console_in_front():
        log.info("console window in front; not sending Ctrl+C")
        return None
    _wait_for_modifiers_release()
    before_count = int(clipboard.sequence())
    previous = clipboard.snapshot() if restore_clipboard else None
    if restore_clipboard and previous is None:
        log.info("clipboard could not be saved; not sending Ctrl+C")
        return None
    _send_copy()
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        now = int(clipboard.sequence())
        if now != before_count:
            after = _settled_sequence(now)
            note_own_changes(before_count, after)  # the app copied because we pressed Ctrl+C
            text = clipboard.get_text()
            log.info("Ctrl+C copied %d chars in %.2f s", len(text), time.monotonic() - started)
            if restore_clipboard:
                _later(_RESTORE_DELAY, partial(_restore_unless_changed, previous, after))
            return text if text.strip() else None
        time.sleep(0.03)
    log.info("Ctrl+C copied nothing in %.2f s", timeout)
    return None


def selection_or_clipboard() -> tuple[str, str]:
    """Returns (text, source) where source is 'selection (accessibility)', 'selection (copy)'
    or 'clipboard'."""
    text = ax_selected_text()
    if text:
        return text, "selection (accessibility)"
    text = selected_text()
    if text:
        return text, "selection (copy)"
    return clipboard_text(), "clipboard"


# Dictation on Windows (typing the result at the cursor, learning corrections) is task D3 of
# the Windows plan. Until then its element helpers raise.
_NO_DICTATION = "Windows dictation is not built yet (task D3)"


def focused_element():
    raise NotImplementedError(_NO_DICTATION)


def ax_attribute(element, name: str):
    raise NotImplementedError(_NO_DICTATION)


def ax_settable(element, name: str) -> bool:
    raise NotImplementedError(_NO_DICTATION)


def ax_role(element) -> str | None:
    raise NotImplementedError(_NO_DICTATION)


def ax_text_value(element) -> str | None:
    raise NotImplementedError(_NO_DICTATION)


def ax_pid(element) -> int | None:
    raise NotImplementedError(_NO_DICTATION)


def ax_set_timeout(element, seconds: float) -> None:
    raise NotImplementedError(_NO_DICTATION)
