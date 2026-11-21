"""The Mac's dictation keys: turns the keyboard tap's raw macOS key events into the events of
lyssna.dictation.hotkey.PushToTalk. The twin of lyssna/win/pushtotalk.py.

The Mac's triggers are single modifier keys: fn (the default), right Option, right Command
and right Control. The keyboard tap (lyssna/mac/events.py) calls flags_changed, key_down and
key_up with the macOS key code, the event's CGEventFlags and a time.

- The trigger is recognised by its own key code in a modifier-change event, never by a flag
  alone: arrow and function keys carry the fn flag in their own events and must not start
  anything.
- A modifier-change event for any other key while holding is another modifier joining: a
  shortcut, so the recording is cancelled quietly.
- The right-hand keys use their device bit, so the left key of the same kind counts as
  another modifier (left Option held, then right Option: a chord, nothing starts).

Modifier-change events are never swallowed."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .. import host
from ..dictation import hotkey
from ..dictation.hotkey import ESCAPE, OTHER, SPACE, TAP_SECONDS

# Key codes (kVK_*), from HIToolbox Events.h.
FN = 63
RIGHT_OPTION = 61
RIGHT_COMMAND = 54
RIGHT_CONTROL = 62
SPACE_KEY = 49
ESCAPE_KEY = 53

# The dictation keys this system offers, by their config name ([dictation] hotkey).
TRIGGERS = {"fn": FN, "right_option": RIGHT_OPTION, "right_command": RIGHT_COMMAND, "right_control": RIGHT_CONTROL}
_KEY_NAMES = {SPACE_KEY: SPACE, ESCAPE_KEY: ESCAPE}

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
# other modifier is down".
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


def _noop(*args) -> None:
    return None


class PushToTalk(hotkey.PushToTalk):
    """The state machine, fed from the Mac keyboard tap: flags_changed / key_down / key_up,
    each returning True to swallow the event. trigger is the trigger's key code."""

    def __init__(
        self,
        trigger: str = "fn",
        hands_free: str = SPACE,
        on_start: Callable[[], bool | None] = _noop,
        on_stop: Callable[[], None] = _noop,
        on_lock: Callable[[], None] = _noop,
        on_cancel: Callable[[bool], None] = _noop,
        tap_seconds: float = TAP_SECONDS,
        double_tap: bool = False,
    ):
        if trigger not in TRIGGERS:
            raise ValueError(f"Unknown dictation key '{trigger}'. Use one of: {', '.join(TRIGGERS)}")
        super().__init__(hands_free=hands_free, on_start=on_start, on_stop=on_stop, on_lock=on_lock,
                         on_cancel=on_cancel, tap_seconds=tap_seconds, double_tap=double_tap)
        self.trigger = TRIGGERS[trigger]

    def flags_changed(self, keycode: int, flags: int, t: float) -> bool:
        if keycode != self.trigger:
            self.key_pressed(OTHER, t)  # another modifier changed: while holding, a shortcut
            return False
        if flags & _OWN[self.trigger]:
            self.trigger_pressed(t, alone=not flags & _OTHERS[self.trigger])
        else:
            self.trigger_released(t)
        return False

    def key_down(self, keycode: int, t: float) -> bool:
        if keycode == self.trigger:
            return False
        return self.key_pressed(_KEY_NAMES.get(keycode, OTHER), t)

    def key_up(self, keycode: int, t: float) -> bool:
        return self.key_released(_KEY_NAMES.get(keycode, OTHER), t)


def attach(
    keyboard_tap,
    cfg: Mapping,
    on_start: Callable[[], bool | None],
    on_stop: Callable[[], None],
    on_lock: Callable[[], None],
    on_cancel: Callable[[bool], None],
) -> PushToTalk:
    """Build the dictation keys from the config's [dictation] section and hand them to the
    keyboard tap (lyssna.mac.events.KeyboardTap). Raises ValueError for a key this system
    does not have; nothing is attached then."""
    section = cfg.get("dictation", {})
    ptt = PushToTalk(
        trigger=str(section.get("hotkey", host.DICTATION_KEY_DEFAULT)), hands_free=str(section.get("hands_free", SPACE)),
        on_start=on_start, on_stop=on_stop, on_lock=on_lock, on_cancel=on_cancel,
        double_tap=bool(section.get("double_tap", host.DICTATION_DOUBLE_TAP_DEFAULT)),
    )
    keyboard_tap.push_to_talk = ptt
    return ptt
