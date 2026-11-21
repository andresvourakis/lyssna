"""The PC's dictation keys, fed virtual-key codes through the real keyboard hook rules
(lyssna.win.events.KeyboardTap.key_event), so this runs on any system. The key that keeps
the Start menu closed is recorded instead of sent."""

import pytest

from lyssna.win import events as win_events
from lyssna.win import pushtotalk
from lyssna.win.events import (
    MODIFIER_VKS,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_RCONTROL,
    VK_RMENU,
    VK_RWIN,
    KeyboardTap,
)
from lyssna.win.pushtotalk import VK_ESCAPE, VK_SPACE, PushToTalk

VK_A = 0x41
VK_C = 0x43
VK_LEFT = 0x25
VK_S = 0x53


@pytest.fixture(autouse=True)
def inline_spawn(monkeypatch):
    monkeypatch.setattr(win_events, "spawn", lambda fn, *args, **kw: fn(*args))


def make(trigger="ctrl+win", refuse=False, double_tap=False):
    calls = []

    def start():
        calls.append("start")
        return False if refuse else None

    tap = KeyboardTap()
    cfg = {"dictation": {"hotkey": trigger, "hands_free": "space", "double_tap": double_tap}}
    ptt = pushtotalk.attach(
        tap, cfg, start, lambda: calls.append("stop"), lambda: calls.append("lock"),
        lambda quiet: calls.append("cancel quiet" if quiet else "cancel"),
    )
    ptt.send_mask_key = lambda: calls.append("mask")
    return tap, ptt, calls


def down(tap, vk, t=0.0):
    return tap.key_event(vk, True, False, t)


def up(tap, vk, t=0.0):
    return tap.key_event(vk, False, False, t)


def chord_down(tap, t=0.0, first=VK_LCONTROL, second=VK_LWIN):
    assert down(tap, first, t) is False
    assert down(tap, second, t) is False


def chord_up(tap, t=1.0, first=VK_LWIN, second=VK_LCONTROL):
    assert up(tap, first, t) is False
    assert up(tap, second, t) is False


# -- Ctrl+Win ------------------------------------------------------------------------------


def test_ctrl_win_hold_and_release_dictates():
    tap, ptt, calls = make()
    chord_down(tap, 0.0)
    assert calls == ["mask", "start"] and ptt.state == "holding"
    chord_up(tap, 1.0)
    assert calls == ["mask", "start", "stop"] and ptt.state == "idle"


@pytest.mark.parametrize("first", [VK_LCONTROL, VK_RCONTROL, VK_LWIN, VK_RWIN])
@pytest.mark.parametrize("release_first", ["ctrl", "win"])
def test_either_key_of_the_chord_can_go_first_and_either_can_let_go_first(first, release_first):
    tap, _ptt, calls = make()
    ctrl = first if first in (VK_LCONTROL, VK_RCONTROL) else VK_RCONTROL
    win = first if first in (VK_LWIN, VK_RWIN) else VK_LWIN
    second = win if first == ctrl else ctrl
    down(tap, first, 0.0)
    assert calls == []  # one key of the chord is not the chord
    down(tap, second, 0.1)
    assert calls == ["mask", "start"]
    let_go = ctrl if release_first == "ctrl" else win
    up(tap, let_go, 1.0)
    assert calls == ["mask", "start", "stop"]  # either key let go ends it
    up(tap, win if let_go == ctrl else ctrl, 1.1)
    assert calls == ["mask", "start", "stop"]


def test_modifier_repeats_do_nothing():
    tap, _ptt, calls = make()
    chord_down(tap, 0.0)
    for t in (0.5, 0.55, 0.6):
        down(tap, VK_LCONTROL, t)
        down(tap, VK_LWIN, t)
    chord_up(tap, 1.0)
    assert calls == ["mask", "start", "stop"]


def test_ctrl_win_space_locks_and_space_is_swallowed():
    tap, ptt, calls = make()
    chord_down(tap, 0.0)
    assert down(tap, VK_SPACE, 0.5) is True
    assert down(tap, VK_SPACE, 0.55) is True  # auto-repeat
    chord_up(tap, 0.7)  # letting go keeps recording
    assert up(tap, VK_SPACE, 0.8) is True
    assert calls == ["mask", "start", "lock"] and ptt.state == "locked"
    assert down(tap, VK_A, 1.0) is False  # typing while hands-free goes through
    assert up(tap, VK_A, 1.1) is False


def test_the_chord_again_finishes_a_hands_free_dictation():
    tap, ptt, calls = make()
    chord_down(tap, 0.0)
    down(tap, VK_SPACE, 0.5)
    up(tap, VK_SPACE, 0.6)
    chord_up(tap, 0.7)
    chord_down(tap, 5.0, VK_LWIN, VK_RCONTROL)
    chord_up(tap, 5.1, VK_RCONTROL, VK_LWIN)
    assert calls == ["mask", "start", "lock", "mask", "stop"] and ptt.state == "idle"


def test_escape_cancels_a_hands_free_dictation_and_is_swallowed():
    tap, ptt, calls = make()
    chord_down(tap, 0.0)
    down(tap, VK_SPACE, 0.5)
    up(tap, VK_SPACE, 0.6)
    chord_up(tap, 0.7)
    assert down(tap, VK_ESCAPE, 2.0) is True
    assert up(tap, VK_ESCAPE, 2.1) is True
    assert calls == ["mask", "start", "lock", "cancel"] and ptt.state == "idle"
    assert down(tap, VK_ESCAPE, 3.0) is False  # idle: Escape belongs to the app again


def test_space_is_swallowed_whenever_ctrl_win_is_held():
    """Ctrl+Win+Space switches the input language, so it never reaches Windows, even when no
    dictation started (here the session refused it)."""
    tap, ptt, calls = make(refuse=True)
    chord_down(tap, 0.0)
    assert ptt.state == "idle"
    assert down(tap, VK_SPACE, 0.5) is True
    assert down(tap, VK_SPACE, 0.55) is True
    assert up(tap, VK_SPACE, 0.6) is True
    chord_up(tap, 1.0)
    assert down(tap, VK_SPACE, 2.0) is False  # the chord is gone: Space types again
    assert up(tap, VK_SPACE, 2.1) is False
    assert calls == ["mask", "start"]


def test_space_after_finishing_while_the_chord_is_still_held_is_swallowed():
    tap, ptt, calls = make()
    ptt.enter_locked()
    chord_down(tap, 1.0)
    assert calls == ["mask", "stop"]
    assert down(tap, VK_SPACE, 1.2) is True
    assert up(tap, VK_SPACE, 1.3) is True


def test_another_key_while_holding_cancels_and_goes_through():
    """Ctrl+Win+Left switches the desktop: it must still work."""
    tap, _ptt, calls = make()
    chord_down(tap, 0.0)
    assert down(tap, VK_LEFT, 0.3) is False
    assert up(tap, VK_LEFT, 0.4) is False
    chord_up(tap, 1.0)
    assert calls == ["mask", "start", "cancel quiet"]


def test_another_modifier_joining_cancels():
    tap, _ptt, calls = make()
    chord_down(tap, 0.0)
    assert down(tap, VK_LSHIFT, 0.3) is False
    assert calls == ["mask", "start", "cancel quiet"]
    up(tap, VK_LSHIFT, 0.4)
    chord_up(tap, 1.0)
    assert calls == ["mask", "start", "cancel quiet"]


def test_the_chord_with_shift_already_held_does_not_start():
    tap, _ptt, calls = make()
    down(tap, VK_LSHIFT, 0.0)
    chord_down(tap, 0.1)
    chord_up(tap, 1.0)
    assert calls == ["mask"]


def test_a_quick_tap_cancels_quietly():
    tap, _ptt, calls = make()
    chord_down(tap, 0.0)
    chord_up(tap, 0.1)
    assert calls == ["mask", "start", "cancel quiet"]


def test_two_quick_taps_of_ctrl_win_lock():
    tap, ptt, calls = make(double_tap=True)
    chord_down(tap, 0.0)
    chord_up(tap, 0.1)
    chord_down(tap, 0.4)
    chord_up(tap, 0.5)
    assert calls == ["mask", "start", "cancel quiet", "mask", "start", "lock"] and ptt.state == "locked"
    chord_down(tap, 3.0)
    chord_up(tap, 3.1)
    assert calls[-1] == "stop" and ptt.state == "idle"


def test_a_tap_then_a_hold_of_ctrl_win_dictates_normally():
    tap, _ptt, calls = make(double_tap=True)
    chord_down(tap, 0.0)
    chord_up(tap, 0.1)
    chord_down(tap, 0.3)
    chord_up(tap, 2.0)
    assert calls == ["mask", "start", "cancel quiet", "mask", "start", "stop"]


def test_the_mask_key_goes_once_per_chord_and_can_be_turned_off(monkeypatch):
    tap, _ptt, calls = make()
    chord_down(tap, 0.0)
    down(tap, VK_LWIN, 0.5)  # repeats
    down(tap, VK_LCONTROL, 0.5)
    chord_up(tap, 1.0)
    assert calls.count("mask") == 1
    monkeypatch.setattr(pushtotalk, "MASK_START_MENU", False)
    calls.clear()
    chord_down(tap, 2.0)
    chord_up(tap, 3.0)
    assert calls == ["start", "stop"]


def test_a_failing_mask_key_does_not_stop_the_dictation():
    tap, ptt, calls = make()

    def broken():
        raise OSError("SendInput failed")

    ptt.send_mask_key = broken
    chord_down(tap, 0.0)
    assert calls == ["start"]


def test_altgr_never_starts_a_dictation():
    """AltGr arrives as left Ctrl plus right Alt; typing @ with it must stay just typing."""
    tap, ptt, calls = make()
    down(tap, VK_LCONTROL, 0.0)
    down(tap, VK_RMENU, 0.0)
    assert down(tap, VK_A, 0.1) is False
    up(tap, VK_A, 0.2)
    down(tap, VK_LWIN, 0.3)  # even with a Windows key added, right Alt is held
    assert down(tap, VK_SPACE, 0.4) is True  # Ctrl+Win is held, so Space is still kept from Windows
    up(tap, VK_SPACE, 0.5)
    for vk in (VK_LWIN, VK_RMENU, VK_LCONTROL):
        up(tap, vk, 1.0)
    assert "start" not in calls and ptt.state == "idle"


def test_altgr_with_right_ctrl_as_the_key_never_starts():
    tap, _ptt, calls = make(trigger="right_control")
    down(tap, VK_LCONTROL, 0.0)
    down(tap, VK_RMENU, 0.0)
    down(tap, VK_A, 0.1)
    for vk in (VK_A, VK_RMENU, VK_LCONTROL):
        up(tap, vk, 1.0)
    assert calls == []


def test_ctrl_alone_or_win_alone_is_not_the_chord():
    tap, _ptt, calls = make()
    for vk in (VK_LCONTROL, VK_RCONTROL, VK_LWIN, VK_RWIN):
        down(tap, vk, 0.0)
        up(tap, vk, 1.0)
    assert calls == []


def test_modifiers_are_never_swallowed():
    tap, ptt, _calls = make()
    seq = [(VK_LCONTROL, True), (VK_LWIN, True), (VK_LSHIFT, True), (VK_LSHIFT, False), (VK_LWIN, False),
           (VK_LCONTROL, False), (VK_LCONTROL, True), (VK_LWIN, True), (VK_LWIN, False), (VK_LCONTROL, False)]
    for i, (vk, is_down) in enumerate(seq):
        assert tap.key_event(vk, is_down, False, i * 0.4) is False
    ptt.enter_locked()
    for vk in MODIFIER_VKS:
        assert down(tap, vk, 10.0) is False and up(tap, vk, 10.1) is False


def test_injected_events_are_ignored():
    tap, ptt, calls = make()
    for vk in (VK_LCONTROL, VK_LWIN):
        assert tap.key_event(vk, True, True, 0.0) is False
    assert tap.key_event(pushtotalk.MASK_KEY, True, True, 0.0) is False
    assert tap.key_event(VK_SPACE, True, True, 0.1) is False
    assert calls == [] and ptt.state == "idle"


def test_a_stuck_ctrl_is_resynced_before_win_counts_as_the_chord():
    """Ctrl was let go while an elevated window had focus; the hook never saw it. A lone Win
    press must not look like Ctrl+Win (and must not have the Start menu masked)."""
    tap, _ptt, calls = make()
    down(tap, VK_LCONTROL, 0.0)
    tap.modifiers_now = lambda: set()  # Windows says nothing is held now
    down(tap, VK_LWIN, 5.0)
    up(tap, VK_LWIN, 5.1)
    assert calls == []
    tap.modifiers_now = lambda: {VK_LCONTROL}  # held for real this time
    down(tap, VK_LWIN, 6.0)
    assert calls == ["mask", "start"]


def test_the_reading_hotkeys_still_work_with_dictation_attached():
    tap, _ptt, calls = make()
    fired = []
    tap.bind("ctrl+alt+s", lambda: fired.append(1))
    down(tap, VK_LCONTROL, 0.0)
    down(tap, VK_LMENU, 0.0)
    assert down(tap, VK_S, 0.1) is True
    assert up(tap, VK_S, 0.2) is True
    assert fired == [1] and calls == []


# -- right Ctrl ------------------------------------------------------------------------------


def test_right_ctrl_hold_dictates_and_space_locks():
    tap, _ptt, calls = make(trigger="right_control")
    down(tap, VK_RCONTROL, 0.0)
    assert calls == ["start"]  # no Windows key, no mask key
    assert down(tap, VK_SPACE, 0.5) is True
    up(tap, VK_RCONTROL, 0.6)
    assert up(tap, VK_SPACE, 0.7) is True
    assert calls == ["start", "lock"]
    down(tap, VK_RCONTROL, 2.0)
    assert calls == ["start", "lock", "stop"]
    up(tap, VK_RCONTROL, 2.1)


def test_right_ctrl_hold_and_release_dictates():
    tap, _ptt, calls = make(trigger="right_control")
    down(tap, VK_RCONTROL, 0.0)
    down(tap, VK_RCONTROL, 0.5)  # repeat
    up(tap, VK_RCONTROL, 1.0)
    assert calls == ["start", "stop"]


def test_right_ctrl_as_a_modifier_cancels_and_the_shortcut_goes_through():
    tap, _ptt, calls = make(trigger="right_control")
    fired = []
    tap.bind("ctrl+c", lambda: fired.append(1))
    down(tap, VK_RCONTROL, 0.0)
    assert down(tap, VK_C, 0.1) is True  # the binding takes it, as it would without dictation
    up(tap, VK_C, 0.2)
    up(tap, VK_RCONTROL, 1.0)
    assert calls == ["start", "cancel quiet"] and fired == [1]


def test_right_ctrl_with_left_ctrl_held_does_not_start():
    tap, _ptt, calls = make(trigger="right_control")
    down(tap, VK_LCONTROL, 0.0)
    down(tap, VK_RCONTROL, 0.1)
    up(tap, VK_RCONTROL, 1.0)
    assert calls == []


def test_right_ctrl_space_is_typed_when_nothing_records():
    tap, _ptt, _calls = make(trigger="right_control", refuse=True)
    down(tap, VK_RCONTROL, 0.0)
    assert down(tap, VK_SPACE, 0.1) is False  # only Ctrl+Win keeps Space from Windows
    assert up(tap, VK_SPACE, 0.2) is False


def test_left_ctrl_alone_is_not_right_ctrl():
    tap, _ptt, calls = make(trigger="right_control")
    down(tap, VK_LCONTROL, 0.0)
    up(tap, VK_LCONTROL, 1.0)
    assert calls == []


# -- the keys this system offers -------------------------------------------------------------


def test_the_pc_offers_ctrl_win_and_right_ctrl():
    assert list(pushtotalk.TRIGGERS) == ["ctrl+win", "right_control"]
    with pytest.raises(ValueError):
        PushToTalk(trigger="fn")
    with pytest.raises(ValueError):
        PushToTalk(hands_free="tab")
    tap = KeyboardTap()
    noop = lambda *a: None  # noqa: E731
    with pytest.raises(ValueError):
        pushtotalk.attach(tap, {"dictation": {"hotkey": "right_option"}}, noop, noop, noop, noop)
    assert tap.push_to_talk is None
