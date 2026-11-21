"""The pure parts of lyssna/win/clipboard.py, against a fake Win32 layer: which formats are
saved and which skipped, the markers that keep transient text out of the clipboard history,
the retry while another program holds the clipboard, and snapshot then restore."""

import struct

import pytest

from lyssna.win import clipboard


class FakeWin32:
    """A clipboard in memory with the same small surface as clipboard._Win32."""

    def __init__(self, contents=None, names=None, busy=0):
        self.contents: dict[int, bytes] = dict(contents or {})
        self.names: dict[int, str] = dict(names or {})
        self.busy = busy  # how many open() calls fail before one succeeds
        self.opened = self.closed = 0
        self.seq = 1
        self.owner = None
        self.registered: list[str] = []

    def last_error(self):
        return 5

    def sequence(self):
        return self.seq

    def make_owner(self):
        return 1234

    def destroy_owner(self, hwnd):
        self.owner = ("destroyed", hwnd)

    def open(self, hwnd=None):
        self.opened += 1
        if self.busy:
            self.busy -= 1
            return False
        return True

    def close(self):
        self.closed += 1

    def empty(self):
        self.contents.clear()
        self.seq += 1
        return True

    def formats(self):
        return list(self.contents)

    def format_name(self, fmt):
        return self.names.get(fmt)

    def register(self, name):
        self.registered.append(name)
        for fmt, known in self.names.items():
            if known == name:
                return fmt
        fmt = 0xC100 + len(self.names)
        self.names[fmt] = name
        return fmt

    def read(self, fmt):
        return self.contents.get(fmt)

    def write(self, fmt, data):
        self.contents[fmt] = bytes(data)
        self.seq += 1
        return True


@pytest.fixture
def fake(monkeypatch):
    api = FakeWin32()
    monkeypatch.setattr(clipboard, "_api", lambda: api)
    monkeypatch.setattr(clipboard.time, "sleep", lambda s: None)
    return api


@pytest.mark.parametrize("fmt", [2, 3, 9, 14, 0x80, 0x81, 0x82, 0x83, 0x8E, 0x200, 0x2FF, 0x300, 0x3FF])
def test_handle_formats_are_skipped(fmt):
    assert not clipboard.copyable(fmt)


@pytest.mark.parametrize("fmt", [1, 7, 8, 13, 15, 16, 17, 0xC000, 0xC123])
def test_memory_formats_are_saved(fmt):
    assert clipboard.copyable(fmt)


def test_transient_markers():
    markers = dict(clipboard.transient_formats())
    assert set(markers) == {"ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                            "CanUploadToCloudClipboard"}
    assert markers["CanIncludeInClipboardHistory"] == struct.pack("<I", 0)
    assert markers["CanUploadToCloudClipboard"] == struct.pack("<I", 0)


def test_set_text_transient_adds_markers(fake):
    assert clipboard.set_text("hello", transient=True)
    assert fake.contents[clipboard.CF_UNICODETEXT] == "hello".encode("utf-16-le") + b"\0\0"
    assert set(fake.registered) == {name for name, _ in clipboard.TRANSIENT_FORMATS}
    assert len(fake.contents) == 4
    assert fake.owner == ("destroyed", 1234)  # the owner window is cleaned up
    assert fake.opened == fake.closed == 1


def test_set_text_plain_has_no_markers(fake):
    assert clipboard.set_text("hello")
    assert list(fake.contents) == [clipboard.CF_UNICODETEXT]
    assert fake.registered == []


def test_get_text_round_trip(fake):
    clipboard.set_text("héllo wörld")
    assert clipboard.get_text() == "héllo wörld"


def test_get_text_stops_at_null(fake):
    fake.contents[clipboard.CF_UNICODETEXT] = "abc".encode("utf-16-le") + b"\0\0junk"
    assert clipboard.get_text() == "abc"


def test_open_retries_then_succeeds(fake):
    fake.busy = 3
    fake.contents[clipboard.CF_UNICODETEXT] = "hi".encode("utf-16-le") + b"\0\0"
    assert clipboard.get_text() == "hi"
    assert fake.opened == 4
    assert fake.closed == 1


def test_open_gives_up_quietly(fake, caplog):
    fake.busy = 100
    caplog.set_level("INFO", logger="lyssna.win.clipboard")
    assert clipboard.get_text() == ""
    assert fake.opened == clipboard.OPEN_TRIES
    assert fake.closed == 0  # never opened, so never closed
    assert clipboard.snapshot() is None
    assert not clipboard.set_text("x")
    assert "busy" in caplog.text


def test_snapshot_skips_handles_and_keeps_registered_names(fake):
    html = 0xC0F0
    fake.contents = {13: b"t\0\0\0", 2: b"bitmap handle", 8: b"dib", html: b"<b>x</b>", 0xC0F1: b"unnamed"}
    fake.names = {html: "HTML Format"}
    items = clipboard.snapshot()
    assert items == [(13, None, b"t\0\0\0"), (8, None, b"dib"), (html, "HTML Format", b"<b>x</b>")]
    assert fake.closed == 1


def test_restore_puts_formats_back_and_registers_by_name(fake):
    fake.names = {0xC0F0: "HTML Format"}
    items = [(13, None, b"t\0\0\0"), (8, None, b"dib"), (0xC055, "HTML Format", b"<b>x</b>")]
    assert clipboard.restore(items)
    # The registered format gets this session's number, found by name, not the saved number.
    assert fake.contents == {13: b"t\0\0\0", 8: b"dib", 0xC0F0: b"<b>x</b>"}
    assert fake.registered == ["HTML Format"]


def test_restore_empty_snapshot_empties_the_clipboard(fake):
    fake.contents = {13: b"x\0\0\0"}
    assert clipboard.restore([])
    assert fake.contents == {}


def test_restore_none_does_nothing(fake):
    fake.contents = {13: b"x\0\0\0"}
    assert not clipboard.restore(None)
    assert fake.contents == {13: b"x\0\0\0"}
    assert fake.opened == 0


def test_clipboard_closed_even_when_a_read_fails(fake):
    def boom(fmt):
        raise OSError("read failed")

    fake.read = boom
    fake.contents = {13: b"x"}
    with pytest.raises(OSError):
        clipboard.snapshot()
    assert fake.opened == fake.closed == 1
