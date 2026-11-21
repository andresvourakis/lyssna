"""The push-to-talk rules, fed abstract key events and times: no keyboard, no clock, no system.
The Mac's and the PC's raw keys are checked in test_mac_pushtotalk.py and test_win_pushtotalk.py."""

import pytest

from lyssna.dictation.hotkey import ESCAPE, OTHER, SPACE, PushToTalk


def make(refuse=False, double_tap=False):
    calls = []

    def start():
        calls.append("start")
        return False if refuse else None

    ptt = PushToTalk(
        on_start=start,
        on_stop=lambda: calls.append("stop"),
        on_lock=lambda: calls.append("lock"),
        on_cancel=lambda quiet: calls.append("cancel quiet" if quiet else "cancel"),
        double_tap=double_tap,
    )
    return ptt, calls


def lock(ptt):
    ptt.trigger_pressed(0.0)
    ptt.key_pressed(SPACE, 0.5)
    ptt.key_released(SPACE, 0.6)
    ptt.trigger_released(0.7)


def test_hold_and_release_starts_and_stops():
    ptt, calls = make()
    assert ptt.trigger_pressed(0.0) is False  # trigger events are never swallowed
    assert calls == ["start"] and ptt.state == "holding"
    assert ptt.trigger_released(1.0) is False
    assert calls == ["start", "stop"] and ptt.state == "idle"


def test_a_quick_tap_cancels_quietly():
    ptt, calls = make()
    ptt.trigger_pressed(0.0)
    ptt.trigger_released(0.2)
    assert calls == ["start", "cancel quiet"] and ptt.state == "idle"


def test_another_key_while_holding_cancels_and_goes_through():
    ptt, calls = make()
    ptt.trigger_pressed(0.0)
    assert ptt.key_pressed(OTHER, 0.5) is False
    assert ptt.key_released(OTHER, 0.6) is False
    ptt.trigger_released(1.0)
    assert calls == ["start", "cancel quiet"]  # the release afterwards does nothing


def test_escape_while_holding_is_just_another_key():
    ptt, calls = make()
    ptt.trigger_pressed(0.0)
    assert ptt.key_pressed(ESCAPE, 0.5) is False
    assert ptt.key_released(ESCAPE, 0.6) is False
    assert calls == ["start", "cancel quiet"]


def test_space_while_holding_locks_and_is_swallowed():
    ptt, calls = make()
    ptt.trigger_pressed(0.0)
    assert ptt.key_pressed(SPACE, 0.5) is True
    assert ptt.key_pressed(SPACE, 0.6) is True  # auto-repeat of the same press
    ptt.trigger_released(0.7)  # letting go of the trigger keeps recording
    assert ptt.key_released(SPACE, 0.8) is True
    assert calls == ["start", "lock"] and ptt.state == "locked"
    assert ptt.key_pressed(SPACE, 2.0) is False  # a later Space is typed normally
    assert ptt.key_released(SPACE, 2.1) is False


def test_locked_trigger_press_stops_and_its_release_does_nothing():
    ptt, calls = make()
    lock(ptt)
    ptt.trigger_pressed(5.0)
    ptt.trigger_released(5.1)
    assert calls == ["start", "lock", "stop"] and ptt.state == "idle"


def test_locked_escape_cancels_with_notice_and_is_swallowed():
    ptt, calls = make()
    lock(ptt)
    assert ptt.key_pressed(OTHER, 1.0) is False  # typing while hands-free goes through
    assert ptt.key_pressed(ESCAPE, 2.0) is True
    assert ptt.key_released(ESCAPE, 2.1) is True
    assert calls == ["start", "lock", "cancel"] and ptt.state == "idle"
    assert ptt.key_pressed(ESCAPE, 3.0) is False  # idle: Escape belongs to the app again


def test_idle_swallows_nothing():
    ptt, calls = make()
    for key in (SPACE, ESCAPE, OTHER):
        assert ptt.key_pressed(key, 0.0) is False
        assert ptt.key_released(key, 0.1) is False
    assert calls == []


def test_repeated_trigger_events_while_holding_are_ignored():
    ptt, calls = make()
    ptt.trigger_pressed(0.0)
    ptt.trigger_pressed(0.3)
    ptt.trigger_pressed(0.6)
    ptt.trigger_released(1.0)
    ptt.trigger_released(1.1)
    assert calls == ["start", "stop"]


def test_trigger_with_another_modifier_already_down_does_not_start():
    ptt, calls = make()
    ptt.trigger_pressed(0.0, alone=False)
    ptt.trigger_released(1.0)
    assert calls == []
    ptt.trigger_pressed(2.0)  # on its own again: starts
    assert calls == ["start"]


def test_refused_start_leaves_the_press_alone():
    ptt, calls = make(refuse=True)
    ptt.trigger_pressed(0.0)
    assert ptt.state == "idle"
    assert ptt.key_pressed(SPACE, 0.3) is False  # no lock: the Space is typed
    ptt.trigger_released(1.0)
    assert calls == ["start"]


def test_enter_locked_and_reset():
    ptt, calls = make()
    ptt.enter_locked()
    assert ptt.key_pressed(ESCAPE, 0.0) is True
    assert calls == ["cancel"]
    assert ptt.key_released(ESCAPE, 0.1) is True
    ptt.enter_locked()
    ptt.reset()
    assert ptt.key_pressed(ESCAPE, 1.0) is False


def test_unknown_hands_free_key_is_refused():
    with pytest.raises(ValueError):
        PushToTalk(hands_free="tab")


# -- double tap ------------------------------------------------------------------------------


def test_two_quick_taps_lock():
    """The first tap is cancelled quietly like any tap; the second one's recording goes on
    hands-free, so only the first tap's split second is thrown away."""
    ptt, calls = make(double_tap=True)
    ptt.trigger_pressed(0.0)
    ptt.trigger_released(0.1)
    assert calls == ["start", "cancel quiet"]
    ptt.reset()  # what the controller does when the quiet cancel ends the session
    ptt.trigger_pressed(0.4)
    assert calls == ["start", "cancel quiet", "start"] and ptt.state == "holding"
    ptt.trigger_released(0.5)
    assert calls == ["start", "cancel quiet", "start", "lock"] and ptt.state == "locked"
    assert ptt.key_pressed(ESCAPE, 2.0) is True  # locked as with Space: Escape cancels
    assert calls[-1] == "cancel"


def test_a_double_tap_is_finished_by_the_trigger():
    ptt, calls = make(double_tap=True)
    for t in (0.0, 0.3):
        ptt.trigger_pressed(t)
        ptt.trigger_released(t + 0.1)
    ptt.trigger_pressed(4.0)
    ptt.trigger_released(4.1)
    assert calls == ["start", "cancel quiet", "start", "lock", "stop"] and ptt.state == "idle"


def test_a_slow_second_tap_does_not_lock():
    ptt, calls = make(double_tap=True)
    ptt.trigger_pressed(0.0)
    ptt.trigger_released(0.1)
    ptt.trigger_pressed(0.7)  # 0.6 s after the first tap ended
    ptt.trigger_released(0.8)
    assert calls == ["start", "cancel quiet", "start", "cancel quiet"] and ptt.state == "idle"


def test_a_second_tap_at_the_limit_still_locks():
    ptt, calls = make(double_tap=True)
    ptt.trigger_pressed(0.0)
    ptt.trigger_released(0.1)
    ptt.trigger_pressed(0.6)
    ptt.trigger_released(0.7)
    assert calls[-1] == "lock"


def test_a_tap_then_a_hold_dictates_normally():
    ptt, calls = make(double_tap=True)
    ptt.trigger_pressed(0.0)
    ptt.trigger_released(0.1)
    ptt.trigger_pressed(0.3)
    ptt.trigger_released(2.0)
    assert calls == ["start", "cancel quiet", "start", "stop"] and ptt.state == "idle"


def test_a_hold_then_a_tap_is_a_single_tap():
    ptt, calls = make(double_tap=True)
    ptt.trigger_pressed(0.0)
    ptt.trigger_released(1.0)  # a dictation, not a tap
    ptt.trigger_pressed(1.2)
    ptt.trigger_released(1.3)
    assert calls == ["start", "stop", "start", "cancel quiet"]


def test_a_shortcut_is_not_half_of_a_double_tap():
    ptt, calls = make(double_tap=True)
    ptt.trigger_pressed(0.0)
    ptt.key_pressed(OTHER, 0.05)  # trigger+arrow
    ptt.trigger_released(0.1)
    ptt.trigger_pressed(0.3)
    ptt.trigger_released(0.4)
    assert calls == ["start", "cancel quiet", "start", "cancel quiet"]


def test_three_quick_taps_lock_then_finish():
    ptt, calls = make(double_tap=True)
    for t in (0.0, 0.3, 0.6):
        ptt.trigger_pressed(t)
        ptt.trigger_released(t + 0.1)
    assert calls == ["start", "cancel quiet", "start", "lock", "stop"]


def test_a_refused_first_tap_does_not_count():
    ptt, calls = make(refuse=True, double_tap=True)
    ptt.trigger_pressed(0.0)
    ptt.trigger_released(0.1)
    ptt.trigger_pressed(0.3)
    ptt.trigger_released(0.4)
    assert calls == ["start", "start"] and ptt.state == "idle"


def test_without_double_tap_two_quick_taps_are_two_taps():
    ptt, calls = make(double_tap=False)
    for t in (0.0, 0.3):
        ptt.trigger_pressed(t)
        ptt.trigger_released(t + 0.1)
    assert calls == ["start", "cancel quiet", "start", "cancel quiet"] and ptt.state == "idle"
