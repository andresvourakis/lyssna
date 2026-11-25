"""What the Qt interface needs from Windows for its windows: floating panels that never take
focus, click-through, a dark title bar, the mouse button. The twin of lyssna/mac/window.py; the
interface reaches it as host.window.

Why native calls at all: Qt's Tool, frameless, stay-on-top and does-not-accept-focus flags do not
stop Windows from activating a window that is clicked, and a tool window still gets a taskbar
button. WS_EX_NOACTIVATE and WS_EX_TOOLWINDOW on the native window fix both. Qt may rebuild the
native window or its styles (on show, on a new window id, on a flag change), so the panel calls
make_floating again after each of those.

The app's identity in the taskbar (the AppUserModelID) is set in lyssna/win/instance.py, before
any window exists."""

from __future__ import annotations

import logging
import ntpath
import os
from types import SimpleNamespace

log = logging.getLogger("lyssna.win.window")

# The Windows hooks (win/events.py) and UI Automation (win/capture.py) report physical pixels,
# which coords.py turns into Qt's logical ones.
POINTS_ARE_PHYSICAL = True

# Other apps' pop-ups (Edge's selection toolbar) can be found with popups_over, so the play
# button asks for them. The interface asks this flag, never the OS.
DETECTS_POPUPS = True

# Qt's enter and leave events reach a floating panel here even while another app is in front,
# so watch_mouse_enter has nothing to do and the interface listens to Qt's own. The interface
# asks this flag, never the OS.
WATCHES_MOUSE_ENTER = False

# The player's speed and voice menus are Qt's here (pop_up_menu shows nothing natively). The
# interface asks this flag, never the OS.
NATIVE_MENUS = False

# Window styles and the calls popups_over makes.
GWL_STYLE = -16
WS_POPUP = 0x80000000
GW_OWNER = 4
DWMWA_CLOAKED = 14

# What front_app asks: the least access that still answers a process's executable path.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH_CHARS = 32768  # the longest path QueryFullProcessImageNameW can answer

# Extended window styles.
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0020, 0x0040
HWND_TOPMOST = -1
DWMWA_USE_IMMERSIVE_DARK_MODE = 20


def _user32():
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    long_ptr = ctypes.c_ssize_t
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongPtrW.restype = long_ptr
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr]
    u.SetWindowLongPtrW.restype = long_ptr
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.SetWindowPos.restype = wintypes.BOOL
    return u


def _set_ex_style(win_id: int, add: int, remove: int = 0) -> None:
    """OR `add` into (and clear `remove` from) a native window's extended style."""
    try:
        u = _user32()
        hwnd = int(win_id)
        style = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        new = (style | add) & ~remove
        if new != style:
            u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
            u.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    except Exception:
        log.warning("could not set the window's extended style", exc_info=True)


def make_floating(widget) -> None:
    """Make the widget's window a floating panel that never activates: no activation on a click
    and no taskbar button. Safe to call again, and called again whenever Qt may have rebuilt
    the native window's styles."""
    _set_ex_style(widget.winId(), WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def set_click_through(widget, on: bool) -> None:
    """Let every click on the window fall through to the window below (or stop doing so)."""
    if on:
        _set_ex_style(widget.winId(), WS_EX_TRANSPARENT)
    else:
        _set_ex_style(widget.winId(), 0, WS_EX_TRANSPARENT)


def watch_mouse_enter(widget, callback) -> bool:
    """Nothing to do on Windows: Qt delivers its enter events to the windows of an app in the
    background, so the interface listens to those (WATCHES_MOUSE_ENTER is False). Returns False:
    nothing is watched here."""
    return False


def pop_up_menu(rows, x: float, y: float, picked) -> bool:
    """Nothing to show natively on Windows (NATIVE_MENUS is False): the interface draws its own
    menu with Qt. Returns False."""
    return False


def confirm_sheet(widget, message: str, info: str, ok: str, then) -> bool:
    """Nothing native to show on Windows: the interface asks with its own dialog. Returns False."""
    return False


def bring_to_top(widget) -> None:
    """Topmost and shown, without activating."""
    try:
        u = _user32()
        u.SetWindowPos(int(widget.winId()), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    except Exception:
        log.warning("could not bring the window to the top", exc_info=True)


def is_on_screen(widget) -> bool | None:
    """None: Windows has not shown the Mac's lost-window quirk, so there is nothing to check."""
    return None


def prepare_app() -> None:
    """Nothing to do on Windows before the QApplication exists: there is no Dock to keep out of
    (the taskbar identity is instance.set_app_id, which run() calls first)."""


def hide_dock_icon() -> None:
    """Nothing to do on Windows: the panels carry WS_EX_TOOLWINDOW and the tray icon is the app's
    only permanent place (the taskbar grouping is instance.set_app_id)."""


def activate_app() -> None:
    """Nothing to do on Windows: QWidget.activateWindow brings a normal window forward."""


def set_dark_title_bar(widget, dark: bool) -> bool:
    """Ask Windows to draw this window's title bar dark (or light again). Windows 10 20H1 and
    later honour it; older ones ignore it. Returns whether it took."""
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.dwmapi.DwmSetWindowAttribute
        fn.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        fn.restype = ctypes.c_long
        value = ctypes.c_int(1 if dark else 0)
        return fn(wintypes.HWND(int(widget.winId())), DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value),
                  ctypes.sizeof(value)) == 0
    except Exception:
        log.debug("could not set the title bar colour", exc_info=True)
        return False


def _popup_api():
    """user32, dwmapi and the EnumWindows callback type, with the argument types popups_over
    needs. Its own function so the tests can hand in stand-ins (ctypes has no Windows DLLs or
    WINFUNCTYPE elsewhere)."""
    import ctypes
    from ctypes import wintypes

    u = _user32()
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]  # Windows only
    u.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    u.EnumWindows.restype = wintypes.BOOL
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetWindow.restype = wintypes.HWND
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    dwm = ctypes.windll.dwmapi
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    return SimpleNamespace(user32=u, dwmapi=dwm, callback=callback)


def popups_over(rect) -> list[tuple]:
    """The rects (left, top, right, bottom, physical pixels) of other apps' pop-up windows that
    overlap `rect` (the same form). A pop-up here is a visible, not cloaked, top-level window of
    another process that has an owner and the WS_POPUP style: Edge's selection toolbar is one
    (Chrome_WidgetWin_1, owned by the Edge window). Cheap, and never raises: any failure is []."""
    try:
        import ctypes
        from ctypes import wintypes

        api = _popup_api()
        u, dwm = api.user32, api.dwmapi
        left, top, right, bottom = rect
        windows: list = []

        def collect(hwnd, _lparam):
            windows.append(hwnd)  # only collect here: an error inside a ctypes callback is lost
            return True

        u.EnumWindows(api.callback(collect), 0)
        me = os.getpid()
        found = []
        for hwnd in windows:
            if not hwnd or not u.IsWindowVisible(hwnd):
                continue
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == me or not u.GetWindow(hwnd, GW_OWNER):
                continue
            if not u.GetWindowLongPtrW(hwnd, GWL_STYLE) & WS_POPUP:
                continue
            cloaked = ctypes.c_int(0)
            if dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
                continue
            r = wintypes.RECT()
            if not u.GetWindowRect(hwnd, ctypes.byref(r)):
                continue
            if r.right <= r.left or r.bottom <= r.top:
                continue
            if r.left < right and r.right > left and r.top < bottom and r.bottom > top:
                found.append((r.left, r.top, r.right, r.bottom))
        return found
    except Exception:
        log.debug("could not look for pop-up windows", exc_info=True)
        return []


def _process_api():
    """user32 and kernel32 with the argument types front_app needs. Its own function so the tests
    can hand in stand-ins."""
    import ctypes
    from ctypes import wintypes

    u = _user32()
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    k = ctypes.windll.kernel32
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return SimpleNamespace(user32=u, kernel32=k)


def front_app() -> str | None:
    """The app in front: the foreground window's executable name, lower case ("msedge.exe"). The
    play button keeps what it learns about other apps' pop-ups under this name. None when it
    cannot be found; never raises."""
    try:
        import ctypes
        from ctypes import wintypes

        api = _process_api()
        u, k = api.user32, api.kernel32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(MAX_PATH_CHARS)
            path = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
            if not k.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                return None
            name = ntpath.basename(path.value).lower()
        finally:
            k.CloseHandle(handle)
        return name or None
    except Exception:
        log.debug("could not find the app in front", exc_info=True)
        return None


def left_button_down() -> bool:
    """Is the left mouse button held right now? Asked of the system, so a release Qt never saw
    (the stuck-drag case the player guards against) still counts."""
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False
