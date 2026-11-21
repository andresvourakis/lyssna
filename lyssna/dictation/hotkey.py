"""Push-to-talk: hold the dictation key to dictate, release to finish.

PushToTalk is a pure state machine that knows no key codes, no modifier flags and no
operating system. Each system's adapter (lyssna/mac/pushtotalk.py, lyssna/win/pushtotalk.py)
turns its raw key events into the few events below and knows that system's keys, so the
rules are written once and unit tested without a keyboard or a clock.

The events:

- trigger_pressed(t, alone): the dictation key went down. For a chord of two keys (Ctrl+Win)
  that is the moment both are held. alone is False when some other modifier is held too.
- trigger_released(t): the dictation key went up; for a chord, the first of its keys let go.
- key_pressed(key, t) and key_released(key, t): any other key, named "space", "escape" or
  OTHER. A modifier that is not part of the trigger counts as OTHER when it goes down.

Each returns True when the event must be swallowed.

The rules:

- Idle: nothing is swallowed. The trigger pressed alone starts a recording at once. Pressed
  while another modifier is already down (fn while Cmd is held) it is part of a shortcut and
  starts nothing.
- Holding: releasing the trigger stops (the text is pasted). Released within TAP_SECONDS, or
  any other key pressed while holding, cancels quietly and lets the keys through, so fn+arrow
  and a stray tap behave as if Lyssna were not there. The hands-free key (Space) locks the
  recording and is swallowed. Repeats of the trigger are ignored.
- Double tap (when turned on): a tap that starts within DOUBLE_TAP_SECONDS of the end of an
  earlier tap locks instead of cancelling. The first tap was cancelled quietly like any tap;
  the second one's recording, started when it went down, simply keeps going hands-free. So a
  double tap throws away exactly what a single tap does, and nothing more.
- Locked (hands-free): pressing the trigger again stops; Escape cancels (swallowed, and the
  session offers Undo). Other keys go through untouched.

The trigger events are never swallowed (the adapters only ever feed modifier keys as
triggers, and holding a modifier back could leave an app believing it is stuck). Swallowed
key presses also have their key-up (and any auto-repeat) swallowed, so the front app never
sees half a keystroke."""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger("lyssna.dictation.hotkey")

SPACE = "space"
ESCAPE = "escape"
OTHER = "other"  # any key or modifier that is neither the trigger, the hands-free key nor Escape

HANDS_FREE_KEYS = (SPACE,)

TAP_SECONDS = 0.25  # a trigger released sooner than this was a tap, not a dictation
DOUBLE_TAP_SECONDS = 0.5  # a second tap starting this soon after the first one ended is a double tap


def _noop(*args) -> None:
    return None


class PushToTalk:
    """Feed it trigger_pressed / trigger_released / key_pressed / key_released; each returns
    True to swallow the event.

    Callbacks: on_start() (may return False to refuse, e.g. while the last dictation is still
    being processed; the press is then ignored), on_stop(), on_lock(), on_cancel(quiet):
    quiet is True for a tap or a shortcut (nothing to tell the user), False for Escape."""

    def __init__(
        self,
        hands_free: str = SPACE,
        on_start: Callable[[], bool | None] = _noop,
        on_stop: Callable[[], None] = _noop,
        on_lock: Callable[[], None] = _noop,
        on_cancel: Callable[[bool], None] = _noop,
        tap_seconds: float = TAP_SECONDS,
        double_tap: bool = False,
        double_tap_seconds: float = DOUBLE_TAP_SECONDS,
    ):
        if hands_free not in HANDS_FREE_KEYS:
            raise ValueError(f"Unknown hands-free key '{hands_free}'. Use one of: {', '.join(HANDS_FREE_KEYS)}")
        self.hands_free = hands_free
        self.on_start, self.on_stop, self.on_lock, self.on_cancel = on_start, on_stop, on_lock, on_cancel
        self.tap_seconds = tap_seconds
        self.double_tap = double_tap
        self.double_tap_seconds = double_tap_seconds
        self.state = "idle"        # idle | holding | locked
        self._trigger_down = False
        self._pressed_at = 0.0
        self._second_tap = False  # this hold began soon enough after a tap to make a double tap
        self._last_tap_end: float | None = None  # when the last quietly cancelled tap was released
        self._swallowing: set[str] = set()  # keys whose press we swallowed and that are still down

    # -- outside changes ---------------------------------------------------------------

    def enter_locked(self) -> None:
        """A hands-free dictation was started elsewhere (the pill's button): keys now act as in locked."""
        self.state = "locked"

    def reset(self) -> None:
        """The dictation ended by some other path (the pill, a time limit, an error). The time
        of the last tap is kept: the session calls this right after a tap's quiet cancel too,
        and a double tap must still be recognised then."""
        self.state = "idle"

    # -- events ---------------------------------------------------------------------------

    def trigger_pressed(self, t: float, alone: bool = True) -> bool:
        if self._trigger_down:
            return False  # a repeat
        self._trigger_down = True
        if self.state == "locked":
            self.state = "idle"
            self._last_tap_end = None
            self.on_stop()
            return False
        if self.state != "idle" or not alone:
            return False
        if self.on_start() is False:
            return False
        self.state = "holding"
        self._pressed_at = t
        last = self._last_tap_end
        self._second_tap = self.double_tap and last is not None and t - last <= self.double_tap_seconds
        return False

    def trigger_released(self, t: float) -> bool:
        if not self._trigger_down:
            return False
        self._trigger_down = False
        if self.state != "holding":
            return False
        if t - self._pressed_at >= self.tap_seconds:
            self.state = "idle"
            self._last_tap_end = None
            self.on_stop()
        elif self._second_tap:
            self.state = "locked"
            self._last_tap_end = None
            self.on_lock()
        else:
            self.state = "idle"
            self._last_tap_end = t
            self.on_cancel(True)
        return False

    def key_pressed(self, key: str, t: float) -> bool:
        if key in self._swallowing:
            return True  # auto-repeat of a key whose press we took
        if self.state == "holding":
            if key == self.hands_free:
                self.state = "locked"
                self._swallowing.add(key)
                self._last_tap_end = None
                self.on_lock()
                return True
            self._cancel(quiet=True)
            return False
        if self.state == "locked" and key == ESCAPE:
            self.state = "idle"
            self._swallowing.add(key)
            self.on_cancel(False)
            return True
        return False

    def key_released(self, key: str, t: float) -> bool:
        if key in self._swallowing:
            self._swallowing.discard(key)
            return True
        return False

    # -- internals ----------------------------------------------------------------------

    def _cancel(self, quiet: bool) -> None:
        self.state = "idle"
        self._last_tap_end = None  # a shortcut, not a tap: it never makes half of a double tap
        self.on_cancel(quiet)
