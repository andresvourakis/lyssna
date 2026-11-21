"""The Windows play button beside a selection (lyssna/ui/qt/selection_button.py): where it lands,
how it converts the hooks' physical pixels, when it goes away and what a click does. Runs on the
offscreen platform with a fixed fake screen, so it needs no display."""

import os
import threading
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6.QtWidgets")

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

SCREEN_H = 720.0


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("a non-GUI Qt application already exists in this process")
    return app or QApplication(["lyssna-tests"])


def pump(seconds: float = 0.0, until=None) -> None:
    end = time.monotonic() + (seconds if until is None else 2.0)
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


@pytest.fixture
def screen(qapp, monkeypatch):
    """One 1280 x 720 logical screen. `scale` sets its physical scale for the hook's points."""
    from lyssna.ui.qt import coords

    table = [((0.0, 0.0, 1280.0, SCREEN_H), 1.0)]
    monkeypatch.setattr(coords, "primary_height", lambda: SCREEN_H)
    monkeypatch.setattr(coords, "visible_frame", lambda screen=None: (0.0, 0.0, 1280.0, SCREEN_H - 40.0))
    monkeypatch.setattr(coords, "physical_to_logical", lambda x, y: coords.map_physical_to_logical(x, y, table))
    monkeypatch.setattr(coords, "physical_rect_to_logical", lambda r: coords.map_physical_rect_to_logical(r, table))

    def scale(s: float) -> None:
        table[0] = (table[0][0], s)
    return scale


@pytest.fixture
def button(screen):
    from lyssna.ui.qt.selection_button import SelectionButton

    b = SelectionButton()
    yield b
    if b._timer:
        b._timer.cancel()
    b.hide()


def test_size_and_look(button):
    from lyssna.ui.qt import theme

    assert button.frame()[2:] == (34.0, 34.0) and button.corner_radius == 17.0
    assert button.surface_fill() == theme.content() and button.surface_border() is None
    assert button.button.icon == "play.fill" and button.button.glow is theme.white_glow


def test_sits_above_the_selection_centred_on_the_cursor(button):
    button._present(150, 310, None, (100, 300, 200, 20))
    # 10 above the selection's top (300), centred on the cursor's x.
    assert button.frame() == (133.0, 256.0, 34.0, 34.0)
    assert button.visible()


def test_without_a_selection_it_floats_above_the_cursor_line(button):
    from lyssna.ui.shared.button import LINE_HEIGHT, OFFSET

    button._present(400, 300, None, None)
    assert button.frame() == (383.0, 300 - LINE_HEIGHT / 2 - OFFSET - 34, 34.0, 34.0)


def test_the_cursor_is_kept_within_the_selection(button):
    button._present(20, 310, None, (100, 300, 200, 20))
    assert button.frame()[0] == 100 - 17


def test_flips_below_when_it_would_cover_the_player(button):
    from lyssna.ui.shared.button import OFFSET

    button._present(150, 310, (100.0, 250.0, 120.0, 40.0), (100, 300, 200, 20))  # the player's frame, Qt logical
    assert button.frame()[1] == 300 + 20 + OFFSET


def test_a_mac_style_avoid_rect_works_too(button):
    from lyssna.ui.qt.selection_button import mac_avoid

    mac = mac_avoid((100.0, 250.0, 120.0, 40.0))
    assert (mac.origin.x, mac.origin.y, mac.size.width, mac.size.height) == (100.0, SCREEN_H - 290.0, 120.0, 40.0)
    assert mac_avoid(mac) is mac and mac_avoid(None) is None
    button._present(150, 310, mac, (100, 300, 200, 20))
    assert button.frame()[1] == 330.0


def test_clamped_to_the_screen(button):
    button._present(3, 30, None, None)
    x, y, _, _ = button.frame()
    assert (x, y) == (0.0, 0.0)
    button._present(1279, 700, None, None)
    x, y, w, h = button.frame()
    assert x + w <= 1280.0 and y + h <= SCREEN_H - 40.0


def test_physical_points_are_converted_on_a_150_percent_screen(button, screen):
    screen(1.5)
    button._present(225, 465, None, (150, 450, 300, 30))  # physical; logical (150, 310) and (100, 300, 200, 20)
    assert button.frame() == (133.0, 256.0, 34.0, 34.0)
    assert button.contains_screen_point(225, 435)  # physical (150, 290): inside
    assert not button.contains_screen_point(150, 435)


def test_present_and_dismiss_from_a_worker(button):
    threading.Thread(target=button.present, args=(150, 310, None, (100, 300, 200, 20))).start()
    pump(until=button.visible)
    assert button.visible()
    threading.Thread(target=button.dismiss).start()
    pump(until=lambda: not button.visible())
    assert not button.visible()


def test_goes_away_after_the_timeout(button):
    button.timeout = 0.05
    button._present(150, 310, None, None)
    assert button.visible()
    pump(until=lambda: not button.visible())
    assert not button.visible()


def test_a_new_selection_restarts_the_timeout(button):
    button.timeout = 0.15
    button._present(150, 310, None, None)
    pump(0.1)
    button._present(160, 310, None, None)
    pump(0.1)
    assert button.visible()


def test_click_hides_then_plays(button):
    seen = []
    button.on_play = lambda: seen.append(button.visible())
    button._present(150, 310, None, None)
    target = button.button
    centre = QPointF(target.width() / 2, target.height() / 2)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(kind, centre, target.mapToGlobal(centre), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, ev)
    assert seen == [False]
    assert not button.visible()


def test_never_takes_focus(button):
    flags = button.panel.windowFlags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert button.panel.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert button.button.focusPolicy() == Qt.FocusPolicy.NoFocus
