"""The rule in capture.py: no Accessibility client call that can land in our own process from
a background thread (AppKit's accessibility code is not thread-safe; racing the main thread
crashed the app). These tests replace the Accessibility functions with fakes, so nothing
touches the real Accessibility API."""

import os
import threading

import ApplicationServices
import pytest

from lyssna import capture

OWN = os.getpid()
OTHER = OWN + 1


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


# -- reading the selection: the plain route, then the text-marker route -----------------------

def _cf_range(location, length):
    return ApplicationServices.AXValueCreate(
        ApplicationServices.kAXValueTypeCFRange, ApplicationServices.CFRangeMake(location, length))


def _cg_rect(x, y, w, h):
    return ApplicationServices.AXValueCreate(ApplicationServices.kAXValueTypeCGRect, ((x, y), (w, h)))


def _markers(start=b"node1:0", end=b"node1:11"):
    """A real text marker range, built locally (no Accessibility call)."""
    return ApplicationServices.AXTextMarkerRangeCreate(
        None,
        ApplicationServices.AXTextMarkerCreate(None, start, len(start)),
        ApplicationServices.AXTextMarkerCreate(None, end, len(end)),
    )


class _ElementAX:
    """Another app's focused element answering the attributes in `attrs` and the
    parameterized attributes in `params`; anything else is unsupported."""

    def __init__(self, attrs, params=None):
        self.attrs, self.params = attrs, params or {}

    def install(self, monkeypatch):
        _front(monkeypatch, OTHER)
        monkeypatch.setattr(ApplicationServices, "AXUIElementCreateSystemWide", lambda: "system")
        monkeypatch.setattr(ApplicationServices, "AXUIElementCopyAttributeValue", self.copy)
        monkeypatch.setattr(ApplicationServices, "AXUIElementCopyParameterizedAttributeValue", self.copy_param)
        monkeypatch.setattr(ApplicationServices, "AXUIElementGetPid", lambda el, out: (0, OTHER))

    def copy(self, element, name, out):
        if element == "system":
            return 0, "focused"
        return self.attrs.get(str(name), (-25205, None))

    def copy_param(self, element, name, param, out):
        return self.params.get(str(name), (-25205, None))


def test_ax_selection_reads_the_plain_route(monkeypatch):
    _ElementAX(
        {"AXSelectedText": (0, "hello world"), "AXSelectedTextRange": (0, _cf_range(4, 11))},
        {"AXBoundsForRange": (0, _cg_rect(1, 2, 30, 10))},
    ).install(monkeypatch)
    assert _in_worker(capture.ax_selection) == capture.AXSelection(
        True, "hello world", ("plain", 4, 11), (1.0, 2.0, 30.0, 10.0))


def test_ax_selection_falls_back_to_text_markers(monkeypatch):
    # A web area: the plain route has no value, the text-marker route has the selection.
    markers = _markers()
    _ElementAX(
        {"AXSelectedText": (-25212, None), "AXSelectedTextMarkerRange": (0, markers)},
        {"AXStringForTextMarkerRange": (0, "hello world"), "AXBoundsForTextMarkerRange": (0, _cg_rect(5, 6, 70, 12))},
    ).install(monkeypatch)
    assert _in_worker(capture.ax_selection) == capture.AXSelection(
        True, "hello world", ("marker", b"node1:0", b"node1:11"), (5.0, 6.0, 70.0, 12.0))


def test_text_marker_identity_tells_selections_apart():
    assert capture._marker_identity(_markers()) == capture._marker_identity(_markers())
    assert capture._marker_identity(_markers()) != capture._marker_identity(_markers(start=b"node1:2"))
    assert capture._marker_identity("not a marker range") is None


def test_ax_selection_unsupported_when_neither_route_answers(monkeypatch):
    _ElementAX({}).install(monkeypatch)
    assert _in_worker(capture.ax_selection) == capture.AXSelection(False, "")


def test_ax_selection_empty_plain_route_keeps_its_range(monkeypatch):
    # Nothing selected, and no web content to ask: the caret's range comes back, text empty.
    _ElementAX({"AXSelectedText": (0, ""), "AXSelectedTextRange": (0, _cf_range(9, 0))}).install(monkeypatch)
    assert _in_worker(capture.ax_selection) == capture.AXSelection(True, "", ("plain", 9, 0))


# -- asking a silent app to turn its accessibility on --------------------------------------------

class _AppAX:
    """Apps by pid: whether their focused element answers, and what each set call returns."""

    def __init__(self, answers=False, manual=0, enhanced=-25208):
        self.answers, self.manual, self.enhanced = answers, manual, enhanced
        self.sets = []

    def install(self, monkeypatch):
        monkeypatch.setattr(capture, "_asked", capture.OrderedDict())
        monkeypatch.setattr(capture, "_warming", {})
        monkeypatch.setattr(capture, "_launch_key", lambda pid: (pid, 0.0))
        monkeypatch.setattr(ApplicationServices, "AXUIElementCreateApplication", lambda pid: ("app", pid))
        monkeypatch.setattr(ApplicationServices, "AXUIElementSetMessagingTimeout", lambda el, t: 0)
        monkeypatch.setattr(ApplicationServices, "AXUIElementCopyAttributeValue", self.copy)
        monkeypatch.setattr(ApplicationServices, "AXUIElementCopyParameterizedAttributeValue", lambda *a: (-25205, None))
        monkeypatch.setattr(ApplicationServices, "AXUIElementGetPid", lambda el, out: (0, OTHER))
        monkeypatch.setattr(ApplicationServices, "AXUIElementSetAttributeValue", self.set)

    def copy(self, element, name, out):
        if str(name) == "AXFocusedUIElement":
            return (0, "focused") if self.answers else (-25212, None)
        if str(name) == "AXSelectedText":
            return 0, ""
        return -25205, None

    def set(self, element, name, value):
        self.sets.append((element, str(name), value))
        return self.manual if str(name) == "AXManualAccessibility" else self.enhanced


def test_wake_asks_manual_accessibility_first(monkeypatch):
    ax = _AppAX(manual=0)
    ax.install(monkeypatch)
    _in_worker(lambda: capture.wake_accessibility(OTHER))
    assert ax.sets == [(("app", OTHER), "AXManualAccessibility", True)]


def test_wake_falls_back_to_enhanced_ui_only_when_unsupported(monkeypatch):
    ax = _AppAX(manual=-25205)
    ax.install(monkeypatch)
    _in_worker(lambda: capture.wake_accessibility(OTHER))
    assert [name for _, name, _ in ax.sets] == ["AXManualAccessibility", "AXEnhancedUserInterface"]
    ax = _AppAX(manual=-25204)  # any other error: no fallback
    ax.install(monkeypatch)
    _in_worker(lambda: capture.wake_accessibility(OTHER))
    assert [name for _, name, _ in ax.sets] == ["AXManualAccessibility"]


def test_wake_asks_each_process_once(monkeypatch):
    ax = _AppAX()
    ax.install(monkeypatch)
    for _ in range(3):
        _in_worker(lambda: capture.wake_accessibility(OTHER))
    assert len(ax.sets) == 1
    monkeypatch.setattr(capture, "_launch_key", lambda pid: (pid, 1.0))  # the pid now names a new process
    _in_worker(lambda: capture.wake_accessibility(OTHER))
    assert len(ax.sets) == 2


def test_wake_never_asks_our_own_process(monkeypatch):
    ax = _AppAX()
    ax.install(monkeypatch)
    monkeypatch.setattr(ApplicationServices, "AXUIElementCreateApplication", _forbidden("AXUIElementCreateApplication"))
    _in_worker(lambda: capture.wake_accessibility(OWN))
    _in_worker(lambda: capture.wake_accessibility(None))
    assert ax.sets == []


def test_wake_leaves_an_app_that_already_answers_alone(monkeypatch):
    ax = _AppAX(answers=True)
    ax.install(monkeypatch)
    _in_worker(lambda: capture.wake_accessibility(OTHER))
    assert ax.sets == []
    assert len(capture._asked) == 0  # not recorded: asked later if it falls silent
