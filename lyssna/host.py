"""Which operating system Lyssna runs on, and the pieces of the engine that differ between them.

Why one module: the Mac app and the Windows app share the engine, and only a handful of its
modules talk to the operating system. Each of those is reached through here, so the choice
between a Mac module and its Windows twin under lyssna/win/ is made in exactly one place.

This module imports nothing from the package when it loads. The platform modules load the
first time they are asked for (`host.capture`, `host.audio`...), so importing host never pulls
in a Mac or Windows framework by itself."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
DEVICE = "Mac" if IS_MAC else "PC"

MIC_SETTINGS_URL = (
    "ms-settings:privacy-microphone" if IS_WINDOWS
    else "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
)


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    state_dir: Path
    cache_dir: Path
    log_dir: Path
    socket_path: Path  # on Windows only a marker: the control channel is a named pipe (lyssna.win.ipc)


def paths() -> Paths:
    """Where Lyssna keeps its config, state (models, history, usage), cache and logs."""
    home = Path.home()
    if IS_WINDOWS:
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        state = local / "Lyssna"
        return Paths(roaming / "Lyssna", state, state / "Cache", state / "Logs", state / "lyssna.pipe")
    state = home / "Library" / "Application Support" / "Lyssna"
    return Paths(home / ".config" / "lyssna", state, home / "Library" / "Caches" / "Lyssna",
                 home / "Library" / "Logs" / "Lyssna", state / "lyssna.sock")


def open_target(target: str) -> None:
    """Open a URL, a settings page or a file with the system's default handler, without
    touching our stdio."""
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]  # exists on Windows only
        return
    subprocess.run(["open", target], check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


# Neutral name -> (Mac module, Windows module). None means the existing Mac code in the module
# that asks handles it itself.
_MODULES: dict[str, tuple[str | None, str]] = {
    "app": ("lyssna.app", "lyssna.win.app"),                       # run(): the interface
    "capture": ("lyssna.capture", "lyssna.win.capture"),           # the selected text and the clipboard
    "events": ("lyssna.events", "lyssna.win.events"),              # global keys and mouse, permission check
    "audio": ("lyssna.audio_mac", "lyssna.win.audio"),             # make_player(audio, speed)
    "hotkey_table": ("lyssna.hotkeys_mac", "lyssna.win.hotkeys"),  # key codes and modifier flags
    "autostart_impl": (None, "lyssna.win.autostart"),              # start at login
    "ipc": (None, "lyssna.win.ipc"),                               # the CLI's channel to the running app
    "credentials": (None, "lyssna.win.credentials"),               # where API keys are stored
    "policy_impl": (None, "lyssna.win.policy"),                    # the managed local-only setting
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mac, windows = _MODULES[name]
    target = windows if IS_WINDOWS else mac
    module = importlib.import_module(target) if target else None
    globals()[name] = module  # cached: the next lookup is a plain attribute
    return module
