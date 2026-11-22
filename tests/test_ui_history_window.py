"""The Qt History window (lyssna/ui/history_window.py), built offscreen and never shown: what it
shows from the store and the dictionary, and what its controls do. The port of
tests/legacy/test_history_window.py. The store and the dictionary live under the per-test temp
dir; the clipboard, the copied-check timer and the confirmation are faked."""

import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from lyssna import host, support
from lyssna.dictation.history import HistoryStore
from lyssna.dictation.types import DictationResult
from lyssna.dictation.vocabulary import Vocabulary
from lyssna.ui import history_window as HW
from lyssna.ui.layout import history as layout
from lyssna.ui.window import Label

NOW = datetime(2026, 11, 17, 16, 0, tzinfo=timezone(timedelta(hours=1)))  # a Tuesday


@pytest.fixture(autouse=True)
def no_side_effects(qapp, monkeypatch):
    """No real clipboard, no timers."""
    copied = []
    monkeypatch.setattr(HW, "copy_to_clipboard", copied.append)
    monkeypatch.setattr(HW, "later", lambda delay, fn, context: None)
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
def win(qapp, store, vocab, clock):
    w = HW.HistoryWindow()
    w.store = store
    w.vocabulary = vocab
    w.now = clock
    yield w
    w.close()
    w.deleteLater()


def add(store, clock, text, when=None, raw=None):
    if when is not None:
        clock.t = when
    store.add(DictationResult(text=text, raw=raw if raw is not None else text.lower(), source="model",
                              transcriber="parakeet", cleaner="s1-mini", audio_seconds=2.0, seconds={},
                              words=len(text.split())))
    clock.t = NOW


def strings(w) -> list[str]:
    """Every text the window shows now: its labels, then the list's rows top to bottom."""
    out = [lbl.value() for lbl in w.findChildren(Label) if not lbl.isHidden() and lbl.value()]
    for item in w.items:
        out += item.texts()
    return out


def list_strings(w) -> list[str]:
    return [s for item in w.items for s in item.texts()]


def kinds(w):
    return [i.kind for i in w.items]


# -- the pure helpers ----------------------------------------------------------------------------


def test_figure_has_thousands_separators():
    assert HW.figure(0) == "0"
    assert HW.figure(18930) == "18,930"
    assert HW.figure(1234567) == "1,234,567"


@pytest.mark.parametrize("value, mac, pc", [
    (30, "Kept for 30 days on this Mac", "Kept for 30 days on this PC"),
    (1, "Kept for 1 day on this Mac", "Kept for 1 day on this PC"),
    (-1, "Kept on this Mac until you clear it", "Kept on this PC until you clear it"),
    (0, "History is off", "History is off"),
])
def test_retention_text_names_the_computer(value, mac, pc):
    assert HW.retention_text(value) == mac  # what the Mac's legacy window gets
    assert HW.retention_text(value, "Mac") == mac
    assert HW.retention_text(value, "PC") == pc


def test_empty_state_names_the_dictation_key():
    assert layout.EMPTY_DICTATIONS == "Your dictations will appear here. Hold fn and speak."  # the Mac's, unchanged
    assert layout.empty_dictations("right Alt") == "Your dictations will appear here. Hold right Alt and speak."


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


def test_time_label_is_hours_and_minutes(qapp):
    text = HW.time_label(NOW)
    local = NOW.astimezone()
    assert str(local.minute).zfill(2) in text and any(c.isdigit() for c in text)
    assert len(text) <= 9  # "4:00 PM" or "16:00", never with seconds


def test_time_label_comes_from_the_systems_formatter(monkeypatch):
    """Qt's system locale misses a Mac's 24-hour region setting, so times go through the twin."""
    from lyssna import host

    monkeypatch.setattr(host.appearance, "short_time", lambda when: f"<{when.minute}>")
    assert HW.time_label(NOW) == f"<{NOW.minute}>"


def test_content_column_stays_centred_on_wide_windows():
    assert HW.column(560) == (layout.MARGIN, 560 - 2 * layout.MARGIN)
    assert HW.column(900) == ((900 - layout.COLUMN_MAX) / 2, layout.COLUMN_MAX)


# -- the window ----------------------------------------------------------------------------------


def test_builds_offscreen_with_the_brand_sizes(win):
    win.prepare()
    assert not win.isVisible()
    assert (win.width(), win.height()) == (HW.W, HW.H)
    assert (win.minimumWidth(), win.minimumHeight()) == (HW.MIN_W, HW.MIN_H)
    assert win.windowTitle() == "Lyssna History"
    assert win.maximumWidth() > HW.W  # resizable
    assert "History" in strings(win)


def test_empty_state(win):
    from lyssna.menu import key_name

    win.prepare()
    assert kinds(win) == ["empty"]
    assert layout.empty_dictations(key_name(host.DICTATION_KEY_DEFAULT)) in list_strings(win)  # this computer's key
    assert not win.clear_button.isEnabled()


def test_empty_state_and_footer_on_a_pc(win, monkeypatch):
    monkeypatch.setattr(host, "DEVICE", "PC")
    win.ctl = type("Ctl", (), {"cfg": {"dictation": {"hotkey": "right_control"}}})()
    win.prepare()
    assert win.retention.value() == "Kept for 30 days on this PC"
    from lyssna.menu import key_name

    assert layout.empty_dictations(key_name("right_control")) in list_strings(win)


def test_clear_history_matches_the_retention_line(win):
    win.prepare()
    assert win.clear_button.font().weight() == win.retention.font().weight()  # regular, not medium


def test_stats_row(win, store, clock):
    add(store, clock, "one two three", NOW - timedelta(days=8))  # last week
    add(store, clock, "four five")
    win.prepare()
    assert [n.value() for n in win.stat_numbers] == ["2", "5", "2"]
    assert [lbl.value() for lbl in win.stat_labels] == ["words this week", "words in total", "dictations"]


def test_stats_use_thousands_separators(win, store, clock):
    add(store, clock, "word " * 1500)
    win.prepare()
    assert win.stat_numbers[1].value() == "1,500"


def test_no_support_line_without_a_website(win, store, clock):
    add(store, clock, "word " * 1500)
    win.prepare()
    assert win.support_label.isHidden() and win.support_button.isHidden()


def test_support_line_waits_for_a_thousand_words(win, store, clock, monkeypatch):
    opened = []
    monkeypatch.setattr(support, "SITE_URL", "https://example.test")
    monkeypatch.setattr(support, "_open", opened.append)
    add(store, clock, "word " * 999)
    win.prepare()
    assert win.support_label.isHidden() and win.support_button.isHidden()
    tabs_y = win.tabs.y()
    add(store, clock, "one more")
    win._refresh()
    assert not win.support_label.isHidden() and not win.support_button.isHidden()
    assert support.HISTORY_LINE in strings(win)
    assert win.tabs.y() > tabs_y  # the line takes its own row above the tabs
    label, button = win.support_label.geometry(), win.support_button.geometry()
    assert button.x() >= label.x() + label.width()  # the link follows the sentence
    assert win.support_button.symbol == support.SYMBOL  # every support link carries the heart
    assert opened == []  # it never opens on its own
    win.support_button.click()
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
    collapsed = row.height()
    assert collapsed == round(2 * layout.ROW_PAD_Y + layout.TEXT_LINES * layout.TEXT_LINE_H)
    row.on_click()
    row = win.items[1]
    assert row.expanded
    assert row.height() > collapsed * 2
    row.on_click()
    assert not win.items[1].expanded


def test_tab_clicks_reach_the_window(win):
    win.prepare()
    win.tabs.on_change(1)
    assert win._tab == "dictionary"
    win.tabs.on_change(0)
    assert win._tab == "dictations"


def test_the_tab_capsule_jumps_when_offscreen(win):
    win.prepare()
    win.select_tab("dictionary")
    assert win.tabs.anim is None and win.tabs.pos_ == (win.tabs.lefts[1], win.tabs.widths[1])


def test_row_actions_are_icons_with_tooltips(win, store, clock):
    add(store, clock, "Icons please.")
    win.prepare()
    row = win.items[1]
    assert (row.copy.icon, row.copy.toolTip()) == ("doc.on.doc", "Copy")
    assert (row.more.icon, row.more.toolTip()) == ("ellipsis", "More")
    assert row.copy.width() == layout.ICON_BTN
    # both sit on the first line of the text, not the middle of the row
    assert row.copy.y() == row.more.y() < layout.ROW_PAD_Y + layout.TEXT_LINE_H


def test_copy_and_more_appear_on_hover(win, store, clock):
    add(store, clock, "Hover me.")
    win.prepare()
    row = win.items[1]
    assert row.copy.isHidden() and row.more.isHidden()
    row.set_hover(True)
    assert not row.copy.isHidden() and not row.more.isHidden()
    row.set_hover(False)
    assert row.copy.isHidden()


def test_copy_puts_the_text_on_the_clipboard(win, store, clock, no_side_effects, monkeypatch):
    add(store, clock, "Copy this exactly.")
    win.prepare()
    row = win.items[1]
    restore = []
    monkeypatch.setattr(HW, "later", lambda delay, fn, context: restore.append((delay, fn)))
    row.copy.perform_click()
    assert no_side_effects == ["Copy this exactly."]
    assert row.copy.icon == "checkmark"  # the copy icon turns into a check for a moment
    [(delay, fn)] = restore
    assert delay == layout.COPIED_FOR
    fn()
    assert row.copy.icon == "doc.on.doc"


def test_more_menu(win, store, clock):
    add(store, clock, "Menu please.")
    win.prepare()
    entry = store.entries()[0]
    menu = win._more_menu(entry)
    assert [a.text() for a in menu.actions()] == ["Show Original", "Fix a Word…", "Delete"]


def test_more_menu_actions_act_on_their_entry(win, store, clock):
    add(store, clock, "Delete me from the menu.")
    win.prepare()
    menu = win._more_menu(store.entries()[0])
    menu.actions()[2].trigger()
    assert store.entries() == []


def test_show_original_is_off_without_one(win, store, clock):
    add(store, clock, "Nothing raw.", raw="")
    win.prepare()
    assert not win._more_menu(store.entries()[0]).actions()[0].isEnabled()


def test_show_original_toggles_the_raw_transcript(win, store, clock):
    add(store, clock, "Send it to Mattias.", raw="send it to matias")
    win.prepare()
    entry_id = win.items[1].entry_id
    assert "send it to matias" not in list_strings(win)
    win.toggle_original(entry_id)
    assert "send it to matias" in list_strings(win)
    assert win._more_menu(store.entries()[0]).actions()[0].text() == "Hide Original"
    win.toggle_original(entry_id)
    assert "send it to matias" not in list_strings(win)


def test_delete_from_the_menu(win, store, clock):
    add(store, clock, "Keep me.", NOW - timedelta(minutes=5))
    add(store, clock, "Delete me.")
    win.prepare()
    win.delete_entry(store.entries()[0].id)
    assert [e.text for e in store.entries()] == ["Keep me."]
    assert "Delete me." not in list_strings(win)


def test_fix_a_word_learns_the_term_and_saves_the_text(win, store, vocab, clock):
    add(store, clock, "Send the draft to Matias before lunch.")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fix_word(entry_id)
    row = win._row(entry_id)
    assert row.editing and row.edit_field is not None
    assert row.edit_field.text() == "Send the draft to Matias before lunch."
    assert row.copy.isHidden() and row.hover_views == []
    row.edit_field.setPlainText("Send the draft to Mattias before lunch.")
    row.save.click()
    assert store.entries()[0].text == "Send the draft to Mattias before lunch."
    assert "Mattias" in vocab.terms()
    assert "Learned: Mattias" in list_strings(win)
    assert not win._row(entry_id).editing


def test_return_saves_and_escape_cancels_a_fix(win, store, clock):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    add(store, clock, "Meet at the cafe at noon.")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fix_word(entry_id)
    field = win._row(entry_id).edit_field
    field.setPlainText("Meet at the bar at noon.")
    field.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert store.entries()[0].text == "Meet at the bar at noon."
    win.fix_word(entry_id)
    field = win._row(entry_id).edit_field
    field.setPlainText("Changed my mind.")
    field.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert store.entries()[0].text == "Meet at the bar at noon."
    assert not win._row(entry_id).editing


def test_fix_a_word_with_nothing_to_learn_says_nothing(win, store, vocab, clock):
    add(store, clock, "Meet at the cafe at noon.")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fix_word(entry_id)
    row = win._row(entry_id)
    row.edit_field.setPlainText("Meet at the bar at noon.")
    win.save_fix()
    assert store.entries()[0].text == "Meet at the bar at noon."
    assert vocab.terms() == []
    assert not any(s.startswith("Learned") for s in list_strings(win))


def test_the_fix_field_grows_with_its_text(win, store, clock):
    add(store, clock, "Short.")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fix_word(entry_id)
    row = win._row(entry_id)
    before = row.height()
    row.edit_field.setPlainText("Much longer now. " * 30)
    assert win._row(entry_id).height() > before


def test_cancel_fix_keeps_the_text(win, store, clock):
    add(store, clock, "Leave this alone.")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fix_word(entry_id)
    win._row(entry_id).edit_field.setPlainText("Changed my mind.")
    win._row(entry_id).cancel.click()
    assert store.entries()[0].text == "Leave this alone."
    assert not win._row(entry_id).editing


def test_a_new_dictation_does_not_sweep_away_a_fix_in_progress(win, store, clock):
    add(store, clock, "Being fixed.", NOW - timedelta(minutes=5))
    win.prepare()
    entry_id = win.items[1].entry_id
    win.fix_word(entry_id)
    win._row(entry_id).edit_field.setPlainText("Being fixed by hand.")
    add(store, clock, "Arrived meanwhile.")
    win.tick()
    assert win._row(entry_id).editing
    assert win._row(entry_id).edit_field.text() == "Being fixed by hand."
    assert win.stat_numbers[2].value() == "2"  # the totals still move
    win.cancel_fix()
    assert "Arrived meanwhile." in list_strings(win)


def test_a_rebuild_while_fixing_keeps_what_was_typed(win, store, clock):
    add(store, clock, "Other row.", NOW - timedelta(minutes=5))
    add(store, clock, "Being fixed.")
    win.prepare()
    fixing, other = win.items[1].entry_id, win.items[2].entry_id
    win.fix_word(fixing)
    win._row(fixing).edit_field.setPlainText("Typed so far")
    win._toggle_expanded(other)
    assert win._row(fixing).edit_field.text() == "Typed so far"


def test_search_filters_the_list(win, store, clock):
    add(store, clock, "Lunch with Mattias.", NOW - timedelta(minutes=5))
    add(store, clock, "Ship the build.")
    win.prepare()
    win.search_field.setText("mattias")
    assert [i.entry.text for i in win.items if i.kind == "dictation"] == ["Lunch with Mattias."]
    win.search_field.setText("nothing like it")
    assert kinds(win) == ["empty"]
    assert layout.EMPTY_SEARCH in list_strings(win)


def test_long_lists_are_capped_with_show_older(win, store, clock, days, monkeypatch):
    monkeypatch.setattr(HW, "ROW_CAP", 5)
    win._cap = 5
    days["days"] = -1
    for i in range(12):
        add(store, clock, f"note {i}", NOW - timedelta(minutes=i))
    win.prepare()
    assert sum(k == "dictation" for k in kinds(win)) == 5
    assert kinds(win)[-1] == "pill"
    assert win.items[-1].texts() == ["Show Older"]
    win.items[-1].button.click()
    assert sum(k == "dictation" for k in kinds(win)) == 10
    win.show_older()
    assert sum(k == "dictation" for k in kinds(win)) == 12
    assert kinds(win)[-1] != "pill"


def test_clear_history_asks_first_and_keeps_totals(win, store, clock, monkeypatch):
    add(store, clock, "one two three")
    win.prepare()
    asked = []
    monkeypatch.setattr(win, "_confirm", lambda message, info, ok, then: asked.append((message, info, ok, then)))
    win.clear_button.click()
    [(message, info, ok, then)] = asked
    assert message == "Delete all saved dictations?" and info == "Word totals are kept." and ok == "Delete"
    assert store.entries()  # nothing until the user agrees
    then()
    assert store.entries() == []
    assert kinds(win) == ["empty"]
    assert win.stat_numbers[1].value() == "3"


def test_the_confirmation_does_nothing_on_cancel(win, store, clock):
    add(store, clock, "Keep me.")
    win.prepare()
    from PySide6.QtWidgets import QMessageBox

    agreed = []
    win._confirm("Delete all saved dictations?", "Word totals are kept.", "Delete", lambda: agreed.append(True))
    box = win.findChild(QMessageBox)
    assert box.text() == "Delete all saved dictations?" and box.informativeText() == "Word totals are kept."
    assert sorted(b.text() for b in box.buttons()) == ["Cancel", "Delete"]
    box.escapeButton().click()
    assert agreed == []


def test_footer_tells_the_retention(win, days):
    win.prepare()
    assert win.retention.value() == f"Kept for 30 days on this {host.DEVICE}"
    days["days"] = -1
    win.tick()
    assert win.retention.value() == f"Kept on this {host.DEVICE} until you clear it"
    days["days"] = 0
    win.tick()
    assert win.retention.value() == "History is off"


def test_the_window_picks_up_a_new_dictation(win, store, clock):
    win.prepare()
    assert kinds(win) == ["empty"]
    add(store, clock, "Just arrived.")
    win.tick()
    assert "Just arrived." in list_strings(win)


def test_nothing_is_rebuilt_while_nothing_changed(win, store, clock):
    add(store, clock, "Still here.")
    win.prepare()
    first = list(win.items)
    win.tick()
    assert win.items == first


def test_resizing_keeps_the_layout(win, store, clock):
    add(store, clock, "Some words. " * 30)
    win.prepare()
    win.resize(round(HW.MIN_W), round(HW.MIN_H))
    win._layout()
    assert win.scroll.width() == HW.MIN_W
    assert win.items[1].width() <= HW.MIN_W


def test_a_wide_window_centres_the_column(win, store, clock):
    add(store, clock, "Centre me.")
    win.prepare()
    win.resize(1000, round(HW.H))
    win._layout()
    x0, inner = HW.column(1000)
    assert win.search_box.x() == x0 and win.search_box.width() == inner
    assert win.items[1].x() == x0 - layout.ROW_PAD_X


def test_closing_forgets_what_was_open(win, store, clock):
    add(store, clock, "Open me.", raw="open me")
    win.prepare()
    entry_id = win.items[1].entry_id
    win.toggle_original(entry_id)
    win._toggle_expanded(entry_id)
    win.close()
    assert not win._originals and not win._expanded and not win._timer.isActive()


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
    assert layout.EMPTY_DICTIONARY in list_strings(win)


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
    assert "heard as: mathias, matias" in list_strings(win)  # most seen first, then by name
    assert rows[0].delete.isHidden()
    rows[0].set_hover(True)
    assert not rows[0].delete.isHidden()


def test_add_a_word_from_the_field(win, vocab):
    win.prepare()
    win.select_tab("dictionary")
    win.add_field.setText("  Uppsala ")
    win.add_field.returnPressed.emit()  # Return adds
    assert vocab.terms() == ["Uppsala"]
    assert win.add_field.text() == ""
    assert [i.term for i in win.items if i.kind == "term"] == ["Uppsala"]
    win.add_field.setText("Kokoro")
    win.add_button.click()
    assert "Kokoro" in vocab.terms()


def test_add_a_word_that_is_too_long(win, vocab):
    win.prepare()
    win.select_tab("dictionary")
    win.add_field.setText("x" * 200)
    win.add_term()
    assert vocab.terms() == []
    assert "That is too long for the dictionary." in strings(win)


def test_remove_a_word(win, vocab):
    vocab.add("Kokoro")
    win.prepare()
    win.select_tab("dictionary")
    row = win.items[0]
    row.delete.perform_click()
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
    win.tick()
    assert [i.term for i in win.items if i.kind == "term"] == ["Lyssna"]


# -- speed and privacy ---------------------------------------------------------------------------


def test_a_full_page_of_rows_builds_quickly(win, store, clock, days):
    import time

    days["days"] = -1
    for i in range(layout.ROW_CAP + 20):
        add(store, clock, f"Dictation number {i}, with a sentence or two of ordinary length to wrap. " * 2,
            NOW - timedelta(minutes=10 * i))
    started = time.perf_counter()
    win.prepare()
    built = time.perf_counter() - started
    assert sum(k == "dictation" for k in kinds(win)) == layout.ROW_CAP and kinds(win)[-1] == "pill"
    assert built < 2.0, f"building {layout.ROW_CAP} rows took {built:.2f} s"


def test_nothing_dictated_reaches_the_log(win, store, vocab, clock, caplog):
    secret = "The door code for Matias is in the blue folder."
    add(store, clock, secret)
    with caplog.at_level("DEBUG"):
        win.prepare()
        entry_id = win.items[1].entry_id
        win.fix_word(entry_id)
        row = win._row(entry_id)
        row.edit_field.setPlainText(secret.replace("Matias", "Mattias"))
        win.save_fix()
        win.delete_entry(entry_id)
    logged = "\n".join(r.getMessage() for r in caplog.records).lower()
    for word in ("door", "folder", "blue", "matias", "mattias"):
        assert word not in logged


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit's own letter positions")
def test_the_fix_field_puts_each_letter_where_appkit_does():
    """The fix field lays its text out itself (a QTextEdit); on the Mac each letter gets AppKit's
    advance, so a line ends where the Mac's field editor ends it rather than up to a pixel short.
    The typed text itself is untouched."""
    from PySide6.QtWidgets import QWidget

    from lyssna.mac import appearance
    from lyssna.ui import theme

    holder = QWidget()
    field = HW.FixField(HW.FieldBox(holder), lambda: None, lambda: None, lambda: None)
    text = "Can you send the updated deck to Mattias before the call?"
    field.setPlainText(text)
    field.document().setTextWidth(2000)
    line = field.document().begin().layout().lineAt(0)
    want = appearance.text_offsets(text, theme.text_style(field.font()))
    got = [line.cursorToX(i)[0] for i in range(len(text) + 1)]
    assert max(abs(a - b) for a, b in zip(got, want, strict=True)) < 0.1
    assert field.text() == text
