"""The Windows setup window (lyssna/ui/setup_window.py), built for real but never shown: it
is laid out on the offscreen platform and its views are read back. A real Controller runs
against the temp config from conftest; the voice files, the download state and the key check
are faked, so nothing reaches the network or a paid service."""

import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from lyssna import config, host
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
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    from lyssna.controller import Controller

    return Controller()


@pytest.fixture
def make(qapp, ctl):
    """make(taps_ok=True, engine=None) -> a prepared SetupWindow, never shown."""
    from lyssna.ui.setup_window import SetupWindow

    made = []

    def build(taps_ok: bool = True, engine: str | None = None) -> SetupWindow:
        w = SetupWindow()
        w.ctl = ctl
        w.taps_ok = lambda: taps_ok
        w.prepare(engine)
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


def shown(view) -> bool:
    return not view.isHidden()


# -- the steps ---------------------------------------------------------------------------------


def test_listen_has_two_steps_voice_and_try_it(window):
    assert (window.icon1.number, window.head1.value()) == (1, "Voice")
    assert (window.icon2.number, window.head2.value()) == (2, "Try it")
    assert shown(window.radio_local) and shown(window.sample) and shown(window.keycap)
    assert not hasattr(window, "icon3")  # no permission step on Windows


def test_voice_step_is_done_once_the_voice_can_read(window, ctl, monkeypatch):
    assert not window.icon1.done and not window.icon2.done
    assert window.footer_status.value() == "Listen: 1 step left"
    monkeypatch.setattr(ctl, "provider_configured", lambda name: True)
    window._refresh()
    assert window.icon1.done and not window.icon2.done  # Try it is never "done"
    assert window.footer_status.value() == "Listen ready"


def test_no_tab_switch_while_dictation_is_off(window):
    from lyssna.ui.setup_window import LISTEN

    assert window.features == {"dictate": False}
    assert window.tabs == (LISTEN,) and window.tab == LISTEN
    assert not hasattr(window, "tab_dictate") and not hasattr(window, "switch")
    assert "Dictate" not in window.footer_status.value()


def test_the_header_and_footer_stay_outside_the_scrolling_steps(window):
    assert window.done.parent() is window.footer and window.heading.parent() is window.header
    assert window.footer.geometry().bottom() + 1 == window.height()
    assert window.scroll_area.geometry().top() == window.header.height()


def test_on_a_short_screen_only_the_steps_scroll(window, monkeypatch):
    monkeypatch.setattr(type(window), "height_limit", lambda self: 0.0)  # no room: the window's smallest height
    window._refresh(force=True)
    assert window.height() == 320
    assert window.content.height() > window.scroll_area.height()
    assert shown(window.header_line)


# -- the local card ----------------------------------------------------------------------------


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


# -- the online card ---------------------------------------------------------------------------


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


# -- local only, the warning row, the sample ---------------------------------------------------


def test_local_only_hides_the_online_card_and_names_the_pc(qapp, ctl, monkeypatch, make):
    monkeypatch.setattr(host, "DEVICE", "PC")
    ctl.set_local_only(True)
    w = make(engine="openai")
    assert w.radio_local.isChecked()
    assert not shown(w.radio_key) and not shown(w.card_key) and not shown(w.key_field)
    assert shown(w.local_only_note)
    assert w.local_only_note.value() == "Online voices are off because “Keep Everything on This PC” is on."
    assert w.radio_local.text() == "On this PC"
    assert w.local_note.value() == f"Your text stays on this PC. One download of about {kokoro.DOWNLOAD_SIZE_TEXT}."


def test_the_warning_row_follows_the_shortcuts(make):
    ok = make(taps_ok=True)
    assert not shown(ok.warn_text) and not shown(ok.relaunch_btn)
    down = make(taps_ok=False)
    assert shown(down.warn_text) and shown(down.relaunch_btn)
    assert down.warn_text.value() == ("Lyssna could not start its keyboard shortcuts. Quit other tools that capture "
                                      "the keyboard, then relaunch.")
    assert down.footer_status.value() == "Listen: 2 steps left"
    relaunched = []
    down.relaunch = lambda: relaunched.append(1)
    down.relaunch_btn.click()
    assert relaunched == [1]
    assert down.relaunch_btn.geometry().bottom() < down.head1.geometry().top()  # above the steps


def test_the_shortcut_is_shown_with_windows_key_names(window, ctl):
    assert ctl.cfg["hotkeys"]["speak"] == "ctrl+alt+s"
    assert window.keycap.labels == ["Ctrl", "Alt", "S"]


def test_read_a_sample(window, ctl, monkeypatch):
    from lyssna.ui.layout.setup import SAMPLE

    spoken = []
    monkeypatch.setattr(ctl, "speak_text", spoken.append)
    window.sample.click()
    assert spoken == [SAMPLE]


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


def test_enter_is_done_except_in_the_key_field(make, ctl, monkeypatch):
    from PySide6.QtCore import QEvent, Qt
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
