"""The parts of input handling that need no operating system: the point type, the worker
thread helper, and the state machine that turns mouse events into "the user probably just
selected text" gestures.

Kept apart from the event taps (lyssna/mac/events.py on the Mac, lyssna/win/events.py on Windows)
so the selection watcher and the tests can use them without loading Quartz."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass


def spawn(fn: Callable, *args, name: str = "lyssna-input") -> None:
    """Run fn(*args) on a fresh daemon thread, so an event tap callback returns at once."""
    threading.Thread(target=fn, args=args, daemon=True, name=name).start()


@dataclass
class Point:
    x: float
    y: float  # Quartz coordinates: origin top-left of the main display


class SelectionGestureDetector:
    """Pure state machine (no Quartz) so it can be unit tested.

    Feed it mouse events; it calls on_select(point) when a gesture that usually ends
    with selected text completes: a drag longer than `drag_threshold` points, or a
    double/triple click. It calls on_dismiss(point) on any other click, and
    on_press(point, click_state) on every press outside our own UI (so the caller can
    snapshot state before the gesture; click_state is 2 or more on a double or triple click)."""

    def __init__(
        self,
        on_select: Callable[[Point], None],
        on_dismiss: Callable[[Point], None],
        drag_threshold: float = 8.0,
        ignore_region: Callable[[Point], bool] | None = None,
        on_press: Callable[[Point, int], None] | None = None,
    ):
        self.on_select = on_select
        self.on_dismiss = on_dismiss
        self.on_press = on_press
        self.drag_threshold = drag_threshold
        self.ignore_region = ignore_region or (lambda p: False)
        self._down: Point | None = None
        self._max_dist = 0.0
        self._click_state = 1

    def mouse_down(self, p: Point, click_state: int = 1) -> None:
        if self.ignore_region(p):
            self._down = None
            return
        self._down = p
        self._max_dist = 0.0
        self._click_state = click_state
        self.on_dismiss(p)
        if self.on_press:
            self.on_press(p, click_state)

    def mouse_dragged(self, p: Point) -> None:
        if self._down is None:
            return
        self._max_dist = max(self._max_dist, math.hypot(p.x - self._down.x, p.y - self._down.y))

    def mouse_up(self, p: Point) -> None:
        if self._down is None:
            return
        dragged = self._max_dist >= self.drag_threshold
        multi_click = self._click_state >= 2
        self._down = None
        # Any real drag or multi-click counts; the selection watcher then wants proof that
        # text was selected, so window moves and scrollbar drags need no rule here.
        if dragged or multi_click:
            self.on_select(p)

    def other_click(self, p: Point) -> None:
        if not self.ignore_region(p):
            self.on_dismiss(p)
