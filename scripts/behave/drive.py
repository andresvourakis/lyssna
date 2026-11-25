"""Real mouse and keyboard input, posted as Quartz events at the HID level, so the window server
and every app see them as they would see a person's. Points are global, top-left origin, in
points (the Quartz convention; the app's `ui` reply is Mac-style, y up, see to_top_left).

Every function records when its event was posted (time.time(), the clock the app's log uses
too), so a scenario can line its events up with the frames and the log."""

from __future__ import annotations

import time

import Quartz

HID = Quartz.kCGHIDEventTap
_held_keys: set[int] = set()
_button_down = False

# Virtual key codes (Carbon's kVK_ values, HIToolbox/Events.h).
KEY = {
    "a": 0x00, "s": 0x01, "v": 0x09, "b": 0x0B, "x": 0x07, "c": 0x08, "space": 0x31, "escape": 0x35, "return": 0x24,
    "left": 0x7B, "right": 0x7C, "delete": 0x33, "down": 0x7D, "up": 0x7E, "=": 0x18, "-": 0x1B, "tab": 0x30,
    "command": 0x37, "shift": 0x38, "option": 0x3A, "control": 0x3B, "fn": 0x3F,
}
MODIFIER_CODES = (0x36, 0x37, 0x38, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E)  # right and left Command, Shift, Option, Control
FLAGS = {"command": Quartz.kCGEventFlagMaskCommand, "shift": Quartz.kCGEventFlagMaskShift,
         "option": Quartz.kCGEventFlagMaskAlternate, "control": Quartz.kCGEventFlagMaskControl}


def _source():
    return Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)


def display_size() -> tuple[float, float]:
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    return float(b.size.width), float(b.size.height)


def to_top_left(frame: dict) -> tuple[float, float, float, float]:
    """A Mac-style frame from the app's `ui` reply ({x, y, w, h}, y up from the bottom of the main
    display) as (x, y, w, h) with y down from the top."""
    _, height = display_size()
    return frame["x"], height - frame["y"] - frame["h"], frame["w"], frame["h"]


def cursor() -> tuple[float, float]:
    p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return float(p.x), float(p.y)


def move(x: float, y: float) -> float:
    kind = Quartz.kCGEventLeftMouseDragged if _button_down else Quartz.kCGEventMouseMoved
    ev = Quartz.CGEventCreateMouseEvent(_source(), kind, (x, y), Quartz.kCGMouseButtonLeft)
    t = time.time()
    Quartz.CGEventPost(HID, ev)
    return t


def glide(x0: float, y0: float, x1: float, y1: float, speed: float = 800.0, hz: float = 125.0) -> list[tuple[float, float, float]]:
    """Move in a straight line at `speed` points a second, one event every 1/hz seconds, as a mouse
    reporting at that rate does. Returns (time, x, y) for every event."""
    dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    steps = max(1, round(dist / speed * hz))
    out = []
    start = time.monotonic()
    for i in range(1, steps + 1):
        f = i / steps
        x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        out.append((move(x, y), x, y))
        wait = start + i / hz - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    return out


def button(down: bool, x: float, y: float, clicks: int = 1) -> float:
    global _button_down
    kind = Quartz.kCGEventLeftMouseDown if down else Quartz.kCGEventLeftMouseUp
    ev = Quartz.CGEventCreateMouseEvent(_source(), kind, (x, y), Quartz.kCGMouseButtonLeft)
    Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, clicks)
    t = time.time()
    Quartz.CGEventPost(HID, ev)
    _button_down = down
    return t


def click(x: float, y: float, clicks: int = 1, settle: float = 0.05) -> float:
    """Move there, then press and release (`clicks` > 1: a double or triple click)."""
    move(x, y)
    time.sleep(settle)
    t = 0.0
    for n in range(1, clicks + 1):
        t = button(True, x, y, n)
        time.sleep(0.03)
        button(False, x, y, n)
        time.sleep(0.06)
    return t


def drag(points: list[tuple[float, float]], hz: float = 125.0, hold: float = 0.15,
         speed: float = 600.0) -> list[tuple[float, float, float]]:
    """Press at the first point, glide through the rest, release at the last."""
    x, y = points[0]
    move(x, y)
    time.sleep(0.05)
    button(True, x, y)
    time.sleep(hold)
    trail = []
    for nx, ny in points[1:]:
        trail += glide(x, y, nx, ny, speed=speed, hz=hz)
        x, y = nx, ny
    time.sleep(hold)
    button(False, x, y)
    return trail


def _post_key(code: int, down: bool, flags: int) -> None:
    ev = Quartz.CGEventCreateKeyboardEvent(_source(), code, down)
    Quartz.CGEventSetFlags(ev, flags)
    Quartz.CGEventPost(HID, ev)
    (_held_keys.add if down else _held_keys.discard)(code)


def key(name: str, mods: tuple[str, ...] = (), down: bool | None = None) -> float:
    """Press and release a key (or only press or release it with `down`). With modifiers it is a
    chord: an event carrying a modifier flag without its key going up afterwards leaves the
    system believing the modifier is held (seen 2026-11-25: Command stuck after Command+A, every
    later keystroke a shortcut), so the modifiers go down and up as keys."""
    if mods and down is None:
        return chord(name, mods)
    code = KEY[name]
    t = time.time()
    for state in ((True, False) if down is None else (down,)):
        _post_key(code, state, 0)
        time.sleep(0.02)
    return t


def chord(name: str, mods: tuple[str, ...]) -> float:
    """A shortcut as a person types it: the modifiers go down one by one, the key, then all up,
    the last of them with no flag left."""
    flags = 0
    for m in mods:
        flags |= FLAGS[m]
        _post_key(KEY[m], True, flags)
        time.sleep(0.02)
    t = time.time()
    for state in (True, False):
        _post_key(KEY[name], state, flags)
        time.sleep(0.02)
    for m in reversed(mods):
        flags &= ~FLAGS[m]
        _post_key(KEY[m], False, flags)
        time.sleep(0.02)
    return t


def type_text(text: str, every: float = 0.03) -> float:
    """Type `text` one character at a time, each as a key press carrying that character (the way
    the system's own character viewer types), so no keyboard layout is assumed."""
    t = time.time()
    for ch in text:
        for state in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(_source(), 0, state)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch.encode("utf-16-le")) // 2, ch)
            Quartz.CGEventPost(HID, ev)
        time.sleep(every)
    return t


def release_all() -> None:
    """Let go of any key and the mouse button this module pressed, and of the left button if the
    window server still thinks it is held."""
    global _button_down
    for code in list(_held_keys):
        _post_key(code, False, 0)
    _held_keys.clear()
    state = Quartz.kCGEventSourceStateHIDSystemState
    for code in MODIFIER_CODES:  # one the system still believes held, whoever pressed it last
        if Quartz.CGEventSourceKeyState(state, code):
            _post_key(code, False, 0)
    state = Quartz.kCGEventSourceStateCombinedSessionState
    if _button_down or Quartz.CGEventSourceButtonState(state, Quartz.kCGMouseButtonLeft):
        x, y = cursor()
        Quartz.CGEventPost(HID, Quartz.CGEventCreateMouseEvent(_source(), Quartz.kCGEventLeftMouseUp, (x, y),
                                                              Quartz.kCGMouseButtonLeft))
    _button_down = False
