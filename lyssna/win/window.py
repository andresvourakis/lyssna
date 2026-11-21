"""What the Qt interface needs from Windows for its windows: floating panels that never take
focus, click-through, a dark title bar. The twin of lyssna/mac/window.py; the interface reaches
it as host.window.

Why native calls at all: Qt's Tool, frameless, stay-on-top and does-not-accept-focus flags do not
stop Windows from activating a window that is clicked, and a tool window still gets a taskbar
button. WS_EX_NOACTIVATE and WS_EX_TOOLWINDOW on the native window fix both. Qt may rebuild the
native window or its styles (on show, on a new window id, on a flag change), so the panel calls
make_floating again after each of those.

The app's identity in the taskbar (the AppUserModelID) is set in lyssna/win/instance.py, before
any window exists."""

from __future__ import annotations

import logging

log = logging.getLogger("lyssna.win.window")

# The Windows hooks (win/events.py) and UI Automation (win/capture.py) report physical pixels,
# which coords.py turns into Qt's logical ones.
POINTS_ARE_PHYSICAL = True

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
