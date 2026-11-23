"""The History window, built offscreen (never put on screen): what it shows from the store and
the dictionary, and what its buttons do. The store and the dictionary live under the per-test
temp dir; the clipboard and the confirmation sheet are faked.

Needs a window server to build an NSWindow: the module skips without one."""

from datetime import date, datetime, timedelta, timezone

import AppKit
import numpy as np
import pytest
from AppKit import NSMenuItem, NSTextField

from lyssna.dictation.history import HistoryStore
from lyssna.dictation.types import DictationResult
from lyssna.dictation.vocabulary import Vocabulary
from lyssna.legacy.ui import history_window as HW

pytestmark = pytest.mark.skipif(not AppKit.NSScreen.screens(), reason="no window server")

NOW = datetime(2026, 11, 17, 16, 0, tzinfo=timezone(timedelta(hours=1)))  # a Tuesday


@pytest.fixture(scope="module", autouse=True)
def app():
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    return app


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    """No real clipboard, no sheets."""
    copied = []
    monkeypatch.setattr(HW, "copy_to_clipboard", copied.append)
    monkeypatch.setattr(HW, "later", lambda delay, fn, *args: None)
    return copied


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def days():
    return {"days": 30}


@pytest.fixture
def store(clock, days):
    return HistoryStore(days=lambda: days["days"], now=clock)


@pytest.fixture
def vocab():
    return Vocabulary()


@pytest.fixture
def win(store, vocab, clock):
    w = HW.HistoryWindow.alloc().init()
    w.store = store
    w.vocabulary = vocab
    w.now = clock
    yield w
    if w.window is not None:
        w.windowWillClose_(None)
        w.window.close()


def add(store, clock, text, when=None, raw=None):
    if when is not None:
        clock.t = when
    store.add(DictationResult(text=text, raw=raw if raw is not None else text.lower(), source="model",
                              transcriber="parakeet", cleaner="s1-mini", audio_seconds=2.0, seconds={},
                              words=len(text.split())))
    clock.t = NOW


def strings(view) -> list[str]:
    """Every visible label's text under a view, top to bottom as added."""
    out = []
    for sub in view.subviews():
        if sub.isHidden():
            continue
        if isinstance(sub, NSTextField):
            out.append(str(sub.stringValue()))
        out += strings(sub)
    return out


def kinds(w):
    return [i.kind for i in w.items]


def menu_item(entry_id: str) -> NSMenuItem:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("x", None, "")
    item.setRepresentedObject_(entry_id)
    return item


# -- the pure helpers ----------------------------------------------------------------------------


def test_figure_has_thousands_separators():
    assert HW.figure(0) == "0"
    assert HW.figure(18930) == "18,930"
    assert HW.figure(1234567) == "1,234,567"


@pytest.mark.parametrize("value, text", [
    (30, "Kept for 30 days on this Mac"),
    (1, "Kept for 1 day on this Mac"),
    (-1, "Kept on this Mac until you clear it"),
    (0, "History is off"),
])
def test_retention_text(value, text):
    assert HW.retention_text(value) == text


def test_day_label():
    today = date(2026, 11, 17)
    assert HW.day_label(today, today) == "Today"
    assert HW.day_label(date(2026, 11, 16), today) == "Yesterday"
    assert HW.day_label(date(2026, 11, 14), today) == "Sat 14 Nov"
    assert HW.day_label(date(2025, 11, 14), today) == "Fri 14 Nov 2025"


def test_learned_text():
    assert HW.learned_text(["Mattias"]) == "Learned: Mattias"
    assert HW.learned_text(["Mattias", "Uppsala"]) == "Learned: Mattias, Uppsala"
    assert HW.learned_text([]) == ""


# -- the window ----------------------------------------------------------------------------------


def test_builds_offscreen_with_the_brand_sizes(win):
    win.prepare()
    assert not win.window.isVisible()
    size = win.window.contentRectForFrameRect_(win.window.frame()).size
    assert (size.width, size.height) == (HW.W, HW.H)
    assert tuple(win.window.contentMinSize()) == (HW.MIN_W, HW.MIN_H)
    mask = win.window.styleMask()
    for flag in (AppKit.NSWindowStyleMaskTitled, AppKit.NSWindowStyleMaskClosable, AppKit.NSWindowStyleMaskResizable):
        assert mask & flag
    assert isinstance(win.window, HW.EditingWindow)  # Cmd+C/V/A in its fields
    assert "History" in strings(win.content)


def test_empty_state(win):
    win.prepare()
    assert kinds(win) == ["empty"]
    assert HW.EMPTY_DICTATIONS in strings(win.doc)
    assert not win.clear_button.isEnabled()


def test_clear_history_matches_the_retention_line(win):
    win.prepare()
    weight = AppKit.NSFontManager.sharedFontManager().weightOfFont_
    assert weight(win.clear_button.text_font) == weight(win.retention.font())  # regular, not medium


def test_stats_row(win, store, clock):
    add(store, clock, "one two three", NOW - timedelta(days=8))  # last week
    add(store, clock, "four five")
    win.prepare()
    assert [str(n.stringValue()) for n in win.stat_numbers] == ["2", "5", "2"]
    assert [str(lbl.stringValue()) for lbl in win.stat_labels] == ["words this week", "words in total", "dictations"]


def test_stats_use_thousands_separators(win, store, clock):
    add(store, clock, "word " * 1500)
    win.prepare()
    assert str(win.stat_numbers[1].stringValue()) == "1,500"


def test_no_support_line_without_a_website(win, store, clock):
    add(store, clock, "word " * 1500)
    win.prepare()
    assert win.support_label.isHidden() and win.support_button.isHidden()


def test_support_line_waits_for_a_thousand_words(win, store, clock, monkeypatch):
    from lyssna import support

    opened = []
    monkeypatch.setattr(support, "SITE_URL", "https://example.test")
    monkeypatch.setattr(support, "_open", opened.append)
    add(store, clock, "word " * 999)
    win.prepare()
    assert win.support_label.isHidden() and win.support_button.isHidden()
    tabs_y = win.tabs.frame().origin.y
    add(store, clock, "one more")
    win._refresh()
    assert not win.support_label.isHidden() and not win.support_button.isHidden()
    assert support.HISTORY_LINE in strings(win.content)
    assert win.tabs.frame().origin.y > tabs_y  # the line takes its own row above the tabs
    label, button = win.support_label.frame(), win.support_button.frame()
    assert button.origin.x >= label.origin.x + label.size.width  # the link follows the sentence
    assert win.support_button.image() is not None  # every support link carries the heart
    win.support_button.performClick_(None)
    assert opened == ["https://example.test/?support=1"]


def test_rows_are_grouped_by_day_newest_first(win, store, clock):
    add(store, clock, "Booked the train.", NOW - timedelta(days=3))
    add(store, clock, "Thanks, that works.", NOW - timedelta(days=1))
    add(store, clock, "First today.", NOW - timedelta(hours=3))
    add(store, clock, "Second today.", NOW - timedelta(hours=1))
    win.prepare()
    assert kinds(win) == ["header", "dictation", "dictation", "header", "dictation", "header", "dictation"]
    headers = [i.text for i in win.items if i.kind == "header"]
    assert headers == ["Today", "Yesterday", "Sat 14 Nov"]
    texts = [i.entry.text for i in win.items if i.kind == "dictation"]
    assert texts == ["Second today.", "First today.", "Thanks, that works.", "Booked the train."]


def test_long_text_is_cut_to_four_lines_until_clicked(win, store, clock):
    add(store, clock, "A long dictation that goes on. " * 40)
    win.prepare()
    row = win.items[1]
    collapsed = row.view.frame().size.height
    row.view.on_click()
    row = win.items[1]
    assert row.expanded
    assert row.view.frame().size.height > collapsed * 2
    row.view.on_click()
    assert not win.items[1].expanded


def test_tab_clicks_reach_the_window(win):
    win.prepare()
    win.tabs.on_change(1)
    assert win._tab == "dictionary"
    win.tabs.on_change(0)
    assert win._tab == "dictations"


def test_row_actions_are_icons_with_tooltips(win, store, clock):
    add(store, clock, "Icons please.")
    win.prepare()
    row = win.items[1]
    assert (row.copy.symbol, str(row.copy.toolTip())) == ("doc.on.doc", "Copy")
    assert (row.more.symbol, str(row.more.toolTip())) == ("ellipsis", "More")
    assert row.copy.frame().size.width == HW.ICON_BTN
    # both sit on the first line of the text, not the middle of the row
    assert row.copy.frame().origin.y == row.more.frame().origin.y < HW.ROW_PAD_Y + HW.TEXT_LINE_H


def test_content_column_stays_centred_on_wide_windows():
    assert HW.column(560) == (HW.MARGIN, 560 - 2 * HW.MARGIN)
    assert HW.column(900) == ((900 - HW.COLUMN_MAX) / 2, HW.COLUMN_MAX)


def test_copy_and_more_appear_on_hover(win, store, clock):
    add(store, clock, "Hover me.")
    win.prepare()
    row = win.items[1]
    assert row.copy.isHidden() and row.more.isHidden()
    row.view.set_hover(True)
    assert not row.copy.isHidden() and not row.more.isHidden()
    row.view.set_hover(False)
    assert row.copy.isHidden()


def test_copy_puts_the_text_on_the_clipboard(win, store, clock, no_side_effects):
    add(store, clock, "Copy this exactly.")
    win.prepare()
    row = win.items[1]
    win.copyEntry_(row.copy)
    assert no_side_effects == ["Copy this exactly."]
    assert row.copy.symbol == "checkmark"  # the copy icon turns into a check for a moment


def test_more_menu(win, store, clock):
    add(store, clock, "Menu please.")
    win.prepare()
    entry = store.entries()[0]
    menu = win._more_menu(entry)
    assert [str(menu.itemAtIndex_(i).title()) for i in range(menu.numberOfItems())] == [
        "Show Original", "Fix a Word…", "Delete"]


def test_show_original_toggles_the_raw_transcript(win, store, clock):
    add(store, clock, "Send it to Mattias.", raw="send it to matias")
    win.prepare()
    entry_id = win.items[1].entry_id
    assert "send it to matias" not in strings(win.doc)
    win.toggleOriginal_(menu_item(entry_id))
    assert "send it to matias" in strings(win.doc)
    assert str(win._more_menu(store.entries()[0]).itemAtIndex_(0).title()) == "Hide Original"
    win.toggleOriginal_(menu_item(entry_id))
    assert "send it to matias" not in strings(win.doc)


def test_delete_from_the_menu(win, store, clock):
    add(store, clock, "Keep me.", NOW - timedelta(minutes=5))
    add(store, clock, "Delete me.")
    win.prepare()
    win.deleteEntry_(menu_item(store.entries()[0].id))
    assert [e.text for e in store.entries()] == ["Keep me."]
    assert "Delete me." not in strings(win.doc)


def test_fix_a_word_learns_the_term_and_saves_the_text(win, store, vocab, clock):
    add(store, clock, "Send the draft to Matias before lunch.")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fixWord_(menu_item(entry_id))
    row = win._row(entry_id)
    assert row.editing and row.edit_field is not None
    assert str(row.edit_field.stringValue()) == "Send the draft to Matias before lunch."
    row.edit_field.setStringValue_("Send the draft to Mattias before lunch.")
    win.saveFix_(row.save)
    assert store.entries()[0].text == "Send the draft to Mattias before lunch."
    assert "Mattias" in vocab.terms()
    assert "Learned: Mattias" in strings(win.doc)
    assert not win._row(entry_id).editing


def test_fix_a_word_with_nothing_to_learn_says_nothing(win, store, vocab, clock):
    add(store, clock, "Meet at the cafe at noon.")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fixWord_(menu_item(entry_id))
    row = win._row(entry_id)
    row.edit_field.setStringValue_("Meet at the bar at noon.")
    win.saveFix_(row.save)
    assert store.entries()[0].text == "Meet at the bar at noon."
    assert vocab.terms() == []
    assert not any(s.startswith("Learned") for s in strings(win.doc))


def test_cancel_fix_keeps_the_text(win, store, clock):
    add(store, clock, "Leave this alone.")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fixWord_(menu_item(entry_id))
    win._row(entry_id).edit_field.setStringValue_("Changed my mind.")
    win.cancelFix_(None)
    assert store.entries()[0].text == "Leave this alone."
    assert not win._row(entry_id).editing


def test_a_new_dictation_does_not_sweep_away_a_fix_in_progress(win, store, clock):
    add(store, clock, "Being fixed.", NOW - timedelta(minutes=5))
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fixWord_(menu_item(entry_id))
    win._row(entry_id).edit_field.setStringValue_("Being fixed by hand.")
    add(store, clock, "Arrived meanwhile.")
    win.tick_(None)
    assert win._row(entry_id).editing
    assert str(win._row(entry_id).edit_field.stringValue()) == "Being fixed by hand."
    win.cancelFix_(None)
    assert "Arrived meanwhile." in strings(win.doc)


def test_search_filters_the_list(win, store, clock):
    add(store, clock, "Lunch with Mattias.", NOW - timedelta(minutes=5))
    add(store, clock, "Ship the build.")
    win.prepare()
    win.search_field.setStringValue_("mattias")
    win.controlTextDidChange_(AppKit.NSNotification.notificationWithName_object_("x", win.search_field))
    assert [i.entry.text for i in win.items if i.kind == "dictation"] == ["Lunch with Mattias."]
    win.search_field.setStringValue_("nothing like it")
    win.controlTextDidChange_(AppKit.NSNotification.notificationWithName_object_("x", win.search_field))
    assert kinds(win) == ["empty"]
    assert HW.EMPTY_SEARCH in strings(win.doc)


def test_long_lists_are_capped_with_show_older(win, store, clock, days, monkeypatch):
    monkeypatch.setattr(HW, "ROW_CAP", 5)
    win._cap = 5
    days["days"] = -1
    for i in range(12):
        add(store, clock, f"note {i}", NOW - timedelta(minutes=i))
    win.prepare()
    assert sum(k == "dictation" for k in kinds(win)) == 5
    assert kinds(win)[-1] == "pill"
    win.showOlder_(None)
    assert sum(k == "dictation" for k in kinds(win)) == 10
    win.showOlder_(None)
    assert sum(k == "dictation" for k in kinds(win)) == 12
    assert kinds(win)[-1] != "pill"


def test_clear_history_asks_first_and_keeps_totals(win, store, clock, monkeypatch):
    add(store, clock, "one two three")
    win.prepare()
    asked = []
    monkeypatch.setattr(win, "_confirm", lambda message, info, ok, then: asked.append((message, info, ok, then)))
    win.clearHistory_(None)
    [(message, info, ok, then)] = asked
    assert message == "Delete all saved dictations?" and info == "Word totals are kept." and ok == "Delete"
    assert store.entries()  # nothing until the user agrees
    then()
    assert store.entries() == []
    assert kinds(win) == ["empty"]
    assert str(win.stat_numbers[1].stringValue()) == "3"


def test_footer_tells_the_retention(win, days):
    win.prepare()
    assert str(win.retention.stringValue()) == "Kept for 30 days on this Mac"
    days["days"] = -1
    win.tick_(None)
    assert str(win.retention.stringValue()) == "Kept on this Mac until you clear it"
    days["days"] = 0
    win.tick_(None)
    assert str(win.retention.stringValue()) == "History is off"


def test_the_window_picks_up_a_new_dictation(win, store, clock):
    win.prepare()
    assert kinds(win) == ["empty"]
    add(store, clock, "Just arrived.")
    win.tick_(None)
    assert "Just arrived." in strings(win.doc)


def test_resizing_keeps_the_layout(win, store, clock):
    add(store, clock, "Some words. " * 30)
    win.prepare()
    win.window.setContentSize_((HW.MIN_W, HW.MIN_H))
    win.windowDidResize_(None)
    assert win.scroll.frame().size.width == HW.MIN_W
    assert win.items[1].view.frame().size.width <= HW.MIN_W


# -- the dictionary tab --------------------------------------------------------------------------


def test_tabs(win):
    win.prepare()
    assert win.tabs.selected == 0 and win._tab == "dictations"
    win.select_tab("dictionary")
    assert win.tabs.selected == 1 and win._tab == "dictionary"
    assert win.search_box.isHidden() and not win.add_box.isHidden()
    assert win.clear_button.isHidden()
    assert not win.retention.isHidden()


def test_dictionary_empty_state(win):
    win.prepare()
    win.select_tab("dictionary")
    assert kinds(win) == ["empty"]
    assert HW.EMPTY_DICTIONARY in strings(win.doc)


def test_dictionary_rows(win, vocab):
    vocab.add("Parakeet")
    vocab.learn("Matias", "Mattias")
    vocab.learn("Mathias", "Mattias")
    vocab.used("Mattias Mattias")
    win.prepare()
    win.select_tab("dictionary")
    rows = [i for i in win.items if i.kind == "term"]
    assert [r.term for r in rows] == ["Mattias", "Parakeet"]  # most used first
    assert [r.badge_text for r in rows] == ["Learned", "Added"]
    texts = strings(win.doc)
    assert "heard as: mathias, matias" in texts  # most seen first, then by name
    assert rows[0].delete.isHidden()
    rows[0].view.set_hover(True)
    assert not rows[0].delete.isHidden()


def test_add_a_word_from_the_field(win, vocab):
    win.prepare()
    win.select_tab("dictionary")
    win.add_field.setStringValue_("  Uppsala ")
    win.addTerm_(win.add_field)
    assert vocab.terms() == ["Uppsala"]
    assert str(win.add_field.stringValue()) == ""
    assert [i.term for i in win.items if i.kind == "term"] == ["Uppsala"]
    assert win.add_field.action() == "addTerm:"  # Return adds


def test_add_a_word_that_is_too_long(win, vocab):
    win.prepare()
    win.select_tab("dictionary")
    win.add_field.setStringValue_("x" * 200)
    win.addTerm_(win.add_field)
    assert vocab.terms() == []
    assert "That is too long for the dictionary." in strings(win.content)


def test_remove_a_word(win, vocab):
    vocab.add("Kokoro")
    win.prepare()
    win.select_tab("dictionary")
    row = win.items[0]
    win.removeTerm_(row.delete)
    assert vocab.terms() == []
    assert kinds(win) == ["empty"]


def test_an_entry_from_another_source_reads_added(win, vocab):
    vocab.add("Mattias", source="imported")  # a source value an older vocabulary file may hold
    win.prepare()
    win.select_tab("dictionary")
    assert [(r.term, r.badge_text) for r in win.items if r.kind == "term"] == [("Mattias", "Added")]
    assert kinds(win) == ["term"]  # the list ends with the last word


def test_the_dictionary_tab_picks_up_a_learned_word(win, vocab):
    win.prepare()
    win.select_tab("dictionary")
    vocab.add("Lyssna")
    win.tick_(None)
    assert [i.term for i in win.items if i.kind == "term"] == ["Lyssna"]


# -- privacy ---------------------------------------------------------------------------------------


def test_nothing_dictated_reaches_the_log(win, store, vocab, clock, caplog):
    secret = "The door code for Matias is in the blue folder."
    add(store, clock, secret)
    with caplog.at_level("DEBUG"):
        win.prepare()
        entry_id = win.items[1].entry_id
        win.fixWord_(menu_item(entry_id))
        row = win._row(entry_id)
        row.edit_field.setStringValue_(secret.replace("Matias", "Mattias"))
        win.saveFix_(row.save)
        win.deleteEntry_(menu_item(entry_id))
    logged = "\n".join(r.getMessage() for r in caplog.records).lower()
    for word in ("door", "folder", "blue", "matias", "mattias"):
        assert word not in logged


# -- a failed dictation ----------------------------------------------------------------------------


TONE = (0.25 * np.sin(np.arange(32000) / 7)).astype(np.float32)


class FakeCtl:
    def __init__(self, start=True):
        self.calls = []
        self.start = start

    def retry_dictation(self, entry_id, paste=True, on_done=None):
        self.calls.append((entry_id, paste))
        self.on_done = on_done
        return self.start


def add_failed(store, clock, seconds=12.0, when=None):
    if when is not None:
        clock.t = when
    entry = store.add_failed(TONE, seconds, "openai")
    clock.t = NOW
    return entry


def test_failed_text():
    assert HW.failed_text(12.2) == "Couldn't transcribe. 12 s of audio"
    assert HW.failed_text(0.3) == "Couldn't transcribe. 1 s of audio"


def test_a_failed_row_shows_retry_and_more_always(win, store, clock):
    add(store, clock, "A normal one.", when=NOW - timedelta(minutes=5))
    add_failed(store, clock)
    win.prepare()
    assert kinds(win) == ["header", "dictation", "dictation"]
    row = win.items[1]
    assert isinstance(row, HW._FailedRow)
    assert "Couldn't transcribe. 12 s of audio" in strings(row.view)
    assert not row.retry.isHidden() and not row.more.isHidden()  # not hover-only
    assert str(row.retry.title()) == "Retry" and row.retry.isEnabled()
    row.view.set_hover(False)
    assert not row.retry.isHidden() and not row.more.isHidden()
    # Retry and More sit on the first line, right-aligned like a dictation row's icons, without overlapping
    other = win.items[2]
    assert row.more.frame().origin.x == other.more.frame().origin.x
    assert row.retry.frame().origin.x + row.retry.frame().size.width <= row.more.frame().origin.x
    assert row.text.frame().origin.x + row.text.frame().size.width <= row.retry.frame().origin.x
    assert row.time.frame().origin.y == row.text.frame().origin.y


def test_a_failed_rows_more_menu_holds_retry_and_delete(win, store, clock):
    entry = add_failed(store, clock)
    win.prepare()
    menu = win._more_menu(store.entries()[0])
    assert [str(menu.itemAtIndex_(i).title()) for i in range(menu.numberOfItems())] == ["Retry", "Delete"]
    win.ctl = FakeCtl()
    win.retryEntry_(menu_item(entry.id))
    assert win.ctl.calls == [(entry.id, False)]


def test_retry_from_the_row_does_not_paste_and_shows_retrying(win, store, clock):
    entry = add_failed(store, clock)
    win.ctl = FakeCtl()
    win.prepare()
    win.retryEntry_(win.items[1].retry)
    assert win.ctl.calls == [(entry.id, False)]
    row = win.items[1]
    assert str(row.retry.title()) == "Retrying…" and not row.retry.isEnabled()
    win.retryEntry_(row.retry)  # a second press while it runs does nothing
    assert len(win.ctl.calls) == 1
    # it worked: the store holds the text now, and the row turns into a normal one
    store.complete(entry.id, DictationResult(text="Second try.", raw="second try", source="model",
                                             transcriber="openai", cleaner="s1-mini", audio_seconds=12.0,
                                             seconds={}, words=2))
    win.ctl.on_done(True)
    row = win.items[1]
    assert isinstance(row, HW._DictationRow) and "Second try." in strings(row.view)


def test_a_failed_retry_leaves_the_row(win, store, clock):
    entry = add_failed(store, clock)
    win.ctl = FakeCtl()
    win.prepare()
    win.retryEntry_(win.items[1].retry)
    win.ctl.on_done(False)
    row = win.items[1]
    assert isinstance(row, HW._FailedRow) and str(row.retry.title()) == "Retry" and row.retry.isEnabled()
    assert store.entries()[0].id == entry.id


def test_a_retry_that_cannot_start_resets_the_pill(win, store, clock):
    add_failed(store, clock)
    win.ctl = FakeCtl(start=False)
    win.prepare()
    win.retryEntry_(win.items[1].retry)
    assert str(win.items[1].retry.title()) == "Retry"


def test_delete_a_failed_row_removes_its_recording(win, store, clock):
    entry = add_failed(store, clock)
    win.prepare()
    win.deleteEntry_(menu_item(entry.id))
    assert store.entries() == [] and not store.recordings.exists(entry.id)
    assert kinds(win) == ["empty"]


def test_search_leaves_failed_rows_out(win, store, clock):
    add(store, clock, "Findable words.")
    add_failed(store, clock)
    win.prepare()
    assert len(win.items) == 3
    win.search_field.setStringValue_("find")
    win.controlTextDidChange_(type("N", (), {"object": lambda self: win.search_field})())
    assert [type(i).__name__ for i in win.items] == ["_Header", "_DictationRow"]
