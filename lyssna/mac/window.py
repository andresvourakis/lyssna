"""What the Qt interface needs from macOS for its windows: floating panels that never take focus,
click-through, no Dock icon, bringing the app forward for a normal window, the mouse button. The twin of
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

Qt's enter and leave events do not reach a floating panel while another app is in front, which
for Lyssna is always: the cursor can come onto the docked sliver and nothing happens. Measured
on Qt 6.12 the view's own tracking area is active always, so the window does hear the cursor
come in, but Qt hands that to the widget under the cursor at that moment, which is the shadow
margin around the sliver, and the moves that would carry it on to the sliver do not seem to
arrive while the app is in the background (the same cause would leave every hover glow dark).
The legacy views each had an NSTrackingArea active always. watch_mouse_enter does the same for
the Qt window: its area hears the cursor come onto the window, and from then until it leaves a
short poll of the cursor tells when it is on the watched widget (WATCHES_MOUSE_ENTER).

Everything is reached through objc messages on the window Qt made, with the AppKit numbers
written out below, so nothing here imports a Mac framework when the module loads (the
pretend-Windows import test loads it) and the settings logic can be tested with a stand-in
window. When Qt is not drawing with Cocoa (the offscreen tests, the preview shots) there is no
NSWindow and every call does nothing."""

from __future__ import annotations

import logging
import os
import weakref

log = logging.getLogger("lyssna.mac.window")

# The Windows hooks and UI Automation report physical pixels; the Mac's event taps already
# report points, which are Qt's logical units. coords.py asks this.
POINTS_ARE_PHYSICAL = False

# Whether popups_over can find other apps' pop-ups. Not here: the play button's spot is the
# legacy one and stays put, so it starts no checks.
DETECTS_POPUPS = False

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

# Qt's enter and leave events do not reach a floating panel here (see the module docstring), so
# watch_mouse_enter hears the cursor arrive itself and the interface relies on it instead. The
# interface asks this flag, never the OS.
WATCHES_MOUSE_ENTER = True

# NSTrackingArea's options (NSTrackingArea.h): enter and exit, whether or not the app is active,
# over whatever part of the view is visible (AppKit keeps the area's rect up to date).
TRACK_ENTERED_AND_EXITED = 0x01  # NSTrackingMouseEnteredAndExited
TRACK_ACTIVE_ALWAYS = 0x80       # NSTrackingActiveAlways
TRACK_IN_VISIBLE_RECT = 0x200    # NSTrackingInVisibleRect
TRACKING_OPTIONS = TRACK_ENTERED_AND_EXITED | TRACK_ACTIVE_ALWAYS | TRACK_IN_VISIBLE_RECT
ENTER_POLL_SECS = 0.03  # the cursor poll while it is on a watched window (HOVER_POLL in ui/layout/pill.py)
OWNER_CLASS = "LyssnaMouseEnterWatcher"

_warned_no_prevents_activation = False
_watches: dict[int, _Watch] = {}  # id(widget) -> its watch; keeps the owners alive (a tracking area does not)
_stuck_owners: list = []  # owners of areas that could not be removed
_owner_cls = None


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


def watch_mouse_enter(widget, callback) -> bool:
    """Call `callback()` each time the cursor arrives on `widget`, also while another app is in
    front (see the module docstring). An NSTrackingArea on the native view of the widget's
    window hears the cursor come onto the window; from then until it leaves, a poll of the
    cursor every ENTER_POLL_SECS fires the callback once per arrival on the widget itself, on
    the main thread. Safe to call again, and called again whenever Qt may have rebuilt the
    native view: the same view keeps its one area, a new one gets it and the old one loses it.
    Returns whether the widget is watched (False off Cocoa); never raises."""
    try:
        _forget_dead()
        view = _native_view(widget)
        if view is None:
            return False
        win_id = int(widget.window().winId())
        w = _watches.get(id(widget))
        if w is not None and w.widget() is not widget:  # a dead widget's id, used again
            w.stop()
            w.remove_area()
            w = None
        if w is not None:
            w.callback = callback
            if w.win_id == win_id and w.area in list(view.trackingAreas()):
                return True
            w.remove_area()
        else:
            w = _Watch(widget, callback)
        owner = _make_owner(w)
        area = _make_area(TRACKING_OPTIONS, owner)
        view.addTrackingArea_(area)
        w.view, w.win_id, w.owner, w.area = view, win_id, owner, area
        _watches[id(widget)] = w
        return True
    except Exception:
        log.warning("could not watch the mouse on a window", exc_info=True)
        return False


class _Watch:
    """One watched widget: its tracking area, the view it is on, the NSObject that owns it (kept
    here: a tracking area does not keep its owner alive) and the poll while the cursor is on
    the window."""

    def __init__(self, widget, callback):
        self.widget = weakref.ref(widget)
        self.callback = callback
        self.view = None
        self.win_id = 0
        self.owner = None
        self.area = None
        self.seen = None  # at the last look: "inside", "outside" or "hidden"; None before the first since entering
        self.timer = None

    def entered(self) -> None:
        """The cursor came onto the window: look now, and keep looking until it leaves."""
        if self.timer is None:
            from PySide6.QtCore import QTimer

            self.timer = QTimer()
            self.timer.setInterval(round(ENTER_POLL_SECS * 1000))
            self.timer.timeout.connect(self.check)
        if not self.timer.isActive():
            self.timer.start()
        self.check()

    def exited(self) -> None:
        self.check()

    def check(self) -> None:
        """Fire the callback if the cursor has just come onto the widget; stop looking once it
        is off the window. An arrival is a crossing: from beside the widget, or straight onto
        it as the cursor comes onto the window. A widget shown under the cursor (the resting
        squiggle back after its menu closed) has not been arrived on, as a tracking area added
        under the cursor would not fire either; the cursor must leave it and come back."""
        try:
            widget = self.widget()
            pos = _cursor_pos()
            on_window = widget is not None and _over(widget.window(), pos)
            shown = on_window and widget.isVisible()
            seen = ("inside" if _over(widget, pos) else "outside") if shown else "hidden"
        except Exception:  # the widget is gone
            on_window, seen = False, "hidden"
        arrived = seen == "inside" and self.seen in (None, "outside")
        self.seen = seen
        if not on_window:
            self.stop()
        if arrived:
            try:
                self.callback()
            except Exception:
                log.exception("the mouse enter callback failed")

    def stop(self) -> None:
        self.seen = None
        if self.timer is not None:
            self.timer.stop()

    def remove_area(self) -> None:
        """Take the area off its view and let the owner go (it is kept for good if the area could
        not be removed, so AppKit never messages a freed object)."""
        if self.view is not None and self.area is not None:
            try:
                self.view.removeTrackingArea_(self.area)
            except Exception:
                log.debug("could not remove a tracking area", exc_info=True)
                _stuck_owners.append(self.owner)
        if self.owner is not None:
            self.owner.watch = None
        self.view = self.owner = self.area = None
        self.win_id = 0


def _forget_dead() -> None:
    """Drop the watches whose widget has been deleted, their areas first."""
    for key, w in list(_watches.items()):
        widget = w.widget()
        alive = widget is not None
        if alive:
            try:
                import shiboken6

                alive = shiboken6.isValid(widget)
            except Exception:
                alive = False
        if not alive:
            w.stop()
            w.remove_area()
            del _watches[key]


def _over(widget, pos) -> bool:
    """Is the global Qt point `pos` on the shown widget?"""
    return bool(widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(pos)))


def _cursor_pos():
    from PySide6.QtGui import QCursor

    return QCursor.pos()


def _native_view(widget):
    """The NSView of the widget's window, or None when Qt is not drawing with Cocoa. The window's
    own view, so no child widget is made native."""
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() != "cocoa":
        return None
    import ctypes

    import objc

    return objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.window().winId())))


def _make_area(options: int, owner):
    """A new NSTrackingArea; the rect is AppKit's to keep with TRACK_IN_VISIBLE_RECT."""
    from AppKit import NSTrackingArea

    return NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(((0.0, 0.0), (0.0, 0.0)), options, owner, None)


def _make_owner(watch: _Watch):
    """The NSObject the tracking area sends mouseEntered: and mouseExited: to, passing them on to
    `watch`. AppKit sends them on the main thread."""
    global _owner_cls
    if _owner_cls is None:
        import objc

        try:
            _owner_cls = objc.lookUpClass(OWNER_CLASS)  # defined already (this module was loaded again)
        except objc.nosuchclass_error:
            from Foundation import NSObject

            class LyssnaMouseEnterWatcher(NSObject):
                def mouseEntered_(self, event):
                    _owner_event(self, "entered")

                def mouseExited_(self, event):
                    _owner_event(self, "exited")

            _owner_cls = LyssnaMouseEnterWatcher
    owner = _owner_cls.alloc().init()
    owner.watch = watch
    return owner


def _owner_event(owner, kind: str) -> None:
    """Never let an error reach AppKit, which would end the app."""
    try:
        w = getattr(owner, "watch", None)
        if w is not None:
            w.entered() if kind == "entered" else w.exited()
    except Exception:
        log.exception("mouse %s handling failed", kind)


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


def prepare_app() -> None:
    """What must happen before the QApplication exists. Run from source, Qt turns the Python
    process into a foreground app as it starts, which brings it to the front at launch even
    when the accessory policy is set right after (hide_dock_icon); this environment variable
    tells Qt not to. The built app's LSUIElement already keeps it in the background."""
    os.environ.setdefault("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM", "1")


def hide_dock_icon() -> None:
    """No Dock icon, like the legacy Lyssna: the accessory activation policy. Run after the
    QApplication exists (and after prepare_app before it), since Qt sets the regular policy
    when it starts from source (the built app's LSUIElement makes it accessory from launch)."""
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


def popups_over(rect) -> list[tuple]:
    """Nothing to find on the Mac (DETECTS_POPUPS is False): the Windows twin answers the rects
    of other apps' pop-ups over `rect`."""
    return []


def front_app() -> str | None:
    """None on the Mac: the play button only learns other apps' pop-ups where it can find them
    (DETECTS_POPUPS), and the Windows twin answers the front app's executable name."""
    return None


def left_button_down() -> bool:
    """Is the left mouse button held right now? Asked of the window server, so a release Qt
    never saw (a drag that ended outside every Qt window) still counts. Off Cocoa (the
    offscreen tests, the preview shots) Qt's own idea of the buttons is all there is."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() == "cocoa":
        try:
            import Quartz

            return bool(Quartz.CGEventSourceButtonState(Quartz.kCGEventSourceStateCombinedSessionState,
                                                        Quartz.kCGMouseButtonLeft))
        except Exception:
            log.debug("could not ask the window server about the mouse button", exc_info=True)
    return bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)
