"""Global keys and mouse on Windows.

The real version will use low-level keyboard and mouse hooks (SetWindowsHookEx through
ctypes) on their own thread. The keyboard hook can swallow a hotkey, and only the left Alt
key counts as Alt so AltGr typing on European keyboards is not taken over. Same public
surface as lyssna/events.py. Not built yet: the taps raise when started."""

from __future__ import annotations

from collections.abc import Callable

from ..gestures import Point, SelectionGestureDetector, spawn

__all__ = ["KeyboardTap", "MouseTap", "Point", "SelectionGestureDetector", "accessibility_trusted", "spawn"]

_NOT_BUILT = "Windows input hooks are not built yet"


def accessibility_trusted(prompt: bool = False) -> bool:
    """Windows has no permission for watching keys and reading the selection, so always True."""
    return True


class KeyboardTap:
    def __init__(self):
        self._bindings: dict[tuple[int, int], Callable[[], None]] = {}
        self.on_any_key: Callable[[], None] | None = None
        self.push_to_talk = None

    def bind(self, spec: str, callback: Callable[[], None]) -> None:
        from ..hotkeys import parse

        self._bindings[parse(spec)] = callback

    def start(self) -> bool:
        raise NotImplementedError(_NOT_BUILT)


class MouseTap:
    def __init__(self, detector: SelectionGestureDetector):
        self.detector = detector

    def start(self) -> bool:
        raise NotImplementedError(_NOT_BUILT)
