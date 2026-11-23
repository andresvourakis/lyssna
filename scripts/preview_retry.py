"""Screenshots for reviewing "retry a failed dictation": the History window with two dictations
and one failed one (its Retry pill and More button, and the pill while a retry runs), the
failure toast with its Retry pill, and the menu with Paste Last Dictation. Each in light and
dark appearance.

Everything runs against a temporary state folder: the real config, history and Keychain are
never touched, nothing is recorded, transcribed or pasted, and nothing goes online.

    uv run python scripts/preview_retry.py <folder for the PNGs>

Windows are captured with `screencapture -l <window id>` (the terminal needs Screen Recording
access); if that fails the History window falls back to its own offscreen snapshot."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import AppKit
import numpy as np
from Foundation import NSDate, NSRunLoop, NSTimer


def isolate(home: Path) -> None:
    """Point every path Lyssna writes at a temporary folder, and keep the Keychain in memory."""
    from lyssna import config, keychain, migrate, usage

    config.CONFIG_DIR = home / "config"
    config.CONFIG_PATH = home / "config" / "config.toml"
    config.STATE_DIR = home / "state"
    config.CACHE_DIR = home / "cache"
    config.LOG_DIR = home / "logs"
    config.SOCKET_PATH = home / "state" / "lyssna.sock"
    usage.USAGE_PATH = home / "state" / "usage.json"
    # The old Speechy locations the one-time migration copies (and moves) from.
    migrate.OLD_CONFIG_PATH = home / "old" / "config.toml"
    migrate.OLD_STATE_DIR = home / "old" / "state"
    migrate.OLD_CACHE_DIR = home / "old" / "cache"

    class MemoryKeychain:
        def get(self, account):
            return -25300, None

        def set(self, account, value):
            return 0

        def delete(self, account):
            return -25300

    keychain._backend = MemoryKeychain()
    keychain._spawn = lambda fn: fn()


def pump(seconds: float) -> None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def set_appearance(dark: bool) -> None:
    name = AppKit.NSAppearanceNameDarkAqua if dark else AppKit.NSAppearanceNameAqua
    AppKit.NSApp.setAppearance_(AppKit.NSAppearance.appearanceNamed_(name))


def capture(window, path: Path) -> bool:
    """The window as it is on screen, without its shadow."""
    result = subprocess.run(["screencapture", "-x", "-o", "-l", str(window.windowNumber()), str(path)],
                            capture_output=True, check=False)
    return result.returncode == 0 and path.is_file() and path.stat().st_size > 0


def snapshot(view, path: Path) -> None:
    """The view's own offscreen drawing (no window chrome), for when screencapture cannot run."""
    rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    data.writeToFile_atomically_(str(path), True)


def fill_history(store) -> None:
    from lyssna.dictation.types import DictationResult

    now = datetime.now().astimezone()
    for minutes, text in ((95, "Send the draft to Mattias before lunch, and ask him about the Uppsala trip."),
                          (40, "Remind me to book the room for Thursday's review.")):
        store._now = lambda t=now - timedelta(minutes=minutes): t
        store.add(DictationResult(text=text, raw=text.lower(), source="model", transcriber="parakeet",
                                  cleaner="s1-mini", audio_seconds=6.0, seconds={}, words=len(text.split())))
    store._now = lambda: now
    tone = (0.2 * np.sin(np.arange(12 * 16000) / 7)).astype(np.float32)  # a test tone, not a voice
    store.add_failed(tone, 12.0, "openai")


class NoRetry:
    """Stands in for the controller: a retry is never run, it only shows its pill."""

    def retry_dictation(self, entry_id, paste=True, on_done=None):
        return True


def shoot_history(out: Path) -> list[Path]:
    from lyssna.dictation import history
    from lyssna.ui.history_window import HistoryWindow

    store = history.HistoryStore(days=lambda: 30)
    fill_history(store)
    win = HistoryWindow.alloc().init()
    win.store = store
    win.ctl = NoRetry()
    win.show()
    pump(0.6)
    shots = []
    for dark in (False, True):
        set_appearance(dark)
        win._refresh(force=True)
        pump(0.5)
        path = out / f"history-{'dark' if dark else 'light'}.png"
        if not capture(win.window, path):
            snapshot(win.content, path)
        shots.append(path)
    # The failed row while its retry runs.
    failed_id = next(e.id for e in store.entries() if e.failed)
    win._retrying.add(failed_id)
    for dark in (False, True):
        set_appearance(dark)
        win._refresh(force=True)
        pump(0.5)
        path = out / f"history-retrying-{'dark' if dark else 'light'}.png"
        if not capture(win.window, path):
            snapshot(win.content, path)
        shots.append(path)
    win.window.close()
    return shots


def shoot_toast(out: Path) -> list[Path]:
    from lyssna.controller import RETRY, RETRY_SECONDS, Notice
    from lyssna.dictation.session import RETRY_FAILED, TRANSCRIPTION_FAILED
    from lyssna.ui.toast import Toast

    toast = Toast.alloc().init()
    shots = []
    for name, text in (("toast", TRANSCRIPTION_FAILED), ("toast-provider", "OpenAI did not answer. Try again."),
                       ("toast-retry-failed", RETRY_FAILED)):
        for dark in (False, True):
            set_appearance(dark)
            toast.present(Notice(text, RETRY, lambda: None, seconds=RETRY_SECONDS))
            pump(0.6)
            path = out / f"{name}-{'dark' if dark else 'light'}.png"
            if capture(toast.panel, path):
                shots.append(path)
            else:
                print(f"could not capture the toast ({path.name}): screencapture failed")
            toast.dismiss()
            pump(0.4)
    return shots


def shoot_menu(out: Path) -> list[Path]:
    """Pop the real menu up under the mouse and capture its window from a timer that runs
    inside the menu's tracking loop, then close it."""
    import Quartz

    from lyssna import config
    from lyssna.app import MenuBar
    from lyssna.controller import Controller

    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE)
    menubar = MenuBar(Controller())
    nsmenu = menubar._menu._menu
    menubar._refresh_checks()
    menubar._apply_visibility()
    shots = []
    pid = AppKit.NSProcessInfo.processInfo().processIdentifier()

    for dark in (False, True):
        set_appearance(dark)
        path = out / f"menu-{'dark' if dark else 'light'}.png"

        def grab(timer, path=path):
            windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
            ours = [w for w in windows if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 0) > 0
                    and w.get("kCGWindowBounds", {}).get("Height", 0) > 200]
            if ours:
                subprocess.run(["screencapture", "-x", "-o", "-l", str(ours[0]["kCGWindowNumber"]), str(path)],
                               capture_output=True, check=False)
            nsmenu.cancelTracking()

        timer = NSTimer.timerWithTimeInterval_repeats_block_(0.8, False, grab)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, AppKit.NSEventTrackingRunLoopMode)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, AppKit.NSDefaultRunLoopMode)
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        at = (screen.origin.x + 80, screen.origin.y + screen.size.height - 20)
        nsmenu.popUpMenuPositioningItem_atLocation_inView_(None, at, None)
        pump(0.3)
        if path.is_file() and path.stat().st_size > 0:
            shots.append(path)
        else:
            print(f"could not capture the menu ({path.name})")
    return shots


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: preview_retry.py <folder for the PNGs>")
        return 2
    out = Path(sys.argv[1]).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="lyssna-preview-"))
    isolate(home)

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    app.finishLaunching()
    app.activateIgnoringOtherApps_(True)

    try:
        shots = shoot_history(out) + shoot_toast(out) + shoot_menu(out)
    finally:
        set_appearance(False)
        shutil.rmtree(home, ignore_errors=True)
    for path in shots:
        print(path)
    print(f"{len(shots)} screenshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
