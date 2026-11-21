"""Global keys and mouse on Windows, the twin of lyssna/events.py.

KeyboardTap  - a low-level keyboard hook (WH_KEYBOARD_LL) that fires the callbacks for
               registered hotkeys and swallows those keystrokes.
MouseTap     - a low-level mouse hook (WH_MOUSE_LL) that feeds a SelectionGestureDetector,
               like the Mac tap. It never swallows anything.

Each tap runs its hook on its own daemon thread with its own message loop, because Windows
calls a low-level hook on the thread that installed it, and only while that thread pumps
messages. Windows also removes a hook without telling anyone when its callback is slow, so
the callbacks only decide and hand the real work to other threads.

The deciding logic (key_event, mouse_event, ClickCounter) takes plain values and never
touches a Windows API, so it is tested on any system. Every ctypes.windll call lives in
the functions at the bottom of this file and in start() and stop().

Only the left Alt key counts as Alt. On many European keyboards AltGr arrives as left Ctrl
plus right Alt and types characters like @ and {, so while right Alt is held no hotkey
matches at all. Ctrl+Alt+S fires with left Alt, never with AltGr+S.

Mouse coordinates: the hook reports physical screen pixels with the origin at the top-left
corner of the primary monitor, negative on monitors to its left or above it. Point carries
exactly those values. The Windows interface converts them to its own units."""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable

from ..gestures import Point, SelectionGestureDetector, spawn
from ..hotkeys import parse_with
from . import hotkeys as win_hotkeys

__all__ = [
    "ClickCounter",
    "KeyboardTap",
    "MouseTap",
    "Point",
    "SelectionGestureDetector",
    "accessibility_trusted",
    "spawn",
]

log = logging.getLogger("lyssna.win.events")

# Hook types and messages.
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
LLKHF_INJECTED = 0x10
SM_CXDOUBLECLK = 36
SM_CYDOUBLECLK = 37

# Modifier virtual-key codes. The hook reports the left and right variants; the generic
# codes are listed too in case some driver sends them, and count as the left key.
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C
MODIFIER_VKS = frozenset({VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN})
_GENERIC = {VK_SHIFT: VK_LSHIFT, VK_CONTROL: VK_LCONTROL, VK_MENU: VK_LMENU}

# A key-down of a swallowed hotkey key this soon after the previous one is its auto-repeat.
# Longer than the slowest keyboard repeat delay Windows offers (1 second). A later key-down
# means its key-up went missing (released while an elevated window had focus), so it is a
# fresh press.
REPEAT_WINDOW = 1.2


def accessibility_trusted(prompt: bool = False) -> bool:
    """Windows has no permission for watching keys and reading the selection, so always True."""
    return True


def modifier_flags(held: set[int] | frozenset[int]) -> int | None:
    """The hotkey flags (lyssna.win.hotkeys) for the modifier keys held, or None while right
    Alt (AltGr) is held, when no hotkey may match."""
    if VK_RMENU in held:
        return None
    flags = 0
    if VK_LCONTROL in held or VK_RCONTROL in held:
        flags |= win_hotkeys.CTRL
    if VK_LMENU in held:
        flags |= win_hotkeys.ALT
    if VK_LSHIFT in held or VK_RSHIFT in held:
        flags |= win_hotkeys.SHIFT
    if VK_LWIN in held or VK_RWIN in held:
        flags |= win_hotkeys.WIN
    return flags


class _Hook:
    """Shared plumbing: a daemon thread installs the hook, pumps messages until stop()
    posts WM_QUIT, then removes the hook."""

    hook_type: int = 0
    name: str = "hook"

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._handle = None
        self._proc = None  # the ctypes callback; kept here so it is not garbage collected
        self._ready = threading.Event()
        self._ok = False

    def start(self) -> bool:
        if sys.platform != "win32":
            log.error("%s: low-level hooks exist only on Windows", type(self).__name__)
            return False
        if self._thread is not None and self._thread.is_alive():
            return self._ok
        self._ready.clear()
        self._ok = False
        self._prepare()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"lyssna-{self.name}-hook")
        self._thread.start()
        if not self._ready.wait(5.0):
            log.error("%s: the hook thread did not report back in time", type(self).__name__)
            return False
        return self._ok

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if self._thread_id:
            _api().user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(2.0)
        self._thread = None
        self._thread_id = 0
        self._finish()

    def _prepare(self) -> None:
        """Runs in start() before the hook thread; for subclasses."""

    def _finish(self) -> None:
        """Runs in stop() after the hook thread ended; for subclasses."""

    def _run(self) -> None:
        import ctypes

        api = _api()
        try:
            self._thread_id = api.kernel32.GetCurrentThreadId()
            self._proc = api.HOOKPROC(self._callback)
            module = api.kernel32.GetModuleHandleW(None)
            self._handle = api.user32.SetWindowsHookExW(self.hook_type, self._proc, module, 0)
            if not self._handle:
                log.error("%s: SetWindowsHookExW failed with error %d", type(self).__name__, ctypes.get_last_error())
                self._thread_id = 0
                return
            self._ok = True
            log.info("%s: hook installed", type(self).__name__)
        except Exception:
            log.exception("%s: could not install the hook", type(self).__name__)
            self._thread_id = 0
            return
        finally:
            self._ready.set()
        msg = api.MSG()
        while api.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass  # the hook callback runs inside GetMessageW; nothing else is posted here
        api.user32.UnhookWindowsHookEx(self._handle)
        self._handle = None
        self._ok = False
        log.info("%s: hook removed", type(self).__name__)

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == HC_ACTION:
            try:
                if self._on_hook(w_param, l_param):
                    return 1  # swallowed
            except Exception:  # never let an exception reach Windows
                log.exception("%s: hook handler failed", type(self).__name__)
        return _api().user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_hook(self, w_param: int, l_param: int) -> bool:
        return False


class KeyboardTap(_Hook):
    """Hotkeys from a low-level keyboard hook. key_event holds all the rules and takes plain
    values; the hook only unpacks the Windows struct and calls it."""

    hook_type = WH_KEYBOARD_LL
    name = "keyboard"

    def __init__(self):
        super().__init__()
        self._bindings: dict[tuple[int, int], Callable[[], None]] = {}
        self.on_any_key: Callable[[], None] | None = None
        # Called first for every key event that is not injected, as raw_listener(vk, down, t);
        # returning True swallows the event. The seam push-to-talk will use.
        self.raw_listener: Callable[[int, bool, float], bool] | None = None
        # Nothing reads this yet; the dictation task wires push-to-talk through raw_listener.
        self.push_to_talk = None
        # Returns the modifier keys Windows says are held right now. Set by start() to read
        # GetAsyncKeyState; when set, a non-modifier key-down resyncs the held set from it.
        self.modifiers_now: Callable[[], set[int]] | None = None
        self._held: set[int] = set()
        self._swallowed: dict[int, float] = {}  # hotkey key -> time of its last swallowed key-down

    def bind(self, spec: str, callback: Callable[[], None]) -> None:
        self._bindings[parse_with(spec, win_hotkeys.MODIFIERS, win_hotkeys.KEYCODES)] = callback

    def key_event(self, vk: int, down: bool, injected: bool, t: float) -> bool:
        """One key event. Returns True to swallow it."""
        if injected:
            return False  # Lyssna's own Ctrl+C and Ctrl+V, among others
        vk = _GENERIC.get(vk, vk)
        is_modifier = vk in MODIFIER_VKS
        if is_modifier:
            if down:
                self._held.add(vk)
            else:
                self._held.discard(vk)
        elif down and self.on_any_key:
            # Every key-down, hotkey or not, as on the Mac (whose modifiers never arrive as
            # key-downs), so any key press dismisses the play button.
            spawn(self.on_any_key)
        raw = self.raw_listener
        if raw is not None and raw(vk, down, t):
            if not down:
                self._swallowed.pop(vk, None)
            return True
        if is_modifier:
            return False
        if not down:
            # The key-up of a swallowed hotkey is swallowed too, so the app in front never
            # sees half a keystroke.
            return self._swallowed.pop(vk, None) is not None
        last = self._swallowed.get(vk)
        if last is not None:
            if t - last <= REPEAT_WINDOW:
                self._swallowed[vk] = t
                return True  # auto-repeat of a hotkey: swallowed, does not fire again
            del self._swallowed[vk]  # its key-up went missing; treat this as a new press
        if self.modifiers_now is not None:
            self._held = set(self.modifiers_now())
        flags = modifier_flags(self._held)
        cb = self._bindings.get((flags, vk)) if flags is not None else None
        if cb is None:
            return False
        self._swallowed[vk] = t
        spawn(cb)
        return True  # swallow so the app in front does not also see the hotkey

    def _prepare(self) -> None:
        self._held.clear()
        self._swallowed.clear()
        self.modifiers_now = _held_modifiers

    def _on_hook(self, w_param: int, l_param: int) -> bool:
        info = _api().KBDLLHOOKSTRUCT.from_address(l_param)
        down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
        return self.key_event(info.vkCode, down, bool(info.flags & LLKHF_INJECTED), time.monotonic())


class ClickCounter:
    """Counts presses into single, double and triple clicks like the Mac click state: a press
    within `interval` seconds of the previous one and within half of `width` and `height` of
    it (the system double-click rectangle is centred on the previous press) adds one, up to 3."""

    def __init__(self, interval: float = 0.5, width: float = 4.0, height: float = 4.0):
        self.interval = interval
        self.width = width
        self.height = height
        self._last: tuple[float, float, float] | None = None
        self._count = 0

    def press(self, x: float, y: float, t: float) -> int:
        last = self._last
        if (
            last is not None
            and t - last[2] <= self.interval
            and abs(x - last[0]) <= self.width / 2
            and abs(y - last[1]) <= self.height / 2
        ):
            self._count = min(self._count + 1, 3)
        else:
            self._count = 1
        self._last = (x, y, t)
        return self._count

    def reset(self) -> None:
        self._last = None
        self._count = 0


class MouseTap(_Hook):
    """Selection gestures from a low-level mouse hook. mouse_event holds the rules and takes
    plain values. Once started, the detector runs on one worker thread fed in order from a
    queue, because the detector's callbacks read the clipboard and the screen, which is too
    slow for the hook thread. Before start() it is called directly, which the tests use."""

    hook_type = WH_MOUSE_LL
    name = "mouse"

    def __init__(self, detector: SelectionGestureDetector):
        super().__init__()
        self.detector = detector
        self.clicks = ClickCounter()
        self._left_down = False
        self._queue: queue.SimpleQueue | None = None
        self._worker: threading.Thread | None = None

    def mouse_event(self, message: int, x: float, y: float, t: float) -> None:
        """One mouse event. Never swallows."""
        if message == WM_MOUSEMOVE:
            if self._left_down:
                self._emit(self.detector.mouse_dragged, Point(x, y))
            return
        if message == WM_LBUTTONDOWN:
            self._left_down = True
            self._emit(self.detector.mouse_down, Point(x, y), self.clicks.press(x, y, t))
        elif message == WM_LBUTTONUP:
            self._left_down = False
            self._emit(self.detector.mouse_up, Point(x, y))
        elif message in (WM_RBUTTONDOWN, WM_MBUTTONDOWN):
            self.clicks.reset()
            self._emit(self.detector.other_click, Point(x, y))

    def _emit(self, fn: Callable, *args) -> None:
        q = self._queue
        if q is None:
            fn(*args)
        else:
            q.put((fn, args))

    def _drain(self, q: queue.SimpleQueue) -> None:
        while True:
            item = q.get()
            if item is None:
                return
            fn, args = item
            try:
                fn(*args)
            except Exception:
                log.exception("mouse gesture handler failed")

    def _prepare(self) -> None:
        self._left_down = False
        try:
            interval, width, height = _double_click_metrics()
            self.clicks = ClickCounter(interval, width, height)
        except Exception:
            log.exception("could not read the double-click settings; using the defaults")
            self.clicks = ClickCounter()
        self._queue = queue.SimpleQueue()
        self._worker = threading.Thread(target=self._drain, args=(self._queue,), daemon=True, name="lyssna-mouse")
        self._worker.start()

    def start(self) -> bool:
        ok = super().start()
        if not ok:
            self._finish()
        return ok

    def _finish(self) -> None:
        if self._queue is not None:
            self._queue.put(None)
        self._queue = None
        self._worker = None

    def _on_hook(self, w_param: int, l_param: int) -> bool:
        if w_param == WM_MOUSEMOVE and not self._left_down:
            return False  # hundreds a second; nothing to do
        info = _api().MSLLHOOKSTRUCT.from_address(l_param)
        self.mouse_event(w_param, info.pt.x, info.pt.y, time.monotonic())
        return False


# -- Win32 ---------------------------------------------------------------------------------

_API = None


def _api():
    """user32 and kernel32 with argtypes and restype declared, built on first use so this
    module imports on any system."""
    global _API
    if _API is not None:
        return _API
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    LRESULT = wintypes.LPARAM
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetDoubleClickTime.argtypes = []
    user32.GetDoubleClickTime.restype = wintypes.UINT
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p

    _API = SimpleNamespace(
        user32=user32, kernel32=kernel32, HOOKPROC=HOOKPROC, MSG=wintypes.MSG,
        KBDLLHOOKSTRUCT=KBDLLHOOKSTRUCT, MSLLHOOKSTRUCT=MSLLHOOKSTRUCT,
    )
    return _API


def _held_modifiers() -> set[int]:
    """The modifier keys Windows says are held now. Catches a key released while a secure
    desktop or an elevated window had focus, which the hook never saw."""
    get = _api().user32.GetAsyncKeyState
    return {vk for vk in MODIFIER_VKS if get(vk) & 0x8000}


def _double_click_metrics() -> tuple[float, float, float]:
    """(seconds, width, height) of the system double-click time and rectangle."""
    user32 = _api().user32
    return (
        user32.GetDoubleClickTime() / 1000.0,
        float(user32.GetSystemMetrics(SM_CXDOUBLECLK)),
        float(user32.GetSystemMetrics(SM_CYDOUBLECLK)),
    )
