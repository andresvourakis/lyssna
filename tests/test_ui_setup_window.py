"""The setup window (lyssna/ui/setup_window.py), built for real but never shown: it is laid out
on the offscreen platform and its views are read back. A real Controller runs against the temp
config from conftest; the voice files, the download state, the microphone, the Accessibility
check, the Globe key setting and the key checks are faked, so nothing reaches the network, a
paid service or a system prompt. The capabilities in lyssna.host are forced per test, so both
the Mac's and the PC's window are checked on any computer. Also the controller's dictation key
path, ported from tests/legacy/test_setup_window.py, with httpx faked."""

import logging
import sys
import threading
import time

import httpx
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtWidgets import QApplication

from lyssna import config, host, keystore
from lyssna.dictation import models
from lyssna.dictation.cleaners import s1mini
from lyssna.dictation.transcribers import parakeet
from lyssna.providers import REGISTRY, kokoro


def pump(until, seconds: float = 2.0) -> None:
    """Run the Qt event loop until `until()` holds (or `seconds` pass)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        QCoreApplication.processEvents()
        time.sleep(0.005)


@pytest.fixture
def ctl(monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)
    monkeypatch.setattr(parakeet.ParakeetTranscriber, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(s1mini.S1MiniCleaner, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(models.ModelSet, "present", lambda self: False)
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    from lyssna.controller import Controller

    c = Controller()
    c.mic = "granted"
    monkeypatch.setattr(c, "microphone_status", lambda: c.mic)
    return c


class Machine:
    """The Accessibility answer and the Globe key setting a test's window sees."""

    def __init__(self):
        self.trusted = True
        self.globe = "emoji"
        self.prompts: list[bool] = []

    def accessibility_trusted(self, prompt: bool = False) -> bool:
        self.prompts.append(prompt)
        return self.trusted


@pytest.fixture
def machine():
    return Machine()


@pytest.fixture
def make(qapp, ctl, machine, monkeypatch):
    """make(system="pc", taps_ok=True, engine=None, dictation=None) -> a
    prepared SetupWindow with that system's capabilities, never shown."""
    from lyssna.ui.setup_window import SetupWindow

    made = []

    def build(system: str = "pc", taps_ok: bool = True, engine: str | None = None,
              dictation: str | None = None) -> SetupWindow:
        mac = system == "mac"
        monkeypatch.setattr(host, "NEEDS_ACCESSIBILITY", mac)
        monkeypatch.setattr(host, "HAS_GLOBE_KEY", mac)
        monkeypatch.setattr(host, "DEVICE", "Mac" if mac else "PC")
        if mac:  # the Mac's dictation key (fn, no double tap), whatever this computer's default is
            ctl.cfg = config.update(**{"dictation.hotkey": "fn", "dictation.double_tap": False})
        w = SetupWindow(ctl)
        w.taps_ok = lambda: taps_ok
        w.accessibility_trusted = machine.accessibility_trusted
        w.globe_action = lambda: machine.globe
        w._globe = machine.globe
        w.prepare(engine, dictation)
        pump(lambda: not w._globe_busy, 0.5)
        made.append(w)
        return w

    yield build
    for w in made:
        w.hide()
        w.deleteLater()
    QCoreApplication.processEvents()


@pytest.fixture
def window(make):
    return make()


@pytest.fixture
def mac(make):
    """The Mac's window on the Dictate tab, as the legacy tests start (most are about Dictate)."""
    from lyssna.ui.setup_window import DICTATE

    w = make("mac")
    w._select_tab(DICTATE)
    return w


def shown(view) -> bool:
    return not view.isHidden()


def refresh(w) -> None:
    w._refresh(force=True)


# -- the tabs ----------------------------------------------------------------------------------


def test_steps_left_and_their_wording():
    from lyssna.ui.layout.setup import footer_status, steps_left, tab_status

    assert steps_left((True, True)) == 0 and steps_left((False, True)) == 1 and steps_left((False, False)) == 2
    assert [tab_status(n) for n in (0, 1, 2)] == ["ready", "1 step left", "2 steps left"]
    assert footer_status(0, 1) == "Listen ready · Dictate: 1 step left"
    assert footer_status(2, 0) == "Listen: 2 steps left · Dictate ready"


def test_which_tab_opens():
    from lyssna.ui.layout.setup import DICTATE, LISTEN, opening_tab

    assert opening_tab(None, "haiku", 0, 0) == DICTATE  # a dictation key from the menu
    assert opening_tab("cartesia", None, 0, 2) == LISTEN  # a voice key from the menu
    assert opening_tab(None, None, 1, 2) == LISTEN  # the first tab with steps left
    assert opening_tab(None, None, 0, 1) == DICTATE
    assert opening_tab(None, None, 0, 0) == LISTEN  # all done


def test_dictate_has_its_tab_beside_listen(window):
    assert shown(window.switch) and window.tab_listen.isChecked() and not window.tab_dictate.isChecked()
    assert window.subtitle.value() == "Lyssna reads text aloud and types what you say. Set up each one below."


def test_show_picks_the_tab(mac, ctl, monkeypatch):
    from lyssna.ui.setup_window import DICTATE, LISTEN

    mac.prepare(None, None, opening=True)
    assert mac.tab == LISTEN and mac._left == (1, 2)  # no voice ready yet: Listen has a step left
    monkeypatch.setattr(ctl, "provider_configured", lambda name: True)
    mac.prepare(None, None, opening=True)
    assert mac.tab == DICTATE and mac.tab_dictate.isChecked() and not mac.tab_listen.isChecked()
    mac.prepare("cartesia", None, opening=False)
    assert mac.tab == LISTEN and mac.radio_key.isChecked()
    mac.prepare(None, "openai", opening=False)
    assert mac.tab == DICTATE and mac.dict_online.isChecked() and shown(mac.key_rows[0].field)


def test_the_timer_never_switches_tabs(mac, ctl, monkeypatch):
    from lyssna.ui.setup_window import LISTEN

    mac._select_tab(LISTEN)
    monkeypatch.setattr(ctl, "provider_configured", lambda name: True)  # Listen ready: a fresh open would pick Dictate
    mac.tick()
    mac.prepare(None, None, opening=False)  # Setup… again while the window is open
    assert mac.tab == LISTEN and mac.tab_listen.isChecked()
    assert mac.tab_listen.status == "ready" and mac.tab_dictate.status == "2 steps left"


def test_switch_and_footer_say_what_is_left(mac):
    assert mac.tab_listen.status == "1 step left" and mac.tab_dictate.status == "2 steps left"
    assert mac.footer_status.value() == "Listen: 1 step left · Dictate: 2 steps left"
    mac._globe = "nothing"
    refresh(mac)
    assert mac.tab_dictate.status == "1 step left"
    assert mac.tab_dictate.accessibleName() == "Dictate, 1 step left"


def test_each_tab_shows_only_its_own_steps(mac):
    from lyssna.ui.setup_window import DICTATE, LISTEN

    mac._select_tab(LISTEN)
    assert shown(mac.sample) and shown(mac.radio_local) and shown(mac.head_permission)
    assert not shown(mac.mic_row) and not shown(mac.dict_local) and not shown(mac.free_keys)
    mac._select_tab(DICTATE)
    assert shown(mac.mic_row) and shown(mac.dict_local) and shown(mac.free_keys)
    assert not shown(mac.sample) and not shown(mac.radio_local) and not shown(mac.head_permission)


def test_a_click_anywhere_on_a_segment_switches_tabs(mac):
    from PySide6.QtTest import QTest

    for tab, name in ((mac.tab_listen, "listen"), (mac.tab_dictate, "dictate")):
        for fx, fy in ((0.5, 0.5), (0.95, 0.9), (0.05, 0.1)):
            mac._select_tab("dictate" if name == "listen" else "listen")
            QTest.mouseClick(tab, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                             QPoint(round(tab.width() * fx), round(tab.height() * fy)))
            assert mac.tab == name
            assert (mac.tab_listen.isChecked(), mac.tab_dictate.isChecked()) == (name == "listen", name == "dictate")


def test_the_header_is_centred_as_on_the_mac(window):
    from lyssna.ui.layout.setup import W

    for view in (window.mark, window.title, window.switch):
        assert abs(view.geometry().center().x() - W / 2) <= 1, view
    assert window.mark.geometry().top() < window.title.geometry().top() < window.subtitle.geometry().top()
    assert window.subtitle.geometry().bottom() < window.switch.geometry().top() < window.header_line.geometry().top()
    assert shown(window.header_line)
    assert window.tab_listen.width() == window.tab_dictate.width()  # equal halves


def test_the_header_and_footer_stay_outside_the_scrolling_steps(make):
    from lyssna.ui.setup_window import DICTATE, LISTEN

    w = make("mac")
    for tab in (LISTEN, DICTATE):
        w._select_tab(tab)
        assert w.done.parent() is w.footer and w.switch.parent() is w.header
        assert w.footer.geometry().bottom() + 1 == w.height()
        assert w.scroll_area.geometry().top() == w.header.height()


def test_on_a_short_screen_only_the_steps_scroll(window, monkeypatch):
    monkeypatch.setattr(type(window), "height_limit", lambda self: 0.0)  # no room: the window's smallest height
    window._refresh(force=True)
    assert window.height() == 320
    assert window.content.height() > window.scroll_area.height()
    assert shown(window.header_line)


# -- Listen: the steps by capability -----------------------------------------------------------


def test_a_pc_has_two_listen_steps_voice_and_try_it(window):
    assert (window.icon_voice.number, window.head_voice.value()) == (1, "Voice")
    assert (window.icon_try.number, window.head_try.value()) == (2, "Try it")
    assert shown(window.radio_local) and shown(window.sample) and shown(window.keycap)
    assert not hasattr(window, "icon_permission")  # no permission step without NEEDS_ACCESSIBILITY
    assert not shown(window.try_note)


def test_a_mac_has_the_permission_step_first(make, machine):
    w = make("mac")
    assert (w.icon_permission.number, w.head_permission.value()) == (1, "Permission")
    assert (w.icon_voice.number, w.icon_try.number) == (2, 3)
    assert w.head_permission.geometry().top() < w.head_voice.geometry().top() < w.head_try.geometry().top()
    assert not hasattr(w, "warn_text")


def test_the_permission_step_in_its_three_states(make, machine):
    machine.trusted = False
    w = make("mac")
    assert w.body_permission.value() == ("The shortcut and the play button need this to read what you select. "
                                         "Click Open Settings and turn on Lyssna. If it is not in the list, "
                                         "click + and choose Lyssna.")
    assert shown(w.open_settings) and not shown(w.relaunch_btn) and not w.icon_permission.done
    assert shown(w.try_note) and w.try_note.value() == "The shortcut works once step 1 is done. The sample works now."
    assert w.footer_status.value() == "Listen: 2 steps left · Dictate: 2 steps left"

    machine.trusted = True
    w.taps_ok = lambda: False
    refresh(w)
    assert w.body_permission.value() == "Allowed. Relaunch Lyssna once so the shortcut starts working."
    assert shown(w.relaunch_btn) and not shown(w.open_settings) and not shown(w.try_note)
    assert not w.icon_permission.done

    w.taps_ok = lambda: True
    refresh(w)
    assert w.body_permission.value() == "Allowed. The shortcut and the play button are ready."
    assert not shown(w.relaunch_btn) and not shown(w.open_settings) and w.icon_permission.done


def test_open_settings_asks_then_opens_the_accessibility_pane(make, machine, monkeypatch):
    opened = []
    monkeypatch.setattr(host, "open_target", opened.append)
    monkeypatch.setattr(host, "ACCESSIBILITY_SETTINGS_URL", "x-apple.systempreferences:accessibility")
    machine.trusted = False
    w = make("mac")
    machine.prompts.clear()
    w.open_settings.click()
    assert machine.prompts == [True] and opened == ["x-apple.systempreferences:accessibility"]


def test_relaunch_on_the_permission_step(make):
    w = make("mac", taps_ok=False)
    relaunched = []
    w.relaunch = lambda: relaunched.append(1)
    w.relaunch_btn.click()
    assert relaunched == [1]


def test_the_warning_row_follows_the_shortcuts_on_a_pc(make):
    ok = make(taps_ok=True)
    assert not shown(ok.warn_text) and not shown(ok.relaunch_btn)
    down = make(taps_ok=False)
    assert shown(down.warn_text) and shown(down.relaunch_btn)
    assert down.warn_text.value() == ("Lyssna could not start its keyboard shortcuts. Quit other tools that capture "
                                      "the keyboard, then relaunch.")
    assert down.footer_status.value() == "Listen: 2 steps left · Dictate: 1 step left"  # no Globe key on a PC
    relaunched = []
    down.relaunch = lambda: relaunched.append(1)
    down.relaunch_btn.click()
    assert relaunched == [1]
    assert down.relaunch_btn.geometry().bottom() < down.head_voice.geometry().top()  # above the steps


def test_voice_step_is_done_once_the_voice_can_read(window, ctl, monkeypatch):
    assert not window.icon_voice.done and not window.icon_try.done
    assert window.footer_status.value().startswith("Listen: 1 step left")
    monkeypatch.setattr(ctl, "provider_configured", lambda name: True)
    window._refresh()
    assert window.icon_voice.done and not window.icon_try.done  # Try it is never "done"
    assert window.footer_status.value().startswith("Listen ready")


# -- Listen: the local card --------------------------------------------------------------------


def test_missing_voice_offers_a_download(window, ctl, monkeypatch):
    assert window.radio_local.isChecked()
    assert shown(window.download) and window.download.text() == "Download"
    assert not shown(window.progress) and not shown(window.ready) and not shown(window.local_error)
    used = []
    monkeypatch.setattr(ctl, "set_provider", used.append)
    window.download.click()
    assert used == ["kokoro"]


def test_downloading_shows_the_bar_and_percentage(window, ctl, monkeypatch):
    monkeypatch.setattr(ctl, "voice_download_progress", lambda: 0.42)
    window._refresh()
    assert shown(window.progress) and shown(window.progress_text) and not shown(window.download)
    assert window.progress_text.value() == "42%" and window.progress.fraction == pytest.approx(0.42)


def test_a_ready_voice_says_so(window, ctl, monkeypatch):
    monkeypatch.setattr(ctl, "provider_configured", lambda name: name == "kokoro")
    window._refresh()
    assert shown(window.ready) and window.ready.label.value() == "Ready"
    assert not shown(window.download) and not shown(window.progress)


def test_a_failed_download_offers_try_again(window, monkeypatch):
    monkeypatch.setattr(kokoro.DOWNLOAD, "error", "The download stopped.")
    window._refresh()
    assert shown(window.local_error) and window.local_error.value() == "The download stopped."
    assert shown(window.download) and window.download.text() == "Try Again"


def test_choosing_the_local_card_uses_the_local_voice(make, ctl, monkeypatch):
    w = make(engine="openai")
    assert w.radio_key.isChecked()
    monkeypatch.setattr(ctl, "provider_configured", lambda name: True)
    ctl.cfg["provider"] = "openai"  # reading online now; the local voice is ready
    used = []
    monkeypatch.setattr(ctl, "set_provider", used.append)
    w.radio_local.click()
    assert w.radio_local.isChecked() and used == ["kokoro"]
    assert w.card_local.chosen and not w.card_key.chosen


# -- Listen: the online card -------------------------------------------------------------------


def test_the_dropdown_lists_only_engines_that_need_a_key(window):
    labels = [window.popup.itemText(i) for i in range(window.popup.count())]
    assert labels == [cls.label for cls in REGISTRY.values() if cls.needs_key]
    assert "kokoro" not in window.engines and all(REGISTRY[n].needs_key for n in window.engines)


def test_show_with_an_engine_preselects_it(make):
    w = make(engine="openai")
    assert w.radio_key.isChecked() and w._engine() == "openai"
    assert w.key_tag.text == "Text is sent to OpenAI"
    assert shown(w.key_field) and shown(w.save) and shown(w.get_key) and shown(w.popup)
    assert w.card_key.chosen


def test_option_cards_say_where_things_go(mac):
    from lyssna.ui.setup_window import LISTEN

    mac._select_tab(LISTEN)
    assert mac.radio_local.text() == "On this Mac" and mac.local_tag.text == "Free and private"
    assert shown(mac.local_note) and shown(mac.key_note)  # both notes, whichever is chosen
    mac._choose_radio("cartesia")
    refresh(mac)
    assert mac.key_tag.text == "Text is sent to Cartesia"
    assert shown(mac.key_field) and shown(mac.card_key)
    assert mac.dict_online_tag.text == "Sent to OpenAI and Anthropic"


def test_get_a_key_opens_the_providers_page(make, monkeypatch):
    opened = []
    monkeypatch.setattr(host, "open_target", opened.append)
    make(engine="openai").get_key.click()
    assert opened == [REGISTRY["openai"].key_url]


def test_save_with_an_empty_field_asks_for_a_key(make, ctl, monkeypatch):
    monkeypatch.setattr(ctl, "add_api_key", lambda *a: pytest.fail("no key to check"))
    w = make(engine="openai")
    w.key_field.setText("   ")
    w.save.click()
    assert w.key_status.value() == "Paste your API key first." and shown(w.key_status)
    assert not w._saving


def test_a_good_key_is_saved_on_the_gui_thread_and_the_field_clears(make, ctl, monkeypatch, fake_keychain):
    calls = []

    def synthesize(self, text):
        calls.append(threading.current_thread() is threading.main_thread())
        return b""

    monkeypatch.setattr(REGISTRY["openai"], "synthesize", synthesize)
    w = make(engine="openai")
    changed = []
    w.on_change = lambda: changed.append(threading.current_thread() is threading.main_thread())
    w.key_field.setText("sk-good")
    w.save.click()
    assert w._saving and shown(w.spinner) and w.spinner.running
    assert w.key_status.value() == "Checking the key…" and not w.save.isEnabled()
    pump(lambda: not w._saving)
    assert calls == [False]  # the request ran on a worker
    assert changed == [True]  # the answer came back on the GUI thread
    assert w.key_field.text() == "" and not w.spinner.running and not shown(w.spinner)
    assert w.key_status.value() == "Saved. Lyssna now reads with OpenAI TTS."
    assert fake_keychain.items["openai"] == "sk-good" and ctl.cfg["provider"] == "openai"


def test_a_rejected_key_says_why_and_stays_in_the_field(make, ctl, monkeypatch, fake_keychain):
    w = make(engine="openai")
    w.key_field.setText("sk-bad")  # the real check reaches httpx, which conftest refuses
    w.save.click()
    pump(lambda: not w._saving)
    assert w.key_status.value().startswith("That key didn't work.")
    assert "online service" in w.key_status.value()  # the request met conftest's refusal, not the network
    assert w.key_field.text() == "sk-bad" and "openai" not in fake_keychain.items


def test_an_already_saved_key_is_mentioned(make, ctl, monkeypatch):
    monkeypatch.setattr(ctl, "provider_configured", lambda name: name == "openai")
    w = make(engine="openai")
    assert w.key_status.value() == "A key is already saved. Paste a new one to replace it."
    ctl.cfg["provider"] = "openai"
    w._refresh()
    assert w.key_status.value() == "Using OpenAI TTS with your saved key."


def test_local_only_hides_the_online_card_and_names_the_pc(ctl, make):
    ctl.set_local_only(True)
    w = make(engine="openai")
    assert w.radio_local.isChecked()
    assert not shown(w.radio_key) and not shown(w.card_key) and not shown(w.key_field)
    assert shown(w.local_only_note)
    assert w.local_only_note.value() == "Online voices are off because “Keep Everything on This PC” is on."
    assert w.radio_local.text() == "On this PC"
    assert w.local_note.value() == f"Your text stays on this PC. One download of about {kokoro.DOWNLOAD_SIZE_TEXT}."


def test_read_a_sample(window, ctl, monkeypatch):
    from lyssna.ui.layout.setup import SAMPLE

    spoken = []
    monkeypatch.setattr(ctl, "speak_text", spoken.append)
    window.sample.click()
    assert spoken == [SAMPLE]


# -- key names per system ----------------------------------------------------------------------


def test_key_caps_take_the_systems_own_names():
    from lyssna.ui.setup_window import dictation_caps, key_caps
    from lyssna.win import hotkeys as win_hotkeys

    assert key_caps("ctrl+alt+s", win_hotkeys) == ["Ctrl", "Alt", "S"]
    assert dictation_caps("right_option", win_hotkeys) == ["right Alt"]
    assert dictation_caps("ctrl+win", win_hotkeys) == ["Ctrl", "Win"]  # a chord: one cap per key
    if sys.platform == "darwin":
        from lyssna.mac import hotkeys as mac_hotkeys

        assert key_caps("ctrl+alt+s", mac_hotkeys) == ["⌃", "⌥", "S"]
        assert dictation_caps("right_option", mac_hotkeys) == ["right ⌥"]


def test_the_shortcut_is_shown_with_this_systems_key_names(window, ctl):
    from lyssna.ui.setup_window import key_caps

    assert ctl.cfg["hotkeys"]["speak"] == "ctrl+alt+s"
    assert window.keycap.labels == key_caps("ctrl+alt+s", host.hotkey_table)
    expected = ["⌃", "⌥", "S"] if sys.platform == "darwin" else ["Ctrl", "Alt", "S"]
    assert window.keycap.labels == expected


# -- Dictate: Permission -----------------------------------------------------------------------


def test_microphone_states(mac, ctl, monkeypatch):
    asked, opened = [], []
    monkeypatch.setattr(ctl, "request_microphone", lambda: asked.append(1))
    monkeypatch.setattr(host, "open_target", opened.append)
    ctl.mic = "undetermined"
    refresh(mac)
    assert shown(mac.mic_allow) and not shown(mac.mic_settings)
    assert mac.mic_row.label.value() == "Allow the microphone so Lyssna can hear you" and not mac.mic_row.done
    mac.mic_allow.click()
    assert asked == [1]
    ctl.mic = "denied"
    refresh(mac)
    assert shown(mac.mic_settings) and not shown(mac.mic_allow)
    assert mac.mic_row.label.value() == "Microphone access is off. Turn it on in Settings."
    mac.mic_settings.click()
    assert opened == [host.MIC_SETTINGS_URL]
    ctl.mic = "granted"
    refresh(mac)
    assert not shown(mac.mic_settings) and not shown(mac.mic_allow)
    assert mac.mic_row.label.value() == "Microphone allowed" and mac.mic_row.done


def test_globe_key_line(mac, monkeypatch):
    opened = []
    monkeypatch.setattr(host, "open_target", opened.append)
    monkeypatch.setattr(host, "KEYBOARD_SETTINGS_URL", "x-apple.systempreferences:keyboard")
    assert shown(mac.globe_settings) and not mac.dicon1.done
    assert mac.globe_row.label.value() == "Set “Press Globe key to” to Do Nothing"
    mac.globe_settings.click()
    assert opened == ["x-apple.systempreferences:keyboard"] and mac._globe_read_at == 0.0
    mac._globe = "nothing"
    refresh(mac)
    assert not shown(mac.globe_settings) and shown(mac.globe_row)
    assert mac.globe_row.label.value() == "Globe key set to Do Nothing" and mac.dicon1.done


def test_the_globe_setting_is_read_on_a_worker_at_most_every_two_seconds(mac, machine):
    from lyssna.ui.layout.setup import GLOBE_POLL

    threads = []

    def read():
        threads.append(threading.current_thread() is threading.main_thread())
        return "nothing"

    mac.globe_action = read
    mac._globe_read_at = time.monotonic()
    mac.tick()
    assert threads == []  # read moments ago: not again yet
    mac._globe_read_at = time.monotonic() - GLOBE_POLL - 0.1
    mac.tick()
    pump(lambda: not mac._globe_busy)
    assert threads == [False] and mac._globe == "nothing"
    mac.tick()
    assert mac.globe_row.label.value() == "Globe key set to Do Nothing"


def test_no_globe_line_when_dictating_with_another_key(mac, ctl):
    ctl.cfg = config.update(**{"dictation.hotkey": "right_option"})
    refresh(mac)
    assert not shown(mac.globe_row) and not shown(mac.globe_settings)


def test_no_globe_line_without_a_globe_key(make, ctl):
    from lyssna.ui.setup_window import DICTATE

    w = make("pc")
    w._select_tab(DICTATE)
    assert ctl.cfg["dictation"]["hotkey"] == host.DICTATION_KEY_DEFAULT
    assert not shown(w.globe_row) and not shown(w.globe_settings)
    assert w.dicon1.done and w.tab_dictate.status == "1 step left"  # the microphone alone decides


# -- Dictate: Engine ---------------------------------------------------------------------------


def test_missing_models_offer_a_download_with_its_size(mac, ctl, monkeypatch):
    assert shown(mac.dict_download)
    assert "One download of about 1.1 GB." in mac.dict_local_note.value()
    assert mac.dict_local_note.value().startswith("Your voice and text stay on this Mac.")
    started = []
    monkeypatch.setattr(models.ModelDownload, "start", lambda self, on_done=None: started.append(self.model.name))
    mac.dict_download.click()
    assert set(started) == {models.PARAKEET_V2.name, models.S1_MINI.name}
    monkeypatch.setattr(ctl, "dictation_download_progress", lambda: 0.5)
    refresh(mac)
    assert shown(mac.dict_progress) and not shown(mac.dict_download)
    assert mac.dict_progress_text.value() == "50%"
    monkeypatch.setattr(ctl, "dictation_download_progress", lambda: None)
    monkeypatch.setattr(models.ModelSet, "present", lambda self: True)
    refresh(mac)
    assert shown(mac.dict_ready) and not shown(mac.dict_download) and mac.dicon2.done


def test_online_option_shows_a_key_field_for_each_engine(mac):
    for row in mac.key_rows:
        assert not shown(row.field)
    mac.dict_online.click()
    assert [row.head.value() for row in mac.key_rows] == ["OpenAI (transcription)", "Anthropic (cleanup)"]
    for row in mac.key_rows:
        assert shown(row.field) and shown(row.save) and shown(row.get_key)
    assert mac.card_dict_online.chosen and not mac.card_dict_local.chosen


def test_the_menu_opens_the_dictation_keys(mac):
    mac._choose_dictation("haiku")
    refresh(mac)
    assert mac.dict_online.isChecked() and shown(mac.key_rows[1].field)


def test_show_with_a_dictation_key_puts_the_cursor_in_its_field(make, monkeypatch):
    from lyssna.ui.setup_window import DICTATE

    w = make("mac")
    monkeypatch.setattr(w, "show_centered", lambda: w.setVisible(True))
    w.show(None, "haiku")
    pump(lambda: w.focusWidget() is w.key_rows[1].field, 0.5)
    assert w.tab == DICTATE and w.dict_online.isChecked()
    assert w.focusWidget() is w.key_rows[1].field


def test_get_a_dictation_key_opens_the_vendors_page(mac, monkeypatch):
    from lyssna.dictation.cleaners import haiku

    opened = []
    monkeypatch.setattr(host, "open_target", opened.append)
    mac.dict_online.click()
    mac.key_rows[1].get_key.click()
    assert opened == [haiku.HaikuCleaner.key_url]


def test_a_dictation_key_is_checked_on_a_worker_and_saved(mac, ctl, monkeypatch):
    calls = []

    def add(name, key):
        calls.append((name, key, threading.current_thread() is threading.main_thread()))
        return ""

    monkeypatch.setattr(ctl, "add_dictation_key", add)
    mac.dict_online.click()
    row = mac.key_rows[1]
    changed = []
    mac.on_change = lambda: changed.append(1)
    row.field.setText("   ")
    row.save.click()
    assert row.status.value() == "Paste your API key first." and calls == []
    row.field.setText("sk-ant")
    row.save.click()
    assert row.saving and shown(row.spinner) and row.status.value() == "Checking the key…" and not row.save.isEnabled()
    pump(lambda: not row.saving)
    assert calls == [("haiku", "sk-ant", False)] and changed
    assert row.field.text() == "" and not shown(row.spinner)
    assert row.status.value() == "Saved. Dictation now uses Anthropic for cleanup."


def test_a_rejected_dictation_key_says_why(mac, ctl, monkeypatch):
    monkeypatch.setattr(ctl, "add_dictation_key", lambda name, key: "OpenAI did not accept that key. Copy it again.")
    mac.dict_online.click()
    row = mac.key_rows[0]
    row.field.setText("sk-bad")
    row.field.returnPressed.emit()  # Enter in the field saves
    pump(lambda: not row.saving)
    assert row.status.value() == "OpenAI did not accept that key. Copy it again." and row.field.text() == "sk-bad"


def test_saved_dictation_keys_are_mentioned(mac, ctl, monkeypatch):
    monkeypatch.setattr(ctl, "dictation_key_state", lambda name: True)
    monkeypatch.setattr(ctl, "current_transcriber", lambda: "openai")
    monkeypatch.setattr(ctl, "current_cleaner", lambda: "s1-mini")
    mac.dict_online.click()
    assert mac.key_rows[0].status.value() == "Using your saved key."
    assert mac.key_rows[1].status.value() == "A key is already saved. Paste a new one to replace it."
    assert mac.dicon2.done  # both keys saved: the online engine is ready


def test_local_only_hides_the_online_option(mac, ctl):
    ctl.set_local_only(True)
    mac._choose_dictation("haiku")
    refresh(mac)
    assert not shown(mac.dict_online) and mac.dict_local.isChecked()
    assert shown(mac.dict_local_only_note)
    assert "Keep Everything on This Mac" in mac.dict_local_only_note.value()
    assert "Keep Everything on This Mac" in mac.local_only_note.value()


# -- Dictate: Try it ---------------------------------------------------------------------------


def test_dictation_step_explains_the_keys(mac):
    assert mac.hold_text.value() == "Hold" and mac.hold_after.value() == "and speak."
    assert mac.dictate_body.value() == "Lyssna types what you said, cleaned up, wherever your cursor is."
    assert mac.hold_keys.labels == ["fn"]
    assert shown(mac.hold_keys) and shown(mac.free_keys)
    assert mac.free_text.value() == "Hands-free:" and mac.free_keys.labels == ["fn", "Space"]
    assert not shown(mac.tap_text) and not shown(mac.tap_keys)  # no double tap on the Mac


def test_where_the_double_tap_is_on_try_it_says_so(make, ctl):
    from lyssna.ui.setup_window import DICTATE

    ctl.cfg = config.update(**{"dictation.hotkey": "right_control", "dictation.double_tap": True})
    w = make("pc")
    w._select_tab(DICTATE)
    assert shown(w.tap_text) and shown(w.tap_keys) and shown(w.tap_after)
    assert (w.tap_text.value(), w.tap_after.value()) == ("Or tap", "twice.")
    assert w.tap_keys.labels == w.hold_keys.labels and len(w.tap_keys.labels) == 1  # the same caps as Hold
    assert w.tap_text.y() > w.free_text.y()  # under the hands-free line
    ctl.cfg = config.update(**{"dictation.double_tap": False})
    refresh(w)
    assert not shown(w.tap_text) and not shown(w.tap_keys)


def test_the_dictation_keys_follow_the_config(mac, ctl):
    from lyssna.ui.setup_window import dictation_caps

    ctl.cfg = config.update(**{"dictation.hotkey": "right_control"})
    refresh(mac)
    name = dictation_caps("right_control", host.hotkey_table)
    assert mac.hold_keys.labels == name and mac.free_keys.labels == [*name, "Space"]


# -- the window --------------------------------------------------------------------------------


def test_closing_hides_and_keeps_the_window(window):
    window.close()
    assert window.isHidden() and window.built
    assert window.content is not None


def test_previews_build_without_a_controller_or_network(qapp):
    from lyssna.ui import setup_window

    for name, factory in setup_window.PREVIEWS.items():
        w = factory()
        assert w.height() > 0, name
        w.deleteLater()
    QCoreApplication.processEvents()


def test_previews_force_the_capabilities(qapp):
    from lyssna.ui import setup_window

    mac = setup_window.PREVIEWS["setup.mac.not-allowed"]()
    pc = setup_window.PREVIEWS["setup.download"]()
    assert mac.needs_accessibility and hasattr(mac, "icon_permission") and mac.radio_local.text() == "On this Mac"
    assert not pc.needs_accessibility and pc.keycap.labels == ["Ctrl", "Alt", "S"] and pc.radio_local.text() == "On this PC"
    for w in (mac, pc):
        w.deleteLater()
    QCoreApplication.processEvents()


def test_enter_is_done_except_in_a_key_field(make, ctl, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    monkeypatch.setattr(ctl, "add_api_key", lambda *a: "")
    w = make(engine="openai")
    w.setVisible(True)
    enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    w.key_field.setFocus()
    pump(lambda: w.focusWidget() is w.key_field, 0.5)
    w.key_field.setText("sk-x")
    QApplication.sendEvent(w.key_field, enter)
    assert w._saving or w.key_status.value().startswith("Saved")  # Enter in the field saves
    pump(lambda: not w._saving)
    assert w.isVisible()
    w.done.setFocus()
    QApplication.sendEvent(w, enter)
    assert w.isHidden()  # anywhere else, Enter is Done


def test_old_wording_is_gone():
    from pathlib import Path

    from lyssna.ui import setup_window

    assert "Only Use Voices" not in Path(setup_window.__file__).read_text(encoding="utf-8")


# -- the controller's dictation keys (ported from tests/legacy/test_setup_window.py) ----------


class Gets:
    """httpx.get, faked: records each request and answers with `status`."""

    def __init__(self, status=200):
        self.status = status
        self.calls = []

    def __call__(self, url, headers=None, timeout=None, **kwargs):
        self.calls.append((url, dict(headers or {}), timeout))
        return httpx.Response(self.status, json={"data": []}, request=httpx.Request("GET", url))


def test_an_anthropic_key_is_checked_for_free_saved_and_used_for_cleanup(ctl, monkeypatch, fake_keychain, caplog):
    gets = Gets(200)
    monkeypatch.setattr(httpx, "get", gets)
    provider = ctl.cfg["provider"]
    with caplog.at_level(logging.DEBUG):
        assert ctl.add_dictation_key("haiku", " sk-ant-secret ") == ""
    (url, headers, timeout), = gets.calls  # one request
    assert url == "https://api.anthropic.com/v1/models?limit=1"  # lists models: no tokens, no cost
    assert headers["x-api-key"] == "sk-ant-secret" and timeout
    assert fake_keychain.items["anthropic"] == "sk-ant-secret"
    assert config.load()["dictation"]["cleaner"] == "haiku" and config.load()["provider"] == provider
    assert all("sk-ant-secret" not in r.getMessage() for r in caplog.records)


def test_an_openai_key_is_checked_with_the_models_list(ctl, monkeypatch, fake_keychain):
    gets = Gets(200)
    monkeypatch.setattr(httpx, "get", gets)
    monkeypatch.setattr(parakeet.ParakeetTranscriber, "prepare", lambda self, on_error=None: None)
    assert ctl.add_dictation_key("openai", "sk-good") == ""
    (url, headers, _), = gets.calls
    assert url == "https://api.openai.com/v1/models" and headers["Authorization"] == "Bearer sk-good"
    assert config.load()["dictation"]["transcriber"] == "openai"
    assert config.load()["provider"] == "kokoro"  # the voice stays as it was


def test_a_rejected_key_is_not_saved(ctl, monkeypatch, fake_keychain):
    monkeypatch.setattr(httpx, "get", Gets(401))
    assert ctl.add_dictation_key("haiku", "sk-bad") == "Anthropic did not accept that key. Copy it again."
    assert "anthropic" not in fake_keychain.items
    assert config.load()["dictation"]["cleaner"] == "s1-mini"


def test_an_unreachable_service_says_so(ctl, monkeypatch, fake_keychain):
    def offline(*args, **kwargs):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "get", offline)
    assert "could not be reached" in ctl.add_dictation_key("openai", "sk-x")
    assert "openai" not in fake_keychain.items


def test_local_only_refuses_without_a_request(ctl, monkeypatch, fake_keychain):
    gets = Gets(200)
    monkeypatch.setattr(httpx, "get", gets)
    ctl.set_local_only(True)
    assert "local-only mode is on" in ctl.add_dictation_key("haiku", "sk-x")
    assert gets.calls == [] and "anthropic" not in fake_keychain.items


def test_key_state_never_waits(ctl, fake_keychain):
    fake_keychain.items["anthropic"] = "k"
    keystore.clear_cache()
    assert ctl.dictation_key_state("haiku") is True  # read inline in tests; None while a real read runs
    assert ctl.dictation_key_state("openai") is False
