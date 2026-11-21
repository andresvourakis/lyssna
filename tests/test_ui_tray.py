"""The menu bar item and its menu (lyssna/ui/tray.py), built from a real controller like the
legacy menu in legacy/test_menu.py, whose behavioural tests are ported here: its rows in each
state, its separators, the title beside the icon or the tooltip, its icon and what each item
calls; the pure rules in lyssna/menu.py; and the two platform twins lyssna/mac/statusbar.py
(against a small fake AppKit) and lyssna/win/statusbar.py.

By default a tray here is built the Windows way (the real lyssna/win/statusbar.py, nothing beside
the icon), so the tests read the same on every computer; `make_tray(system="mac")` builds it the
Mac way with a recording fake of host.statusbar. Runs on the offscreen platform; nothing is shown
and the app never runs, except the opt-in manual check at the end (LYSSNA_MANUAL_MENUBAR=1, on a
Mac), which shows a real menu bar item for about three seconds."""

import itertools
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from lyssna import config, host
from lyssna import menu as rules
from lyssna.mac import statusbar as mac_statusbar
from lyssna.menu import (
    DICTATE_HEADER,
    LISTEN_HEADER,
    LOCAL_HEADER,
    ONLINE_HEADER,
    MenuState,
    key_name,
    local_only_title,
    separators_shown,
    visibility,
)
from lyssna.providers import kokoro
from lyssna.win import statusbar as win_statusbar

KOKORO_ROW = f"Kokoro · {kokoro.DOWNLOAD_SIZE_TEXT} download"  # 140 MB on the Mac, 200 MB on Windows
HEADERS = {LISTEN_HEADER, DICTATE_HEADER, LOCAL_HEADER, ONLINE_HEADER}


class FakeMacStatusbar:
    """host.statusbar the Mac way, recording what the tray asks of it."""

    QUIT_SHORTCUT = mac_statusbar.QUIT_SHORTCUT
    CLICK_OPENS_MENU = mac_statusbar.CLICK_OPENS_MENU
    shortcut_text = staticmethod(mac_statusbar.shortcut_text)
    style_header = staticmethod(mac_statusbar.style_header)

    def __init__(self):
        self.titles: list[str] = []
        self.tooltips: list[str] = []
        self.headers: list[set[str]] = []
        self.icons: list[bool] = []
        self.opened: list[Path] = []

    def set_icon(self, tray, warning, force=False):
        self.icons.append(warning)

    def show_status(self, tray, title, tooltip):
        self.titles.append(title)
        self.tooltips.append(tooltip)

    def mark_headers(self, tray, titles):
        self.headers.append(set(titles))

    def open_text_file(self, path):
        self.opened.append(path)


@pytest.fixture
def make_tray(qapp, monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)  # no download
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    from lyssna.controller import Controller
    from lyssna.ui.tray import Tray

    made = []

    def make(system="windows"):
        mac = system == "mac"
        monkeypatch.setattr(host, "statusbar", FakeMacStatusbar() if mac else win_statusbar, raising=False)
        monkeypatch.setattr(host, "STATUS_TEXT_BESIDE_ICON", mac)
        monkeypatch.setattr(host, "NEEDS_ACCESSIBILITY", mac)
        tray = Tray(Controller())
        tray.timer.stop()  # the tests tick by hand
        made.append(tray)
        return tray

    yield make
    for tray in made:
        tray.timer.stop()
        tray._unsubscribe()
        tray.tray.hide()


@pytest.fixture
def tray(make_tray):
    return make_tray()


def rows(menu) -> list[str]:
    from lyssna.ui.tray import label_of

    return ["---" if a.isSeparator() else label_of(a) for a in menu.actions() if a.isVisible()]


def find(menu, title: str):
    from lyssna.ui.tray import label_of

    return next(a for a in menu.actions() if not a.isSeparator() and label_of(a) == title)


def all_menus(menu):
    yield menu
    for a in menu.actions():
        if a.menu() is not None:
            yield from all_menus(a.menu())


def kokoro_provider_title(tray) -> str:
    return f"Voice Provider: {tray.ctl.provider_label()}"


# -- structure --------------------------------------------------------------------------------


def test_top_level_menu_when_idle(tray):
    from lyssna.dictation.cleaners.s1mini import S1MiniCleaner

    d = host.DEVICE
    # The cleanup model needs Apple silicon and llama.cpp; elsewhere (a PC) cleanup falls back to the rules alone.
    cleanup = f"Cleanup: On this {d} (S1-mini by Superwhisper)" if S1MiniCleaner.can_run() else "Cleanup: Off"
    assert rows(tray.menu) == [
        LISTEN_HEADER, "Read Selection", "Read Clipboard",
        "Speed: 1.2×", "Voice: Heart", kokoro_provider_title(tray), f"Text stays on this {d}", "---",
        DICTATE_HEADER, f"Start Dictation · hold {key_name(host.DICTATION_KEY_DEFAULT)}", "History…",
        f"Transcription: On this {d} (Parakeet)",
        cleanup, f"Speech stays on this {d}", "---",
        "Settings", "Setup…", "About Lyssna", "---",
        "Quit Lyssna",
    ]


def test_the_dictate_row_names_the_key_and_finishes_while_recording(tray, monkeypatch):
    from lyssna.menu import key_name

    cfg = tray.ctl.cfg.setdefault("dictation", {})
    cfg["hotkey"] = "right_option"
    tray._tick()
    assert tray.item_dictate.text() == f"Start Dictation · hold {key_name('right_option')}"
    monkeypatch.setattr(type(tray.ctl.dictation), "state", property(lambda self: "recording"))
    tray._tick()
    assert tray.item_dictate.text() == "Finish Dictation"


def test_sections_are_dim_header_lines(tray):
    for title in (LISTEN_HEADER,):
        item = find(tray.menu, title)
        assert not item.isEnabled() and item.data() == "header"
        assert item.font().weight() > tray.menu.font().weight()
    headers = [a.text() for a in tray.provider_menu.actions() if a.data() == "header"]
    assert headers == [LOCAL_HEADER, ONLINE_HEADER]


def test_reading_shows_the_transport(tray, monkeypatch):
    from lyssna.controller import Controller
    from lyssna.player import Status

    monkeypatch.setattr(Controller, "status", property(lambda self: Status(state="playing", index=1, total=3, text="Hello")))
    tray._tick()
    assert rows(tray.menu)[:9] == [LISTEN_HEADER, "Read Selection", "Read Clipboard", "Reading 1 of 3: Hello",
                                   "Pause / Resume", "Stop", "Previous Sentence", "Next Sentence", "Speed: 1.2×"]


def test_a_long_sentence_is_cut_in_the_now_playing_line(tray, monkeypatch):
    from lyssna.controller import Controller
    from lyssna.player import Status

    text = "word " * 60
    monkeypatch.setattr(Controller, "status", property(lambda self: Status(state="playing", index=1, total=3, text=text)))
    tray._tick()
    line = tray.item_now.text()
    assert line.startswith("Reading 1 of 3: word") and line.endswith("…") and len(line) <= 80


def test_hotkeys_show_at_the_right_without_being_shortcuts(tray):
    pretty = win_statusbar.shortcut_text
    hk = tray.ctl.cfg["hotkeys"]
    assert tray.item_read.text() == f"Read Selection\t{pretty(hk['speak'])}"
    assert [t.text().split("\t")[1] for t in tray.transport_items] == [pretty(hk[k]) for k in ("pause", "stop", "prev", "next")]
    assert tray.item_button.text() == f"Play Button on Selection\t{pretty(hk['button'])}"
    assert find(tray.speed_menu, "Faster").text().endswith(pretty(hk["faster"]))
    for menu in all_menus(tray.menu):
        for a in menu.actions():
            assert a.shortcut().isEmpty(), a.text()  # the global hook owns the keys


def test_provider_menu_groups_local_and_online(tray):
    r = rows(tray.provider_menu)
    assert r[0] == LOCAL_HEADER and KOKORO_ROW in r[:r.index(ONLINE_HEADER)]
    online = r[r.index(ONLINE_HEADER) + 1:]
    assert "Cartesia Sonic · no API key" in online and "Inworld TTS-2 · no API key" in online
    checked = [a.text() for a in tray.provider_menu.actions() if a.isChecked()]
    assert checked == [KOKORO_ROW]


def test_settings_menu(tray):
    assert rows(tray.settings_menu) == [
        "Play Button on Selection", "Auto-hide Squiggle", "Squiggle Wave", local_only_title(False), "API Keys", "---",
        "Edit Config File…", "Open Log", "Clear Audio Cache",
    ]
    keys = rows(tray.key_menu)
    assert "---" not in keys and not any(k.startswith("Remove") for k in keys)  # no key stored
    assert "Cartesia Sonic…" in keys


def test_local_only_hides_online_rows_and_api_keys(tray):
    tray.ctl.set_local_only(True)
    tray._refresh_checks()
    assert "API Keys" not in rows(tray.settings_menu)
    r = rows(tray.provider_menu)
    assert ONLINE_HEADER not in r and r[0] == LOCAL_HEADER and KOKORO_ROW in r
    assert find(tray.settings_menu, local_only_title(False)).isChecked()
    tray.ctl.set_local_only(False)
    tray._refresh_checks()
    assert ONLINE_HEADER in rows(tray.provider_menu) and "API Keys" in rows(tray.settings_menu)


def test_managed_local_only_is_greyed_out(tray, managed_prefs):
    managed_prefs["LocalOnly"] = True
    tray._refresh_checks()
    item = tray.item_local
    assert item.text() == local_only_title(True)
    assert not item.isEnabled() and item.isChecked()


def test_support_row_shows_once_the_website_has_an_address_and_carries_the_heart(tray, monkeypatch):
    from lyssna import support

    assert support.MENU_TITLE not in rows(tray.menu)
    opened = []
    monkeypatch.setattr(support, "SITE_URL", "https://example.test/")
    monkeypatch.setattr(support, "_open", opened.append)
    tray._tick()
    assert rows(tray.menu)[-4:] == ["About Lyssna", support.MENU_TITLE, "---", "Quit Lyssna"]
    assert not tray.item_support.icon().isNull()
    assert opened == []  # never opens on its own
    tray.item_support.trigger()
    assert opened == ["https://example.test/?support=1"]


def test_whats_new_row_shows_while_a_note_waits_and_goes_once_opened(tray):
    import lyssna
    from lyssna import whatsnew

    opened = []
    tray.on_whats_new = lambda: opened.append(True)
    tray.whats_new_pending = True
    tray._tick()
    title = whatsnew.menu_title(lyssna.__version__)
    r = rows(tray.menu)
    assert r[r.index(title) + 1] == "Settings"
    tray.item_whats_new.trigger()
    assert opened == [True] and title not in rows(tray.menu)
    assert whatsnew.read_seen() == lyssna.__version__


def test_usage_line_shows_only_with_a_cost(tray):
    from lyssna import usage

    usage.record("kokoro", 5000, 0.0)
    tray._apply_visibility()
    assert not tray.item_usage.isVisible()
    usage.record("cartesia", 2000, 50.0)
    tray._apply_visibility()
    r = rows(tray.menu)
    assert r[r.index(f"Speech stays on this {host.DEVICE}") + 1:][:3] == ["---", "This month: 2k characters, about $0.10", "Settings"]


def test_separators_never_double_up_lead_or_trail(tray, monkeypatch):
    from lyssna import support


    def check():
        for menu in all_menus(tray.menu):
            r = rows(menu)
            if not r:
                continue
            assert r[0] != "---" and r[-1] != "---", r
            assert all(not (a == b == "---") for a, b in itertools.pairwise(r)), r

    check()
    tray.ctl.set_local_only(True)
    tray._refresh_checks()
    check()
    monkeypatch.setattr(support, "SITE_URL", "https://example.test/")
    tray.show_permission_warning()
    tray._tick()
    check()


def test_no_submenu_title_ends_with_an_ellipsis(tray):
    for menu in all_menus(tray.menu):
        if menu is not tray.menu:
            assert not menu.title().endswith(("…", "...")), menu.title()


def test_permission_warning_sits_on_top_and_opens_setup(tray):
    from lyssna.ui.tray import HOOKS_WARNING_TITLE as WARNING_TITLE

    opened = []
    tray.on_setup = opened.append
    tray.show_permission_warning()
    tray._tick()
    assert rows(tray.menu)[:2] == [WARNING_TITLE, LISTEN_HEADER]
    assert not tray.item_warning.icon().isNull()
    assert tray.tray.toolTip() == WARNING_TITLE
    tray.item_warning.trigger()
    assert opened == [None]


# -- tooltip and icon ------------------------------------------------------------------------


def test_tooltip_follows_the_state(tray, monkeypatch):
    from lyssna.controller import Controller
    from lyssna.player import Status

    tray._tick()
    assert tray.tray.toolTip() == "Lyssna"
    for state, expected in (("playing", "Lyssna: Reading 2 of 5"), ("paused", "Lyssna: Paused 2 of 5"),
                            ("loading", "Lyssna: Loading")):
        monkeypatch.setattr(Controller, "status", property(lambda self, s=state: Status(state=s, index=2, total=5, text="Hi")))
        tray._tick()
        assert tray.tray.toolTip() == expected
    monkeypatch.setattr(tray.ctl, "voice_download_progress", lambda: 0.43)
    tray._tick()
    assert tray.tray.toolTip() == "Lyssna: Downloading voice, 43%" and tray.item_now.text() == "Downloading voice, 43%"
    monkeypatch.setattr(tray.ctl, "voice_download_progress", lambda: None)
    tray.ctl.message = "Nothing selected"
    tray._tick()
    assert tray.tray.toolTip() == "Lyssna: Nothing selected" and tray.item_now.text() == "Nothing selected"


def test_tray_icon_is_tinted_for_the_taskbar_at_every_size(qapp):
    from lyssna.win.statusbar import ICON_SIZES, WARNING_DOT, tray_icon, tray_pixmap

    def colours(pm):
        img = pm.toImage()
        return {QColor(img.pixel(x, y)).rgb() & 0xFFFFFF for x in range(img.width()) for y in range(img.height())
                if QColor.fromRgba(img.pixel(x, y)).alpha() == 255}

    assert colours(tray_pixmap(32, dark_taskbar=True)) == {0xFFFFFF}
    assert colours(tray_pixmap(32, dark_taskbar=False)) == {0x1C1C1C}
    amber = (WARNING_DOT[0] << 16) | (WARNING_DOT[1] << 8) | WARNING_DOT[2]
    assert amber in colours(tray_pixmap(32, dark_taskbar=True, warning=True))
    icon = tray_icon(True)
    assert sorted(s.width() for s in icon.availableSizes()) == sorted(ICON_SIZES)


def fake_winreg(value):
    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def query(key, name):
        if isinstance(value, Exception):
            raise value
        assert name == "SystemUsesLightTheme"
        return value, 4

    return types.SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=lambda root, path: Key(), QueryValueEx=query)


def test_taskbar_mode_comes_from_the_registry(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(1))
    assert win_statusbar.taskbar_is_dark() is False
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(0))
    assert win_statusbar.taskbar_is_dark() is True
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(OSError("no key")))
    assert win_statusbar.taskbar_is_dark() is True  # Windows 11's default


def test_the_windows_icon_is_redrawn_only_when_the_taskbar_or_the_warning_changes(qapp, monkeypatch):
    class FakeTray:
        def __init__(self):
            self.icons = []

        def setIcon(self, icon):
            self.icons.append(icon)

    dark = [True]
    monkeypatch.setattr(win_statusbar, "taskbar_is_dark", lambda: dark[0])
    t = FakeTray()
    win_statusbar.set_icon(t, False)
    win_statusbar.set_icon(t, False)
    assert len(t.icons) == 1
    dark[0] = False
    win_statusbar.set_icon(t, False)
    win_statusbar.set_icon(t, True)
    win_statusbar.set_icon(t, True, force=True)
    assert len(t.icons) == 4


def test_left_click_opens_the_menu(tray, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    shown = []
    monkeypatch.setattr(tray.menu, "popup", lambda pos: shown.append(pos))
    tray._activated(QSystemTrayIcon.ActivationReason.Trigger)
    tray._activated(QSystemTrayIcon.ActivationReason.Context)  # Windows opens the context menu itself
    assert len(shown) == 1


def test_a_click_on_the_mac_item_leaves_the_menu_to_the_system(make_tray, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    tray = make_tray(system="mac")
    shown = []
    monkeypatch.setattr(tray.menu, "popup", lambda pos: shown.append(pos))
    tray._activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert shown == []


# -- what the items call ---------------------------------------------------------------------


def test_speed_items_set_the_speed(tray):
    tray.speed_items[1.5].trigger()
    assert tray.ctl.reader.speed == pytest.approx(1.5) and config.load()["speed"] == pytest.approx(1.5)
    assert [s for s, a in tray.speed_items.items() if a.isChecked()] == [1.5]
    assert tray.speed_menu.title() == "Speed: 1.5×"
    find(tray.speed_menu, "Faster").trigger()
    assert tray.ctl.reader.speed == pytest.approx(1.6) and tray.speed_menu.title() == "Speed: 1.6×"


def test_clicking_the_checked_speed_keeps_it_checked(tray):
    current = next(a for a in tray.speed_items.values() if a.isChecked())
    current.trigger()  # Qt unchecks a checked item on its own; the refresh puts it back
    assert current.isChecked()


def test_voice_items_set_the_voice(tray):
    voices = dict(tray.ctl.voices())
    other = next(v for v in voices if v != tray.ctl.current_voice())
    item = next(a for a in tray.voice_items if a.text() == voices[other])
    item.trigger()
    assert tray.ctl.current_voice() == other and item.isChecked()
    assert tray.voice_menu.title() == f"Voice: {voices[other].split(' · ')[0]}"
    hidden = [a for a in tray.voice_items if not a.isVisible()]
    assert all(a.text() not in voices.values() for a in hidden)  # other providers' voices


def test_toggles_are_checked_persisted_and_applied(tray):
    applied = []
    tray.on_auto_hide = lambda on: applied.append(("auto_hide", on))
    tray.on_wave = lambda on: applied.append(("wave", on))
    tray.item_auto_hide.trigger()
    tray.item_wave.trigger()
    tray.item_button.trigger()
    assert applied == [("auto_hide", False), ("wave", False)]
    assert not tray.item_auto_hide.isChecked() and not tray.item_wave.isChecked() and not tray.item_button.isChecked()
    ui = config.load()["ui"]
    assert ui["auto_hide"] is False and ui["wave"] is False and ui["selection_button"] is False


def test_local_only_item_toggles_it(tray):
    tray.item_local.trigger()
    assert tray.ctl.local_only() and tray.item_local.isChecked()
    tray.item_local.trigger()
    assert not tray.ctl.local_only() and not tray.item_local.isChecked()


def test_choosing_a_provider_without_its_key_opens_setup(tray):
    opened = []
    tray.on_setup = opened.append
    tray.provider_items["cartesia"].trigger()
    assert opened == ["cartesia"]
    assert config.load()["provider"] == "kokoro"
    assert not tray.provider_items["cartesia"].isChecked() and tray.provider_items["kokoro"].isChecked()
    find(tray.key_menu, "Cartesia Sonic…").trigger()
    assert opened == ["cartesia", "cartesia"]


def test_choosing_a_provider_with_its_key_switches(tray, fake_keychain):
    from lyssna import keystore

    fake_keychain.items["cartesia"] = "k"
    keystore.clear_cache()
    tray._refresh_checks()
    tray.provider_items["cartesia"].trigger()
    assert config.load()["provider"] == "cartesia"
    assert tray.provider_menu.title() == "Voice Provider: Cartesia Sonic"


def test_an_online_provider_is_refused_in_local_only_mode(tray):
    tray.ctl.set_local_only(True)
    tray._set_provider("cartesia")
    assert tray.ctl.message == f"{local_only_title(False)} is on"
    assert config.load()["provider"] == "kokoro"


def test_remove_key_asks_first(tray, monkeypatch):
    from lyssna import keystore

    keystore.set("cartesia", "k")
    tray._refresh_checks()
    assert rows(tray.key_menu)[-2:] == ["---", "Remove Cartesia Sonic Key…"]
    asked = []

    def answer(choice):
        def exec_(box):
            asked.append((box.text(), box.informativeText()))
            next(b for b in box.buttons() if b.text() == choice).click()
            return 0
        return exec_

    monkeypatch.setattr(QMessageBox, "exec", answer("Cancel"))
    tray.remove_items["cartesia"].trigger()
    assert asked == [("Remove your Cartesia Sonic API key?", "You can add it again any time.")]
    assert tray.ctl.has_stored_key("cartesia")
    monkeypatch.setattr(QMessageBox, "exec", answer("Remove"))
    tray.remove_items["cartesia"].trigger()
    assert not tray.ctl.has_stored_key("cartesia")
    assert "---" not in rows(tray.key_menu) and not tray.remove_items["cartesia"].isVisible()


def test_transport_and_read_items_call_the_controller(tray, monkeypatch):
    called = []
    for name in ("speak_selection", "speak_clipboard", "toggle_pause", "stop", "prev", "next"):
        monkeypatch.setattr(tray.ctl, name, lambda n=name: called.append(n))
    from lyssna.ui.tray import Tray

    tray = Tray(tray.ctl)  # built again so the items hold the fakes
    tray.timer.stop()
    tray.item_read.trigger()
    tray.item_clipboard.trigger()
    for t in tray.transport_items:
        t.trigger()
    assert called == ["speak_selection", "speak_clipboard", "toggle_pause", "stop", "prev", "next"]
    tray._unsubscribe()


def test_settings_files_and_cache(tray, monkeypatch):
    popened, opened, cleared = [], [], []
    monkeypatch.setattr(win_statusbar.subprocess, "Popen", lambda args, **kw: popened.append(args))
    monkeypatch.setattr(host, "open_target", opened.append)
    monkeypatch.setattr(tray.ctl.reader.cache, "clear", lambda: cleared.append(1))
    find(tray.settings_menu, "Edit Config File…").trigger()
    find(tray.settings_menu, "Open Log").trigger()
    find(tray.settings_menu, "Clear Audio Cache").trigger()
    assert popened == [["notepad.exe", str(config.CONFIG_PATH)]]
    assert opened == [str(config.LOG_DIR / "lyssna.log")]
    assert cleared == [1]


def test_setup_and_about(tray, monkeypatch):
    from lyssna.ui import tray as tray_module

    opened, about = [], []
    tray.on_setup = opened.append
    monkeypatch.setattr(tray_module, "show_about", lambda: about.append(1))
    tray.item_setup.trigger()
    assert opened == [None]
    # The About item was connected to the real show_about; build its dialog directly.
    dialog = tray_module.AboutDialog()
    import lyssna

    # What the legacy About panel shows: name, version, the credits sentence and the link.
    assert dialog.name.text() == "Lyssna"
    assert dialog.version.text() == f"Version {lyssna.__version__}"
    assert dialog.license.text() == "Free and open source, GPL-3.0"
    assert 'href="https://github.com/andresvourakis/lyssna"' in dialog.link.text()
    assert ">github.com/andresvourakis/lyssna<" in dialog.link.text()
    from lyssna.ui import theme

    credits = theme.ui_font(tray_module.CREDITS_PT)
    assert dialog.license.font().pixelSize() == dialog.link.font().pixelSize() == credits.pixelSize()
    dialog._unsubscribe()


def test_dictation_engines_without_their_key_go_to_setup(tray):
    opened, dictation, toasts = [], [], []
    tray.on_setup = opened.append
    tray.on_setup_dictation = dictation.append
    tray.ctl.subscribe_toast(toasts.append)
    tray._set_transcriber("openai")
    tray._set_cleaner("haiku")
    assert dictation == ["openai", "haiku"]
    tray._set_transcriber("inworld")
    assert opened == ["inworld"] and toasts == []
    assert config.load()["dictation"]["transcriber"] == "parakeet"


def test_quit_calls_the_app_then_quits(tray, monkeypatch):
    """The loop ends with exit(0), which a window cannot refuse, never with quit()."""
    quit_called, app_exit, app_quit = [], [], []
    tray.on_quit = lambda: quit_called.append(1)
    monkeypatch.setattr(QApplication, "exit", lambda *args: app_exit.append(args[-1] if args else None))
    monkeypatch.setattr(QApplication, "quit", lambda *args: app_quit.append(1))
    find(tray.menu, "Quit Lyssna").trigger()
    assert quit_called == [1] and app_exit == [0] and app_quit == []


# -- the Dictate section (ported from legacy/test_menu.py) ------------------------------------


def test_dictation_engines_show_their_hints(tray, monkeypatch):
    from lyssna.dictation import models

    monkeypatch.setattr(models.ModelSet, "present", lambda self: False)
    tray._refresh_checks()
    assert rows(tray.transcription_menu) == [
        LOCAL_HEADER, "Parakeet · 630 MB download",
        ONLINE_HEADER, "OpenAI (gpt-transcribe) · no API key", "Inworld STT · no API key"]
    r = rows(tray.cleanup_menu)
    assert r[:2] == ["Off", LOCAL_HEADER] and r[-2:] == [ONLINE_HEADER, "Claude Haiku 5.5 · no API key"]
    assert [a.text() for a in tray.transcription_menu.actions() if a.isChecked()] == ["Parakeet · 630 MB download"]


def test_local_only_hides_online_dictation_engines(tray):
    tray.ctl.set_local_only(True)
    tray._refresh_checks()
    assert [r.split(" · ")[0] for r in rows(tray.transcription_menu)] == [LOCAL_HEADER, "Parakeet"]
    assert [r.split(" · ")[0] for r in rows(tray.cleanup_menu)] == ["Off", LOCAL_HEADER, "S1-mini by Superwhisper"]


def test_cleanup_off_is_checked(tray):
    tray._set_cleaner("")
    assert tray.cleanup_menu.title() == "Cleanup: Off"
    assert [a.text() for a in tray.cleanup_menu.actions() if a.isChecked()] == ["Off"]


def test_choosing_an_engine_with_its_key(tray, fake_keychain):
    from lyssna import keystore

    fake_keychain.items["openai"] = "k"
    keystore.clear_cache()  # building the menu already looked for it
    tray._set_transcriber("openai")
    assert tray.transcription_menu.title() == "Transcription: OpenAI (gpt-transcribe)"
    assert tray.item_dictation_privacy.text() == "Speech is sent to OpenAI"


def test_privacy_lines_show_where_things_go(tray, fake_keychain):
    from lyssna import keystore

    assert tray._privacy_symbols == {"reading": "lock", "dictation": "lock"}
    assert not tray.item_privacy.icon().isNull() and not tray.item_dictation_privacy.icon().isNull()
    assert tray.item_privacy.icon().isMask()  # a template image in a native Mac menu
    fake_keychain.items["openai"] = "k"
    keystore.clear_cache()
    tray._set_transcriber("openai")
    assert tray._privacy_symbols["dictation"] == "arrow.up.right"


def test_history_and_start_dictation_call_the_app(tray, monkeypatch):
    called = []
    tray.on_history = lambda: called.append("history")
    monkeypatch.setattr(tray.ctl, "dictation_start", lambda hands_free=False: called.append(("start", hands_free)))
    find(tray.menu, "History…").trigger()
    tray.item_dictate.trigger()
    assert called == ["history", ("start", True)]


def test_no_speak_or_engine_in_any_title(tray):
    for m in all_menus(tray.menu):
        for a in m.actions():
            assert "Speak" not in a.text() and "Engine" not in a.text(), a.text()


# -- the rules in lyssna/menu.py (ported from legacy/test_menu.py) ---------------------------


def test_visibility_rules():
    assert visibility(MenuState(player="idle"))["now_playing"] is False
    for player in ("loading", "playing", "paused"):
        assert visibility(MenuState(player=player))["now_playing"] is True
        assert visibility(MenuState(player=player))["transport"] is True
    assert visibility(MenuState(message="Nothing selected"))["now_playing"] is True
    assert visibility(MenuState(downloading=True))["now_playing"] is True
    assert visibility(MenuState(player="idle", message="Nothing selected", downloading=True))["transport"] is False
    assert visibility(MenuState(usage_line=""))["usage"] is False
    assert visibility(MenuState(usage_line="This month: 4k characters, about $0.10"))["usage"] is True
    assert visibility(MenuState(local_only=True))["api_keys"] is False
    assert visibility(MenuState(has_voices=False))["voice"] is False
    assert visibility(MenuState())["whats_new"] is False and visibility(MenuState())["support"] is False
    assert visibility(MenuState(whats_new=True))["whats_new"] and visibility(MenuState(support=True))["support"]


@pytest.mark.parametrize(("rows_", "expected"), [
    ([True, None, True], [True]),
    ([True, None, False, None, True], [True, False]),   # an empty group: one separator, not two
    ([None, True, None], [False, False]),              # never first or last
    ([True, None, False], [False]),                    # nothing visible after it
    ([False, None, True], [False]),                    # nothing visible before it
    ([True, None, None, True], [True, False]),
])
def test_separators_never_double_up_or_dangle(rows_, expected):
    assert separators_shown(rows_) == expected


def test_wording():
    d = host.DEVICE
    assert rules.local_only_title(False) == f"Keep Everything on This {d}"
    assert rules.local_only_title(True) == f"Keep Everything on This {d} (Set by Your Organization)"
    assert rules.provider_title("Cartesia Sonic", True, "no API key") == "Cartesia Sonic"
    assert rules.provider_title("Cartesia Sonic", False, "no API key") == "Cartesia Sonic · no API key"
    assert rules.now_playing("paused", 2, 5, "Hello") == "Paused 2 of 5: Hello"
    assert rules.now_playing("idle", 0, 0, "") == ""
    assert rules.engine_row_label(f"On this {d} (Kokoro)") == "Kokoro"
    assert rules.engine_row_label("Inworld TTS-2") == "Inworld TTS-2"
    assert rules.engine_row_label("OpenAI (gpt-transcribe)") == "OpenAI (gpt-transcribe)"
    assert rules.privacy_symbol(True) == "lock" and rules.privacy_symbol(False) == "arrow.up.right"
    assert rules.dictation_title("fn", recording=False) == "Start Dictation · hold fn"
    assert rules.dictation_title("right_option", recording=False) == f"Start Dictation · hold {rules.key_name('right_option')}"
    assert rules.dictation_title("fn", recording=True) == "Finish Dictation"
    assert rules.dictation_privacy("", "") == f"Speech stays on this {d}"
    assert rules.dictation_privacy("OpenAI", "") == "Speech is sent to OpenAI"
    assert rules.dictation_privacy("", "Anthropic") == f"Speech stays on this {d}, text is sent to Anthropic"
    assert rules.dictation_privacy("Inworld", "Anthropic") == "Speech is sent to Inworld, text is sent to Anthropic"
    assert rules.cleanup_title("") == "Cleanup: Off"
    assert rules.download_line("voice", 0.43) == "Downloading voice, 43%"
    assert rules.download_line("model", 0.43) == "Getting the speech model ready, 43%"


def test_no_menu_bar_text_while_dictating():
    for state in ("recording", "locked", "working"):
        assert rules.menubar_text_allowed(MenuState(dictation=state)) is False
    for state in ("idle", "notice"):
        assert rules.menubar_text_allowed(MenuState(dictation=state)) is True


# -- the title beside the icon (the Mac) ------------------------------------------------------


def test_menubar_title_is_the_legacy_text_in_every_state():
    from lyssna.ui.tray import WARNING_MARK, menubar_title

    assert menubar_title("idle", 0, 0) == ""
    assert menubar_title("loading", 1, 5) == "…"
    assert menubar_title("playing", 2, 5) == "2/5"
    assert menubar_title("paused", 2, 5) == "⏸ 2/5"
    assert menubar_title("playing", 2, 5, download="Downloading voice, 43%") == "Downloading voice, 43%"
    assert menubar_title("idle", 0, 0, message="x" * 90) == "x" * 60
    assert menubar_title("playing", 2, 5, dictating=True, message="Hi", warning=True) == ""  # the pill is the indicator
    assert menubar_title("idle", 0, 0, warning=True) == WARNING_MARK
    assert menubar_title("paused", 2, 5, warning=True) == "⏸ 2/5"


def test_the_mac_shows_the_title_beside_the_icon(make_tray, monkeypatch):
    from lyssna.controller import Controller
    from lyssna.player import Status

    tray = make_tray(system="mac")
    bar = host.statusbar
    tray._tick()
    assert bar.titles[-1] == ""
    for state, expected in (("loading", "…"), ("playing", "2/5"), ("paused", "⏸ 2/5")):
        monkeypatch.setattr(Controller, "status", property(lambda self, s=state: Status(state=s, index=2, total=5, text="Hi")))
        tray._tick()
        assert bar.titles[-1] == expected
    assert bar.tooltips[-1] == "Lyssna: Paused 2 of 5"  # for the Mac twin's fallback, when the button cannot be found
    monkeypatch.setattr(tray.ctl, "voice_download_progress", lambda: 0.43)
    tray._tick()
    assert bar.titles[-1] == "Downloading voice, 43%"
    monkeypatch.setattr(tray.ctl, "voice_download_progress", lambda: None)
    tray.ctl.message = "Nothing selected"
    tray._tick()
    assert bar.titles[-1] == "Nothing selected"
    tray.ctl.message = ""
    monkeypatch.setattr(type(tray.ctl.dictation), "state", property(lambda self: "recording"))
    tray._tick()
    assert bar.titles[-1] == ""


def test_the_mac_warning_asks_for_accessibility_and_marks_the_icon(make_tray):
    from lyssna.ui.tray import ACCESSIBILITY_WARNING_TITLE, WARNING_MARK

    tray = make_tray(system="mac")
    tray.show_permission_warning()
    assert rows(tray.menu)[:2] == [ACCESSIBILITY_WARNING_TITLE, LISTEN_HEADER]
    assert host.statusbar.titles[-1] == WARNING_MARK
    assert host.statusbar.icons[-1] is True


def test_windows_shows_nothing_beside_the_icon(tray, monkeypatch):
    from lyssna.controller import Controller
    from lyssna.player import Status

    calls = []
    monkeypatch.setattr(win_statusbar, "show_status", lambda t, title, tip: calls.append((title, tip)))
    monkeypatch.setattr(Controller, "status", property(lambda self: Status(state="paused", index=2, total=5, text="Hi")))
    tray._tick()
    assert calls[-1] == ("", "Lyssna: Paused 2 of 5")


def test_headers_are_handed_to_the_system_on_show_and_when_a_menu_opens(make_tray, monkeypatch):
    tray = make_tray(system="mac")
    monkeypatch.setattr(tray.tray, "show", lambda: None)
    tray.show()
    assert host.statusbar.headers == [HEADERS]
    tray.transcription_menu.aboutToShow.emit()
    assert len(host.statusbar.headers) == 2
    for title in (LISTEN_HEADER, DICTATE_HEADER):
        item = find(tray.menu, title)
        assert not item.isEnabled() and item.data() == "header"


def test_the_config_file_opens_through_the_system(make_tray):
    tray = make_tray(system="mac")
    find(tray.settings_menu, "Edit Config File…").trigger()
    assert host.statusbar.opened == [config.CONFIG_PATH]


# -- shortcuts --------------------------------------------------------------------------------


def test_the_mac_writes_qt_key_sequences_qt_turns_into_native_glyphs():
    from PySide6.QtCore import Qt

    m = mac_statusbar.shortcut_text
    assert m("ctrl+alt+s") == "Meta+Alt+S"  # Meta is Control on a Mac, Ctrl is Command
    assert m("cmd+q") == "Ctrl+Q"
    assert m("ctrl+alt+space") == "Meta+Alt+Space" and m("ctrl+alt+left") == "Meta+Alt+Left"
    assert m("cmd+shift+f5") == "Ctrl+Shift+F5" and m("") == ""
    for spec in config.DEFAULTS["hotkeys"].values():  # each default is one key with Control and Option
        seq = QKeySequence.fromString(m(spec), QKeySequence.SequenceFormat.PortableText)
        assert seq.count() == 1, spec
        mods = seq[0].keyboardModifiers()
        assert mods == Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier, spec
        assert seq[0].key() != Qt.Key.Key_unknown, spec


def test_windows_writes_the_key_names():
    w = win_statusbar.shortcut_text
    assert w("ctrl+alt+s") == "Ctrl+Alt+S" and w("ctrl+alt+right") == "Ctrl+Alt+Right" and w("") == ""
    assert win_statusbar.QUIT_SHORTCUT == "" and mac_statusbar.QUIT_SHORTCUT == "cmd+q"


def test_the_mac_menu_carries_native_shortcut_text_and_quit_shows_command_q(make_tray):
    tray = make_tray(system="mac")
    assert tray.item_read.text() == "Read Selection\tMeta+Alt+S"
    assert find(tray.menu, "Quit Lyssna").text() == "Quit Lyssna\tCtrl+Q"
    assert find(make_tray().menu, "Quit Lyssna").text() == "Quit Lyssna"  # Windows: none
    for m in all_menus(tray.menu):
        for a in m.actions():
            assert a.shortcut().isEmpty(), a.text()  # tab text only: the global key tap owns the keys
            if not a.isSeparator():
                assert a.menuRole() == QAction.MenuRole.NoRole, a.text()  # "Edit Config File…" stays put


# -- lyssna/mac/statusbar.py against a fake AppKit ------------------------------------------


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def fake_appkit(*, with_header_setter=True, status_item=True):
    """Just the AppKit objects the Mac twin touches: NSApp's windows, a status bar window with
    Qt's NSStatusBarButton, its status item and the native menu."""
    calls = []

    class NSStatusBarButton:
        def __init__(self):
            self._title, self._image, self.window_ = "", None, None

        def target(self):
            return type("QStatusItemDelegate", (), {})()

        def subviews(self):
            return []

        def window(self):
            return self.window_

        def valueForKey_(self, key):
            calls.append(("valueForKey", key))
            return item if status_item else None

        def setImagePosition_(self, pos):
            calls.append(("imagePosition", pos))

        def title(self):
            return self._title

        def setTitle_(self, t):
            calls.append(("title", t))
            self._title = t

        def image(self):
            return self._image

        def setImage_(self, image):
            calls.append(("image", image))
            self._image = image

    class NSMenuItem:
        @staticmethod
        def instancesRespondToSelector_(sel):
            return with_header_setter and sel == "setSectionHeader:"

        def __init__(self, title, submenu=None, separator=False):
            self._title, self._submenu, self._sep, self.header = title, submenu, separator, False

        def title(self):
            return self._title

        def submenu(self):
            return self._submenu

        def isSeparatorItem(self):
            return self._sep

        def isSectionHeader(self):
            return self.header

        def setSectionHeader_(self, on):
            self.header = on

    class NSMenu:
        def __init__(self, items):
            self.items = items

        def itemArray(self):
            return self.items

    class NSImage:
        @classmethod
        def alloc(cls):
            return cls()

        def initByReferencingFile_(self, path):
            self.path = path
            return self

        def setScalesWhenResized_(self, on):
            pass

        def setSize_(self, size):
            self.size = size

        def setTemplate_(self, on):
            self.template = on

        def isEqual_(self, other):
            return self is other

    class NSStatusBarWindow:
        def contentView(self):
            return _Obj(subviews=lambda: [button])

    sub = NSMenu([NSMenuItem(LOCAL_HEADER), NSMenuItem("Kokoro")])
    native_menu = NSMenu([NSMenuItem(LISTEN_HEADER), NSMenuItem("Read Selection"), NSMenuItem("", separator=True),
                          NSMenuItem("Voice Provider: Kokoro", submenu=sub), NSMenuItem(DICTATE_HEADER)])
    item = _Obj(setLength_=lambda n: calls.append(("length", n)), menu=lambda: native_menu)
    button = NSStatusBarButton()
    window = NSStatusBarWindow()
    button.window_ = window
    appkit = types.ModuleType("AppKit")
    appkit.__dict__.update(NSApp=_Obj(windows=lambda: [window]), NSStatusBarButton=NSStatusBarButton, NSMenuItem=NSMenuItem,
                           NSImage=NSImage, NSVariableStatusItemLength=-1, NSImageLeading=7)
    appkit.calls, appkit.button, appkit.menu, appkit.sub = calls, button, native_menu, sub
    return appkit


class FakeQtTray:
    def __init__(self, visible=True):
        self.visible, self.tip = visible, "Lyssna"

    def isVisible(self):
        return self.visible

    def toolTip(self):
        return self.tip

    def setToolTip(self, tip):
        self.tip = tip


def test_mac_title_goes_on_qt_s_own_status_button(monkeypatch):
    appkit = fake_appkit()
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    t = FakeQtTray()
    mac_statusbar.show_status(t, "⏸ 2/5", "Lyssna: Paused 2 of 5")
    assert ("valueForKey", "statusItem") in appkit.calls
    assert ("length", -1) in appkit.calls and ("imagePosition", 7) in appkit.calls  # variable length, image leading
    assert appkit.button.title() == "⏸ 2/5" and t.toolTip() == ""  # the legacy item has no tooltip
    image = appkit.button.image()
    assert image.size == (20, 20) and image.template and image.path.endswith("menubar.png")  # as rumps makes it
    before = len(appkit.calls)
    mac_statusbar.show_status(t, "⏸ 2/5", "Lyssna: Paused 2 of 5")
    assert len(appkit.calls) == before  # nothing changed, nothing set
    mac_statusbar.show_status(t, "", "Lyssna")
    assert appkit.button.title() == ""


@pytest.mark.parametrize("broken", ["no button", "no status item"])
def test_mac_title_falls_back_to_the_tooltip_and_logs_once(monkeypatch, caplog, broken):
    appkit = fake_appkit(status_item=broken != "no status item")
    if broken == "no button":
        appkit.NSApp = _Obj(windows=lambda: [])
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    t = FakeQtTray()
    with caplog.at_level("WARNING", logger="lyssna.mac.statusbar"):
        mac_statusbar.show_status(t, "2/5", "Lyssna: Reading 2 of 5")
        mac_statusbar.show_status(t, "3/5", "Lyssna: Reading 3 of 5")
    assert t.toolTip() == "Lyssna: Reading 3 of 5"
    assert len([r for r in caplog.records if "tooltip" in r.getMessage()]) == 1


def test_mac_title_waits_for_the_item_to_show(monkeypatch, caplog):
    appkit = fake_appkit()
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    t = FakeQtTray(visible=False)
    with caplog.at_level("WARNING", logger="lyssna.mac.statusbar"):
        mac_statusbar.show_status(t, "2/5", "Lyssna: Reading 2 of 5")
    assert not appkit.calls and not caplog.records and t.toolTip() == "Lyssna: Reading 2 of 5"


def test_mac_headers_become_native_section_headers(monkeypatch):
    appkit = fake_appkit()
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    mac_statusbar.mark_headers(FakeQtTray(), HEADERS)
    marked = [i.title() for m in (appkit.menu, appkit.sub) for i in m.itemArray() if i.isSectionHeader()]
    assert marked == [LISTEN_HEADER, DICTATE_HEADER, LOCAL_HEADER]


def test_mac_headers_stay_greyed_rows_without_the_setter(monkeypatch):
    appkit = fake_appkit(with_header_setter=False)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    mac_statusbar.mark_headers(FakeQtTray(), HEADERS)
    assert not any(i.isSectionHeader() for i in appkit.menu.itemArray())


def test_mac_icon_is_a_template_image_set_once(qapp):
    from PySide6.QtWidgets import QSystemTrayIcon

    t = QSystemTrayIcon()
    mac_statusbar.set_icon(t, warning=False)
    icon = t.icon()
    assert icon.isMask() and not icon.isNull()
    mac_statusbar.set_icon(t, warning=True, force=True)  # the Mac marks a warning with ⚠︎ beside it instead
    assert t.icon().cacheKey() == icon.cacheKey()


def test_windows_headers_are_dim_semibold_rows(qapp):
    from PySide6.QtWidgets import QMenu

    m = QMenu()
    a = QAction("Listen", m)
    win_statusbar.style_header(a, m)
    assert a.font().weight() > m.font().weight()


# -- opt-in manual check -------------------------------------------------------------------

MANUAL = textwrap.dedent(
    """
    import json, sys
    import AppKit
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    from lyssna.mac import statusbar

    app = QApplication([sys.argv[0]])
    AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    def pump(s):
        loop = QEventLoop(); QTimer.singleShot(int(s * 1000), loop.quit); loop.exec()

    m = QMenu()
    for text, enabled in (("Listen", False), ("Read Selection\\t" + statusbar.shortcut_text("ctrl+alt+s"), True),
                          ("Quit Lyssna\\t" + statusbar.shortcut_text(statusbar.QUIT_SHORTCUT), True)):
        a = QAction(text, m); a.setMenuRole(QAction.MenuRole.NoRole); a.setEnabled(enabled); m.addAction(a)
    tray = QSystemTrayIcon()
    statusbar.set_icon(tray, False)
    tray.setContextMenu(m)
    tray.show()
    pump(0.5)
    statusbar.show_status(tray, "⏸ 2/5", "Lyssna: Paused 2 of 5")
    statusbar.mark_headers(tray, {"Listen"})
    st = statusbar._state(tray)
    items = {str(i.title()): i for i in st.status_item.menu().itemArray()}
    out = {"title": str(st.button.title()), "template": bool(st.button.image().isTemplate()),
           "header": bool(items["Listen"].isSectionHeader()),
           "read_key": [str(items["Read Selection"].keyEquivalent()), int(items["Read Selection"].keyEquivalentModifierMask())],
           "quit_key": [str(items["Quit Lyssna"].keyEquivalent()), int(items["Quit Lyssna"].keyEquivalentModifierMask())]}
    pump(3.0)  # look at the menu bar now: the icon and "⏸ 2/5"
    tray.hide()
    print(json.dumps(out))
    """
)


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("LYSSNA_MANUAL_MENUBAR"),
                    reason="opt-in manual check on a Mac: LYSSNA_MANUAL_MENUBAR=1")
def test_manual_menubar_item_on_this_mac():
    """Shows a real menu bar item (not the app) for about three seconds; look for the icon with
    "⏸ 2/5" beside it. Checks the title, the template icon, the native section header and the
    ⌃⌥S and ⌘Q key equivalents on the native menu."""
    import AppKit

    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", MANUAL], cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    ctrl_opt = int(AppKit.NSEventModifierFlagControl | AppKit.NSEventModifierFlagOption)
    assert out == {"title": "⏸ 2/5", "template": True, "header": True, "read_key": ["s", ctrl_opt],
                   "quit_key": ["q", int(AppKit.NSEventModifierFlagCommand)]}
