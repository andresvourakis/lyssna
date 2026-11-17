import threading

from lyssna.events import Point
from lyssna.selection import AXSelection, SelectionWatcher, offers_button

NONE = AXSelection(True, "")
UNSUPPORTED = AXSelection(False, "")
OLD = AXSelection(True, "old words")
NEW = AXSelection(True, "hello world")


def make(selections, non_text=True, owner=100, front=100, enabled=True, delays=(0, 0), copied=None, sources=None):
    """`selections` are the AX answers in order: the before-snapshot first, then each after-probe.
    `copied` is the text someone else put on the clipboard since the press ("" or None: nothing);
    the source each shown button was offered for is appended to `sources`."""
    answers = list(selections)
    shown = []
    done = threading.Event()
    seen_counts = []

    def probe_selection():
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def present(p, bounds, source):
        shown.append((p, bounds))
        if sources is not None:
            sources.append(source)
        done.set()

    def probe_copied(count):
        seen_counts.append(count)
        return copied or ""

    w = SelectionWatcher(
        probe_cursor=lambda: non_text,
        probe_selection=probe_selection,
        probe_press_owner=lambda p: owner,
        probe_front_pid=lambda: front,
        present=present,
        enabled=lambda: enabled,
        probe_bounds=lambda: (1.0, 2.0, 3.0, 4.0),
        delays=delays,
        probe_clipboard=lambda: 7,
        probe_copied=probe_copied,
    )
    w.seen_counts = seen_counts
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


# -- clipboard evidence: programs that copy their own selection -------------------------


def test_copy_on_select_shows_when_ax_says_nothing_selected():
    # A terminal program draws its own selection and copies it; the terminal reports no selection.
    sources = []
    w, shown, _ = make([NONE, NONE], non_text=False, copied="ls -la is a word", sources=sources)
    run(w)
    assert len(shown) == 1 and sources == ["clipboard"]
    assert shown[0][1] is None  # Accessibility has no rectangle for it: the button sits by the cursor
    assert set(w.seen_counts) == {7}  # compared against the change count read at the press


def test_copy_on_select_shows_when_ax_cannot_answer_even_on_arrow():
    sources = []
    w, _, _ = make([UNSUPPORTED, UNSUPPORTED], non_text=True, copied="hello there", sources=sources)
    run(w)
    assert sources == ["clipboard"]


def test_copied_text_without_a_word_does_not_show():
    w, shown, _ = make([NONE, NONE], non_text=False, copied=" . ")
    run(w)
    assert shown == []


def test_nothing_copied_still_needs_a_selection():
    w, shown, _ = make([NONE, NONE], non_text=False, copied="")
    run(w)
    assert shown == []


def test_copy_on_select_never_beats_a_press_on_another_apps_window():
    w, shown, _ = make([NONE, NONE], non_text=False, owner=200, front=100, copied="hello world")
    run(w)
    assert shown == []
    assert not offers_button(NONE, NONE, non_text_cursor=False, pressed_in_front_app=False, copied=True)


def test_an_accessibility_selection_is_read_as_a_selection():
    sources = []
    w, _, _ = make([NONE, NEW], copied="hello world", sources=sources)
    run(w)
    assert sources == ["selection"]


def test_offers_button_takes_the_copy_as_evidence():
    assert offers_button(NONE, NONE, **CUSTOM, copied=True)
    assert offers_button(UNSUPPORTED, UNSUPPORTED, **ARROW, copied=True)
    assert offers_button(NEW, NEW, **ARROW, copied=True)  # an old selection, but a fresh copy


# -- capture: which clipboard changes count -----------------------------------------


class Pasteboard:
    def __init__(self, count, text):
        self.count, self.text = count, text

    def changeCount(self):
        return self.count

    def stringForType_(self, kind):
        return self.text


def test_copied_since_ignores_our_own_writes(monkeypatch):
    from lyssna import capture

    pb = Pasteboard(10, "hello world")
    class General:
        @staticmethod
        def generalPasteboard():
            return pb

    monkeypatch.setattr(capture, "NSPasteboard", General)
    monkeypatch.setattr(capture, "_own_counts", capture.deque(maxlen=64))
    assert capture.copied_since(10) == ""  # unchanged
    assert capture.copied_since(None) == ""  # count unknown at the press
    assert capture.copied_since(9) == "hello world"  # someone else copied
    capture.note_own_changes(9, 10)  # ...unless it was us
    assert capture.copied_since(9) == ""
    assert capture.own_change(10) and not capture.own_change(9)


def test_our_paste_and_restore_are_noted_as_ours(monkeypatch):
    from lyssna import capture
    from lyssna.dictation import insert

    class Board:
        def __init__(self):
            self.count, self.items = 3, []

        def changeCount(self):
            return self.count

        def clearContents(self):
            self.count += 1
            self.items = []

        def writeObjects_(self, items):
            self.count += 1
            self.items.extend(items)
            return True

        def pasteboardItems(self):
            return list(self.items)

    monkeypatch.setattr(capture, "_own_counts", capture.deque(maxlen=64))
    board = Board()
    count = insert.put_text(board, "dictated words", transient=True)
    assert count == 5 and capture.own_change(4) and capture.own_change(5)
    capture.restore_pasteboard(board, [{"public.utf8-plain-text": b"before"}])
    assert capture.own_change(board.changeCount())
