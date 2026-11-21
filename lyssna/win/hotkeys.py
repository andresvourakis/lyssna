"""The Windows key tables for hotkey specs: virtual-key codes, modifier flags and the names
the menus show (Ctrl+Alt+S). The key names are the ones the Mac table in
lyssna/hotkeys_mac.py accepts, so a config file works on both systems.

The modifier flags are our own bits, not a Windows constant: the keyboard hook builds them
from the modifier keys it sees held down."""

from __future__ import annotations

CTRL, ALT, SHIFT, WIN = 1, 2, 4, 8

KEYCODES: dict[str, int] = {
    **{chr(ord("a") + i): 0x41 + i for i in range(26)},
    **{str(i): 0x30 + i for i in range(10)},
    "=": 0xBB, "-": 0xBD, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0,
    "return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20, "delete": 0x08, "backspace": 0x08,
    "escape": 0x1B, "esc": 0x1B,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    **{f"f{i}": 0x70 + i - 1 for i in range(1, 13)},
}
MODIFIERS: dict[str, int] = {
    "ctrl": CTRL, "control": CTRL,
    "alt": ALT, "option": ALT, "opt": ALT,
    "shift": SHIFT,
    "cmd": WIN, "command": WIN, "win": WIN,
}
MODIFIER_MASK = CTRL | ALT | SHIFT | WIN
PRETTY = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "option": "Alt", "opt": "Alt", "shift": "Shift",
          "cmd": "Win", "command": "Win", "win": "Win"}


def pretty(parts: list[str]) -> str:
    """["ctrl", "alt", "s"] -> "Ctrl+Alt+S"."""
    return "+".join(PRETTY.get(p, p.capitalize()) for p in parts)
