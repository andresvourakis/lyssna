"""FloatingPanel gets itself on screen, and rebuilds its window when the server didn't.

Needs a real window server: the whole module skips without one."""

import AppKit
import pytest
import Quartz
from Foundation import NSDate, NSRunLoop

from lyssna.legacy.ui.panel import FloatingPanel

pytestmark = pytest.mark.skipif(not AppKit.NSScreen.screens(), reason="no window server")


@pytest.fixture(scope="module", autouse=True)
def app():
    """An accessory app: windows can show without stealing focus or a Dock icon."""
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    return app


def pump(seconds: float) -> None:
    """Let AppKit and the `later` timers run."""
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def on_screen_numbers() -> set:
    infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    return {info.get(Quartz.kCGWindowNumber) for info in infos}


def test_show_at_is_on_screen():
    p = FloatingPanel.alloc().init()
    p.show_at(0, 300)
    pump(0.3)
    assert p._on_screen()
    p.hide()


def test_rebuild_replaces_window_and_keeps_content():
    p = FloatingPanel.alloc().init()
    p.show_at(0, 300)
    pump(0.3)
    old = p.panel
    content = old.contentView()
    frame = old.frame()

    # Pretend the window server dropped the window once; the rebuilt one is fine.
    seen = []
    def fake_on_screen():
        seen.append(1)
        return len(seen) > 1
    p._on_screen = fake_on_screen

    p._verify_on_screen(old)
    pump(0.5)

    assert p.panel is not old
    assert p.panel.contentView() is content
    assert p.panel.frame() == frame
    assert p.panel.isVisible()
    assert p.panel.windowNumber() in on_screen_numbers()
    assert not old.isVisible()
    p.hide()


def test_verify_noop_when_healthy():
    p = FloatingPanel.alloc().init()
    p.show_at(0, 300)
    pump(0.3)
    panel = p.panel
    p._verify_on_screen(panel)
    assert p.panel is panel
    p.hide()
