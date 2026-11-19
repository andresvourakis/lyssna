"""Global input monitoring through Quartz event taps.

KeyboardTap  - fires callbacks for registered hotkeys and swallows those keystrokes; with a
               push-to-talk attached it also feeds it key-up and modifier changes.
MouseTap     - listen-only; turns raw mouse events into "the user probably just selected
               text" gestures (drag, double/triple click) and "dismiss" signals.

Both taps must be started on the main thread (they attach to its run loop). Callbacks
are invoked on a worker thread so the tap never blocks input."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import Quartz

from .hotkeys import MODIFIER_MASK, parse

log = logging.getLogger("lyssna.events")


def accessibility_trusted(prompt: bool = False) -> bool:
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))


class _EventTap:
    """Shared plumbing: create the tap, keep it alive, re-enable after timeouts."""

    mask: int = 0
    listen_only: bool = False

    def __init__(self):
        self._tap = None
        self._source = None

    def start(self) -> bool:
        options = Quartz.kCGEventTapOptionListenOnly if self.listen_only else Quartz.kCGEventTapOptionDefault
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, options, self.mask, self._on_event, None
        )
        if self._tap is None:
            log.error("%s: could not create event tap (missing Accessibility permission?)", type(self).__name__)
            return False
        self._source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), self._source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        return True

    def _on_event(self, proxy, event_type, event, refcon):
        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        try:
            return self.handle(event_type, event)
        except Exception:  # never let an exception kill the tap
            log.exception("event handler failed")
            return event

    def handle(self, event_type, event):
        return event


def spawn(fn: Callable, *args, name: str = "lyssna-input") -> None:
    """Run fn(*args) on a fresh daemon thread, so an event tap callback returns at once."""
    threading.Thread(target=fn, args=args, daemon=True, name=name).start()


class KeyboardTap(_EventTap):
    """Key-up and modifier-change events are in the mask so a push-to-talk key (Fn, right
    Option...) can be seen going down and up; while none is attached they pass straight
    through and only key presses are looked at, exactly as before."""

    mask = (
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
    )

    def __init__(self):
        super().__init__()
        self._bindings: dict[tuple[int, int], Callable[[], None]] = {}
        self.on_any_key: Callable[[], None] | None = None
        # A lyssna.dictation.hotkey.PushToTalk. Its methods run inline in the tap callback,
        # so they must stay quick; it decides whether each event is swallowed.
        self.push_to_talk = None

    def bind(self, spec: str, callback: Callable[[], None]) -> None:
        self._bindings[parse(spec)] = callback

    def handle(self, event_type, event):
        ptt = self.push_to_talk
        if event_type != Quartz.kCGEventKeyDown:
            if ptt is None:
                return event
            keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            if event_type == Quartz.kCGEventFlagsChanged:
                swallow = ptt.flags_changed(keycode, Quartz.CGEventGetFlags(event), time.monotonic())
            elif event_type == Quartz.kCGEventKeyUp:
                swallow = ptt.key_up(keycode, time.monotonic())
            else:
                swallow = False
            return None if swallow else event
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        flags = Quartz.CGEventGetFlags(event) & MODIFIER_MASK
        if self.on_any_key:
            spawn(self.on_any_key)
        if ptt is not None and ptt.key_down(keycode, time.monotonic()):
            return None
        cb = self._bindings.get((flags, keycode))
        if cb is None:
            return event
        spawn(cb)
        return None  # swallow so the front app doesn't also see the hotkey


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


class MouseTap(_EventTap):
    listen_only = True
    mask = (
        Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
        | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseUp)
        | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
    )

    def __init__(self, detector: SelectionGestureDetector):
        super().__init__()
        self.detector = detector

    def handle(self, event_type, event):
        loc = Quartz.CGEventGetLocation(event)
        p = Point(loc.x, loc.y)
        if event_type == Quartz.kCGEventLeftMouseDown:
            clicks = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventClickState)
            self.detector.mouse_down(p, int(clicks))
        elif event_type == Quartz.kCGEventLeftMouseDragged:
            self.detector.mouse_dragged(p)
        elif event_type == Quartz.kCGEventLeftMouseUp:
            self.detector.mouse_up(p)
        elif event_type == Quartz.kCGEventScrollWheel:
            pass  # scrolling shouldn't dismiss; the button is anchored to the screen anyway
        else:
            self.detector.other_click(p)
        return event
