"""One Lyssna per user on Windows, the app's identity in the taskbar, and how it starts a fresh
copy of itself. The twin of lyssna/mac/instance.py.

The single copy is kept by a named mutex the first copy holds while it runs.

Why a mutex and not only the control pipe: the pipe is opened after the interface is built, so
two copies started together (a double click, or the Start menu and the login entry at once)
would both build a tray icon before one of them lost the race for the pipe. Asking for the
mutex is the first thing the app does, before any window, and Windows answers at once.

The name follows the pipe name in lyssna/win/ipc.py, so it is per user: two accounts on one
PC each get their own Lyssna. The "Local\\" prefix keeps it inside the user's session."""

from __future__ import annotations

import logging
import subprocess
import sys

from .ipc import pipe_address

log = logging.getLogger("lyssna.win.instance")

ERROR_ALREADY_EXISTS = 183
APP_ID = "io.github.andresvourakis.lyssna"


def mutex_name() -> str:
    r"""Local\lyssna-<user>-<hash>, the same user part as the control pipe's name."""
    return "Local\\" + pipe_address().rsplit("\\", 1)[-1]


class SingleInstance:
    def __init__(self) -> None:
        self._handle = None

    def acquire(self) -> bool:
        """True when this is the only copy running (and it now holds the mutex)."""
        import ctypes

        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, False, mutex_name())
        error = ctypes.get_last_error()
        if not handle:
            log.error("could not create the single-instance mutex (error %d); starting anyway", error)
            return True
        if error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """Let go of the mutex, so a new copy (a relaunch) can start. Safe to call twice."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        _kernel32().CloseHandle(handle)


def set_app_id() -> None:
    """Group the tray icon and the windows under Lyssna in the taskbar, not under Python. Runs
    before any window."""
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # type: ignore[attr-defined]
    except Exception:
        log.exception("could not set the AppUserModelID")


def relaunch() -> None:
    """Start a fresh copy of Lyssna.exe, once this one has let go of the mutex and the pipe.
    From source there is no program to start again, so nothing starts."""
    if not getattr(sys, "frozen", False):
        return
    log.info("relaunching %s", sys.executable)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    subprocess.Popen([sys.executable], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
