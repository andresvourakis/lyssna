"""Screenshots for reviewing "retry a failed dictation": the History window with two dictations
and one failed one (its Retry pill and More button, and the pill while a retry runs), the
failure toast with its Retry pill, and the menu with Paste Last Dictation. Each in light and
dark appearance.

Every shot is the whole display, with each element where the app puts it: the History window
opened as its menu item opens it, the toast presented as after a failed dictation (above the
dictation pill, which then goes back to its resting sliver), and the menu dropped down from
Lyssna's own menu bar icon. One Lyssna element per shot.

Everything runs against a temporary state folder: the real config, history and Keychain are
never touched, nothing is recorded, transcribed or pasted, and nothing goes online.

    uv run python scripts/preview_retry.py <folder for the PNGs>

The shots are taken with `screencapture` (the terminal needs Screen Recording access)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import AppKit
import numpy as np
import Quartz
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


def display_of(screen) -> int:
    """screencapture's -D number for `screen` (1 is the main display)."""
    number = screen.deviceDescription()["NSScreenNumber"] if screen is not None else None
    _err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    ids = list(ids[:count]) if ids else []
    return ids.index(number) + 1 if number in ids else 1


def capture(screen, path: Path) -> bool:
    """The whole display `screen` is on, as it is now."""
    result = subprocess.run(["screencapture", "-x", "-D", str(display_of(screen)), str(path)],
                            capture_output=True, check=False)
    ok = result.returncode == 0 and path.is_file() and path.stat().st_size > 0
    if not ok:
        print(f"could not capture {path.name}: {result.stderr.decode().strip() or 'screencapture failed'}")
    return ok


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
    """Opened as LyssnaApp.show_history opens it: HistoryWindow.show, centred on the screen
    under the mouse, the app brought to the front."""
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
    failed_id = next(e.id for e in store.entries() if e.failed)
    for name, retrying in (("history", False), ("history-retrying", True)):
        if retrying:
            win._retrying.add(failed_id)  # the failed row while its retry runs
        for dark in (False, True):
            set_appearance(dark)
            win._refresh(force=True)
            pump(0.5)
            path = out / f"{name}-{'dark' if dark else 'light'}.png"
            if capture(win.window.screen(), path):
                shots.append(path)
    win.window.close()
    pump(0.4)
    return shots


def make_pill(ctl):
    """The dictation pill, set up from the config as LyssnaApp sets it up."""
    from lyssna.ui.mini_player import MiniPlayer

    ui_cfg = ctl.cfg.get("ui", {})
    if not ui_cfg.get("mini_player", True):
        return None
    pill = MiniPlayer.alloc().init()
    pill.layout = ctl.player_layout()
    pill.dictation_layout = ctl.dictation_layout()
    pill.idle_pill = bool(ui_cfg.get("idle_pill", True))
    pill.auto_hide = bool(ui_cfg.get("auto_hide", True))
    pill.rest_wave = bool(ui_cfg.get("wave", True))
    return pill


def shoot_toast(out: Path, ctl) -> list[Path]:
    """As after a failed dictation (or retry): the pill is in its dictate form ("working")
    when the session turns to "notice". The controller's toast is queued first, so the toast
    is presented while the pill still shows the dictate capsule (LyssnaApp._present_toast:
    its screen and toast_floor), then the pill leaves dictation for its resting sliver."""
    from lyssna.controller import RETRY, RETRY_SECONDS, Notice
    from lyssna.dictation.session import RETRY_FAILED, TRANSCRIPTION_FAILED
    from lyssna.ui.toast import Toast

    toast = Toast.alloc().init()
    pill = make_pill(ctl)
    shots = []
    for name, text in (("toast", TRANSCRIPTION_FAILED), ("toast-provider", "OpenAI did not answer. Try again."),
                       ("toast-retry-failed", RETRY_FAILED)):
        for dark in (False, True):
            set_appearance(dark)
            if pill is not None:
                pill._set_dictation("working")  # the wave only animates: nothing is recorded
                pump(0.4)
            screen, floor = None, None
            if pill is not None and pill.visible():
                screen = pill.panel.screen()
                floor = pill.toast_floor()
            toast.present(Notice(text, RETRY, lambda: None, seconds=RETRY_SECONDS), screen, floor)
            if pill is not None:
                pill._set_dictation("notice")
            pump(0.6)
            path = out / f"{name}-{'dark' if dark else 'light'}.png"
            if capture(toast.panel.screen(), path):
                shots.append(path)
            toast.dismiss()
            if pill is not None:
                pill._stop_auto_watch()
                pill._stop_wave_timer()
                pill.hide()
                pill._set_dictation("idle")
            pump(0.5)
    return shots


def shoot_menu(out: Path, ctl) -> list[Path]:
    """Lyssna's status item, made as rumps makes it at launch, and its menu opened by clicking
    the menu bar icon. A timer inside the menu's tracking loop takes the shot, then closes it."""
    import rumps

    from lyssna.app import MenuBar

    menubar = MenuBar(ctl)
    menubar._refresh_checks()
    menubar._apply_visibility()
    nsapp = rumps.rumps.NSApp.alloc().init()
    nsapp._app = menubar.__dict__
    nsapp.initializeStatusBar()
    item = nsapp.nsstatusitem
    nsmenu = menubar._menu._menu
    pump(0.5)
    shots = []
    for dark in (False, True):
        set_appearance(dark)
        pump(0.3)
        path = out / f"menu-{'dark' if dark else 'light'}.png"

        def grab(timer, path=path):
            if capture(item.button().window().screen(), path):
                shots.append(path)
            nsmenu.cancelTracking()

        timer = NSTimer.timerWithTimeInterval_repeats_block_(0.8, False, grab)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, AppKit.NSEventTrackingRunLoopMode)
        item.button().performClick_(None)  # returns once the menu closes
        pump(0.3)
    AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(item)
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
        from lyssna import config
        from lyssna.controller import Controller

        config.ensure_dirs()
        # LYSSNA_PREVIEW_DOCK=left|right puts the resting pill on that screen edge, to see where the toast goes then.
        dock = os.environ.get("LYSSNA_PREVIEW_DOCK", "")
        config.CONFIG_PATH.write_text(config.TEMPLATE.replace('dictation_dock = ""', f'dictation_dock = "{dock}"'))
        ctl = Controller()
        shots = shoot_history(out) + shoot_toast(out, ctl) + shoot_menu(out, ctl)
    finally:
        set_appearance(False)
        shutil.rmtree(home, ignore_errors=True)
    for path in shots:
        print(path)
    print(f"{len(shots)} screenshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
