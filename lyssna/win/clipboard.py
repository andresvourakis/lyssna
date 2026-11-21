"""The Windows clipboard through ctypes: its sequence number, its text, and a full copy of it
that can be put back.

Why this exists: the Ctrl+C fallback in lyssna/win/capture.py overwrites the user's clipboard
to learn the selection, and dictation will paste through it, so both need to save every format
the clipboard holds and restore it afterwards, the way the Mac code saves every pasteboard type.

What can be saved: every format whose data is a plain memory block (text, HTML, rich text, DIB
images, file lists, registered formats). Formats that are GDI objects or other handles cannot
be copied as bytes and are skipped; Windows re-creates the common derived ones on its own (a
CF_BITMAP from a CF_DIB, for example). Registered formats get a new number on every boot, so
they are saved by name and registered again on restore.

Text Lyssna puts on the clipboard for a moment (the dictation paste) can be marked transient:
it then carries the formats that keep it out of the clipboard history (Win+V), out of cloud
clipboard sync, and away from clipboard monitors that honour the convention.

Clipboard writes need a window to own the clipboard: with no owner window, EmptyClipboard
leaves the owner unset and SetClipboardData fails. Each write opens a hidden window for the
duration of the call and destroys it afterwards.

Every Win32 call goes through one small object (`_api()`), created on first use, so this module
imports on any system and the tests swap in a fake. It uses its own WinDLL instances, so the
argument types declared here never clash with other modules that declare the same functions."""

from __future__ import annotations

import logging
import struct
import threading
import time
from collections.abc import Iterable

log = logging.getLogger("lyssna.win.clipboard")

CF_UNICODETEXT = 13

# Formats whose clipboard data is a handle that is not a plain memory block.
CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE, CF_OWNERDISPLAY = 2, 3, 9, 14, 0x80
_HANDLE_FORMATS = frozenset({CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE, CF_OWNERDISPLAY})
_DSP_RANGE = range(0x81, 0x8F)  # CF_DSPTEXT .. CF_DSPENHMETAFILE: private display formats
_PRIVATE_RANGE = range(0x200, 0x300)  # CF_PRIVATEFIRST .. CF_PRIVATELAST: freed by the owner, meaning unknown
_GDIOBJ_RANGE = range(0x300, 0x400)  # CF_GDIOBJFIRST .. CF_GDIOBJLAST: GDI objects
_REGISTERED_FIRST = 0xC000  # formats from here on are registered by name

# The registered formats that keep a clipboard entry out of history, sync and monitors.
_DWORD_ZERO = struct.pack("<I", 0)
TRANSIENT_FORMATS: tuple[tuple[str, bytes], ...] = (
    ("ExcludeClipboardContentFromMonitorProcessing", _DWORD_ZERO),  # any data; its presence is the signal
    ("CanIncludeInClipboardHistory", _DWORD_ZERO),  # DWORD 0: not in the Win+V history
    ("CanUploadToCloudClipboard", _DWORD_ZERO),  # DWORD 0: not synced to other devices
)

OPEN_TRIES = 10
OPEN_DELAY = 0.02  # seconds between tries while another program holds the clipboard

# One saved format: (format number, its registered name or None, its bytes).
Item = tuple[int, str | None, bytes]

_lock = threading.Lock()  # our own clipboard calls never overlap; other programs are handled by the retry


def copyable(fmt: int) -> bool:
    """Can this format be saved as bytes and put back? False for GDI and other handle formats."""
    return not (fmt in _HANDLE_FORMATS or fmt in _DSP_RANGE or fmt in _PRIVATE_RANGE or fmt in _GDIOBJ_RANGE)


def transient_formats() -> tuple[tuple[str, bytes], ...]:
    """The registered formats (name, data) set next to transient text."""
    return TRANSIENT_FORMATS


def _decode_text(data: bytes | None) -> str:
    """CF_UNICODETEXT bytes to a string, cut at the terminating null."""
    if not data:
        return ""
    return data.decode("utf-16-le", errors="replace").split("\0", 1)[0]


def _encode_text(text: str) -> bytes:
    return text.encode("utf-16-le") + b"\0\0"


class _Win32:
    """The real Win32 calls. Only created on Windows, on first use."""

    GMEM_MOVEABLE = 0x0002

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        HANDLE, HWND, UINT, BOOL = wintypes.HANDLE, wintypes.HWND, wintypes.UINT, wintypes.BOOL
        signatures = (
            (user32.OpenClipboard, [HWND], BOOL),
            (user32.CloseClipboard, [], BOOL),
            (user32.EmptyClipboard, [], BOOL),
            (user32.EnumClipboardFormats, [UINT], UINT),
            (user32.GetClipboardData, [UINT], HANDLE),
            (user32.SetClipboardData, [UINT, HANDLE], HANDLE),
            (user32.RegisterClipboardFormatW, [wintypes.LPCWSTR], UINT),
            (user32.GetClipboardFormatNameW, [UINT, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (user32.GetClipboardSequenceNumber, [], wintypes.DWORD),
            (user32.CreateWindowExW,
             [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
              ctypes.c_int, ctypes.c_int, HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID], HWND),
            (user32.DestroyWindow, [HWND], BOOL),
            (kernel32.GlobalAlloc, [UINT, ctypes.c_size_t], wintypes.HGLOBAL),
            (kernel32.GlobalLock, [wintypes.HGLOBAL], wintypes.LPVOID),
            (kernel32.GlobalUnlock, [wintypes.HGLOBAL], BOOL),
            (kernel32.GlobalSize, [wintypes.HGLOBAL], ctypes.c_size_t),
            (kernel32.GlobalFree, [wintypes.HGLOBAL], wintypes.HGLOBAL),
        )
        for fn, argtypes, restype in signatures:
            fn.argtypes, fn.restype = argtypes, restype
        self.user32, self.kernel32 = user32, kernel32

    def last_error(self) -> int:
        return self._ctypes.get_last_error()

    def sequence(self) -> int:
        return int(self.user32.GetClipboardSequenceNumber())

    def make_owner(self):
        """A hidden window to own the clipboard while we write to it (same thread only)."""
        return self.user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)

    def destroy_owner(self, hwnd) -> None:
        if hwnd:
            self.user32.DestroyWindow(hwnd)

    def open(self, hwnd=None) -> bool:
        return bool(self.user32.OpenClipboard(hwnd))

    def close(self) -> None:
        self.user32.CloseClipboard()

    def empty(self) -> bool:
        return bool(self.user32.EmptyClipboard())

    def formats(self) -> list[int]:
        out, fmt = [], 0
        while True:
            fmt = int(self.user32.EnumClipboardFormats(fmt))
            if not fmt:
                return out
            out.append(fmt)

    def format_name(self, fmt: int) -> str | None:
        buf = self._ctypes.create_unicode_buffer(256)
        n = self.user32.GetClipboardFormatNameW(fmt, buf, len(buf))
        return buf.value[:n] if n > 0 else None

    def register(self, name: str) -> int:
        return int(self.user32.RegisterClipboardFormatW(name))

    def read(self, fmt: int) -> bytes | None:
        """The bytes of one format's memory block, or None if it has none."""
        handle = self.user32.GetClipboardData(fmt)
        if not handle:
            return None
        size = int(self.kernel32.GlobalSize(handle))
        ptr = self.kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return self._ctypes.string_at(ptr, size)
        finally:
            self.kernel32.GlobalUnlock(handle)

    def write(self, fmt: int, data: bytes) -> bool:
        """Put `data` on the open clipboard as format `fmt`. On success the system owns the memory."""
        handle = self.kernel32.GlobalAlloc(self.GMEM_MOVEABLE, max(len(data), 1))
        if not handle:
            return False
        ptr = self.kernel32.GlobalLock(handle)
        if not ptr:
            self.kernel32.GlobalFree(handle)
            return False
        try:
            self._ctypes.memmove(ptr, data, len(data))
        finally:
            self.kernel32.GlobalUnlock(handle)
        if not self.user32.SetClipboardData(fmt, handle):
            self.kernel32.GlobalFree(handle)
            return False
        return True


_win32: _Win32 | None = None


def _api():
    global _win32
    if _win32 is None:
        _win32 = _Win32()
    return _win32


def _open(api, hwnd=None) -> bool:
    """Open the clipboard, retrying while another program holds it. False after the last try."""
    for attempt in range(OPEN_TRIES):
        if api.open(hwnd):
            return True
        if attempt < OPEN_TRIES - 1:
            time.sleep(OPEN_DELAY)
    log.info("clipboard busy after %d tries; giving up", OPEN_TRIES)
    return False


def sequence() -> int:
    """The clipboard sequence number: it goes up on every change. 0 if it cannot be read."""
    return _api().sequence()


def get_text() -> str:
    """The clipboard's text (CF_UNICODETEXT), or "" if there is none or it cannot be read."""
    api = _api()
    with _lock:
        if not _open(api):
            return ""
        try:
            return _decode_text(api.read(CF_UNICODETEXT))
        finally:
            api.close()


def _write(entries: Iterable[tuple[int | None, str | None, bytes]]) -> bool:
    """Empty the clipboard and put `entries` on it: (format, registered name or None, data).
    A named entry is registered first and its number used. False if the clipboard could not be
    opened or emptied; formats that fail to write are logged and skipped."""
    api = _api()
    with _lock:
        owner = api.make_owner()
        try:
            if not _open(api, owner):
                return False
            try:
                if not api.empty():
                    log.info("clipboard could not be emptied (error %d)", api.last_error())
                    return False
                failed = 0
                for fmt, name, data in entries:
                    if name:
                        fmt = api.register(name)
                    if not fmt or not api.write(fmt, data):
                        failed += 1
                if failed:
                    log.info("%d clipboard formats could not be written", failed)
                return True
            finally:
                api.close()
        finally:
            api.destroy_owner(owner)


def set_text(text: str, transient: bool = False) -> bool:
    """Replace the clipboard with `text`. Transient text also carries the formats that keep it
    out of the clipboard history, cloud sync and clipboard monitors. False if it failed."""
    entries: list[tuple[int | None, str | None, bytes]] = [(CF_UNICODETEXT, None, _encode_text(text))]
    if transient:
        entries += [(None, name, data) for name, data in transient_formats()]
    return _write(entries)


def snapshot() -> list[Item] | None:
    """Every format on the clipboard that can be saved as bytes, in the order the clipboard
    lists them. [] is an empty clipboard. None when the clipboard could not be opened, so the
    caller knows there is nothing safe to put back."""
    api = _api()
    with _lock:
        if not _open(api):
            return None
        try:
            items: list[Item] = []
            skipped = 0
            for fmt in api.formats():
                if not copyable(fmt):
                    skipped += 1
                    continue
                data = api.read(fmt)
                if data is None:
                    skipped += 1
                    continue
                name = api.format_name(fmt) if fmt >= _REGISTERED_FIRST else None
                if fmt >= _REGISTERED_FIRST and not name:
                    skipped += 1
                    continue
                items.append((fmt, name, data))
            log.debug("clipboard snapshot: %d formats saved, %d skipped", len(items), skipped)
            return items
        finally:
            api.close()


def restore(items: list[Item] | None) -> bool:
    """Put back what snapshot() saved. [] leaves the clipboard empty; None does nothing."""
    if items is None:
        return False
    return _write([(None if name else fmt, name, data) for fmt, name, data in items])
