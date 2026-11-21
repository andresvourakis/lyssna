r"""Start at login on Windows.

Why: the per-user Run key HKCU\Software\Microsoft\Windows\CurrentVersion\Run is the standard
place for an app that starts when the user signs in, and needs no administrator rights. Our
value is named Lyssna and holds the quoted path of the installed Lyssna.exe.

As on the Mac, start at login needs the installed app: running from source, sys.executable is
the Python interpreter, which must not be registered. winreg is imported inside the functions,
so this module imports on any system and tests put a fake winreg in sys.modules. Neither
function raises."""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("lyssna.win.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Lyssna"


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _normalise(command: str) -> str:
    return command.replace('"', "").strip().casefold()


def is_enabled() -> bool:
    """True when the Run key starts this very executable at login."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as handle:
            value, _kind = winreg.QueryValueEx(handle, VALUE_NAME)
    except FileNotFoundError:
        return False
    except Exception as e:
        log.warning("could not read the start at login setting: %s", e)
        return False
    return isinstance(value, str) and _normalise(value) == _normalise(sys.executable)


def set_enabled(enabled: bool) -> bool:
    """Turn start at login on or off. Returns True on success."""
    try:
        import winreg

        if enabled:
            if not _frozen():
                log.warning("start at login needs the installed app, not a run from source")
                return False
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.SetValueEx(handle, VALUE_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as handle:
                    winreg.DeleteValue(handle, VALUE_NAME)
            except FileNotFoundError:
                pass  # already off
    except Exception as e:
        log.error("start at login %s failed: %s", "on" if enabled else "off", e)
        return False
    log.info("start at login %s", "on" if enabled else "off")
    return True
