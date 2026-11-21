"""The Windows tray menu (lyssna/ui/qt/tray.py), built from a real controller like the Mac menu in
test_menu.py: its rows in each state, its separators, its tooltip, its icon and what each item
calls. Runs on the offscreen platform; nothing is shown and the app never runs."""

import itertools
import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6.QtWidgets")

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMessageBox

from lyssna import config, host
from lyssna.menu import DICTATE_HEADER, LISTEN_HEADER, LOCAL_HEADER, ONLINE_HEADER, local_only_title
from lyssna.providers import kokoro

KOKORO_ROW = f"Kokoro · {kokoro.DOWNLOAD_SIZE_TEXT} download"  # 140 MB on the Mac, 200 MB on Windows


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("a non-GUI Qt application already exists in this process")
    return app or QApplication(["lyssna-tests"])


@pytest.fixture
def make_tray(qapp, monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)  # no download
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE)
    from lyssna.controller import Controller
    from lyssna.ui.qt.tray import Tray

    made = []

    def make(features=None):
        tray = Tray(Controller(), features)
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
    from lyssna.ui.qt.tray import label_of

    return ["---" if a.isSeparator() else label_of(a) for a in menu.actions() if a.isVisible()]


def find(menu, title: str):
    from lyssna.ui.qt.tray import label_of

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
    assert rows(tray.menu) == [
        LISTEN_HEADER, "Read Selection", "Read Clipboard",
        "Speed: 1.2×", "Voice: Heart", kokoro_provider_title(tray), f"Text stays on this {host.DEVICE}", "---",
        "Settings", "Setup…", "About Lyssna", "---",
        "Quit Lyssna",
    ]


def test_the_dictate_section_waits_for_its_feature_flag(make_tray):
    tray = make_tray({"dictate": True, "history": True})
    r = rows(tray.menu)
    start = r.index(f"Text stays on this {host.DEVICE}") + 1
    assert r[start:start + 3] == ["---", DICTATE_HEADER, tray._dictate_title()]
    assert r[start + 3] == "History…"
    assert r[start + 4].startswith("Transcription: ") and r[start + 5].startswith("Cleanup: ")
    assert r[start + 6:start + 9] == [f"Speech stays on this {host.DEVICE}", "---", "Settings"]

    tray = make_tray({"dictate": True})  # history off: the rest of the section shows
    assert "History…" not in rows(tray.menu) and DICTATE_HEADER in rows(tray.menu)
    tray.features["dictate"] = False
    tray._tick()
    assert DICTATE_HEADER not in rows(tray.menu)


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
    from lyssna.hotkeys import pretty

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
    assert r[r.index(f"Text stays on this {host.DEVICE}") + 1:][:3] == ["---", "This month: 2k characters, about $0.10", "Settings"]


@pytest.mark.parametrize("features", [None, {"dictate": True, "history": True}])
def test_separators_never_double_up_lead_or_trail(make_tray, monkeypatch, features):
    from lyssna import support

    tray = make_tray(features)

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


def test_no_submenu_title_ends_with_an_ellipsis(make_tray):
    tray = make_tray({"dictate": True, "history": True})
    for menu in all_menus(tray.menu):
        if menu is not tray.menu:
            assert not menu.title().endswith(("…", "...")), menu.title()


def test_permission_warning_sits_on_top_and_opens_setup(tray):
    from lyssna.ui.qt.tray import WARNING_TITLE

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
    from lyssna.ui.qt.tray import ICON_SIZES, WARNING_DOT, tray_icon, tray_pixmap

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


def test_taskbar_mode_off_windows_follows_the_scheme(qapp):
    from lyssna.ui.qt import theme, tray

    if host.IS_WINDOWS:
        pytest.skip("Windows reads the registry")
    try:
        theme.set_scheme("dark")
        assert tray.taskbar_is_dark() is True
        theme.set_scheme("light")
        assert tray.taskbar_is_dark() is False
    finally:
        theme.set_scheme(None)


def test_left_click_opens_the_menu(tray, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    shown = []
    monkeypatch.setattr(tray.menu, "popup", lambda pos: shown.append(pos))
    tray._activated(QSystemTrayIcon.ActivationReason.Trigger)
    tray._activated(QSystemTrayIcon.ActivationReason.Context)  # Windows opens the context menu itself
    assert len(shown) == 1


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
    from lyssna import keychain

    fake_keychain.items["cartesia"] = "k"
    keychain.clear_cache()
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
    from lyssna import keychain

    keychain.set("cartesia", "k")
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
    from lyssna.ui.qt.tray import Tray

    tray = Tray(tray.ctl)  # built again so the items hold the fakes
    tray.timer.stop()
    tray.item_read.trigger()
    tray.item_clipboard.trigger()
    for t in tray.transport_items:
        t.trigger()
    assert called == ["speak_selection", "speak_clipboard", "toggle_pause", "stop", "prev", "next"]
    tray._unsubscribe()


def test_settings_files_and_cache(tray, monkeypatch):
    from lyssna.ui.qt import tray as tray_module

    popened, opened, cleared = [], [], []
    monkeypatch.setattr(host, "IS_WINDOWS", True)
    monkeypatch.setattr(tray_module.subprocess, "Popen", lambda args, **kw: popened.append(args))
    monkeypatch.setattr(host, "open_target", opened.append)
    monkeypatch.setattr(tray.ctl.reader.cache, "clear", lambda: cleared.append(1))
    find(tray.settings_menu, "Edit Config File…").trigger()
    find(tray.settings_menu, "Open Log").trigger()
    find(tray.settings_menu, "Clear Audio Cache").trigger()
    assert popened == [["notepad.exe", str(config.CONFIG_PATH)]]
    assert opened == [str(config.LOG_DIR / "lyssna.log")]
    assert cleared == [1]


def test_setup_and_about(tray, monkeypatch):
    from lyssna.ui.qt import tray as tray_module

    opened, about = [], []
    tray.on_setup = opened.append
    monkeypatch.setattr(tray_module, "show_about", lambda: about.append(1))
    tray.item_setup.trigger()
    assert opened == [None]
    # The About item was connected to the real show_about; build its dialog directly.
    dialog = tray_module.AboutDialog()
    import lyssna

    assert dialog.version.text() == f"Version {lyssna.__version__}"
    assert dialog.license.text() == "Free and open source, GPL-3.0"
    assert "github.com/andresvourakis/lyssna" in dialog.link.text()


def test_dictation_engines_without_their_key_go_to_setup(make_tray):
    tray = make_tray({"dictate": True, "history": True})
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
