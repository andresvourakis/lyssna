"""The app wiring (lyssna/app.py), built for real around a real Controller on the temp
config, but kept off the machine: the keyboard and mouse taps, the selection probes, the control
channel, the single-instance mutex and every deferred call are fakes, and the Qt event loop never
runs (queued calls are pumped by hand). Runs on the offscreen platform."""

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from lyssna import config, host, hotkeys
from lyssna.controller import Controller, Notice
from lyssna.gestures import Point
from lyssna.player import Status
from lyssna.providers import kokoro


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
    from lyssna import app as app_mod

    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)  # no download
    warmed = []
    monkeypatch.setattr(Controller, "warm_dictation", lambda self: warmed.append(True))
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    deferred = []
    monkeypatch.setattr(app_mod, "later", lambda delay, fn, *args: deferred.append((delay, fn)))
    made = []

    def make(kb_ok=True, mouse_ok=True, server_cls=FakeServer, template=None):
        if template is not None:
            config.CONFIG_PATH.write_text(template, encoding="utf-8")
        events = fake_events(kb_ok, mouse_ok)
        a = app_mod.LyssnaApp(FakeInstance(), events=events, capture=FAKE_CAPTURE, control_server=server_cls)
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
        for window in (a.setup_window, a.history_window):
            if window is not None and hasattr(window, "deleteLater"):
                window.hide()
                window.deleteLater()  # not left for interpreter exit, where its theme hook would complain


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
    from lyssna.ui import coords

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


class FakePushToTalk:
    """Stands in for host.pushtotalk and records what attach() was given."""

    def __init__(self, error: str = ""):
        self.error = error
        self.calls = []
        self.made = object()

    def attach(self, keyboard, cfg, on_start, on_stop, on_lock, on_cancel):
        if self.error:
            raise ValueError(self.error)
        self.calls.append((keyboard, cfg, on_start, on_stop, on_lock, on_cancel))
        keyboard.push_to_talk = self.made
        return self.made


def test_the_dictation_keys_attach_through_the_host_with_the_controllers_methods(make_app, monkeypatch):
    fake = FakePushToTalk()
    monkeypatch.setattr(host, "pushtotalk", fake, raising=False)
    a = make_app()
    [(keyboard, cfg, on_start, on_stop, on_lock, on_cancel)] = fake.calls
    assert keyboard is a.keyboard and cfg is a.ctl.cfg
    assert (on_start, on_stop, on_lock) == (a.ctl.dictation_start, a.ctl.dictation_stop, a.ctl.dictation_lock)
    cancelled = []
    monkeypatch.setattr(a.ctl, "dictation_cancel", lambda quiet=False: cancelled.append(quiet))
    on_cancel(True)
    on_cancel(False)
    assert cancelled == [True, False]
    assert a.ctl.push_to_talk is fake.made and a.keyboard.push_to_talk is fake.made


def test_the_real_dictation_keys_attach_to_the_keyboard(lyssna):
    """This computer's own host.pushtotalk, from the template's [dictation] section."""
    assert lyssna.ctl.push_to_talk is not None and lyssna.keyboard.push_to_talk is lyssna.ctl.push_to_talk


def test_a_bad_dictation_key_is_logged_and_toasted_and_the_app_still_starts(make_app, monkeypatch, caplog):
    toasts = []
    monkeypatch.setattr(Controller, "toast", lambda self, notice: toasts.append((notice.text, notice.seconds)))
    key_line = f'hotkey = "{host.DICTATION_KEY_DEFAULT}"'
    template = config.TEMPLATE.replace(key_line, 'hotkey = "nosuchkey"')
    assert template != config.TEMPLATE
    a = make_app(template=template)
    assert "bad dictation key" in caplog.text
    [(text, seconds)] = toasts
    assert "nosuchkey" in text and seconds == 8.0
    assert a.ctl.push_to_talk is None and a.keyboard.push_to_talk is None
    assert a.taps_ok is True and len(a.keyboard.bound) == 8  # everything else runs as usual
    assert (1.0, a._welcome_once) in a.deferred


def test_dictation_status_reaches_the_player(lyssna, monkeypatch):
    seen = []
    monkeypatch.setattr(lyssna.mini_player, "_set_dictation", lambda state: seen.append(state))
    worker = threading.Thread(target=lambda: lyssna.ctl.dictation._set_if(None, state="recording"))
    worker.start()
    worker.join()
    pump(lambda: seen)
    assert seen == ["recording"]


def test_the_player_has_the_dictation_and_history_actions(lyssna, monkeypatch):
    ctl, actions = lyssna.ctl, lyssna.mini_player.actions
    assert actions["dictate_stop"] == ctl.dictation_stop and actions["dictate_cancel"] == ctl.dictation_cancel
    assert actions["dictation_layout"] == ctl.save_dictation_layout and actions["history"] == lyssna.show_history
    started = []
    monkeypatch.setattr(ctl, "dictation_start", lambda hands_free=False: started.append(hands_free))
    actions["dictate"]()
    assert started == [True]  # the player's Dictate starts hands-free
    from lyssna.menu import key_name

    hotkey = ctl.cfg["dictation"].get("hotkey", host.DICTATION_KEY_DEFAULT)
    assert lyssna.mini_player.key_name == key_name(hotkey)


def test_the_history_window_opens_from_the_tray_and_the_player_and_is_made_once(lyssna, monkeypatch):
    from lyssna.ui import history_window

    made = []

    class FakeHistory:
        def __init__(self):
            self.ctl = None
            self.shown = 0
            made.append(self)

        def show(self):
            self.shown += 1

    monkeypatch.setattr(history_window, "HistoryWindow", FakeHistory)
    lyssna.tray.item_history.trigger()
    lyssna.mini_player.actions["history"]()
    assert len(made) == 1 and made[0].shown == 2 and made[0].ctl is lyssna.ctl
    assert lyssna.history_window is made[0]


def test_the_tray_opens_setup_at_the_dictation_keys(lyssna, monkeypatch):
    opened = []
    monkeypatch.setattr(lyssna, "show_setup", lambda engine=None, dictation=None: opened.append((engine, dictation)))
    lyssna.tray.on_setup_dictation("openai")
    assert opened == [(None, "openai")]


def test_the_microphone_is_warmed_once_at_launch(lyssna):
    assert lyssna.warmed == [True]


def test_the_ui_command_says_whether_dictation_is_ready(lyssna):
    reply = lyssna.ctl.dispatch({"cmd": "ui"})
    assert reply["dictation"]["keys"] is True
    pieces = reply["dictation"]["pieces"]
    assert set(pieces) == {"mic", "insert", "field_probe", "pushtotalk"}
    assert all(name.startswith(("lyssna.mac.", "lyssna.win.")) for name in pieces.values()), pieces
    assert "shown" not in reply
    assert lyssna.ctl.dispatch({"cmd": "ui", "show": "setup"})["shown"] == "setup"


def test_shutdown_cancels_the_timers_still_waiting(lyssna):
    from lyssna.ui import mainthread

    fired = []
    mainthread.later(0.05, lambda: fired.append(True))
    lyssna.shutdown()
    time.sleep(0.15)
    QCoreApplication.processEvents()
    assert fired == []


def test_the_app_has_no_feature_switch_left():
    """Dictation and history ship: the switch that held them back is gone from the package."""
    import re
    from pathlib import Path

    package = Path(__file__).resolve().parent.parent / "lyssna"
    hits = [f"{path.relative_to(package)}:{n}" for path in package.rglob("*.py") if "legacy" not in path.parts
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"\bfeatures\b|FEATURES", line)]
    assert hits == []


def test_the_window_titles_have_no_suffix(qapp, monkeypatch):
    """Qt on Windows writes a title as "<title> - <display name>" unless the title ends with the
    display name; the app's display name is empty, so the titles stay exactly the Mac's."""
    from lyssna import app as app_mod
    from lyssna.ui.history_window import HistoryWindow
    from lyssna.ui.setup_window import SetupWindow
    from lyssna.ui.whats_new_window import WhatsNewWindow

    calls = []
    monkeypatch.setattr(host, "window", SimpleNamespace(prepare_app=lambda: calls.append("prepare_app"),
                                                       hide_dock_icon=lambda: calls.append("hide_dock_icon")), raising=False)
    shown_name = qapp.applicationDisplayName()
    try:
        assert app_mod._qapplication() is qapp
        assert calls == ["prepare_app", "hide_dock_icon"]
        assert qapp.applicationName() == "Lyssna" and qapp.applicationDisplayName() == ""
        windows = [SetupWindow(None), HistoryWindow(), WhatsNewWindow()]
        assert [w.windowTitle() for w in windows] == ["Lyssna Setup", "Lyssna History", "What's New in Lyssna"]
        for w in windows:
            assert w.windowTitle().endswith(qapp.applicationDisplayName())  # Qt's own test for adding nothing
            w.deleteLater()
    finally:
        qapp.setApplicationDisplayName(shown_name)
        QCoreApplication.processEvents()


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
    from lyssna import app as app_mod

    sent = []
    monkeypatch.setattr(host.ipc, "send", lambda req, timeout=5.0: sent.append(req) or {"ok": True})
    monkeypatch.setattr(host.instance, "SingleInstance", lambda: FakeInstance(free=False))

    def refuse(*args, **kwargs):
        raise AssertionError("built an app although another copy runs")

    monkeypatch.setattr(app_mod, "LyssnaApp", refuse)
    monkeypatch.setattr(app_mod, "_qapplication", refuse)
    app_mod.run()
    assert sent == [app_mod.SHOW_REQUEST]


@pytest.mark.parametrize(("reply", "printed"), [({"ok": True, "shown": "setup"}, False), ({"ok": True}, True), (None, True)])
def test_a_second_launch_says_so_unless_the_running_copy_came_forward(qapp, monkeypatch, capsys, reply, printed):
    from lyssna import app as app_mod

    monkeypatch.setattr(host.ipc, "send", lambda req, timeout=5.0: reply)
    app_mod._ask_running_copy_to_show()
    assert ("Another Lyssna is running" in capsys.readouterr().err) is printed


def test_losing_the_pipe_race_counts_as_a_second_launch(make_app, monkeypatch):
    sent = []
    monkeypatch.setattr(host.ipc, "send", lambda req, timeout=5.0: sent.append(req) or {"ok": True})
    a = make_app(server_cls=TakenServer)
    assert a.lost_race is True and a.server is None
    assert a.keyboard is None and a.mouse is None  # the keys stay with the running copy
    assert a.deferred == []  # no setup, no idle squiggle
    assert a.warmed == [] and a.ctl.push_to_talk is None  # nor the microphone or the dictation keys
    a.run()
    assert sent == [{"cmd": "ui", "show": "setup"}]
    assert a._shut is True
