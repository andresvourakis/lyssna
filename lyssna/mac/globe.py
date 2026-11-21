"""The macOS "Press Globe key to" setting (System Settings > Keyboard): what pressing the
Globe (fn) key does. Dictation on fn needs it set to Do Nothing, so the app reads it (never
writes it) to tell the user. lyssna.host hands this out as host.globe on a Mac."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

# com.apple.HIToolbox AppleFnUsageType, as System Settings > Keyboard writes it.
GLOBE_ACTIONS = {0: "nothing", 1: "input_source", 2: "emoji", 3: "dictation"}
GLOBE_LABELS = {
    "nothing": "Do Nothing",
    "input_source": "Change Input Source",
    "emoji": "Show Emoji & Symbols",
    "dictation": "Start Dictation",
    "default": "the macOS default",
}


def _read_default(domain: str, key: str) -> str | None:
    try:
        done = subprocess.run(["/usr/bin/defaults", "read", domain, key], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else ""


def globe_key_action(read: Callable[[str, str], str | None] = _read_default) -> str:
    """What pressing the Globe (Fn) key does, from the macOS keyboard setting. Read only.

    "nothing" is what dictation on Fn needs. "input_source", "emoji" and "dictation" each
    react to Fn as well. "default" means the setting was never changed: macOS then shows the
    emoji picker (or switches input source when several are set up), so it also needs
    changing. "unknown" means it could not be read."""
    raw = read("com.apple.HIToolbox", "AppleFnUsageType")
    if raw is None:
        return "unknown"
    if raw == "":
        return "default"
    try:
        return GLOBE_ACTIONS.get(int(raw), "unknown")
    except ValueError:
        return "unknown"
