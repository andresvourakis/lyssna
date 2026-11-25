"""The Windows play button beside a selection (lyssna/ui/selection_button.py): where it lands,
how it converts the hooks' physical pixels, when it goes away and what a click does. Runs on the
offscreen platform with a fixed fake screen, so it needs no display."""

import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

SCREEN_H = 720.0


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
    from lyssna.ui import coords

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
    from lyssna.ui.selection_button import SelectionButton

    b = SelectionButton()
    yield b
    if b._timer:
        b._timer.cancel()
    b.hide()


def test_size_and_look(button):
    from lyssna.ui import theme

    assert button.frame()[2:] == (34.0, 34.0) and button.corner_radius == 17.0
    assert button.surface_fill() == theme.content() and button.surface_border() is None
    # The Mac's button has no hover light (a bare symbol button on the panel's disc).
    assert button.button.icon == "play.fill" and button.button.glow is None
    button.button.set_hover(True, animate=False)
    assert button.button.glow_level() == 1.0 and button.button.glow is None  # nothing to paint


def test_pressed_tint_is_the_macs(button):
    """A pressed borderless NSButton draws its symbol with the pressed tint and leaves the disc."""
    from PySide6.QtGui import QColor

    from lyssna.ui import theme
    from lyssna.ui.widgets import pressed_tint

    theme.set_scheme("light")
    try:
        assert pressed_tint(QColor(255, 255, 255)).red() == 215
        assert pressed_tint(QColor(128, 128, 128)).red() == 98
        assert pressed_tint(QColor(0, 0, 0)).red() == 0
        theme.set_scheme("dark")
        assert pressed_tint(QColor(0, 0, 0)).red() == 46
        assert pressed_tint(QColor(128, 128, 128)).red() == 174
        assert pressed_tint(QColor(244, 239, 230)) == QColor(255, 255, 255)
    finally:
        theme.set_scheme(None)


def test_sits_above_the_selection_centred_on_the_cursor(button):
    button._present(150, 310, None, (100, 300, 200, 20))
    # 10 above the selection's top (300), centred on the cursor's x.
    assert button.frame() == (133.0, 256.0, 34.0, 34.0)
    assert button.visible()


def test_without_a_selection_it_floats_above_the_cursor_line(button):
    from lyssna.ui.layout.button import LINE_HEIGHT, OFFSET

    button._present(400, 300, None, None)
    assert button.frame() == (383.0, 300 - LINE_HEIGHT / 2 - OFFSET - 34, 34.0, 34.0)


def test_the_cursor_is_kept_within_the_selection(button):
    button._present(20, 310, None, (100, 300, 200, 20))
    assert button.frame()[0] == 100 - 17


def test_flips_below_when_it_would_cover_the_player(button):
    from lyssna.ui.layout.button import OFFSET

    button._present(150, 310, (100.0, 250.0, 120.0, 40.0), (100, 300, 200, 20))  # the player's frame, Qt logical
    assert button.frame()[1] == 300 + 20 + OFFSET


def test_a_mac_style_avoid_rect_works_too(button):
    from lyssna.ui.selection_button import mac_avoid

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


# -- moving off another app's pop-up (Edge's selection toolbar) ------------------------------

# Measured on a Windows 11 PC at 100 %: the cursor at (1497, 1208) after selecting a line, and
# Edge's toolbar at left 1502, top 826, right 1662, bottom 1234 (so x, y, w, h below).
CURSOR = (1497.0, 1208.0)
LINE = (1300.0, 1195.0, 197.0, 26.0)  # the selected text, ending at the cursor
TOOLBAR = (1502.0, 826.0, 160.0, 408.0)
SIZE = (34.0, 34.0)
DEFAULT = (1480.0, 1151.0)  # centred on the cursor, 10 above the line: what place() answers


def test_clear_spot_keeps_the_default_without_an_obstacle():
    from lyssna.ui.layout.button import clear_spot

    assert clear_spot(DEFAULT, SIZE, *CURSOR, [], LINE) == DEFAULT
    assert clear_spot(DEFAULT, SIZE, *CURSOR, [(0.0, 0.0, 100.0, 100.0)], LINE) == DEFAULT


def test_clear_spot_flips_below_the_selection():
    from lyssna.ui.layout.button import OFFSET, clear_spot

    short = (1502.0, 1000.0, 160.0, 200.0)  # a toolbar that ends above the line
    assert clear_spot(DEFAULT, SIZE, *CURSOR, [short], LINE) == (DEFAULT[0], 1195.0 + 26.0 + OFFSET)


def test_clear_spot_goes_left_of_edges_toolbar():
    from lyssna.ui.layout.button import OFFSET, clear_spot

    # Below is covered too (the toolbar reaches 26 px under the cursor), so: just left of it.
    assert clear_spot(DEFAULT, SIZE, *CURSOR, [TOOLBAR], LINE) == (1502.0 - OFFSET - 34.0, 1151.0)


def test_clear_spot_goes_right_when_left_is_covered():
    from lyssna.ui.layout.button import OFFSET, clear_spot

    left = (1400.0, 1100.0, 80.0, 200.0)  # e.g. the player, beside the toolbar
    assert clear_spot(DEFAULT, SIZE, *CURSOR, [TOOLBAR, left], LINE) == (1662.0 + OFFSET, 1151.0)


def test_clear_spot_keeps_the_default_when_nothing_is_clear():
    from lyssna.ui.layout.button import clear_spot

    # The toolbar, plus something under the line and on both sides of the toolbar.
    walls = [TOOLBAR, (1000.0, 1200.0, 1000.0, 100.0), (1400.0, 1100.0, 80.0, 200.0), (1662.0, 1100.0, 80.0, 200.0)]
    assert clear_spot(DEFAULT, SIZE, *CURSOR, walls, LINE) == DEFAULT
    # Without a selection the cursor's line stands in for it.
    assert clear_spot(DEFAULT, SIZE, *CURSOR, walls) == DEFAULT


class PopupTwin:
    """host.window for the button: answers popups_over with `popups` (physical l, t, r, b)."""

    POINTS_ARE_PHYSICAL = False

    def __init__(self, detects: bool = True, popups=()):
        self.DETECTS_POPUPS = detects
        self.popups = list(popups)
        self.asked: list = []

    def popups_over(self, rect):
        self.asked.append(rect)
        return [p for p in self.popups if p[0] < rect[2] and p[2] > rect[0] and p[1] < rect[3] and p[3] > rect[1]]

    def __getattr__(self, name):  # the panel's native calls: nothing to do
        return lambda *a, **k: None


@pytest.fixture
def popup_twin(monkeypatch):
    from lyssna import host
    from lyssna.ui import selection_button

    def make(**kw):
        twin = PopupTwin(**kw)
        monkeypatch.setattr(host, "window", twin)
        return twin
    monkeypatch.setattr(selection_button, "POPUP_CHECKS", (0.01, 0.03))
    return make


def test_moves_off_a_pop_up_that_covers_it(button, popup_twin):
    from lyssna.ui.layout.button import OFFSET

    # The toolbar to the right of the cursor, from above the selection to just under it.
    twin = popup_twin(popups=[(155, 100, 315, 335)])
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button.frame() == (133.0, 256.0, 34.0, 34.0)  # the default spot first
    pump(until=lambda: button.frame()[0] != 133.0)
    assert button.frame() == (155 - OFFSET - 34.0, 256.0, 34.0, 34.0)
    assert twin.asked[0] == (133.0, 256.0, 167.0, 290.0)  # its own rect, (l, t, r, b)
    assert button.visible()


def test_does_not_move_without_a_pop_up(button, popup_twin):
    twin = popup_twin(popups=[(600, 100, 700, 200)])  # elsewhere on screen
    button._present(150, 310, None, (100, 300, 200, 20))
    pump(until=lambda: len(twin.asked) >= 2)
    pump(0.02)
    assert len(twin.asked) == 2 and button.frame() == (133.0, 256.0, 34.0, 34.0)


def test_stays_clear_of_the_player_when_it_moves(button, popup_twin):
    from lyssna.ui.layout.button import OFFSET

    popup_twin(popups=[(155, 100, 315, 335)])
    player = (60.0, 200.0, 60.0, 80.0)  # where the left spot would go
    button._present(150, 310, player, (100, 300, 200, 20))
    pump(until=lambda: button.frame()[0] != 133.0)
    assert button.frame() == (315 + OFFSET, 256.0, 34.0, 34.0)


def test_no_checks_where_pop_ups_cannot_be_found(button, popup_twin):
    twin = popup_twin(detects=False, popups=[(155, 100, 315, 335)])
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button._checks == []
    pump(0.08)
    assert twin.asked == [] and button.frame() == (133.0, 256.0, 34.0, 34.0)


def test_checks_are_cancelled_on_hide_and_on_a_new_selection(button, popup_twin):
    twin = popup_twin(popups=[(155, 100, 315, 335)])
    button._present(150, 310, None, (100, 300, 200, 20))
    first = list(button._checks)
    button._present(150, 310, None, (100, 300, 200, 20))
    assert all(t.finished.is_set() for t in first)  # cancelled
    button.hide()
    assert button._checks == []
    pump(0.08)
    assert twin.asked == []
