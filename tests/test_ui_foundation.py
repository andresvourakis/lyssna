"""The Qt interface's foundation (lyssna/ui/): coordinate conversions, colours, the main-thread
hop, the floating panel's flags, geometry and drag rules, the icons, the bars, the meter and the
preview screenshots. Runs on the offscreen platform, so it needs no display."""

import math
import sys
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, Qt, QThread
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from lyssna.ui.layout.panel import DRAG_SLOP
from lyssna.ui.layout.tokens import CREAM, FILL_SOFT_ALPHA, HAIRLINE_ALPHA, INK, INK_TEXT
from lyssna.ui.layout.wave import BAR_H


@pytest.fixture
def scheme(qapp):
    from lyssna.ui import theme

    yield theme.set_scheme
    theme.set_scheme(None)


def pump(seconds: float = 0.0, until=None) -> None:
    """Run the Qt event loop for `seconds`, or until `until()` holds (up to two seconds)."""
    end = time.monotonic() + (seconds if until is None else 2.0)
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


def mouse(widget: QWidget, kind: QEvent.Type, gx: float, gy: float, buttons=Qt.MouseButton.LeftButton) -> None:
    """A mouse event at the global point (gx, gy), delivered to `widget` through the application
    (so application event filters see it, as with a real click)."""
    local = widget.mapFromGlobal(QPointF(gx, gy))
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    ev = QMouseEvent(kind, local, QPointF(gx, gy), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


def centre(widget: QWidget) -> tuple[float, float]:
    g = widget.mapToGlobal(QPointF(widget.width() / 2, widget.height() / 2))
    return g.x(), g.y()


# -- coords ------------------------------------------------------------------------------------


def test_mac_rect_round_trip_and_flip():
    from lyssna.ui import coords

    H = 900.0
    qt = (100.0, 50.0, 200.0, 30.0)
    mac = coords.to_mac_rect(qt, H)
    assert mac == (100.0, 900.0 - 80.0, 200.0, 30.0)
    assert coords.from_mac_rect(mac, H) == qt
    assert coords.from_mac_rect(((100.0, 820.0), (200.0, 30.0)), H) == qt  # a Mac frame works too
    assert coords.to_mac_rect(QRectF(*qt), H) == mac
    assert coords.as_frame(qt) == ((100.0, 50.0), (200.0, 30.0))
    assert coords.to_mac_point(10, 100, H) == (10, 800)
    assert coords.from_mac_point(*coords.to_mac_point(10, 100, H), H) == (10, 100)


def test_flip_rect_inside_a_parent():
    from lyssna.ui import coords

    assert coords.flip_rect((4, 10, 20, 6), 100) == (4.0, 84.0, 20.0, 6.0)
    assert coords.flip_rect(coords.flip_rect((4, 10, 20, 6), 100), 100) == (4.0, 10.0, 20.0, 6.0)


def test_clamp_to_keeps_the_rect_inside():
    from lyssna.ui import coords

    frame = (0, 40, 800, 560)
    assert coords.clamp_to((780, 590, 100, 50), frame) == (700.0, 550.0, 100.0, 50.0)
    assert coords.clamp_to((-20, 0, 100, 50), frame) == (0.0, 40.0, 100.0, 50.0)
    assert coords.clamp_to((10, 100, 100, 50), frame) == (10.0, 100.0, 100.0, 50.0)


def test_physical_to_logical_with_mixed_scales():
    from lyssna.ui import coords

    # A 1920 x 1080 primary at 150 % (1280 x 720 logical) and a 1920 x 1080 monitor at 100 % to
    # its right. Qt keeps each screen's physical origin, so the second starts at x 1920 in both.
    screens = [((0.0, 0.0, 1280.0, 720.0), 1.5), ((1920.0, 0.0, 1920.0, 1080.0), 1.0)]
    assert coords.map_physical_to_logical(960, 540, screens) == (640.0, 360.0)
    assert coords.map_physical_to_logical(1919, 0, screens) == pytest.approx((1919 / 1.5, 0.0))
    assert coords.map_physical_to_logical(2000, 100, screens) == (2000.0, 100.0)
    for x, y in ((960, 540), (2000, 100), (3000, 1000)):
        lx, ly = coords.map_physical_to_logical(x, y, screens)
        assert coords.map_logical_to_physical(lx, ly, screens) == pytest.approx((x, y))
    # A monitor to the left of the primary, at 200 %: negative physical coordinates.
    screens = [((0.0, 0.0, 1920.0, 1080.0), 1.0), ((-3840.0, 0.0, 1920.0, 1080.0), 2.0)]
    assert coords.map_physical_to_logical(-1920, 1000, screens) == (-3840 + 960.0, 500.0)
    assert coords.map_physical_rect_to_logical((960, 540, 300, 60), [((0.0, 0.0, 1280.0, 720.0), 1.5)]) == \
        (640.0, 360.0, 200.0, 40.0)


def test_physical_is_logical_off_windows(qapp):
    from lyssna.ui import coords

    if sys.platform == "win32":
        pytest.skip("Windows converts for real")
    assert coords.physical_to_logical(123, 456) == (123.0, 456.0)
    assert coords.physical_rect_to_logical((1, 2, 3, 4)) == (1.0, 2.0, 3.0, 4.0)


def test_screen_readers_answer(qapp):
    from lyssna.ui import coords

    vf = coords.visible_frame()
    assert vf[2] > 0 and vf[3] > 0
    assert coords.screen_at(-10000, -10000) is not None  # the nearest screen
    mac = coords.visible_frame_mac()
    assert coords.qt_rect_from_mac(mac) == vf


# -- theme -------------------------------------------------------------------------------------


def _rgb(c: QColor) -> tuple[int, int, int]:
    return (c.red(), c.green(), c.blue())


def test_colours_follow_the_scheme(scheme):
    from lyssna.ui import theme

    scheme("light")
    assert not theme.is_dark()
    assert _rgb(theme.surface()) == CREAM and _rgb(theme.content()) == INK_TEXT
    assert theme.surface(0.97).alphaF() == pytest.approx(0.97, abs=0.01)
    assert _rgb(theme.hairline()) == INK_TEXT and theme.hairline().alphaF() == pytest.approx(HAIRLINE_ALPHA, abs=0.01)
    scheme("dark")
    assert theme.is_dark()
    assert _rgb(theme.surface()) == INK and _rgb(theme.content()) == CREAM
    assert _rgb(theme.fill_soft()) == CREAM and theme.fill_soft().alphaF() == pytest.approx(FILL_SOFT_ALPHA, abs=0.01)
    assert theme.white_glow() == theme.white_glow()  # fixed colours do not change with the scheme


def test_scheme_change_is_announced(scheme):
    from lyssna.ui import theme

    scheme("light")
    heard = []
    unsubscribe = theme.on_scheme_change(lambda: heard.append(theme.is_dark()))
    scheme("dark")
    scheme("dark")  # no flip, no news
    scheme("light")
    unsubscribe()
    scheme("dark")
    assert heard == [True, False]


def test_fonts_use_logical_pixels(qapp):
    from lyssna.ui import theme

    f = theme.ui_font(13, "semibold")
    assert f.pixelSize() == 13 and f.weight() == f.Weight.DemiBold
    assert theme.digits_font(11).pixelSize() == 11
    assert theme.serif_font(20).pixelSize() == 20
    with pytest.raises(ValueError):
        theme.ui_font(13, "black")


def test_reduce_motion_reads_the_system_setting():
    from lyssna import host
    from lyssna.ui import theme

    assert theme.reduce_motion() is host.appearance.reduce_motion()  # the twin's answer; tests/test_ui_fonts.py fakes it


# -- mainthread --------------------------------------------------------------------------------


def test_on_main_from_a_worker_runs_on_the_gui_thread(qapp):
    from lyssna.ui.mainthread import on_main

    seen = []

    def record(tag):
        seen.append((tag, QThread.currentThread() == qapp.thread(), threading.current_thread() is threading.main_thread()))

    worker = threading.Thread(target=lambda: on_main(record, "worker"))
    worker.start()
    worker.join()
    pump(until=lambda: seen)
    assert seen == [("worker", True, True)]
    on_main(record, "inline")  # already on the GUI thread: runs at once
    assert seen[-1] == ("inline", True, True)


def test_later_runs_on_the_gui_thread_and_can_be_cancelled(qapp):
    from lyssna.ui.mainthread import later

    fired = []
    t = later(0.02, lambda: fired.append(threading.current_thread() is threading.main_thread()))
    assert isinstance(t, threading.Timer) and t.daemon
    pump(until=lambda: fired)
    assert fired == [True]
    cancelled = []
    t = later(0.05, lambda: cancelled.append(1))
    t.cancel()
    pump(0.15)
    assert cancelled == []


def test_cancel_pending_stops_every_waiting_timer(qapp):
    from lyssna.ui import mainthread

    fired = []
    mainthread.cancel_pending()  # whatever earlier tests left waiting
    kept = mainthread.later(0.05, lambda: fired.append("cancelled by hand"))
    kept.cancel()
    mainthread.later(0.05, lambda: fired.append("a"))
    mainthread.later(0.05, lambda: fired.append("b"))
    assert mainthread.cancel_pending() == 2  # the one cancelled by hand is not waiting any more
    pump(0.15)
    assert fired == [] and mainthread.cancel_pending() == 0


def test_a_call_after_the_dispatcher_is_gone_is_dropped_quietly(qapp, monkeypatch, caplog):
    """At shutdown a timer may fire after Qt deleted the dispatcher: nothing runs, nothing is raised."""
    import shiboken6

    from lyssna.ui import mainthread

    gone = mainthread._Dispatcher()
    shiboken6.delete(gone)
    monkeypatch.setattr(mainthread, "_dispatcher", gone)
    ran, errors = [], []
    monkeypatch.setattr(threading, "excepthook", lambda hook_args: errors.append(hook_args.exc_value))
    worker = threading.Thread(target=lambda: _call_catching(lambda: mainthread.on_main(ran.append, 1), errors))
    worker.start()
    worker.join()
    t = mainthread.later(0.01, ran.append, 2)
    t.join(1.0)
    assert ran == [] and errors == []


def _call_catching(fn, errors: list) -> None:
    try:
        fn()
    except Exception as e:
        errors.append(e)


# -- panel -------------------------------------------------------------------------------------


def _panel(**attrs):
    from lyssna.ui.panel import FloatingPanel

    cls = type("TestPanel", (FloatingPanel,), {"width": 120.0, "height": 40.0, **attrs})
    return cls()


def test_panel_never_takes_focus(qapp):
    fp = _panel()
    flags = fp.panel.windowFlags()
    for flag in (Qt.WindowType.WindowDoesNotAcceptFocus, Qt.WindowType.FramelessWindowHint,
                 Qt.WindowType.WindowStaysOnTopHint, Qt.WindowType.Tool, Qt.WindowType.NoDropShadowWindowHint):
        assert flags & flag, flag
    assert fp.panel.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert fp.panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert fp.panel.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_surface_rect_and_the_shadow_margin(qapp):
    from lyssna.ui.panel import MARGIN

    fp = _panel()
    assert fp.margin == MARGIN
    assert fp.surface_rect() == QRectF(MARGIN, MARGIN, 120, 40)
    fp.set_frame((300, 200, 150, 50))
    g = fp.panel.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == (300 - MARGIN, 200 - MARGIN, 150 + 2 * MARGIN, 50 + 2 * MARGIN)
    assert fp.frame() == (300.0, 200.0, 150.0, 50.0)
    assert fp.content.geometry().topLeft().x() == MARGIN and fp.content.width() == 150
    flat = _panel(shadow=False)
    assert flat.margin == 0 and flat.surface_rect() == QRectF(0, 0, 120, 40)
    fp.set_shadow(False)
    assert fp.frame() == (300.0, 200.0, 150.0, 50.0) and fp.panel.width() == 150


def test_show_at_clamps_to_the_visible_frame(qapp, monkeypatch):
    from lyssna.ui import coords

    monkeypatch.setattr(coords, "visible_frame", lambda screen=None: (0.0, 40.0, 800.0, 560.0))
    fp = _panel()
    fp.show_at(780, 590)
    assert fp.visible()
    assert fp.frame() == (680.0, 560.0, 120.0, 40.0)
    fp.show_at(-50, 0)
    assert fp.frame()[:2] == (0.0, 40.0)
    fp.hide()
    assert not fp.visible()


def test_contains_screen_point_is_the_surface_only(qapp):
    fp = _panel()
    fp.set_frame((100, 100, 120, 40))
    assert not fp.contains_screen_point(150, 120)  # hidden
    fp.order_front()
    assert fp.contains_screen_point(150, 120)
    assert fp.contains_screen_point(150, 120, physical=False)
    assert not fp.contains_screen_point(95, 120)  # in the shadow margin, not on the surface
    assert not fp.contains_screen_point(500, 500)
    fp.hide()


def test_click_through_and_alpha(qapp):
    fp = _panel()
    fp.set_click_through(True)
    assert fp.click_through() and fp.panel.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    fp.set_click_through(False)
    assert not fp.panel.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    fp.set_alpha(0.4)
    assert fp.alpha() == pytest.approx(0.4)
    done = []
    fp.order_front()
    fp.fade_to(1.0, 0.05, lambda: done.append(True))
    pump(until=lambda: done)
    assert done and fp.alpha() == pytest.approx(1.0)
    fp.hide()


class _Recorder:
    def __init__(self):
        self.events = []


def _drag_panel():
    from lyssna.ui.widgets import IconButton, ScrubBar

    rec = _Recorder()

    def hook(name):
        return lambda self, *args: rec.events.append((name, *args))

    fp = _panel(draggable=True, width=240.0, height=80.0, drag_began=hook("began"), drag_moved=hook("moved"),
                drag_ended=hook("ended"), clicked=hook("clicked"))
    btn = IconButton("play.fill", fp.content, diameter=30, clicked=lambda: rec.events.append(("button",)))
    btn.move(10, 10)
    scrub = ScrubBar(fp.content)
    scrub.setGeometry(100, 50, 120, 22)
    scrub.on_scrub = lambda f: rec.events.append(("scrub", round(f, 2)))
    fp.set_frame((200, 200, 240, 80))
    fp.order_front()
    return fp, btn, scrub, rec


def test_a_press_that_stays_put_clicks_the_button(qapp):
    fp, btn, _, rec = _drag_panel()
    x, y = centre(btn)
    mouse(btn, QEvent.Type.MouseButtonPress, x, y)
    assert btn.pressed  # the panel shows the press on the button it will click
    mouse(btn, QEvent.Type.MouseMove, x + DRAG_SLOP - 1, y + 1)
    mouse(btn, QEvent.Type.MouseButtonRelease, x + DRAG_SLOP - 1, y + 1, Qt.MouseButton.NoButton)
    assert rec.events == [("button",)]
    assert not btn.pressed
    assert fp.frame()[:2] == (200.0, 200.0)
    fp.hide()


def test_a_press_off_any_button_is_a_panel_click(qapp):
    fp, _, _, rec = _drag_panel()
    x, y = fp.content.mapToGlobal(QPointF(60, 60)).x(), fp.content.mapToGlobal(QPointF(60, 60)).y()
    mouse(fp.content, QEvent.Type.MouseButtonPress, x, y)
    mouse(fp.content, QEvent.Type.MouseButtonRelease, x, y, Qt.MouseButton.NoButton)
    assert rec.events == [("clicked",)]
    fp.hide()


def test_a_press_that_travels_drags_the_panel(qapp):
    fp, btn, _, rec = _drag_panel()
    x, y = centre(btn)
    mouse(btn, QEvent.Type.MouseButtonPress, x, y)
    mouse(btn, QEvent.Type.MouseMove, x + 20, y + 10)
    mouse(btn, QEvent.Type.MouseMove, x + 30, y + 15)
    mouse(btn, QEvent.Type.MouseButtonRelease, x + 30, y + 15, Qt.MouseButton.NoButton)
    assert rec.events == [("began",), ("moved", x + 20, y + 10), ("moved", x + 30, y + 15), ("ended", x + 30, y + 15)]
    assert fp.frame()[:2] == (230.0, 215.0)
    assert not btn.pressed
    fp.hide()


def test_a_widget_that_consumes_the_mouse_is_not_a_drag_handle(qapp):
    fp, _, scrub, rec = _drag_panel()
    x, y = centre(scrub)
    mouse(scrub, QEvent.Type.MouseButtonPress, x, y)
    mouse(scrub, QEvent.Type.MouseMove, x + 30, y)
    mouse(scrub, QEvent.Type.MouseButtonRelease, x + 30, y, Qt.MouseButton.NoButton)
    assert ("began",) not in rec.events and ("clicked",) not in rec.events
    assert rec.events[0] == ("scrub", 0.5) and rec.events[-1] == ("scrub", 0.75)
    assert fp.frame()[:2] == (200.0, 200.0)
    fp.hide()


def test_without_draggable_buttons_handle_their_own_clicks(qapp):
    fp, btn, _, rec = _drag_panel()
    fp.draggable = False
    x, y = centre(btn)
    mouse(btn, QEvent.Type.MouseButtonPress, x, y)
    mouse(btn, QEvent.Type.MouseMove, x + 20, y)
    mouse(btn, QEvent.Type.MouseButtonRelease, x - 2, y, Qt.MouseButton.NoButton)
    assert rec.events == [("button",)]
    assert fp.frame()[:2] == (200.0, 200.0)
    fp.hide()


# -- icons -------------------------------------------------------------------------------------


def test_labels_are_framed_as_appkit_labels(qapp):
    """A label sized to its text is the advance plus 2 each side, with its text drawn 2 in; a
    wrapping label breaks its lines 2 inside each side of its width."""
    from PySide6.QtGui import QFontMetricsF

    from lyssna.ui import theme
    from lyssna.ui.window import TEXT_INSET, Label, text_frame_width

    label = Label("Select text in any app and press", size=13)
    advance = theme.text_width(label.value(), label.font())  # AppKit's own measure on a Mac
    assert label.fit()[0] == text_frame_width(advance) == math.ceil(advance + 4)
    assert label.contentsRect().left() == TEXT_INSET
    words = "Lyssna reads text aloud and types"
    wrap = Label(words, size=13, wrap=True)
    one_line = QFontMetricsF(theme.ui_font(13)).horizontalAdvance(words)
    _, h1 = wrap.fit(one_line + 4.5)
    _, h2 = wrap.fit(one_line + 3)
    assert h2 > h1  # less than the insets' room: the last word wraps, as AppKit's does


def test_text_buttons_are_framed_as_borderless_appkit_buttons(qapp):
    from PySide6.QtGui import QFontMetrics

    from lyssna.ui import theme
    from lyssna.ui.window import TextButton, text_frame_width

    plain = TextButton("Clear History…", size=12, weight="regular")
    advance = theme.text_width(plain.text(), plain.font())
    assert plain.sizeHint().width() == text_frame_width(advance)
    assert plain.sizeHint().height() == QFontMetrics(plain.font()).height()
    heart = TextButton("Support it", size=12, link=True, symbol="heart.fill")
    advance = theme.text_width(heart.text(), heart.font())
    assert heart.title_x() == 2 + 12 + 2  # the 10 pt symbol's image is 12 wide
    assert heart.sizeHint().width() == 16 + math.ceil(advance) + 2


def test_every_icon_has_a_path(qapp):
    from lyssna.ui import icons

    assert len(icons.NAMES) >= 23
    for name in icons.NAMES:
        for weight in icons.WEIGHTS:
            path = icons.icon_path(name, weight)
            assert not path.isEmpty(), (name, weight)
            b = path.boundingRect()
            # The unit box is the point size; like SF Symbols, a few glyphs overhang it (the
            # pages, the mic, the clock's arrow), never by more than an eighth.
            assert -0.125 <= b.left() and b.right() <= 1.125 and -0.125 <= b.top() and b.bottom() <= 1.125, (name, b)
    assert icons.icon_path("no.such.symbol").isEmpty()
    pm = icons.icon_pixmap("play.fill", 13, QColor("black"), dpr=2.0)
    assert pm.width() == 26
    assert len({pm.toImage().pixel(x, y) for x in range(26) for y in range(26)}) > 1


def test_optical_nudges_move_only_their_own_size_and_weight(qapp):
    """The OPTICAL table shifts an icon's ink where the Mac's sits off centre, for that symbol at
    that size and weight only, and by less than a pixel."""
    import numpy as np

    from lyssna.ui import icons

    def ink_centre(name: str, size: float, weight: str) -> tuple[float, float]:
        img = QImage(96, 96, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.scale(4, 4)
        icons.draw_icon(p, name, icons.icon_rect(12, 12, size), QColor("black"), weight)
        p.end()
        a = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(96, 96, 4)[..., 3].astype(float)
        ys, xs = np.mgrid[0:96, 0:96]
        return (xs * a).sum() / a.sum() / 4, (ys * a).sum() / a.sum() / 4

    for (name, size, weight), (dx, dy) in list(icons.OPTICAL.items()):
        assert name in icons.NAMES and weight in icons.WEIGHTS and abs(dx) < 1 and abs(dy) < 1
        cx, cy = ink_centre(name, size, weight)
        saved = icons.OPTICAL.pop((name, size, weight))
        try:
            bx, by = ink_centre(name, size, weight)
        finally:
            icons.OPTICAL[(name, size, weight)] = saved
        assert (cx - bx, cy - by) == pytest.approx((dx, dy), abs=0.01)


def test_a_boxed_icon_pixmap_keeps_the_symbol_size(qapp):
    from lyssna.ui import icons

    pm = icons.icon_pixmap("lock", 13, QColor("black"), "regular", dpr=2.0, box=16)
    assert pm.width() == 32 and pm.deviceIndependentSize().width() == 16
    img = pm.toImage()
    rows = [y for y in range(32) if any(img.pixelColor(x, y).alpha() > 128 for x in range(32))]
    assert rows[-1] - rows[0] + 1 <= 26  # a 13 point lock (about 12 tall), not stretched to 16


# -- bars --------------------------------------------------------------------------------------


def test_progress_bar_fill_is_never_shorter_than_its_height(qapp):
    from lyssna.ui.widgets import ProgressBar

    bar = ProgressBar()
    bar.resize(200, round(BAR_H))
    bar.set_fraction(0.0)
    assert bar.fill_width() == BAR_H
    bar.set_fraction(0.5)
    assert bar.fill_width() == 100
    bar.set_fraction(3.0)
    assert bar.fraction == 1.0 and bar.fill_width() == 200


def test_pill_button_is_as_wide_as_the_macs(qapp):
    """NSButton.sizeToFit: title advance plus 2 pt each side, rounded up; then PILL_PAD each side."""
    from lyssna.ui import theme
    from lyssna.ui.layout.setup import PILL_PAD
    from lyssna.ui.widgets import PillButton

    for title, primary in (("Download", False), ("Done", True), ("Open Settings…", False)):
        b = PillButton(title, primary=primary)
        advance = theme.text_width(title, b.font())
        assert b.width() == math.ceil(advance + 4.0 - 1e-6) + 2 * PILL_PAD


def test_playback_and_drags_glide_the_fill_and_knob(qapp, monkeypatch):
    """The Mac's fill and knob are layers with implicit 0.25 s actions: playback glides them, and
    so does a drag (the legacy bar's drag leaves 0.25 s position and bounds animations on both).
    The knob's fade is a transition: interrupted, it starts afresh rather than from the shown alpha.
    The glides are stepped by hand, never timed against the clock, and motion is pinned on (a CI
    machine may have animations switched off)."""
    from lyssna.ui import theme
    from lyssna.ui.widgets import ScrubBar

    monkeypatch.setattr(theme, "reduce_motion", lambda: False)
    s = ScrubBar()
    s.resize(200, 22)
    s.show()
    s.grab()  # painted once (grab paints at once, whatever the platform's exposure): from now on changes glide

    def glides():
        return (s.bar._fill, s._knob_pos, s._knob_alpha)

    def at(fraction: float) -> None:
        """Step every running glide to `fraction` of its duration (1.0 ends it)."""
        for g in glides():
            if g._anim is not None:
                g._anim.setCurrentTime(round(g._anim.duration() * fraction))

    s.set_hover(False)
    at(1.0)
    s.set_fraction(0.5)
    assert s.bar._fill.gliding() and s.bar.fill_width() == BAR_H  # still where it was shown
    at(0.5)
    assert BAR_H < s.bar.fill_width() < 100
    at(1.0)
    assert not s.bar._fill.gliding() and s.bar.fill_width() == 100
    s.set_hover(True)  # the knob fades in and the fill's height follows the thicker track
    assert s.knob.isVisible() and s._knob_alpha.gliding() and s.bar._fill.gliding()
    at(1.0)
    origin = s.mapToGlobal(QPointF(0, 11))
    mouse(s, QEvent.Type.MouseButtonPress, origin.x() + 150, origin.y())
    assert s.bar.fraction == 0.75 and s.bar._fill.gliding() and s._knob_pos.gliding()
    assert s.bar.fill_width() == 100  # it starts from where it was, 0.25 s behind the cursor
    at(0.5)
    assert 100 < s.bar.fill_width() < 150
    shown = s.bar.fill_width()
    mouse(s, QEvent.Type.MouseMove, origin.x() + 180, origin.y())
    assert s.bar._fill.gliding() and s.bar.fill_width() == pytest.approx(shown)  # each move glides afresh from the shown spot
    mouse(s, QEvent.Type.MouseButtonRelease, origin.x() + 180, origin.y(), Qt.MouseButton.NoButton)
    at(1.0)
    assert s.bar.fill_width() == 180
    s.set_hover(False)  # the knob fades out ...
    at(0.5)
    assert 0.0 < s._knob_alpha.value()[0] < 1.0
    s.set_hover(True)  # ... and, interrupted, fades in again from nothing
    assert s._knob_alpha.gliding() and s._knob_alpha.value() == (0.0,)
    at(1.0)
    s.hide()


def test_scrub_bar_jumps_when_motion_is_reduced(qapp, monkeypatch):
    from lyssna.ui import theme
    from lyssna.ui.widgets import ScrubBar

    monkeypatch.setattr(theme, "reduce_motion", lambda: True)
    s = ScrubBar()
    s.resize(200, 22)
    s.show()
    s.grab()
    s.set_hover(True)
    s.set_fraction(0.5)
    origin = s.mapToGlobal(QPointF(0, 11))
    mouse(s, QEvent.Type.MouseButtonPress, origin.x() + 150, origin.y())
    assert not any(g.gliding() for g in (s.bar._fill, s._knob_pos, s._knob_alpha)) and s.bar.fill_width() == 150
    mouse(s, QEvent.Type.MouseButtonRelease, origin.x() + 150, origin.y(), Qt.MouseButton.NoButton)
    s.hide()


def test_scrub_bar_scrubs_then_seeks(qapp):
    from lyssna.ui.widgets import ScrubBar

    s = ScrubBar()
    s.resize(200, 22)
    s.show()
    scrubs, seeks = [], []
    s.on_scrub, s.on_seek = scrubs.append, seeks.append
    origin = s.mapToGlobal(QPointF(0, 11))
    mouse(s, QEvent.Type.MouseButtonPress, origin.x() + 50, origin.y())
    assert s.scrubbing and s.bar_height() == BAR_H * 1.5 and s.knob.isVisible()
    mouse(s, QEvent.Type.MouseMove, origin.x() + 100, origin.y())
    mouse(s, QEvent.Type.MouseButtonRelease, origin.x() + 150, origin.y(), Qt.MouseButton.NoButton)
    assert scrubs == [0.25, 0.5]
    assert seeks == [0.75]
    assert not s.scrubbing and s.fraction == 0.75
    s.set_fraction(0.1)
    assert s.fraction == 0.1
    s.hide()


# -- wave --------------------------------------------------------------------------------------


def test_wave_ticks_and_runs(qapp):
    from lyssna.ui.wave import WaveView, breathe

    w = WaveView()
    w.level_fn = lambda: 1.0
    before = w.path()
    w._last = 0.0
    w.tick(0.05)
    assert w.path() != before
    assert not w.running()
    w.start()
    assert w.running()
    w.stop()
    assert not w.running()
    w.set_mode("working")
    w.tick(10.0)
    assert 0.45 <= w.opacity <= 1.0
    assert breathe(0.0) == pytest.approx(0.45) and breathe(0.7) == pytest.approx(1.0)
    # A quarter of the way in, on the Mac's ease-in-out bezier (0.42, 0, 0.58, 1) and not on a
    # cosine (which would give 0.53); the way back mirrors it.
    assert breathe(0.175) == pytest.approx(0.521, abs=0.001)
    assert breathe(0.875) == pytest.approx(1.0 - (0.521 - 0.45), abs=0.001)


# -- preview -----------------------------------------------------------------------------------


def test_preview_shots_cover_every_preview_in_both_schemes(qapp, tmp_path):
    from lyssna.ui import preview

    names = list(preview.discover())
    for required in ("panel", "icons", "buttons", "bars", "mark", "wave"):
        assert required in names
    files = preview.shots(tmp_path)
    assert {f.name for f in files} == {f"{n}-{s}.png" for n in names for s in ("light", "dark")}
    backdrop = {preview.CHECK_A.rgba(), preview.CHECK_B.rgba()}
    for f in files:
        img = QImage(str(f))
        assert not img.isNull(), f
        colours = {img.pixel(x, y) for x in range(0, img.width(), 3) for y in range(0, img.height(), 3)} - backdrop
        assert len(colours) > 1, f"{f.name} is blank"
