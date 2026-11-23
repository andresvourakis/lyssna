"""The Windows keyboard and mouse rules, checked with plain values so they run on any system."""

import sys

import pytest

from lyssna import config
from lyssna.win import events as win_events
from lyssna.win.events import (
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_RWIN,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MBUTTONDOWN,
    WM_MOUSEMOVE,
    WM_RBUTTONDOWN,
    ClickCounter,
    KeyboardTap,
    MouseTap,
    Point,
    SelectionGestureDetector,
)
from lyssna.win.hotkeys import ALT, CTRL, SHIFT, WIN

VK_S = 0x53
VK_A = 0x41


@pytest.fixture
def ran(monkeypatch):
    calls = []
    monkeypatch.setattr(win_events, "spawn", lambda fn, *args, **kw: (calls.append(fn), fn(*args)))
    return calls


def press(tap, vk, t=0.0):
    return tap.key_event(vk, True, False, t)


def release(tap, vk, t=0.0):
    return tap.key_event(vk, False, False, t)


def test_accessibility_is_always_trusted():
    assert win_events.accessibility_trusted(prompt=True) is True


def test_bound_hotkey_fires_once_and_swallows_down_repeat_and_up(ran):
    tap = KeyboardTap()
    fired = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    assert not press(tap, VK_LCONTROL) and not press(tap, VK_LMENU)
    assert press(tap, VK_S, 1.0) is True
    assert press(tap, VK_S, 1.5) is True  # auto-repeat
    assert press(tap, VK_S, 1.53) is True
    assert release(tap, VK_S, 1.6) is True
    assert fired == [1]
    assert not release(tap, VK_LMENU) and not release(tap, VK_LCONTROL)
    # A plain S afterwards is typed normally.
    assert press(tap, VK_S, 2.0) is False and release(tap, VK_S, 2.1) is False


def test_hotkey_whose_key_up_went_missing_fires_again_later(ran):
    tap = KeyboardTap()
    fired = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    press(tap, VK_LCONTROL)
    press(tap, VK_LMENU)
    assert press(tap, VK_S, 1.0)
    assert press(tap, VK_S, 10.0)  # no key-up in between, long after: a new press
    assert fired == [1, 1]


def test_unbound_key_passes_through_and_reaches_on_any_key(ran):
    tap = KeyboardTap()
    seen = []
    tap.on_any_key = lambda: seen.append("any")
    tap.bind("ctrl+alt+s", lambda: seen.append("hotkey"))
    assert press(tap, VK_A) is False
    assert release(tap, VK_A) is False
    assert seen == ["any"]


def test_every_key_down_reaches_on_any_key_including_hotkeys(ran):
    """As on the Mac: a hotkey's key-down reaches on_any_key too (before the hotkey itself), and
    so does each auto-repeat. Modifier keys and key-ups do not."""
    tap = KeyboardTap()
    seen = []
    tap.on_any_key = lambda: seen.append("any")
    tap.bind("ctrl+alt+s", lambda: seen.append("hotkey"))
    assert not press(tap, VK_LCONTROL) and not press(tap, VK_LMENU)
    assert seen == []
    assert press(tap, VK_S, 1.0) is True
    assert seen == ["any", "hotkey"]
    assert press(tap, VK_S, 1.5) is True  # auto-repeat: swallowed, the hotkey does not fire again
    assert release(tap, VK_S, 1.6) is True
    assert not release(tap, VK_LMENU) and not release(tap, VK_LCONTROL)
    assert seen == ["any", "hotkey", "any"]


def test_injected_events_are_ignored(ran):
    tap = KeyboardTap()
    seen = []
    tap.on_any_key = lambda: seen.append("any")
    tap.raw_listener = lambda vk, down, t: seen.append("raw") or True
    tap.bind("ctrl+s", lambda: seen.append("hotkey"))
    assert tap.key_event(VK_LCONTROL, True, True, 0.0) is False
    assert tap.key_event(VK_S, True, True, 0.0) is False
    assert tap.key_event(VK_S, False, True, 0.0) is False
    assert seen == []
    assert tap._held == set()  # an injected Ctrl does not count as held


def test_altgr_does_not_fire_ctrl_alt(ran):
    tap = KeyboardTap()
    fired = []
    seen = []
    tap.on_any_key = lambda: seen.append("any")
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    press(tap, VK_LCONTROL)  # AltGr arrives as left Ctrl plus right Alt
    press(tap, VK_RMENU)
    assert press(tap, VK_S) is False
    assert release(tap, VK_S) is False
    assert fired == [] and seen == ["any"]


def test_right_alt_blocks_every_binding(ran):
    tap = KeyboardTap()
    fired = []
    tap.bind("ctrl+s", lambda: fired.append(1))
    press(tap, VK_RCONTROL)
    press(tap, VK_RMENU)
    assert press(tap, VK_S) is False
    assert fired == []


def test_left_ctrl_and_left_alt_fire(ran):
    tap = KeyboardTap()
    fired = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    press(tap, VK_LCONTROL)
    press(tap, VK_LMENU)
    assert press(tap, VK_S) is True
    assert fired == [1]


def test_right_ctrl_counts_as_ctrl(ran):
    tap = KeyboardTap()
    fired = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    press(tap, VK_RCONTROL)
    press(tap, VK_LMENU)
    assert press(tap, VK_S) is True
    assert fired == [1]


@pytest.mark.parametrize(
    ("left", "right", "flag"),
    [(VK_LCONTROL, VK_RCONTROL, CTRL), (VK_LSHIFT, VK_RSHIFT, SHIFT), (VK_LWIN, VK_RWIN, WIN)],
)
def test_modifier_state_follows_downs_and_ups(left, right, flag):
    tap = KeyboardTap()
    for vk in (left, right):
        tap.key_event(vk, True, False, 0.0)
        assert win_events.modifier_flags(tap._held) == flag
        tap.key_event(vk, False, False, 0.0)
        assert win_events.modifier_flags(tap._held) == 0
    tap.key_event(left, True, False, 0.0)
    tap.key_event(right, True, False, 0.0)
    tap.key_event(left, False, False, 0.0)
    assert win_events.modifier_flags(tap._held) == flag  # the right one is still held
    tap.key_event(right, False, False, 0.0)
    assert tap._held == set()


def test_alt_state_left_counts_right_blocks():
    tap = KeyboardTap()
    tap.key_event(VK_LMENU, True, False, 0.0)
    assert win_events.modifier_flags(tap._held) == ALT
    tap.key_event(VK_LMENU, False, False, 0.0)
    tap.key_event(VK_RMENU, True, False, 0.0)
    assert win_events.modifier_flags(tap._held) is None
    tap.key_event(VK_RMENU, False, False, 0.0)
    assert win_events.modifier_flags(tap._held) == 0


def test_modifiers_resync_from_the_system_on_a_key_down(ran):
    tap = KeyboardTap()
    fired = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    press(tap, VK_LCONTROL)
    press(tap, VK_LMENU)
    # Both were released while an elevated window had focus; the hook never saw it.
    tap.modifiers_now = lambda: set()
    assert press(tap, VK_S) is False
    assert fired == []
    tap.modifiers_now = lambda: {VK_LCONTROL, VK_LMENU}
    assert press(tap, VK_S, 5.0) is True
    assert fired == [1]


def test_raw_listener_sees_everything_first_and_can_swallow(ran):
    tap = KeyboardTap()
    fired = []
    events = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))

    def raw(vk, down, t):
        events.append((vk, down))
        return vk == VK_S

    tap.raw_listener = raw
    assert press(tap, VK_LCONTROL) is False
    assert press(tap, VK_LMENU) is False
    assert press(tap, VK_S) is True
    assert release(tap, VK_S) is True
    assert fired == []  # the raw listener took it before the bindings
    assert events == [(VK_LCONTROL, True), (VK_LMENU, True), (VK_S, True), (VK_S, False)]
    assert tap.push_to_talk is None


class RecordingPTT:
    """Stands in for lyssna.win.pushtotalk.PushToTalk: records what the tap hands it."""

    def __init__(self, swallow=True):
        self.calls = []
        self.swallow = swallow

    def key_event(self, vk, down, held, t):
        self.calls.append((vk, down, held))
        return self.swallow


def test_push_to_talk_sees_every_key_with_the_held_modifiers_but_never_swallows_one(ran):
    tap = KeyboardTap()
    tap.push_to_talk = ptt = RecordingPTT(swallow=True)
    assert press(tap, VK_LCONTROL) is False  # a modifier always goes through
    assert press(tap, VK_LWIN) is False
    assert press(tap, 0x20) is True  # a key it takes is swallowed, down and up
    assert release(tap, 0x20) is True
    assert release(tap, VK_LWIN) is False
    assert ptt.calls == [
        (VK_LCONTROL, True, frozenset({VK_LCONTROL})),
        (VK_LWIN, True, frozenset({VK_LCONTROL, VK_LWIN})),
        (0x20, True, frozenset({VK_LCONTROL, VK_LWIN})),
        (0x20, False, frozenset({VK_LCONTROL, VK_LWIN})),
        (VK_LWIN, False, frozenset({VK_LCONTROL})),
    ]


def test_push_to_talk_returning_false_lets_bindings_run(ran):
    tap = KeyboardTap()
    fired = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    tap.push_to_talk = RecordingPTT(swallow=False)
    press(tap, VK_LCONTROL)
    press(tap, VK_LMENU)
    assert press(tap, VK_S) is True
    assert fired == [1]


def test_a_modifier_press_resyncs_the_held_keys_only_with_push_to_talk(ran):
    tap = KeyboardTap()
    press(tap, VK_LCONTROL)
    tap.modifiers_now = lambda: set()  # left Ctrl was let go where the hook could not see it
    press(tap, VK_LWIN)
    assert tap._held == {VK_LCONTROL, VK_LWIN}  # without push-to-talk nothing changes here
    tap = KeyboardTap()
    tap.push_to_talk = ptt = RecordingPTT(swallow=False)
    press(tap, VK_LCONTROL)
    tap.modifiers_now = lambda: set()
    press(tap, VK_LWIN)
    assert tap._held == {VK_LWIN} and ptt.calls[-1] == (VK_LWIN, True, frozenset({VK_LWIN}))


def test_raw_listener_returning_false_lets_bindings_run(ran):
    tap = KeyboardTap()
    fired = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    tap.raw_listener = lambda vk, down, t: False
    press(tap, VK_LCONTROL)
    press(tap, VK_LMENU)
    assert press(tap, VK_S) is True
    assert fired == [1]


@pytest.mark.parametrize("spec", ["ctrl+alt+nope", "ctrl+alt", ""])
def test_bad_spec_raises(spec):
    with pytest.raises(ValueError):
        KeyboardTap().bind(spec, lambda: None)


def test_default_hotkeys_all_bind_with_the_windows_table():
    tap = KeyboardTap()
    for spec in config.DEFAULTS["hotkeys"].values():
        tap.bind(spec, lambda: None)
    assert len(tap._bindings) == 9
    assert (CTRL | ALT, VK_S) in tap._bindings
    assert (CTRL | ALT, 0x20) in tap._bindings  # space
    assert (CTRL | ALT, 0x27) in tap._bindings  # right arrow
    assert (CTRL | ALT, 0x56) in tap._bindings  # V, paste last dictation


@pytest.mark.skipif(sys.platform == "win32", reason="checks the behaviour off Windows")
def test_start_returns_false_off_windows():
    assert KeyboardTap().start() is False
    d = SelectionGestureDetector(on_select=lambda p: None, on_dismiss=lambda p: None)
    assert MouseTap(d).start() is False


# -- clicks ----------------------------------------------------------------------------------


def test_click_counter_single_double_triple_and_cap():
    c = ClickCounter(interval=0.5, width=4, height=4)
    assert c.press(100, 100, 0.0) == 1
    assert c.press(101, 99, 0.3) == 2
    assert c.press(100, 100, 0.6) == 3
    assert c.press(100, 100, 0.8) == 3  # capped


def test_click_counter_too_slow():
    c = ClickCounter(interval=0.5, width=4, height=4)
    c.press(100, 100, 0.0)
    assert c.press(100, 100, 0.51) == 1


def test_click_counter_too_far():
    c = ClickCounter(interval=0.5, width=4, height=4)
    c.press(100, 100, 0.0)
    assert c.press(103, 100, 0.1) == 1  # outside the 4 pixel wide rectangle centred on the first press
    assert c.press(103, 103, 0.2) == 1


def test_click_counter_reset():
    c = ClickCounter()
    c.press(0, 0, 0.0)
    c.reset()
    assert c.press(0, 0, 0.1) == 1


# -- mouse -----------------------------------------------------------------------------------


class FakeDetector:
    def __init__(self):
        self.calls = []

    def mouse_down(self, p, click_state=1):
        self.calls.append(("down", p, click_state))

    def mouse_dragged(self, p):
        self.calls.append(("drag", p))

    def mouse_up(self, p):
        self.calls.append(("up", p))

    def other_click(self, p):
        self.calls.append(("other", p))


def test_mouse_down_drag_up():
    d = FakeDetector()
    tap = MouseTap(d)
    tap.mouse_event(WM_LBUTTONDOWN, 10, 20, 0.0)
    tap.mouse_event(WM_MOUSEMOVE, 30, 20, 0.01)
    tap.mouse_event(WM_LBUTTONUP, 40, 21, 0.02)
    assert d.calls == [("down", Point(10, 20), 1), ("drag", Point(30, 20)), ("up", Point(40, 21))]


def test_mouse_moves_with_no_button_down_are_ignored():
    d = FakeDetector()
    tap = MouseTap(d)
    tap.mouse_event(WM_MOUSEMOVE, 5, 5, 0.0)
    tap.mouse_event(WM_LBUTTONDOWN, 5, 5, 0.1)
    tap.mouse_event(WM_LBUTTONUP, 5, 5, 0.2)
    tap.mouse_event(WM_MOUSEMOVE, 50, 50, 0.3)
    assert [c[0] for c in d.calls] == ["down", "up"]


def test_right_and_middle_clicks_are_other_clicks():
    d = FakeDetector()
    tap = MouseTap(d)
    tap.mouse_event(WM_RBUTTONDOWN, -100, 5, 0.0)  # a monitor left of the primary one
    tap.mouse_event(WM_MBUTTONDOWN, 1, 2, 0.1)
    assert d.calls == [("other", Point(-100, 5)), ("other", Point(1, 2))]


def test_double_click_reaches_the_detector_as_click_state_two():
    d = FakeDetector()
    tap = MouseTap(d)
    for t in (0.0, 0.2):
        tap.mouse_event(WM_LBUTTONDOWN, 7, 7, t)
        tap.mouse_event(WM_LBUTTONUP, 7, 7, t + 0.05)
    assert [c[2] for c in d.calls if c[0] == "down"] == [1, 2]


def test_mouse_feeds_a_real_detector_to_a_selection():
    hits = {"select": [], "dismiss": []}
    det = SelectionGestureDetector(on_select=hits["select"].append, on_dismiss=hits["dismiss"].append)
    tap = MouseTap(det)
    tap.mouse_event(WM_LBUTTONDOWN, 10, 10, 0.0)
    tap.mouse_event(WM_MOUSEMOVE, 80, 12, 0.1)
    tap.mouse_event(WM_LBUTTONUP, 80, 12, 0.2)
    assert hits["select"] == [Point(80, 12)] and len(hits["dismiss"]) == 1
