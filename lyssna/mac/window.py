"""What the Qt interface needs from macOS for its windows: floating panels that never take focus,
click-through, no Dock icon, bringing the app forward for a normal window. The twin of
lyssna/win/window.py; the interface reaches it as host.window.

Why native calls at all: for a Qt.Tool window Qt creates an NSPanel (Qt's QNSPanel), but not one
that behaves like the legacy panel in lyssna/legacy/ui/panel.py. Without help a click on it makes
Lyssna the frontmost app (the user's text field loses its keyboard), and it hides behind
full-screen apps and stays on one Space. The Stage 1 proof (scripts/spike_qt_mac.py, item 1)
found the settings that match the legacy panel: borderless and non-activating, the status window
level, all Spaces plus full-screen auxiliary plus stationary, never hidden when the app is
inactive, key only if needed, and the undocumented _setPreventsActivation:, the only call that
kept the app in front frontmost after a click on a window Qt had already made.

Qt resets the style mask and the level whenever it re-applies window flags (QWindow.setFlag, which
the panel uses for click-through), so make_floating must run again after every such change.
Hiding, showing, opacity, moving and resizing keep the settings.

Everything is reached through objc messages on the window Qt made, with the AppKit numbers
written out below, so nothing here imports a Mac framework when the module loads (the
pretend-Windows import test loads it) and the settings logic can be tested with a stand-in
window. When Qt is not drawing with Cocoa (the offscreen tests, the preview shots) there is no
NSWindow and every call does nothing."""

from __future__ import annotations

import logging

log = logging.getLogger("lyssna.mac.window")

# The Windows hooks and UI Automation report physical pixels; the Mac's event taps already
# report points, which are Qt's logical units. coords.py asks this.
POINTS_ARE_PHYSICAL = False

# AppKit's values (NSWindow.h), written out so this module loads without AppKit.
STYLE_BORDERLESS = 0
STYLE_NONACTIVATING_PANEL = 1 << 7
STATUS_WINDOW_LEVEL = 25  # NSStatusWindowLevel
JOIN_ALL_SPACES = 1 << 0  # NSWindowCollectionBehaviorCanJoinAllSpaces
STATIONARY = 1 << 4  # NSWindowCollectionBehaviorStationary
FULL_SCREEN_AUXILIARY = 1 << 8  # NSWindowCollectionBehaviorFullScreenAuxiliary
PANEL_STYLE = STYLE_BORDERLESS | STYLE_NONACTIVATING_PANEL
PANEL_SPACES = JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY | STATIONARY
PREVENTS_ACTIVATION = "_setPreventsActivation:"  # undocumented; see make_floating

_warned_no_prevents_activation = False


def _native_window(widget):
    """The NSWindow behind a Qt widget's window, or None when Qt is not drawing with Cocoa."""
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() != "cocoa":
        return None
    import ctypes

    import objc

    view = objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))
    return view.window()


def make_floating(widget) -> None:
    """Make the widget's window a floating panel that never activates the app: above other
    windows and full-screen apps, on every Space, a click on it leaving the app in front where
    it is. Safe to call again, and it must be called again after any Qt window flag change."""
    win = _native_window(widget)
    if win is not None:
        _float_window(win)


def _float_window(win) -> None:
    """The legacy panel's settings on an existing NSWindow (see the module docstring)."""
    try:
        if win.respondsToSelector_("setBecomesKeyOnlyIfNeeded:"):  # an NSPanel, as Qt makes for Qt.Tool
            win.setStyleMask_(PANEL_STYLE)
            win.setBecomesKeyOnlyIfNeeded_(True)
        win.setLevel_(STATUS_WINDOW_LEVEL)
        win.setCollectionBehavior_(PANEL_SPACES)
        win.setHidesOnDeactivate_(False)
    except Exception:
        log.warning("could not make the window a floating panel", exc_info=True)
        return
    _prevent_activation(win)


def _prevent_activation(win) -> None:
    """The undocumented call that keeps a click from making Lyssna the frontmost app. If a macOS
    release drops it, say so once and carry on with the public settings: the panel still floats,
    and only a click on it would bring Lyssna forward."""
    global _warned_no_prevents_activation
    try:
        if win.respondsToSelector_(PREVENTS_ACTIVATION):
            win._setPreventsActivation_(True)
            return
        reason = "the window does not answer it"
    except Exception as e:
        reason = f"it failed: {e!r}"
    if not _warned_no_prevents_activation:
        _warned_no_prevents_activation = True
        log.warning("%s is not available (%s): a click on a floating panel may bring Lyssna to the front",
                    PREVENTS_ACTIVATION, reason)


def set_click_through(widget, on: bool) -> None:
    """Let every click on the window fall through to the window below (or stop doing so)."""
    win = _native_window(widget)
    if win is None:
        return
    try:
        win.setIgnoresMouseEvents_(bool(on))
    except Exception:
        log.warning("could not set click-through", exc_info=True)


def bring_to_top(widget) -> None:
    """Put the shown window in front of every other window without activating the app (the
    legacy panel's orderFrontRegardless)."""
    win = _native_window(widget)
    if win is None:
        widget.raise_()
        return
    try:
        win.orderFrontRegardless()
    except Exception:
        log.warning("could not bring the window to the top", exc_info=True)


def is_on_screen(widget) -> bool | None:
    """Does the window server list the window as on screen? None when it cannot be asked.

    Why: after a display is unplugged macOS has been seen dropping the legacy AppKit panels from
    every Space while AppKit still called them visible; the window server's list is the only
    check that was verified against that state. The Qt panel logs this answer so the side-by-side
    stage can tell whether the quirk exists under Qt too."""
    win = _native_window(widget)
    if win is None:
        return None
    try:
        import Quartz

        number = int(win.windowNumber())
        infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
        return any(info.get(Quartz.kCGWindowNumber) == number for info in infos)
    except Exception:
        log.debug("could not ask the window server about the window", exc_info=True)
        return None


def hide_dock_icon() -> None:
    """No Dock icon, like the legacy Lyssna: the accessory activation policy. Run after the
    QApplication exists, since Qt sets the regular policy when it starts from source (the built
    app's LSUIElement makes it accessory from launch)."""
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        log.debug("could not hide the Dock icon", exc_info=True)


def activate_app() -> None:
    """Bring Lyssna to the front, for a normal window the user asked to see (setup, What's New).
    An app with no Dock icon is not brought forward by showing a window, so the legacy windows
    call activateIgnoringOtherApps too."""
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() != "cocoa":
        return
    try:
        from AppKit import NSApp

        NSApp.activateIgnoringOtherApps_(True)
    except Exception:
        log.debug("could not bring the app to the front", exc_info=True)


def set_dark_title_bar(widget, dark: bool) -> bool:
    """Nothing to do on the Mac: the system draws the title bar in the app's appearance."""
    return False
