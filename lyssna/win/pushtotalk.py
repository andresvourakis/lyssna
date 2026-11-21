"""The PC's dictation keys: turns the keyboard hook's virtual-key events into the events of
lyssna.dictation.hotkey.PushToTalk. The twin of lyssna/mac/pushtotalk.py.

The triggers, as Wispr Flow has them on Windows:

- ctrl+win (the default): either Ctrl plus either Windows key. It is down once both are
  held and up as soon as either is let go, in whatever order they were pressed.
- right_control: the right Ctrl key alone. Used as a modifier for another key (right
  Ctrl+C), that is another key while holding, which cancels quietly and lets the key through.

The keyboard hook (lyssna/win/events.py) calls key_event with the virtual-key code, whether
it went down, the modifier keys it sees held (left and right apart) and a time.

- Modifier key events are never swallowed: the hook's own modifier tracking depends on
  every app seeing them, and a swallowed modifier-up leaves a key stuck down.
- While Ctrl+Win is held, Space is always swallowed (its press, repeats and release), even
  when no dictation started, because Ctrl+Win+Space would otherwise switch the input language.
- AltGr on European keyboards arrives as left Ctrl plus right Alt. It never starts anything:
  left Ctrl alone is not a trigger, and right Alt held means the trigger is not alone.
- Releasing a Windows key that was pressed with no other key makes Windows open the Start
  menu. The remedy AutoHotkey uses: the first time Ctrl+Win becomes active, while the Windows
  key is still down, inject one press of the unassigned virtual key 0xE8, so Windows no longer
  treats the press as a lone one. MASK_START_MENU turns it off. The hook ignores the injected
  key like every injected event."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from .. import host
from ..dictation import hotkey
from ..dictation.hotkey import ESCAPE, OTHER, SPACE, TAP_SECONDS
from .events import MODIFIER_VKS, VK_LCONTROL, VK_LWIN, VK_RCONTROL, VK_RWIN

log = logging.getLogger("lyssna.win.pushtotalk")

VK_SPACE = 0x20
VK_ESCAPE = 0x1B
MASK_KEY = 0xE8  # unassigned: pressing it means nothing to Windows or to any app
MASK_START_MENU = True  # False: never inject MASK_KEY (if a real PC shows it is not needed)
INJECTED_MARK = 0x4C59534E  # dwExtraInfo on every key Lyssna sends, the same mark as lyssna.win.capture

_CTRL = frozenset({VK_LCONTROL, VK_RCONTROL})
_WIN = frozenset({VK_LWIN, VK_RWIN})

# The dictation keys this system offers, by their config name ([dictation] hotkey): each is the
# groups of keys that must all have one key held for the trigger to be down.
TRIGGERS: dict[str, tuple[frozenset[int], ...]] = {
    "ctrl+win": (_CTRL, _WIN),
    "right_control": (frozenset({VK_RCONTROL}),),
}
_KEY_NAMES = {VK_SPACE: SPACE, VK_ESCAPE: ESCAPE}


def _noop(*args) -> None:
    return None


class PushToTalk(hotkey.PushToTalk):
    """The state machine, fed from the Windows keyboard hook through key_event, which returns
    True to swallow the event. send_mask_key is the function that injects MASK_KEY; tests
    replace it."""

    def __init__(
        self,
        trigger: str = "ctrl+win",
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
        self.trigger = trigger
        self._groups = TRIGGERS[trigger]
        self._keys = frozenset().union(*self._groups)  # every key that is part of the trigger
        self._uses_win = bool(self._keys & _WIN)
        self.send_mask_key: Callable[[], None] = _send_mask_key
        self._held: frozenset[int] = frozenset()
        self._active = False  # the trigger is down
        self._forced: set[int] = set()  # Space presses swallowed because Ctrl+Win was held

    def key_event(self, vk: int, down: bool, held: frozenset[int], t: float) -> bool:
        if vk in MODIFIER_VKS:
            self._modifiers(held, t)
            return False
        if not down:
            if vk in self._forced:
                self._forced.discard(vk)
                self.key_released(SPACE, t)
                return True
            return self.key_released(_KEY_NAMES.get(vk, OTHER), t)
        if vk in self._forced:
            return True  # auto-repeat of a Space taken while Ctrl+Win was held
        swallow = self.key_pressed(_KEY_NAMES.get(vk, OTHER), t)
        if not swallow and vk == VK_SPACE and self._active and self._uses_win:
            self._forced.add(vk)
            return True
        return swallow

    def _modifiers(self, held: frozenset[int], t: float) -> None:
        """A modifier went down or up (or repeated). Feeds the trigger's transitions, and a
        modifier joining that is not part of the trigger."""
        joined = (held - self._held) - self._keys
        self._held = held
        if joined:
            self.key_pressed(OTHER, t)
        active = all(group & held for group in self._groups)
        if active == self._active:
            return
        self._active = active
        if not active:
            self.trigger_released(t)
            return
        if self._uses_win and MASK_START_MENU:
            try:
                self.send_mask_key()
            except Exception:
                log.exception("could not send the key that keeps the Start menu closed")
        self.trigger_pressed(t, alone=held <= self._keys)


def attach(
    keyboard_tap,
    cfg: Mapping,
    on_start: Callable[[], bool | None],
    on_stop: Callable[[], None],
    on_lock: Callable[[], None],
    on_cancel: Callable[[bool], None],
) -> PushToTalk:
    """Build the dictation keys from the config's [dictation] section and hand them to the
    keyboard hook (lyssna.win.events.KeyboardTap). Raises ValueError for a key this system
    does not have; nothing is attached then."""
    section = cfg.get("dictation", {})
    ptt = PushToTalk(
        trigger=str(section.get("hotkey", host.DICTATION_KEY_DEFAULT)), hands_free=str(section.get("hands_free", SPACE)),
        on_start=on_start, on_stop=on_stop, on_lock=on_lock, on_cancel=on_cancel,
        double_tap=bool(section.get("double_tap", host.DICTATION_DOUBLE_TAP_DEFAULT)),
    )
    keyboard_tap.push_to_talk = ptt
    return ptt


# -- Win32 ---------------------------------------------------------------------------------

_SEND = None


def _sender():
    """SendInput and the INPUT structure (with its full union, so its size is what SendInput
    expects), built on first use so this module imports on any system."""
    global _SEND
    if _SEND is not None:
        return _SEND
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    _SEND = (ctypes, INPUT, user32)
    return _SEND


def _send_mask_key() -> None:
    """Press and release MASK_KEY in one SendInput call, marked as Lyssna's own."""
    ctypes, INPUT, user32 = _sender()
    INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002
    inputs = (INPUT * 2)()
    for slot, flags in zip(inputs, (0, KEYEVENTF_KEYUP), strict=True):
        slot.type = INPUT_KEYBOARD
        slot.ki.wVk = MASK_KEY
        slot.ki.dwFlags = flags
        slot.ki.dwExtraInfo = INJECTED_MARK
    sent = user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
    if sent != 2:
        log.info("SendInput sent %d of 2 key events (error %d)", sent, ctypes.get_last_error())
