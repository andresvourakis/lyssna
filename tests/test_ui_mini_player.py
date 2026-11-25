"""The Windows player (lyssna/ui/mini_player.py and sliver.py), driven the way
legacy/test_dictation_pill.py drives the Mac one: a fixed fake screen, a recorded order_front instead of
a real window, and a fake clock and cursor for the hover and auto-hide rules. The checks are on
forms, frames (Mac-style, as the shared geometry speaks) and widget state, never pixels. Runs on
the offscreen platform, so it needs no display."""

import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication

from lyssna.player import Status
from lyssna.ui.layout.pill import (
    BUTTON_HIT,
    CAP_W,
    COMPACT_H,
    DOCK_GAP,
    DOCK_GAP_V,
    DOCK_W,
    FREE_H,
    GROW_SECS,
    HIDE_DELAY,
    HOVER_OUT_DELAY,
    LINGER_SECS,
    MARGIN,
    MENU_DISC,
    MENU_H,
    MENU_MIC_H,
    MENU_W,
    WAVE_EVERY,
    along_fraction,
    pill_len,
)
from lyssna.ui.layout.wave import CAPSULE_H, CAPSULE_INSET, CAPSULE_W, SLIVER_H, SLIVER_W, capsule_centre

VF = (0.0, 25.0, 1440.0, 875.0)  # the fake screen's usable area (Mac-style); the tests never see a real one
ACTIONS = ("toggle", "stop", "back", "forward", "set_speed", "seek_to", "set_voice", "layout", "dictation_layout",
           "dictate", "dictate_stop", "dictate_cancel", "read_selection", "history")


@pytest.fixture(autouse=True)
def animations_on(monkeypatch):
    """These tests are about motion, so the machine's own setting must not decide them: GitHub's
    Windows machine has animation effects off. A test about reduced motion patches it back."""
    from lyssna.ui import theme

    monkeypatch.setattr(theme, "reduce_motion", lambda: False)


def pump(seconds: float = 0.0, until=None) -> None:
    """Run the Qt event loop for `seconds`, or until `until()` holds (up to five seconds: an
    animation on a busy CI machine is slower, and a fast machine returns as soon as it ends)."""
    end = time.monotonic() + (seconds if until is None else 5.0)
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)
    assert until is None or until(), "the event loop never got there"


@pytest.fixture
def pill(qapp, monkeypatch):
    """A MiniPlayer that never reaches the screen, on a fixed fake screen."""
    from lyssna.ui import mini_player as mp

    monkeypatch.setattr(mp, "_vf", lambda screen=None: VF)
    monkeypatch.setattr(mp, "_screen_at", lambda x, y: None)
    monkeypatch.setattr(mp, "_screen_under_mouse", lambda: None)
    p = mp.MiniPlayer()
    p.shown = []
    p.order_front = lambda: p.shown.append(p.form)
    p.zones = []
    p.zone.order_front = lambda: p.zones.append("side")
    p.flat_zone.order_front = lambda: p.zones.append("flat")
    p.visible = lambda: bool(p.shown)  # as if each order_front had put it on screen
    p.auto_hide = False  # most tests expect the resting squiggle to stay out; auto-hide has its own
    p.dictation_layout = {"dock": "right", "y": -1.0}
    p.fired = []
    p.actions = {name: (lambda *a, n=name: p.fired.append((n, *a))) for name in ACTIONS}
    p._reduce_motion = lambda: False
    p._button_down = lambda: False
    yield p
    p._cancel_anim()
    p._stop_auto_watch()
    p._stop_wave_timer()
    for kit in p._kits.values():
        kit["wave"].stop()
    if p._grow_anim is not None:
        p._grow_anim.stop()
    for panel in (p, p.zone, p.flat_zone):
        panel.panel.hide()
        panel.panel.deleteLater()
    QCoreApplication.processEvents()


def frame(p):
    return p.frame_mac()


def move(p, x, y):
    """Put the surface's bottom-left corner at the Mac-style point (x, y)."""
    _, _, w, h = p.frame_mac()
    p.set_frame_mac((x, y, w, h))


def centre(p):
    x, y, w, h = p.frame_mac()
    return x + w / 2, y + h / 2


def playing(**kw):
    defaults = dict(state="playing", session=1, total=9, index=3, position=84.0, duration=334.0, speed=1.2,
                    provider="Kokoro · Heart")
    defaults.update(kw)
    return Status(**defaults)


def settle(p):
    pump(until=lambda: p._pending_done is None and p._slide_token is None)


# -- forms -------------------------------------------------------------------------------------


def test_the_card_has_the_mac_size_and_shows_bottom_right_by_default(pill):
    assert (pill.width, pill.height, pill.corner_radius) == (400.0, 156.0, 22.0)
    pill._update(playing())
    assert pill.form == "card" and pill.content is pill.card and pill.shown == ["card"]
    x, y, w, h = frame(pill)
    assert (w, h) == (400.0, 156.0)
    assert (x, y) == (1440 - 400 - MARGIN, 25 + MARGIN)


@pytest.mark.parametrize(("layout", "idle_pill", "form"), [
    ({"dock": "", "x": -1.0, "y": -1.0}, True, "card"),
    ({"dock": "left", "x": 0.0, "y": 0.5}, True, "sliver"),
    ({"dock": "bottom", "x": 0.0, "y": 0.5}, False, "sliver"),
])
def test_a_reading_shows_the_card_or_the_docked_sliver(pill, layout, idle_pill, form):
    pill.layout = layout
    pill.idle_pill = idle_pill
    pill._update(playing())
    assert pill.form == form


def test_idle_shows_the_resting_squiggle_or_nothing(pill):
    pill.show_idle()
    assert pill.form == "rest" and pill.content is pill.rest and pill.rest.rest
    x, _y, w, h = frame(pill)
    assert (w, h) == (SLIVER_W, SLIVER_H) and x == 1440 - SLIVER_W  # flush with the right edge
    assert pill.margin == 0  # no shadow around the squiggle
    pill._update(playing())
    assert pill.form == "card"
    pill._update(Status())
    assert pill.form == "rest"
    pill.set_idle_pill(False)
    assert pill.form == "rest" and pill.shown[-1] == "rest"  # hide() only takes the window away
    other = pill.shown[:]
    pill.show_idle()
    assert pill.shown == other  # nothing new shown with the idle pill off


def test_a_fresh_config_rests_at_the_bottom_centre(pill):
    pill.dictation_layout = {"dock": "", "y": -1.0}
    pill.show_idle()
    x, y, w, h = frame(pill)
    assert (w, h) == (SLIVER_H, SLIVER_W) and y == 25 and x + w / 2 == pytest.approx(720, abs=1)
    assert pill.content is pill._kits[True]["rest"] and pill.rest.edge == "bottom"


def test_stop_fires_and_hides_at_once(pill):
    pill._update(playing())
    pill.buttons["stop"][0].perform_click()
    assert pill.fired[0] == ("stop",) and pill.form == "rest"


# -- the card's text -----------------------------------------------------------------------------


def test_times_counts_speed_and_voice(pill):
    pill._update(playing())
    assert (pill.elapsed.text(), pill.progress.text(), pill.remaining.text()) == ("1:24", "3 of 9", "-4:10")
    assert pill.speed_btn.text() == "1.2×" and pill.badge.letter == "H"
    assert all(b.icon == "pause.fill" for b in pill.buttons["toggle"])
    pill._update(playing(state="paused", index=128, total=240, position=1834.0, duration=3460.0, speed=1.75))
    assert (pill.elapsed.text(), pill.progress.text(), pill.remaining.text()) == ("30:34", "128 of 240", "-27:06")
    assert pill.speed_btn.text() == "1.75×"
    assert all(b.icon == "play.fill" for b in pill.buttons["toggle"])
    assert [b.icon_size for b in pill.buttons["toggle"]] == [20.0, 14.0, 14.0]  # the card's, then each pill's
    pill._update(playing(state="loading", index=0, total=0, position=0.0, duration=0.0, speed=1.0))
    assert pill.progress.text() == "Loading…" and pill.speed_btn.text() == "1×"
    assert pill.scrub.fraction == 0.0


def test_the_scrub_preview_shows_the_time_at_the_knob(pill):
    pill._update(playing())
    pill.scrub.scrubbing = True
    pill._scrub_preview(0.5)
    assert (pill.elapsed.text(), pill.remaining.text()) == ("2:47", "-2:47")
    pill._update(playing(position=100.0))
    assert pill.elapsed.text() == "2:47"  # playback does not fight the hand
    pill.scrub.scrubbing = False
    pill._update(playing(position=100.0))
    assert pill.elapsed.text() == "1:40"


def test_the_badge_opens_a_voice_menu_only_with_voices(pill):
    pill._update(playing())
    assert not pill.badge.pickable and pill.badge.glow is None and pill.badge.toolTip() == "Kokoro · Heart"
    pill.badge.perform_click()
    assert pill._popup is None
    pill.voices_fn = lambda: ([("heart", "Heart"), ("bella", "Bella")], "heart")
    pill._update(playing())
    assert pill.badge.pickable and pill.badge.toolTip() == "Change voice"
    pill.badge.perform_click()
    acts = pill._popup.actions()
    assert [a.text() for a in acts] == ["Heart", "Bella"] and [a.isChecked() for a in acts] == [True, False]
    acts[1].trigger()
    pill._popup.close()
    assert pill.fired[-1] == ("set_voice", "bella")


def test_the_speed_menu_checks_the_current_speed_and_sets_one(pill):
    from lyssna.player import SPEEDS

    pill._update(playing())
    pill.speed_btn.perform_click()
    acts = pill._popup.actions()
    assert [a.text() for a in acts] == [f"{s:g}×" for s in SPEEDS]
    assert [a.text() for a in acts if a.isChecked()] == ["1.2×"]
    acts[-1].trigger()
    pill._popup.close()
    assert pill.fired[-1] == ("set_speed", 2.0)


def test_every_button_fires_its_action(pill):
    pill.layout = {"dock": "right", "x": 0.0, "y": 0.5}
    pill._update(playing())
    for name in ("back", "toggle", "forward"):
        assert len(pill.buttons[name]) == 3  # the card's and each orientation's pill
        for btn in pill.buttons[name]:
            pill.fired.clear()
            btn.perform_click()
            assert pill.fired == [(name,)]
    for scrub in (pill.scrub, pill._kits[False]["dock_scrub"], pill._kits[True]["dock_scrub"]):
        pill.fired.clear()
        scrub.on_seek(0.25)
        assert pill.fired == [("seek_to", 0.25)]
    pill.chevron.perform_click()
    assert pill.form == "card" and pill.fired[-1][0] == "layout"
    pill.dock_btn.perform_click()
    assert pill.form == "sliver" and pill.fired[-1][:2] == ("layout", "right")
    pill.fired.clear()
    pill._kits[False]["pill"].shapes[-1][0].perform_click()  # the pill's own close disc
    assert pill.fired == [("stop",)] and pill.form == "rest"


def test_tooltips(pill):
    assert pill.buttons["stop"][0].toolTip() == "Stop"
    assert pill.dock_btn.toolTip() == "Tuck into the side of the screen"
    assert pill.scrub.toolTip() == "Drag to scrub"
    assert pill.chevron.toolTip() == "Expand player"
    pill.set_tooltips({"toggle": "Pause / resume  Ctrl+Alt+P", "speed": "Speed"})
    assert {b.toolTip() for b in pill.buttons["toggle"]} == {"Pause / resume  Ctrl+Alt+P"}
    assert pill.speed_btn.toolTip() == "Speed"
    pill._update(playing())
    assert pill.sliver.toolTip() == "3 of 9  ·  -4:10"


# -- docking ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("origin", "edge"), [((20, 400), "left"), ((1000, 400), "right"), ((500, 30), "bottom")])
def test_the_dock_button_tucks_the_card_into_the_nearest_edge(pill, origin, edge):
    pill._update(playing())
    move(pill, *origin)
    pill._refresh_dock_arrow()
    assert pill.dock_btn.icon == {"left": "arrow.left.to.line.compact", "right": "arrow.right.to.line.compact",
                                  "bottom": "arrow.down.to.line.compact"}[edge]
    pill.dock_btn.perform_click()
    assert pill.dock == edge and pill.form == "sliver" and pill.content is pill.sliver
    assert pill.fired[-1][:2] == ("layout", edge)
    x, y, w, h = frame(pill)
    if edge == "bottom":
        assert (w, h, y) == (SLIVER_H, SLIVER_W, 25)
    else:
        assert (w, h) == (SLIVER_W, SLIVER_H) and x == (0 if edge == "left" else 1440 - SLIVER_W)
    assert pill.sliver.edge == edge and pill.sliver.fraction == pytest.approx(84 / 334)
    assert pill.chevron.icon == {"left": "chevron.right", "right": "chevron.left", "bottom": "chevron.up"}[edge]


@pytest.mark.parametrize(("origin", "to"), [((-60, 400), (0.0, 400)), ((500, 10), (500, 25.0))])
def test_the_card_drags_freely_and_never_docks(pill, origin, to):
    pill._update(playing())
    pill.drag_began()
    move(pill, *origin)
    pill.drag_moved_mac(origin[0] + 20, origin[1] + 20)
    assert pill.zones == []  # no landing hint: the card never docks by dragging
    pill.drag_ended_mac(origin[0] + 20, origin[1] + 20)
    assert pill.dock == "" and pill.form == "card"
    assert frame(pill)[:2] == to  # pulled back onto the screen, right against the edge
    name, dock, fx, fy = pill.fired[-1]
    assert (name, dock) == ("layout", "")
    assert fx == pytest.approx(to[0] / (1440 - 400)) and fy == pytest.approx((to[1] - 25) / (875 - 156))


def test_a_saved_card_spot_comes_back_as_the_same_fractions(pill):
    pill.layout = {"dock": "", "x": 0.25, "y": 0.75}
    pill._update(playing())
    x, y, _, _ = frame(pill)
    assert (x, y) == pytest.approx((0.25 * (1440 - 400), 25 + 0.75 * (875 - 156)), abs=1)


def test_dragging_the_docked_player_onto_the_bottom_docks_it_there(pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill._update(playing())
    assert pill.form == "sliver" and pill.dock == "left"
    pill.drag_began()
    move(pill, 500, 40)
    pill.drag_moved_mac(600, 80)
    assert pill.zones == ["flat"]  # the hint lies flat along the bottom edge
    zx, zy, zw, zh = pill.flat_zone.frame_mac()
    assert (zw, zh) == (CAPSULE_H + 8, CAPSULE_W + 8) and zy == 25 + CAPSULE_INSET - 4
    pill.drag_ended_mac(600, 80)
    assert pill.dock == "bottom" and pill.form == "sliver" and pill.content is pill._kits[True]["sliver"]
    assert pill.fired[-1][:2] == ("layout", "bottom")
    x, _y, w, _h = frame(pill)
    assert zx + zw / 2 == pytest.approx(x + w / 2, abs=1)  # the hint showed the spot it landed on
    _, _, fx, fy = pill.fired[-1]
    assert fx == 0.0 and fy == pytest.approx(along_fraction(VF, "bottom", x + w / 2), abs=0.002)


def test_a_docked_player_dropped_away_from_every_edge_becomes_the_card_there(pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill._update(playing())
    pill.drag_began()
    move(pill, 600, 400)
    pill.drag_moved_mac(620, 420)
    assert pill.zones == []
    pill.drag_ended_mac(620, 420)
    assert pill.dock == "" and pill.form == "card"
    assert centre(pill) == pytest.approx((620, 420), abs=1)
    assert pill.fired[-1][:2] == ("layout", "")


def test_the_pill_opened_by_the_chevron_expands_away_from_the_edge(pill):
    pill.layout = {"dock": "right", "x": 0.0, "y": 0.5}
    pill._update(playing())
    sliver_cy = centre(pill)[1]
    pill.expand()
    x, y, w, h = frame(pill)
    assert pill.form == "card" and x + w == 1440 - DOCK_GAP and y + h / 2 == pytest.approx(sliver_cy, abs=1)


# -- hover: sliver and pill ----------------------------------------------------------------------


@pytest.fixture
def clocked(pill):
    pill.clock = [100.0]
    pill._now = lambda: pill.clock[0]
    pill.cursor = (720.0, 460.0)  # far from everything
    pill._cursor = lambda: pill.cursor
    return pill


def test_hovering_the_sliver_opens_the_pill_and_leaving_closes_it_after_the_delay(clocked):
    p = clocked
    p.layout = {"dock": "right", "x": 0.0, "y": 0.5}
    p._update(playing())
    sliver = frame(p)
    p.mouse_entered()
    assert p.form == "pill" and p.content is p.pill_host and p._watch is not None
    x, y, w, h = frame(p)
    assert (w, h) == (DOCK_W, pill_len(False)) and x == 1440 - DOCK_GAP - DOCK_W
    assert y + h / 2 == pytest.approx(sliver[1] + sliver[3] / 2, abs=1)
    assert p._watch.interval() == 30  # cursor polling, as on the Mac
    settle(p)
    assert p.pill_host.scale == 1.0 and p.pill.isVisibleTo(p.panel)

    p.cursor = (x + 10, y + 50)  # on the pill
    p._hover_tick()
    assert p._outside_since is None
    p.cursor = (x + w + 6, y + h / 2)  # within the 6 point margin beside the window
    p._hover_tick()
    assert p._outside_since is None
    p.cursor = (1438.0, y + h / 2)  # pushed against the screen edge: outside, as the Mac's frame-only rule has it
    p._hover_tick()
    assert p._outside_since == 100.0
    p.cursor = (720.0, 460.0)
    p._hover_tick()  # left at 100.0
    p.clock[0] = 100.0 + HOVER_OUT_DELAY - 0.01
    p._hover_tick()
    assert p.form == "pill" and p._pending_done is None
    p.clock[0] = 100.0 + HOVER_OUT_DELAY + 1e-6
    p._hover_tick()
    assert p._watch is None and p._pending_done is not None  # shrinking
    settle(p)
    assert p.form == "sliver" and frame(p) == pytest.approx(sliver)


@pytest.mark.parametrize("auto_hide", [False, True])
def test_a_cursor_pressed_against_the_edge_beside_the_menu_closes_it_once(clocked, auto_hide):
    """The legacy rule, in the legacy geometry: the open menu sits DOCK_GAP in from the edge and
    the cursor counts as inside within 6 points of its window, so the last 4 points against the
    edge (where the resting squiggle's window still reaches) are outside and the menu closes
    after the delay. Back on the squiggle, nothing the player polls opens it again: only a new
    enter event would, and that comes from the system's tracking, the same on both interfaces."""
    p = clocked
    p.auto_hide = auto_hide
    p.show_idle(linger=False)
    settle(p)
    x, y, w, h = frame(p)
    assert (x, w) == (1440 - SLIVER_W, SLIVER_W)  # the squiggle's window is flush with the edge
    p.mouse_entered()
    settle(p)
    assert p.form == "menu" and len(p.menu_hovers) == 3
    mx, _my, mw, _mh = frame(p)
    assert mx + mw == 1440 - DOCK_GAP
    p.cursor = (1440 - DOCK_GAP + 6, y + h / 2)  # the edge of the margin: still inside
    p._hover_tick()
    assert p._outside_since is None
    p.cursor = (1439.0, y + h / 2)  # pressed against the edge, at the squiggle's height
    p._hover_tick()
    p.clock[0] = 100.0 + HOVER_OUT_DELAY + 1e-6
    p._hover_tick()
    settle(p)
    assert p.form == "rest" and p._watch is None
    opened = []
    p._open_menu = lambda: opened.append(p.clock[0])
    for i in range(1, 40):  # four seconds of polling with the cursor held there
        p.clock[0] = 100.0 + i * 0.1
        p._auto_tick()
        QCoreApplication.processEvents()
    assert opened == [] and p.form == "rest"


def test_the_pill_stands_upright_on_the_sides_and_lies_flat_on_the_bottom(pill):
    pill.layout = {"dock": "bottom", "x": 0.0, "y": 0.5}
    pill._update(playing())
    cx = centre(pill)[0]
    pill.mouse_entered()
    x, y, w, h = frame(pill)
    assert pill.form == "pill" and (w, h) == (pill_len(True), DOCK_W)
    assert y == 25 + DOCK_GAP and x + w / 2 == pytest.approx(cx, abs=1)
    assert pill.pill is pill._kits[True]["pill"]
    # left to right: close, expand, previous, play, next, progress
    stop, chevron = pill.pill.shapes[-1][0], pill.pill.shapes[-2][0]
    main = pill.pill.shapes[0][0]
    xs = [stop.x(), chevron.x()]
    for name in ("back", "toggle", "forward"):
        btn = pill.buttons[name][2]  # the flat kit's
        xs.append(main.x() + btn.x())
    xs.append(main.x() + pill.dock_scrub.x())
    assert xs == sorted(xs)


def test_the_upright_pill_reads_top_to_bottom(pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill._update(playing())
    pill.mouse_entered()
    stop, chevron, main = pill.pill.shapes[-1][0], pill.pill.shapes[-2][0], pill.pill.shapes[0][0]
    ys = [stop.y(), chevron.y()]
    for name in ("back", "toggle", "forward"):
        ys.append(main.y() + pill.buttons[name][1].y())
    ys.append(main.y() + pill.dock_scrub.y())
    assert ys == sorted(ys)
    assert main.y() + main.height() == pill.pill.height()  # the capsule sits at the bottom, discs above


@pytest.fixture
def watching(monkeypatch):
    """host.window as on a Mac: Qt's enter events do not reach a window in the background, so the
    twin watches. Records {widget: callback}; list it before `pill` so the player is built after."""
    from lyssna import host

    watches: dict = {}

    def watch(widget, callback):
        watches[widget] = callback
        return True
    monkeypatch.setattr(host.window, "WATCHES_MOUSE_ENTER", True)
    monkeypatch.setattr(host.window, "watch_mouse_enter", watch)
    return watches


def test_on_a_mac_the_watch_hears_the_cursor_arrive_on_the_sliver_and_the_squiggle(watching, pill):
    p = pill
    for kit in p._kits.values():
        assert kit["sliver"] in watching and kit["rest"] in watching
        assert kit["sliver"].on_enter is None and kit["rest"].on_enter is None  # Qt's enter event fires nothing too
    p.layout = {"dock": "right", "x": 0.0, "y": 0.5}
    p._update(playing())
    assert p.form == "sliver"
    p._moving = True
    watching[p.sliver]()  # mid-drag: no pill
    assert p.form == "sliver"
    p._moving = False
    watching[p.sliver]()
    assert p.form == "pill"
    p._cancel_anim()
    p._update(Status())
    p.show_idle(linger=False)
    settle(p)
    assert p.form == "rest"
    watching[p.rest]()
    assert p.form == "menu"


def test_no_pill_from_the_watch_after_the_reading_ended(watching, pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill.idle_pill = False
    pill.hide = lambda: pill.shown.clear()
    pill._update(playing())
    pill._update(Status())
    pill.shown.append("sliver")  # a late arrival on a window still thought visible
    watching[pill.sliver]()
    assert pill.form == "sliver"


@pytest.fixture
def qt_enter(monkeypatch):
    """host.window as on Windows: Qt's enter events arrive, nothing is watched."""
    from lyssna import host

    watched = []
    monkeypatch.setattr(host.window, "WATCHES_MOUSE_ENTER", False)
    monkeypatch.setattr(host.window, "watch_mouse_enter", lambda widget, callback: watched.append(widget) or False)
    return watched


def test_elsewhere_qt_enter_events_open_the_pill_and_nothing_is_watched(qt_enter, pill):
    assert qt_enter == []
    for kit in pill._kits.values():
        assert kit["sliver"].on_enter == pill.mouse_entered and kit["rest"].on_enter == pill.mouse_entered
    pill.layout = {"dock": "right", "x": 0.0, "y": 0.5}
    pill._update(playing())
    pill.sliver.on_enter()
    assert pill.form == "pill"


def test_no_pill_after_the_reading_ended(pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill.idle_pill = False
    pill.hide = lambda: pill.shown.clear()
    pill._update(playing())
    pill._update(Status())
    assert pill.form == "sliver" and pill.shown == []
    pill.shown.append("sliver")  # a late enter event on a window still thought visible
    pill.mouse_entered()
    assert pill.form == "sliver"


# -- the idle menu -------------------------------------------------------------------------------


def test_the_menu_matches_the_mac(pill):
    pill.show_idle()
    rest_cy = centre(pill)[1]
    pill.mouse_entered()
    assert pill.form == "menu" and pill.content is pill.menu_host and pill._watch is not None
    x, y, w, h = frame(pill)
    assert x == 1440 - DOCK_GAP - MENU_W and y + h / 2 == pytest.approx(rest_cy, abs=1)
    assert (w, h) == (MENU_W, MENU_H)
    assert not any(v.isHidden() for v in (pill.menu_dictate, pill.menu_read, pill.menu_history))
    d, r, hi = (v.geometry() for v in (pill.menu_dictate, pill.menu_read, pill.menu_history))
    # bottom to top: the dictate capsule, read selection, history, DOCK_GAP_V apart
    assert d.y() + d.height() == MENU_H and (d.width(), d.height()) == (CAP_W, MENU_MIC_H)
    assert r.y() + r.height() + DOCK_GAP_V == d.y() and hi.y() + hi.height() + DOCK_GAP_V == r.y()
    for item in (d, r, hi):
        assert item.x() + item.width() / 2 == pytest.approx(MENU_W / 2)


def test_the_bottom_menu_is_a_row(pill):
    pill.dictation_layout = {"dock": "bottom", "y": 0.3}
    pill.show_idle()
    rest_cx = centre(pill)[0]
    pill.mouse_entered()
    x, y, w, h = frame(pill)
    assert (w, h) == (MENU_H, MENU_W) and y == 25 + DOCK_GAP and x + w / 2 == pytest.approx(rest_cx, abs=1)
    d, r, hi = (v.geometry() for v in (pill.menu_dictate, pill.menu_read, pill.menu_history))
    assert d.x() == 0 and r.x() == d.width() + DOCK_GAP_V and hi.x() == r.x() + r.width() + DOCK_GAP_V


@pytest.mark.parametrize(("item", "fired"), [("menu_read", "read_selection"), ("menu_dictate", "dictate"),
                                             ("menu_history", "history")])
def test_a_menu_item_closes_the_menu_then_acts(pill, item, fired):
    pill.show_idle()
    pill.mouse_entered()
    seen = []
    pill.actions[fired] = lambda: seen.append(pill.form)
    getattr(pill, item).perform_click()
    assert seen == ["rest"] and pill.form == "rest"


def test_leaving_the_menu_shrinks_it_back_to_the_squiggle(clocked):
    p = clocked
    p.show_idle()
    rest = frame(p)
    p.mouse_entered()
    settle(p)
    p._close_menu()
    settle(p)
    assert p.form == "rest" and frame(p) == pytest.approx(rest)


def test_a_menu_tip_shows_after_a_hover(pill):
    shown = []

    class Tip:
        def show_beside(self, anchor, edge, text, key):
            shown.append((anchor, edge, text, key))

        def hide(self):
            shown.append("hidden")

    pill.tip = Tip()
    pill.read_key = "Ctrl+Alt+S"
    pill.show_idle()
    pill.mouse_entered()
    pill._stop_watch()  # the live hover poll would read the real cursor, away from the menu
    settle(pill)
    pill.menu_read.set_hover(True, animate=False)
    pump(0.6)
    (anchor, edge, text, key), = shown
    assert (edge, text, key) == ("right", "Read selection", "Ctrl+Alt+S")
    x, y, w, h = anchor
    fx, fy, fw, fh = frame(pill)
    assert (w, h) == (MENU_DISC, MENU_DISC) and fx <= x and x + w <= fx + fw and fy <= y and y + h <= fy + fh
    pill.menu_read.set_hover(False, animate=False)
    assert shown[-1] == "hidden"


def test_no_tips_without_a_tip_panel(pill):
    pill.show_idle()
    pill.mouse_entered()
    pill._stop_watch()
    settle(pill)
    pill.menu_read.set_hover(True, animate=False)
    pump(0.5)  # nothing to show it on; nothing fails


# -- the idle pill dragged ------------------------------------------------------------------------


@pytest.mark.parametrize("open_menu", [False, True])
def test_dropping_the_idle_pill_snaps_to_the_nearer_edge_and_is_remembered(pill, open_menu):
    pill.show_idle()
    if open_menu:
        pill.mouse_entered()
    move(pill, 300, 500)
    pill.drag_began()
    pill.drag_ended_mac(320, 520)
    x, y, w, h = frame(pill)
    assert pill.form == "rest" and (x, w, h) == (0.0, SLIVER_W, SLIVER_H)
    name, edge, fy = pill.fired[-1]
    assert (name, edge) == ("dictation_layout", "left")
    assert fy == pytest.approx(along_fraction(VF, "left", y + h / 2), abs=0.002)
    assert pill.dictation_layout == {"dock": "left", "y": fy}


def test_dropping_the_idle_pill_moves_a_docked_player_too(pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill.show_idle()
    move(pill, 1300, 500)
    pill.drag_began()
    pill.drag_ended_mac(1320, 520)
    assert pill.fired[-1][:2] == ("layout", "right") and pill.layout["dock"] == "right"


@pytest.mark.parametrize(("origin", "edge", "kind"), [
    ((60, 500), "left", "side"), ((1350, 300), "right", "side"), ((700, 60), "bottom", "flat"),
    ((5, 880), "left", "side"),  # near a corner: the hint is kept on screen, like the squiggle
])
def test_dragging_the_idle_pill_shows_where_it_lands(pill, origin, edge, kind):
    pill.show_idle()
    move(pill, *origin)
    pill.drag_began()
    pill.drag_moved_mac(*origin)
    assert pill.zones == [kind] and pill.shown[-1] == "rest"  # the pill stays above the hint
    zone = pill.flat_zone if kind == "flat" else pill.zone
    hint = centre(zone)
    pill.drag_ended_mac(*origin)
    assert pill.form == "rest" and pill._edge == edge
    x, y, _w, _h = frame(pill)
    cx, cy = capsule_centre(edge)
    assert hint == pytest.approx((x + cx, y + cy), abs=1)  # around the squiggle, where it landed


def test_the_idle_pill_moves_between_all_three_docks(pill):
    pill.show_idle()
    for origin, edge in (((700, 30), "bottom"), ((15, 500), "left"), ((1420, 500), "right"), ((700, 26), "bottom")):
        move(pill, *origin)
        pill.drag_began()
        pill.drag_ended_mac(*origin)
        assert pill.form == "rest" and pill._edge == edge and pill.fired[-1][:2] == ("dictation_layout", edge)
        assert pill.content is pill._kits[edge == "bottom"]["rest"]


# -- auto-hide -----------------------------------------------------------------------------------

REST_X = 1440 - SLIVER_W  # the resting squiggle on the right edge, middle: x and centre y
REST_CY = 25 + 875 / 2
FAR = (720.0, 460.0)
NEAR = (REST_X - 60, REST_CY)


@pytest.fixture
def hiding(clocked):
    """Auto-hide on; hide() takes it off screen as visible() sees it."""
    p = clocked
    p.auto_hide = True
    p.hide = lambda: p.shown.clear()
    return p


def at(p, t):
    p.clock[0] = t
    p._auto_tick()


def test_auto_hide_shows_at_idle_then_slides_away_after_the_linger(hiding):
    p = hiding
    p.show_idle()
    assert p.form == "rest" and p.shown == ["rest"] and p._auto_watch is not None
    assert p._auto_watch.interval() == 100
    settle(p)
    at(p, 100.0)
    at(p, 100.0 + LINGER_SECS - 0.01)
    assert not p._tucked  # far from it, but lingering so a new user sees where it lives
    at(p, 100.0 + LINGER_SECS)
    assert p._tucked and p._slide_token is not None
    x, _y, w, h = frame(p)
    assert (x, w, h) == (REST_X, SLIVER_W, SLIVER_H)  # the window stays put: the squiggle slides inside it
    settle(p)
    assert p.rest.slide == SLIVER_W and p.shown == [] and p.form == "rest" and p._auto_watch is not None


def test_auto_hide_reveals_near_keeps_the_menu_and_hides_after_the_delay(hiding):
    p = hiding
    p.show_idle()
    at(p, 100.0)
    at(p, 103.0)
    settle(p)
    assert p._tucked and p.shown == []
    p.mouse_entered()  # a stale enter on the hidden window
    assert p.form == "rest"

    p.cursor = NEAR
    at(p, 104.0)
    assert not p._tucked and p.shown == ["rest"] and p.form == "rest"
    settle(p)
    assert p.rest.slide == 0.0 and frame(p)[0] == REST_X
    at(p, 110.0)
    assert not p._tucked  # near: it stays

    p.mouse_entered()
    assert p.form == "menu"
    p.cursor = FAR
    at(p, 120.0)
    at(p, 130.0)
    assert p.form == "menu" and not p._tucked  # never while the menu is open

    p._close_menu_now()
    assert p.form == "rest" and p.shown[-1] == "rest"
    at(p, 131.0)
    at(p, 131.0 + HIDE_DELAY - 0.01)
    assert not p._tucked  # no linger after the menu, only the delay
    at(p, 131.0 + HIDE_DELAY)
    assert p._tucked


def test_auto_hide_opens_the_menu_when_it_slides_in_under_the_cursor(hiding):
    p = hiding
    p.show_idle()
    at(p, 100.0)
    at(p, 103.0)
    p.cursor = (REST_X + 5, REST_CY)
    at(p, 104.0)  # revealed
    assert p.form == "rest"
    at(p, 104.1)  # arrived on it
    assert p.form == "menu"


def test_auto_hide_switched_live(clocked):
    p = clocked
    p.show_idle()
    p.set_auto_hide(True)
    assert p._auto_watch is not None and p._linger_until == 100.0 + LINGER_SECS
    p.set_auto_hide(False)
    assert p._auto_watch is None and p.form == "rest" and not p._tucked


def test_a_reading_while_hidden_shows_its_own_form_and_stops_the_watch(hiding):
    p = hiding
    p.show_idle()
    at(p, 100.0)
    at(p, 103.0)
    settle(p)
    assert p._tucked
    p._update(playing())
    assert p.form == "card" and p.shown[-1] == "card" and p._auto_watch is None and not p._tucked
    p._update(Status())  # the reading ended: back, lingering
    assert p.form == "rest" and p.shown[-1] == "rest" and p._auto_watch is not None
    assert p._linger_until == 103.0 + LINGER_SECS


def test_dropping_the_idle_pill_with_auto_hide_lingers_where_it_landed(hiding):
    p = hiding
    p.show_idle()
    p.clock[0] = 150.0
    move(p, 300, 500)
    p.drag_began()
    at(p, 150.0)
    assert not p._tucked  # never during a drag
    p.drag_ended_mac(320, 520)
    assert p.form == "rest" and p._edge == "left" and p._linger_until == 150.0 + LINGER_SECS
    at(p, 151.0)
    assert not p._tucked


# -- the resting ripple --------------------------------------------------------------------------


def test_the_resting_squiggle_ripples_on_each_tick_while_it_sits_there(pill):
    pill.show_idle()
    assert pill._wave_timer is not None and pill._wave_timer.interval() == WAVE_EVERY * 1000
    pill._wave_tick()
    assert pill.rest.waving()
    pill.rest.stop_wave()
    pill._reduce_motion = lambda: True
    pill._wave_tick()
    assert not pill.rest.waving()


def test_the_ripple_switched_off_does_not_ripple_and_cuts_one_in_flight(pill):
    pill.show_idle()
    pill._wave_tick()
    pill.set_wave(False)
    assert not pill.rest.waving() and pill._wave_timer is not None
    pill._wave_tick()
    assert not pill.rest.waving()
    pill.set_wave(True)
    pill._wave_tick()
    assert pill.rest.waving()


def test_a_drag_a_reading_or_the_menu_cuts_the_ripple(pill):
    pill.show_idle()
    pill._wave_tick()
    pill.drag_began()
    assert not pill.rest.waving()
    pill._wave_tick()
    assert not pill.rest.waving()  # in the hand
    pill.drag_ended_mac(1420, 460)
    pill._wave_tick()
    assert pill.rest.waving()
    pill.mouse_entered()
    assert pill.form == "menu" and pill._wave_timer is None and not pill.rest.waving()
    pill._close_menu_now()
    assert pill._wave_timer is not None
    pill._update(playing())
    assert pill._wave_timer is None


# -- threads -------------------------------------------------------------------------------------


def test_updates_from_a_worker_are_coalesced_and_drained_once(pill, monkeypatch):
    from lyssna.ui import mini_player as mp

    queued = []
    monkeypatch.setattr(mp, "on_main", lambda fn, *a: queued.append((fn, a)))

    def burst():
        for i in range(50):
            pill.update(playing(index=i + 1))

    t = threading.Thread(target=burst)
    t.start()
    t.join()
    assert len(queued) == 1
    fn, args = queued[0]
    fn(*args)
    assert pill.form == "card" and pill.progress.text() == "50 of 9"
    pill.update(playing(index=2))
    assert len(queued) == 2  # drained: the next snapshot queues again


def test_a_worker_update_lands_on_the_gui_thread(pill):
    seen = []
    real = pill._update
    pill._update = lambda st: (seen.append(threading.current_thread() is threading.main_thread()), real(st))
    t = threading.Thread(target=lambda: [pill.update(playing(index=i + 1)) for i in range(20)])
    t.start()
    t.join()
    pump(until=lambda: bool(seen))
    pump(0.05)
    assert seen == [True] and pill.progress.text() == "20 of 9"


def test_the_live_status_wins_over_a_stale_snapshot(pill):
    pill.status_fn = lambda: playing(index=7)
    pill.update(playing(index=2))
    assert pill.progress.text() == "7 of 9"


# -- dictation -----------------------------------------------------------------------------------
# The dictation half of legacy/test_dictation_pill.py, on the Qt player.


@pytest.fixture
def dictating(pill):
    return pill


def grown(p):
    pump(until=lambda: p._grow_anim is None)


def size(w):
    return (w.width(), w.height())


def box(view, ancestor):
    """A widget's rect inside an ancestor, Qt coordinates (y down)."""
    from PySide6.QtCore import QPoint

    o = view.mapTo(ancestor, QPoint(0, 0))
    return (o.x(), o.y(), view.width(), view.height())


def test_dictation_forms(dictating):
    p = dictating
    p.show_idle()
    p._set_dictation("recording")
    assert p.form == "dictate" and p.content is p.dictate_host and p.shown[-1] == "dictate"
    x, _, w, h = frame(p)
    assert (w, h) == (CAP_W, FREE_H) and x == pytest.approx(1440 - CAPSULE_INSET - CAP_W, abs=1)
    assert size(p.dictate_capsule) == (CAP_W, COMPACT_H)
    assert p.wave.running() and p.wave.mode == "recording"
    assert p.draggable is False  # a drag would fight push-to-talk
    assert p.cancel_btn.isHidden() and p.finish_btn.isHidden()
    assert p.margin > 0  # the capsule casts a shadow

    p._set_dictation("locked")
    grown(p)
    assert p.hands_free and size(p.dictate_capsule) == (CAP_W, FREE_H)
    assert not p.cancel_btn.isHidden() and not p.finish_btn.isHidden()
    assert p.cancel_btn.alpha == p.finish_btn.alpha == 1.0
    p.finish_btn.perform_click()
    p.cancel_btn.perform_click()
    assert p.fired == [("dictate_stop",), ("dictate_cancel",)]

    p._set_dictation("working")
    grown(p)
    assert not p.hands_free and size(p.dictate_capsule) == (CAP_W, COMPACT_H)
    assert p.cancel_btn.isHidden() and p.finish_btn.isHidden()
    assert p.wave.mode == "working" and p.form == "dictate" and p.wave.running()

    p._set_dictation("idle")
    assert p.form == "rest" and not p.wave.running() and p.draggable is True


def test_the_capsule_grows_about_its_centre_with_easy_buttons(dictating):
    p = dictating
    p._set_dictation("recording")
    c = p.dictate_capsule.geometry()
    centre = c.y() + c.height() / 2
    p._set_dictation("locked")
    grown(p)
    c = p.dictate_capsule.geometry()
    assert c.y() + c.height() / 2 == pytest.approx(centre) and c.y() == 0
    _, y, w, h = box(p.cancel_disc, p.dictate_capsule)
    assert (w, h) == (22, 22) and y == 6                         # 6 pt from the top
    _, y, w, h = box(p.finish_disc, p.dictate_capsule)
    assert (w, h) == (24, 24) and FREE_H - (y + h) == 5          # 5 pt from the bottom
    for btn, disc in ((p.cancel_btn, p.cancel_disc), (p.finish_btn, p.finish_disc)):
        assert size(btn) == (CAP_W, BUTTON_HIT)  # the click area, bigger than the disc
        assert btn.shape_rect().center() == disc.geometry().toRectF().center()
    cx = box(p.wave, p.dictate_capsule)
    assert cx[0] + cx[2] / 2 == CAP_W / 2 and cx[1] + cx[3] / 2 == FREE_H / 2  # the wave stays in the middle


def test_the_growth_animates_over_grow_secs_and_fades_the_buttons(dictating, monkeypatch):
    p = dictating
    p._set_dictation("recording")
    p._set_dictation("locked")
    anim = p._grow_anim
    assert anim is not None and anim.duration() == round(GROW_SECS * 1000)
    assert not p.cancel_btn.isHidden() and p.cancel_btn.alpha < 1.0  # shown at once, fading in
    assert p.dictate_host.shadow_rect.height() == COMPACT_H  # the shadow waits for the end, as on the Mac
    pump(until=lambda: COMPACT_H < p.dictate_capsule.height() < FREE_H)
    grown(p)
    assert p.dictate_capsule.height() == FREE_H and p.dictate_host.shadow_rect.height() == FREE_H
    p._set_dictation("working")
    assert not p.finish_btn.isHidden()  # fading out first
    grown(p)
    assert p.finish_btn.isHidden() and p.finish_btn.alpha == 0.0

    from lyssna.ui import theme

    monkeypatch.setattr(theme, "reduce_motion", lambda: True)
    p._set_dictation("locked")
    assert p._grow_anim is None and p.dictate_capsule.height() == FREE_H and p.finish_btn.alpha == 1.0


def test_dictation_on_the_bottom_edge_runs_left_to_right(dictating):
    from lyssna.ui.layout.wave import WAVE_H, WAVE_W

    p = dictating
    p.dictation_layout = {"dock": "bottom", "y": -1.0}
    p.show_idle()
    p._set_dictation("recording")
    x, y, w, h = frame(p)
    assert (w, h) == (FREE_H, CAP_W) and y == pytest.approx(25 + CAPSULE_INSET, abs=1)
    assert x + w / 2 == pytest.approx(720, abs=1)
    assert p.dictate_host is p._kits[True]["dictate_host"]
    assert size(p.dictate_capsule) == (COMPACT_H, CAP_W)
    assert p.wave.horizontal and size(p.wave) == (WAVE_H, WAVE_W)
    p._set_dictation("locked")
    grown(p)
    c = p.dictate_capsule.geometry()
    assert (c.width(), c.height(), c.x()) == (FREE_H, CAP_W, 0)
    cancel, finish = box(p.cancel_disc, p.dictate_capsule), box(p.finish_disc, p.dictate_capsule)
    assert cancel[0] == 6 and cancel[2] == 22                    # cancel at the left
    assert FREE_H - (finish[0] + finish[2]) == 5                 # finish at the right
    for btn in (p.cancel_btn, p.finish_btn):
        assert size(btn) == (BUTTON_HIT, CAP_W)
    p._set_dictation("working")
    grown(p)
    assert size(p.dictate_capsule) == (COMPACT_H, CAP_W)
    p._set_dictation("idle")
    assert p.form == "rest" and p.rest.edge == "bottom"


def test_a_dictation_during_a_card_reading_brings_the_card_back(dictating):
    p = dictating
    p._update(playing())
    assert p.form == "card"
    before = frame(p)
    p._set_dictation("recording")
    assert p.form == "dictate" and p.content is p.dictate_host  # one pill: the card is gone
    p._update(playing(state="paused"))  # the reading paused meanwhile
    assert p.form == "dictate"
    p._set_dictation("working")
    p._set_dictation("idle")
    assert p.form == "card" and frame(p) == pytest.approx(before, abs=1)
    assert all(b.icon == "play.fill" for b in p.buttons["toggle"])  # still paused


def test_a_dictation_during_a_docked_reading_uses_the_dock_edge(dictating):
    p = dictating
    p.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    p._update(playing())
    assert p.form == "sliver" and p.dock == "left"
    sliver = frame(p)
    p._set_dictation("recording")
    x, y, _w, h = frame(p)
    assert x == pytest.approx(CAPSULE_INSET, abs=1)
    assert y + h / 2 == pytest.approx(sliver[1] + sliver[3] / 2, abs=1)  # the same height as the dock
    p._set_dictation("idle")
    assert p.form == "sliver" and frame(p) == pytest.approx(sliver, abs=1)


def test_a_dictation_over_the_open_pill_gives_back_the_sliver(dictating):
    p = dictating
    p.layout = {"dock": "right", "x": 0.0, "y": 0.5}
    p._update(playing())
    sliver = frame(p)
    p.mouse_entered()
    assert p.form == "pill"
    p._set_dictation("recording")
    assert p.form == "dictate" and p._watch is None and p._pending_done is None
    p._set_dictation("idle")
    assert p.form == "sliver" and frame(p) == pytest.approx(sliver, abs=1)


def test_a_reading_that_ends_during_a_dictation_leaves_the_idle_pill(dictating):
    p = dictating
    p._update(playing())
    p._set_dictation("recording")
    p._update(Status())
    assert p.form == "dictate" and p._reading_spot is None
    p._set_dictation("idle")
    assert p.form == "rest"


def test_no_menu_and_no_ripple_during_a_dictation(dictating):
    p = dictating
    p.show_idle()
    assert p._wave_timer is not None
    p._set_dictation("recording")
    assert p._wave_timer is None
    p.mouse_entered()
    assert p.form == "dictate"
    p._set_dictation("idle")
    assert p.form == "rest" and p._wave_timer is not None


def test_a_dictation_while_hidden_shows_its_form_and_stops_the_watch(dictating):
    p = dictating
    p.auto_hide = True
    p.clock = [100.0]
    p._now = lambda: p.clock[0]
    p._cursor = lambda: FAR
    p.hide = lambda: p.shown.clear()
    p.show_idle()
    at(p, 100.0)
    at(p, 100.0 + LINGER_SECS)
    settle(p)
    assert p._tucked and p.shown == []
    p._set_dictation("recording")
    assert p.form == "dictate" and p.shown[-1] == "dictate" and p._auto_watch is None and not p._tucked
    p._set_dictation("idle")
    assert p.form == "rest" and p._auto_watch is not None and not p._tucked


def test_level_updates_do_not_flood_the_gui_thread(dictating, monkeypatch):
    from lyssna.dictation.types import DictationStatus
    from lyssna.ui import mini_player as mp

    p = dictating
    queued = []
    monkeypatch.setattr(mp, "_post", lambda fn: queued.append(fn))

    def burst():
        for i in range(50):
            p.dictation_update(DictationStatus(state="recording", level=i / 50))

    t = threading.Thread(target=burst)
    t.start()
    t.join()
    assert len(queued) == 1 and p._level == pytest.approx(49 / 50)
    queued[0]()
    assert p.form == "dictate"
    p.dictation_update(DictationStatus(state="recording", level=0.2))
    assert len(queued) == 1  # same state: only the level is stored
    p.dictation_update(DictationStatus(state="locked", level=0.2))
    p.dictation_update(DictationStatus(state="idle"))
    assert len(queued) == 2  # two changes before the drain: one hop, and the latest wins
    queued[1]()
    assert p.form == "rest" and p.dictation_state == "idle"


def test_a_dictation_update_never_runs_inline(dictating):
    from lyssna.dictation.types import DictationStatus

    p = dictating
    p.show_idle()
    p.dictation_update(DictationStatus(state="recording", level=0.4))  # on the GUI thread itself
    assert p.form == "rest"  # posted, not run inside the caller (it may be a key hook)
    pump(until=lambda: p.form == "dictate")
    t = threading.Thread(target=lambda: p.dictation_update(DictationStatus(state="idle")))
    t.start()
    t.join()
    pump(until=lambda: p.form == "rest")


def test_wave_reads_the_stored_level_and_breathes_while_working(dictating):
    from lyssna.ui.layout.wave import AMP_MAX, WORK_AMP

    p = dictating
    p._set_dictation("recording")
    p.wave.stop()
    p._level = 1.0
    p.wave._last = 0.0
    for i in range(1, 30):
        p.wave.tick(i / 30)
    assert p.wave.amplitude == pytest.approx(AMP_MAX, abs=0.1) and p.wave.opacity == 1.0
    p._set_dictation("working")
    p.wave._breath_start = 1.0
    seen = set()
    for i in range(30, 120):
        p.wave.tick(i / 30)
        seen.add(round(p.wave.opacity, 2))
    assert p.wave.amplitude == pytest.approx(WORK_AMP, abs=0.1)
    assert min(seen) < 0.6 and max(seen) > 0.95  # it breathes


@pytest.mark.parametrize(("btn", "text", "key"), [("finish_btn", "Finish", "Ctrl+Win"), ("cancel_btn", "Cancel", "esc")])
def test_hands_free_tips(dictating, btn, text, key):
    shown = []

    class Tip:
        def show_beside(self, anchor, edge, text, key):
            shown.append((anchor, edge, text, key))

        def hide(self):
            shown.append("hidden")

    p = dictating
    p.tip = Tip()
    p.key_name = "Ctrl+Win"
    p._set_dictation("recording")
    p._set_dictation("locked")
    grown(p)
    button = getattr(p, btn)
    button.set_hover(True, animate=False)
    pump(0.6)
    (anchor, edge, t, k), = shown
    assert (edge, t, k) == ("right", text, key)
    disc = p.finish_disc if btn == "finish_btn" else p.cancel_disc
    assert anchor[2:] == (disc.width(), disc.height())  # beside the disc, not the whole strip
    button.perform_click()
    assert shown[-1] == "hidden"


def test_toast_anchor_is_a_bottom_dictation(dictating):
    p = dictating
    p.dictation_layout = {"dock": "bottom", "y": -1.0}
    p._set_dictation("recording")
    assert p.form == "dictate" and p.toast_anchor() == (frame(p), "bottom")


# -- toasts --------------------------------------------------------------------------------------


def test_toast_anchor_is_the_pill_and_its_edge(pill):
    assert pill.toast_anchor() is None  # not on screen
    pill.show_idle()  # right edge
    assert pill.toast_anchor() == (frame(pill), "right")
    pill.dictation_layout = {"dock": "left", "y": -1.0}
    pill.show_idle()
    assert pill.toast_anchor() == (frame(pill), "left")
    pill.dictation_layout = {"dock": "bottom", "y": -1.0}
    pill.show_idle()
    assert pill.toast_anchor() == (frame(pill), "bottom")


# -- the sliver ----------------------------------------------------------------------------------


@pytest.fixture
def sliver(qapp):
    from lyssna.ui.sliver import Sliver

    s = Sliver(None, "left", seed=1)
    yield s
    s.hide()
    s.deleteLater()


def test_the_sliver_animates_only_on_screen_and_playing(sliver, monkeypatch):
    from lyssna.ui import theme

    sliver.set_state(0.4, False, "left", playing=True)
    assert not sliver.animating()  # not on screen
    sliver.show()
    assert sliver.animating()
    sliver.hide()
    assert not sliver.animating()  # every animation stops with the view
    sliver.show()
    sliver.set_state(0.4, True, "left", playing=False)
    pump(until=lambda: not sliver.animating())  # paused: the tween and the sparks aloft finish, then nothing
    monkeypatch.setattr(theme, "reduce_motion", lambda: True)
    sliver.set_state(0.5, False, "left", playing=True)
    assert not sliver.animating()  # Reduce Motion: the still state


def test_sparks_are_born_while_playing_and_die_out_when_paused(sliver):
    sliver.set_state(0.5, False, "left", playing=True)
    for _ in range(90):
        sliver.advance(1 / 30)
    assert 3 <= sliver.sparks() <= 12  # about 2.2 a second, living about 2.4 seconds
    sliver.set_state(0.5, True, "left", playing=False)
    for _ in range(120):
        sliver.advance(1 / 30)
    assert sliver.sparks() == 0


def test_the_waterline_eases_and_a_bloom_only_while_playing(sliver):
    sliver.show()
    sliver.set_state(0.2, False, "left", playing=True)
    sliver.advance(1.0)
    sliver.set_state(0.8, False, "left", playing=True)
    sliver.advance(0.1)
    assert 0.2 < sliver._shown < 0.8
    sliver.advance(0.3)
    assert sliver._shown == pytest.approx(0.8)
    sliver.pulse()
    assert sliver._bloom == 0.0
    sliver.set_state(0.8, True, "left", playing=False)
    sliver._bloom = None
    sliver.pulse()
    assert sliver._bloom is None


def test_the_squiggle_ripples_once_and_ends_at_rest(sliver):
    from lyssna.ui.layout.wave import WAVE_SECS
    from lyssna.ui.sliver import ripple_phase

    rest = sliver.squiggle_path()
    sliver.wave()
    assert not sliver.waving()  # only at rest
    sliver.set_rest(True)
    sliver.wave()
    assert sliver.waving()
    sliver.advance(WAVE_SECS / 2)
    assert sliver.squiggle_path() != rest
    sliver.advance(WAVE_SECS / 2)
    assert not sliver.waving() and sliver.squiggle_path() == rest
    assert ripple_phase(0.0) == 0.0 and ripple_phase(WAVE_SECS) == pytest.approx(0.0)
    assert sliver.shadow_path().isEmpty()


@pytest.mark.parametrize("edge", ["left", "right", "bottom"])
def test_the_capsule_sits_on_the_inner_side_and_lies_flat_on_the_bottom(qapp, edge):
    from lyssna.ui.sliver import Sliver

    s = Sliver(None, edge)
    s.set_state(0.4, False, edge)
    b = s.shadow_path().boundingRect()
    if edge == "bottom":
        assert (b.width(), b.height()) == pytest.approx((CAPSULE_H, CAPSULE_W))
        assert b.bottom() == pytest.approx(SLIVER_W - CAPSULE_INSET)  # CAPSULE_INSET above the screen edge
    else:
        assert (b.width(), b.height()) == pytest.approx((CAPSULE_W, CAPSULE_H))
        gap = b.left() if edge == "left" else SLIVER_W - b.right()
        assert gap == pytest.approx(CAPSULE_INSET)
    s.deleteLater()


def test_previews_build(qapp):
    from lyssna.ui import mini_player as mp

    for name, factory in mp.PREVIEWS.items():
        w = factory()
        assert w.width() > 0 and w.height() > 0, name
        w.hide()
        w.deleteLater()
    QCoreApplication.processEvents()
