"""lyssna/win/fieldprobe.py with UI Automation faked, so it runs on any system: the four reads
the correction watcher makes, the character cap applied before text crosses from the app, the
handles that keep COM objects on their thread, every failure reading as unknown, and the real
EditWatcher learning a typed correction through it."""

import os
import threading
import time

import pytest

from lyssna.dictation.learn import EditWatcher
from lyssna.win import capture, fieldprobe
from lyssna.win.fieldprobe import FieldProbe

OWN = os.getpid()
OTHER = OWN + 1
EDIT, DOCUMENT, GROUP, CUSTOM, BUTTON = 50004, 50030, 50026, 50025, 50000


class UIA:
    UIA_ValuePatternId = 10002
    UIA_TextPatternId = 10014
    IUIAutomationValuePattern = "IUIAutomationValuePattern"
    IUIAutomationTextPattern = "IUIAutomationTextPattern"


class Pattern:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def QueryInterface(self, interface):
        return self


class Document:
    """A Text pattern's document range; records how many characters were asked for."""

    def __init__(self, text):
        self.text, self.asked = text, []

    def GetText(self, limit):
        self.asked.append(limit)
        return self.text if limit < 0 else self.text[:limit]


class El:
    def __init__(self, control_type=EDIT, value=None, read_only=False, document=None, pid=OTHER, parent=None):
        self.CurrentControlType, self.CurrentProcessId, self.parent = control_type, pid, parent
        self.patterns = {}
        if value is not None:
            self.value_pattern = Pattern(CurrentValue=value, CurrentIsReadOnly=read_only)
            self.patterns[UIA.UIA_ValuePatternId] = self.value_pattern
        if document is not None:
            self.patterns[UIA.UIA_TextPatternId] = Pattern(DocumentRange=document)

    def GetCurrentPattern(self, pattern_id):
        return self.patterns.get(pattern_id)


class Walker:
    def GetParentElement(self, element):
        return element.parent


class Automation:
    def __init__(self):
        self.focused = None
        self.ControlViewWalker = Walker()
        self.compared = 0

    def GetFocusedElement(self):
        return self.focused

    def CompareElements(self, a, b):
        self.compared += 1
        return a is b


class InlineCom:
    def __init__(self):
        self.calls, self.timeouts = 0, []

    def call(self, fn, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        return fn()


@pytest.fixture
def uia(monkeypatch):
    state = {"pid": OTHER, "class": "Notepad", "hwnd": 100}
    automation, com = Automation(), InlineCom()
    monkeypatch.setattr(capture, "_foreground_window", lambda: state["hwnd"])
    monkeypatch.setattr(capture, "_window_class", lambda hwnd: state["class"])
    monkeypatch.setattr(capture, "_window_pid", lambda hwnd: state["pid"] if hwnd else None)
    monkeypatch.setattr(capture, "_com", com)
    monkeypatch.setitem(capture._uia, "UIA", UIA)
    monkeypatch.setitem(capture._uia, "automation", automation)
    state["automation"], state["com"] = automation, com
    return state


def test_focused_hands_out_a_handle_not_the_com_object(uia):
    element = El(value="hello")
    uia["automation"].focused = element
    probe = FieldProbe()
    handle = probe.focused()
    assert handle is not None and handle is not element and not hasattr(handle, "GetCurrentPattern")
    assert probe.value(handle) == "hello"
    assert set(uia["com"].timeouts) == {FieldProbe.timeout}


def test_value_reads_the_text_pattern_document_with_the_cap(uia):
    document = Document("Dear team, I met matias at the cafe.")
    uia["automation"].focused = El(DOCUMENT, document=document)
    probe = FieldProbe()
    assert probe.value(probe.focused()) == "Dear team, I met matias at the cafe."
    assert document.asked == [FieldProbe.max_chars + 1]  # never the whole of a huge document


def test_a_read_only_value_gives_way_to_the_text_pattern(uia):
    document = Document("typed in the page")
    uia["automation"].focused = El(DOCUMENT, value="https://example.com", read_only=True, document=document)
    probe = FieldProbe()
    assert probe.value(probe.focused()) == "typed in the page"


def test_the_text_may_sit_on_a_near_ancestor(uia):
    uia["automation"].focused = El(CUSTOM, parent=El(GROUP, parent=El(EDIT, value="up two")))
    probe = FieldProbe()
    assert probe.value(probe.focused()) == "up two"


def test_an_element_without_text_reads_as_unknown(uia):
    uia["automation"].focused = El(BUTTON)
    probe = FieldProbe()
    assert probe.value(probe.focused()) is None


@pytest.mark.parametrize("over", ["value", "document"])
def test_text_past_the_cap_reads_as_unknown(uia, monkeypatch, over):
    monkeypatch.setattr(FieldProbe, "max_chars", 10)
    long = "x" * 11
    uia["automation"].focused = El(value=long) if over == "value" else El(DOCUMENT, document=Document(long))
    probe = FieldProbe()
    assert probe.value(probe.focused()) is None
    uia["automation"].focused = El(value="x" * 10)
    assert probe.value(probe.focused()) == "x" * 10


def test_the_cap_is_the_watchers():
    assert FieldProbe.max_chars == EditWatcher.MAX_CHARS


def test_same_compares_the_elements_on_the_ui_automation_thread(uia):
    field, other = El(value="a"), El(value="b")
    probe = FieldProbe()
    uia["automation"].focused = field
    first, again = probe.focused(), probe.focused()
    uia["automation"].focused = other
    elsewhere = probe.focused()
    assert probe.same(first, first) and uia["automation"].compared == 0  # one handle: no COM call
    assert probe.same(first, again) is True
    assert probe.same(first, elsewhere) is False
    assert probe.same(first, None) is False and probe.same("x", first) is False


def test_old_handles_are_let_go_but_one_in_use_is_kept(uia):
    uia["automation"].focused = El(value="kept")
    probe = FieldProbe()
    watched = probe.focused()
    first_spare = probe.focused()
    for _ in range(3 * FieldProbe.keep):
        probe.focused()
        assert probe.value(watched) == "kept"  # read on every poll, so never dropped
    assert len(probe._elements) == FieldProbe.keep
    assert probe.value(first_spare) is None and probe.same(first_spare, watched) is False


def test_our_own_window_is_never_read(uia):
    uia["pid"] = OWN
    uia["automation"].focused = El(value="ours")
    assert FieldProbe().focused() is None
    assert uia["com"].calls == 0


def test_focus_in_our_own_process_is_not_handed_out(uia):
    uia["automation"].focused = El(value="ours", pid=OWN)
    assert FieldProbe().focused() is None


def test_no_focused_element(uia):
    assert FieldProbe().focused() is None


@pytest.mark.parametrize("cls", sorted(capture.CONSOLE_CLASSES))
def test_consoles_are_not_watched(uia, cls):
    uia["class"] = cls
    uia["automation"].focused = El(DOCUMENT, document=Document("PS C:\\> "))
    assert FieldProbe().focused() is None
    assert uia["com"].calls == 0


def test_errors_read_as_unknown(uia):
    uia["automation"].focused = El(value="x")
    probe = FieldProbe()
    handle = probe.focused()

    def broken(*args):
        raise OSError("COM error")

    uia["automation"].GetFocusedElement = broken
    uia["automation"].CompareElements = broken
    probe._elements[handle.key]["reader"] = lambda limit: broken()
    other = fieldprobe._Field(handle.key + 1000)
    assert probe.focused() is None
    assert probe.value(handle) is None
    assert probe.same(handle, other) is False


def test_a_hung_app_reads_as_unknown_after_the_short_timeout(uia, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(capture, "_com", capture._ComThread(init=lambda: None))
    uia["automation"].GetFocusedElement = lambda: (release.wait(2), El(value="late"))[1]
    start = time.monotonic()
    try:
        assert FieldProbe().focused() is None
        assert time.monotonic() - start < 1.0
    finally:
        release.set()


def test_front_pid_is_the_foreground_windows_owner(uia):
    assert FieldProbe().front_pid() == OTHER
    uia["hwnd"] = None
    assert FieldProbe().front_pid() is None


# -- the real watcher, through the Windows probe ----------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class TimedValue:
    """An Edit control's Value pattern whose text changes at given times: the user typing a fix."""

    CurrentIsReadOnly = False

    def __init__(self, clock, values):
        self.clock, self.values = clock, sorted(values.items())

    @property
    def CurrentValue(self):
        out = None
        for t, v in self.values:
            if self.clock.t >= t:
                out = v
        return out

    def QueryInterface(self, interface):
        return self


def timed_edit(clock, values):
    element = El(EDIT)
    element.patterns[UIA.UIA_ValuePatternId] = TimedValue(clock, values)
    return element


def test_the_watcher_learns_a_correction_typed_in_a_windows_field(uia):
    clock = Clock()
    field = timed_edit(clock, {0: "Notes: I met matias at the cafe", 3: "Notes: I met Mattias at the cafe"})
    uia["automation"].focused = field
    learned = []
    watcher = EditWatcher(lambda o, c: learned.append((o, c)), probe=FieldProbe(), clock=clock, sleep=clock.sleep)
    watcher.watch("I met matias at the cafe", background=False)
    assert learned == [("matias", "Mattias")]
    assert clock.t >= EditWatcher.LIMIT  # watched to the limit, the same element all along


def test_the_watcher_stops_when_focus_moves_in_windows(uia):
    clock = Clock()
    field = timed_edit(clock, {0: "I met matias at the cafe", 10: "I met Mattias at the cafe"})
    uia["automation"].focused = field
    watcher = EditWatcher(lambda o, c: pytest.fail("nothing to learn"), probe=FieldProbe(), clock=clock, sleep=clock.sleep)

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t > 2:
            uia["automation"].focused = El(value="another field")

    watcher.sleep = sleep
    watcher.watch("I met matias at the cafe", background=False)
    assert clock.t < 3
