"""The menu bar item (the tray icon on Windows) and its menu: the Qt twin of MenuBar in
lyssna/legacy/app.py, which is its specification, on both systems.

The menu is built item for item like the legacy one and asks the same pure rules in
lyssna/menu.py what to show and what to call things. It reads state from the controller on a
0.4 s timer and never owns state itself, exactly as MenuBar does; the handlers below port
MenuBar's rules (the provider checks, keys going to setup, the local-only block, the
remove-key confirmation).

What differs between the systems comes from host, never from a test of the system:
- host.STATUS_TEXT_BESIDE_ICON: the Mac shows the legacy title beside the icon ("2/5",
  "⏸ 2/5", "…", a download line, a short message, ⚠︎). Windows cannot, so its tooltip says it
  in words ("Lyssna: Reading 2 of 5").
- host.NEEDS_ACCESSIBILITY: the warning row asks for the Accessibility permission (the legacy
  "Allow Accessibility Access…"), or says the keyboard shortcuts could not start.
- host.statusbar (lyssna/mac/statusbar.py, lyssna/win/statusbar.py) shows the title, sets the
  icon (a template image on the Mac, tinted for the taskbar on Windows), makes the section
  headers (native on the Mac, a dim semibold row on Windows), writes the shortcut after an
  item's tab (native ⌃⌥S on the Mac, "Ctrl+Alt+S" on Windows; never a live Qt shortcut, since
  the global key tap owns the keys) and opens the config file in a plain text editor.
- A left click on the Windows tray icon opens the same menu a right click does.
- About is one Qt dialog on both systems, with what the legacy About panel shows."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget

from .. import __version__, config, host, support, whatsnew
from ..controller import Notice
from ..dictation import cleaners, transcribers
from ..menu import (
    DICTATE_HEADER,
    KEY_MENU_LABELS,
    LISTEN_HEADER,
    LOCAL_HEADER,
    ONLINE_HEADER,
    REPO_URL,
    MenuState,
    cleanup_title,
    dictation_title,
    download_line,
    engine_row_label,
    local_only_title,
    menubar_text_allowed,
    now_playing,
    privacy_symbol,
    provider_title,
    separators_shown,
    visibility,
)
from ..player import SPEEDS, Status
from ..providers import REGISTRY, allowed
from . import icons, theme
from .layout.setup import DICTATION_KEY_ENGINES
from .mainthread import on_main
from .mark import MarkView

log = logging.getLogger("lyssna.ui.tray")

TICK_MS = 400
HOOKS_WARNING_TITLE = "Lyssna could not start its keyboard shortcuts"
ACCESSIBILITY_WARNING_TITLE = "Allow Accessibility Access…"  # the legacy menu's row
STATE_TEXT = {"loading": "…", "playing": "", "paused": "⏸ "}  # the legacy menu bar title, before "2/5"
WARNING_MARK = "⚠︎"  # beside the icon while the permission is missing
TITLE_MAX = 60  # characters of a message beside the icon, as the legacy app
NOW_PLAYING_MAX = 80  # characters of the sentence in the now-playing line; the Mac caps a message at the same
MENU_ICON = 16.0      # the privacy, warning and heart icons' image in the menu; the symbol inside is host.statusbar.MENU_SYMBOL_POINTS
CREDITS_PT = 11       # the About panel's credits, as show_about in lyssna/legacy/app.py sets them


# -- pure helpers ------------------------------------------------------------------------------


def warning_title() -> str:
    """The row at the top of the menu when the keyboard and selection access is missing."""
    return ACCESSIBILITY_WARNING_TITLE if host.NEEDS_ACCESSIBILITY else HOOKS_WARNING_TITLE


def menubar_title(player: str, index: int, total: int, *, dictating: bool = False, download: str = "",
                  message: str = "", warning: bool = False) -> str:
    """What the legacy app shows beside the menu bar icon (_tick in lyssna/legacy/app.py):
    nothing while a dictation records or works (the pill is the indicator), else the download
    line, else a message, else "…" loading, "2/5" reading, "⏸ 2/5" paused. ⚠︎ when there is
    nothing else to show and the permission is missing."""
    if dictating:
        return ""
    if download:
        return download
    if message:
        return message[:TITLE_MAX]
    if player in STATE_TEXT:
        return "…" if player == "loading" else f"{STATE_TEXT[player]}{index}/{total}"
    return WARNING_MARK if warning else ""


def status_line(player: str, index: int, total: int) -> str:
    """The menu bar title in words, for the tooltip: "Reading 2 of 5"."""
    if player == "loading":
        return "Loading"
    if player in ("playing", "paused"):
        return f"{'Reading' if player == 'playing' else 'Paused'} {index} of {total}"
    return ""


def tooltip_text(line: str, warning: bool = False) -> str:
    """The icon's tooltip where nothing shows beside it: "Lyssna", or "Lyssna: " and what the
    Mac's title says, in words."""
    if line:
        return f"Lyssna: {line}"
    return warning_title() if warning else "Lyssna"


def short(text: str, limit: int = NOW_PLAYING_MAX) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def label_of(action: QAction) -> str:
    """An item's title without the hotkey after the tab."""
    return action.text().split("\t", 1)[0]


def tidy_separators(menu: QMenu) -> None:
    """Hide the separators of a menu that would double up or dangle around hidden items."""
    actions = menu.actions()
    rows = [None if a.isSeparator() else a.isVisible() for a in actions]
    separators = [a for a in actions if a.isSeparator()]
    for sep, show in zip(separators, separators_shown(rows), strict=True):
        if sep.isVisible() != show:
            sep.setVisible(show)


def confirm(title: str, message: str, ok: str, cancel: str = "Cancel") -> bool:
    """A question with two buttons (the Mac's rumps.alert). True when `ok` was clicked."""
    box = QMessageBox()
    box.setWindowTitle("Lyssna")
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setText(title)
    box.setInformativeText(message)
    yes = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton(cancel, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)  # a tray app is never in front on its own
    box.exec()
    return box.clickedButton() is yes


# -- About -------------------------------------------------------------------------------------


class AboutDialog(QDialog):
    """Name, version, license and a link to the source, on the brand surface: the one About on
    both systems, with what the Mac's standard About panel shows as show_about in
    lyssna/legacy/app.py fills it (the credits, the sentence and the link, at its 11 points)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("About Lyssna")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        col = QVBoxLayout(self)
        col.setContentsMargins(40, 28, 40, 24)
        col.setSpacing(0)
        self.mark = MarkView(30, self)
        col.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(12)
        self.name = QLabel("Lyssna", self)
        self.name.setFont(theme.serif_font(22))
        col.addWidget(self.name, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(4)
        self.version = QLabel(f"Version {__version__}", self)
        self.version.setFont(theme.ui_font(12))
        col.addWidget(self.version, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(14)
        self.license = QLabel("Free and open source, GPL-3.0", self)
        self.license.setFont(theme.ui_font(CREDITS_PT))
        col.addWidget(self.license, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(2)
        self.link = QLabel(self)
        self.link.setFont(theme.ui_font(CREDITS_PT))
        self.link.setTextFormat(Qt.TextFormat.RichText)
        self.link.setOpenExternalLinks(False)
        self.link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.link.linkActivated.connect(lambda url: host.open_target(url))
        col.addWidget(self.link, 0, Qt.AlignmentFlag.AlignHCenter)
        self._unsubscribe = theme.on_scheme_change(self._restyle)
        self._restyle()
        self.adjustSize()
        self.setFixedSize(max(300, self.width()), self.height())

    def _restyle(self) -> None:
        def css(c: QColor) -> str:
            return f"color: {c.name(QColor.NameFormat.HexArgb)}; background: transparent;"
        self.name.setStyleSheet(css(theme.content()))
        self.version.setStyleSheet(css(theme.content_dim()))
        self.license.setStyleSheet(css(theme.content()))
        self.link.setStyleSheet(css(theme.content()))
        self.link.setText(f'<a href="{REPO_URL}" style="color: {theme.content().name()};">{REPO_URL.removeprefix("https://")}</a>')
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.surface())
        p.end()


_about: AboutDialog | None = None


def show_about() -> None:
    """Open the About dialog (one at a time) and bring it to the front."""
    global _about
    if _about is None:
        _about = AboutDialog()
    _about.show()
    _about.raise_()
    _about.activateWindow()


# -- the tray ----------------------------------------------------------------------------------


class Tray:
    """Tray icon + menu. Reads state from the controller on a timer; never owns state itself.

    The menu, top to bottom (optional rows in brackets; lyssna.menu has the rules):

        [Allow Accessibility Access... / Lyssna could not start its keyboard shortcuts]
        Listen (a section header)
          Read Selection, Read Clipboard, [now playing],
          [Pause / Resume, Stop, Previous Sentence, Next Sentence] (only while reading),
          Speed >, [Voice >], Voice Provider >, where the text goes
        Dictate (a section header)
          Start Dictation, Paste Last Dictation, History..., Transcription >, Cleanup >,
          where speech goes]
        [What's New in 0.3.0...], [usage this month], Settings >, Setup..., About Lyssna,
        [Support Lyssna]
        Quit Lyssna (⌘Q on the Mac)

    with a separator after each section and before Quit, hidden when the rows around them are."""

    # How a hotkey spec is written after an item's tab, and how a header row is styled. Class
    # attributes so a preview can show the other system's way.
    key_text: Callable[[str], str] = staticmethod(lambda spec: host.statusbar.shortcut_text(spec))
    style_header: Callable[[QAction, QMenu], None] = staticmethod(lambda a, menu: host.statusbar.style_header(a, menu))

    def __init__(self, ctl):
        self.ctl = ctl
        self.hk = ctl.cfg.get("hotkeys", {})
        self.on_quit: Callable[[], None] | None = None
        self.on_setup: Callable[[str | None], None] = lambda engine: None  # opens the setup window; set by the app
        self.on_setup_dictation: Callable[[str], None] = lambda engine: None  # ...at its dictation keys
        self.on_history: Callable[[], None] = lambda: None
        self.on_auto_hide: Callable[[bool], None] = lambda on: None
        self.on_wave: Callable[[bool], None] = lambda on: None
        self.on_whats_new: Callable[[], None] = lambda: None
        self.whats_new_pending = False  # an update's note is waiting; set by the app at launch
        self.warning = False  # the keyboard and selection access is missing
        self.title = ""  # what the legacy app shows beside the icon now
        self.line = ""  # the same, in words, for the tooltip
        self._downloading = False
        self._header_titles: set[str] = set()  # every section header's title, made native where the system can
        self._engine_groups: list[tuple[QAction, list[QAction]]] = []  # header, its rows
        self._privacy_symbols: dict[str, str] = {}  # "reading" / "dictation": the symbol shown now
        self._icons: dict[QAction, str] = {}  # menu items with an icon, re-tinted when the scheme flips
        self._shown_speed = None

        self.menu = QMenu()
        self.menu.setToolTipsVisible(False)
        self._build()
        self.tray = QSystemTrayIcon()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self._update_icon()
        self.tray.setToolTip(tooltip_text(""))
        self._refresh_checks()
        self.menu.aboutToShow.connect(self._tick)  # never shows a stale row
        # Native headers go on the native items, which exist once a menu has opened.
        for menu in (self.menu, self.provider_menu, self.transcription_menu, self.cleanup_menu):
            menu.aboutToShow.connect(self._mark_headers)
        self._unsubscribe = theme.on_scheme_change(self._scheme_changed)
        # The saved keys are read in the background at launch; until then the menu shows
        # engines as set up. When they land, the menu catches up with the real state.
        ctl.subscribe_keys(lambda: on_main(self._refresh_checks))
        self.timer = QTimer()
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # -- building --------------------------------------------------------------------------

    def _build(self) -> None:
        m = self.menu
        self.item_warning = self._action(m, warning_title(), lambda: self.on_setup(None))
        self._set_icon(self.item_warning, "exclamationmark.triangle")
        self.item_warning.setVisible(False)
        self.header_listen = self._header(m, LISTEN_HEADER)
        self._build_reading(m)
        self._build_transport(m)
        self._build_voice(m)
        m.addSeparator()
        self.header_dictate = self._header(m, DICTATE_HEADER)
        self._build_dictation(m)
        m.addSeparator()
        self._build_app(m)
        m.addSeparator()
        self.item_quit = self._action(m, "Quit Lyssna", self.quit, spec=host.statusbar.QUIT_SHORTCUT)

    def _action(self, menu: QMenu, title: str, fn: Callable[[], None] | None = None, hotkey: str = "",
                checkable: bool = False, spec: str = "") -> QAction:
        """An item running `fn`, showing the configured shortcut for `hotkey` (or the hotkey
        `spec`) if given. Without `fn` it is a greyed-out, non-clickable line, as an item without
        a callback is on the Mac."""
        spec = str(self.hk.get(hotkey, "")) if hotkey else spec
        keys = self.key_text(spec) if spec else ""
        a = QAction(f"{title}\t{keys}" if keys else title, menu)
        a.setMenuRole(QAction.MenuRole.NoRole)  # never moved to a Mac application menu by its title
        a.setCheckable(checkable)
        if fn is None:
            a.setEnabled(False)
        else:
            a.triggered.connect(lambda _checked=False: fn())
        menu.addAction(a)
        return a

    def _header(self, menu: QMenu, title: str) -> QAction:
        """A titled section of a menu (section_header in the legacy app): a disabled row that
        host.statusbar makes a native header (Mac) or styles as one (Windows)."""
        a = QAction(title, menu)
        a.setMenuRole(QAction.MenuRole.NoRole)
        a.setEnabled(False)
        a.setData("header")
        self.style_header(a, menu)
        self._header_titles.add(title)
        menu.addAction(a)
        return a

    def _submenu(self, parent: QMenu, title: str) -> QMenu:
        sub = QMenu(title, parent)
        sub.menuAction().setMenuRole(QAction.MenuRole.NoRole)
        parent.addMenu(sub)
        return sub

    def _build_reading(self, m: QMenu) -> None:
        self.item_read = self._action(m, "Read Selection", self.ctl.speak_selection, "speak")
        self.item_clipboard = self._action(m, "Read Clipboard", self.ctl.speak_clipboard)
        self.item_now = self._action(m, "Nothing playing")

    def _build_transport(self, m: QMenu) -> None:
        self.transport_items = [
            self._action(m, "Pause / Resume", self.ctl.toggle_pause, "pause"),
            self._action(m, "Stop", self.ctl.stop, "stop"),
            self._action(m, "Previous Sentence", self.ctl.prev, "prev"),
            self._action(m, "Next Sentence", self.ctl.next, "next"),
        ]

    def _build_voice(self, m: QMenu) -> None:
        self.speed_menu = self._submenu(m, "Speed")
        self._action(self.speed_menu, "Faster", lambda: self._step_speed(self.ctl.faster), "faster")
        self._action(self.speed_menu, "Slower", lambda: self._step_speed(self.ctl.slower), "slower")
        self.speed_menu.addSeparator()
        self.speed_items: dict[float, QAction] = {}
        for s in SPEEDS:
            self.speed_items[s] = self._action(self.speed_menu, f"{s:g}×", lambda s=s: self._set_speed(s), checkable=True)

        # One entry per voice any provider offers; _refresh_voices shows the current provider's.
        self.voice_menu = self._submenu(m, "Voice")
        self.voice_items: list[QAction] = []
        for voice, text in dict(v for cls in REGISTRY.values() for v in cls.voices).items():
            self.voice_items.append(self._action(self.voice_menu, text, lambda v=voice: self._set_voice(v), checkable=True))

        self.provider_menu = self._submenu(m, "Voice Provider")
        self.provider_items = self._add_engines(self.provider_menu, REGISTRY, lambda cls: not cls.sends_text_off_device,
                                                self._set_provider)
        self.item_privacy = self._action(m, self.ctl.privacy_note())

    def _build_dictation(self, m: QMenu) -> None:
        self.item_dictate = self._action(m, self._dictate_title(), self._dictate)
        self.item_paste_last = self._action(m, "Paste Last Dictation", self.ctl.paste_last, "paste_last")
        self.item_history = self._action(m, "History…", lambda: self.on_history())
        self.transcription_menu = self._submenu(m, "Transcription")
        self.transcriber_items = self._add_engines(self.transcription_menu, transcribers.REGISTRY,
                                                   lambda cls: not cls.sends_audio_off_device, self._set_transcriber)
        self.cleanup_menu = self._submenu(m, "Cleanup")
        self.cleanup_items = {"": self._action(self.cleanup_menu, "Off", lambda: self._set_cleaner(""), checkable=True)}
        self.cleanup_items.update(self._add_engines(self.cleanup_menu, cleaners.REGISTRY,
                                                    lambda cls: not cls.sends_text_off_device, self._set_cleaner))
        self.item_dictation_privacy = self._action(m, self.ctl.dictation_privacy_note())

    def _add_engines(self, menu: QMenu, registry: dict, runs_here: Callable[[type], bool],
                     choose: Callable[[str], None]) -> dict[str, QAction]:
        """Fill an engine picker: the engines on this computer under one header, the online
        ones under another, each group in registry order. Returns the rows by engine name."""
        items = {}
        for header, local in ((LOCAL_HEADER, True), (ONLINE_HEADER, False)):
            names = [name for name, cls in registry.items() if runs_here(cls) == local]
            if not names:
                continue
            head = self._header(menu, header)
            for name in names:
                items[name] = self._action(menu, engine_row_label(registry[name].label), lambda n=name: choose(n),
                                           checkable=True)
            self._engine_groups.append((head, [items[name] for name in names]))
        return items

    def _build_app(self, m: QMenu) -> None:
        # Both hidden until _apply_visibility says otherwise: an update's note, and the support
        # link once the website has an address. Neither ever opens on its own.
        self.item_whats_new = self._action(m, whatsnew.menu_title(__version__), self._open_whats_new)
        self.item_usage = self._action(m, "No usage this month")
        self._build_settings(m)
        self.item_setup = self._action(m, "Setup…", lambda: self.on_setup(None))
        self.item_about = self._action(m, "About Lyssna", show_about)
        self.item_support = self._action(m, support.MENU_TITLE, lambda: support.open_support("menu"))
        self._set_icon(self.item_support, support.SYMBOL)

    def _build_settings(self, m: QMenu) -> None:
        self.settings_menu = s = self._submenu(m, "Settings")
        self.item_login = None
        if getattr(sys, "frozen", False):  # from source this would register the wrong program
            self.item_login = self._action(s, "Start at Login", self._toggle_login, checkable=True)
            self.item_login.setChecked(host.autostart.is_enabled())
        self.item_button = self._action(s, "Play Button on Selection", self._toggle_button, "button", checkable=True)
        self.item_auto_hide = self._action(s, "Auto-hide Squiggle", self._toggle_auto_hide, checkable=True)
        self.item_wave = self._action(s, "Squiggle Wave", self._toggle_wave, checkable=True)
        self.item_local = self._action(s, local_only_title(False), self._toggle_local_only, checkable=True)
        self._build_api_keys(s)
        s.addSeparator()
        self._action(s, "Edit Config File…", lambda: host.statusbar.open_text_file(config.CONFIG_PATH))
        self._action(s, "Open Log", lambda: host.open_target(str(config.LOG_DIR / "lyssna.log")))
        self._action(s, "Clear Audio Cache", lambda: self.ctl.reader.cache.clear())

    def _build_api_keys(self, s: QMenu) -> None:
        self.key_menu = self._submenu(s, "API Keys")
        self.key_engines: dict[str, str] = {}  # one provider per config section: the one its key entries act on
        for name, cls in REGISTRY.items():
            if cls.needs_key and cls.section() not in self.key_engines:
                self.key_engines[cls.section()] = name
        self.key_items: dict[str, QAction] = {}
        for name in self.key_engines.values():
            self.key_items[name] = self._action(self.key_menu, f"{self._key_label(name)}…", lambda n=name: self._prompt_for_key(n))
        self.key_menu.addSeparator()  # hidden by tidy_separators while no key is stored
        self.remove_items: dict[str, QAction] = {}
        for name in self.key_engines.values():
            self.remove_items[name] = self._action(self.key_menu, f"Remove {self._key_label(name)} Key…",
                                                   lambda n=name: self._remove_key(n))

    # -- icons -----------------------------------------------------------------------------

    def _icon_color(self) -> QColor:
        return self.menu.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)

    def _set_icon(self, action: QAction, symbol: str) -> None:
        """Show an icon in the menu's text colour in front of an item's title. It is a mask too,
        so a native Mac menu draws it as a template image, as set_symbol does in the legacy app."""
        self._icons[action] = symbol
        # Qt gives a native Mac menu item's image the 16 pt icon size, so the symbol is drawn at
        # its own size inside a MENU_ICON square rather than scaled to fill it.
        icon = QIcon(icons.icon_pixmap(symbol, host.statusbar.MENU_SYMBOL_POINTS, self._icon_color(), "regular",
                                       box=MENU_ICON))
        icon.setIsMask(True)
        action.setIcon(icon)

    def _scheme_changed(self) -> None:
        for action, symbol in list(self._icons.items()):
            self._set_icon(action, symbol)
        self._update_icon(force=True)

    def _update_icon(self, force: bool = False) -> None:
        """The icon: a template image on the Mac, re-tinted on Windows when the taskbar's mode
        changes or the warning comes on (host.statusbar decides, and redraws only on a change)."""
        host.statusbar.set_icon(self.tray, self.warning, force)

    def _mark_headers(self) -> None:
        host.statusbar.mark_headers(self.tray, self._header_titles)

    def _show_status(self) -> None:
        """The title beside the icon where the system can show it, else the tooltip in words."""
        title = self.title if host.STATUS_TEXT_BESIDE_ICON else ""
        host.statusbar.show_status(self.tray, title, tooltip_text(self.line, self.warning))

    # -- actions ---------------------------------------------------------------------------

    def _activated(self, reason) -> None:
        """A left click opens the same menu a right click does, where the system does not
        already (on the Mac it does, and a second popup would open the menu twice)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger and not host.statusbar.CLICK_OPENS_MENU:
            self.menu.popup(QCursor.pos())

    def _set_speed(self, speed: float) -> None:
        self.ctl.set_speed(speed)
        self._refresh_checks()

    def _step_speed(self, step: Callable[[], None]) -> None:
        step()
        self._refresh_checks()

    def _set_voice(self, name: str) -> None:
        self.ctl.set_voice(name)
        self._refresh_checks()

    def _toggle_button(self) -> None:
        self.ctl.set_selection_button(not self.ctl.selection_button_enabled)
        self._refresh_checks()

    def _toggle_auto_hide(self) -> None:
        self.ctl.set_auto_hide(not self.ctl.auto_hide_enabled)
        self.on_auto_hide(self.ctl.auto_hide_enabled)
        self._refresh_checks()

    def _toggle_wave(self) -> None:
        self.ctl.set_wave(not self.ctl.wave_enabled)
        self.on_wave(self.ctl.wave_enabled)
        self._refresh_checks()

    def _toggle_local_only(self) -> None:
        if self.ctl.local_only_managed():  # the row is greyed out then; this is a second guard
            self._refresh_checks()
            return
        self.ctl.set_local_only(not self.ctl.local_only())
        self._refresh_checks()

    def _set_provider(self, name: str) -> None:
        if not allowed(name, self.ctl.cfg):
            self.ctl.notify(f"{local_only_title(False)} is on", ttl=3.0)
            self._refresh_checks()
            return
        if not REGISTRY[name].can_run():
            self.ctl.notify(f"{REGISTRY[name].label} is not available in this build")
            self._refresh_checks()
            return
        if REGISTRY[name].needs_key and not self.ctl.provider_configured(name):
            self._prompt_for_key(name)
            self._refresh_checks()
            return
        self.ctl.set_provider(name)  # Kokoro starts downloading its voice here if it has to
        self._refresh_checks()

    def _dictate(self) -> None:
        """Start a hands-free dictation from the menu, or finish the one recording."""
        if self.ctl.dictation.state in ("recording", "locked"):
            self.ctl.dictation_stop()
        else:
            self.ctl.dictation_start(hands_free=True)

    def _set_transcriber(self, name: str) -> None:
        cls = transcribers.REGISTRY[name]
        if not transcribers.allowed(name, self.ctl.cfg):
            self.ctl.notify(f"{local_only_title(False)} is on", ttl=3.0)
        elif not cls.can_run():
            self.ctl.notify(f"{cls.label} is not available in this build")
        elif cls.needs_key and not self.ctl.transcriber_configured(name):
            self._ask_for_dictation_key(name)
        else:
            self.ctl.set_transcriber(name)  # the model on this computer starts downloading here if it has to
        self._refresh_checks()

    def _set_cleaner(self, name: str) -> None:
        if name:
            cls = cleaners.REGISTRY[name]
            if not cleaners.allowed(name, self.ctl.cfg):
                self.ctl.notify(f"{local_only_title(False)} is on", ttl=3.0)
                self._refresh_checks()
                return
            if not cls.can_run():
                self.ctl.notify(f"{cls.label} is not available in this build")
                self._refresh_checks()
                return
            if cls.needs_key and not self.ctl.cleaner_configured(name):
                self._ask_for_dictation_key(name)
                self._refresh_checks()
                return
        self.ctl.set_cleaner(name)
        self._refresh_checks()

    def _ask_for_dictation_key(self, name: str) -> None:
        """A dictation engine needs a key: the setup window's dictation step takes it for the
        engines it lists (OpenAI, Anthropic); for another one that shares its key with a voice
        (Inworld), the voice step does; otherwise a notice says where to add it."""
        cls = transcribers.REGISTRY.get(name) or cleaners.REGISTRY[name]
        if name in DICTATION_KEY_ENGINES:
            self.on_setup_dictation(name)
        elif cls.section() in self.key_engines:
            self.on_setup(self.key_engines[cls.section()])
        else:
            self.ctl.toast(Notice(f"Add your {cls.vendor} key in Setup"))

    def _prompt_for_key(self, name: str) -> None:
        """Adding a key happens in the setup window, with that provider picked under "Use my own API key"."""
        self.on_setup(name)

    @staticmethod
    def _key_label(name: str) -> str:
        cls = REGISTRY[name]
        return KEY_MENU_LABELS.get(cls.section(), cls.label)

    def _remove_key(self, name: str) -> None:
        label = self._key_label(name)
        if not confirm(f"Remove your {label} API key?", "You can add it again any time.", ok="Remove"):
            return
        self.ctl.remove_api_key(name)
        self._refresh_checks()

    def _open_whats_new(self) -> None:
        """Opening the note is what deals with it: the row goes and does not come back."""
        self.whats_new_pending = False
        whatsnew.mark_seen(__version__)
        self._apply_visibility()
        self.on_whats_new()

    def _toggle_login(self) -> None:
        host.autostart.set_enabled(not host.autostart.is_enabled())
        if self.item_login is not None:
            self.item_login.setChecked(host.autostart.is_enabled())

    def show_permission_warning(self) -> None:
        """The keyboard and selection access is missing: say so at the top of the menu (it
        opens setup), and beside the icon (⚠︎) or with a warning dot on it."""
        self.warning = True
        self.item_warning.setVisible(True)
        self._update_icon(force=True)
        self._tick()

    def show(self) -> None:
        self.tray.show()
        self._show_status()
        self._mark_headers()

    def quit(self) -> None:
        """Call the app's quit, then end the Qt loop. exit(0), not quit(): Qt 6 lets a window
        refuse quit(), and the Lyssna windows do (closing one only hides it)."""
        if self.on_quit:
            self.on_quit()
        self.timer.stop()
        self.tray.hide()
        app = QApplication.instance()
        if app is not None:
            app.exit(0)

    # -- refreshing ------------------------------------------------------------------------

    def _refresh_checks(self) -> None:
        """Bring every checkmark, title and optional row in line with the controller."""
        self._refresh_speed()
        self._refresh_dictation()
        self._refresh_providers()
        self._refresh_engine_headers()
        self._refresh_voices()
        self._refresh_settings()
        self._apply_visibility()

    def _refresh_speed(self) -> None:
        speed = self.ctl.reader.speed
        for s, item in self.speed_items.items():
            item.setChecked(abs(s - speed) < 0.01)
        self.speed_menu.setTitle(f"Speed: {speed:g}×")
        self._shown_speed = speed

    def _refresh_providers(self) -> None:
        current = self.ctl.cfg.get("provider")
        for name, cls in REGISTRY.items():
            item = self.provider_items[name]
            item.setChecked(name == current)
            item.setVisible(allowed(name, self.ctl.cfg))  # online providers hide while local-only mode is on
            hint = cls.setup_hint if cls.can_run() else "not available"
            item.setText(provider_title(engine_row_label(cls.label), self.ctl.provider_configured(name), hint))
        self.provider_menu.setTitle(self._provider_title())
        self.item_privacy.setText(self.ctl.privacy_note())
        cls = REGISTRY.get(current)
        self._show_privacy_symbol("reading", self.item_privacy, cls is None or not cls.sends_text_off_device)

    def _refresh_dictation(self) -> None:
        cfg = self.ctl.cfg
        current = self.ctl.current_transcriber()
        for name, cls in transcribers.REGISTRY.items():
            item = self.transcriber_items[name]
            item.setChecked(name == current)
            item.setVisible(transcribers.allowed(name, cfg))  # online engines hide while local-only mode is on
            hint = cls.setup_hint if cls.can_run() else "not available"
            item.setText(provider_title(engine_row_label(cls.label), self.ctl.transcriber_configured(name), hint))
        self.transcription_menu.setTitle(self._transcription_title())
        current = self.ctl.current_cleaner()
        self.cleanup_items[""].setChecked(not current)
        for name, cls in cleaners.REGISTRY.items():
            item = self.cleanup_items[name]
            item.setChecked(name == current)
            item.setVisible(cleaners.allowed(name, cfg))
            hint = cls.setup_hint if cls.can_run() else "not available"
            item.setText(provider_title(engine_row_label(cls.label), self.ctl.cleaner_configured(name), hint))
        self.cleanup_menu.setTitle(self._cleanup_title())
        self.item_dictation_privacy.setText(self.ctl.dictation_privacy_note())
        self._show_privacy_symbol("dictation", self.item_dictation_privacy, self.ctl.dictation_stays_local())

    def _refresh_engine_headers(self) -> None:
        """A picker's header hides when every engine under it does (online ones in local-only mode)."""
        for header, rows in self._engine_groups:
            header.setVisible(any(row.isVisible() for row in rows))

    def _show_privacy_symbol(self, which: str, item: QAction, stays_local: bool) -> None:
        """Put the lock or the arrow out in front of a privacy line, only when it changes."""
        symbol = privacy_symbol(stays_local)
        if self._privacy_symbols.get(which) != symbol:
            self._privacy_symbols[which] = symbol
            self._set_icon(item, symbol)

    def _transcription_title(self) -> str:
        cls = transcribers.REGISTRY.get(self.ctl.current_transcriber())
        return f"Transcription: {cls.label if cls else 'Not available'}"

    def _cleanup_title(self) -> str:
        cls = cleaners.REGISTRY.get(self.ctl.current_cleaner())
        return cleanup_title(cls.label if cls else "")

    def _dictate_title(self) -> str:
        return dictation_title(str(self.ctl.cfg.get("dictation", {}).get("hotkey", host.DICTATION_KEY_DEFAULT)),
                               recording=self.ctl.dictation.state in ("recording", "locked"))

    def _refresh_voices(self) -> None:
        self.voice_menu.setTitle(self._voice_title())
        voices = dict(self.ctl.voices())
        voice = self.ctl.current_voice()
        for item in self.voice_items:
            vid = next((v for v, text in voices.items() if text == item.text()), None)
            item.setChecked(vid is not None and vid == voice)
            item.setVisible(vid is not None)  # hidden: belongs to another provider's list

    def _refresh_settings(self) -> None:
        self.item_button.setChecked(self.ctl.selection_button_enabled)
        self.item_auto_hide.setChecked(self.ctl.auto_hide_enabled)
        self.item_wave.setChecked(self.ctl.wave_enabled)
        managed = self.ctl.local_only_managed()
        self.item_local.setChecked(self.ctl.local_only())
        self.item_local.setText(local_only_title(managed))
        self.item_local.setEnabled(not managed)  # greyed out, as the Mac's item without a callback
        for name, item in self.remove_items.items():
            item.setVisible(self.ctl.has_stored_key(name))

    def _apply_visibility(self) -> None:
        """Show or hide the optional rows (cheap: runs on every tick), then fix the separators."""
        usage_line = self.ctl.usage_summary()
        if usage_line and self.item_usage.text() != usage_line:
            self.item_usage.setText(usage_line)
        state = MenuState(player=self.ctl.status.state, downloading=self._downloading, message=self.ctl.message,
                          has_voices=bool(self.ctl.voices()), usage_line=usage_line, local_only=self.ctl.local_only(),
                          dictation=self.ctl.dictation.state, whats_new=self.whats_new_pending,
                          support=support.enabled())
        shown = visibility(state)
        for item, key in ((self.item_now, "now_playing"), *((t, "transport") for t in self.transport_items),
                          (self.voice_menu.menuAction(), "voice"), (self.item_usage, "usage"),
                          (self.key_menu.menuAction(), "api_keys"), (self.item_whats_new, "whats_new"),
                          (self.item_support, "support")):
            if item.isVisible() != shown[key]:
                item.setVisible(shown[key])
        if self.item_warning.isVisible() != self.warning:
            self.item_warning.setVisible(self.warning)
        for menu in (self.menu, self.settings_menu, self.key_menu):
            tidy_separators(menu)

    def _tick(self) -> None:
        st: Status = self.ctl.status
        what, download = "voice", self.ctl.voice_download_progress()
        if download is None:
            what, download = "model", self.ctl.dictation_download_progress()
        if self._downloading and download is None:  # just ended: the provider entry may lose its "download" hint
            self._refresh_checks()
        self._downloading = download is not None
        dictating = not menubar_text_allowed(MenuState(dictation=self.ctl.dictation.state))
        if self.item_dictate.text() != self._dictate_title():
            self.item_dictate.setText(self._dictate_title())
        line = download_line(what, download) if download is not None else ""
        self.title = menubar_title(st.state, st.index, st.total, dictating=dictating, download=line,
                                   message=self.ctl.message, warning=self.warning)
        if dictating:  # the pill is the indicator
            self.line = ""
        elif line:  # a reading asked for meanwhile starts once this finishes
            self.line = line
            self.item_now.setText(line)
        elif self.ctl.message:
            self.line = self.ctl.message[:TITLE_MAX]
            self.item_now.setText(self.ctl.message[:NOW_PLAYING_MAX])
        elif st.state == "idle":
            self.line = ""
        else:
            self.line = status_line(st.state, st.index, st.total)
            self.item_now.setText(short(now_playing(st.state, st.index, st.total, st.text)))
        self._show_status()
        self._update_icon()
        if self.item_button.isChecked() != bool(self.ctl.selection_button_enabled):
            self._refresh_checks()
        elif self._shown_speed is None or abs(self.ctl.reader.speed - self._shown_speed) > 0.01:
            self._refresh_checks()
        elif self.voice_menu.title() != self._voice_title():  # changed from the player badge or the socket
            self._refresh_checks()
        elif self.provider_menu.title() != self._provider_title():  # e.g. local-only mode switched it
            self._refresh_checks()
        elif (self.transcription_menu.title() != self._transcription_title()
              or self.cleanup_menu.title() != self._cleanup_title()):
            self._refresh_checks()
        else:
            self._apply_visibility()

    def _voice_title(self) -> str:
        label = dict(self.ctl.voices()).get(self.ctl.current_voice(), self.ctl.current_voice())
        return f"Voice: {label.split(' · ')[0]}"

    def _provider_title(self) -> str:
        return f"Voice Provider: {self.ctl.provider_label()}"


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------


class _PreviewController:
    """Just enough of a Controller to draw the menu, so a preview never reads the real config,
    the saved keys or the network."""

    def __init__(self, **state):
        import tomllib
        from types import SimpleNamespace

        self.cfg = tomllib.loads(config.TEMPLATE)
        self.cfg["provider"] = state.get("provider", "kokoro")
        self.reader = SimpleNamespace(speed=1.2, cache=SimpleNamespace(clear=lambda: None))
        self.status = state.get("status", Status())
        self.message = ""
        self.dictation = SimpleNamespace(state="idle")
        self.selection_button_enabled = True
        self.auto_hide_enabled = True
        self.wave_enabled = True
        self._local = state.get("local_only", False)
        self._managed = state.get("managed", False)
        self._keys = set(state.get("keys", ()))
        self._usage = state.get("usage", "")

    def subscribe_keys(self, fn) -> None:
        pass

    def voice_download_progress(self):
        return None

    def dictation_download_progress(self):
        return None

    def usage_summary(self) -> str:
        return self._usage

    def local_only(self) -> bool:
        return self._local or self._managed

    def local_only_managed(self) -> bool:
        return self._managed

    def provider_configured(self, name: str) -> bool:
        cls = REGISTRY[name]
        return not cls.needs_key or cls.section() in self._keys

    def transcriber_configured(self, name: str) -> bool:
        cls = transcribers.REGISTRY[name]
        return not cls.needs_key or cls.section() in self._keys

    def cleaner_configured(self, name: str) -> bool:
        cls = cleaners.REGISTRY[name]
        return not cls.needs_key or cls.section() in self._keys

    def has_stored_key(self, name: str) -> bool:
        return REGISTRY[name].section() in self._keys

    def current_transcriber(self) -> str:
        return transcribers.LOCAL

    def current_cleaner(self) -> str:
        return cleaners.LOCAL

    def privacy_note(self) -> str:
        cls = REGISTRY.get(self.cfg["provider"])
        return f"Text stays on this {host.DEVICE}" if cls is None or not cls.sends_text_off_device else f"Text is sent to {cls.vendor}"

    def dictation_privacy_note(self) -> str:
        return f"Speech stays on this {host.DEVICE}"

    def dictation_stays_local(self) -> bool:
        return True

    def provider_label(self) -> str:
        cls = REGISTRY.get(self.cfg["provider"])
        return cls.label if cls else "?"

    def voices(self):
        cls = REGISTRY.get(self.cfg["provider"])
        return list(cls.voices) if cls else []

    def current_voice(self) -> str:
        cls = REGISTRY.get(self.cfg["provider"])
        return (cls.default_voice or "") if cls else ""

    def __getattr__(self, name):  # every action: does nothing in a preview
        if name.startswith(("set_", "speak_", "toggle_", "dictation_", "remove_", "notify", "toast", "stop", "next", "prev",
                            "faster", "slower", "paste_")):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


def _preview_palette(menu: QMenu) -> None:
    """The offscreen platform has no dark system palette; give the preview's menus one so the
    dark shots show what Windows draws in dark mode."""
    if not theme.is_dark():
        return
    pal = QPalette(menu.palette())
    for role, color in ((QPalette.ColorRole.Window, QColor(0x2C, 0x2C, 0x2C)), (QPalette.ColorRole.Base, QColor(0x2C, 0x2C, 0x2C)),
                        (QPalette.ColorRole.WindowText, QColor(0xF2, 0xF2, 0xF2)), (QPalette.ColorRole.Text, QColor(0xF2, 0xF2, 0xF2)),
                        (QPalette.ColorRole.ButtonText, QColor(0xF2, 0xF2, 0xF2)), (QPalette.ColorRole.Button, QColor(0x2C, 0x2C, 0x2C)),
                        (QPalette.ColorRole.Highlight, QColor(0x45, 0x45, 0x45)),
                        (QPalette.ColorRole.HighlightedText, QColor(0xFF, 0xFF, 0xFF))):
        pal.setColor(role, color)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(0x8A, 0x8A, 0x8A))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(0x8A, 0x8A, 0x8A))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(0x8A, 0x8A, 0x8A))
    menu.setPalette(pal)


def _preview_keys(style: str) -> Callable[[str], str]:
    """How a hotkey shows at the right of an item in `style` ("mac" or "windows"), on any
    computer: the Mac's tab text as the glyphs its native menu draws, Windows' as written."""
    from PySide6.QtGui import QKeySequence

    from ..mac import statusbar as mac_statusbar
    from ..win import statusbar as win_statusbar

    if style == "mac":
        return lambda spec: QKeySequence.fromString(mac_statusbar.shortcut_text(spec)).toString(
            QKeySequence.SequenceFormat.NativeText) if spec else ""
    return win_statusbar.shortcut_text


class _StatusStrip(QWidget):
    """A strip standing in for the menu bar (Mac) or the tooltip (Windows) above a preview menu:
    the icon and what shows beside it, or the tooltip's text."""

    HEIGHT = 28

    def __init__(self, text: str, beside_icon: bool, parent: QWidget):
        super().__init__(parent)
        self.text, self.beside_icon = text, beside_icon
        self.setFixedHeight(self.HEIGHT)

    def paintEvent(self, ev) -> None:
        from ..win.statusbar import tray_pixmap

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), theme.surface())
        p.setFont(theme.ui_font(13))
        p.setPen(theme.content())
        x = 10.0
        if self.beside_icon:
            dpr = p.device().devicePixelRatioF()
            pm = tray_pixmap(round(18 * dpr), theme.is_dark())
            pm.setDevicePixelRatio(dpr)
            p.drawPixmap(round(x), (self.HEIGHT - 18) // 2, pm)
            x += 22
            text = self.text
        else:
            text = f"Tooltip: {self.text}"
        p.drawText(QRectF(x, 0, self.width() - x, self.HEIGHT), int(Qt.AlignmentFlag.AlignVCenter), text)
        p.end()


def _preview_tray(open_menu: str = "", warning: bool = False, style: str = "", **state) -> QWidget:
    """The menu (and one open submenu) side by side on a stage, drawn by Qt (the Mac's is drawn
    natively, so its rows only approximate it), under a strip showing what the icon shows. `style`
    forces "mac" or "windows" shortcuts, warning row and status; by default this computer's."""
    from ..win import statusbar as win_statusbar

    style = style or ("mac" if host.STATUS_TEXT_BESIDE_ICON else "windows")

    class PreviewTray(Tray):
        key_text = staticmethod(_preview_keys(style))
        style_header = staticmethod(win_statusbar.style_header)  # the native Mac header is a dim bold row too

    tray = PreviewTray(_PreviewController(**state))
    tray.timer.stop()
    tray.whats_new_pending = state.get("whats_new", False)
    tray.item_quit.setText(f"Quit Lyssna\t{_preview_keys(style)('cmd+q')}" if style == "mac" else "Quit Lyssna")
    if warning:
        tray.show_permission_warning()
        tray.item_warning.setText(ACCESSIBILITY_WARNING_TITLE if style == "mac" else HOOKS_WARNING_TITLE)
    tray._tick()
    if state.get("support"):  # hidden until the website has an address; shown here to review the heart
        tray.item_support.setVisible(True)
        tidy_separators(tray.menu)
    menus = [tray.menu, *{"provider": [tray.provider_menu], "settings": [tray.settings_menu],
                          "keys": [tray.settings_menu, tray.key_menu], "speed": [tray.speed_menu],
                          "voice": [tray.voice_menu], "transcription": [tray.transcription_menu],
                          "cleanup": [tray.cleanup_menu]}.get(open_menu, [])]
    stage = QWidget()
    if style == "mac":
        status = _StatusStrip(tray.title or "(the icon alone)", True, stage)
    else:
        tip = tooltip_text(tray.line) if tray.line or not tray.warning else HOOKS_WARNING_TITLE
        status = _StatusStrip(tip, False, stage)
    top = _StatusStrip.HEIGHT + 6
    x, y, h = 0, top, top
    for i, menu in enumerate(menus):
        _preview_palette(menu)
        if i:  # line each submenu up with its row in the menu before it
            y += menus[i - 1].actionGeometry(menu.menuAction()).y()
        for a in menu.actions():
            if a in tray._icons:
                tray._set_icon(a, tray._icons[a])
        menu.ensurePolished()
        menu.resize(menu.sizeHint())  # lays out the rows, so the next submenu can find its own; adjustSize
        # would cap a wide menu at two thirds of the screen
        menu.setParent(stage, Qt.WindowType.Widget)
        menu.move(x, y)
        menu.show()
        x += menu.width() - 4
        h = max(h, y + menu.height())
    stage.resize(max(x + 4, 260), h)
    status.setGeometry(0, 0, stage.width(), _StatusStrip.HEIGHT)
    stage._tray = tray  # keeps the menus' owner alive as long as the preview
    return stage


def _preview_icons() -> QWidget:
    """The Windows tray icon at 16, 24 and 32 px on a dark and a light taskbar, with and without the dot."""
    from ..win.statusbar import tray_pixmap

    sizes = (16, 24, 32)

    class Icons(QWidget):
        def paintEvent(self, ev) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for row, dark in enumerate((True, False)):
                y = row * 56
                p.fillRect(QRectF(0, y, self.width(), 56), QColor(0x20, 0x20, 0x20) if dark else QColor(0xEE, 0xEE, 0xEE))
                x = 16.0
                for warning in (False, True):
                    for n in sizes:
                        dpr = p.device().devicePixelRatioF()
                        pm = tray_pixmap(round(n * dpr), dark, warning)
                        pm.setDevicePixelRatio(dpr)
                        p.drawPixmap(round(x), round(y + (56 - n) / 2), pm)
                        x += n + 18
                    x += 18
            p.end()

    w = Icons()
    w.setFixedSize(16 + 2 * (sum(sizes) + 18 * len(sizes)) + 36, 112)
    return w


def _preview_about() -> QWidget:
    d = AboutDialog()
    d.setWindowFlag(Qt.WindowType.Window, False)
    return d


_READING = Status(state="playing", index=2, total=5, text="Select any text, press the shortcut, and I will read it to you.")

PREVIEWS = {
    "tray.menu.idle": lambda: _preview_tray(),
    "tray.menu.reading": lambda: _preview_tray(status=_READING),
    "tray.menu.reading.mac": lambda: _preview_tray(style="mac", status=_READING),
    "tray.menu.reading.windows": lambda: _preview_tray(style="windows", status=_READING),
    "tray.menu.paused.mac": lambda: _preview_tray(style="mac", status=Status(state="paused", index=2, total=5, text="Hello.")),
    "tray.menu.provider": lambda: _preview_tray("provider", keys=("cartesia",)),
    "tray.menu.transcription": lambda: _preview_tray("transcription"),
    "tray.menu.cleanup": lambda: _preview_tray("cleanup"),
    "tray.menu.settings": lambda: _preview_tray("settings", keys=("cartesia",), usage="This month: 2k characters, about $0.10"),
    "tray.menu.keys": lambda: _preview_tray("keys", keys=("cartesia",)),
    "tray.menu.managed": lambda: _preview_tray("settings", managed=True),
    "tray.menu.warning.mac": lambda: _preview_tray(style="mac", warning=True, whats_new=True, support=True),
    "tray.menu.warning.windows": lambda: _preview_tray(style="windows", warning=True, whats_new=True, support=True),
    "tray.icons": _preview_icons,
    "tray.about": _preview_about,
}
