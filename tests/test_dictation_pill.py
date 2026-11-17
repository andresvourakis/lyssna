"""The unified pill's idle and dictation forms, the toast, the tooltip pill and the wave meter.

The rules (which form, which edge, where) and the wave's motion are plain functions. The
AppKit part is driven without ever putting a window on screen: order_front is replaced on
each panel, so the tests check forms, frames and view state, not pixels. The whole AppKit
half skips without a window server."""

import itertools
import threading

import AppKit
import pytest
from Foundation import NSDate, NSRunLoop

from lyssna.controller import Notice
from lyssna.player import Status
from lyssna.ui import mini_player as mp_mod
from lyssna.ui import wave
from lyssna.ui.mini_player import (
    BUTTON_HIT,
    CAP_W,
    COMPACT_H,
    DOCK_GAP,
    DOCK_GAP_V,
    DOCK_H,
    DOCK_W,
    FREE_H,
    MENU_DISC,
    MENU_H,
    MENU_MIC_H,
    MENU_MIC_W,
    MENU_W,
    along_centre,
    along_fraction,
    anchor_of,
    dictation_spot,
    docked_frame,
    edge_frame,
    lay,
    nearest_edge,
    oriented,
    pick_form,
    pill_len,
    snap_edge,
)
from lyssna.ui.toast import ACTION_PAD, ACTION_RIGHT, BOTTOM, PAD_L, TOAST_H, tip_origin, toast_width
from lyssna.ui.widgets import CAPSULE_H, CAPSULE_INSET, CAPSULE_W, SLIVER_H, SLIVER_W, capsule_centre

# -- the rules -------------------------------------------------------------------------------


@pytest.mark.parametrize(("reading", "dictation", "docked", "idle_pill", "form"), [
    ("idle", "idle", False, True, "rest"),
    ("idle", "idle", False, False, ""),
    ("idle", "notice", True, True, "rest"),         # a notice is a toast, not a form
    ("playing", "idle", False, True, "card"),
    ("paused", "idle", True, True, "sliver"),
    ("playing", "recording", False, True, "dictate"),  # dictation wins
    ("paused", "locked", True, False, "dictate"),
    ("idle", "working", False, False, "dictate"),
])
def test_pick_form(reading, dictation, docked, idle_pill, form):
    assert pick_form(reading, dictation, docked, idle_pill) == form


def test_dictation_spot_follows_the_docked_player_then_the_idle_pill_then_the_bottom_centre():
    assert dictation_spot({"dock": "left", "y": 0.3}, {"dock": "right", "y": 0.9}) == ("left", 0.3)
    assert dictation_spot({"dock": "bottom", "y": 0.2}, {"dock": "right", "y": 0.9}) == ("bottom", 0.2)
    assert dictation_spot({"dock": "", "y": 0.5}, {"dock": "left", "y": 0.9}) == ("left", 0.9)
    assert dictation_spot({"dock": "", "y": 0.5}, {"dock": "bottom", "y": 0.7}) == ("bottom", 0.7)
    assert dictation_spot({"dock": "", "y": 0.5}, {"dock": "", "y": 0.4}) == ("bottom", -1.0)  # fresh config


VF = (0.0, 25.0, 1440.0, 875.0)
EDGES = ("left", "right", "bottom")


def test_oriented_and_lay():
    assert oriented(60, 34, "left") == oriented(60, 34, "right") == (34, 60)
    assert oriented(60, 34, "bottom") == (60, 34)
    assert lay(10, 20, 3, 8, 100, flat=False) == ((3, 10), (8, 20))           # upright: along is y
    assert lay(10, 20, 3, 8, 100, flat=True) == ((10, 3), (20, 8))            # flat: along is x
    assert lay(10, 20, 3, 8, 100, flat=True, flip=True) == ((70, 3), (20, 8))  # read from the right


@pytest.mark.parametrize("edge", EDGES)
def test_edge_frame_hugs_each_edge(edge):
    (x, y), (w, h) = edge_frame(VF, edge, -1.0, COMPACT_H, CAP_W)
    assert (w, h) == oriented(COMPACT_H, CAP_W, edge)
    if edge == "left":
        assert x == CAPSULE_INSET and y + h / 2 == pytest.approx(25 + 875 / 2)
    elif edge == "right":
        assert x + w == 1440 - CAPSULE_INSET and y + h / 2 == pytest.approx(25 + 875 / 2)
    else:  # above the Dock, by the same gap, in the middle
        assert y == 25 + CAPSULE_INSET and x + w / 2 == pytest.approx(720)
    (x, y), (w, h) = edge_frame(VF, edge, 0.5, SLIVER_H, SLIVER_W, inset=0.0)
    assert {"left": x, "right": 1440 - (x + w), "bottom": y - 25}[edge] == 0  # the sliver is flush


@pytest.mark.parametrize("edge", EDGES)
def test_a_saved_fraction_means_the_same_spot_for_every_form(edge):
    """The fraction is measured on the room an open pill has, so every form on the edge
    centres on the same point: a dictation lines up with the docked player."""
    for frac in (0.0, 0.25, 1.0):
        centres = set()
        for length, across in ((COMPACT_H, CAP_W), (FREE_H, CAP_W), (SLIVER_H, SLIVER_W), (DOCK_H, DOCK_W)):
            (x, y), (w, h) = edge_frame(VF, edge, frac, length, across)
            centres.add(round(x + w / 2 if edge == "bottom" else y + h / 2, 6))
        assert centres == {round(along_centre(VF, edge, frac), 6)}


@pytest.mark.parametrize("edge", EDGES)
def test_edge_frame_stays_on_screen(edge):
    for frac in (0.0, 1.0):
        (x, y), (w, h) = edge_frame((0, 0, 800, 600), edge, frac, FREE_H, CAP_W)
        assert 0 <= x and x + w <= 800 and 0 <= y and y + h <= 600


@pytest.mark.parametrize("edge", EDGES)
def test_along_fraction_inverts_along_centre(edge):
    for frac in (0.0, 0.3, 1.0):
        assert along_fraction(VF, edge, along_centre(VF, edge, frac)) == pytest.approx(frac)
    assert along_fraction(VF, edge, -500) == 0.0 and along_fraction(VF, edge, 5000) == 1.0


@pytest.mark.parametrize("edge", EDGES)
def test_docked_frame_and_anchor_of_round_trip(edge):
    coord = {"left": 0.0, "right": 1440.0, "bottom": 25.0}[edge]
    frame = docked_frame(edge, coord, 400.0, MENU_H, MENU_W, DOCK_GAP)
    assert frame[1] == oriented(MENU_H, MENU_W, edge)
    assert anchor_of(frame, edge, DOCK_GAP) == (coord, 400.0)


def test_nearest_and_snap_edges():
    assert nearest_edge(VF, 100, 500) == "left"
    assert nearest_edge(VF, 1400, 500) == "right"
    assert nearest_edge(VF, 700, 60) == "bottom"
    assert nearest_edge(VF, 30, 50) == "bottom" and nearest_edge(VF, 20, 50) == "left"  # nearer wins
    assert snap_edge(VF, ((10, 400), (46, 100))) == "left"
    assert snap_edge(VF, ((1400, 400), (30, 100))) == "right"
    assert snap_edge(VF, ((600, 40), (200, 50))) == "bottom"
    assert snap_edge(VF, ((600, 300), (200, 50))) == ""
    assert snap_edge(VF, ((5, 40), (100, 50))) == "left"     # in the corner, the nearer edge
    assert snap_edge(VF, ((20, 30), (100, 50))) == "bottom"
    assert snap_edge(VF, ((5, 30), (100, 50))) == "left"     # a tie goes to the side, as nearest_edge


def test_sizes():
    assert (CAP_W, COMPACT_H, FREE_H) == (34.0, 60.0, 118.0)
    assert (MENU_MIC_W, MENU_MIC_H, MENU_DISC) == (CAP_W, 52.0, 28.0)  # the dictate item as slim as the capsule
    assert MENU_H == 52.0 + 2 * (DOCK_GAP_V + 28.0) and MENU_W == CAP_W
    assert BUTTON_HIT >= 28.0
    assert pill_len(False) == DOCK_H


@pytest.mark.parametrize("edge", EDGES)
def test_the_capsule_sits_on_the_inner_side_of_the_sliver(edge):
    x, y = capsule_centre(edge)
    w, h = oriented(SLIVER_H, SLIVER_W, edge)
    near = CAPSULE_INSET + CAPSULE_W / 2
    assert {"left": x, "right": w - x, "bottom": y}[edge] == near
    assert (y if edge != "bottom" else x) == (h if edge != "bottom" else w) / 2
    assert CAPSULE_H < SLIVER_H


# -- the wave ---------------------------------------------------------------------------------


def test_amplitude_follows_the_level():
    assert wave.target_amplitude(0.0) == wave.AMP_MIN == 1.0
    assert wave.target_amplitude(1.0) == wave.AMP_MAX == 5.5
    assert wave.target_amplitude(7.0) == 5.5 and wave.target_amplitude(-1) == 1.0
    assert (wave.WAVE_W, wave.WAVE_H, wave.STROKE, wave.WORK_AMP) == (14.0, 36.0, 2.5, 2.5)


def test_ease_attacks_fast_and_releases_slowly():
    up = wave.ease(1.0, 5.5, 0.06)    # one attack time constant: about 63 % of the way
    down = wave.ease(5.5, 1.0, 0.06)
    assert up == pytest.approx(1.0 + 4.5 * 0.632, abs=0.01)
    assert 5.5 - down < up - 1.0  # falling is slower than rising
    assert wave.ease(3.0, 3.0, 1.0) == 3.0 and wave.ease(2.0, 5.0, 0.0) == 2.0


def test_wave_lies_flat_from_the_same_routine():
    upright = wave.wave_points(4.0, 0.1)
    flat = wave.wave_points(4.0, 0.1, wave.WAVE_H, wave.WAVE_W, horizontal=True)
    assert flat == [(a, b) for b, a in upright]  # the same wave, axes swapped: left to right


def test_wave_points_run_down_the_meter_with_one_and_a_half_cycles():
    pts = wave.wave_points(4.0, 0.1)
    assert len(pts) == wave.STEPS + 1
    assert pts[0][1] == pytest.approx(wave.STROKE / 2) and pts[-1][1] == pytest.approx(wave.WAVE_H - wave.STROKE / 2)
    xs = [x for x, _ in pts]
    mid = wave.WAVE_W / 2
    assert max(xs) == pytest.approx(mid + 4.0, abs=0.2) and min(xs) == pytest.approx(mid - 4.0, abs=0.2)
    crossings = sum(1 for a, b in itertools.pairwise(xs) if (a - mid) * (b - mid) < 0)
    assert crossings == 3  # 1.5 cycles: three times across the middle
    # the widest swing plus the stroke stays inside the meter
    assert wave.WAVE_W / 2 + wave.AMP_MAX + wave.STROKE / 2 <= wave.WAVE_W


# -- the toast and the tip (pure) -----------------------------------------------------------


def test_toast_width():
    assert toast_width(100, 0) == PAD_L + 100 + PAD_L
    assert toast_width(100, 40) == PAD_L + 100 + 12 + (40 + 2 * ACTION_PAD) + ACTION_RIGHT


def test_tip_goes_on_the_inner_side():
    assert tip_origin((1000, 400, 34, 34), "right", 80) == (1000 - 8 - 80, 400 + (34 - 30) / 2)
    assert tip_origin((10, 400, 34, 34), "left", 80) == (10 + 34 + 8, 402)
    assert tip_origin((600, 40, 34, 34), "bottom", 80) == (600 + (34 - 80) / 2, 40 + 34 + 8)  # above it


# -- the pill on AppKit ----------------------------------------------------------------------

appkit = pytest.mark.skipif(not AppKit.NSScreen.screens(), reason="no window server")


@pytest.fixture(scope="module")
def nsapp():
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    return app


@pytest.fixture
def pill(nsapp, monkeypatch):
    """A MiniPlayer that never reaches the screen, on a fixed fake screen."""
    vf = AppKit.NSMakeRect(*VF)
    monkeypatch.setattr(mp_mod, "visible_frame", lambda screen=None: vf)
    p = mp_mod.MiniPlayer.alloc().init()
    p.shown = []
    p.order_front = lambda: p.shown.append(p.form)
    p.tip.order_front = lambda: None
    p.zones = []
    p.zone.order_front = lambda: p.zones.append("side")
    p.flat_zone.order_front = lambda: p.zones.append("flat")
    p.visible = lambda: bool(p.shown)  # as if each order_front had put it on screen
    p.dictation_layout = {"dock": "right", "y": -1.0}  # the side tests below; bottom ones set their own
    p.fired = []
    p.actions = {name: (lambda *a, n=name: p.fired.append((n, *a)))
                 for name in ("dictate", "dictate_stop", "dictate_cancel", "layout", "dictation_layout", "toggle",
                              "read_selection", "history")}
    yield p
    p.wave.stop()
    p._stop_watch()


def frame(p):
    f = p.panel.frame()
    return (f.origin.x, f.origin.y, f.size.width, f.size.height)


def centre_y(p):
    f = p.panel.frame()
    return f.origin.y + f.size.height / 2


def pump(seconds):
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


@appkit
def test_a_saved_right_edge_shows_the_resting_sliver_there(pill):
    pill.show_idle()
    assert pill.form == "rest" and pill.panel.contentView() is pill.rest
    x, _y, w, h = frame(pill)
    assert (w, h) == (SLIVER_W, SLIVER_H) and x == 1440 - SLIVER_W
    assert pill.rest.rest and pill.rest.fraction == 0.0 and pill.rest.edge == "right"
    assert pill.draggable is True and pill.shown == ["rest"]


@appkit
def test_idle_pill_off_shows_nothing(pill):
    pill.idle_pill = False
    pill.show_idle()
    assert pill.form != "rest" and pill.shown == []


@appkit
def test_hover_opens_the_menu_beside_the_sliver(pill):
    pill.show_idle()
    rest_centre = centre_y(pill)
    pill.mouseEntered_(None)
    assert pill.form == "menu" and pill.panel.contentView() is pill.menu_host
    x, _y, w, h = frame(pill)
    assert (w, h) == (MENU_W, MENU_H) and x == 1440 - DOCK_GAP - MENU_W
    assert centre_y(pill) == pytest.approx(rest_centre)
    # bottom to top: the dictate capsule, read selection, history, DOCK_GAP_V apart
    d, r, hi = (v.frame() for v in (pill.menu_dictate, pill.menu_read, pill.menu_history))
    assert (d.origin.y, d.size.width, d.size.height) == (0.0, MENU_MIC_W, MENU_MIC_H)
    assert r.origin.y == MENU_MIC_H + DOCK_GAP_V and r.size.width == MENU_DISC
    assert hi.origin.y == r.origin.y + MENU_DISC + DOCK_GAP_V and hi.size.width == MENU_DISC
    for item in (d, r, hi):  # centred on one axis
        assert item.origin.x + item.size.width / 2 == pytest.approx(MENU_W / 2)
    assert pill._watch is not None  # the hover watcher closes it when the mouse leaves


@appkit
@pytest.mark.parametrize(("action", "fired"), [
    ("menuDictate_", "dictate"), ("menuRead_", "read_selection"), ("menuHistory_", "history"),
])
def test_a_menu_item_closes_the_menu_then_acts(pill, action, fired):
    pill.show_idle()
    pill.mouseEntered_(None)
    seen = []
    pill.actions[fired] = lambda: seen.append(pill.form)
    getattr(pill, action)(None)
    assert seen == ["rest"] and pill.form == "rest"  # closed before the action ran


@appkit
def test_leaving_the_menu_shrinks_it_back_to_the_sliver(pill):
    pill.show_idle()
    rest = frame(pill)
    pill.mouseEntered_(None)
    pill._close_menu()
    pump(0.4)  # the scale animation's completion
    assert pill.form == "rest" and frame(pill) == rest


@appkit
def test_no_menu_during_a_reading_or_a_dictation(pill):
    pill._update(Status(state="playing", session=1, total=3, index=1))
    pill.mouseEntered_(None)
    assert pill.form == "card"
    pill._update(Status())
    pill._set_dictation("recording")
    pill.mouseEntered_(None)
    assert pill.form == "dictate"


@appkit
def test_dictation_forms(pill):
    pill.show_idle()
    pill._set_dictation("recording")
    assert pill.form == "dictate" and pill.panel.contentView() is pill.dictate_host
    x, _, w, h = frame(pill)
    assert (x, w, h) == (1440 - CAPSULE_INSET - CAP_W, CAP_W, FREE_H)
    assert pill.dictate_capsule.frame().size == (CAP_W, COMPACT_H)
    assert pill.wave.running() and pill.wave.mode == "recording"
    assert pill.draggable is False  # a drag would fight push-to-talk
    assert pill.cancel_bg.isHidden() and pill.finish_bg.isHidden()

    pill._set_dictation("locked")
    assert pill.hands_free and pill.dictate_capsule.frame().size.height == FREE_H
    assert not pill.cancel_bg.isHidden() and not pill.finish_bg.isHidden()
    pill.dictateFinish_(None)
    pill.dictateCancel_(None)
    assert pill.fired == [("dictate_stop",), ("dictate_cancel",)]

    pill._set_dictation("working")
    assert not pill.hands_free and pill.dictate_capsule.frame().size.height == COMPACT_H
    assert pill.wave.mode == "working" and pill.form == "dictate"

    pill._set_dictation("idle")
    assert pill.form == "rest" and not pill.wave.running() and pill.draggable is True


@appkit
def test_the_capsule_grows_about_its_centre_with_easy_buttons(pill):
    pill._set_dictation("recording")
    c = pill.dictate_capsule.frame()
    centre = c.origin.y + c.size.height / 2
    pill._set_dictation("locked")
    c = pill.dictate_capsule.frame()
    assert c.origin.y + c.size.height / 2 == pytest.approx(centre)

    def in_capsule(view):
        r = view.convertRect_toView_(view.bounds(), pill.dictate_capsule)
        return r.origin.y, r.size.width, r.size.height

    y, w, h = in_capsule(pill.cancel_disc)
    assert (w, h) == (22.0, 22.0) and FREE_H - (y + h) == pytest.approx(6.0)  # 6 pt from the top
    y, w, h = in_capsule(pill.finish_disc)
    assert (w, h) == (24.0, 24.0) and y == pytest.approx(5.0)                 # 5 pt from the bottom
    for strip, btn in ((pill.cancel_bg, pill.cancel_btn), (pill.finish_bg, pill.finish_btn)):
        assert strip.frame().size == (CAP_W, BUTTON_HIT)  # the click area, bigger than the disc
        assert btn.frame().size == (CAP_W, BUTTON_HIT)


@appkit
def test_a_dictation_during_a_card_reading_brings_the_card_back(pill):
    pill._update(Status(state="playing", session=1, total=3, index=1))
    assert pill.form == "card"
    before = frame(pill)
    pill._set_dictation("recording")
    assert pill.form == "dictate"
    pill._update(Status(state="paused", session=1, total=3, index=1))  # the reading paused meanwhile
    assert pill.form == "dictate"
    pill._set_dictation("working")
    pill._set_dictation("idle")
    assert pill.form == "card" and frame(pill) == before


@appkit
def test_a_dictation_during_a_docked_reading_uses_the_dock_edge(pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill._update(Status(state="playing", session=1, total=3, index=1))
    assert pill.form == "sliver" and pill.dock == "left"
    sliver = frame(pill)
    pill._set_dictation("recording")
    x, y, _w, h = frame(pill)
    assert x == CAPSULE_INSET
    assert y + h / 2 == pytest.approx(sliver[1] + sliver[3] / 2)  # the same height as the dock
    pill._set_dictation("idle")
    assert pill.form == "sliver" and frame(pill) == sliver


@appkit
def test_the_resting_sliver_sits_where_the_docked_sliver_would(pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill._update(Status(state="playing", session=1, total=3, index=1))
    reading = frame(pill)
    pill._update(Status())
    assert pill.form == "rest" and frame(pill) == reading


@appkit
def test_a_reading_that_ends_during_a_dictation_leaves_the_idle_pill(pill):
    pill._update(Status(state="playing", session=1, total=3, index=1))
    pill._set_dictation("recording")
    pill._update(Status())
    assert pill.form == "dictate"
    pill._set_dictation("idle")
    assert pill.form == "rest"


@appkit
def test_a_reading_replaces_the_idle_pill(pill):
    pill.show_idle()
    pill.mouseEntered_(None)
    pill._update(Status(state="playing", session=1, total=3, index=1))
    assert pill.form == "card" and pill.panel.contentView() is pill.card
    pill._update(Status())
    assert pill.form == "rest"


@appkit
@pytest.mark.parametrize("open_menu", [False, True])
def test_dropping_the_idle_pill_snaps_to_the_nearer_edge_and_is_remembered(pill, open_menu):
    pill.show_idle()
    if open_menu:
        pill.mouseEntered_(None)
    pill.panel.setFrameOrigin_((300, 500))
    pill.drag_began()
    pill.drag_ended(320, 520)
    x, y, w, h = frame(pill)
    assert pill.form == "rest" and (x, w, h) == (0.0, SLIVER_W, SLIVER_H)
    name, edge, fy = pill.fired[-1]
    assert (name, edge) == ("dictation_layout", "left") and fy == pytest.approx(along_fraction(VF, "left", y + h / 2))
    assert pill.dictation_layout == {"dock": "left", "y": fy}


@appkit
def test_dropping_the_idle_pill_moves_a_docked_player_too(pill):
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill.show_idle()
    pill.panel.setFrameOrigin_((1300, 500))
    pill.drag_began()
    pill.drag_ended(1320, 520)
    assert pill.fired[-1][:2] == ("layout", "right") and pill.layout["dock"] == "right"


@appkit
def test_level_updates_do_not_flood_the_main_thread(pill, monkeypatch):
    queued = []

    class Queue:
        @staticmethod
        def mainQueue():
            return Queue

        @staticmethod
        def addOperationWithBlock_(block):
            queued.append(block)

    monkeypatch.setattr(mp_mod, "NSOperationQueue", Queue)
    from lyssna.dictation.types import DictationStatus

    def burst():
        for i in range(50):
            pill.dictation_update(DictationStatus(state="recording", level=i / 50))

    t = threading.Thread(target=burst)
    t.start()
    t.join()
    assert len(queued) == 1 and pill._level == pytest.approx(49 / 50)
    queued[0]()
    assert pill.form == "dictate"
    pill.dictation_update(DictationStatus(state="recording", level=0.2))
    assert len(queued) == 1  # same state: only the level is stored
    pill.dictation_update(DictationStatus(state="idle"))
    assert len(queued) == 2
    queued[1]()
    assert pill.form == "rest"


@appkit
def test_wave_reads_the_stored_level(pill):
    pill._set_dictation("recording")
    pill._level = 1.0
    pill.wave._last = 0.0
    for i in range(1, 30):
        pill.wave.tick(i / 30)
    assert pill.wave.amplitude == pytest.approx(wave.AMP_MAX, abs=0.1)
    pill._set_dictation("working")
    for i in range(30, 120):
        pill.wave.tick(i / 30)
    assert pill.wave.amplitude == pytest.approx(wave.WORK_AMP, abs=0.1)


# -- the bottom dock ---------------------------------------------------------------------------


def box(view, ancestor):
    r = view.convertRect_toView_(view.bounds(), ancestor)
    return (r.origin.x, r.origin.y, r.size.width, r.size.height)


@appkit
def test_a_fresh_config_rests_at_the_bottom_centre(pill):
    pill.dictation_layout = {"dock": "", "y": -1.0}
    pill.show_idle()
    x, y, w, h = frame(pill)
    assert (w, h) == (SLIVER_H, SLIVER_W) and y == 25 and x + w / 2 == pytest.approx(720)
    assert pill.panel.contentView() is pill._kits[True]["rest"] and pill.rest.edge == "bottom"


@appkit
def test_the_bottom_menu_is_a_row_growing_up_from_the_sliver(pill):
    pill.dictation_layout = {"dock": "bottom", "y": 0.3}
    pill.show_idle()
    rest_cx = frame(pill)[0] + SLIVER_H / 2
    pill.mouseEntered_(None)
    x, y, w, h = frame(pill)
    assert pill.form == "menu" and pill.panel.contentView() is pill._kits[True]["menu_host"]
    assert (w, h) == (MENU_H, MENU_W) and y == 25 + DOCK_GAP and x + w / 2 == pytest.approx(rest_cx)
    d, r, hi = (box(v, pill.menu) for v in (pill.menu_dictate, pill.menu_read, pill.menu_history))
    assert d[0] == 0 and r[0] == d[2] + DOCK_GAP_V and hi[0] == r[0] + r[2] + DOCK_GAP_V  # left to right
    assert (d[2], d[3]) == (MENU_MIC_H, MENU_MIC_W)
    for item in (d, r, hi):
        assert item[1] + item[3] / 2 == pytest.approx(MENU_W / 2)  # one axis


@appkit
def test_dictation_on_the_bottom_edge_runs_left_to_right(pill):
    pill.dictation_layout = {"dock": "bottom", "y": -1.0}
    pill.show_idle()
    pill._set_dictation("recording")
    x, y, w, h = frame(pill)
    assert (w, h) == (FREE_H, CAP_W) and y == 25 + CAPSULE_INSET and x + w / 2 == pytest.approx(720)
    assert pill.dictate_capsule.frame().size == (COMPACT_H, CAP_W)
    assert pill.wave.horizontal and pill.wave.frame().size == (wave.WAVE_H, wave.WAVE_W)
    pill._set_dictation("locked")
    c = pill.dictate_capsule.frame()
    assert c.size == (FREE_H, CAP_W) and c.origin.x == 0
    cancel, finish = box(pill.cancel_disc, pill.dictate_capsule), box(pill.finish_disc, pill.dictate_capsule)
    assert cancel[0] == pytest.approx(6.0) and cancel[2] == 22.0                 # cancel at the left
    assert FREE_H - (finish[0] + finish[2]) == pytest.approx(5.0)               # finish at the right
    for strip in (pill.cancel_bg, pill.finish_bg):
        assert strip.frame().size == (BUTTON_HIT, CAP_W)
    pill._set_dictation("working")
    assert pill.dictate_capsule.frame().size == (COMPACT_H, CAP_W)
    pill._set_dictation("idle")
    assert pill.form == "rest" and pill.rest.edge == "bottom"


@appkit
def test_a_reading_docked_at_the_bottom(pill):
    pill.layout = {"dock": "bottom", "x": 0.0, "y": 0.5}
    pill._update(Status(state="playing", session=1, total=3, index=1))
    x, y, w, h = frame(pill)
    assert pill.form == "sliver" and pill.dock == "bottom"
    assert (w, h) == (SLIVER_H, SLIVER_W) and y == 25
    assert pill.panel.contentView() is pill._kits[True]["sliver"] and pill.sliver.edge == "bottom"
    cx = x + w / 2
    pill.mouseEntered_(None)
    x, y, w, h = frame(pill)
    assert pill.form == "pill" and (w, h) == (pill_len(True), DOCK_W)
    assert y == 25 + DOCK_GAP and x + w / 2 == pytest.approx(cx)
    # left to right: close, expand, previous, play, next, progress
    stop = next(b for b in pill.buttons["stop"] if b.window() is pill.panel)
    order = [box(stop.superview(), pill.pill)[0], box(pill.chevron.superview(), pill.pill)[0]]
    for name in ("back", "toggle", "forward"):
        btn = next(b for b in pill.buttons[name] if b.window() is pill.panel)
        order.append(box(btn.superview(), pill.pill)[0])
    order.append(box(pill.dock_scrub, pill.pill)[0])
    assert order == sorted(order)


@appkit
def test_dragging_the_card_onto_the_bottom_docks_it(pill):
    pill._update(Status(state="playing", session=1, total=3, index=1))
    pill.panel.setFrameOrigin_((500, 40))
    pill.drag_began()
    pill.drag_moved(600, 80)
    assert pill.zones == ["flat"]  # the hint lies flat along the bottom edge
    _zx, zy, zw, zh = frame(pill.flat_zone)
    assert (zw, zh) == (CAPSULE_H + 8, CAPSULE_W + 8) and zy == 25 + CAPSULE_INSET - 4
    pill.drag_ended(600, 80)
    assert pill.dock == "bottom" and pill.form == "sliver"
    assert pill.fired[-1][:2] == ("layout", "bottom")


@appkit
def test_the_idle_pill_moves_between_all_three_docks(pill):
    pill.show_idle()
    for origin, edge in (((700, 30), "bottom"), ((15, 500), "left"), ((1420, 500), "right"), ((700, 26), "bottom")):
        pill.panel.setFrameOrigin_(origin)
        pill.drag_began()
        pill.drag_ended(*origin)
        assert pill.form == "rest" and pill._edge == edge and pill.fired[-1][:2] == ("dictation_layout", edge)
        assert pill.panel.contentView() is pill._kits[edge == "bottom"]["rest"]


@appkit
def test_one_form_at_a_time_across_orientations(pill):
    pill.dictation_layout = {"dock": "bottom", "y": -1.0}
    pill.show_idle()
    assert pill.panel.contentView() is pill._kits[True]["rest"]
    pill.layout = {"dock": "left", "x": 0.0, "y": 0.5}
    pill._update(Status(state="playing", session=1, total=3, index=1))
    assert pill.panel.contentView() is pill._kits[False]["sliver"]
    pill._update(Status())
    assert pill.form == "rest" and pill._edge == "left"  # the docked player's edge is shared


@appkit
def test_toast_floor_only_on_the_bottom(pill):
    pill.show_idle()  # right edge
    assert pill.toast_floor() is None
    pill.dictation_layout = {"dock": "bottom", "y": -1.0}
    pill.show_idle()
    _, y, _, h = frame(pill)
    assert pill.toast_floor() == y + h + 12


# -- the toast on AppKit ----------------------------------------------------------------------


@pytest.fixture
def toast(nsapp, monkeypatch):
    from lyssna.ui import toast as toast_mod

    vf = AppKit.NSMakeRect(*VF)
    monkeypatch.setattr(toast_mod, "visible_frame", lambda screen=None: vf)
    monkeypatch.setattr(toast_mod, "later", lambda delay, fn, *args: None)
    t = toast_mod.Toast.alloc().init()
    t.order_front = lambda: None
    return t


@appkit
def test_toast_sits_at_the_bottom_centre(toast):
    toast.present(Notice("Copied. Press ⌘V to paste"))
    f = toast.panel.frame()
    assert f.size.height == TOAST_H and f.origin.y == 25 + BOTTOM
    assert f.origin.x + f.size.width / 2 == pytest.approx(720, abs=0.5)
    assert toast.action_bg.isHidden()


@appkit
def test_toast_action_runs_and_dismisses(toast):
    ran = []
    toast.present(Notice("Transcript cancelled", "Undo", lambda: ran.append(1)))
    assert not toast.action_bg.isHidden() and toast.action_btn.title() == "Undo"
    f, b = toast.panel.frame(), toast.action_bg.frame()
    assert f.size.width - (b.origin.x + b.size.width) == pytest.approx(ACTION_RIGHT)
    toast.act_(None)
    assert ran == [1] and toast._key == ""


@appkit
def test_toast_with_the_same_key_updates_in_place(toast):
    faded = []
    toast._fade = lambda alpha, seconds, done=None: faded.append(alpha)
    toast.visible = lambda: True  # as if on screen
    toast.present(Notice("Getting the speech model ready, 10%", key="models"))
    toast.present(Notice("Getting the speech model ready, 11%", key="models"))
    assert toast.text.stringValue() == "Getting the speech model ready, 11%"
    assert faded == [1.0]  # faded in once, then updated in place
    toast.present(Notice("The speech model is ready. Dictate again."))
    assert faded == [1.0, 1.0]  # a different notice replaces it with a fresh fade


@appkit
def test_toast_sits_above_a_bottom_pill(toast):
    toast.present(Notice("Transcript cancelled"), floor=25 + 70)
    assert toast.panel.frame().origin.y == 95
    toast.present(Notice("Transcript cancelled"), floor=10)  # below its own spot: no change
    assert toast.panel.frame().origin.y == 25 + BOTTOM


@appkit
def test_long_toast_text_is_cut_to_the_screen(toast):
    toast.present(Notice("word " * 200))
    assert toast.panel.frame().size.width <= 640
