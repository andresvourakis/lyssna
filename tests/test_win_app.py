"""The Windows wiring (lyssna/win/app.py), built for real around a real Controller on the temp
config, but kept off the machine: the keyboard and mouse taps, the selection probes, the control
channel, the single-instance mutex and every deferred call are fakes, and the Qt event loop never
runs (queued calls are pumped by hand). Runs on the offscreen platform."""

import os
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6.QtWidgets")

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from lyssna import config, host, hotkeys
from lyssna.controller import Controller, Notice
from lyssna.gestures import Point
from lyssna.player import Status
from lyssna.providers import kokoro


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("a non-GUI Qt application already exists in this process")
    return app or QApplication(["lyssna-tests"])


def pump(until, seconds: float = 3.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until():
            return
        time.sleep(0.005)
    assert until(), "the queued call never ran"


class FakeKeyboard:
    def __init__(self, ok: bool):
        self.ok = ok
        self.bound: dict[str, object] = {}
        self.on_any_key = None
        self.push_to_talk = None
        self.started = 0
        self.stopped = 0

    def bind(self, spec, fn):
        hotkeys.parse(spec)  # raises ValueError on a bad spec, like the real taps
        self.bound[spec] = fn

    def start(self):
        self.started += 1
        return self.ok

    def stop(self):
        self.stopped += 1


class FakeMouse:
    def __init__(self, detector, ok: bool):
        self.detector = detector
        self.ok = ok
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1
        return self.ok

    def stop(self):
        self.stopped += 1


class FakeServer:
    fail = False

    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.started = 0
        self.closed = 0

    def start(self):
        if self.fail:
            raise OSError("the pipe exists")
        self.started += 1

    def close(self):
        self.closed += 1


class TakenServer(FakeServer):
    fail = True


class FakeInstance:
    def __init__(self, free: bool = True):
        self.free = free
        self.released = 0

    def acquire(self):
        return self.free

    def release(self):
        self.released += 1


def fake_events(kb_ok: bool = True, mouse_ok: bool = True):
    made = SimpleNamespace(keyboard=None, mouse=None)

    def keyboard():
        made.keyboard = FakeKeyboard(kb_ok)
        return made.keyboard

    def mouse(detector):
        made.mouse = FakeMouse(detector, mouse_ok)
        return made.mouse

    return SimpleNamespace(KeyboardTap=keyboard, MouseTap=mouse, accessibility_trusted=lambda prompt=False: True)


FAKE_CAPTURE = SimpleNamespace(
    ax_selection=lambda: None, clipboard_change_count=lambda: 0, copied_since=lambda count: "",
    wake_accessibility=lambda pid: None, front_app_pid=lambda: None,
)


@pytest.fixture
def make_app(qapp, monkeypatch):
    from lyssna.win import app as app_mod

    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)  # no download
    warmed = []
    monkeypatch.setattr(Controller, "warm_dictation", lambda self: warmed.append(True))
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE)
    deferred = []
    monkeypatch.setattr(app_mod, "later", lambda delay, fn, *args: deferred.append((delay, fn)))
    made = []

    def make(kb_ok=True, mouse_ok=True, server_cls=FakeServer, template=None):
        if template is not None:
            config.CONFIG_PATH.write_text(template)
        events = fake_events(kb_ok, mouse_ok)
        a = app_mod.LyssnaWinApp(FakeInstance(), events=events, capture=FAKE_CAPTURE, control_server=server_cls)
        a.deferred, a.warmed = deferred, warmed
        made.append(a)
        return a

    yield make
    for a in made:
        a.shutdown()
        a.tray._unsubscribe()
        for panel in (a.mini_player, a.toast, a.selection_button):
            if panel is not None:
                panel.hide()
        if a.setup_window is not None:
            a.setup_window.hide()
            a.setup_window.deleteLater()  # not left for interpreter exit, where its theme hook would complain


@pytest.fixture
def lyssna(make_app):
    return make_app()


def test_the_eight_hotkeys_are_bound_to_the_controller(lyssna):
    hk = lyssna.ctl.cfg["hotkeys"]
    ctl = lyssna.ctl
    want = {"speak": lyssna._speak_from_hotkey, "pause": ctl.toggle_pause, "stop": ctl.stop, "next": ctl.next,
            "prev": ctl.prev, "faster": ctl.faster, "slower": ctl.slower, "button": lyssna._toggle_button_from_hotkey}
    bound = lyssna.keyboard.bound
    assert len(bound) == 8
    for action, fn in want.items():
        assert bound[hk[action]] == fn, action
    assert lyssna.keyboard.on_any_key == lyssna._dismiss_button


def test_a_bad_hotkey_is_logged_and_skipped(make_app, caplog):
    template = config.TEMPLATE.replace('speak  = "ctrl+alt+s"', 'speak  = "ctrl+alt+nosuchkey"')
    assert template != config.TEMPLATE
    a = make_app(template=template)
    assert "bad hotkey for speak" in caplog.text
    assert len(a.keyboard.bound) == 7 and "ctrl+alt+nosuchkey" not in a.keyboard.bound


def test_the_speak_hotkey_dismisses_the_play_button(lyssna, monkeypatch):
    calls = []
    monkeypatch.setattr(lyssna.selection_button, "dismiss", lambda: calls.append("dismiss"))
    monkeypatch.setattr(lyssna.ctl, "speak_selection", lambda: calls.append("speak"))
    lyssna._speak_from_hotkey()
    assert calls == ["dismiss", "speak"]


def test_taps_failing_shows_the_tray_warning(make_app):
    a = make_app(kb_ok=False)
    assert a.taps_ok is False and a.tray.warning is True
    assert a.tray.item_warning.isVisible()
    a.show_setup()
    assert a.setup_window.taps_ok() is False


def test_the_mouse_tap_failing_also_counts(make_app):
    a = make_app(mouse_ok=False)
    assert a.taps_ok is False and a.tray.warning is True


def test_taps_starting_shows_no_warning(lyssna):
    assert lyssna.taps_ok is True and lyssna.tray.warning is False
    assert lyssna.keyboard.started == 1 and lyssna.mouse.started == 1


def test_the_selection_gesture_presents_the_button_on_the_gui_thread(lyssna, monkeypatch):
    shown = []
    monkeypatch.setattr(lyssna.selection_button, "present",
                        lambda x, y, avoid, bounds: shown.append((x, y, avoid, bounds, threading.current_thread())))
    monkeypatch.setattr(lyssna.mini_player, "visible", lambda: True)
    monkeypatch.setattr(lyssna.mini_player, "frame", lambda: (10.0, 20.0, 400.0, 156.0))
    worker = threading.Thread(target=lyssna._on_selection_gesture, args=(Point(500.0, 600.0), (1, 2, 3, 4), "selection"))
    worker.start()
    worker.join()
    pump(lambda: shown)
    assert shown == [(500.0, 600.0, (10.0, 20.0, 400.0, 156.0), (1, 2, 3, 4), threading.main_thread())]


def test_the_button_stays_away_when_it_is_switched_off(lyssna, monkeypatch):
    shown = []
    monkeypatch.setattr(lyssna.selection_button, "present", lambda *args: shown.append(args))
    lyssna.ctl.selection_button_enabled = False
    lyssna._present_button(Point(1.0, 2.0), None, "selection")
    assert shown == []


@pytest.mark.parametrize(("source", "want"), [("clipboard", "clipboard"), ("selection", "selection")])
def test_play_reads_what_the_button_was_offered_for(lyssna, monkeypatch, source, want):
    read = []
    monkeypatch.setattr(lyssna.selection_button, "present", lambda *args: None)
    monkeypatch.setattr(lyssna.ctl, "speak_clipboard", lambda: read.append("clipboard"))
    monkeypatch.setattr(lyssna.ctl, "speak_selection", lambda: read.append("selection"))
    lyssna._present_button(Point(1.0, 2.0), None, source)
    lyssna.selection_button.on_play()
    assert read == [want]


def test_over_our_ui_takes_physical_points(lyssna, monkeypatch):
    from lyssna.ui.qt import coords

    # One screen at 200 percent: physical (230, 230) is logical (115, 115).
    table = [((0.0, 0.0, 1000.0, 800.0), 2.0)]
    monkeypatch.setattr(coords, "physical_to_logical", lambda x, y: coords.map_physical_to_logical(x, y, table))
    button = lyssna.selection_button
    monkeypatch.setattr(button, "visible", lambda: True)
    monkeypatch.setattr(button, "frame", lambda: (100.0, 100.0, 34.0, 34.0))
    assert lyssna._is_over_our_ui(Point(230.0, 230.0))
    assert not lyssna._is_over_our_ui(Point(115.0, 115.0))  # a logical point inside would be a miss in physical pixels


def test_controller_status_reaches_the_player(lyssna, monkeypatch):
    playing = Status(state="playing", index=1, total=3, session=1)
    monkeypatch.setattr(Controller, "status", property(lambda self: playing))
    seen = []
    monkeypatch.setattr(lyssna.mini_player, "_update", lambda st: seen.append(st))
    lyssna.ctl._changed(playing)
    pump(lambda: seen)
    assert seen[0].state == "playing" and seen[0].index == 1


def test_a_toast_is_presented_above_the_player(lyssna, monkeypatch):
    shown = []
    monkeypatch.setattr(lyssna.toast, "present", lambda notice, screen=None, floor=None: shown.append((notice.text, screen, floor)))
    monkeypatch.setattr(lyssna.mini_player, "visible", lambda: True)
    monkeypatch.setattr(lyssna.mini_player, "toast_floor", lambda gap=12.0: 123.0)
    lyssna.ctl.toast(Notice("Hello"))
    assert shown == []  # queued, never inline
    pump(lambda: shown)
    assert shown == [("Hello", lyssna.mini_player.panel.screen(), 123.0)]


def test_the_control_channel_gets_ui_and_quit(lyssna, monkeypatch):
    assert set(lyssna.ctl.extensions) == {"ui", "quit"}
    assert lyssna.server.started == 1 and lyssna.server.dispatch == lyssna.ctl.dispatch
    reply = lyssna.ctl.dispatch({"cmd": "ui"})
    assert reply["ok"] is True and reply["mini_player"] is None and reply["selection_button"] is None
    opened = []
    monkeypatch.setattr(lyssna, "show_setup", lambda: opened.append(True))
    lyssna.ctl.dispatch({"cmd": "ui", "show": "setup"})
    assert opened == [True]
    assert lyssna.ctl.dispatch({"cmd": "quit"})["ok"] is True
    assert (0.2, lyssna.tray.quit) in lyssna.deferred


def test_first_launch_opens_setup_once(lyssna, monkeypatch):
    opened = []
    monkeypatch.setattr(lyssna, "show_setup", lambda *args: opened.append(True))
    welcome = [fn for delay, fn in lyssna.deferred if delay == 1.0]
    assert welcome == [lyssna._welcome_once]
    assert (0.5, lyssna.mini_player.show_idle) in lyssna.deferred
    assert lyssna.tray.whats_new_pending is False  # a first install has no update note
    lyssna._welcome_once()
    assert (config.STATE_DIR / "welcomed").exists()
    lyssna._welcome_once()
    assert opened == [True]


def test_features_off_means_no_dictation(lyssna):
    from lyssna.win import app as app_mod

    assert app_mod.FEATURES == {"dictate": False, "history": False}
    assert lyssna.keyboard.push_to_talk is None and lyssna.ctl.push_to_talk is None
    assert lyssna.warmed == []
    assert lyssna.mini_player.features == app_mod.FEATURES and lyssna.tray.features == app_mod.FEATURES
    assert not {"dictate", "dictate_stop", "dictate_cancel", "history"} & set(lyssna.mini_player.actions)
    lyssna.show_setup()
    assert lyssna.setup_window.features["dictate"] is False


def test_shutdown_stops_everything_once(lyssna, monkeypatch):
    hidden = []
    monkeypatch.setattr(lyssna.tray.tray, "hide", lambda: hidden.append(True))
    stopped = []
    monkeypatch.setattr(lyssna.ctl, "shutdown", lambda: stopped.append(True))
    lyssna.shutdown()
    lyssna.shutdown()
    assert lyssna.keyboard.stopped == 1 and lyssna.mouse.stopped == 1
    assert lyssna.server.closed == 1 and stopped == [True] and hidden == [True]
    assert lyssna.instance.released == 1
    assert not lyssna.tray.timer.isActive()


def test_a_second_launch_asks_the_running_copy_to_show_itself(qapp, monkeypatch):
    from lyssna.win import app as app_mod

    sent = []
    monkeypatch.setattr(host.ipc, "send", lambda req, timeout=5.0: sent.append(req) or {"ok": True})
    monkeypatch.setattr(app_mod, "SingleInstance", lambda: FakeInstance(free=False))

    def refuse(*args, **kwargs):
        raise AssertionError("built an app although another copy runs")

    monkeypatch.setattr(app_mod, "LyssnaWinApp", refuse)
    monkeypatch.setattr(app_mod, "_qapplication", refuse)
    app_mod.run()
    assert sent == [app_mod.SHOW_REQUEST]


def test_losing_the_pipe_race_counts_as_a_second_launch(make_app, monkeypatch):
    sent = []
    monkeypatch.setattr(host.ipc, "send", lambda req, timeout=5.0: sent.append(req) or {"ok": True})
    a = make_app(server_cls=TakenServer)
    assert a.lost_race is True and a.server is None
    assert a.keyboard is None and a.mouse is None  # the keys stay with the running copy
    assert a.deferred == []  # no setup, no idle squiggle
    a.run()
    assert sent == [{"cmd": "ui", "show": "setup"}]
    assert a._shut is True
