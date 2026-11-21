"""The Windows What's New window (lyssna/ui/qt/whats_new_window.py), built on the offscreen
platform and never shown."""

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6.QtWidgets")

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from lyssna import support, whatsnew

NOTES = {"9.9.9": ("First thing.", "Second thing, long enough that it has to wrap onto a second line in the window.")}


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("a non-GUI Qt application already exists in this process")
    return app or QApplication(["lyssna-tests"])


@pytest.fixture
def win(qapp, monkeypatch):
    from lyssna.ui.qt.whats_new_window import WhatsNewWindow

    monkeypatch.setattr(whatsnew, "NOTES", NOTES)
    w = WhatsNewWindow()
    w.version = "9.9.9"
    yield w
    w.hide()
    w.deleteLater()
    QCoreApplication.processEvents()


def test_shows_the_versions_highlights(win):
    from lyssna.ui.qt import whats_new_window as WN

    win.prepare()
    assert not win.isVisible()
    assert win.heading.title.value() == "What's new in Lyssna 9.9.9"
    assert [lbl.value() for lbl in win.note_labels] == list(NOTES["9.9.9"])
    one, two = (lbl.height() for lbl in win.note_labels)
    assert two > one  # the long highlight wraps
    assert win.width() == WN.W
    assert win.changes_button.geometry().bottom() < win.height()  # the height follows the text


def test_no_support_line_without_a_website(win):
    win.prepare()
    assert win.support_label.isHidden() and win.support_button.isHidden()
    assert not win.changes_button.isHidden()


def test_support_line_carries_the_heart_and_opens_the_support_page(win, monkeypatch):
    from lyssna.ui.qt import whats_new_window as WN

    opened = []
    monkeypatch.setattr(support, "SITE_URL", "https://example.test")
    monkeypatch.setattr(support, "_open", opened.append)
    win.prepare()
    assert not win.support_label.isHidden() and not win.support_button.isHidden()
    assert win.support_label.value() == support.WHATS_NEW_LINE
    assert win.support_button.text() == support.MENU_TITLE
    assert win.support_button.symbol == "heart.fill"  # every support link carries the heart
    assert opened == []  # it never opens on its own
    win.support_button.click()
    assert opened == ["https://example.test/?support=1"]
    win.changes_button.click()
    assert opened[-1] == WN.CHANGES_URL


def test_previews_build(qapp):
    from lyssna.ui.qt import whats_new_window

    for factory in whats_new_window.PREVIEWS.values():
        w = factory()
        assert w.height() > 0
        w.deleteLater()
    QCoreApplication.processEvents()
