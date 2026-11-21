"""Which computer Lyssna runs on, what it can do, and the platform modules the engine uses.

Why one module: the Mac app and the Windows app share the engine, and only the modules under
lyssna/mac/ and lyssna/win/ talk to the operating system. They come in pairs with the same
names and the same public functions, and the engine reaches them only through here
(`host.capture`, `host.audio`...), so the choice between a Mac module and its Windows twin is
made in exactly one place. Where the two systems differ in a way the engine or the interface
must know about, that difference is a named capability below, never a test of the system.

It also picks the interface (`host.app`): the Qt one in lyssna/app.py, or on a Mac, until that
one matches it, the AppKit one in lyssna/legacy/ (see interface()).

This module imports nothing from the package when it loads. The platform modules load the
first time they are asked for, so importing host never pulls in a Mac or Windows framework
by itself."""

from __future__ import annotations

import importlib
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
DEVICE = "Mac" if IS_MAC else "PC"

# Wording that names a place or a key on the computer. On the Mac each is the text the app always used.
KEY_STORE = "Windows Credential Manager" if IS_WINDOWS else "the Keychain"  # where API keys are saved
MENU_PLACE = "the Lyssna icon by the clock" if IS_WINDOWS else "the menu bar"  # where Lyssna's menu opens
APP_KIND = "the app" if IS_WINDOWS else "the menu bar app"  # what the command line's help calls the app
PASTE_SHORTCUT = "Ctrl+V" if IS_WINDOWS else "⌘V"  # the keys that paste
MIC_SETTINGS_PLACE = (  # where the user allows the microphone, as the system's settings name it
    "Settings > Privacy & security > Microphone" if IS_WINDOWS else "System Settings > Privacy & Security > Microphone"
)

# What the computer has. The engine and the interface ask these, never the operating system.
NEEDS_ACCESSIBILITY = not IS_WINDOWS  # reading the selection and the global keys need the Accessibility permission
HAS_GLOBE_KEY = not IS_WINDOWS  # the fn key doubles as the Globe key, and a system setting decides what it does
STATUS_TEXT_BESIDE_ICON = not IS_WINDOWS  # the menu bar can show text (the reading progress) next to the icon
FAST_INT8 = not IS_WINDOWS  # 8-bit model maths is fast here (Apple silicon); x86 PCs run the half-precision model faster
SYSTEM_VOICE: str | None = "sapi" if IS_WINDOWS else "say" if IS_MAC else None  # the built-in voice's provider name
DICTATION_KEY_DEFAULT = "ctrl+win" if IS_WINDOWS else "fn"  # [dictation] hotkey: the key held to dictate
DICTATION_DOUBLE_TAP_DEFAULT = IS_WINDOWS  # [dictation] double_tap: two quick taps of that key start hands-free

# How this computer runs the local cleanup model (S1-mini through llama.cpp): "gpu" with Metal on
# Apple silicon, "cpu" on a PC, None where it does not run and dictation cleans up with rules.
# Measured: a 2-core cloud PC fits the cleanup budget up to about 90 words on the CPU, and an
# M3 Pro on the CPU with 4 threads cleans 180 words in 1.77 s. Intel Macs stay on rules for now;
# turning the CPU route on there is one change, `"cpu" if IS_WINDOWS or IS_MAC`, plus installing
# llama-cpp-python for them in pyproject.toml.
LOCAL_CLEANUP: str | None = "gpu" if IS_MAC and platform.machine() == "arm64" else "cpu" if IS_WINDOWS else None

# Settings pages the app opens. None where the system has no such page.
MIC_SETTINGS_URL = (
    "ms-settings:privacy-microphone" if IS_WINDOWS
    else "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
)
KEYBOARD_SETTINGS_URL = None if IS_WINDOWS else "x-apple.systempreferences:com.apple.Keyboard-Settings.extension"
ACCESSIBILITY_SETTINGS_URL = None if IS_WINDOWS else "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


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


def library_file(name: str) -> str:
    """A shared library's file name as the frozen app ships it: espeak-ng.dll, libespeak-ng.dylib."""
    return f"{name}.dll" if IS_WINDOWS else f"lib{name}.dylib"


def open_target(target: str) -> None:
    """Open a URL, a settings page or a file with the system's default handler, without
    touching our stdio."""
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]  # exists on Windows only
        return
    subprocess.run(["open", target], check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


# Neutral name -> (Mac module, Windows module). None means that system has no such piece yet:
# `host.<name>` is then None, and require() says so.
MODULES: dict[str, tuple[str | None, str | None]] = {
    "app": ("lyssna.legacy.app", "lyssna.app"),                 # run(): the interface (on a Mac, interface() decides)
    "instance": ("lyssna.mac.instance", "lyssna.win.instance"),  # SingleInstance, set_app_id(), relaunch()
    "capture": ("lyssna.mac.capture", "lyssna.win.capture"),    # the selected text and the clipboard
    "events": ("lyssna.mac.events", "lyssna.win.events"),       # global keys and mouse, permission check
    "audio": ("lyssna.mac.audio", "lyssna.win.audio"),          # make_player(audio, speed)
    "hotkey_table": ("lyssna.mac.hotkeys", "lyssna.win.hotkeys"),  # key codes, modifier flags, key names
    "credentials": ("lyssna.mac.credentials", "lyssna.win.credentials"),  # CredentialBackend: where API keys are stored
    "policy": ("lyssna.mac.policy", "lyssna.win.policy"),       # read_preference(key): the managed settings
    "autostart": ("lyssna.mac.autostart", "lyssna.win.autostart"),  # is_enabled(), set_enabled(on): start at login
    "ipc": ("lyssna.mac.ipc", "lyssna.win.ipc"),                # ControlServer, send(): the CLI's channel to the app
    "window": ("lyssna.mac.window", "lyssna.win.window"),       # floating panels that never take focus, Dock icon, title bar
    "statusbar": ("lyssna.mac.statusbar", "lyssna.win.statusbar"),  # the menu bar or tray item: title, icon, headers, shortcuts
    "appearance": ("lyssna.mac.appearance", "lyssna.win.appearance"),  # the fonts, their tracking, reduce_motion()
    "mic": ("lyssna.mac.mic", "lyssna.win.mic"),                # the microphone and its permission
    "insert": ("lyssna.mac.insert", "lyssna.win.insert"),       # the paste pieces dictation uses
    "field_probe": ("lyssna.mac.fieldprobe", "lyssna.win.fieldprobe"),  # FieldProbe: reads the text field a dictation went into
    "pushtotalk": ("lyssna.mac.pushtotalk", "lyssna.win.pushtotalk"),  # attach(): the dictation key, hold to talk
    "cpu": ("lyssna.mac.cpu", "lyssna.win.cpu"),                # physical_cores(): for the cleanup model's thread count
    "globe": ("lyssna.mac.globe", None),                        # globe_key_action(): the system's Globe key setting
    "launch_agent": ("lyssna.mac.launch_agent", None),          # start at login from source, for the command line
    "speechy": ("lyssna.mac.speechy", None),                    # the carry-over from the app's old name
}


INTERFACE_ENV = "LYSSNA_INTERFACE"


def interface() -> str:
    """Which interface runs: "new" (the Qt one, lyssna/app.py) or "legacy" (the Mac's AppKit
    one, lyssna/legacy/app.py). Windows always runs the new one. On a Mac the legacy one runs
    unless the environment variable LYSSNA_INTERFACE=new, or else interface = "new" under [ui]
    in the config file, asks for the new one. Temporary: it goes when lyssna/legacy/ does."""
    if IS_WINDOWS:
        return "new"
    choice = os.environ.get(INTERFACE_ENV, "").strip().lower()
    if not choice:
        from . import config  # here, not at the top: host imports nothing from the package when it loads

        choice = str(config.load().get("ui", {}).get("interface", "")).strip().lower()
    return "new" if choice == "new" else "legacy"


def require(name: str) -> ModuleType:
    """`host.<name>`, for code that cannot go on without it. Raises NotImplementedError on a
    system that has no such piece yet."""
    module = getattr(sys.modules[__name__], name)
    if module is None:
        raise NotImplementedError(f"{name} is not built for this computer yet")
    return module


def __getattr__(name: str):
    if name not in MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mac, windows = MODULES[name]
    target = windows if IS_WINDOWS or (name == "app" and interface() == "new") else mac
    module = importlib.import_module(target) if target else None
    globals()[name] = module  # cached: the next lookup is a plain attribute
    return module
