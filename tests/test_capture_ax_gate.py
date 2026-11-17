"""The rule in capture.py: no Accessibility client call that can land in our own process from
a background thread (AppKit's accessibility code is not thread-safe; racing the main thread
crashed the app). These tests replace the Accessibility functions with fakes, so nothing
touches the real Accessibility API."""

import os
import threading

import ApplicationServices
import pytest
import Quartz

from lyssna import capture

OWN = os.getpid()
OTHER = OWN + 1


def _window(pid, x=0, y=0, w=100, h=100, layer=0):
    return {
        Quartz.kCGWindowOwnerPID: pid,
        Quartz.kCGWindowLayer: layer,
        Quartz.kCGWindowAlpha: 1.0,
        Quartz.kCGWindowBounds: {"X": x, "Y": y, "Width": w, "Height": h},
    }


class _Forbidden(BaseException):
    """Raised by an Accessibility fake that must not run. A BaseException, so the capture
    functions' `except Exception` cannot swallow it."""


def _forbidden(name):
    def fail(*args, **kwargs):
        raise _Forbidden(f"{name} must not be called")

    return fail


def _in_worker(fn):
    out = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as e:  # surface assertion failures on the test thread
            out["error"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join(5)
    if "error" in out:
        raise out["error"]
    return out["value"]


class _FakeApp:
    def __init__(self, pid):
        self.pid = pid

    def processIdentifier(self):
        return self.pid


class _FakeWorkspace:
    def __init__(self, pid):
        self.app = _FakeApp(pid)

    def sharedWorkspace(self):
        return self

    def frontmostApplication(self):
        return self.app


def _front(monkeypatch, pid, active=False):
    monkeypatch.setattr(capture, "NSWorkspace", _FakeWorkspace(pid))

    class _App:
        def isActive(self):
            return active

    monkeypatch.setattr(capture, "NSApp", lambda: _App())


# -- hit test -------------------------------------------------------------------------------

@pytest.mark.parametrize("layer", [0, 25, 101])  # normal window, status item, menu
def test_hit_test_on_our_window_returns_our_pid_without_accessibility(monkeypatch, layer):
    monkeypatch.setattr(capture, "_on_screen_windows", lambda: [_window(OWN, layer=layer), _window(OTHER)])
    monkeypatch.setattr(
        ApplicationServices, "AXUIElementCopyElementAtPosition", _forbidden("AXUIElementCopyElementAtPosition")
    )
    assert _in_worker(lambda: capture.window_owner_pid_at(10, 10)) == OWN


def test_hit_test_on_another_app_asks_accessibility(monkeypatch):
    monkeypatch.setattr(capture, "_on_screen_windows", lambda: [_window(OWN, x=500), _window(OTHER)])
    calls = []

    def at_position(system, x, y, out):
        calls.append((x, y))
        return 0, "element"

    monkeypatch.setattr(ApplicationServices, "AXUIElementCopyElementAtPosition", at_position)
    monkeypatch.setattr(ApplicationServices, "AXUIElementGetPid", lambda el, out: (0, 4242))
    assert capture.window_owner_pid_at(10, 10) == 4242
    assert calls == [(10, 10)]


def test_hit_test_fallback_still_skips_our_windows(monkeypatch):
    # Our window lies behind another app's at the point; Accessibility gives nothing.
    monkeypatch.setattr(capture, "_on_screen_windows", lambda: [_window(OTHER), _window(OWN)])
    monkeypatch.setattr(ApplicationServices, "AXUIElementCopyElementAtPosition", lambda *a: (-25204, None))
    assert capture.window_owner_pid_at(10, 10) == OTHER


def test_hit_test_without_a_window_list_skips_accessibility(monkeypatch):
    def broken():
        raise RuntimeError("no window list")

    monkeypatch.setattr(capture, "_on_screen_windows", broken)
    monkeypatch.setattr(
        ApplicationServices, "AXUIElementCopyElementAtPosition", _forbidden("AXUIElementCopyElementAtPosition")
    )
    assert capture.window_owner_pid_at(10, 10) is None


# -- focused element ------------------------------------------------------------------------

def _forbid_focus_reads(monkeypatch):
    for name in ("AXUIElementCopyAttributeValue", "AXUIElementCopyParameterizedAttributeValue",
                 "AXUIElementIsAttributeSettable"):
        monkeypatch.setattr(ApplicationServices, name, _forbidden(name))


@pytest.mark.parametrize("frontmost, active", [(True, False), (False, True)])
def test_worker_thread_with_our_app_in_front_reads_nothing(monkeypatch, frontmost, active):
    _front(monkeypatch, OWN if frontmost else OTHER, active=active)
    _forbid_focus_reads(monkeypatch)
    assert _in_worker(capture.focused_element) is None
    assert _in_worker(capture.ax_selection) == capture.AXSelection(False, "")
    assert _in_worker(capture.ax_selected_text) is None
    assert _in_worker(capture.ax_selection_bounds) is None


def test_worker_thread_never_reads_attributes_of_our_own_element(monkeypatch):
    _front(monkeypatch, OTHER)
    monkeypatch.setattr(ApplicationServices, "AXUIElementGetPid", lambda el, out: (0, OWN))
    _forbid_focus_reads(monkeypatch)
    assert _in_worker(lambda: capture.ax_attribute("element", "AXValue")) is None
    assert _in_worker(lambda: capture.ax_settable("element", "AXValue")) is False
    assert _in_worker(lambda: capture.ax_text_value("element")) is None
    assert _in_worker(lambda: capture.ax_role("element")) is None


class _FakeAX:
    """Accessibility for another app: a focused element with a selection."""

    def __init__(self, pid=OTHER):
        self.pid = pid
        self.calls = []

    def install(self, monkeypatch):
        monkeypatch.setattr(ApplicationServices, "AXUIElementCreateSystemWide", lambda: "system")
        monkeypatch.setattr(ApplicationServices, "AXUIElementCopyAttributeValue", self.copy)
        monkeypatch.setattr(ApplicationServices, "AXUIElementGetPid", lambda el, out: (0, self.pid))

    def copy(self, element, name, out):
        self.calls.append((element, str(name)))
        if element == "system":
            return 0, "focused"
        if str(name) == "AXSelectedText":
            return 0, "hello world"
        return -25212, None


def test_worker_thread_with_another_app_in_front_reads_as_before(monkeypatch):
    _front(monkeypatch, OTHER)
    ax = _FakeAX()
    ax.install(monkeypatch)
    assert _in_worker(capture.focused_element) == "focused"
    assert _in_worker(capture.ax_selection) == capture.AXSelection(True, "hello world")
    assert ("focused", "AXSelectedText") in ax.calls


def test_worker_thread_drops_focus_that_moved_to_our_app(monkeypatch):
    # The front-app check passed, but the focused element turns out to be ours.
    _front(monkeypatch, OTHER)
    ax = _FakeAX(pid=OWN)
    ax.install(monkeypatch)
    assert _in_worker(capture.ax_selection) == capture.AXSelection(False, "")
    assert all(element == "system" for element, _ in ax.calls)


def test_main_thread_with_our_app_in_front_reads_as_before(monkeypatch):
    _front(monkeypatch, OWN, active=True)
    ax = _FakeAX(pid=OWN)
    ax.install(monkeypatch)
    assert capture.focused_element() == "focused"
    assert capture.ax_selection() == capture.AXSelection(True, "hello world")
