r"""Start at login on Windows.

The real version will add or remove a value under the registry Run key
HKCU\Software\Microsoft\Windows\CurrentVersion\Run (through winreg). Not built yet: start at
login reads as off and cannot be turned on."""

from __future__ import annotations


def is_enabled() -> bool:
    return False


def set_enabled(enabled: bool) -> bool:
    """Turn start at login on or off. Returns True on success."""
    return False
