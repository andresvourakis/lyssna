"""Push-to-talk: hold a modifier key (Fn by default) to dictate, release to finish.

PushToTalk is a pure state machine with no Quartz in it: the keyboard tap feeds it raw key
codes, modifier flags and times, and it answers whether to swallow each event. So the rules
below are unit tested without a keyboard or a clock.

- Idle: nothing is swallowed. Pressing the trigger on its own starts a recording at once. The
  trigger is recognised by its own key code in a modifier-change event, never by a flag alone:
  arrow and function keys carry the Fn flag in their own events and must not start anything.
  A trigger pressed while another modifier is already down (Fn while Cmd is held) is part of a
  shortcut and starts nothing.
- Holding: releasing the trigger stops (the text is pasted). Released within TAP_SECONDS, or
  any other key pressed while holding, cancels quietly and lets the keys through, so Fn+arrow
  and a stray tap behave as if Lyssna were not there. The hands-free key (Space) locks the
  recording and is swallowed. Repeats of the trigger are ignored.
- Locked (hands-free): pressing the trigger again stops; Escape cancels (swallowed, and the
  session offers Undo). Other keys go through untouched.

Modifier-change events are never swallowed: holding one back could leave an app believing a
modifier is stuck. Swallowed key presses also have their key-up (and any auto-repeat)
swallowed, so the front app never sees half a keystroke."""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger("lyssna.dictation.hotkey")

# Key codes (kVK_*), from HIToolbox Events.h.
FN = 63
RIGHT_OPTION = 61
RIGHT_COMMAND = 54
RIGHT_CONTROL = 62
SPACE = 49
ESCAPE = 53

TRIGGERS = {"fn": FN, "right_option": RIGHT_OPTION, "right_command": RIGHT_COMMAND, "right_control": RIGHT_CONTROL}
HANDS_FREE_KEYS = {"space": SPACE}

# CGEventFlags: the generic modifier bits, and the device bits that tell left from right.
FLAG_SHIFT = 0x00020000
FLAG_CONTROL = 0x00040000
FLAG_OPTION = 0x00080000
FLAG_COMMAND = 0x00100000
FLAG_FN = 0x00800000
DEVICE_LEFT_CONTROL = 0x00000001
DEVICE_LEFT_COMMAND = 0x00000008
DEVICE_RIGHT_COMMAND = 0x00000010
DEVICE_LEFT_OPTION = 0x00000020
DEVICE_RIGHT_OPTION = 0x00000040
DEVICE_RIGHT_CONTROL = 0x00002000

# For each trigger: the bit that says "this very key is down", and the bits that say "some
# other modifier is down". The right-hand keys use their device bit, so the left key of the
# same kind counts as another modifier (left Option held, then right Option: a chord).
_OWN = {
    FN: FLAG_FN,
    RIGHT_OPTION: DEVICE_RIGHT_OPTION,
    RIGHT_COMMAND: DEVICE_RIGHT_COMMAND,
    RIGHT_CONTROL: DEVICE_RIGHT_CONTROL,
}
_ALL_OTHERS = FLAG_SHIFT | FLAG_CONTROL | FLAG_OPTION | FLAG_COMMAND | FLAG_FN
_OTHERS = {
    FN: _ALL_OTHERS & ~FLAG_FN,
    RIGHT_OPTION: (_ALL_OTHERS & ~FLAG_OPTION) | DEVICE_LEFT_OPTION,
    RIGHT_COMMAND: (_ALL_OTHERS & ~FLAG_COMMAND) | DEVICE_LEFT_COMMAND,
    RIGHT_CONTROL: (_ALL_OTHERS & ~FLAG_CONTROL) | DEVICE_LEFT_CONTROL,
}

TAP_SECONDS = 0.25  # a trigger released sooner than this was a tap, not a dictation


def _noop(*args) -> None:
    return None


class PushToTalk:
    """Feed it flags_changed / key_down / key_up; each returns True to swallow the event.

    Callbacks: on_start() (may return False to refuse, e.g. while the last dictation is still
    being processed; the press is then ignored), on_stop(), on_lock(), on_cancel(quiet):
    quiet is True for a tap or a shortcut (nothing to tell the user), False for Escape."""

    def __init__(
        self,
        trigger: str = "fn",
        hands_free: str = "space",
        on_start: Callable[[], bool | None] = _noop,
        on_stop: Callable[[], None] = _noop,
        on_lock: Callable[[], None] = _noop,
        on_cancel: Callable[[bool], None] = _noop,
        tap_seconds: float = TAP_SECONDS,
    ):
        if trigger not in TRIGGERS:
            raise ValueError(f"Unknown dictation key '{trigger}'. Use one of: {', '.join(TRIGGERS)}")
        if hands_free not in HANDS_FREE_KEYS:
            raise ValueError(f"Unknown hands-free key '{hands_free}'. Use one of: {', '.join(HANDS_FREE_KEYS)}")
        self.trigger = TRIGGERS[trigger]
        self.hands_free = HANDS_FREE_KEYS[hands_free]
        self.on_start, self.on_stop, self.on_lock, self.on_cancel = on_start, on_stop, on_lock, on_cancel
        self.tap_seconds = tap_seconds
        self.state = "idle"        # idle | holding | locked
        self._trigger_down = False
        self._pressed_at = 0.0
        self._swallowing: set[int] = set()  # keys whose press we swallowed and that are still down

    # -- outside changes ---------------------------------------------------------------

    def enter_locked(self) -> None:
        """A hands-free dictation was started elsewhere (the pill's button): keys now act as in locked."""
        self.state = "locked"

    def reset(self) -> None:
        """The dictation ended by some other path (the pill, a time limit, an error)."""
        self.state = "idle"

    # -- events ---------------------------------------------------------------------------

    def flags_changed(self, keycode: int, flags: int, t: float) -> bool:
        if keycode != self.trigger:
            if self.state == "holding":
                self._cancel(quiet=True)  # another modifier joined: this is a shortcut
            return False
        down = bool(flags & _OWN[self.trigger])
        if down == self._trigger_down:
            return False  # a repeat, or an event for the other half of a pair
        self._trigger_down = down
        if down:
            self._trigger_pressed(flags, t)
        else:
            self._trigger_released(t)
        return False

    def key_down(self, keycode: int, t: float) -> bool:
        if keycode in self._swallowing:
            return True  # auto-repeat of a key whose press we took
        if keycode == self.trigger:
            return False
        if self.state == "holding":
            if keycode == self.hands_free:
                self.state = "locked"
                self._swallowing.add(keycode)
                self.on_lock()
                return True
            self._cancel(quiet=True)
            return False
        if self.state == "locked" and keycode == ESCAPE:
            self.state = "idle"
            self._swallowing.add(keycode)
            self.on_cancel(False)
            return True
        return False

    def key_up(self, keycode: int, t: float) -> bool:
        if keycode in self._swallowing:
            self._swallowing.discard(keycode)
            return True
        return False

    # -- internals ----------------------------------------------------------------------

    def _trigger_pressed(self, flags: int, t: float) -> None:
        if self.state == "locked":
            self.state = "idle"
            self.on_stop()
            return
        if self.state != "idle" or flags & _OTHERS[self.trigger]:
            return
        if self.on_start() is False:
            return
        self.state = "holding"
        self._pressed_at = t

    def _trigger_released(self, t: float) -> None:
        if self.state != "holding":
            return
        self.state = "idle"
        if t - self._pressed_at < self.tap_seconds:
            self.on_cancel(True)
        else:
            self.on_stop()

    def _cancel(self, quiet: bool) -> None:
        self.state = "idle"
        self.on_cancel(quiet)
