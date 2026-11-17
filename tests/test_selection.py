import threading

from lyssna.events import Point
from lyssna.selection import AXSelection, SelectionWatcher, offers_button

NONE = AXSelection(True, "")
UNSUPPORTED = AXSelection(False, "")
OLD = AXSelection(True, "old words")
NEW = AXSelection(True, "hello world")


def make(selections, non_text=True, owner=100, front=100, enabled=True, delays=(0, 0)):
    """`selections` are the AX answers in order: the before-snapshot first, then each after-probe."""
    answers = list(selections)
    shown = []
    done = threading.Event()

    def probe_selection():
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def present(p, bounds):
        shown.append((p, bounds))
        done.set()

    w = SelectionWatcher(
        probe_cursor=lambda: non_text,
        probe_selection=probe_selection,
        probe_press_owner=lambda p: owner,
        probe_front_pid=lambda: front,
        present=present,
        enabled=lambda: enabled,
        probe_bounds=lambda: (1.0, 2.0, 3.0, 4.0),
        delays=delays,
    )
    return w, shown, done


HERE = Point(10, 10)


def run(w, p=HERE):
    """Press and gesture, then wait for the worker threads to finish."""
    before = set(threading.enumerate())
    w.press(p)
    w.gesture(p)
    for t in set(threading.enumerate()) - before:
        t.join(2)


def test_new_selection_shows():
    w, shown, _ = make([NONE, NEW])
    run(w)
    assert shown == [(Point(10, 10), (1.0, 2.0, 3.0, 4.0))]


def test_screenshot_like_drag_does_not_show():
    # Arrow cursor, and the old selection is still there afterwards.
    w, shown, _ = make([OLD, OLD], non_text=True)
    run(w)
    assert shown == []


def test_press_on_another_apps_window_does_not_show():
    # Screenshot overlay owns the window under the press; the front app gains a selection anyway.
    w, shown, _ = make([NONE, NEW], non_text=False, owner=200, front=100)
    run(w)
    assert shown == []


def test_unknown_owner_or_front_does_not_block():
    for owner, front in ((None, 100), (200, None)):
        w, shown, _ = make([NONE, NEW], owner=owner, front=front)
        run(w)
        assert len(shown) == 1


def test_retry_catches_a_late_selection():
    w, shown, _ = make([NONE, NONE, NEW])
    run(w)
    assert len(shown) == 1


def test_newer_press_cancels_pending_check():
    w, shown, done = make([NONE, NEW], delays=(0.2,))
    w.press(Point(1, 1))
    w.gesture(Point(1, 1))
    w.press(Point(2, 2))  # before the first check's delay elapses
    assert not done.wait(0.4)
    assert shown == []


def test_disabled_never_presents():
    w, shown, _ = make([NONE, NEW], enabled=False)
    run(w)
    assert shown == []


# -- offers_button: the decision itself, from the facts the watcher gathers --------

ARROW = dict(non_text_cursor=True, pressed_in_front_app=True)
CUSTOM = dict(non_text_cursor=False, pressed_in_front_app=True)


def test_press_on_another_apps_window_never_shows():
    # Screenshot overlay: new text in the front app doesn't matter.
    for cursor in (True, False):
        assert not offers_button(NONE, NEW, non_text_cursor=cursor, pressed_in_front_app=False)
        assert not offers_button(UNSUPPORTED, UNSUPPORTED, non_text_cursor=cursor, pressed_in_front_app=False)


def test_new_word_selection_shows_even_on_arrow():
    assert offers_button(OLD, NEW, **ARROW)
    assert offers_button(NONE, NEW, **ARROW)
    assert offers_button(UNSUPPORTED, NEW, **ARROW)


def test_unchanged_selection_shows_only_on_custom_cursor():
    assert not offers_button(NEW, NEW, **ARROW)  # file or window drag over an old selection
    assert offers_button(NEW, NEW, **CUSTOM)  # e.g. a terminal's own text cursor


def test_nothing_selected_never_shows():
    assert not offers_button(OLD, NONE, **CUSTOM)
    assert not offers_button(OLD, AXSelection(True, "   "), **CUSTOM)


def test_single_character_is_not_a_word():
    assert not offers_button(NONE, AXSelection(True, "a"), **CUSTOM)
    assert not offers_button(NONE, AXSelection(True, " , "), **CUSTOM)
    assert offers_button(NONE, AXSelection(True, "ab"), **ARROW)


def test_unsupported_shows_unless_standard_non_text_cursor():
    assert offers_button(UNSUPPORTED, UNSUPPORTED, **CUSTOM)
    assert not offers_button(UNSUPPORTED, UNSUPPORTED, **ARROW)


def test_press_on_our_own_ui_does_not_show():
    # Our app is in front (say the History window) and the press landed on our own window.
    for front in (100, 300):
        w, shown, _ = make([NONE, NEW], non_text=False, owner=100, front=front)
        w.own_pid = 100
        run(w)
        assert shown == []
