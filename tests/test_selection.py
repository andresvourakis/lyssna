import threading
import time

from lyssna.events import Point
from lyssna.selection import AXSelection, SelectionWatcher, offers_button, selection_proof

BOX = (1.0, 2.0, 3.0, 4.0)
NONE = AXSelection(True, "", ("plain", 3, 0))
UNSUPPORTED = AXSelection(False, "")
OLD = AXSelection(True, "old words", ("plain", 20, 9), BOX)
NEW = AXSelection(True, "hello world", ("plain", 0, 11), BOX)


def make(selections, enabled=True, delays=(0, 0), copied=None, sources=None, before_timeout=0.5, slow_before=0.0):
    """`selections` are the AX answers in order: the before-snapshot first, then each after-probe.
    `copied` is the text someone else put on the clipboard since the press ("" or None: nothing);
    the source each shown button was offered for is appended to `sources`. `slow_before` makes
    the before-snapshot take that long (after taking its answer)."""
    answers = list(selections)
    shown = []
    done = threading.Event()
    seen_counts = []
    woken = []
    calls = [0]

    def probe_selection():
        calls[0] += 1
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if calls[0] == 1 and slow_before:
            time.sleep(slow_before)
        return answer

    def present(p, bounds, source):
        shown.append((p, bounds))
        if sources is not None:
            sources.append(source)
        done.set()

    def probe_copied(count):
        seen_counts.append(count)
        return copied or ""

    w = SelectionWatcher(
        probe_selection=probe_selection,
        present=present,
        enabled=lambda: enabled,
        delays=delays,
        before_timeout=before_timeout,
        probe_clipboard=lambda: 7,
        probe_copied=probe_copied,
        wake=lambda: woken.append(1),
    )
    w.seen_counts = seen_counts
    w.woken = woken
    return w, shown, done


HERE = Point(10, 10)


def _join(before):
    for t in set(threading.enumerate()) - before:
        t.join(2)


def run(w, p=HERE):
    """Press and gesture, then wait for the worker threads to finish."""
    before = set(threading.enumerate())
    w.press(p)
    w.gesture(p)
    _join(before)


def test_new_selection_shows_as_a_selection_with_its_bounds():
    sources = []
    w, shown, _ = make([NONE, NEW], sources=sources)
    run(w)
    assert shown == [(Point(10, 10), BOX)]
    assert sources == ["selection"]


def test_ax_cannot_answer_and_nothing_copied_does_not_show():
    # A screenshot region drag over Chrome before it reports anything.
    w, shown, _ = make([UNSUPPORTED, UNSUPPORTED])
    run(w)
    assert shown == []


def test_same_selection_before_and_after_does_not_show():
    # A screenshot or slider drag over text that was already selected.
    w, shown, _ = make([OLD, OLD])
    run(w)
    assert shown == []


def test_missing_before_snapshot_shows_only_on_a_copy():
    w, shown, _ = make([NONE, NEW], before_timeout=0.01, slow_before=0.3)
    run(w)
    assert shown == []
    sources = []
    w, shown, _ = make([NONE, NEW], before_timeout=0.01, slow_before=0.3, copied="hello world", sources=sources)
    run(w)
    assert sources == ["clipboard"]
    assert shown[0][1] is None  # no Accessibility proof: the button sits by the cursor


def test_empty_selection_whose_range_moved_does_not_show():
    # iTerm2: the caret's range moves while output scrolls, with nothing selected.
    w, shown, _ = make([AXSelection(True, "", ("plain", 5, 0)), AXSelection(True, "", ("plain", 90, 0))])
    run(w)
    assert shown == []


def test_retry_catches_a_late_selection():
    w, shown, _ = make([NONE, NONE, NEW])
    run(w)
    assert len(shown) == 1


def test_double_click_compares_with_the_first_press():
    # The first press saw nothing selected; by the second press the app already shows the word.
    w, shown, _ = make([NONE, NEW, NEW])
    before = set(threading.enumerate())
    w.press(HERE, 1)
    _join(before)
    before = set(threading.enumerate())
    w.press(HERE, 2)
    w.gesture(HERE)
    _join(before)
    assert len(shown) == 1


def test_a_press_while_our_own_app_is_in_front_does_not_show():
    # With our app in front capture reads nothing (see test_capture_ax_gate); no rule needed here.
    w, shown, _ = make([UNSUPPORTED, UNSUPPORTED])
    run(w)
    assert shown == []


def test_press_asks_the_front_app_to_report():
    w, _, _ = make([NONE, NONE])
    run(w)
    assert w.woken == [1]


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


# -- the decision itself, from the facts the watcher gathers ----------------------------


def test_a_new_word_selection_is_proof():
    assert selection_proof(OLD, NEW)
    assert selection_proof(NONE, NEW)
    assert selection_proof(UNSUPPORTED, NEW)  # a reading was taken; the app could not answer then


def test_the_same_text_in_a_new_place_is_proof():
    assert selection_proof(NEW, NEW._replace(range=("plain", 40, 11)))


def test_unchanged_selection_is_no_proof():
    assert not offers_button(NEW, NEW)
    assert not offers_button(UNSUPPORTED, UNSUPPORTED)


def test_missing_before_snapshot_is_no_proof():
    assert not offers_button(None, NEW)
    assert offers_button(None, NEW, copied=True)


def test_nothing_selected_never_shows():
    assert not offers_button(OLD, NONE)
    assert not offers_button(OLD, AXSelection(True, "   "))


def test_single_character_is_not_a_word():
    assert not offers_button(NONE, AXSelection(True, "a"))
    assert not offers_button(NONE, AXSelection(True, " , "))
    assert offers_button(NONE, AXSelection(True, "ab"))


# -- clipboard evidence: programs that copy their own selection -------------------------


def test_copy_on_select_shows_when_ax_says_nothing_selected():
    # A terminal program draws its own selection and copies it; the terminal reports no selection.
    sources = []
    w, shown, _ = make([NONE, NONE], copied="ls -la is a word", sources=sources)
    run(w)
    assert len(shown) == 1 and sources == ["clipboard"]
    assert shown[0][1] is None  # Accessibility has no rectangle for it: the button sits by the cursor
    assert set(w.seen_counts) == {7}  # compared against the change count read at the press


def test_copy_on_select_shows_when_ax_cannot_answer():
    sources = []
    w, _, _ = make([UNSUPPORTED, UNSUPPORTED], copied="hello there", sources=sources)
    run(w)
    assert sources == ["clipboard"]


def test_copy_on_select_over_an_unchanged_old_selection_reads_the_clipboard():
    sources = []
    w, _, _ = make([OLD, OLD], copied="hello there", sources=sources)
    run(w)
    assert sources == ["clipboard"]


def test_copied_text_without_a_word_does_not_show():
    w, shown, _ = make([NONE, NONE], copied=" . ")
    run(w)
    assert shown == []


def test_nothing_copied_still_needs_a_selection():
    w, shown, _ = make([NONE, NONE], copied="")
    run(w)
    assert shown == []


def test_an_accessibility_selection_is_read_as_a_selection():
    sources = []
    w, _, _ = make([NONE, NEW], copied="hello world", sources=sources)
    run(w)
    assert sources == ["selection"]


def test_offers_button_takes_the_copy_as_evidence():
    assert offers_button(NONE, NONE, copied=True)
    assert offers_button(UNSUPPORTED, UNSUPPORTED, copied=True)
    assert offers_button(NEW, NEW, copied=True)  # an old selection, but a fresh copy


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
