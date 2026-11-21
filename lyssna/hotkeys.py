"""Hotkey spec parsing ("ctrl+alt+s" -> modifier flags + key code) and pretty printing.

The key codes, modifier flags and display style differ per system, so the tables live in
lyssna/hotkeys_mac.py and lyssna/win/hotkeys.py and lyssna.host picks one."""

from __future__ import annotations

from . import host

_table = host.hotkey_table

KEYCODES: dict[str, int] = _table.KEYCODES
MODIFIERS: dict[str, int] = _table.MODIFIERS
MODIFIER_MASK: int = _table.MODIFIER_MASK


def tokens(spec: str) -> list[str]:
    """Split a spec like "Ctrl + Alt + S" into lowercase parts: ["ctrl", "alt", "s"]."""
    return [p.strip().lower() for p in spec.replace(" ", "").split("+") if p.strip()]


def parse_with(spec: str, modifiers: dict[str, int], keycodes: dict[str, int]) -> tuple[int, int]:
    """parse() against any key table, so each system's table can be checked on any system."""
    flags = 0
    key = None
    for p in tokens(spec):
        if p in modifiers:
            flags |= modifiers[p]
        elif p in keycodes:
            key = keycodes[p]
        else:
            raise ValueError(f"Unknown key '{p}' in hotkey '{spec}'")
    if key is None:
        raise ValueError(f"Hotkey '{spec}' has no non-modifier key")
    return flags, key


def parse(spec: str) -> tuple[int, int]:
    return parse_with(spec, MODIFIERS, KEYCODES)


def pretty(spec: str) -> str:
    return _table.pretty(tokens(spec))
