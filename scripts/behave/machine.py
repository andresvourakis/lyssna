"""The Mac around a run, and putting it back: the installed Lyssna quit before and started again
after, the sound muted and restored, the Mac kept awake, the cursor put back where it was, and no
key or mouse button left held, whatever way the run ends (an error, Ctrl+C, a kill signal).

It also tells when the run must not go on: the screen locked or asleep, or another app full
screen in front (the owner sometimes has the Shadow remote desktop full screen). The harness does
not fight either; it stops and says so."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path

log = logging.getLogger("behave.machine")

INSTALLED = Path("/Applications/Lyssna.app")
INSTALLED_BINARY = INSTALLED / "Contents" / "MacOS" / "Lyssna"
QUIT_WAIT = 10.0


class Blocked(RuntimeError):
    """The Mac is not in a state the run may drive (locked, asleep, another app full screen)."""


def _osascript(script: str, timeout: float = 10.0) -> str:
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return out.stdout.strip()


def installed_pids() -> list[int]:
    out = subprocess.run(["pgrep", "-f", str(INSTALLED_BINARY)], capture_output=True, text=True).stdout
    return [int(p) for p in out.split() if p.strip().isdigit()]


def output_muted() -> bool:
    return _osascript("output muted of (get volume settings)") == "true"


def set_output_muted(on: bool) -> None:
    _osascript("set volume with output muted" if on else "set volume without output muted")


def screen_state() -> str | None:
    """Why the screen cannot be driven now, or None when it can."""
    import Quartz

    session = Quartz.CGSessionCopyCurrentDictionary() or {}
    if session.get("CGSSessionScreenIsLocked"):
        return "the screen is locked"
    if not session.get("kCGSSessionOnConsoleKey", True):
        return "this login is not the one on the screen"
    if Quartz.CGDisplayIsAsleep(Quartz.CGMainDisplayID()):
        return "the display is asleep"
    foreign = foreign_full_screen()
    if foreign:
        return f"{foreign} is full screen in front"
    return None


def foreign_full_screen(ours: tuple[int, ...] = ()) -> str | None:
    """The name of an app with a normal window covering the whole main display, other than the
    Finder's desktop and our own processes."""
    import Quartz

    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID) or []
    for info in infos:
        if info.get(Quartz.kCGWindowLayer, 0) != 0 or info.get(Quartz.kCGWindowOwnerPID) in ours:
            continue
        b = info.get(Quartz.kCGWindowBounds) or {}
        owner = str(info.get(Quartz.kCGWindowOwnerName, ""))
        if owner in ("Finder", "Dock", "Window Server"):
            continue
        if (b.get("X", 1) <= bounds.origin.x and b.get("Y", 1) <= bounds.origin.y
                and b.get("Width", 0) >= bounds.size.width and b.get("Height", 0) >= bounds.size.height):
            return owner
    return None


def front_app() -> tuple[int, str]:
    """(pid, name) of the app in front."""
    from AppKit import NSWorkspace

    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None:
        return 0, ""
    return int(front.processIdentifier()), str(front.localizedName())


class Machine:
    """Use as a context manager around the whole run."""

    def __init__(self, scratch: Path):
        self.scratch = scratch
        self.was_running = False
        self.was_muted: bool | None = None
        self.caffeinate: subprocess.Popen | None = None
        self.cursor: tuple[float, float] | None = None
        self.notes: list[str] = []
        self.cleanups: list = []  # callables run first at the end, newest first (test apps, windows)
        self._old_term = None

    def note(self, text: str) -> None:
        log.info(text)
        self.notes.append(text)

    def __enter__(self) -> Machine:
        reason = screen_state()
        if reason:
            raise Blocked(reason)
        self._old_term = signal.signal(signal.SIGTERM, _raise_interrupt)
        try:
            self._prepare()
        except BaseException:
            self.restore()
            raise
        return self

    def _prepare(self) -> None:
        from . import drive

        self.cursor = drive.cursor()
        self.caffeinate = subprocess.Popen(["caffeinate", "-dimsu", "-w", str(os.getpid())])
        self.was_muted = output_muted()
        if not self.was_muted:
            set_output_muted(True)
        self.note(f"sound was {'muted' if self.was_muted else 'on'}; muted for the run")
        pids = installed_pids()
        self.was_running = bool(pids)
        if pids:
            self.note(f"the installed Lyssna was running (pid {', '.join(map(str, pids))}); quitting it")
            try:
                _osascript(f'tell application "{INSTALLED}" to quit', timeout=QUIT_WAIT)
            except subprocess.TimeoutExpired:
                self.note("the installed Lyssna did not answer the quit request in time")
            deadline = time.monotonic() + QUIT_WAIT
            while installed_pids() and time.monotonic() < deadline:
                time.sleep(0.2)
            for pid in installed_pids():
                self.note(f"the installed Lyssna (pid {pid}) was still running; sent it a terminate signal")
                os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + QUIT_WAIT
            while installed_pids() and time.monotonic() < deadline:
                time.sleep(0.2)
            if installed_pids():
                raise RuntimeError("the installed Lyssna would not quit; nothing was driven")
        else:
            self.note("the installed Lyssna was not running")

    def __exit__(self, kind, value, tb) -> None:
        self.restore()

    def restore(self) -> None:
        from . import drive

        for fn in reversed(self.cleanups):
            try:
                fn()
            except Exception:
                log.exception("a cleanup failed")
        self.cleanups.clear()
        try:
            drive.release_all()
        except Exception:
            log.exception("could not release the keys and the mouse button")
        if self.cursor is not None:
            try:
                drive.move(*self.cursor)
            except Exception:
                log.exception("could not put the cursor back")
        if self.was_muted is False:
            try:
                set_output_muted(False)
                self.note("sound restored (unmuted)")
            except Exception:
                log.exception("could not unmute the sound")
        if self.was_running and not installed_pids():
            subprocess.run(["open", str(INSTALLED)], check=False)
            self.note("started the installed Lyssna again")
            self.was_running = False
        if self.caffeinate is not None:
            self.caffeinate.terminate()
            self.caffeinate = None
        if self._old_term is not None:
            signal.signal(signal.SIGTERM, self._old_term)
            self._old_term = None


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")
