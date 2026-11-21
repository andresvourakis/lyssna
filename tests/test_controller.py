"""Controller.dispatch: every socket command reaches the right action. The reader is a fake
that records calls, and capture is stubbed, so nothing plays, synthesizes or reads the screen."""

import threading

import pytest

from lyssna import config, controller
from lyssna.controller import SPEED_STEP, Controller
from lyssna.player import Status


class FakeReader:
    def __init__(self):
        self.calls = []
        self.speed = 1.0
        self.status = Status()

    def __getattr__(self, name):  # toggle_pause, stop, next, prev, seek, seek_to, ...
        return lambda *args: self.calls.append((name, *args))

    def speak(self, text):
        self.calls.append(("speak", text))
        return bool(text.strip())

    def set_speed(self, speed):
        self.speed = round(speed, 2)
        self.calls.append(("set_speed", self.speed))
        return self.speed

    def is_active(self):
        return False


@pytest.fixture
def ctl(monkeypatch):
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    c = Controller()
    c.reader = FakeReader()
    c.notices = []
    monkeypatch.setattr(c, "notify", lambda message, ttl=3.0: c.notices.append(message))
    return c


@pytest.mark.parametrize(("cmd", "call"), [
    ("pause", "toggle_pause"), ("stop", "stop"), ("next", "next"), ("prev", "prev"),
])
def test_simple_commands(ctl, cmd, call):
    resp = ctl.dispatch({"cmd": cmd})
    assert resp["ok"] is True and "status" in resp
    assert ctl.reader.calls == [(call,)]


def test_status_does_nothing_but_report(ctl):
    resp = ctl.dispatch({"cmd": "status"})
    assert resp["ok"] is True and ctl.reader.calls == []
    assert resp["status"]["state"] == "idle" and resp["status"]["provider_name"] == ctl.cfg["provider"]


def test_speak(ctl):
    assert ctl.dispatch({"cmd": "speak", "text": "Hello."})["ok"] is True
    assert ctl.dispatch({"cmd": "speak", "text": "  "})["ok"] is False
    assert ctl.reader.calls == [("speak", "Hello."), ("speak", "  ")]


def test_speed_persists(ctl):
    resp = ctl.dispatch({"cmd": "speed", "value": 1.5})
    assert resp["ok"] is True and ctl.reader.calls == [("set_speed", 1.5)]
    assert config.load()["speed"] == 1.5


def test_faster_and_slower_use_the_step(ctl):
    ctl.reader.speed = 1.0
    ctl.faster()
    ctl.slower()
    assert ctl.reader.calls == [("set_speed", round(1.0 + SPEED_STEP, 2)), ("set_speed", 1.0)]


def test_seek_and_seek_to(ctl):
    ctl.dispatch({"cmd": "seek", "value": -5})
    ctl.dispatch({"cmd": "seek_to", "value": 0.25})
    assert ctl.reader.calls == [("seek", -5.0), ("seek_to", 0.25)]


@pytest.mark.parametrize(("value", "expected"), [("on", True), ("off", False), ("true", True), ("0", False)])
def test_button_on_off(ctl, value, expected):
    resp = ctl.dispatch({"cmd": "button", "value": value})
    assert resp["status"]["selection_button"] is expected
    assert config.load()["ui"]["selection_button"] is expected


def test_button_toggle(ctl):
    start = ctl.selection_button_enabled
    ctl.dispatch({"cmd": "button"})
    assert ctl.selection_button_enabled is not start
    ctl.dispatch({"cmd": "button", "value": "toggle"})
    assert ctl.selection_button_enabled is start


def test_selection_speaks_captured_text(ctl, monkeypatch):
    done = threading.Event()
    monkeypatch.setattr(controller.capture, "selection_or_clipboard", lambda: ("Picked text.", "test"))
    speak = ctl.reader.speak
    ctl.reader.speak = lambda text: (speak(text), done.set())
    assert ctl.dispatch({"cmd": "selection"})["ok"] is True
    assert done.wait(2)
    assert ctl.reader.calls == [("speak", "Picked text.")]


def test_selection_with_nothing_selected_notifies(ctl, monkeypatch):
    done = threading.Event()
    monkeypatch.setattr(controller.capture, "selection_or_clipboard", lambda: ("  ", "clipboard"))
    monkeypatch.setattr(ctl, "notify", lambda message, ttl=3.0: (ctl.notices.append(message), done.set()))
    ctl.dispatch({"cmd": "selection"})
    assert done.wait(2)
    assert ctl.notices == ["Nothing selected"] and ctl.reader.calls == []


def test_clipboard(ctl, monkeypatch):
    monkeypatch.setattr(controller.capture, "clipboard_text", lambda: "From the clipboard.")
    ctl.dispatch({"cmd": "clipboard"})
    monkeypatch.setattr(controller.capture, "clipboard_text", lambda: "")
    ctl.dispatch({"cmd": "clipboard"})
    assert ctl.reader.calls == [("speak", "From the clipboard.")]
    assert ctl.notices == ["Clipboard is empty"]


def test_voice(ctl):
    ctl.set_provider("inworld")
    assert ctl.dispatch({"cmd": "voice", "value": "Luna"})["status"]["voice"] == "Luna"
    resp = ctl.dispatch({"cmd": "voice", "value": "Nobody"})
    assert resp["ok"] is False and "Nobody" in resp["error"]


def test_extension_commands(ctl):
    ctl.extensions["ui"] = lambda req: {"ok": True, "echo": req["cmd"]}
    assert ctl.dispatch({"cmd": "ui"}) == {"ok": True, "echo": "ui"}


def test_unknown_command(ctl):
    resp = ctl.dispatch({"cmd": "dance"})
    assert resp == {"ok": False, "error": "unknown command 'dance'"}
    assert ctl.reader.calls == []
