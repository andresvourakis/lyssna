"""The push-to-talk rules, fed raw key codes, flags and times: no keyboard, no clock."""

import pytest

from lyssna.dictation.hotkey import (
    DEVICE_LEFT_OPTION,
    DEVICE_RIGHT_OPTION,
    ESCAPE,
    FLAG_COMMAND,
    FLAG_FN,
    FLAG_OPTION,
    FN,
    RIGHT_OPTION,
    SPACE,
    PushToTalk,
)

LEFT_ARROW = 123
KEY_A = 0
LEFT_COMMAND = 55


def make(trigger="fn", refuse=False):
    calls = []

    def start():
        calls.append("start")
        return False if refuse else None

    ptt = PushToTalk(
        trigger=trigger,
        on_start=start,
        on_stop=lambda: calls.append("stop"),
        on_lock=lambda: calls.append("lock"),
        on_cancel=lambda quiet: calls.append("cancel quiet" if quiet else "cancel"),
    )
    return ptt, calls


def fn_down(ptt, t, extra=0):
    return ptt.flags_changed(FN, FLAG_FN | extra, t)


def fn_up(ptt, t, extra=0):
    return ptt.flags_changed(FN, extra, t)


def test_hold_and_release_starts_and_stops():
    ptt, calls = make()
    assert fn_down(ptt, 0.0) is False  # modifier changes are never swallowed
    assert calls == ["start"] and ptt.state == "holding"
    assert fn_up(ptt, 1.0) is False
    assert calls == ["start", "stop"] and ptt.state == "idle"


def test_a_quick_tap_cancels_quietly():
    ptt, calls = make()
    fn_down(ptt, 0.0)
    fn_up(ptt, 0.2)
    assert calls == ["start", "cancel quiet"]


def test_another_key_while_holding_cancels_and_goes_through():
    ptt, calls = make()
    fn_down(ptt, 0.0)
    assert ptt.key_down(LEFT_ARROW, 0.5) is False
    assert ptt.key_up(LEFT_ARROW, 0.6) is False
    fn_up(ptt, 1.0)
    assert calls == ["start", "cancel quiet"]  # the release afterwards does nothing


def test_another_modifier_while_holding_cancels():
    ptt, calls = make()
    fn_down(ptt, 0.0)
    ptt.flags_changed(LEFT_COMMAND, FLAG_FN | FLAG_COMMAND, 0.4)
    fn_up(ptt, 1.0, FLAG_COMMAND)
    assert calls == ["start", "cancel quiet"]


def test_space_while_holding_locks_and_is_swallowed():
    ptt, calls = make()
    fn_down(ptt, 0.0)
    assert ptt.key_down(SPACE, 0.5) is True
    assert ptt.key_down(SPACE, 0.6) is True  # auto-repeat of the same press
    fn_up(ptt, 0.7)  # letting go of Fn keeps recording
    assert ptt.key_up(SPACE, 0.8) is True
    assert calls == ["start", "lock"] and ptt.state == "locked"
    assert ptt.key_down(SPACE, 2.0) is False  # a later Space is typed normally
    assert ptt.key_up(SPACE, 2.1) is False


def test_locked_trigger_press_stops_and_its_release_does_nothing():
    ptt, calls = make()
    fn_down(ptt, 0.0)
    ptt.key_down(SPACE, 0.5)
    ptt.key_up(SPACE, 0.6)
    fn_up(ptt, 0.7)
    fn_down(ptt, 5.0)
    fn_up(ptt, 5.1)
    assert calls == ["start", "lock", "stop"] and ptt.state == "idle"


def test_locked_escape_cancels_with_notice_and_is_swallowed():
    ptt, calls = make()
    fn_down(ptt, 0.0)
    ptt.key_down(SPACE, 0.5)
    ptt.key_up(SPACE, 0.6)
    fn_up(ptt, 0.7)
    assert ptt.key_down(KEY_A, 1.0) is False  # typing while hands-free goes through
    assert ptt.key_down(ESCAPE, 2.0) is True
    assert ptt.key_up(ESCAPE, 2.1) is True
    assert calls == ["start", "lock", "cancel"] and ptt.state == "idle"
    assert ptt.key_down(ESCAPE, 3.0) is False  # idle: Escape belongs to the app again


def test_idle_swallows_nothing():
    ptt, calls = make()
    for key in (SPACE, ESCAPE, KEY_A, LEFT_ARROW):
        assert ptt.key_down(key, 0.0) is False
        assert ptt.key_up(key, 0.1) is False
    assert calls == []


def test_repeated_trigger_events_while_holding_are_ignored():
    ptt, calls = make()
    fn_down(ptt, 0.0)
    fn_down(ptt, 0.3)
    fn_down(ptt, 0.6)
    assert ptt.key_down(FN, 0.7) is False
    fn_up(ptt, 1.0)
    assert calls == ["start", "stop"]


def test_keys_carrying_the_fn_flag_do_not_start():
    """Arrow and function keys set the Fn flag in their own events; only the Fn key's own
    key code in a modifier change counts."""
    ptt, calls = make()
    assert ptt.key_down(LEFT_ARROW, 0.0) is False
    ptt.flags_changed(LEFT_COMMAND, FLAG_FN, 0.1)  # some other modifier event that happens to carry Fn
    assert calls == [] and ptt.state == "idle"


def test_fn_with_another_modifier_already_down_does_not_start():
    ptt, calls = make()
    fn_down(ptt, 0.0, FLAG_COMMAND)
    fn_up(ptt, 1.0, FLAG_COMMAND)
    assert calls == []
    fn_down(ptt, 2.0)  # on its own again: starts
    assert calls == ["start"]


def test_refused_start_leaves_the_press_alone():
    ptt, calls = make(refuse=True)
    fn_down(ptt, 0.0)
    assert ptt.state == "idle"
    assert ptt.key_down(SPACE, 0.3) is False  # no lock: the Space is typed
    fn_up(ptt, 1.0)
    assert calls == ["start"]


def test_right_option_uses_its_device_bit():
    ptt, calls = make(trigger="right_option")
    ptt.flags_changed(RIGHT_OPTION, FLAG_OPTION | DEVICE_RIGHT_OPTION, 0.0)
    assert calls == ["start"]
    ptt.flags_changed(RIGHT_OPTION, 0, 1.0)
    assert calls == ["start", "stop"]


def test_right_option_while_left_option_is_held_is_a_chord():
    ptt, calls = make(trigger="right_option")
    ptt.flags_changed(RIGHT_OPTION, FLAG_OPTION | DEVICE_LEFT_OPTION | DEVICE_RIGHT_OPTION, 0.0)
    ptt.flags_changed(RIGHT_OPTION, FLAG_OPTION | DEVICE_LEFT_OPTION, 1.0)
    assert calls == []


def test_right_option_with_fn_held_does_not_start():
    ptt, calls = make(trigger="right_option")
    ptt.flags_changed(RIGHT_OPTION, FLAG_OPTION | DEVICE_RIGHT_OPTION | FLAG_FN, 0.0)
    assert calls == []  # Fn held counts as another modifier


def test_enter_locked_and_reset():
    ptt, calls = make()
    ptt.enter_locked()
    assert ptt.key_down(ESCAPE, 0.0) is True
    assert calls == ["cancel"]
    assert ptt.key_up(ESCAPE, 0.1) is True
    ptt.enter_locked()
    ptt.reset()
    assert ptt.key_down(ESCAPE, 1.0) is False


def test_unknown_keys_are_refused():
    with pytest.raises(ValueError):
        PushToTalk(trigger="caps_lock")
    with pytest.raises(ValueError):
        PushToTalk(hands_free="tab")
