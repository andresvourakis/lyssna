"""One Lyssna per user on the Mac, and how the app starts a fresh copy of itself. The twin of
lyssna/win/instance.py.

Launch Services keeps the bundled app to one copy. From the command line nothing does, so a
copy counts as running when it answers on the control socket: a second one would take the
socket and the hotkeys from it."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from . import ipc

log = logging.getLogger("lyssna.mac.instance")


class SingleInstance:
    def acquire(self) -> bool:
        """True when no other Lyssna answers on the control socket."""
        return ipc.send({"cmd": "status"}, timeout=1.0) is None

    def release(self) -> None:
        """Nothing to let go of: the control socket closes with the app."""


def set_app_id() -> None:
    """Nothing to do on the Mac: the bundle identifier already groups the app's windows."""


def relaunch() -> None:
    """Start a fresh copy of the bundle, once this one has shut down. From source there is no
    bundle to reopen, so nothing starts."""
    if not getattr(sys, "frozen", False):
        return
    bundle = Path(sys.executable).parents[2]  # Lyssna.app/Contents/MacOS/Lyssna
    log.info("relaunching %s", bundle)
    subprocess.Popen(["open", "-n", str(bundle)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
