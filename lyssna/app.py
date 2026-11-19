"""Wiring: menu bar (rumps) + global input taps + floating UI + control socket, all around one Controller."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import AppKit
import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSBundle
from Foundation import NSURL, NSNotificationCenter, NSOperationQueue

from . import __version__, autostart, capture, config
from .controller import Controller, Notice
from .dictation import cleaners, transcribers
from .events import KeyboardTap, MouseTap, Point, SelectionGestureDetector, accessibility_trusted
from .hotkeys import pretty, tokens
from .menu import (
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
    key_name,
    local_only_title,
    menubar_text_allowed,
    now_playing,
    privacy_symbol,
    provider_title,
    separators_shown,
    visibility,
)
from .player import SPEEDS, Status
from .providers import REGISTRY, allowed
from .selection import SelectionWatcher
from .server import ControlServer
from .ui.mainthread import later, on_main
from .ui.mini_player import MiniPlayer
from .ui.selection_button import SelectionButton
from .ui.setup_window import DICTATION_KEY_ENGINES, SetupWindow
from .ui.toast import Toast

log = logging.getLogger("lyssna.app")

MENUBAR_ICON = str(Path(__file__).parent / "ui" / "assets" / "menubar.png")
STATE_TEXT = {"loading": "…", "playing": "", "paused": "⏸ "}


_KEY_CHARS = {
    "space": " ", "return": "\r", "enter": "\r", "tab": "\t", "escape": "\x1b", "esc": "\x1b",
    "delete": "\x7f", "backspace": "\x7f",
    # Current macOS draws arrow-key shortcuts as triangles in menus, whether given as these
    # function keys or as arrow characters; these are the correct key equivalents.
    "left": AppKit.NSLeftArrowFunctionKey, "right": AppKit.NSRightArrowFunctionKey,
    "up": AppKit.NSUpArrowFunctionKey, "down": AppKit.NSDownArrowFunctionKey,
}
_MOD_FLAGS = {
    "ctrl": AppKit.NSEventModifierFlagControl, "control": AppKit.NSEventModifierFlagControl,
    "alt": AppKit.NSEventModifierFlagOption, "option": AppKit.NSEventModifierFlagOption, "opt": AppKit.NSEventModifierFlagOption,
    "cmd": AppKit.NSEventModifierFlagCommand, "command": AppKit.NSEventModifierFlagCommand,
    "shift": AppKit.NSEventModifierFlagShift,
}


def with_shortcut(item: rumps.MenuItem, spec: str) -> rumps.MenuItem:
    """Show a hotkey on a menu item the native way (right-aligned glyphs), e.g. "ctrl+alt+s"."""
    mask = 0
    key = ""
    for part in tokens(spec):
        if part in _MOD_FLAGS:
            mask |= _MOD_FLAGS[part]
        else:
            key = _KEY_CHARS.get(part, part)
    item._menuitem.setKeyEquivalent_(key)
    item._menuitem.setKeyEquivalentModifierMask_(mask)
    return item


def info_item(title: str) -> rumps.MenuItem:
    """A greyed-out, non-clickable line."""
    return rumps.MenuItem(title)


def section_header(title: str) -> rumps.MenuItem:
    """A titled section of a menu: the native header on macOS 14 and later, else a greyed-out line."""
    item = rumps.MenuItem(title)
    if AppKit.NSMenuItem.respondsToSelector_("sectionHeaderWithTitle:"):
        item._menuitem = AppKit.NSMenuItem.sectionHeaderWithTitle_(title)
    return item


def set_symbol(item: rumps.MenuItem, symbol: str) -> None:
    """Show an SF Symbol, as a template image, in front of a menu item's title."""
    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, None)
    if image is not None:
        image.setTemplate_(True)
    item._menuitem.setImage_(image)


def tidy_separators(nsmenu) -> None:
    """Hide the separators of an NSMenu that would double up or dangle around hidden items."""
    items = list(nsmenu.itemArray())
    rows = [None if item.isSeparatorItem() else not item.isHidden() for item in items]
    separators = [item for item in items if item.isSeparatorItem()]
    for sep, show in zip(separators, separators_shown(rows), strict=True):
        if sep.isHidden() == show:
            sep.setHidden_(not show)


def open_path(*args: str) -> None:
    """Run `open` detached from our stdio (a Finder-launched app has none worth inheriting)."""
    subprocess.run(["open", *args], check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def bring_to_front() -> None:
    """Menu bar apps are never active on their own; do this before a modal so it is not hidden."""
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


def show_about() -> None:
    """The standard About panel: name, version, license and a link to the source."""
    bring_to_front()
    style = AppKit.NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(AppKit.NSTextAlignmentCenter)
    attrs = {
        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(11),
        AppKit.NSParagraphStyleAttributeName: style,
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.labelColor(),
    }
    credits = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_("Free and open source, GPL-3.0\n", attrs)
    link = {**attrs, AppKit.NSLinkAttributeName: NSURL.URLWithString_(REPO_URL)}
    credits.appendAttributedString_(
        AppKit.NSAttributedString.alloc().initWithString_attributes_(REPO_URL.removeprefix("https://"), link))
    NSApplication.sharedApplication().orderFrontStandardAboutPanelWithOptions_({
        AppKit.NSAboutPanelOptionApplicationName: "Lyssna",
        AppKit.NSAboutPanelOptionApplicationVersion: __version__,
        AppKit.NSAboutPanelOptionVersion: "",
        AppKit.NSAboutPanelOptionCredits: credits,
        "Copyright": "",  # from source the panel would otherwise show Python's
    })


class MenuBar(rumps.App):
    """Status item + menu. Reads state from the controller on a timer; never owns state itself.

    The menu, top to bottom (optional rows in brackets; lyssna.menu has the rules):

        [Allow Accessibility Access…]
        Listen (a section header)
          Read Selection, Read Clipboard, [now playing],
          [Pause / Resume, Stop, Previous Sentence, Next Sentence] (only while reading),
          Speed ▸, [Voice ▸], Voice Provider ▸, where the text goes
        Dictate (a section header)
          Start Dictation, History…, Transcription ▸, Cleanup ▸, where speech goes
        [usage this month], Settings ▸, Setup…, About Lyssna
        Quit Lyssna

    with a separator after each section and before Quit. Separators hide themselves when the
    rows around them do. The three engine pickers group their engines under "On this Mac" and
    "Online, with your API key"; a lock or an arrow out marks each "where it goes" line."""

    def __init__(self, ctl: Controller):
        super().__init__("Lyssna", title=None, icon=MENUBAR_ICON, template=True, quit_button=None)
        self.ctl = ctl
        self.hk = ctl.cfg["hotkeys"]
        self.on_quit = None
        self.on_setup: Callable[[str | None], None] = lambda engine: None  # opens the setup window; set by LyssnaApp
        self.on_setup_dictation: Callable[[str], None] = lambda engine: None  # ...at its dictation keys
        self.on_history: Callable[[], None] = lambda: None  # opens the dictation history; set by LyssnaApp
        self._downloading = False
        self._engine_groups: list[tuple[rumps.MenuItem, list[rumps.MenuItem]]] = []  # header, its rows
        self._privacy_symbols: dict[str, str] = {}  # "reading" / "dictation": the symbol shown now
        self.header_listen = section_header(LISTEN_HEADER)
        self.menu = [
            self.header_listen, *self._build_reading(), *self._build_transport(), *self._build_voice(), None,
            section_header(DICTATE_HEADER), *self._build_dictation(), None,
            *self._build_app(), None,
            with_shortcut(rumps.MenuItem("Quit Lyssna", callback=self.quit), "cmd+q"),
        ]
        self._refresh_checks()
        # The saved keys are read in the background at launch; until then the menu shows
        # engines as set up. When they land, the menu catches up with the real state.
        ctl.subscribe_keys(lambda: on_main(self._refresh_checks))
        rumps.Timer(self._tick, 0.4).start()

    # -- building --------------------------------------------------------------------

    def _item(self, title: str, action: Callable[[], None] | None = None, hotkey: str = "") -> rumps.MenuItem:
        """A menu item running `action`, showing the configured shortcut for `hotkey` if given."""
        item = rumps.MenuItem(title, callback=(lambda _: action()) if action else None)
        return with_shortcut(item, self.hk[hotkey]) if hotkey else item

    def _build_reading(self) -> list:
        self.item_read = self._item("Read Selection", self.ctl.speak_selection, "speak")
        self.item_now = info_item("Nothing playing")
        return [self.item_read, self._item("Read Clipboard", self.ctl.speak_clipboard), self.item_now]

    def _build_dictation(self) -> list:
        self.item_dictate = rumps.MenuItem(self._dictate_title(), callback=lambda _: self._dictate())
        self.transcription_menu = rumps.MenuItem("Transcription")
        self.transcriber_items = self._add_engines(self.transcription_menu, transcribers.REGISTRY,
                                                   lambda cls: not cls.sends_audio_off_device, self._set_transcriber)
        self.cleanup_menu = rumps.MenuItem("Cleanup")
        self.cleanup_items = {"": rumps.MenuItem("Off", callback=lambda _: self._set_cleaner(""))}
        self.cleanup_menu.add(self.cleanup_items[""])
        self.cleanup_items.update(self._add_engines(self.cleanup_menu, cleaners.REGISTRY,
                                                    lambda cls: not cls.sends_text_off_device, self._set_cleaner))
        self.item_dictation_privacy = info_item(self.ctl.dictation_privacy_note())
        return [self.item_dictate, rumps.MenuItem("History…", callback=lambda _: self.on_history()),
                self.transcription_menu, self.cleanup_menu, self.item_dictation_privacy]

    def _add_engines(self, menu: rumps.MenuItem, registry: dict, runs_here: Callable[[type], bool],
                     choose: Callable[[str], None]) -> dict[str, rumps.MenuItem]:
        """Fill an engine picker: the engines on this Mac under one header, the online ones under
        another, each group in registry order. Returns the rows by engine name."""
        items = {}
        for header, local in ((LOCAL_HEADER, True), (ONLINE_HEADER, False)):
            names = [name for name, cls in registry.items() if runs_here(cls) == local]
            if not names:
                continue
            menu.add(section_header(header))
            for name in names:
                items[name] = rumps.MenuItem(engine_row_label(registry[name].label), callback=lambda _, n=name: choose(n))
                menu.add(items[name])
            self._engine_groups.append((menu[header], [items[name] for name in names]))
        return items

    def _build_transport(self) -> list:
        self.transport_items = [
            self._item("Pause / Resume", self.ctl.toggle_pause, "pause"),
            self._item("Stop", self.ctl.stop, "stop"),
            self._item("Previous Sentence", self.ctl.prev, "prev"),
            self._item("Next Sentence", self.ctl.next, "next"),
        ]
        return self.transport_items

    def _build_voice(self) -> list:
        self.speed_menu = rumps.MenuItem("Speed")
        self.speed_menu.add(self._item("Faster", lambda: self._step_speed(self.ctl.faster), "faster"))
        self.speed_menu.add(self._item("Slower", lambda: self._step_speed(self.ctl.slower), "slower"))
        self.speed_menu.add(None)
        for s in SPEEDS:
            self.speed_menu.add(rumps.MenuItem(f"{s:g}×", callback=lambda _, s=s: self._set_speed(s)))

        # One entry per voice any provider offers; _refresh_voices shows the current provider's.
        self.voice_menu = rumps.MenuItem("Voice")
        for voice, text in dict(v for cls in REGISTRY.values() for v in cls.voices).items():
            self.voice_menu.add(rumps.MenuItem(text, callback=lambda _, v=voice: self._set_voice(v)))

        self.provider_menu = rumps.MenuItem("Voice Provider")
        self.provider_items = self._add_engines(self.provider_menu, REGISTRY,
                                                lambda cls: not cls.sends_text_off_device, self._set_provider)
        self.item_privacy = info_item(self.ctl.privacy_note())
        return [self.speed_menu, self.voice_menu, self.provider_menu, self.item_privacy]

    def _build_app(self) -> list:
        self.item_usage = info_item("No usage this month")
        return [
            self.item_usage,
            self._build_settings(),
            rumps.MenuItem("Setup…", callback=lambda _: self.on_setup(None)),
            rumps.MenuItem("About Lyssna", callback=lambda _: show_about()),
        ]

    def _build_settings(self) -> rumps.MenuItem:
        self.settings_menu = rumps.MenuItem("Settings")
        self.item_login = None
        if getattr(sys, "frozen", False):  # from source this would register the wrong bundle
            self.item_login = rumps.MenuItem("Start at Login", callback=self._toggle_login)
            self.item_login.state = 1 if autostart.is_enabled() else 0
            self.settings_menu.add(self.item_login)
        self.item_button = self._item("Play Button on Selection", self._toggle_button, "button")
        self.item_local = rumps.MenuItem(local_only_title(False), callback=self._toggle_local_only)
        for item in (self.item_button, self.item_local, self._build_api_keys(), None,
                     rumps.MenuItem("Edit Config File…", callback=lambda _: open_path("-t", str(config.CONFIG_PATH))),
                     rumps.MenuItem("Open Log", callback=lambda _: open_path(str(config.LOG_DIR / "lyssna.log"))),
                     rumps.MenuItem("Clear Audio Cache", callback=lambda _: self.ctl.reader.cache.clear())):
            self.settings_menu.add(item)
        return self.settings_menu

    def _build_api_keys(self) -> rumps.MenuItem:
        self.key_menu = rumps.MenuItem("API Keys")
        self.key_engines: dict[str, str] = {}  # one provider per config section: the one its key entries act on
        for name, cls in REGISTRY.items():
            if cls.needs_key and cls.section() not in self.key_engines:
                self.key_engines[cls.section()] = name
        for name in self.key_engines.values():
            self.key_menu.add(rumps.MenuItem(f"{self._key_label(name)}…", callback=lambda _, n=name: self._prompt_for_key(n)))
        self.key_menu.add(None)  # hidden by tidy_separators while no key is stored
        self.remove_items = {}
        for name in self.key_engines.values():
            self.remove_items[name] = rumps.MenuItem(f"Remove {self._key_label(name)} Key…",
                                                     callback=lambda _, n=name: self._remove_key(n))
            self.key_menu.add(self.remove_items[name])
        return self.key_menu

    # -- actions ---------------------------------------------------------------------

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

    def _toggle_local_only(self, _=None) -> None:
        self.ctl.set_local_only(not self.ctl.local_only())
        self._refresh_checks()

    def _set_provider(self, name: str) -> None:
        if not allowed(name, self.ctl.cfg):
            self.ctl.notify(f"{local_only_title(False)} is on", ttl=3.0)
            return
        if not REGISTRY[name].can_run():
            self.ctl.notify(f"{REGISTRY[name].label} is not available in this build")
            return
        if REGISTRY[name].needs_key and not self.ctl.provider_configured(name):
            self._prompt_for_key(name)
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
            return
        if not cls.can_run():
            self.ctl.notify(f"{cls.label} is not available in this build")
            return
        if cls.needs_key and not self.ctl.transcriber_configured(name):
            self._ask_for_dictation_key(name)
            return
        self.ctl.set_transcriber(name)  # the model on this Mac starts downloading here if it has to
        self._refresh_checks()

    def _set_cleaner(self, name: str) -> None:
        if name:
            cls = cleaners.REGISTRY[name]
            if not cleaners.allowed(name, self.ctl.cfg):
                self.ctl.notify(f"{local_only_title(False)} is on", ttl=3.0)
                return
            if not cls.can_run():
                self.ctl.notify(f"{cls.label} is not available in this build")
                return
            if cls.needs_key and not self.ctl.cleaner_configured(name):
                self._ask_for_dictation_key(name)
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
        bring_to_front()
        if rumps.alert(title=f"Remove your {label} API key?", message="You can add it again any time.",
                       ok="Remove", cancel="Cancel") != 1:
            return
        self.ctl.remove_api_key(name)
        self._refresh_checks()

    def _toggle_login(self, item: rumps.MenuItem) -> None:
        autostart.set_enabled(not autostart.is_enabled())
        item.state = 1 if autostart.is_enabled() else 0

    def show_permission_warning(self) -> None:
        item = rumps.MenuItem("Allow Accessibility Access…", callback=lambda _: self.on_setup(None))
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("exclamationmark.triangle", "Warning")
        if image is not None:
            image.setTemplate_(True)
            item._menuitem.setImage_(image)
        self.menu.insert_before(self.header_listen.title, item)
        self.title = "⚠︎"

    def _relaunch(self) -> None:
        """Shut down (releasing the control socket), start a fresh copy of the bundle so it picks up
        the Accessibility grant, then exit. From source there is no bundle to reopen, so just quit."""
        if self.on_quit:
            self.on_quit()
            self.on_quit = None
        if getattr(sys, "frozen", False):
            path = NSBundle.mainBundle().bundlePath()
            log.info("relaunching %s", path)
            subprocess.Popen(["open", "-n", path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rumps.quit_application()

    # -- refreshing ------------------------------------------------------------------

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
        for item in self.speed_menu.values():
            if getattr(item, "title", "").endswith("×"):
                item.state = 1 if abs(float(item.title.rstrip("×")) - speed) < 0.01 else 0
        self.speed_menu.title = f"Speed: {speed:g}×"

    def _refresh_providers(self) -> None:
        current = self.ctl.cfg.get("provider")
        for name, cls in REGISTRY.items():
            item = self.provider_items[name]
            item.state = 1 if name == current else 0
            item.hidden = not allowed(name, self.ctl.cfg)  # online providers while local-only mode is on
            hint = cls.setup_hint if cls.can_run() else "not available"
            item.title = provider_title(engine_row_label(cls.label), self.ctl.provider_configured(name), hint)
        self.provider_menu.title = self._provider_title()
        self.item_privacy.title = self.ctl.privacy_note()
        cls = REGISTRY.get(current)
        self._show_privacy_symbol("reading", self.item_privacy, cls is None or not cls.sends_text_off_device)

    def _refresh_dictation(self) -> None:
        cfg = self.ctl.cfg
        current = self.ctl.current_transcriber()
        for name, cls in transcribers.REGISTRY.items():
            item = self.transcriber_items[name]
            item.state = 1 if name == current else 0
            item.hidden = not transcribers.allowed(name, cfg)  # online engines while local-only mode is on
            hint = cls.setup_hint if cls.can_run() else "not available"
            item.title = provider_title(engine_row_label(cls.label), self.ctl.transcriber_configured(name), hint)
        self.transcription_menu.title = self._transcription_title()
        current = self.ctl.current_cleaner()
        self.cleanup_items[""].state = 1 if not current else 0
        for name, cls in cleaners.REGISTRY.items():
            item = self.cleanup_items[name]
            item.state = 1 if name == current else 0
            item.hidden = not cleaners.allowed(name, cfg)
            hint = cls.setup_hint if cls.can_run() else "not available"
            item.title = provider_title(engine_row_label(cls.label), self.ctl.cleaner_configured(name), hint)
        self.cleanup_menu.title = self._cleanup_title()
        self.item_dictation_privacy.title = self.ctl.dictation_privacy_note()
        self._show_privacy_symbol("dictation", self.item_dictation_privacy, self.ctl.dictation_stays_local())

    def _refresh_engine_headers(self) -> None:
        """A picker's header hides when every engine under it does (online ones in local-only mode)."""
        for header, rows in self._engine_groups:
            header.hidden = all(row.hidden for row in rows)

    def _show_privacy_symbol(self, which: str, item: rumps.MenuItem, stays_local: bool) -> None:
        """Put the lock or the arrow out in front of a privacy line, only when it changes."""
        symbol = privacy_symbol(stays_local)
        if self._privacy_symbols.get(which) != symbol:
            self._privacy_symbols[which] = symbol
            set_symbol(item, symbol)

    def _transcription_title(self) -> str:
        cls = transcribers.REGISTRY.get(self.ctl.current_transcriber())
        return f"Transcription: {cls.label if cls else 'Not available'}"

    def _cleanup_title(self) -> str:
        cls = cleaners.REGISTRY.get(self.ctl.current_cleaner())
        return cleanup_title(cls.label if cls else "")

    def _dictate_title(self) -> str:
        return dictation_title(str(self.ctl.cfg.get("dictation", {}).get("hotkey", "fn")),
                               recording=self.ctl.dictation.state in ("recording", "locked"))

    def _refresh_voices(self) -> None:
        self.voice_menu.title = self._voice_title()
        voices = dict(self.ctl.voices())
        voice = self.ctl.current_voice()
        for item in self.voice_menu.values():
            vid = next((v for v, text in voices.items() if text == item.title), None)
            item.state = 1 if vid == voice else 0
            item.hidden = vid is None  # belongs to another provider's list

    def _refresh_settings(self) -> None:
        self.item_button.state = 1 if self.ctl.selection_button_enabled else 0
        managed = self.ctl.local_only_managed()
        self.item_local.state = 1 if self.ctl.local_only() else 0
        self.item_local.title = local_only_title(managed)
        self.item_local.set_callback(None if managed else self._toggle_local_only)  # None greys it out
        for name, item in self.remove_items.items():
            item.hidden = not self.ctl.has_stored_key(name)

    def _apply_visibility(self) -> None:
        """Show or hide the optional rows (cheap: runs on every tick), then fix the separators."""
        usage_line = self.ctl.usage_summary()
        if usage_line and self.item_usage.title != usage_line:
            self.item_usage.title = usage_line
        state = MenuState(player=self.ctl.status.state, downloading=self._downloading, message=self.ctl.message,
                          has_voices=bool(self.ctl.voices()), usage_line=usage_line, local_only=self.ctl.local_only(),
                          dictation=self.ctl.dictation.state)
        shown = visibility(state)
        for item, key in ((self.item_now, "now_playing"), *((t, "transport") for t in self.transport_items),
                          (self.voice_menu, "voice"), (self.item_usage, "usage"), (self.key_menu, "api_keys")):
            if item.hidden == shown[key]:
                item.hidden = not shown[key]
        for menu in (self._menu._menu, self.settings_menu._menu, self.key_menu._menu):
            tidy_separators(menu)

    def _tick(self, _timer) -> None:
        st: Status = self.ctl.status
        what, download = "voice", self.ctl.voice_download_progress()
        if download is None:
            what, download = "model", self.ctl.dictation_download_progress()
        if self._downloading and download is None:  # just ended: the provider entry may lose its "download" hint
            self._refresh_checks()
        self._downloading = download is not None
        dictating = not menubar_text_allowed(MenuState(dictation=self.ctl.dictation.state))
        if self.item_dictate.title != self._dictate_title():
            self.item_dictate.title = self._dictate_title()
        if dictating:  # the pill is the indicator
            self.title = None
        elif download is not None:  # a reading asked for meanwhile starts once this finishes
            self.title = self.item_now.title = download_line(what, download)
        elif self.ctl.message:
            self.title = self.ctl.message[:60]
            self.item_now.title = self.ctl.message[:80]
        elif st.state == "idle":
            self.title = None
        else:
            self.title = f"{STATE_TEXT[st.state]}{st.index}/{st.total}" if st.state != "loading" else "…"
            self.item_now.title = now_playing(st.state, st.index, st.total, st.text)
        if self.item_button.state != (1 if self.ctl.selection_button_enabled else 0):
            self._refresh_checks()
        elif abs(self.ctl.reader.speed - float(self.speed_menu.title.split(": ")[1].rstrip("×"))) > 0.01:
            self._refresh_checks()
        elif self.voice_menu.title != self._voice_title():  # changed from the player badge or the socket
            self._refresh_checks()
        elif self.provider_menu.title != self._provider_title():  # e.g. local-only mode switched it
            self._refresh_checks()
        elif (self.transcription_menu.title != self._transcription_title()
              or self.cleanup_menu.title != self._cleanup_title()):
            self._refresh_checks()
        else:
            self._apply_visibility()

    def _voice_title(self) -> str:
        label = dict(self.ctl.voices()).get(self.ctl.current_voice(), self.ctl.current_voice())
        return f"Voice: {label.split(' · ')[0]}"

    def _provider_title(self) -> str:
        return f"Voice Provider: {self.ctl.provider_label()}"

    def quit(self, _=None) -> None:
        if self.on_quit:
            self.on_quit()
        rumps.quit_application()


class LyssnaApp:
    def __init__(self):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.ctl = Controller()
        self.menubar = MenuBar(self.ctl)
        self.menubar.on_quit = self.shutdown
        ui_cfg = self.ctl.cfg.get("ui", {})

        # Floating UI
        dictation_key = key_name(str(self.ctl.cfg.get("dictation", {}).get("hotkey", "fn")))
        self.mini_player = MiniPlayer.alloc().init() if ui_cfg.get("mini_player", True) else None
        if self.mini_player:
            self.mini_player.actions = {
                "toggle": self.ctl.toggle_pause, "stop": self.ctl.stop, "back": self.ctl.prev,
                "forward": self.ctl.next, "set_speed": self.ctl.set_speed, "seek_to": self.ctl.seek_to,
                "layout": self.ctl.save_player_layout, "set_voice": self.ctl.set_voice,
                "dictate": lambda: self.ctl.dictation_start(hands_free=True), "dictate_stop": self.ctl.dictation_stop,
                "dictate_cancel": self.ctl.dictation_cancel, "dictation_layout": self.ctl.save_dictation_layout,
                "read_selection": self._speak_from_hotkey, "history": self.show_history,
            }
            self.mini_player.voices_fn = lambda: (self.ctl.voices(), self.ctl.current_voice())
            self.mini_player.layout = self.ctl.player_layout()
            self.mini_player.dictation_layout = self.ctl.dictation_layout()
            self.mini_player.idle_pill = bool(ui_cfg.get("idle_pill", True))
            self.mini_player.key_name = dictation_key
            self.mini_player.read_key = pretty(self.ctl.cfg["hotkeys"]["speak"])
            self.ctl.subscribe(self.mini_player.update)
            self.ctl.subscribe_dictation(self.mini_player.dictation_update)
            self.mini_player.status_fn = lambda: self.ctl.status  # so a queued update can check the live state
            hk = self.ctl.cfg["hotkeys"]
            self.mini_player.set_tooltips({
                "toggle": f"Pause / resume  {pretty(hk['pause'])}", "stop": f"Stop  {pretty(hk['stop'])}",
                "back": f"Previous sentence  {pretty(hk['prev'])}", "forward": f"Next sentence  {pretty(hk['next'])}",
                "speed": f"Speed  {pretty(hk['slower'])} / {pretty(hk['faster'])}",
            })
        self.toast = Toast.alloc().init()
        # Always queued, never inline: a toast can come from inside the keyboard tap's callback.
        self.ctl.subscribe_toast(
            lambda notice: NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: self._present_toast(notice)))
        self.selection_button = SelectionButton.alloc().init()
        if self.selection_button:
            self.selection_button.timeout = float(ui_cfg.get("button_timeout", 4.0))
            self.selection_button.on_play = self._play_from_button
        self._button_source = "selection"  # what the play button on screen reads: "selection" or "clipboard"

        # Plugging or unplugging a display is the suspected trigger for the window server
        # dropping our floating windows from every Space. Only a breadcrumb: FloatingPanel
        # detects and repairs that on the next order-front.
        self._screen_observer = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            AppKit.NSApplicationDidChangeScreenParametersNotification, None, NSOperationQueue.mainQueue(),
            lambda note: self._log_screens(),
        )

        # Global input
        hk = self.ctl.cfg["hotkeys"]
        self.keyboard = KeyboardTap()
        for action, fn in (("speak", self._speak_from_hotkey), ("pause", self.ctl.toggle_pause), ("stop", self.ctl.stop),
                           ("next", self.ctl.next), ("prev", self.ctl.prev), ("faster", self.ctl.faster), ("slower", self.ctl.slower),
                           ("button", self._toggle_button_from_hotkey)):
            try:
                self.keyboard.bind(hk[action], fn)
            except ValueError as e:
                log.error("bad hotkey for %s: %s", action, e)
        self.keyboard.on_any_key = self._dismiss_button
        self._attach_push_to_talk()
        self.mouse = None
        if self.selection_button:
            # The detector spots selection-like gestures; the watcher wants proof that text was
            # really selected (a new Accessibility selection, or a copy on select) before the button shows.
            self.selection_watcher = SelectionWatcher(
                probe_selection=capture.ax_selection,
                present=self._on_selection_gesture,
                enabled=lambda: self.ctl.selection_button_enabled,
                probe_clipboard=capture.clipboard_change_count,
                probe_copied=capture.copied_since,
                wake=lambda: capture.wake_accessibility(capture.front_app_pid()),
            )
            # Apps that keep their accessibility off until asked (Chrome, Electron) are asked as
            # they come to the front, and the one in front now at launch; the press is the fallback.
            # wake_accessibility moves itself off the main thread.
            workspace_center = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
            self._activation_observer = workspace_center.addObserverForName_object_queue_usingBlock_(
                AppKit.NSWorkspaceDidActivateApplicationNotification, None, NSOperationQueue.mainQueue(),
                self._on_app_activated,
            )
            capture.wake_accessibility(capture.front_app_pid())
            detector = SelectionGestureDetector(
                on_select=self.selection_watcher.gesture,
                on_dismiss=lambda p: self._dismiss_button(),
                ignore_region=self._is_over_our_ui,
                on_press=self.selection_watcher.press,
            )
            self.mouse = MouseTap(detector)

        trusted = accessibility_trusted(prompt=True)
        self.taps_ok = trusted and self.keyboard.start() and (self.mouse is None or self.mouse.start())
        if not self.taps_ok:
            self.menubar.show_permission_warning()
        self.setup_window = None
        self.menubar.on_setup = self.show_setup
        self.menubar.on_setup_dictation = lambda engine: self.show_setup(dictation=engine)
        self.history_window = None
        self.menubar.on_history = self.show_history
        self.ctl.warm_dictation()  # gets the microphone ready if access was already granted

        self.ctl.extensions["ui"] = self._ui_state
        self.ctl.extensions["quit"] = self._quit_cmd
        self.server = ControlServer(self.ctl.dispatch)
        self.server.start()
        # Deferred so it appears once the run loop and the menu bar icon are up, after the permission prompt.
        later(1.0, self._welcome_once)
        if self.mini_player:
            later(0.5, self.mini_player.show_idle)  # the idle pill, when nothing is playing

    # -- glue ----------------------------------------------------------------------

    def _welcome_once(self) -> None:
        """First launch only: open the setup window."""
        marker = config.STATE_DIR / "welcomed"
        if marker.exists():
            return
        marker.write_text("")
        log.info("first run: showing the setup window")
        self.show_setup()

    def _attach_push_to_talk(self) -> None:
        """Hold the dictation key ([dictation] hotkey, Fn by default) to dictate; the hands-free
        key while holding keeps it recording. The controller keeps it in step with the session."""
        from .dictation.hotkey import PushToTalk

        section = self.ctl.cfg.get("dictation", {})
        try:
            ptt = PushToTalk(
                trigger=str(section.get("hotkey", "fn")), hands_free=str(section.get("hands_free", "space")),
                on_start=self.ctl.dictation_start, on_stop=self.ctl.dictation_stop,
                on_lock=self.ctl.dictation_lock, on_cancel=lambda quiet: self.ctl.dictation_cancel(quiet=quiet),
            )
        except ValueError as e:
            log.error("bad dictation key: %s", e)
            self.ctl.toast(Notice(str(e), seconds=8.0))
            return
        self.keyboard.push_to_talk = ptt
        self.ctl.push_to_talk = ptt

    def _present_toast(self, notice: Notice) -> None:
        """On the screen the pill is on, else the one under the mouse; above the pill when it
        sits on the bottom edge."""
        screen, floor = None, None
        if self.mini_player and self.mini_player.visible():
            screen = self.mini_player.panel.screen()
            floor = self.mini_player.toast_floor()
        self.toast.present(notice, screen, floor)

    def show_history(self) -> None:
        """The dictation history window, from the module the history task adds."""
        try:
            from .ui.history_window import HistoryWindow
        except ImportError:
            log.info("no history window in this build")
            self.ctl.toast(Notice("History is not available in this build yet"))
            return
        if self.history_window is None:
            self.history_window = HistoryWindow.alloc().init()
            if hasattr(self.history_window, "ctl"):
                self.history_window.ctl = self.ctl
        self.history_window.show()

    def show_setup(self, engine: str | None = None, dictation: str | None = None) -> None:
        if self.setup_window is None:
            self.setup_window = SetupWindow.alloc().init()
            self.setup_window.ctl = self.ctl
            self.setup_window.taps_ok = lambda: self.taps_ok
            self.setup_window.relaunch = self.menubar._relaunch
            self.setup_window.on_change = self.menubar._refresh_checks
        self.setup_window.show(engine, dictation)

    def _log_screens(self) -> None:
        names = []
        for screen in AppKit.NSScreen.screens():
            f = screen.frame()
            names.append(f"{screen.localizedName()} ({f.origin.x:.0f}, {f.origin.y:.0f}, {f.size.width:.0f}, {f.size.height:.0f})")
        log.info("screens changed: %s", names)

    def _speak_from_hotkey(self) -> None:
        self._dismiss_button()
        self.ctl.speak_selection()

    def _toggle_button_from_hotkey(self) -> None:
        self._dismiss_button()
        self.ctl.toggle_selection_button()

    def _on_selection_gesture(self, p: Point, bounds: tuple[float, float, float, float] | None, source: str) -> None:
        """Called by the selection watcher from a worker thread once text is confirmed selected.
        `source` says where the evidence came from: "selection" (Accessibility) or "clipboard"
        (the app copied the selection itself)."""
        on_main(self._present_button, p, bounds, source)

    def _present_button(self, p: Point, bounds: tuple[float, float, float, float] | None, source: str) -> None:
        if not self.ctl.selection_button_enabled:
            return
        self._button_source = source
        avoid = self.mini_player.panel.frame() if self.mini_player and self.mini_player.visible() else None
        self.selection_button.present(p.x, p.y, avoid, bounds)

    def _on_app_activated(self, note) -> None:
        """An app came to the front: ask it to report its selection, if it never was."""
        try:
            app = note.userInfo()[AppKit.NSWorkspaceApplicationKey]
            capture.wake_accessibility(int(app.processIdentifier()))
        except Exception:
            log.debug("activated app lookup failed", exc_info=True)

    def _play_from_button(self) -> None:
        """The play button reads what it was offered for: a selection the app copied itself is
        on the clipboard already, so no Cmd+C goes to that app."""
        if self._button_source == "clipboard":
            self.ctl.speak_clipboard()
        else:
            self.ctl.speak_selection()

    def _dismiss_button(self) -> None:
        if self.selection_button:
            self.selection_button.dismiss()

    def _is_over_our_ui(self, p: Point) -> bool:
        for panel in (self.selection_button, self.mini_player, self.toast):
            if panel is not None and panel.contains_screen_point(p.x, p.y):
                return True
        return False

    def _quit_cmd(self, req: dict) -> dict:
        later(0.2, self.menubar.quit)
        return {"ok": True, "status": self.ctl.status_dict()}

    def _ui_state(self, req: dict) -> dict:
        """Debug/introspection for the CLI and tests: where the floating panels are."""
        def frame(panel):
            if panel is None or not panel.visible():
                return None
            f = panel.panel.frame()
            return {"x": f.origin.x, "y": f.origin.y, "w": f.size.width, "h": f.size.height}
        return {"ok": True, "selection_button": frame(self.selection_button), "mini_player": frame(self.mini_player),
                "dock": self.mini_player.dock if self.mini_player else ""}

    def shutdown(self) -> None:
        self.ctl.shutdown()
        try:
            self.server.close()
        except Exception:
            pass

    def run(self) -> None:
        self.menubar.run()


def run() -> None:
    LyssnaApp().run()
