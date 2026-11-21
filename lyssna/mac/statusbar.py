"""The Mac side of the menu bar item and its menu, for the Qt tray in lyssna/ui/tray.py. Twin of
lyssna/win/statusbar.py; MenuBar in lyssna/legacy/app.py is the specification.

Why a platform module: Qt's QSystemTrayIcon has no title, no template image in the menu bar's
own size, and no section header. The legacy app (rumps) gets all three from AppKit, and so does
this module, on the NSStatusItem and the NSMenu that Qt itself created:

- The reading progress ("2/5", "⏸ 2/5", "…") is the title of Qt's NSStatusBarButton, beside
  the icon, as the legacy app shows it. The button is found among NSApp.windows() (the window
  class NSStatusBarWindow), and its status item through the undocumented key "statusItem", so
  its length can become variable. If either cannot be found, a warning is logged once and the
  text goes to the tooltip instead, as on Windows.
- The icon is menubar.png as a template image (black or white with the menu bar). Once the
  button is found it gets the very NSImage the legacy app gives it (the whole file at 20 x 20
  points), since Qt scales it to 18 x 22.
- "Listen", "Dictate" and the engine groups become real section headers (macOS 14 and later)
  through NSMenuItem's undocumented setter setSectionHeader:, on Qt's own items so Qt keeps
  hiding and showing them. Where the setter is missing the rows stay greyed-out lines, the
  legacy app's own fallback.
- Shortcuts: an item's text after a tab ("Read Selection\\tMeta+Alt+S") becomes the native key
  equivalent (⌃⌥S at the right) without creating a live Qt shortcut. In Qt on a Mac, Meta is
  the Control key and Ctrl is Command.

AppKit is imported inside the functions, so this module imports on any system and the tests
can hand it a fake AppKit. Nothing here raises.

Manual check (opt-in, a few seconds, on a Mac): `LYSSNA_MANUAL_MENUBAR=1 uv run pytest -q
tests/test_ui_tray.py -k manual` shows a real menu bar item for about three seconds with the
title "⏸ 2/5", and checks the template icon, the key equivalents and the section headers in
the native menu."""

from __future__ import annotations

import logging
import subprocess
import weakref
from collections.abc import Collection
from pathlib import Path

log = logging.getLogger("lyssna.mac.statusbar")

ICON_PATH = Path(__file__).resolve().parent.parent / "ui" / "assets" / "menubar.png"
ICON_POINTS = (20, 20)  # the size rumps gives the legacy icon
QUIT_SHORTCUT = "cmd+q"  # Quit shows ⌘Q, as in the legacy menu
CLICK_OPENS_MENU = True  # a click on the item opens its menu natively (Qt still reports it as a Trigger)

# A hotkey spec's parts as Qt's key sequence text, which Qt turns into the native key equivalent.
_QT_MODIFIERS = {"ctrl": "Meta", "control": "Meta", "alt": "Alt", "option": "Alt", "opt": "Alt",
                 "cmd": "Ctrl", "command": "Ctrl", "shift": "Shift"}
_QT_KEYS = {"space": "Space", "return": "Return", "enter": "Return", "tab": "Tab", "escape": "Esc", "esc": "Esc",
            "delete": "Backspace", "backspace": "Backspace", "left": "Left", "right": "Right", "up": "Up", "down": "Down"}


class _Item:
    """What this module found and set on one tray's status item."""

    def __init__(self):
        self.button = None
        self.status_item = None
        self.image = None  # the legacy-sized NSImage, once made
        self.icon_set = False
        self.missing_logged = False
        self.headers_logged = False


_items: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _state(tray) -> _Item:
    if tray not in _items:
        _items[tray] = _Item()
    return _items[tray]


def _status_buttons() -> list:
    """Every NSStatusBarButton in this app's status bar windows, oldest first."""
    import AppKit

    app = AppKit.NSApp
    if app is None:
        return []
    found = []

    def walk(view) -> None:
        if isinstance(view, AppKit.NSStatusBarButton):
            found.append(view)
        for sub in view.subviews() or ():
            walk(sub)

    for window in app.windows() or ():
        if type(window).__name__ == "NSStatusBarWindow" and window.contentView() is not None:
            walk(window.contentView())
    return found


def _button(tray):
    """Qt's NSStatusBarButton for `tray`, made variable length with the image leading, or None.
    The app has one Qt status item; if there are more, the newest is taken."""
    st = _state(tray)
    if st.button is not None and st.button.window() is not None:
        return st.button
    st.button = st.status_item = None
    try:
        import AppKit

        buttons = [b for b in _status_buttons() if type(b.target()).__name__ == "QStatusItemDelegate"]
        if not buttons:
            return None
        button = buttons[-1]
        status_item = button.valueForKey_("statusItem")  # undocumented
        if status_item is None:
            return None
        status_item.setLength_(AppKit.NSVariableStatusItemLength)
        button.setImagePosition_(AppKit.NSImageLeading)
    except Exception:
        log.debug("could not reach the status item", exc_info=True)
        return None
    st.button, st.status_item = button, status_item
    return button


def _legacy_image(st: _Item):
    import AppKit

    if st.image is None:
        image = AppKit.NSImage.alloc().initByReferencingFile_(str(ICON_PATH))
        image.setScalesWhenResized_(True)
        image.setSize_(ICON_POINTS)
        image.setTemplate_(True)
        st.image = image
    return st.image


def set_icon(tray, warning: bool, force: bool = False) -> None:
    """The logo as a template image, which the menu bar draws black or white itself. `warning`
    changes nothing here: the Mac marks it with ⚠︎ beside the icon (see show_status)."""
    st = _state(tray)
    if st.icon_set:
        return
    from PySide6.QtGui import QIcon, QPixmap

    pixmap = QPixmap(str(ICON_PATH))
    pixmap.setDevicePixelRatio(2.0)  # the 40 px file is the 20 point icon
    icon = QIcon(pixmap)
    icon.setIsMask(True)  # Qt makes the NSImage a template
    tray.setIcon(icon)
    st.icon_set = True


def show_status(tray, title: str, tooltip: str) -> None:
    """Show `title` beside the icon (an empty title shows the icon alone). When Qt's button
    cannot be reached, show `tooltip` instead and say so once in the log."""
    st = _state(tray)
    button = _button(tray) if tray.isVisible() else None
    if button is None:
        if tray.isVisible() and not st.missing_logged:
            st.missing_logged = True
            log.warning("could not find the menu bar item's button; the reading progress shows in its tooltip")
        if tray.toolTip() != tooltip:
            tray.setToolTip(tooltip)
        return
    try:
        image = _legacy_image(st)
        if button.image() is None or not button.image().isEqual_(image):
            button.setImage_(image)
        if str(button.title() or "") != title:
            button.setTitle_(title)
    except Exception:
        log.debug("could not set the menu bar title", exc_info=True)
        if tray.toolTip() != tooltip:
            tray.setToolTip(tooltip)
        return
    if tray.toolTip():
        tray.setToolTip("")  # the legacy item has no tooltip


def _mark(nsmenu, titles: Collection[str]) -> None:
    for item in nsmenu.itemArray() or ():
        if item.isSeparatorItem():
            continue
        if item.submenu() is not None:
            _mark(item.submenu(), titles)
        elif str(item.title()) in titles and not item.isSectionHeader():
            item.setSectionHeader_(True)  # undocumented setter of a public property


def mark_headers(tray, titles: Collection[str]) -> None:
    """Make the rows titled `titles` native section headers, in the menu and every submenu Qt
    has attached so far (Qt attaches a submenu when it first opens, so this runs again then).
    Where that is not possible the rows stay greyed-out lines."""
    st = _state(tray)
    if _button(tray) is None or st.status_item is None:
        return
    try:
        import AppKit

        if not AppKit.NSMenuItem.instancesRespondToSelector_("setSectionHeader:"):
            if not st.headers_logged:
                st.headers_logged = True
                log.info("this macOS has no menu section headers; they show as greyed-out lines")
            return
        nsmenu = st.status_item.menu()
        if nsmenu is not None:
            _mark(nsmenu, titles)
    except Exception:
        if not st.headers_logged:
            st.headers_logged = True
            log.warning("could not make the menu's section headers", exc_info=True)


def style_header(action, menu) -> None:
    """Nothing to draw: the native section header styles itself, and its fallback is the plain
    greyed-out line a disabled row already is (as in the legacy menu)."""


def shortcut_text(spec: str) -> str:
    """The text after an item's tab for a hotkey spec: "ctrl+alt+s" -> "Meta+Alt+S", which Qt
    shows as the native ⌃⌥S."""
    from ..hotkeys import tokens

    mods, key = [], ""
    for part in tokens(spec):
        if part in _QT_MODIFIERS:
            mods.append(_QT_MODIFIERS[part])
        else:
            key = _QT_KEYS.get(part, part.upper())
    return "+".join([*mods, key]) if key else ""


def open_text_file(path: Path) -> None:
    """Open a file in the default plain text editor (TextEdit), as `open -t` does."""
    subprocess.run(["open", "-t", str(path)], check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
