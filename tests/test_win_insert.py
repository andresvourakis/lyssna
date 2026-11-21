"""lyssna/win/insert.py with the clipboard, SendInput and UI Automation faked, so it runs on any
system: the paste flow through the engine's Inserter, the clipboard history formats, the
sequence-number settling, the restore rule, the "copied" outcome, every text_target case, the
own-process gate and the console rule, and timeouts meaning "maybe"."""

import os
import threading
import time

import pytest

from lyssna.dictation.insert import Inserter
from lyssna.win import capture, clipboard, insert

OWN = os.getpid()
OTHER = OWN + 1
CTRL_V = [(0x11, True), (0x56, True), (0x56, False), (0x11, False)]


# -- UI Automation fakes ----------------------------------------------------------------------

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


class El:
    """A UI Automation element: a control type, optional Value and Text patterns, a parent."""

    def __init__(self, control_type, value=None, read_only=False, text=False, pid=OTHER, parent=None):
        self.CurrentControlType, self.CurrentProcessId, self.parent = control_type, pid, parent
        self.patterns = {}
        if value is not None:
            self.patterns[UIA.UIA_ValuePatternId] = Pattern(CurrentValue=value, CurrentIsReadOnly=read_only)
        if text:
            self.patterns[UIA.UIA_TextPatternId] = Pattern()

    def GetCurrentPattern(self, pattern_id):
        return self.patterns.get(pattern_id)


class Walker:
    def GetParentElement(self, element):
        return element.parent


class Automation:
    def __init__(self, focused=None):
        self.focused = focused
        self.root = El(50033)  # the desktop, a pane
        self.ControlViewWalker = Walker()

    def GetFocusedElement(self):
        return self.focused

    def GetRootElement(self):
        return self.root

    def CompareElements(self, a, b):
        return a is b


class InlineCom:
    """Stands in for the UI Automation thread: runs each request at once and counts them."""

    def __init__(self):
        self.calls = 0
        self.timeouts = []

    def call(self, fn, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        return fn()


@pytest.fixture
def uia(monkeypatch):
    """Notepad-like app in front (another process, not a console), UI Automation answering inline."""
    state = {"pid": OTHER, "class": "Notepad", "hwnd": 100}
    automation = Automation()
    com = InlineCom()
    monkeypatch.setattr(capture, "_foreground_window", lambda: state["hwnd"])
    monkeypatch.setattr(capture, "_window_class", lambda hwnd: state["class"])
    monkeypatch.setattr(capture, "_window_pid", lambda hwnd: state["pid"] if hwnd else None)
    monkeypatch.setattr(capture, "_com", com)
    monkeypatch.setitem(capture._uia, "UIA", UIA)
    monkeypatch.setitem(capture._uia, "automation", automation)
    state["automation"], state["com"] = automation, com
    return state


B, CB, ED, LI, LS, TX, DOC, CUSTOM, PANE, GROUP, HYPERLINK = (insert.BUTTON, insert.COMBO_BOX, insert.EDIT, insert.LIST_ITEM,
                                                              insert.LIST, insert.TEXT, insert.DOCUMENT, 50025, 50033, 50026,
                                                              insert.HYPERLINK)


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (lambda: El(ED, value="abc"), "text"),                                   # a text box
        (lambda: El(ED, value=""), "text"),                                      # an empty one is still a field
        (lambda: El(DOC, text=True), "text"),                                    # Word, a rich editor
        (lambda: El(CB, text=True), "text"),                                     # a combo box with an edit part
        (lambda: El(50029, value="12"), "text"),                                 # a spreadsheet cell with a writable value
        (lambda: El(CUSTOM, parent=El(DOC, text=True)), "text"),                 # the caret inside an editor
        (lambda: El(CUSTOM, parent=El(GROUP, parent=El(ED, value="x"))), "text"),  # two levels up
        (lambda: El(CUSTOM, parent=El(GROUP, parent=El(GROUP, parent=El(ED, value="x")))), "maybe"),  # too far up
        (lambda: El(DOC, value="https://example.com", read_only=True, text=True), "maybe"),  # a web page: its address
        (lambda: El(ED, value="locked", read_only=True), "maybe"),
        (lambda: El(PANE, text=True), "maybe"),                                  # text, but not a place to type
        (lambda: El(PANE), "maybe"),                                             # a custom-drawn app
        (lambda: El(CUSTOM), "maybe"),
        (lambda: El(B), "none"),
        (lambda: El(B, parent=El(DOC, text=True)), "none"),                      # a button on a web page
        (lambda: El(LS), "none"),                                                # the desktop's icons, Explorer
        (lambda: El(LI), "none"),
        (lambda: El(HYPERLINK), "none"),
        (lambda: El(TX, text=True), "none"),                                     # a label
        (lambda: None, "maybe"),                                                 # no focused element
    ],
)
def test_text_target(uia, make, expected):
    uia["automation"].focused = make()
    assert insert.text_target() == expected
    assert uia["com"].timeouts == [insert.TARGET_TIMEOUT]


def test_the_desktop_itself_is_not_a_text_field(uia):
    uia["automation"].focused = uia["automation"].root
    assert insert.text_target() == "none"


def test_our_own_window_is_never_inspected(uia):
    uia["pid"] = OWN
    uia["automation"].focused = El(B)
    assert insert.text_target() == "maybe"
    assert uia["com"].calls == 0


def test_an_unknown_window_owner_is_never_inspected(uia):
    uia["pid"] = None
    assert insert.text_target() == "maybe"
    assert uia["com"].calls == 0


def test_focus_in_our_own_process_is_not_looked_into(uia):
    element = El(B, pid=OWN)
    element.GetCurrentPattern = lambda pattern_id: pytest.fail("our own element must not be read")
    uia["automation"].focused = element
    assert insert.text_target() == "maybe"


@pytest.mark.parametrize("cls", sorted(capture.CONSOLE_CLASSES))
def test_a_console_is_pasted_into_without_asking(uia, cls):
    uia["class"] = cls
    uia["automation"].focused = El(B)
    assert insert.text_target() == "maybe"
    assert uia["com"].calls == 0


def test_a_ui_automation_error_means_maybe(uia):
    def broken():
        raise OSError("COM error")

    uia["automation"].GetFocusedElement = broken
    assert insert.text_target() == "maybe"


def test_a_hung_app_means_maybe_after_the_short_timeout(uia, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(capture, "_com", capture._ComThread(init=lambda: None))
    monkeypatch.setattr(insert, "TARGET_TIMEOUT", 0.1)
    uia["automation"].GetFocusedElement = lambda: (release.wait(2), El(B))[1]
    start = time.monotonic()
    try:
        assert insert.text_target() == "maybe"
        assert time.monotonic() - start < 1.0  # not the thread's own 2.5 s limit
    finally:
        release.set()


# -- the clipboard and the paste key ----------------------------------------------------------

class FakeClipboard:
    """Stands in for lyssna.win.clipboard. A write moves the sequence number once per format;
    `late` lists the moves another program makes on the next reads after a write."""

    def __init__(self, log):
        self.log = log
        self.seq = 10
        self.formats = [(13, None, b"what was there"), (0xC100, "HTML Format", b"<b>x</b>")]
        self.late: list[int] = []
        self.pending: list[int] = []
        self.writes = []
        self.restored = []
        self.fail = False

    def sequence(self):
        if self.pending:
            self.seq += self.pending.pop(0)
        return self.seq

    def set_text(self, text, transient=False):
        self.log.append(("write", transient))
        if self.fail:
            return False
        self.writes.append((text, transient))
        self.seq += 2 + (len(clipboard.TRANSIENT_FORMATS) if transient else 0)
        self.pending = list(self.late)
        return True

    def snapshot(self):
        self.log.append("snapshot")
        return list(self.formats)

    def restore(self, items):
        self.log.append("restore")
        self.restored.append(items)
        self.seq += 1 + len(items)
        return True


@pytest.fixture
def machine(monkeypatch, uia):
    """The Windows twin with the clipboard and SendInput faked, a text field focused, the
    user still holding Ctrl+Win for the first two key-state reads, and the delayed restore
    collected instead of run."""
    log = []
    board = FakeClipboard(log)
    for name in ("sequence", "set_text", "snapshot", "restore"):
        monkeypatch.setattr(clipboard, name, getattr(board, name))
    held = {"reads": 0}

    def key_down(vk):
        held["reads"] += 1
        return held["reads"] <= 2 and vk in (capture.VK_CONTROL, capture.VK_LWIN)

    def send(events):
        assert not any(key_down(vk) for vk in (capture.VK_CONTROL, capture.VK_LWIN)), "keys sent while held"
        log.append(("keys", events))

    monkeypatch.setattr(capture, "_key_down", key_down)
    monkeypatch.setattr(capture, "_send_inputs", send)
    monkeypatch.setattr(capture, "_SETTLE", 0.0)
    capture._own_counts.clear()
    uia["automation"].focused = El(ED, value="")
    later = []
    inserter = Inserter(system=insert, later=lambda delay, fn: later.append(fn))
    yield inserter, board, log, later
    capture._own_counts.clear()


def test_paste_writes_transient_text_presses_ctrl_v_and_restores(machine):
    inserter, board, log, later = machine
    assert inserter.paste("Hello there.") == "pasted"
    assert log == ["snapshot", ("write", True), ("keys", CTRL_V)]  # Ctrl+V only once Ctrl+Win are up
    assert board.writes == [("Hello there.", True)]  # exactly the text, kept out of the clipboard history
    [restore] = later
    assert restore() is True
    assert board.restored == [[(13, None, b"what was there"), (0xC100, "HTML Format", b"<b>x</b>")]]
    assert capture.own_change(board.seq)  # the restore is noted as ours


def test_our_write_is_noted_as_ours(machine):
    inserter, board, _, _ = machine
    inserter.paste("dictated")
    assert all(capture.own_change(n) for n in range(11, board.seq + 1))
    assert not capture.own_change(10)


def test_the_token_waits_for_the_sequence_number_to_settle(machine):
    _, board, _, _ = machine
    board.late = [1, 1]  # another program adds two formats just after our write
    token = insert.put_text("dictated", transient=True)
    assert token == board.seq == 10 + 2 + len(clipboard.TRANSIENT_FORMATS) + 2
    assert capture.own_change(token)
    assert insert.restore_unless_changed(board.formats, token) is True  # not mistaken for a user copy


def test_no_restore_when_the_user_copied_something_since(machine):
    inserter, board, _, later = machine
    inserter.paste("dictated")
    board.seq += 1  # a copy after our paste
    [restore] = later
    assert restore() is False
    assert board.restored == []


def test_no_text_field_leaves_a_normal_clipboard_entry_and_no_paste(machine, uia):
    inserter, board, log, later = machine
    uia["automation"].focused = El(LS)
    assert inserter.paste("Copy me") == "copied"
    assert board.writes == [("Copy me", False)]  # a normal entry: in the history, the user's to keep
    assert not any(isinstance(e, tuple) and e[0] == "keys" for e in log)
    assert later == []


def test_a_failed_write_never_pastes_the_old_clipboard(machine):
    inserter, board, log, later = machine
    board.fail = True
    with pytest.raises(OSError):
        inserter.paste("dictated")
    assert not any(isinstance(e, tuple) and e[0] == "keys" for e in log) and later == []


def test_an_unsaved_clipboard_is_not_restored(machine, monkeypatch):
    inserter, board, _, later = machine
    monkeypatch.setattr(clipboard, "snapshot", lambda: None)  # another program holds the clipboard
    assert inserter.paste("dictated") == "pasted"
    [restore] = later
    assert restore() is False and board.restored == []


def test_paste_waits_for_held_modifiers_and_sends_ctrl_v(machine):
    _, _, log, _ = machine
    insert.paste()
    assert log == [("keys", CTRL_V)]
