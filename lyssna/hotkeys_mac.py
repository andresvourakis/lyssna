"""The Mac key tables for hotkey specs: virtual key codes, Quartz modifier flags and the
symbols the menus show (⌃⌥S). lyssna/hotkeys.py picks this or lyssna/win/hotkeys.py."""

from __future__ import annotations

import Quartz

KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31,
    "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111,
}
MODIFIERS = {
    "ctrl": Quartz.kCGEventFlagMaskControl, "control": Quartz.kCGEventFlagMaskControl,
    "alt": Quartz.kCGEventFlagMaskAlternate, "option": Quartz.kCGEventFlagMaskAlternate, "opt": Quartz.kCGEventFlagMaskAlternate,
    "cmd": Quartz.kCGEventFlagMaskCommand, "command": Quartz.kCGEventFlagMaskCommand,
    "shift": Quartz.kCGEventFlagMaskShift,
}
MODIFIER_MASK = (
    Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskControl
    | Quartz.kCGEventFlagMaskAlternate | Quartz.kCGEventFlagMaskShift
)
PRETTY = {"ctrl": "⌃", "control": "⌃", "alt": "⌥", "option": "⌥", "opt": "⌥", "cmd": "⌘", "command": "⌘",
          "shift": "⇧", "space": "Space", "left": "←", "right": "→", "up": "↑", "down": "↓", "return": "↩", "escape": "⎋"}


def pretty(parts: list[str]) -> str:
    """["ctrl", "alt", "s"] -> "⌃⌥S"."""
    return "".join(PRETTY.get(p, p.upper()) for p in parts)
