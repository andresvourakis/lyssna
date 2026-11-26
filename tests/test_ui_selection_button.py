"""The Windows play button beside a selection (lyssna/ui/selection_button.py): where it lands,
how it converts the hooks' physical pixels, when it goes away and what a click does. Runs on the
offscreen platform with a fixed fake screen, so it needs no display."""

import threading
import time
from types import SimpleNamespace

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


# -- waiting for the pop-up in an app that has one (layout/popup_wait) ------------------------


def test_an_app_is_learned_the_first_time_a_pop_up_covers_the_button():
    from lyssna.ui.layout.popup_wait import FIRST_WAIT, PopupMemory

    m = PopupMemory()
    assert not m.knows("msedge.exe") and not m.knows(None)
    assert m.covered("msedge.exe", 0.12) is True  # learned; the checks' arrival is not kept
    assert m.knows("msedge.exe") and not m.knows("notepad.exe")
    assert m.deadline("msedge.exe") == FIRST_WAIT
    assert m.covered("msedge.exe", None) is False


def test_the_deadline_follows_the_slowest_arrival_and_is_capped():
    from lyssna.ui.layout.popup_wait import MARGIN, MAX_WAIT, PopupMemory

    m = PopupMemory()
    m.covered("msedge.exe")
    m.covered("msedge.exe", 0.1)
    assert m.deadline("msedge.exe") == pytest.approx(0.1 + MARGIN)
    m.covered("msedge.exe", 0.05)  # a faster one does not lower it
    assert m.deadline("msedge.exe") == pytest.approx(0.1 + MARGIN)
    m.covered("msedge.exe", 0.3)
    assert m.deadline("msedge.exe") == pytest.approx(0.3 + MARGIN)
    m.covered("msedge.exe", 0.9)  # a late one from the checks after showing
    assert m.deadline("msedge.exe") == MAX_WAIT


def test_an_app_is_forgotten_after_three_presentations_without_a_pop_up():
    from lyssna.ui.layout.popup_wait import FIRST_WAIT, FORGET_AFTER, PopupMemory

    assert FORGET_AFTER == 3
    m = PopupMemory()
    m.covered("msedge.exe")
    m.covered("msedge.exe", 0.1)
    assert m.missed("msedge.exe") is False and m.missed("msedge.exe") is False
    m.covered("msedge.exe", 0.1)  # a hit starts the run again
    assert m.missed("msedge.exe") is False and m.missed("msedge.exe") is False and m.knows("msedge.exe")
    assert m.missed("msedge.exe") is True and not m.knows("msedge.exe")
    assert m.missed("msedge.exe") is False  # nothing to forget
    m.covered("msedge.exe", 0.2)  # learned afresh: back to the first deadline
    assert m.deadline("msedge.exe") == FIRST_WAIT


class FakeTimers:
    """later() and the clock for the button: nothing runs until advance() moves the clock."""

    def __init__(self):
        self.now = 100.0
        self.timers: list = []

    def later(self, delay, fn, *args):
        t = SimpleNamespace(due=self.now + delay, delay=delay, fn=fn, args=args, cancelled=False)
        t.cancel = lambda: setattr(t, "cancelled", True)
        self.timers.append(t)
        return t

    def monotonic(self):
        return self.now

    def advance(self, seconds: float) -> None:
        end = self.now + seconds
        while True:
            due = [t for t in self.timers if not t.cancelled and t.due <= end + 1e-9]
            if not due:
                break
            t = min(due, key=lambda t: t.due)
            self.timers.remove(t)
            self.now = max(self.now, t.due)
            t.fn(*t.args)
        self.now = end


class AppTwin(PopupTwin):
    """PopupTwin that also names the app in front, and can fail on purpose."""

    def __init__(self, app="msedge.exe", **kw):
        super().__init__(**kw)
        self.app = app
        self.fail = False

    def front_app(self):
        return self.app

    def popups_over(self, rect):
        if self.fail:
            raise RuntimeError("no windows here")
        return super().popups_over(rect)


TOOLBAR_PX = (155, 100, 315, 335)  # to the right of the cursor, from above the selection to just under it


@pytest.fixture
def waits(monkeypatch):
    """A twin with an app in front, fake timers and a fake clock for the button."""
    from lyssna import host
    from lyssna.ui import selection_button

    clock = FakeTimers()
    monkeypatch.setattr(selection_button, "later", clock.later)
    monkeypatch.setattr(selection_button, "time", SimpleNamespace(monotonic=clock.monotonic))

    def make(**kw):
        twin = AppTwin(**kw)
        monkeypatch.setattr(host, "window", twin)
        return twin, clock
    return make


def _learn(button, twin, clock):
    """One presentation in Edge with its toolbar over the button: it shows, then jumps once."""
    twin.popups = [TOOLBAR_PX]
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button.visible() and button.frame()[0] == 133.0
    clock.advance(0.2)
    assert button.frame()[0] != 133.0  # moved off it
    assert button._memory.knows("msedge.exe")
    button.hide()


def test_an_unlearned_app_shows_at_once_as_before(button, waits):
    twin, clock = waits(popups=[(600, 100, 700, 200)])
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button.visible() and button.frame() == (133.0, 256.0, 34.0, 34.0)
    assert [t.delay for t in clock.timers] == [button.timeout, 0.12, 0.35, 0.7]
    clock.advance(1.0)
    assert button.frame() == (133.0, 256.0, 34.0, 34.0) and not button._memory.knows("msedge.exe")
    assert len(twin.asked) == 3  # only the checks after showing looked


def test_a_learned_app_shows_once_at_the_clear_spot_when_the_pop_up_comes(button, waits):
    from lyssna.ui.layout.button import OFFSET

    twin, clock = waits()
    _learn(button, twin, clock)
    twin.popups = []
    button._present(150, 310, None, (100, 300, 200, 20))
    assert not button.visible()  # waiting for the toolbar
    assert not button.contains_screen_point(150, 270)
    clock.advance(0.05)
    assert not button.visible()
    twin.popups = [TOOLBAR_PX]
    clock.advance(0.025)
    assert button.visible() and button.frame() == (155 - OFFSET - 34.0, 256.0, 34.0, 34.0)
    assert twin.asked[-1] == (133.0, 256.0, 167.0, 290.0)  # it looked over its would-be spot
    # The auto-hide counts from the moment it showed, and the later checks leave it where it is.
    hide = [t for t in clock.timers if t.fn == button.hide and not t.cancelled]
    assert len(hide) == 1 and hide[0].due == pytest.approx(clock.now + button.timeout)
    clock.advance(1.0)
    assert button.frame() == (155 - OFFSET - 34.0, 256.0, 34.0, 34.0)
    # The arrival (about 75 ms) became the deadline, plus the margin.
    from lyssna.ui.layout.popup_wait import MARGIN
    assert button._memory.deadline("msedge.exe") == pytest.approx(0.075 + MARGIN)


def test_a_learned_app_without_a_pop_up_shows_at_the_default_at_the_deadline(button, waits, caplog):
    import logging

    from lyssna.ui.layout.popup_wait import FIRST_WAIT

    twin, clock = waits()
    _learn(button, twin, clock)
    twin.popups = [(600, 100, 700, 200)]  # elsewhere
    with caplog.at_level(logging.INFO, logger="lyssna.ui.selection_button"):
        button._present(150, 310, None, (100, 300, 200, 20))
        clock.advance(FIRST_WAIT - 0.01)
        assert not button.visible()
        clock.advance(0.01)
    assert button.visible() and button.frame() == (133.0, 256.0, 34.0, 34.0)
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["play button in msedge.exe: pop-up none, waited up to 250 ms"]


def test_a_learned_app_is_forgotten_after_three_waits_with_nothing(button, waits):
    twin, clock = waits()
    _learn(button, twin, clock)
    twin.popups = []
    for _ in range(3):
        assert button._memory.knows("msedge.exe")
        button._present(150, 310, None, (100, 300, 200, 20))
        clock.advance(1.0)
        assert button.visible()
        button.hide()
    assert not button._memory.knows("msedge.exe")
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button.visible()  # at once again


def test_dismiss_during_the_wait_leaves_nothing_showing(button, waits):
    twin, clock = waits()
    _learn(button, twin, clock)
    twin.popups = []
    button._present(150, 310, None, (100, 300, 200, 20))
    clock.advance(0.05)
    button.dismiss()
    twin.popups = [TOOLBAR_PX]
    clock.advance(1.0)
    assert not button.visible() and button._wait is None
    # A wait cut short says nothing about the app: it is still learned.
    assert button._memory.knows("msedge.exe")


def test_a_new_selection_during_the_wait_replaces_it(button, waits):
    twin, clock = waits()
    _learn(button, twin, clock)
    twin.popups = []
    button._present(150, 310, None, (100, 300, 200, 20))
    first = button._wait
    clock.advance(0.05)
    button._present(450, 310, None, (400, 300, 200, 20))
    assert button._wait is not first
    clock.advance(1.0)
    assert button.visible() and button.frame()[0] == 433.0


def test_an_error_in_the_wait_shows_the_default_at_once(button, waits):
    twin, clock = waits()
    _learn(button, twin, clock)
    twin.popups = []
    twin.fail = True
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button.visible() and button.frame() == (133.0, 256.0, 34.0, 34.0)
    assert button._wait is None


def test_an_error_later_in_the_wait_shows_the_default_at_once(button, waits):
    twin, clock = waits()
    _learn(button, twin, clock)
    twin.popups = []
    button._present(150, 310, None, (100, 300, 200, 20))
    assert not button.visible()
    twin.fail = True
    clock.advance(0.025)
    assert button.visible() and button.frame() == (133.0, 256.0, 34.0, 34.0)


def test_an_error_naming_the_front_app_shows_at_once(button, waits):
    twin, clock = waits()
    _learn(button, twin, clock)

    def boom():
        raise OSError("no foreground window")
    twin.front_app = boom
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button.visible() and button.frame()[0] == 133.0


def test_no_waiting_where_pop_ups_cannot_be_found(button, waits):
    twin, clock = waits()
    _learn(button, twin, clock)
    twin.DETECTS_POPUPS = False
    twin.asked.clear()
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button.visible() and [t.delay for t in clock.timers if not t.cancelled] == [button.timeout]
    clock.advance(1.0)
    assert twin.asked == []


def test_a_late_pop_up_raises_the_deadline(button, waits):
    from lyssna.ui.layout.popup_wait import FIRST_WAIT, MAX_WAIT

    twin, clock = waits()
    _learn(button, twin, clock)
    twin.popups = []
    button._present(150, 310, None, (100, 300, 200, 20))
    clock.advance(FIRST_WAIT)
    assert button.visible() and button.frame()[0] == 133.0
    twin.popups = [TOOLBAR_PX]
    clock.advance(0.12)  # the first check after showing finds it: one jump, as before
    assert button.frame()[0] != 133.0
    assert button._memory.deadline("msedge.exe") == MAX_WAIT  # 0.37 s plus the margin, capped


# -- the learned apps survive a restart (MEMORY_FILE) -----------------------------------------


def test_the_memory_exports_and_loads_its_state():
    from lyssna.ui.layout.popup_wait import FIRST_WAIT, MARGIN, PopupMemory

    m = PopupMemory()
    m.covered("msedge.exe")
    m.covered("msedge.exe", 0.12)
    m.covered("chrome.exe")
    m.missed("chrome.exe")
    state = m.export()
    assert state == {"version": 1, "apps": {"msedge.exe": {"slowest": 0.12, "misses": 0},
                                            "chrome.exe": {"slowest": None, "misses": 1}}}
    again = PopupMemory()
    again.load(state)
    assert again.export() == state
    assert again.deadline("msedge.exe") == pytest.approx(0.12 + MARGIN) and again.deadline("chrome.exe") == FIRST_WAIT
    assert again.missed("chrome.exe") is False and again.missed("chrome.exe") is True  # the run of misses carried over


@pytest.mark.parametrize("state", [None, "", [], 3, {}, {"apps": []}, {"apps": "msedge.exe"}, {"version": 1, "apps": None},
                                   {"apps": {"msedge.exe": 0.1, "": {}, "x" * 300: {}}}],
                         ids=["none", "empty-text", "list", "number", "empty-dict", "apps-list", "apps-text", "apps-none",
                              "bad-entries"])
def test_the_memory_loads_a_wrong_shape_as_no_apps(state):
    from lyssna.ui.layout.popup_wait import PopupMemory

    m = PopupMemory()
    m.covered("notepad.exe")
    m.load(state)
    assert m.export() == {"version": 1, "apps": {}}


def test_the_memory_clamps_what_it_loads():
    from lyssna.ui.layout.popup_wait import FORGET_AFTER, MAX_APPS, MAX_WAIT, PopupMemory

    m = PopupMemory()
    m.load({"version": 7, "extra": True, "apps": {
        "slow.exe": {"slowest": 9.0, "misses": 99, "colour": "red"},
        "negative.exe": {"slowest": -1, "misses": -3},
        "words.exe": {"slowest": "fast", "misses": "two"},
        "flags.exe": {"slowest": True, "misses": True},
        "nan.exe": {"slowest": float("nan")},
        "empty.exe": {},
    }})
    apps = m.export()["apps"]
    assert apps["slow.exe"] == {"slowest": MAX_WAIT, "misses": FORGET_AFTER - 1}
    assert apps["negative.exe"] == {"slowest": 0.0, "misses": 0}
    for name in ("words.exe", "flags.exe", "nan.exe", "empty.exe"):
        assert apps[name] == {"slowest": None, "misses": 0}
    many = PopupMemory()
    many.load({"apps": {f"app{i}.exe": {} for i in range(MAX_APPS + 10)}})
    assert list(many.export()["apps"]) == [f"app{i}.exe" for i in range(10, MAX_APPS + 10)]


@pytest.fixture
def make_button(screen):
    """Make the button after the test has set host.window (it reads the learned apps then)."""
    from lyssna.ui.selection_button import SelectionButton

    made = []

    def make():
        b = SelectionButton()
        made.append(b)
        return b
    yield make
    for b in made:
        if b._timer:
            b._timer.cancel()
        b.hide()


def _memory_file():
    from lyssna import config
    from lyssna.ui.selection_button import MEMORY_FILE

    return config.STATE_DIR / MEMORY_FILE


def _stored():
    import json

    return json.loads(_memory_file().read_text(encoding="utf-8"))


def test_a_learned_app_is_on_disk_and_waits_after_a_restart(make_button, waits):
    twin, clock = waits()
    assert not _memory_file().exists()
    button = make_button()
    _learn(button, twin, clock)
    assert _stored() == {"version": 1, "apps": {"msedge.exe": {"slowest": None, "misses": 0}}}
    # The next launch: a new button waits in Edge straight away.
    again = make_button()
    assert again._memory.knows("msedge.exe")
    twin.popups = []
    again._present(150, 310, None, (100, 300, 200, 20))
    assert not again.visible()


def test_a_forgotten_app_is_gone_from_the_file(make_button, waits):
    twin, clock = waits()
    button = make_button()
    _learn(button, twin, clock)
    twin.popups = []
    for misses in (1, 2):
        button._present(150, 310, None, (100, 300, 200, 20))
        clock.advance(1.0)
        button.hide()
        assert _stored()["apps"]["msedge.exe"]["misses"] == misses
    button._present(150, 310, None, (100, 300, 200, 20))
    clock.advance(1.0)
    button.hide()
    assert not button._memory.knows("msedge.exe")
    assert _stored() == {"version": 1, "apps": {}}
    assert not make_button()._memory.knows("msedge.exe")


def test_nothing_is_written_when_nothing_changed(make_button, waits, monkeypatch):
    import os

    twin, clock = waits()
    button = make_button()
    _learn(button, twin, clock)
    for _ in range(2):  # the first wait measures the arrival (a change), the second matches it
        button._present(150, 310, None, (100, 300, 200, 20))
        clock.advance(1.0)
        button.hide()
    writes = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda *a: (writes.append(a), real(*a)))
    for _ in range(3):
        button._present(150, 310, None, (100, 300, 200, 20))
        clock.advance(1.0)
        button.hide()
    twin.app = "notepad.exe"  # an app that was never learned, without a pop-up
    twin.popups = []
    button._present(150, 310, None, (100, 300, 200, 20))
    clock.advance(1.0)
    button.hide()
    assert writes == []
    assert _stored()["apps"] == {"msedge.exe": {"slowest": 0.0, "misses": 0}}


@pytest.mark.parametrize("content", [b"", b"{not json", b'["msedge.exe"]', b'{"apps": 3}', b"\xff\xfe\x00",
                                     b'{"apps": {}, "pad": "' + b"x" * (70 * 1024) + b'"}'],
                         # Short plain ids: pytest puts the test's id in an environment variable,
                         # which Windows caps at 32767 characters (the last case is 70 KB).
                         ids=["empty", "not-json", "list", "apps-number", "not-utf8", "oversized"])
def test_a_bad_file_means_no_learned_apps(make_button, waits, content):
    waits(popups=[(600, 100, 700, 200)])
    path = _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    button = make_button()
    assert button._memory.export()["apps"] == {}
    button._present(150, 310, None, (100, 300, 200, 20))
    assert button.visible() and button.frame() == (133.0, 256.0, 34.0, 34.0)


def test_an_oversized_file_is_not_read_even_when_it_is_valid(make_button, waits):
    import json

    from lyssna.ui.selection_button import MEMORY_MAX_BYTES

    waits()
    path = _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"apps": {"msedge.exe": {}}, "pad": "x" * MEMORY_MAX_BYTES}), encoding="utf-8")
    assert not make_button()._memory.knows("msedge.exe")


def test_an_unreadable_file_means_no_learned_apps(make_button, waits):
    waits()
    _memory_file().mkdir(parents=True)  # a folder where the file should be
    button = make_button()
    assert button._memory.export()["apps"] == {}


def test_no_file_is_read_or_written_where_pop_ups_cannot_be_found(make_button, waits):
    import json

    _, clock = waits(detects=False, popups=[TOOLBAR_PX])
    path = _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"apps": {"msedge.exe": {}}}), encoding="utf-8")
    button = make_button()
    assert not button._memory.knows("msedge.exe")
    path.unlink()
    button._present(150, 310, None, (100, 300, 200, 20))
    clock.advance(1.0)
    button.hide()
    assert not path.exists() and not path.with_name(path.name + ".tmp").exists()


def test_a_failing_write_does_not_stop_the_button(make_button, waits, monkeypatch):
    import os

    twin, clock = waits()
    button = make_button()

    def fail(*a):
        raise PermissionError("the folder is locked")
    monkeypatch.setattr(os, "replace", fail)
    _learn(button, twin, clock)  # shows, jumps once and learns, as without a file
    assert button._memory.knows("msedge.exe") and not _memory_file().exists()
    twin.popups = [TOOLBAR_PX]
    button._present(150, 310, None, (100, 300, 200, 20))
    clock.advance(0.05)
    assert button.visible()
