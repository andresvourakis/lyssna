"""lyssna/win/capture.py with every Win32 and COM call faked, so it runs on any system: the order
of the three routes, the console guard, the own-process gate, the clipboard bookkeeping, the
save and restore around Ctrl+C, the modifier wait, and the key events sent for Ctrl+C."""

import os
import threading
import time

import pytest

from lyssna.selection import AXSelection
from lyssna.win import capture, clipboard, hotkeys

OWN = os.getpid()
OTHER = OWN + 1


class FakeClipboard:
    """Stands in for lyssna.win.clipboard: text plus a sequence number."""

    def __init__(self, text="old", seq=10):
        self.text, self.seq = text, seq
        self.saved = [(13, None, b"old")]
        self.restored = []
        self.snapshots = 0

    def sequence(self):
        return self.seq

    def get_text(self):
        return self.text

    def snapshot(self):
        self.snapshots += 1
        return self.saved

    def restore(self, items):
        self.restored.append(items)
        self.seq += 2  # emptying and one format


@pytest.fixture
def board(monkeypatch):
    fake = FakeClipboard()
    for name in ("sequence", "get_text", "snapshot", "restore"):
        monkeypatch.setattr(clipboard, name, getattr(fake, name))
    return fake


@pytest.fixture
def machine(monkeypatch, board):
    """A normal app (Notepad, another process) in front, no keys held, key events collected,
    and the delayed restore recorded instead of run."""
    state = {"class": "Notepad", "pid": OTHER, "hwnd": 100, "sent": [], "later": [], "copies": 0}
    monkeypatch.setattr(capture, "_foreground_window", lambda: state["hwnd"])
    monkeypatch.setattr(capture, "_window_class", lambda hwnd: state["class"])
    monkeypatch.setattr(capture, "_window_pid", lambda hwnd: state["pid"] if hwnd else None)
    monkeypatch.setattr(capture, "_key_down", lambda vk: False)
    monkeypatch.setattr(capture, "_later", lambda delay, fn: state["later"].append((delay, fn)))
    monkeypatch.setattr(capture, "_SETTLE", 0.0)

    def send(events):
        state["sent"].append(events)
        if state.get("copy_text") is not None and (capture.VK_C, True) in events:
            state["copies"] += 1
            board.text = state["copy_text"]
            board.seq += 3  # the app empties the clipboard and writes two formats

    monkeypatch.setattr(capture, "_send_inputs", send)
    capture._own_counts.clear()
    yield state
    capture._own_counts.clear()


# Route order and source strings


def test_accessibility_first(machine, monkeypatch):
    monkeypatch.setattr(capture, "_uia_selection", lambda: AXSelection(True, "from uia", ("uia", 0, 8)))
    machine["copy_text"] = "from copy"
    assert capture.selection_or_clipboard() == ("from uia", "selection (accessibility)")
    assert machine["sent"] == []


def test_copy_second(machine, monkeypatch):
    monkeypatch.setattr(capture, "_uia_selection", lambda: AXSelection(True, ""))
    machine["copy_text"] = "from copy"
    assert capture.selection_or_clipboard() == ("from copy", "selection (copy)")


def test_clipboard_last(machine, monkeypatch, board):
    monkeypatch.setattr(capture, "_uia_selection", lambda: AXSelection(False, ""))
    monkeypatch.setattr(capture, "selected_text", lambda: None)
    board.text = "on the clipboard"
    assert capture.selection_or_clipboard() == ("on the clipboard", "clipboard")


def test_ax_selected_text_none_when_empty(machine, monkeypatch):
    monkeypatch.setattr(capture, "_uia_selection", lambda: AXSelection(True, ""))
    assert capture.ax_selected_text() is None


# Console guard


@pytest.mark.parametrize("cls", sorted(capture.CONSOLE_CLASSES))
def test_console_guard_blocks_ctrl_c(machine, board, cls):
    machine["class"] = cls
    machine["copy_text"] = "would interrupt"
    assert capture.selected_text() is None
    assert machine["sent"] == []
    assert board.snapshots == 0


def test_console_guard_lists_every_terminal():
    assert {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow", "mintty", "VirtualConsoleClass",
            "org.wezfurlong.wezterm", "Alacritty"} <= capture.CONSOLE_CLASSES


def test_no_foreground_window_sends_nothing(machine):
    machine["hwnd"] = None
    machine["copy_text"] = "x"
    assert capture.selected_text() is None
    assert machine["sent"] == []


def test_console_still_gets_accessibility_then_clipboard(machine, monkeypatch, board):
    machine["class"] = "CASCADIA_HOSTING_WINDOW_CLASS"
    monkeypatch.setattr(capture, "_uia_selection", lambda: AXSelection(True, ""))
    board.text = "copied on select"
    assert capture.selection_or_clipboard() == ("copied on select", "clipboard")
    assert machine["sent"] == []


# Own-process gate


def test_own_window_in_front_is_not_supported(machine, monkeypatch):
    machine["pid"] = OWN

    def forbidden():
        raise AssertionError("UI Automation must not be asked about our own windows")

    monkeypatch.setattr(capture, "_uia_selection", forbidden)
    assert capture.ax_selection() == AXSelection(False, "")


def test_unknown_owner_is_not_supported(machine, monkeypatch):
    machine["pid"] = None
    monkeypatch.setattr(capture, "_uia_selection", lambda: AXSelection(True, "x"))
    assert capture.ax_selection().supported is False


def test_other_app_is_read(machine, monkeypatch):
    sel = AXSelection(True, "hello world", ("uia", 3, 14), (10.0, 20.0, 100.0, 16.0))
    monkeypatch.setattr(capture, "_uia_selection", lambda: sel)
    assert capture.ax_selection() == sel


def test_errors_mean_not_supported(machine, monkeypatch):
    def boom():
        raise OSError("COM error")

    monkeypatch.setattr(capture, "_own_window_in_front", boom)
    assert capture.ax_selection() == AXSelection(False, "")


# Clipboard bookkeeping


def test_copied_since(machine, board):
    count = capture.clipboard_change_count()
    assert count == 10
    assert capture.copied_since(count) == ""  # unchanged
    board.text, board.seq = "user copy", 12
    assert capture.copied_since(count) == "user copy"
    capture.note_own_changes(12, 15)
    board.seq = 15
    assert capture.own_change(13) and capture.own_change(15) and not capture.own_change(12)
    assert capture.copied_since(count) == ""  # ours
    assert capture.copied_since(None) == ""


def test_change_count_zero_is_unknown(machine, board):
    board.seq = 0
    assert capture.clipboard_change_count() is None


def test_copy_route_marks_its_changes_as_own(machine, board):
    machine["copy_text"] = "selected"
    assert capture.selected_text() == "selected"
    for seq in (11, 12, 13):
        assert capture.own_change(seq)
    assert capture.copied_since(10) == ""


# Save and restore around Ctrl+C


def test_restore_when_unchanged(machine, board):
    machine["copy_text"] = "selected"
    assert capture.selected_text() == "selected"
    [(delay, fn)] = machine["later"]
    assert delay == pytest.approx(0.4)
    assert fn() is True
    assert board.restored == [[(13, None, b"old")]]
    assert capture.own_change(board.seq)  # the restore is ours too


def test_no_restore_when_clipboard_changed_again(machine, board):
    machine["copy_text"] = "selected"
    capture.selected_text()
    board.seq += 1  # the user copied something else in the meantime
    [(_, fn)] = machine["later"]
    assert fn() is False
    assert board.restored == []


def test_no_snapshot_or_restore_when_not_asked(machine, board):
    machine["copy_text"] = "selected"
    assert capture.selected_text(restore_clipboard=False) == "selected"
    assert board.snapshots == 0
    assert machine["later"] == []


def test_nothing_copied(machine, board):
    machine["copy_text"] = None  # the app ignores Ctrl+C
    assert capture.selected_text(timeout=0.05) is None
    assert machine["later"] == []  # nothing changed, so nothing to put back
    assert board.restored == []


def test_whitespace_copy_is_none_but_still_restored(machine, board):
    machine["copy_text"] = "  \n"
    assert capture.selected_text() is None
    assert len(machine["later"]) == 1


def test_busy_clipboard_means_no_ctrl_c(machine, board, monkeypatch):
    monkeypatch.setattr(clipboard, "snapshot", lambda: None)
    machine["copy_text"] = "selected"
    assert capture.selected_text() is None
    assert machine["sent"] == []


def test_restore_pasteboard_notes_own_changes(machine, board):
    capture.restore_pasteboard(object(), [(13, None, b"x")])
    assert board.restored == [[(13, None, b"x")]]
    assert capture.own_change(11) and capture.own_change(12)
    capture.restore_pasteboard(None, None)
    assert len(board.restored) == 1


def test_snapshot_pasteboard_ignores_pb(machine, board):
    assert capture.snapshot_pasteboard("anything") == [(13, None, b"old")]


# Keys


def test_modifier_wait_times_out(monkeypatch):
    monkeypatch.setattr(capture, "_key_down", lambda vk: vk == capture.VK_CONTROL)
    start = time.monotonic()
    capture._wait_for_modifiers_release(timeout=0.1)
    assert 0.09 <= time.monotonic() - start < 1.0


def test_modifier_wait_returns_on_release(monkeypatch):
    monkeypatch.setattr(capture, "_key_down", lambda vk: False)
    start = time.monotonic()
    capture._wait_for_modifiers_release(timeout=5)
    assert time.monotonic() - start < 0.5


def test_send_key_ctrl_c(machine):
    capture.send_key(capture.VK_C, hotkeys.CTRL)
    assert machine["sent"] == [[(0x11, True), (0x43, True), (0x43, False), (0x11, False)]]


def test_send_key_modifiers_nest(machine):
    capture.send_key(0x56, hotkeys.CTRL | hotkeys.SHIFT)
    assert machine["sent"] == [[(0x11, True), (0x10, True), (0x56, True), (0x56, False), (0x10, False), (0x11, False)]]


def test_send_key_plain(machine):
    capture.send_key(0x0D)
    assert machine["sent"] == [[(0x0D, True), (0x0D, False)]]


# Pure UI Automation helpers and the COM thread


def test_rects_and_union():
    rects = capture._rects([10, 20, 100, 16, 10, 36, 50, 16, 0, 0, 0, 0])
    assert rects == [(10.0, 20.0, 100.0, 16.0), (10.0, 36.0, 50.0, 16.0)]
    assert capture._union(rects) == (10.0, 20.0, 100.0, 32.0)
    assert capture._union([]) is None
    assert capture._rects(None) == []


def test_com_thread_runs_jobs_and_passes_errors():
    calls = []
    com = capture._ComThread(init=lambda: calls.append(threading.current_thread().name), timeout=1)
    assert com.call(lambda: 42) == 42
    with pytest.raises(ValueError):
        com.call(lambda: (_ for _ in ()).throw(ValueError("x")))
    assert calls == ["lyssna-uia"]  # initialised once, on its own thread


def test_com_thread_times_out_and_refuses_while_stuck():
    release = threading.Event()
    com = capture._ComThread(init=lambda: None, timeout=0.4)
    with pytest.raises(TimeoutError):
        com.call(release.wait)
    time.sleep(0.05)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        com.call(lambda: 1)
    assert time.monotonic() - start < 0.2  # refused at once, not after waiting out the timeout
    release.set()
    deadline = time.monotonic() + 3.0  # the worker frees itself once the stuck call returns
    while com._busy_since is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert com.call(lambda: 2) == 2


def test_com_init_failure_is_not_supported(machine, monkeypatch):
    def fail():
        raise OSError("no COM")

    monkeypatch.setattr(capture, "_com", capture._ComThread(init=fail, timeout=1))
    assert capture._uia_selection() == AXSelection(False, "")


def test_dictation_helpers_not_built():
    for fn, args in ((capture.focused_element, ()), (capture.ax_role, (None,)), (capture.ax_set_timeout, (None, 1.0))):
        with pytest.raises(NotImplementedError, match="dictation"):
            fn(*args)


def test_wake_is_a_no_op():
    assert capture.wake_accessibility(OTHER) is None
