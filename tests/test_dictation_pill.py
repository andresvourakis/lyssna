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
    DISC,
    DOCK_GAP,
    DOCK_GAP_V,
    DOCK_H,
    FREE_H,
    MENU_H,
    MENU_MIC_H,
    MENU_MIC_W,
    MENU_W,
    dictation_spot,
    edge_frame,
    pick_form,
    spot_fraction,
)
from lyssna.ui.toast import ACTION_PAD, ACTION_RIGHT, BOTTOM, PAD_L, TOAST_H, tip_origin, toast_width
from lyssna.ui.widgets import CAPSULE_INSET, SLIVER_H, SLIVER_W

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


def test_dictation_spot_follows_the_docked_player_then_the_idle_pill_then_the_right_edge():
    assert dictation_spot({"dock": "left", "y": 0.3}, {"dock": "right", "y": 0.9}) == ("left", 0.3)
    assert dictation_spot({"dock": "", "y": 0.5}, {"dock": "left", "y": 0.9}) == ("left", 0.9)
    assert dictation_spot({"dock": "", "y": 0.5}, {"dock": "", "y": -1.0}) == ("right", -1.0)


VF = (0.0, 25.0, 1440.0, 875.0)


def test_edge_frame_sits_on_the_edge_and_lines_up_with_the_dock():
    (x, y), (w, h) = edge_frame(VF, "right", -1.0, CAP_W, COMPACT_H)
    assert x == 1440 - CAPSULE_INSET - CAP_W and (w, h) == (CAP_W, COMPACT_H)
    assert y + h / 2 == pytest.approx(25 + 875 / 2)  # centred without a saved spot
    (x, _), _ = edge_frame(VF, "left", 0.5, CAP_W, COMPACT_H)
    assert x == CAPSULE_INSET
    (x, _), _ = edge_frame(VF, "right", 0.5, SLIVER_W, SLIVER_H, inset=0.0)
    assert x == 1440 - SLIVER_W  # the resting sliver's window is flush with the edge
    # the same fraction puts its centre where the docked player's centre goes
    (_, y), (_, h) = edge_frame(VF, "left", 0.25, CAP_W, FREE_H)
    assert y + h / 2 == pytest.approx(25 + 0.25 * (875 - DOCK_H) + DOCK_H / 2)


def test_edge_frame_stays_on_screen():
    (_, y), (_, h) = edge_frame(VF, "right", 1.0, CAP_W, FREE_H)
    assert y + h <= 25 + 875
    (_, y), _ = edge_frame((0, 0, 800, 600), "right", 0.0, CAP_W, FREE_H)
    assert y >= 0


def test_spot_fraction_inverts_edge_frame():
    for fy in (0.0, 0.3, 1.0):
        (_, y), (_, h) = edge_frame(VF, "left", fy, SLIVER_W, SLIVER_H, inset=0.0)
        assert spot_fraction(VF, y + h / 2) == pytest.approx(fy)
    assert spot_fraction(VF, -500) == 0.0 and spot_fraction(VF, 5000) == 1.0


def test_sizes():
    assert (CAP_W, COMPACT_H, FREE_H) == (34.0, 60.0, 118.0)
    assert (MENU_MIC_W, MENU_MIC_H, MENU_H) == (40.0, 56.0, 56.0 + 2 * (DOCK_GAP_V + DISC))
    assert BUTTON_HIT >= 28.0


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
    p.visible = lambda: bool(p.shown)  # as if each order_front had put it on screen
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
def test_idle_shows_the_resting_sliver_on_the_right_edge(pill):
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
    assert r.origin.y == MENU_MIC_H + DOCK_GAP_V and r.size.width == DISC
    assert hi.origin.y == r.origin.y + DISC + DOCK_GAP_V and hi.size.width == DISC
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
    assert (name, edge) == ("dictation_layout", "left") and fy == pytest.approx(spot_fraction(VF, y + h / 2))
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
def test_long_toast_text_is_cut_to_the_screen(toast):
    toast.present(Notice("word " * 200))
    assert toast.panel.frame().size.width <= 640
