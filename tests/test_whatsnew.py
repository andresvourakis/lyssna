"""When the menu offers the What's New note (lyssna.whatsnew)."""

import lyssna
from lyssna import config, whatsnew

NOTES = {"1.0.0": ("One thing.",), "1.1.0": ("Another thing.",), "1.1.1": ()}


def test_the_current_version_has_an_entry():
    """Every release decides what its note says. An empty entry means: nothing to announce."""
    assert lyssna.__version__ in whatsnew.NOTES, "add this version to lyssna/whatsnew.py NOTES (may be empty)"


def test_notes_are_short_plain_lines():
    for lines in whatsnew.NOTES.values():
        assert len(lines) <= 5
        for line in lines:
            assert line.strip() == line and line.endswith(".") and len(line) <= 120 and "—" not in line


def test_pending_rules(monkeypatch):
    monkeypatch.setattr(whatsnew, "NOTES", NOTES)
    assert whatsnew.pending("1.1.0", "1.0.0", True) is True      # an update
    assert whatsnew.pending("1.1.0", "1.1.0", True) is False     # already dealt with
    assert whatsnew.pending("1.1.0", None, False) is False       # a first install
    assert whatsnew.pending("1.1.0", None, True) is True         # an update from before notes existed
    assert whatsnew.pending("1.1.1", "1.1.0", True) is False     # nothing to announce
    assert whatsnew.pending("2.0.0", "1.1.0", True) is False     # no entry at all


def test_a_first_install_records_the_version_and_offers_nothing(monkeypatch):
    monkeypatch.setattr(whatsnew, "NOTES", NOTES)
    config.ensure_dirs()
    assert whatsnew.check("1.0.0", existing_install=False) is False
    assert whatsnew.read_seen() == "1.0.0"
    assert whatsnew.check("1.1.0", existing_install=True) is True  # the next update is recognised


def test_a_note_stays_offered_until_it_is_opened(monkeypatch):
    monkeypatch.setattr(whatsnew, "NOTES", NOTES)
    config.ensure_dirs()
    whatsnew.mark_seen("1.0.0")
    assert whatsnew.check("1.1.0", existing_install=True) is True
    assert whatsnew.check("1.1.0", existing_install=True) is True  # still there after a relaunch
    whatsnew.mark_seen("1.1.0")
    assert whatsnew.check("1.1.0", existing_install=True) is False


def test_a_version_with_nothing_to_announce_is_recorded_quietly(monkeypatch):
    monkeypatch.setattr(whatsnew, "NOTES", NOTES)
    config.ensure_dirs()
    whatsnew.mark_seen("1.1.0")
    assert whatsnew.check("1.1.1", existing_install=True) is False
    assert whatsnew.read_seen() == "1.1.1"


def test_titles():
    assert whatsnew.menu_title("0.3.0") == "What's New in 0.3.0…"
    assert whatsnew.window_title("0.3.0") == "What's new in Lyssna 0.3.0"
