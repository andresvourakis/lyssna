"""The What's New window, built offscreen (never put on screen).

Needs a window server to build an NSWindow: the module skips without one."""

import AppKit
import pytest
from AppKit import NSTextField

from lyssna import support, whatsnew
from lyssna.ui import whats_new_window as WN

pytestmark = pytest.mark.skipif(not AppKit.NSScreen.screens(), reason="no window server")

NOTES = {"9.9.9": ("First thing.", "Second thing, long enough that it has to wrap onto a second line in the window.")}


@pytest.fixture(scope="module", autouse=True)
def app():
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    return app


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(whatsnew, "NOTES", NOTES)
    w = WN.WhatsNewWindow.alloc().init()
    w.version = "9.9.9"
    yield w
    if w.window is not None:
        w.window.close()


def strings(view) -> list[str]:
    return [str(s.stringValue()) for s in view.subviews() if isinstance(s, NSTextField) and not s.isHidden()]


def test_shows_the_versions_highlights(win):
    win.prepare()
    assert not win.window.isVisible()
    texts = strings(win.content)
    assert "What's new in Lyssna 9.9.9" in texts
    assert [str(lbl.stringValue()) for lbl in win.note_labels] == list(NOTES["9.9.9"])
    one, two = (lbl.frame().size.height for lbl in win.note_labels)
    assert two > one  # the long highlight wraps
    assert win.window.contentRectForFrameRect_(win.window.frame()).size.width == WN.W


def test_no_support_line_without_a_website(win):
    win.prepare()
    assert win.support_label.isHidden() and win.support_button.isHidden()
    assert not win.changes_button.isHidden()


def test_support_line_opens_the_support_page(win, monkeypatch):
    opened = []
    monkeypatch.setattr(support, "SITE_URL", "https://example.test")
    monkeypatch.setattr(support, "_open", opened.append)
    win.prepare()
    assert not win.support_label.isHidden()
    assert str(win.support_label.stringValue()) == support.WHATS_NEW_LINE
    assert win.support_button.image() is not None  # every support link carries the heart
    win.support_button.performClick_(None)
    assert opened == ["https://example.test/?support=1"]
    win.changes_button.performClick_(None)
    assert opened[-1] == WN.CHANGES_URL
