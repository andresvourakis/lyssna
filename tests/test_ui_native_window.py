"""The native window behaviour behind host.window: the floating panel hands every native setting to
the platform twin (at creation, on show, after every Qt window flag change), the twins keep the
same names, the Windows twin sets the same extended styles as before, and the Mac twin carries
on when the undocumented call is missing.

The last section runs only on a Mac with a window server: it starts a fresh Python with Qt's
Cocoa platform (the other tests share an offscreen QApplication, which cannot switch platform),
shows a real FloatingPanel for a moment and reads the NSWindow behind it."""

import json
import logging
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, Qt

from lyssna import host
from lyssna.mac import window as mac_window
from lyssna.win import window as win_window

REPO = Path(__file__).resolve().parent.parent


def pump(seconds: float = 0.0, until=None) -> None:
    end = time.monotonic() + (seconds if until is None else 2.0)
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


class FakeTwin:
    """Stands in for host.window and records what the interface asks of it."""

    POINTS_ARE_PHYSICAL = False

    def __init__(self, on_screen=None):
        self.calls: list[tuple] = []
        self.on_screen = on_screen

    def make_floating(self, widget):
        handle = widget.windowHandle()
        through = bool(handle is not None and handle.flags() & Qt.WindowType.WindowTransparentForInput)
        self.calls.append(("make_floating", widget, through))

    def set_click_through(self, widget, on):
        self.calls.append(("set_click_through", widget, on))

    def bring_to_top(self, widget):
        self.calls.append(("bring_to_top", widget))

    def is_on_screen(self, widget):
        self.calls.append(("is_on_screen", widget))
        return self.on_screen

    def prepare_app(self):
        self.calls.append(("prepare_app",))

    def hide_dock_icon(self):
        self.calls.append(("hide_dock_icon",))

    def activate_app(self):
        self.calls.append(("activate_app",))

    def set_dark_title_bar(self, widget, dark):
        self.calls.append(("set_dark_title_bar", widget, dark))
        return False

    def left_button_down(self):
        self.calls.append(("left_button_down",))
        return False

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def twin(monkeypatch):
    fake = FakeTwin()
    monkeypatch.setattr(host, "window", fake)
    return fake


def _panel():
    from lyssna.ui.panel import FloatingPanel

    return type("NativeTestPanel", (FloatingPanel,), {"width": 120.0, "height": 40.0})()


# -- the panel goes through the twin ---------------------------------------------------------


def test_the_panel_applies_the_native_settings_at_creation(qapp, twin):
    fp = _panel()
    assert ("make_floating", fp.panel, False) in twin.calls
    assert ("set_click_through", fp.panel, False) in twin.calls


def test_every_flag_change_is_followed_by_the_native_settings(qapp, twin):
    fp = _panel()
    fp.panel.winId()
    twin.calls.clear()
    fp.set_click_through(True)
    # make_floating ran after Qt's flag had changed, so it saw click-through already on.
    assert twin.calls[0] == ("make_floating", fp.panel, True)
    assert ("set_click_through", fp.panel, True) in twin.calls
    twin.calls.clear()
    fp.set_click_through(False)
    assert twin.calls[0] == ("make_floating", fp.panel, False)
    assert ("set_click_through", fp.panel, False) in twin.calls


def test_the_panel_changes_window_flags_in_one_place():
    """Only _set_window_flag may call setFlag/setWindowFlags, so the re-apply cannot be forgotten."""
    import ast

    from lyssna.ui import panel

    tree = ast.parse(Path(panel.__file__).read_text(encoding="utf-8"))
    callers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and node.attr in ("setFlag", "setFlags", "setWindowFlags", "setWindowFlag"):
                    callers.add(fn.name)
    assert callers == {"_set_window_flag"}


def test_order_front_brings_to_top_through_the_twin(qapp, twin):
    fp = _panel()
    twin.calls.clear()
    fp.order_front()
    assert ("bring_to_top", fp.panel) in twin.calls
    fp.hide()


def test_a_window_missing_from_the_screen_is_logged(qapp, monkeypatch, caplog):
    from lyssna.ui import panel

    monkeypatch.setattr(panel, "VERIFY_SECS", 0.0)
    for answer, logged in ((False, True), (True, False), (None, False)):
        fake = FakeTwin(on_screen=answer)
        monkeypatch.setattr(host, "window", fake)
        fp = _panel()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="lyssna.ui.panel"):
            fp.order_front()
            pump(until=lambda f=fake: "is_on_screen" in f.names())
            pump(0.02)
        assert "is_on_screen" in fake.names()
        assert any("not on screen" in r.message for r in caplog.records) is logged, answer
        fp.hide()


def test_the_titled_window_asks_the_twin_for_its_title_bar_and_the_front(qapp, twin):
    from lyssna.ui.window import LyssnaWindow

    w = LyssnaWindow("Test")
    w.resize(200, 100)
    w.show_centered()
    assert any(c[0] == "set_dark_title_bar" and c[1] is w for c in twin.calls)
    assert "activate_app" in twin.names()
    w.hide()


def test_coords_converts_only_where_the_twin_says_points_are_physical(qapp, twin, monkeypatch):
    from lyssna.ui import coords

    table = [((0.0, 0.0, 1280.0, 720.0), 1.5)]
    monkeypatch.setattr(coords, "_screen_table", lambda: table)
    assert coords.physical_to_logical(960, 540) == (960.0, 540.0)
    assert coords.physical_rect_to_logical((960, 540, 300, 60)) == (960.0, 540.0, 300.0, 60.0)
    twin.POINTS_ARE_PHYSICAL = True
    assert coords.physical_to_logical(960, 540) == (640.0, 360.0)
    assert coords.logical_to_physical(640, 360) == (960.0, 540.0)
    assert coords.physical_rect_to_logical((960, 540, 300, 60)) == (640.0, 360.0, 200.0, 40.0)


# -- the twins -------------------------------------------------------------------------------


def _public_functions(module) -> set[str]:
    import inspect

    return {n for n, f in vars(module).items() if inspect.isfunction(f) and f.__module__ == module.__name__ and not n.startswith("_")}


def test_the_twins_offer_the_same_functions():
    assert _public_functions(mac_window) == _public_functions(win_window)
    assert {"make_floating", "set_click_through", "bring_to_top", "is_on_screen", "prepare_app", "hide_dock_icon",
            "activate_app", "set_dark_title_bar", "left_button_down"} == _public_functions(mac_window)
    assert host.MODULES["window"] == ("lyssna.mac.window", "lyssna.win.window")


class FakeUser32:
    def __init__(self, style: int):
        self.style = style
        self.set_calls: list[int] = []
        self.pos_calls: list[tuple] = []

    def GetWindowLongPtrW(self, hwnd, index):
        assert index == win_window.GWL_EXSTYLE
        return self.style

    def SetWindowLongPtrW(self, hwnd, index, value):
        self.set_calls.append(value)
        self.style = value
        return 0

    def SetWindowPos(self, *args):
        self.pos_calls.append(args)
        return 1


class FakeWidget:
    def winId(self):
        return 4242


def test_the_windows_twin_sets_the_same_extended_styles(monkeypatch):
    w = win_window
    u = FakeUser32(style=0x100)
    monkeypatch.setattr(w, "_user32", lambda: u)
    w.make_floating(FakeWidget())
    assert u.style == 0x100 | w.WS_EX_NOACTIVATE | w.WS_EX_TOOLWINDOW
    flags = w.SWP_NOMOVE | w.SWP_NOSIZE | w.SWP_NOZORDER | w.SWP_NOACTIVATE | w.SWP_FRAMECHANGED
    assert u.pos_calls == [(4242, None, 0, 0, 0, 0, flags)]
    w.make_floating(FakeWidget())  # nothing changes, so no second SetWindowPos
    assert len(u.pos_calls) == 1
    w.set_click_through(FakeWidget(), True)
    assert u.style & w.WS_EX_TRANSPARENT
    w.set_click_through(FakeWidget(), False)
    assert not u.style & w.WS_EX_TRANSPARENT and u.style & w.WS_EX_NOACTIVATE
    u.pos_calls.clear()
    w.bring_to_top(FakeWidget())
    assert u.pos_calls == [(4242, w.HWND_TOPMOST, 0, 0, 0, 0, w.SWP_NOMOVE | w.SWP_NOSIZE | w.SWP_NOACTIVATE | w.SWP_SHOWWINDOW)]
    assert w.is_on_screen(FakeWidget()) is None


class FakeNSWindow:
    """An NSPanel stand-in: answers the selectors in `selectors` and records the setters."""

    def __init__(self, selectors=None, prevents_raises=False):
        self.selectors = set(selectors if selectors is not None else
                             {"setBecomesKeyOnlyIfNeeded:", mac_window.PREVENTS_ACTIVATION})
        self.prevents_raises = prevents_raises
        self.state: dict = {}

    def respondsToSelector_(self, name):
        return name in self.selectors

    def setStyleMask_(self, v):
        self.state["styleMask"] = v

    def setBecomesKeyOnlyIfNeeded_(self, v):
        self.state["becomesKeyOnlyIfNeeded"] = v

    def setLevel_(self, v):
        self.state["level"] = v

    def setCollectionBehavior_(self, v):
        self.state["collectionBehavior"] = v

    def setHidesOnDeactivate_(self, v):
        self.state["hidesOnDeactivate"] = v

    def _setPreventsActivation_(self, v):
        if self.prevents_raises:
            raise AttributeError("gone")
        self.state["preventsActivation"] = v


PUBLIC = {"styleMask": 0x80, "becomesKeyOnlyIfNeeded": True, "level": 25, "collectionBehavior": 0x111,
          "hidesOnDeactivate": False}


def test_the_mac_twin_applies_the_legacy_panel_settings():
    win = FakeNSWindow()
    mac_window._float_window(win)
    assert win.state == {**PUBLIC, "preventsActivation": True}


def test_the_mac_twin_carries_on_without_the_undocumented_call(monkeypatch, caplog):
    monkeypatch.setattr(mac_window, "_warned_no_prevents_activation", False)
    with caplog.at_level(logging.WARNING, logger="lyssna.mac.window"):
        for _ in range(3):
            win = FakeNSWindow(selectors={"setBecomesKeyOnlyIfNeeded:"})
            mac_window._float_window(win)
            assert win.state == PUBLIC
        broken = FakeNSWindow(prevents_raises=True)
        mac_window._float_window(broken)
        assert {k: broken.state[k] for k in PUBLIC} == PUBLIC
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and mac_window.PREVENTS_ACTIVATION in warnings[0].getMessage()


def test_the_mac_twin_does_nothing_without_cocoa(qapp):
    """The offscreen tests and the preview shots have no NSWindow: nothing is touched, and
    bring_to_top falls back to Qt's raise."""
    from PySide6.QtWidgets import QWidget

    assert qapp.platformName() != "cocoa"
    w = QWidget()
    mac_window.make_floating(w)
    mac_window.set_click_through(w, True)
    mac_window.bring_to_top(w)
    mac_window.activate_app()
    assert mac_window.is_on_screen(w) is None
    assert mac_window.set_dark_title_bar(w, True) is False
    assert mac_window.left_button_down() is False  # Qt's own answer: no button is held in a test


def test_the_mac_twin_keeps_qt_from_bringing_the_app_forward_at_launch(monkeypatch):
    monkeypatch.delenv("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM", raising=False)
    mac_window.prepare_app()
    assert os.environ["QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"] == "1"
    monkeypatch.delenv("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM")
    win_window.prepare_app()  # nothing to do on Windows
    assert "QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM" not in os.environ


def test_the_windows_twin_asks_the_system_for_the_mouse_button(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    state = {"bits": 0x8000}
    user32 = SimpleNamespace(GetAsyncKeyState=lambda vk: state["bits"] if vk == 0x01 else 0)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    assert win_window.left_button_down() is True
    state["bits"] = 0x0001  # pressed since the last call, but not held now
    assert win_window.left_button_down() is False


# -- a real panel on a Mac -------------------------------------------------------------------


def _window_server() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import Quartz

        return Quartz.CGSessionCopyCurrentDictionary() is not None
    except Exception:
        return False


REAL_PANEL = textwrap.dedent(
    """
    import json, time
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    app = QApplication(["lyssna-native-window-test"])
    app.setQuitOnLastWindowClosed(False)

    import AppKit
    from lyssna import host
    from lyssna.mac import window as mac_window
    from lyssna.ui.panel import FloatingPanel

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QCoreApplication.processEvents()
            time.sleep(0.005)

    pump(0.3)  # let the app finish launching first: the accessory policy set before that brings it to the front
    host.window.hide_dock_icon()

    def facts(fp):
        win = mac_window._native_window(fp.panel)
        behaviour = int(win.collectionBehavior())
        return {
            "class": type(win).__name__,
            "is_panel": bool(win.isKindOfClass_(AppKit.NSPanel)),
            "nonactivating": bool(int(win.styleMask()) & AppKit.NSWindowStyleMaskNonactivatingPanel),
            "status_level": int(win.level()) == AppKit.NSStatusWindowLevel,
            "all_spaces": bool(behaviour & AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces),
            "full_screen_auxiliary": bool(behaviour & AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary),
            "stationary": bool(behaviour & AppKit.NSWindowCollectionBehaviorStationary),
            "hides_on_deactivate": bool(win.hidesOnDeactivate()),
            "key_only_if_needed": bool(win.becomesKeyOnlyIfNeeded()),
            "ignores_mouse": bool(win.ignoresMouseEvents()),
        }

    class Panel(FloatingPanel):
        width, height = 120.0, 40.0

    fp = Panel()
    out = {"platform": app.platformName(), "created": facts(fp)}
    try:
        from lyssna.ui import coords

        vx, vy, vw, _vh = coords.visible_frame(coords.primary_screen())
        fp.show_at(vx + vw - 160, vy + 80)
        pump(0.3)
        out["shown"] = facts(fp)
        out["on_screen"] = host.window.is_on_screen(fp.panel)
        fp.set_click_through(True)
        pump(0.05)
        out["click_through_on"] = facts(fp)
        fp.set_click_through(False)
        pump(0.05)
        out["click_through_off"] = facts(fp)
        out["policy_accessory"] = int(AppKit.NSApp.activationPolicy()) == AppKit.NSApplicationActivationPolicyAccessory
    finally:
        fp.hide()
        pump(0.05)
    print("RESULT " + json.dumps(out))
    """
)


@pytest.mark.skipif(not _window_server(), reason="needs a Mac with a window server")
def test_a_real_panel_on_the_mac_has_the_legacy_panel_settings():
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    env["QT_QPA_PLATFORM"] = "cocoa"
    # Run from source Qt would turn the process into a regular app and bring it to the front;
    # this keeps the test from disturbing whatever app the person is using. The test asserts on
    # the window's own settings only, never on which app is in front.
    env["QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"] = "1"
    result = subprocess.run([sys.executable, "-c", REAL_PANEL], cwd=REPO, env=env, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    line = next(x for x in result.stdout.splitlines() if x.startswith("RESULT "))
    out = json.loads(line[len("RESULT "):])
    assert out["platform"] == "cocoa"
    want = {"is_panel": True, "nonactivating": True, "status_level": True, "all_spaces": True, "full_screen_auxiliary": True,
            "stationary": True, "hides_on_deactivate": False, "key_only_if_needed": True}
    for stage in ("created", "shown", "click_through_on", "click_through_off"):
        assert {k: out[stage][k] for k in want} == want, (stage, out[stage])
    assert out["click_through_on"]["ignores_mouse"] and not out["click_through_off"]["ignores_mouse"]
    assert out["on_screen"] is True
    assert out["policy_accessory"]
