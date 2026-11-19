"""LyssnaApp's dictation wiring, built for real but kept off the machine: the keyboard and
mouse taps, the Accessibility prompt, the control socket, the microphone warm-up and every
deferred call are stubbed, so nothing is registered, prompted for or shown."""

import AppKit
import pytest

from lyssna import app as app_mod
from lyssna import config
from lyssna.controller import Controller, Notice
from lyssna.dictation.hotkey import FN, PushToTalk
from lyssna.providers import kokoro

pytestmark = pytest.mark.skipif(not AppKit.NSScreen.screens(), reason="no window server")


class Server:
    def __init__(self, dispatch):
        self.dispatch = dispatch

    def start(self):
        pass

    def close(self):
        pass


@pytest.fixture
def lyssna(monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE)
    monkeypatch.setattr(app_mod, "accessibility_trusted", lambda prompt=False: True)
    monkeypatch.setattr(app_mod.KeyboardTap, "start", lambda self: True)
    monkeypatch.setattr(app_mod.MouseTap, "start", lambda self: True)
    monkeypatch.setattr(app_mod, "ControlServer", Server)
    deferred = []
    monkeypatch.setattr(app_mod, "later", lambda delay, fn, *args: deferred.append(fn))
    monkeypatch.setattr(Controller, "warm_dictation", lambda self: None)
    queued = []

    class Queue:
        @staticmethod
        def mainQueue():
            return Queue

        @staticmethod
        def addOperationWithBlock_(block):
            queued.append(block)

    monkeypatch.setattr(app_mod, "NSOperationQueue", Queue)
    a = app_mod.LyssnaApp()
    a.deferred, a.queued = deferred, queued
    yield a
    a.mini_player.wave.stop()


def test_push_to_talk_is_attached_and_shared(lyssna):
    ptt = lyssna.keyboard.push_to_talk
    assert isinstance(ptt, PushToTalk) and ptt.trigger == FN
    assert lyssna.ctl.push_to_talk is ptt


def test_the_pill_is_wired_to_dictation(lyssna):
    mp = lyssna.mini_player
    for name in ("dictate", "dictate_stop", "dictate_cancel", "dictation_layout", "read_selection", "history"):
        assert callable(mp.actions[name])
    assert mp.key_name == "fn" and mp.read_key and mp.idle_pill is True and mp.auto_hide is True
    assert lyssna.menubar.on_auto_hide == mp.set_auto_hide  # the Settings item applies it live
    assert mp.rest_wave is True and lyssna.menubar.on_wave == mp.set_wave
    assert "dictation_update" in [getattr(fn, "__name__", "") for fn in lyssna.ctl.dictation._observers]
    assert "show_idle" in [getattr(fn, "__name__", "") for fn in lyssna.deferred]  # the mic button, after launch


def test_toasts_are_queued_not_shown_inline(lyssna):
    shown = []
    lyssna.toast.present = lambda notice, screen=None, floor=None: shown.append(notice.text)
    lyssna.ctl.toast(Notice("Hello"))
    assert shown == [] and len(lyssna.queued) == 1
    lyssna.queued[0]()
    assert shown == ["Hello"]


def test_history_opens_the_window_or_says_it_is_missing(lyssna, monkeypatch):
    import sys

    shown = []

    class Window:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            self.ctl = None
            return self

        def show(self):
            shown.append(self.ctl)

    fake = type(sys)("lyssna.ui.history_window")
    fake.HistoryWindow = Window
    monkeypatch.setitem(sys.modules, "lyssna.ui.history_window", fake)
    lyssna.show_history()
    assert shown == [lyssna.ctl]

    lyssna.history_window = None
    monkeypatch.setitem(sys.modules, "lyssna.ui.history_window", None)  # import fails
    toasts = []
    lyssna.ctl.subscribe_toast(toasts.append)
    lyssna.show_history()
    assert [t.text for t in toasts] == ["History is not available in this build yet"]


def test_a_bad_dictation_key_is_reported_not_fatal(monkeypatch, lyssna):
    config.update(**{"dictation.hotkey": "caps_lock"})
    lyssna.ctl.reload_config()
    lyssna.keyboard.push_to_talk = None
    lyssna.ctl.push_to_talk = None
    lyssna._attach_push_to_talk()
    assert lyssna.keyboard.push_to_talk is None
    assert len(lyssna.queued) == 1  # the toast saying so


def test_play_button_reads_what_it_was_offered_for(lyssna, monkeypatch):
    """A button offered on a copy-on-select reads the clipboard, with no Cmd+C sent; one
    offered on an Accessibility selection reads the selection."""
    from lyssna.events import Point

    calls = []
    monkeypatch.setattr(lyssna.ctl, "speak_clipboard", lambda: calls.append("clipboard"))
    monkeypatch.setattr(lyssna.ctl, "speak_selection", lambda: calls.append("selection"))
    lyssna.selection_button.present = lambda *args: None  # nothing on screen
    lyssna._present_button(Point(1, 1), None, "clipboard")
    lyssna.selection_button.on_play()
    lyssna._present_button(Point(1, 1), (0.0, 0.0, 1.0, 1.0), "selection")
    lyssna.selection_button.on_play()
    assert calls == ["clipboard", "selection"]
