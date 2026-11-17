import time

import Quartz

from lyssna import events
from lyssna.events import KeyboardTap, Point, SelectionGestureDetector


def make(ignore=None):
    hits = {"select": [], "dismiss": [], "press": []}
    d = SelectionGestureDetector(
        on_select=lambda p: hits["select"].append(p),
        on_dismiss=lambda p: hits["dismiss"].append(p),
        ignore_region=ignore,
        on_press=lambda p: hits["press"].append(p),
    )
    return d, hits


def test_drag_selects():
    d, hits = make()
    d.mouse_down(Point(10, 10))
    time.sleep(0.1)
    d.mouse_dragged(Point(60, 12))
    d.mouse_up(Point(60, 12))
    assert len(hits["select"]) == 1 and hits["select"][0].x == 60
    assert len(hits["dismiss"]) == 1  # the mouse-down dismissed any previous button


def test_plain_click_dismisses_only():
    d, hits = make()
    d.mouse_down(Point(10, 10))
    d.mouse_up(Point(11, 10))
    assert hits["select"] == [] and len(hits["dismiss"]) == 1


def test_double_click_selects():
    d, hits = make()
    d.mouse_down(Point(5, 5), click_state=2)
    d.mouse_up(Point(5, 5))
    assert len(hits["select"]) == 1


def test_clicks_on_our_ui_are_ignored():
    d, hits = make(ignore=lambda p: p.x > 100)
    d.mouse_down(Point(150, 5))
    d.mouse_dragged(Point(200, 5))
    d.mouse_up(Point(200, 5))
    assert hits["select"] == [] and hits["dismiss"] == []


def test_press_fires_on_every_press():
    d, hits = make()
    d.mouse_down(Point(1, 1))
    d.mouse_up(Point(1, 1))
    d.mouse_down(Point(2, 2), click_state=2)
    assert [p.x for p in hits["press"]] == [1, 2]


def test_press_not_fired_on_our_ui():
    d, hits = make(ignore=lambda p: p.x > 100)
    d.mouse_down(Point(150, 5))
    assert hits["press"] == []


# -- KeyboardTap routing (events are built but never posted) ---------------------------------

def key_event(keycode, event_type, flags=0):
    e = Quartz.CGEventCreateKeyboardEvent(None, keycode, event_type == Quartz.kCGEventKeyDown)
    Quartz.CGEventSetType(e, event_type)
    Quartz.CGEventSetFlags(e, flags)
    return e


class FakePTT:
    def __init__(self, swallow=False):
        self.calls = []
        self.swallow = swallow

    def flags_changed(self, keycode, flags, t):
        self.calls.append(("flags", keycode, flags & 0x800000))
        return self.swallow

    def key_down(self, keycode, t):
        self.calls.append(("down", keycode))
        return self.swallow

    def key_up(self, keycode, t):
        self.calls.append(("up", keycode))
        return self.swallow


def inline_spawn(monkeypatch):
    ran = []
    monkeypatch.setattr(events, "spawn", lambda fn, *args, **kw: ran.append(fn))
    return ran


def test_tap_listens_for_key_up_and_modifier_changes():
    for kind in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp, Quartz.kCGEventFlagsChanged):
        assert KeyboardTap.mask & Quartz.CGEventMaskBit(kind)


def test_without_push_to_talk_only_bound_key_presses_are_taken(monkeypatch):
    ran = inline_spawn(monkeypatch)
    tap = KeyboardTap()
    hit = []
    tap.bind("ctrl+alt+s", lambda: hit.append(1))
    tap.on_any_key = lambda: None
    flags = Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskAlternate
    down = key_event(1, Quartz.kCGEventKeyDown, flags)
    assert tap.handle(Quartz.kCGEventKeyDown, down) is None  # swallowed, callback spawned
    assert len(ran) == 2  # on_any_key and the binding
    other = key_event(0, Quartz.kCGEventKeyDown)
    assert tap.handle(Quartz.kCGEventKeyDown, other) is other
    up = key_event(1, Quartz.kCGEventKeyUp, flags)
    assert tap.handle(Quartz.kCGEventKeyUp, up) is up
    fn = key_event(63, Quartz.kCGEventFlagsChanged, 0x800000)
    assert tap.handle(Quartz.kCGEventFlagsChanged, fn) is fn
    assert len(ran) == 3  # key-up and modifier changes reach no callback


def test_push_to_talk_sees_every_kind_of_key_event(monkeypatch):
    inline_spawn(monkeypatch)
    tap = KeyboardTap()
    tap.push_to_talk = ptt = FakePTT()
    fn = key_event(63, Quartz.kCGEventFlagsChanged, 0x800000)
    space = key_event(49, Quartz.kCGEventKeyDown)
    space_up = key_event(49, Quartz.kCGEventKeyUp)
    assert tap.handle(Quartz.kCGEventFlagsChanged, fn) is fn
    assert tap.handle(Quartz.kCGEventKeyDown, space) is space
    assert tap.handle(Quartz.kCGEventKeyUp, space_up) is space_up
    assert ptt.calls == [("flags", 63, 0x800000), ("down", 49), ("up", 49)]


def test_push_to_talk_can_swallow_and_bindings_then_do_not_run(monkeypatch):
    ran = inline_spawn(monkeypatch)
    tap = KeyboardTap()
    hit = []
    tap.bind("space", lambda: hit.append(1))
    tap.push_to_talk = FakePTT(swallow=True)
    assert tap.handle(Quartz.kCGEventKeyDown, key_event(49, Quartz.kCGEventKeyDown)) is None
    assert tap.handle(Quartz.kCGEventKeyUp, key_event(49, Quartz.kCGEventKeyUp)) is None
    assert ran == []  # the space binding was not reached
