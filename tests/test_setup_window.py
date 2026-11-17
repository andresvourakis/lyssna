"""The setup window, built for real but never shown: the window is laid out offscreen and its
views are read back. Its two tabs (Listen, Dictate) and their wording, then Dictate's steps. The microphone, the Globe key setting and the
Accessibility check are faked; the model files are faked as present or missing. Also the
controller's dictation key path and the free key checks, with httpx faked."""

import logging

import AppKit
import httpx
import pytest

from lyssna import config, keychain
from lyssna.dictation import models
from lyssna.dictation.cleaners import s1mini
from lyssna.dictation.transcribers import parakeet
from lyssna.providers import kokoro

needs_screen = pytest.mark.skipif(not AppKit.NSScreen.screens(), reason="no window server")


@pytest.fixture
def ctl(monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)
    monkeypatch.setattr(parakeet.ParakeetTranscriber, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(s1mini.S1MiniCleaner, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(models.ModelSet, "present", lambda self: False)
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE)
    from lyssna.controller import Controller

    c = Controller()
    c.mic = "granted"
    monkeypatch.setattr(c, "microphone_status", lambda: c.mic)
    return c


@pytest.fixture
def window(ctl, monkeypatch):
    from lyssna.ui import setup_window

    monkeypatch.setattr(setup_window, "accessibility_trusted", lambda prompt=False: True)
    w = setup_window.SetupWindow.alloc().init()
    w.ctl = ctl
    w.globe_action = lambda: "emoji"
    w._build()
    w._globe = "emoji"
    w._choose_radio(None)
    w._choose_dictation(None)
    w.refresh = lambda: w._refresh(force=True)
    w._select_tab(setup_window.DICTATE)  # most tests below are about Dictate's steps
    yield w
    w.window.close()


def shown(view) -> bool:
    return not view.isHidden()


# -- the tabs ------------------------------------------------------------------------------


def test_steps_left_and_their_wording():
    from lyssna.ui.setup_window import footer_status, steps_left, tab_status

    assert steps_left((True, True)) == 0 and steps_left((False, True)) == 1 and steps_left((False, False)) == 2
    assert [tab_status(n) for n in (0, 1, 2)] == ["ready", "1 step left", "2 steps left"]
    assert footer_status(0, 1) == "Listen ready · Dictate: 1 step left"
    assert footer_status(2, 0) == "Listen: 2 steps left · Dictate ready"


def test_which_tab_opens():
    from lyssna.ui.setup_window import DICTATE, LISTEN, opening_tab

    assert opening_tab(None, "haiku", 0, 0) == DICTATE  # a dictation key from the menu
    assert opening_tab("cartesia", None, 0, 2) == LISTEN  # a voice key from the menu
    assert opening_tab(None, None, 1, 2) == LISTEN  # the first tab with steps left
    assert opening_tab(None, None, 0, 1) == DICTATE
    assert opening_tab(None, None, 0, 0) == LISTEN  # all done


@needs_screen
def test_show_picks_the_tab(window, ctl, monkeypatch):
    from lyssna.ui.setup_window import DICTATE, LISTEN

    window._prepare(None, None, opening=True)
    assert window.tab == LISTEN and window._left == (1, 2)  # no voice ready yet: Listen has a step left
    monkeypatch.setattr(ctl, "provider_configured", lambda name: True)
    window._prepare(None, None, opening=True)
    assert window.tab == DICTATE and window.tab_dictate.state() == 1 and window.tab_listen.state() == 0
    window._prepare("cartesia", None, opening=False)
    assert window.tab == LISTEN and window.radio_key.state() == 1
    window._prepare(None, "openai", opening=False)
    assert window.tab == DICTATE and window.dict_online.state() == 1 and shown(window.key_rows[0].box)


@needs_screen
def test_the_timer_never_switches_tabs(window, ctl, monkeypatch):
    from lyssna.ui.setup_window import LISTEN

    window._select_tab(LISTEN)
    monkeypatch.setattr(ctl, "provider_configured", lambda name: True)  # Listen ready: a fresh open would pick Dictate
    window.tick_(None)
    window._prepare(None, None, opening=False)  # Setup… again while the window is open
    assert window.tab == LISTEN and window.tab_listen.state() == 1
    assert window.tab_listen.status == "ready" and window.tab_dictate.status == "2 steps left"


@needs_screen
def test_switch_and_footer_say_what_is_left(window):
    assert window.tab_listen.status == "1 step left" and window.tab_dictate.status == "2 steps left"
    assert window.footer_status.stringValue() == "Listen: 1 step left · Dictate: 2 steps left"
    window._globe = "nothing"
    window.refresh()
    assert window.tab_dictate.status == "1 step left"
    assert window.tab_dictate.accessibilityLabel() == "Dictate, 1 step left"


@needs_screen
def test_done_stays_outside_the_scrolling_steps(window):
    from lyssna.ui.setup_window import DICTATE, LISTEN

    for tab in (LISTEN, DICTATE):
        window._select_tab(tab)
        assert not window.done.isDescendantOf_(window.content)
        assert window.done.isDescendantOf_(window.window.contentView()) and shown(window.done)
        bounds = window.window.contentView().bounds()
        f = window.done.convertRect_toView_(window.done.bounds(), window.window.contentView())
        assert f.origin.y >= 0 and f.origin.y + f.size.height <= bounds.size.height
        assert window.footer.frame().origin.y + window.footer.frame().size.height == bounds.size.height


@needs_screen
def test_on_a_short_screen_only_the_steps_scroll(window, monkeypatch):
    from lyssna.ui import setup_window

    monkeypatch.setattr(setup_window, "SCREEN_GAP", 10_000.0)  # no room at all: the window's smallest height
    window.refresh()
    bounds = window.window.contentView().bounds()
    assert bounds.size.height == 320.0
    assert window.content.frame().size.height > window.scroll.frame().size.height  # the steps scroll
    assert window.scroll.frame().origin.y == window.header.frame().size.height
    assert window.footer.frame().origin.y + window.footer.frame().size.height == bounds.size.height
    f = window.done.convertRect_toView_(window.done.bounds(), window.window.contentView())
    assert f.origin.y + f.size.height <= bounds.size.height


@needs_screen
def test_each_tab_shows_only_its_own_steps(window):
    from lyssna.ui.setup_window import DICTATE, LISTEN

    window._select_tab(LISTEN)
    assert shown(window.sample) and shown(window.radio_local) and shown(window.head1)
    assert not shown(window.mic_row) and not shown(window.dict_local) and not shown(window.free_keys)
    window._select_tab(DICTATE)
    assert shown(window.mic_row) and shown(window.dict_local) and shown(window.free_keys)
    assert not shown(window.sample) and not shown(window.radio_local) and not shown(window.head1)


@needs_screen
def test_option_cards_say_where_things_go(window):
    from lyssna.ui.setup_window import LISTEN

    window._select_tab(LISTEN)
    assert window.radio_local.title() == "On this Mac" and window.local_tag.label.stringValue() == "Free and private"
    assert shown(window.local_note) and shown(window.key_note)  # both notes, whichever is chosen
    window._choose_radio("cartesia")
    window.refresh()
    assert window.key_tag.label.stringValue() == "Text is sent to Cartesia"
    assert shown(window.key_box) and shown(window.card_key)
    assert window.dict_online_tag.label.stringValue() == "Sent to OpenAI and Anthropic"


@needs_screen
def test_dictation_step_explains_the_keys(window):
    assert window.hold_text.stringValue() == "Hold" and window.hold_after.stringValue() == "and speak."
    assert window.dictate_body.stringValue() == "Lyssna types what you said, cleaned up, wherever your cursor is."
    assert [v.stringValue() for cap in window.hold_keys.subviews() for v in cap.subviews()] == ["fn"]
    assert shown(window.hold_keys) and shown(window.free_keys)
    labels = [v.stringValue() for cap in window.free_keys.subviews() for v in cap.subviews()]
    assert labels == ["fn", "Space"]


@needs_screen
def test_microphone_states(window, ctl, monkeypatch):
    asked = []
    monkeypatch.setattr(ctl, "request_microphone", lambda: asked.append(1))
    ctl.mic = "undetermined"
    window.refresh()
    assert shown(window.mic_allow) and not shown(window.mic_settings)
    window.allowMicrophone_(None)
    assert asked == [1]
    ctl.mic = "denied"
    window.refresh()
    assert shown(window.mic_settings) and not shown(window.mic_allow)
    ctl.mic = "granted"
    window.refresh()
    assert not shown(window.mic_settings) and not shown(window.mic_allow)
    assert window.mic_row.label.stringValue() == "Microphone allowed"


@needs_screen
def test_globe_key_line(window):
    assert shown(window.globe_settings)
    assert window.globe_row.label.stringValue() == "Set “Press Globe key to” to Do Nothing"
    window._globe = "nothing"
    window.refresh()
    assert not shown(window.globe_settings) and shown(window.globe_row)
    assert window.globe_row.label.stringValue() == "Globe key set to Do Nothing"


@needs_screen
def test_no_globe_line_when_dictating_with_another_key(window, ctl):
    ctl.cfg = config.update(**{"dictation.hotkey": "right_option"})
    window.refresh()
    assert not shown(window.globe_row) and not shown(window.globe_settings)


@needs_screen
def test_missing_models_offer_a_download_with_its_size(window, ctl, monkeypatch):
    assert shown(window.dict_download)
    assert "One download of about 1.1 GB." in window.dict_local_note.stringValue()
    started = []
    monkeypatch.setattr(models.ModelDownload, "start", lambda self, on_done=None: started.append(self.model.name))
    window.downloadModels_(None)
    assert set(started) == {models.PARAKEET_V2.name, models.S1_MINI.name}
    monkeypatch.setattr(ctl, "dictation_download_progress", lambda: 0.5)
    window.refresh()
    assert shown(window.dict_progress) and not shown(window.dict_download)
    assert window.dict_progress_text.stringValue() == "50%"
    monkeypatch.setattr(ctl, "dictation_download_progress", lambda: None)
    monkeypatch.setattr(models.ModelSet, "present", lambda self: True)
    window.refresh()
    assert shown(window.dict_ready) and not shown(window.dict_download)


@needs_screen
def test_online_option_shows_a_key_field_for_each_engine(window):
    for row in window.key_rows:
        assert not shown(row.box)
    window.chooseDictation_(window.dict_online)
    assert [row.head.stringValue() for row in window.key_rows] == ["OpenAI (transcription)", "Anthropic (cleanup)"]
    for row in window.key_rows:
        assert shown(row.box) and shown(row.save) and shown(row.get_key)


@needs_screen
def test_the_menu_opens_the_dictation_keys(window):
    window._choose_dictation("haiku")
    window.refresh()
    assert window.dict_online.state() == 1 and shown(window.key_rows[1].box)


@needs_screen
def test_local_only_hides_the_online_option(window, ctl):
    ctl.set_local_only(True)
    window._choose_dictation("haiku")
    window.refresh()
    assert not shown(window.dict_online) and window.dict_local.state() == 1
    assert shown(window.dict_local_only_note)
    assert "Keep Everything on This Mac" in window.dict_local_only_note.stringValue()
    assert "Keep Everything on This Mac" in window.local_only_note.stringValue()


def test_old_wording_is_gone():
    from pathlib import Path

    from lyssna.ui import setup_window

    assert "Only Use Voices" not in Path(setup_window.__file__).read_text()


# -- the controller's dictation keys ------------------------------------------------------


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
    keychain.clear_cache()
    assert ctl.dictation_key_state("haiku") is True  # read inline in tests; None while a real read runs
    assert ctl.dictation_key_state("openai") is False


@needs_screen
def test_a_click_anywhere_on_a_segment_switches_tabs(window):
    """The radio cell under a segment only tracks over its own unseen circle and title, so the
    segment takes the click itself: its far corner works as well as its middle."""
    from AppKit import NSEvent, NSEventTypeLeftMouseDown

    for tab, name in ((window.tab_listen, "listen"), (window.tab_dictate, "dictate")):
        for fx, fy in ((0.5, 0.5), (0.95, 0.9), (0.05, 0.1)):
            window._select_tab("dictate" if name == "listen" else "listen")
            f = tab.convertRect_toView_(tab.bounds(), None)
            at = (f.origin.x + f.size.width * fx, f.origin.y + f.size.height * fy)
            tab.mouseDown_(NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                NSEventTypeLeftMouseDown, at, 0, 0, window.window.windowNumber(), None, 0, 1, 1.0))
            assert window.tab == name
            assert (window.tab_listen.state(), window.tab_dictate.state()) == ((1, 0) if name == "listen" else (0, 1))
