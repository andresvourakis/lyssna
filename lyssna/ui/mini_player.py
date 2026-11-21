"""The floating player for Windows, the twin of legacy/ui/mini_player.py (read that file's docstring for
the whole design; this one follows it rule for rule).

One window, one form in it at a time, every switch through _show_form:

    card     the transport card (400 x 156): close, tuck, scrub bar, times, voice, transport, speed
    sliver   docked on the left, right or bottom edge: the slim progress capsule (sliver.py)
    pill     the docked sliver hovered: close, expand, previous, play, next and a little scrub bar,
             upright on a side edge and lying flat along the bottom
    rest     idle: the logo's squiggle on its edge, rippling now and then (may_wave); with
             auto-hide it slides out of sight until the cursor comes near
    menu     the squiggle hovered: Dictate, Read selection and History
    dictate  the dictation capsule on a screen edge with the wave meter; hands-free it grows a
             cancel and a finish button; while the speech is turned into text the wave breathes

The card drags freely and never docks by dragging; its tuck button docks it on the nearest edge.
A docked sliver or pill dragged near an edge docks there, and dropped away from every edge opens
the card at that spot. The idle squiggle snaps to the nearest edge. Places are saved through the
`layout` and `dictation_layout` actions as the same fractions the Mac saves, so a config file means
the same spot on either system.

Dictation wins, as on the Mac: when one starts during a reading its form goes away, and when it
ends the reading form comes back where it was, still paused (the app pauses it). The dictation
form sits on the edge pick_form and dictation_spot name: the docked player's, else the idle
form's own saved spot, else the bottom centre.

Coordinates: the rules in layout/pill.py work in Mac-style rectangles (y up from the bottom of the
primary screen). So does this file, almost line for line with the Mac's: frames go through
FloatingPanel.frame_mac and set_frame_mac, screens through the _vf, _screen_at and
_screen_under_mouse helpers below, and the drag hooks turn Qt's cursor point into a Mac-style one.
`tip.show_beside` gets a Mac-style anchor rect, and toast_floor returns a Mac-style y, exactly as
on the Mac.

Motion: the hover growth scales a picture of the pill or the menu inside a window already sized
for the open form (the Mac scales a layer the same way), the auto-hide slide moves the squiggle
inside a window that stays put and clips it at the screen edge, the dictation capsule grows inside a
window already FREE_H long, and nothing resizes a window per frame. Window frames never animate."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
import weakref
from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QTransform
from PySide6.QtWidgets import QMenu, QWidget

from .. import host
from ..player import SPEEDS, Status
from . import coords, theme
from .layout.pill import (
    AUTO_HIDE_POLL,
    BADGE,
    BUTTON_HIT,
    CANCEL_D,
    CANCEL_TOP,
    CAP_W,
    CLOSE,
    CLOSE_GAP,
    CLOSE_TOP,
    COMPACT_H,
    DICTATING,
    DISC,
    DOCK_BAR,
    DOCK_GAP,
    DOCK_GAP_V,
    DOCK_PLAY,
    DOCK_SKIP,
    DOCK_W,
    DOCKS,
    FINISH_BOTTOM,
    FINISH_D,
    FREE_H,
    GROW_SECS,
    HIDE_DELAY,  # noqa: F401  (re-exported for the tests)
    HOVER_OUT_DELAY,
    HOVER_POLL,
    HOVER_SECS,
    LINGER_SECS,
    MARGIN,
    MENU_DISC,
    MENU_MIC_H,
    MENU_MIC_W,
    MENU_W,
    PAD,
    PILL_H,
    PILL_W,
    PLAY,
    SCRUB_H,
    SKIP,
    SLIDE_SECS,
    TIP_DELAY,
    WAVE_EVERY,
    _axis,
    _fmt,
    along_centre,
    along_fraction,
    anchor_of,
    dictation_spot,
    docked_frame,
    edge_coord,
    edge_frame,
    is_flat,
    lay,
    may_wave,
    near_frame,
    nearest_edge,
    oriented,
    pick_form,
    pill_len,
    pill_main_len,
    snap_edge,
    tuck_due,
)
from .layout.wave import BAR_H, CAPSULE_H, CAPSULE_INSET, CAPSULE_W, SLIVER_H, SLIVER_W, WAVE_H, WAVE_W
from .mainthread import is_main_thread, on_main
from .panel import FloatingPanel
from .sliver import EASE_IN, EASE_OUT, Sliver
from .wave import WaveView
from .widgets import HoverView, IconButton, ScrubBar, rounded

log = logging.getLogger("lyssna.ui.player")

CHEVRONS = {"left": "chevron.right", "right": "chevron.left", "bottom": "chevron.up"}
ARROWS = {"left": "left", "right": "right", "bottom": "down"}  # arrow.<x>.to.line.compact
MENU_POP = 0.5  # a menu shorter than the sliver grows from half its size instead
MENU_ITEMS = (("dictate", MENU_MIC_H, MENU_MIC_W), ("read", MENU_DISC, MENU_DISC), ("history", MENU_DISC, MENU_DISC))


# -- screens and the mouse (replaced in the tests) -------------------------------------------------


def _screen_at(x: float, y: float):
    """The screen holding a Mac-style point."""
    return coords.screen_at_mac(x, y)


def _screen_under_mouse():
    return coords.screen_under_mouse()


def _vf(screen=None) -> coords.Rect:
    """A screen's visible frame (taskbar excluded) as a Mac-style rect: what "the edge" means."""
    return coords.visible_frame_mac(screen)


def _left_button_down() -> bool:
    """Is the left mouse button held right now? Asked of the system (host.window), so a release
    Qt never saw (the stuck-drag case this guards against) still counts."""
    return host.window.left_button_down()


def _post(fn: Callable[[], None]) -> None:
    """Run fn on the GUI thread on a later turn of the event loop, never inline (the Mac posts to
    its main queue the same way): a dictation status may arrive from inside a key hook, which
    must return at once."""
    if is_main_thread():
        QTimer.singleShot(0, fn)
    else:
        on_main(fn)


def _place(widget: QWidget, rect, parent_h: float) -> None:
    """Set a widget's geometry from a Mac-style rect inside a parent `parent_h` tall (y up, as
    layout.pill.lay and the Mac builders give it)."""
    x, y, w, h = coords.flip_rect(rect, parent_h)
    widget.setGeometry(round(x), round(y), round(w), round(h))


def _centre(rect) -> tuple[float, float]:
    x, y, w, h = coords.as_rect(rect)
    return x + w / 2, y + h / 2


def _same_frame(a, b, tolerance: float = 1.0) -> bool:
    """Frames equal up to the rounding of window positions to whole pixels."""
    return all(abs(p - q) <= tolerance for p, q in zip(coords.as_rect(a), coords.as_rect(b), strict=True))


# -- the pieces of the card and the docked forms ---------------------------------------------------


class Text(QWidget):
    """A one-line label (the Mac's label()): 11 px medium, tabular digits, in a colour function."""

    def __init__(self, parent: QWidget, text: str = "", align=Qt.AlignmentFlag.AlignLeft, size: float = 11.0,
                 color=theme.content_dim):
        super().__init__(parent)
        self._text = text
        self.align = align
        self.font_ = theme.digits_font(size, "medium")
        self.color = color
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(self.font_)
        p.setPen(self.color())
        p.drawText(QRectF(self.rect()), int(self.align | Qt.AlignmentFlag.AlignVCenter), self._text)
        p.end()


class Surface(QWidget):
    """A rounded brand surface with a hairline (the Mac's make_surface): the docked pill's capsule."""

    def __init__(self, parent: QWidget, radius: float):
        super().__init__(parent)
        self.radius = radius

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        p.fillPath(rounded(r, self.radius), theme.surface(0.97))
        p.setPen(theme.hairline())
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rounded(r.adjusted(0.5, 0.5, -0.5, -0.5), self.radius - 0.5))
        p.end()


class CapsuleButton(IconButton):
    """An IconButton whose shape is its whole rect (the dictate capsule in the idle menu)."""

    def shape_rect(self) -> QRectF:
        return QRectF(self.rect())


class Badge(HoverView):
    """The voice badge: the voice's initial on a soft disc. A click opens the voice menu when the
    engine has voices to pick from; otherwise it does nothing and never lights up."""

    def __init__(self, parent: QWidget, clicked: Callable[[], None]):
        super().__init__(parent, fill=theme.fill_soft, radius=None, glow=None)
        self.letter = "S"
        self.pickable = False
        self.clicked = clicked

    def set_letter(self, letter: str) -> None:
        if letter != self.letter:
            self.letter = letter
            self.update()

    def set_pickable(self, on: bool) -> None:
        self.pickable = bool(on)
        self.glow = theme.clear_glow if on else None  # no hover light on a badge that does nothing
        if not on:
            self.set_hover(False, animate=False)
        self.update()

    def paint_content(self, p: QPainter) -> None:
        p.setFont(theme.ui_font(14, "semibold"))
        p.setPen(theme.content())
        p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self.letter)

    def perform_click(self) -> None:
        if self.pickable and self.clicked is not None:
            self.clicked()


class SpeedPill(HoverView):
    """The speed pill: "1.2×" in 12 px semibold tabular digits on a soft capsule; opens the menu."""

    def __init__(self, parent: QWidget, clicked: Callable[[], None]):
        super().__init__(parent, fill=theme.fill_soft, radius=None)
        self._text = "1.0×"
        self.clicked = clicked

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.update()

    def paint_content(self, p: QPainter) -> None:
        p.setFont(theme.digits_font(12, "semibold"))
        p.setPen(theme.content())
        p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self._text)

    def perform_click(self) -> None:
        if self.clicked is not None:
            self.clicked()


class Shapes(QWidget):
    """A transparent container of separate capsules and discs (the open pill, the idle menu).
    `shapes` lists the (rect, radius) of each, for the shadow."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.shapes: list[tuple[QWidget, float]] = []

    def shadow_path(self) -> QPainterPath:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        for w, radius in self.shapes:
            if not w.isHidden():
                path.addPath(rounded(QRectF(w.geometry()), radius))
        return path


class ScaleHost(QWidget):
    """Holds a form (the pill or the menu) and shows it scaled about the middle of the side that
    meets the screen edge: the Mac scales the form's layer, Qt cannot scale widgets, so while the
    scale is not 1 the host paints a picture of the form instead of the form itself. The window
    around it is already the open size, so nothing resizes while it grows or shrinks."""

    def __init__(self, parent: QWidget, child: QWidget):
        super().__init__(parent)
        child.setParent(self)
        child.move(0, 0)
        self.child = child
        self.edge = "left"
        self.scale = 1.0
        self.shadow_scale = 1.0  # the scale the shadow was last drawn for (settled, as on the Mac)
        self._picture: QPixmap | None = None

    def anchor(self) -> tuple[float, float]:
        w, h = float(self.width()), float(self.height())
        if is_flat(self.edge):
            return w / 2, h
        return (0.0 if self.edge == "left" else w), h / 2

    def _transform(self, s: float) -> QTransform:
        ax, ay = self.anchor()
        return QTransform().translate(ax, ay).scale(s, s).translate(-ax, -ay)

    def set_scale(self, s: float) -> None:
        self.scale = float(s)
        if s >= 0.999:
            self._picture = None
            self.child.show()
        else:
            if self._picture is None:
                self._picture = self.child.grab()
            self.child.hide()
        self.update()

    def paintEvent(self, ev) -> None:
        if self._picture is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setTransform(self._transform(self.scale))
        p.drawPixmap(QPointF(0, 0), self._picture)
        p.end()

    def shadow_path(self) -> QPainterPath:
        fn = getattr(self.child, "shadow_path", None)
        path = fn() if callable(fn) else QPainterPath()
        return self._transform(self.shadow_scale).map(path)


class FadeButton(IconButton):
    """The dictation capsule's cancel and finish: a disc inside a longer click strip that fades
    in and out with the hands-free capsule (the Mac animates the strip's alphaValue)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.alpha = 0.0

    def set_alpha(self, alpha: float) -> None:
        self.alpha = max(0.0, min(1.0, float(alpha)))
        self.update()

    def paintEvent(self, ev) -> None:
        if self.alpha <= 0.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(self.alpha)
        self.paint_shape(p)
        self.paint_content(p)
        p.end()


class DictateHost(QWidget):
    """The dictate form: a transparent strip FREE_H long that holds the capsule centred in it, so
    the capsule grows about its centre without the window changing size. The shadow follows the
    capsule's settled size (set by _settle_dictate), as the Mac invalidates its shadow only when
    the growth ends."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.capsule: QWidget | None = None
        self.shadow_rect = QRectF()

    def shadow_path(self) -> QPainterPath:
        if self.shadow_rect.isEmpty():
            return QPainterPath()
        return rounded(self.shadow_rect, CAP_W / 2)


class DropZone(FloatingPanel):
    """Faint content-coloured pill shown on a side edge while a drag hovers near it."""

    width = CAPSULE_W + 8
    height = CAPSULE_H + 8
    corner_radius = (CAPSULE_W + 8) / 2
    adaptive = True
    shadow = False

    def __init__(self) -> None:
        super().__init__()
        self.fill_color = lambda: theme.content(0.28)
        self.border_color = theme.content_dim
        self.set_click_through(True)


class FlatDropZone(DropZone):
    """The same hint lying flat along the bottom edge."""

    width = CAPSULE_H + 8
    height = CAPSULE_W + 8


# The widgets that exist once per orientation; _orient points these attributes at one set.
ORIENTED = ("pill", "pill_host", "dock_scrub", "chevron", "sliver", "rest", "menu", "menu_host", "menu_dictate",
            "menu_read", "menu_history", "menu_hovers", "dictate_host", "dictate_capsule", "wave", "cancel_btn",
            "cancel_disc", "finish_btn", "finish_disc")


class MiniPlayer(FloatingPanel):
    width = 400.0
    height = CLOSE_TOP + CLOSE + CLOSE_GAP + BAR_H + 8 + 14 + 16 + PLAY + PAD  # 156
    corner_radius = 22.0
    adaptive = True    # brand surface colour, follows light/dark mode
    draggable = True

    def __init__(self) -> None:
        self.actions: dict[str, Callable] = {}
        self.layout = {"dock": "", "x": -1.0, "y": -1.0}  # last resting place; see place()
        self.dock = ""          # "" (card) | "left" | "right" | "bottom" (docked: sliver at rest, pill on hover)
        self.form = "card"      # card | sliver | pill | rest | menu: which form is showing
        self._session = 0
        self._speed = 1.0
        self._status: Status | None = None
        self._scale = 1.0
        self._scale_anim: QVariantAnimation | None = None
        self._scale_host: ScaleHost | None = None
        self._anchor = (0.0, 0.0)  # (edge coord, centre along the edge) the docked forms hang off
        self._watch: QTimer | None = None  # hover poll while the pill or the menu is open
        self._outside_since: float | None = None
        self._last_chunk = (None, -1)  # (session, index) last shown, to bloom the sliver per sentence
        self._tip = ""
        self._badge_tip = ""
        self._pending_done = None  # completion token of the running scale animation
        self._moving = False       # a drag of the player is live (the Mac's _dragging)
        self.status_fn: Callable[[], Status] | None = None  # reader's live status, so a drain shows the truth
        self.voices_fn: Callable[[], tuple[list, str]] | None = None  # ([(id, label)], current id) for the badge menu
        self._latest: Status | None = None  # newest snapshot waiting for the GUI thread
        self._latest_lock = threading.Lock()
        self._drain_pending = False
        self._popup: QMenu | None = None

        # Idle and dictation
        self.idle_pill = True                         # ui.idle_pill: the resting squiggle when nothing else shows
        self.key_name = "fn"                          # the dictation key, as shown in the tips
        self.read_key = ""                            # the read-selection hotkey, as shown in its tip
        self.dictation_layout = {"dock": "", "y": -1.0}  # the idle pill's own spot (player not docked)
        self.dictation_state = "idle"                 # the session state the window shows
        self.hands_free = False
        self.tip = None                               # the tooltip pill (show_beside, hide), set by the wiring
        self._tip_token = None
        self._tip_up = False
        self._level = 0.0                             # latest microphone level; the wave's timer reads it
        self._dlock = threading.Lock()
        self._dqueued = "idle"                        # newest state handed to the GUI thread
        self._dpending = False
        self._reading_spot = None                     # where the reading form was when a dictation took over
        self._grow_anim: QVariantAnimation | None = None  # the hands-free growth or shrink
        self._edge = "bottom"                         # the edge the idle and dictation forms are on
        self._rest_anchor = (0.0, 0.0)                # (edge coord, centre along) of the resting squiggle
        self._menu_len = 0.0                          # the open menu's length along the edge
        self.auto_hide = True                         # ui.auto_hide: the resting squiggle hides until the cursor comes near
        self.rest_wave = True                         # ui.wave: the resting squiggle ripples now and then
        self._tucked = False                          # the squiggle is hidden, or sliding out, by auto-hide
        self._auto_watch: QTimer | None = None        # the cursor poll while idle with auto-hide on
        self._away_since: float | None = None         # when the cursor left the squiggle's surroundings
        self._linger_until = 0.0                      # the squiggle stays out at least until then
        self._slide_token = None                      # completion token of the running slide
        self._slide_anim: QVariantAnimation | None = None
        self._was_inside = False                      # the cursor was on the squiggle at the last auto-hide tick
        self._wave_timer: QTimer | None = None        # the countdown to the next ripple, while the rest form is up

        super().__init__()  # builds the card into self.content
        self.card = self.content
        self.zone = DropZone()
        self.flat_zone = FlatDropZone()
        self._kits = {flat: self._build_kit(flat) for flat in (False, True)}
        self._flat = None
        self._orient(False)
        ref = weakref.ref(self)

        def restyle() -> None:
            me = ref()
            if me is not None:
                me._restyle()
        self._unsubscribe_style = theme.on_scheme_change(restyle)

    # -- building --------------------------------------------------------------------

    def build(self, content: QWidget) -> None:
        W, H = self.width, self.height
        self.buttons: dict[str, list] = {}
        self.hovers: list[HoverView] = []  # so glows can be reset when a form goes away

        # Close button: its own strip in the top-right corner, with a faint disc so it reads as a
        # target (same tint as the speed pill).
        close = IconButton("xmark", content, diameter=CLOSE, fill=theme.fill_soft, icon_size=11.0,
                           icon_color=theme.content_dim, weight="bold", tooltip="Stop", clicked=self._stop)
        _place(close, ((W - PAD - CLOSE, H - CLOSE_TOP - CLOSE), (CLOSE, CLOSE)), H)
        self._button("stop", close)

        # Tuck button beside it: collapses the card to the nearest screen edge (the arrow points at
        # the side it will go to). Dragging the card never docks it, so this is the way.
        dock = IconButton("arrow.right.to.line.compact", content, diameter=CLOSE, fill=theme.fill_soft, icon_size=11.0,
                          icon_color=theme.content_dim, weight="bold", tooltip="Tuck into the side of the screen",
                          clicked=self._dock_side)
        _place(dock, ((W - PAD - CLOSE - 8 - CLOSE, H - CLOSE_TOP - CLOSE), (CLOSE, CLOSE)), H)
        self.dock_btn = dock

        # Progress bar with a generous hit strip around it
        bar_y = H - CLOSE_TOP - CLOSE - CLOSE_GAP - BAR_H
        self.scrub = ScrubBar(content)
        _place(self.scrub, ((PAD, bar_y - (SCRUB_H - BAR_H) / 2), (W - 2 * PAD, SCRUB_H)), H)
        self.scrub.on_scrub = self._scrub_preview
        self.scrub.on_seek = lambda f: self._fire("seek_to", f)
        self.scrub.setToolTip("Drag to scrub")

        # Times row: elapsed | chunk | remaining
        row_y = bar_y - 8 - 14
        self.elapsed = Text(content, "0:00", Qt.AlignmentFlag.AlignLeft)
        _place(self.elapsed, ((PAD, row_y), (80, 14)), H)
        self.progress = Text(content, "", Qt.AlignmentFlag.AlignHCenter)
        _place(self.progress, ((W / 2 - 60, row_y), (120, 14)), H)
        self.remaining = Text(content, "-0:00", Qt.AlignmentFlag.AlignRight)
        _place(self.remaining, ((W - PAD - 80, row_y), (80, 14)), H)

        # Transport row
        y = PAD
        cx = W / 2

        # Persona badge: the voice's initial. A click opens the voice menu (when the engine has one).
        self.badge = Badge(content, self._voice_menu)
        _place(self.badge, ((PAD, y + (PLAY - BADGE) / 2), (BADGE, BADGE)), H)
        self.hovers.append(self.badge)

        back = IconButton("backward.end.fill", content, diameter=SKIP, icon_size=17.0, clicked=self._back)
        _place(back, ((cx - PLAY / 2 - 22 - SKIP, y + (PLAY - SKIP) / 2), (SKIP, SKIP)), H)
        self.hovers.append(back)
        self._button("back", back)

        play = IconButton("pause.fill", content, diameter=PLAY, fill=theme.content, glow=theme.white_glow, icon_size=20.0,
                          icon_color=theme.surface, clicked=self._toggle)
        _place(play, ((cx - PLAY / 2, y), (PLAY, PLAY)), H)
        self._button("toggle", play)

        fwd = IconButton("forward.end.fill", content, diameter=SKIP, icon_size=17.0, clicked=self._forward)
        _place(fwd, ((cx + PLAY / 2 + 22, y + (PLAY - SKIP) / 2), (SKIP, SKIP)), H)
        self.hovers.append(fwd)
        self._button("forward", fwd)

        self.speed_btn = SpeedPill(content, self._speed_menu)
        _place(self.speed_btn, ((W - PAD - PILL_W, y + (PLAY - PILL_H) / 2), (PILL_W, PILL_H)), H)
        self._button("speed", self.speed_btn)

    def _build_kit(self, flat: bool) -> dict:
        """Every docked form for one orientation, then a snapshot of their attributes."""
        edge = "bottom" if flat else "left"
        self.pill = Shapes(self.panel)
        self.pill.resize(*(round(v) for v in oriented(pill_len(flat), DOCK_W, edge)))
        self.build_pill(self.pill, flat)
        self.pill_host = ScaleHost(self.panel, self.pill)
        self.pill_host.resize(self.pill.size())
        self.sliver = Sliver(self.panel, edge)
        self.sliver.on_enter = self.mouse_entered
        self.rest = Sliver(self.panel, edge)
        self.rest.set_rest(True)
        self.rest.on_enter = self.mouse_entered
        self.menu = Shapes(self.panel)
        self.build_menu(self.menu, flat)
        self.menu_host = ScaleHost(self.panel, self.menu)
        self.dictate_host = DictateHost(self.panel)
        self.dictate_host.resize(*(round(v) for v in oriented(FREE_H, CAP_W, edge)))
        self.build_dictate(self.dictate_host, flat)
        for view in (self.pill_host, self.sliver, self.rest, self.menu_host, self.dictate_host):
            view.hide()
        return {name: getattr(self, name) for name in ORIENTED}

    def _orient(self, flat: bool) -> None:
        """Point the docked-form attributes at the upright or the flat set of widgets."""
        if flat == self._flat:
            return
        self._flat = flat
        for name, view in self._kits[flat].items():
            setattr(self, name, view)

    def build_pill(self, content: Shapes, flat: bool) -> None:
        """The open docked layout: separate discs for close and expand beside the main capsule,
        all in one transparent container so they scale together. Upright, top to bottom: close,
        expand, previous, play, next, progress; flat, the same left to right."""
        T = DOCK_W
        c = T / 2
        L = pill_main_len(flat)
        total = pill_len(flat)
        content_h = T if flat else total
        main_h = T if flat else L

        main = Surface(content, T / 2)
        _place(main, lay(0, L, 0, T, total, flat, flip=True), content_h)
        content.shapes.append((main, T / 2))

        a = 12.0
        if flat:  # the progress bar lies along the capsule
            frame = ((L - a - DOCK_BAR, (T - SCRUB_H) / 2), (DOCK_BAR, SCRUB_H))
            a += DOCK_BAR + 10
        else:
            frame = ((c - DOCK_BAR / 2, a - (SCRUB_H - BAR_H) / 2), (DOCK_BAR, SCRUB_H))
            a += BAR_H + 10
        self.dock_scrub = ScrubBar(main, knob=10.0)
        _place(self.dock_scrub, frame, main_h)
        self.dock_scrub.on_scrub = self._scrub_preview
        self.dock_scrub.on_seek = lambda f: self._fire("seek_to", f)

        fwd = IconButton("forward.end.fill", main, diameter=DOCK_SKIP, icon_size=12.0, clicked=self._forward)
        _place(fwd, lay(a, DOCK_SKIP, c - DOCK_SKIP / 2, DOCK_SKIP, L, flat, flip=True), main_h)
        self.hovers.append(fwd)
        self._button("forward", fwd)
        a += DOCK_SKIP + 4

        play = IconButton("pause.fill", main, diameter=DOCK_PLAY, fill=theme.content, glow=theme.white_glow, icon_size=14.0,
                          icon_color=theme.surface, clicked=self._toggle)
        _place(play, lay(a, DOCK_PLAY, c - DOCK_PLAY / 2, DOCK_PLAY, L, flat, flip=True), main_h)
        self.hovers.append(play)
        self._button("toggle", play)
        a += DOCK_PLAY + 4

        back = IconButton("backward.end.fill", main, diameter=DOCK_SKIP, icon_size=12.0, clicked=self._back)
        _place(back, lay(a, DOCK_SKIP, c - DOCK_SKIP / 2, DOCK_SKIP, L, flat, flip=True), main_h)
        self.hovers.append(back)
        self._button("back", back)

        def disc(a: float, icon: str, clicked: Callable[[], None], tip: str) -> IconButton:
            btn = IconButton(icon, content, diameter=DISC, fill=lambda: theme.surface(0.97), border=theme.hairline,
                             icon_size=11.0, icon_color=theme.content_dim, weight="bold", tooltip=tip, clicked=clicked)
            _place(btn, lay(a, DISC, c - DISC / 2, DISC, total, flat, flip=True), content_h)
            content.shapes.append((btn, DISC / 2))
            self.hovers.append(btn)
            return btn

        a = L + DOCK_GAP_V
        self.chevron = disc(a, "chevron.right", self.expand, "Expand player")
        a += DISC + DOCK_GAP_V
        self._button("stop", disc(a, "xmark", self._stop, "Stop"))

    def build_menu(self, content: Shapes, flat: bool) -> None:
        """Idle, hovered: what grows out of the resting squiggle. Upright, bottom to top; flat,
        left to right: a dictate capsule, a read-selection disc and a history disc, centred on one
        axis, in one transparent container (scaled as a unit, like the docked reading pill).
        _layout_menu shows only the items whose feature is on and sizes the container to them."""
        self.menu_hovers = []

        def item(length: float, thick: float, icon: str, size: float, tint, clicked, tip: str, key_fn, cls=IconButton):
            w, h = oriented(length, thick, "bottom" if flat else "left")
            btn = cls(icon, content, diameter=min(w, h), fill=lambda: theme.surface(0.97), border=theme.hairline,
                      icon_size=size, icon_color=tint, weight="semibold", clicked=clicked)
            btn.resize(round(w), round(h))
            btn.on_hover = lambda on: self._hover_tip(on, btn, tip, key_fn)
            self.menu_hovers.append(btn)
            content.shapes.append((btn, thick / 2))
            return btn

        self.menu_dictate = item(MENU_MIC_H, MENU_MIC_W, "mic.fill", 14.0, theme.content, self._menu_dictate, "Dictate",
                                 lambda: self.key_name, CapsuleButton)
        self.menu_read = item(MENU_DISC, MENU_DISC, "play.fill", 10.0, theme.content_dim, self._menu_read,
                              "Read selection", lambda: self.read_key)
        self.menu_history = item(MENU_DISC, MENU_DISC, "clock.arrow.circlepath", 11.0, theme.content_dim,
                                 self._menu_history, "History", lambda: "")
        self._layout_menu_kit(content, flat)

    def build_dictate(self, host: DictateHost, flat: bool) -> None:
        """Dictating: a capsule centred in the FREE_H long host. Compact it is COMPACT_H long with
        only the wave; hands-free it grows to FREE_H about its centre and shows cancel (top, or
        left when flat) and finish (bottom, or right). Each button keeps its distance to its own
        end of the capsule as it grows (_lay_dictate), and the capsule clips them. Each button's
        click area is a strip BUTTON_HIT long across the whole capsule, around its smaller disc."""
        T = CAP_W
        capsule = Surface(host, T / 2)
        capsule.length = COMPACT_H
        host.capsule = capsule
        wave = WaveView(capsule, horizontal=flat)
        wave.level_fn = lambda: self._level

        def button(d: float, fill, glow, icon: str, size: float, tint, clicked, tip: str, key_fn):
            btn = FadeButton(icon, capsule, diameter=d, fill=fill, glow=glow, icon_size=size, icon_color=tint,
                             weight="bold", clicked=clicked)
            btn.resize(*(round(v) for v in oriented(BUTTON_HIT, T, "bottom" if flat else "left")))
            disc = QWidget(btn)  # the disc's own rect: the tip sits beside it, as on the Mac
            disc.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            r = btn.shape_rect()
            disc.setGeometry(round(r.x()), round(r.y()), round(r.width()), round(r.height()))
            btn.on_hover = lambda on: self._hover_tip(on, disc, tip, key_fn)
            btn.hide()
            return btn, disc

        self.cancel_btn, self.cancel_disc = button(CANCEL_D, theme.fill_soft, None, "xmark", 9.0, theme.content_dim,
                                                   self._dictate_cancel, "Cancel", lambda: "esc")
        self.finish_btn, self.finish_disc = button(FINISH_D, theme.content, theme.white_glow, "checkmark", 11.0,
                                                   theme.surface, self._dictate_finish, "Finish", lambda: self.key_name)
        self.dictate_capsule, self.wave = capsule, wave
        kit = {"dictate_host": host, "dictate_capsule": capsule, "wave": wave, "cancel_btn": self.cancel_btn,
               "finish_btn": self.finish_btn}
        self._lay_dictate(kit, flat, COMPACT_H, 0.0)
        self._settle_dictate(kit)

    def _lay_dictate(self, kit: dict, flat: bool, length: float, alpha: float) -> None:
        """The dictate capsule `length` long, centred in its host, with the wave in its middle and
        cancel and finish (at `alpha`) each the same distance from its own end as when compact."""
        T = CAP_W
        capsule = kit["dictate_capsule"]
        capsule.length = length
        _place(capsule, lay((FREE_H - length) / 2, length, 0, T, FREE_H, flat), T if flat else FREE_H)
        inner_h = T if flat else length
        _place(kit["wave"], lay((length - WAVE_H) / 2, WAVE_H, (T - WAVE_W) / 2, WAVE_W, length, flat), inner_h)
        cancel_at = length - CANCEL_TOP - CANCEL_D - (BUTTON_HIT - CANCEL_D) / 2
        finish_at = FINISH_BOTTOM - (BUTTON_HIT - FINISH_D) / 2
        for btn, along in ((kit["cancel_btn"], cancel_at), (kit["finish_btn"], finish_at)):
            _place(btn, lay(along, BUTTON_HIT, 0, T, length, flat, flip=True), inner_h)
            btn.set_alpha(alpha)

    def _settle_dictate(self, kit: dict) -> None:
        """The growth ended: the shadow takes the capsule's new size."""
        kit["dictate_host"].shadow_rect = QRectF(kit["dictate_capsule"].geometry())
        self.panel.update()

    def menu_length(self) -> float:
        """The open menu's length along its edge."""
        return sum(length for _, length, _ in MENU_ITEMS) + DOCK_GAP_V * (len(MENU_ITEMS) - 1)

    def _layout_menu_kit(self, content: Shapes, flat: bool) -> float:
        total = self.menu_length()
        w, h = oriented(total, MENU_W, "bottom" if flat else "left")
        content.resize(round(w), round(h))
        content_h = MENU_W if flat else total
        widgets = {"dictate": content.shapes[0][0], "read": content.shapes[1][0], "history": content.shapes[2][0]}
        c = MENU_W / 2
        a = 0.0
        for name, length, thick in MENU_ITEMS:
            _place(widgets[name], lay(a, length, c - thick / 2, thick, total, flat), content_h)
            a += length + DOCK_GAP_V
        host = content.parentWidget()
        if isinstance(host, ScaleHost):
            host.resize(content.size())
        return total

    def _layout_menu(self) -> float:
        """Lay out both orientations' menus; the length along the edge."""
        for flat, kit in self._kits.items():
            self._layout_menu_kit(kit["menu"], flat)
        self._menu_len = self.menu_length()
        return self._menu_len

    def _button(self, name: str, btn) -> None:
        self.buttons.setdefault(name, []).append(btn)

    def set_tooltips(self, tips: dict[str, str]) -> None:
        for name, tip in tips.items():
            for btn in self.buttons.get(name, []):
                btn.setToolTip(tip)

    def _restyle(self) -> None:
        """Light or dark flipped: repaint every form, the hidden ones too."""
        try:
            for w in self.panel.findChildren(QWidget):
                w.update()
            for kit in self._kits.values():
                for host in (kit["pill_host"], kit["menu_host"]):
                    if host.scale < 0.999:
                        host._picture = None
                        host.set_scale(host.scale)
        except RuntimeError:  # the window is gone (a closed preview)
            pass

    # -- actions -----------------------------------------------------------------

    def _fire(self, name: str, *args) -> None:
        cb = self.actions.get(name)
        if cb:
            cb(*args)

    def _back(self) -> None:
        self._fire("back")

    def _toggle(self) -> None:
        self._fire("toggle")

    def _forward(self) -> None:
        self._fire("forward")

    def _stop(self) -> None:
        self._fire("stop")
        self._update(Status())  # hide right away, even if the reader was already idle

    def _menu_dictate(self) -> None:
        self._close_menu_now()
        self._fire("dictate")

    def _menu_read(self) -> None:
        self._close_menu_now()
        self._fire("read_selection")

    def _menu_history(self) -> None:
        self._close_menu_now()
        self._fire("history")

    def _dictate_cancel(self) -> None:
        self._hide_tip()
        self._fire("dictate_cancel")

    def _dictate_finish(self) -> None:
        self._hide_tip()
        self._fire("dictate_stop")

    def _dock_side(self) -> None:
        """Collapse the card onto the nearest edge of the screen it's on."""
        if self.dock:
            return
        edge, screen, along = self._nearest_edge()
        self._collapse(edge, screen, along)
        self._remember()

    def _nearest_edge(self):
        cx, cy = _centre(self.frame_mac())
        screen = _screen_at(cx, cy)
        edge = nearest_edge(_vf(screen), cx, cy)
        return edge, screen, cx if is_flat(edge) else cy

    def _refresh_dock_arrow(self) -> None:
        edge, _, _ = self._nearest_edge()
        self.dock_btn.set_icon(f"arrow.{ARROWS[edge]}.to.line.compact", 11.0, "bold")

    def _new_menu(self) -> QMenu:
        """A popup menu that asks for as little focus as Qt allows: it never activates on show
        (the app in front keeps its selection), and closes on a click anywhere else."""
        menu = QMenu()
        menu.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._popup = menu  # kept, or Python would collect it while it is open
        return menu

    def _pop(self, menu: QMenu, anchor: QWidget) -> None:
        """Open the menu with its top-left corner 4 px above the button's, as the Mac does."""
        menu.popup(anchor.mapToGlobal(QPoint(0, -4)))

    def _speed_menu(self) -> None:
        menu = self._new_menu()
        for s in SPEEDS:
            act = menu.addAction(f"{s:g}×")
            act.setCheckable(True)
            act.setChecked(abs(s - self._speed) < 0.01)
            act.triggered.connect(lambda _checked=False, v=s: self._fire("set_speed", float(v)))
        self._pop(menu, self.speed_btn)

    def _voice_menu(self) -> None:
        voices, current = self._voices()
        if not voices:
            return
        menu = self._new_menu()
        for vid, text in voices:
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(vid == current)
            act.triggered.connect(lambda _checked=False, v=vid: self._fire("set_voice", str(v)))
        self._pop(menu, self.badge)

    def _voices(self) -> tuple[list, str]:
        if self.voices_fn is None:
            return [], ""
        try:
            return self.voices_fn()
        except Exception:
            log.exception("voices_fn failed")
            return [], ""

    def _scrub_preview(self, f: float) -> None:
        st = self._status
        if st and st.duration > 0:
            self.elapsed.set_text(_fmt(f * st.duration))
            self.remaining.set_text("-" + _fmt((1 - f) * st.duration))

    # -- hover: sliver <-> pill, rest <-> menu ------------------------------------------

    def mouse_entered(self) -> None:
        """The cursor came onto the sliver or the resting squiggle (the Mac's mouseEntered_)."""
        log.debug("mouse_entered form=%s dock=%s dragging=%s", self.form, self.dock, self._moving)
        # Only grow into the pill for a reading that is still on screen: a late enter event after
        # the reading ended would otherwise bring the player back from the dead.
        if (self.dock and self.form == "sliver" and not self._moving
                and self._status is not None and self.visible()):
            self._open_pill()
        elif (self.form == "rest" and not self._moving and self._status is None
              and self.dictation_state not in DICTATING and self.visible() and not self._tucked):
            self._open_menu()

    def _mouse_inside(self, margin: float = 6.0) -> bool:
        """Is the cursor on the open form, or on the resting spot it grew from? The resting spot
        counts too, so a cursor pushed against the screen edge (or on the ends of the squiggle,
        past a one-item menu) keeps it open instead of closing and reopening it."""
        px, py = self._cursor()
        x, y, w, h = self.frame_mac()
        if x - margin <= px <= x + w + margin and y - margin <= py <= y + h + margin:
            return True
        if self.form == "menu":
            spot = docked_frame(self._edge, *self._rest_anchor, SLIVER_H, SLIVER_W, 0.0)
        elif self.form == "pill":
            spot = self._docked_frame(SLIVER_H, SLIVER_W, 0.0)
        else:
            return False
        return near_frame(px, py, spot, reach=0.0)

    def _watch_hover(self) -> None:
        """While the pill or the menu is open, poll the cursor; shrink back once it has been outside
        for HOVER_OUT_DELAY. Polling is immune to the enter and leave churn that form changes cause."""
        self._stop_watch()
        self._outside_since = None
        timer = QTimer(self.panel)
        timer.setInterval(round(HOVER_POLL * 1000))
        timer.timeout.connect(self._hover_tick)
        timer.start()
        self._watch = timer

    def _hover_tick(self) -> None:
        if self.form not in ("pill", "menu") or self._moving:
            self._outside_since = None
            return
        for hv in (self.hovers if self.form == "pill" else self.menu_hovers):
            hv.sync()
        if self._mouse_inside():
            self._outside_since = None
            return
        now = self._now()
        if self._outside_since is None:
            self._outside_since = now
        elif now - self._outside_since >= HOVER_OUT_DELAY:
            self._stop_watch()
            if self.form == "menu":
                self._close_menu()
            else:
                self._close_pill()

    def _stop_watch(self) -> None:
        if self._watch is not None:
            self._watch.stop()
            self._watch.deleteLater()
            self._watch = None

    def form_edge(self, name: str | None = None) -> str:
        """The edge a form sits on: the dock for the reading's docked forms, the idle and
        dictation edge for those; "" for the card."""
        name = name or self.form
        if name in ("sliver", "pill"):
            return self.dock
        if name in ("rest", "menu", "dictate"):
            return self._edge
        return ""

    def _show_form(self, name: str) -> None:
        """The one way the window changes form: put that form's widget in and nothing else. A
        docked form comes from the widget set for its edge's orientation."""
        if name != "dictate":
            for kit in self._kits.values():
                kit["wave"].stop()
        if name != "menu":
            for kit in self._kits.values():
                for hv in kit["menu_hovers"]:
                    hv.set_hover(False, animate=False)
        edge = self.form_edge(name)
        if edge:
            self._orient(is_flat(edge))
        views = {"card": self.card, "pill": self.pill_host, "sliver": self.sliver, "rest": self.rest, "menu": self.menu_host,
                 "dictate": self.dictate_host}
        self._hide_tip()
        self._tucked = False  # a new form is a shown form; only _tuck hides the rest form
        self._slide_token = None  # and a slide still running belongs to the old one
        if self._slide_anim is not None:
            self._slide_anim.stop()
            self._slide_anim = None
        if name not in ("rest", "menu"):
            self._stop_auto_watch()  # a reading or a dictation owns the window: nothing to auto-hide
        if name == "rest":
            self._start_wave_timer()  # (re)shown: the countdown starts over
        else:
            self._stop_wave_timer()
        self.form = name
        self.draggable = name != "dictate"  # a drag would fight push-to-talk
        shadow = name != "rest"  # the squiggle's halo is its own outline
        if (self.margin > 0) != shadow:
            self.set_shadow(shadow)
        self.set_content(views[name], surface=name == "card")

    def _open_pill(self) -> None:
        self._show_form("pill")
        length = pill_len(is_flat(self.dock))
        self.set_frame_mac(self._docked_frame(length, DOCK_W, DOCK_GAP))
        self._set_scale(SLIVER_H / length, animate=False)
        self.order_front()
        self._set_scale(1.0, animate=True)
        self._watch_hover()

    def _close_pill(self) -> None:
        log.debug("close_pill form=%s dragging=%s", self.form, self._moving)
        if self.form != "pill" or self._moving:
            return
        # A pill that closes after the reading ended goes back to the sliver, but hidden.
        self._set_scale(SLIVER_H / pill_len(is_flat(self.dock)), animate=True,
                        done=lambda: self._show_sliver(show=self._status is not None))

    def _menu_start(self) -> float:
        """The scale the menu grows from: the sliver's length, or half size for a menu shorter
        than the sliver (a lone disc)."""
        return SLIVER_H / self._menu_len if self._menu_len > SLIVER_H else MENU_POP

    def _open_menu(self) -> None:
        """The resting squiggle grows into the idle menu, exactly as the docked reading sliver grows
        into its pill."""
        self._layout_menu()
        self._show_form("menu")
        self.set_frame_mac(docked_frame(self._edge, *self._rest_anchor, self._menu_len, MENU_W, DOCK_GAP))
        self._set_scale(self._menu_start(), animate=False)
        self.order_front()
        self._set_scale(1.0, animate=True)
        self._watch_hover()

    def _close_menu(self) -> None:
        if self.form != "menu" or self._moving:
            return
        # The cursor closed it, so it is near: back to the squiggle without a linger.
        self._set_scale(self._menu_start(), animate=True, done=lambda: self.show_idle(linger=False))

    def _close_menu_now(self) -> None:
        """An item was clicked: back to the resting squiggle at once, before its action runs."""
        if self.form == "menu":
            self._cancel_anim()
            self.show_idle(linger=False)

    def _show_sliver(self, show: bool = True) -> None:
        if show and self.form == "pill" and self._moving:
            return  # mid-drag the pill stays open; resetting the form while hiding is fine
        for hv in self.hovers:
            hv.set_hover(False, animate=False)
        self._show_form("sliver")
        self.set_frame_mac(self._docked_frame(SLIVER_H, SLIVER_W, 0.0))
        if show:
            self.order_front()
        self._refresh_sliver()

    def _refresh_sliver(self) -> None:
        st = self._status
        if st is None:
            return
        frac = st.position / st.duration if st.duration > 0 else 0.0
        self.sliver.set_state(frac, st.state != "playing", self.dock or "left", playing=st.state == "playing")
        if (st.session, st.index) != self._last_chunk:
            if self._last_chunk[0] == st.session and self.form == "sliver":
                self.sliver.pulse()
            self._last_chunk = (st.session, st.index)

    def _cancel_anim(self) -> None:
        self._stop_watch()
        self._pending_done = None
        if self._scale_anim is not None:
            self._scale_anim.stop()
            self._scale_anim = None
            if self._scale_host is not None:  # the animation's end state, as removing a layer animation shows
                self._scale_host.set_scale(self._scale)
                self._settle_shadow(self._scale_host)

    def _scaled(self) -> ScaleHost:
        """The host the hover animation scales, set to grow from its edge: the idle menu while that
        form is up, else the docked reading pill."""
        if self.form == "menu":
            host, host.edge = self.menu_host, self._edge
        else:
            host, host.edge = self.pill_host, self.dock or "left"
        return host

    def _settle_shadow(self, host: ScaleHost) -> None:
        host.shadow_scale = host.scale
        self.panel.update()

    def _set_scale(self, s: float, animate: bool, done: Callable[[], None] | None = None) -> None:
        """Set the open form's scale, optionally animating from where it visibly is over
        HOVER_SECS, easing out. `done` runs when it finishes. The shadow follows at the end."""
        host = self._scaled()
        if self._scale_anim is not None:
            self._scale_anim.stop()
            self._scale_anim = None
        current = host.scale
        self._scale = s
        self._scale_host = host
        log.debug("set_scale %.2f animate=%s", s, animate)
        if animate and not theme.reduce_motion() and abs(current - s) > 1e-6:
            anim = QVariantAnimation(self.panel)
            anim.setStartValue(float(current))
            anim.setEndValue(float(s))
            anim.setDuration(round(HOVER_SECS * 1000))
            anim.setEasingCurve(EASE_OUT)
            anim.valueChanged.connect(lambda v: host.set_scale(float(v)))
            token = object()
            self._pending_done = token

            def finished() -> None:
                host.set_scale(s)
                if self._scale_anim is anim:
                    self._scale_anim = None
                if self._pending_done is token:
                    self._pending_done = None
                    self._settle_shadow(host)
                    if done:
                        done()
            anim.finished.connect(finished)
            self._scale_anim = anim
            anim.start()
            return
        host.set_scale(s)
        self._settle_shadow(host)
        if done:
            done()

    def _docked_frame(self, length: float, across: float, gap: float):
        """Frame of a form `length` long and `across` deep hanging off the dock anchor, `gap`
        points in from the edge (Mac-style)."""
        return docked_frame(self.dock, *self._anchor, length, across, gap)

    # -- dragging / docking ---------------------------------------------------------

    def drag_began(self) -> None:
        log.debug("drag_began form=%s dock=%s scale=%.2f", self.form, self.dock, self._scale)
        self._moving = True
        if self.form in ("rest", "menu"):
            self.rest.stop_wave()
            if self._tucked:  # caught mid slide-out: it is in the hand now, fully in
                self._tucked = False
                self._slide_rest(True, animate=False)
            self._hide_tip()
            self._cancel_anim()
            if self.form == "menu" and self._scale != 1.0:
                self._set_scale(1.0, animate=False)
            return
        self._cancel_anim()
        if self.form == "pill" and self._scale != 1.0:
            self._set_scale(1.0, animate=False)

    def drag_moved(self, x: float, y: float) -> None:
        """The panel's hook, with the cursor in Qt logical coordinates."""
        self.drag_moved_mac(*coords.to_mac_point(x, y, coords.primary_height()))

    def drag_ended(self, x: float, y: float) -> None:
        self.drag_ended_mac(*coords.to_mac_point(x, y, coords.primary_height()))

    def drag_moved_mac(self, mx: float, my: float) -> None:
        if self.form in ("rest", "menu"):
            self._hint_rest(mx, my)  # it follows the mouse; on release it snaps to the nearer edge
            return
        if not self.dock:
            return  # the card moves freely; only its tuck button docks it
        edge, screen = self._edge_near(mx, my)
        if not edge:
            self._hide_zones()
            return
        zone, other = (self.flat_zone, self.zone) if is_flat(edge) else (self.zone, self.flat_zone)
        other.hide()
        vf = _vf(screen)
        (x, y), _ = docked_frame(edge, edge_coord(vf, edge), self._centre_along(edge),
                                 *((zone.width, zone.height) if is_flat(edge) else (zone.height, zone.width)),
                                 CAPSULE_INSET - 4)
        self._show_zone(zone, x, y, screen)
        self.order_front()

    def _show_zone(self, zone: DropZone, x: float, y: float, screen) -> None:
        """The hint at a Mac-style bottom-left corner, kept on the screen (the Mac show_at)."""
        x, y, w, h = coords.clamp_to((x, y, zone.width, zone.height), _vf(screen))
        zone.set_frame_mac((x, y, w, h))
        zone.order_front()

    def _hide_zones(self) -> None:
        self.zone.hide()
        self.flat_zone.hide()

    def drag_ended_mac(self, mx: float, my: float) -> None:
        log.debug("drag_ended at (%.0f, %.0f) form=%s dock=%s", mx, my, self.form, self.dock)
        self._moving = False
        if self.form in ("rest", "menu"):
            self._hide_zones()
            self._drop_rest(mx, my)
            return
        self._hide_zones()
        edge, screen = self._edge_near(mx, my)
        if edge and self.dock:  # a docked player moves between edges; the card never docks by dragging
            self._collapse(edge, screen, self._centre_along(edge))
        elif self.dock:
            self._expand_at(mx, my)
        else:
            x, y, _, _ = self.frame_mac()
            self.set_frame_mac(coords.clamp_to((x, y, self.width, self.height), _vf(screen)))
            self._refresh_dock_arrow()
        if self._status is None:  # the reading ended while the drag was still live
            self.hide()
        self._remember()  # the new dock and position stick either way

    def clicked(self) -> None:
        if self.form == "rest":
            self._open_menu()  # a click on the squiggle opens the menu too (hover usually has)
        elif self.dock:
            self.expand()

    def expand(self) -> None:
        """Chevron or click: open the card next to where the docked player sits."""
        if not self.dock:
            return
        (x, y), (w, h) = docked_frame(self.dock, *self._anchor, self.width if is_flat(self.dock) else self.height,
                                      self.height if is_flat(self.dock) else self.width, DOCK_GAP)
        self._expand_at(x + w / 2, y + h / 2)
        self._remember()

    def _centre_along(self, edge: str) -> float:
        """The window's centre along `edge`: x for the bottom, y for the sides."""
        cx, cy = _centre(self.frame_mac())
        return cx if is_flat(edge) else cy

    def _edge_near(self, mx: float, my: float):
        """Which screen edge (if any) the panel is close enough to for docking."""
        screen = _screen_at(mx, my)
        return snap_edge(_vf(screen), coords.as_frame(self.frame_mac())), screen

    def _collapse(self, edge: str, screen, along: float) -> None:
        """Dock on `edge` of `screen`, resting form, centred on `along`, with room for the open pill."""
        self._cancel_anim()
        vf = _vf(screen)
        lo, n = _axis(vf, edge)
        length = pill_len(is_flat(edge))
        along = min(max(along, lo + length / 2), lo + n - length / 2)
        self.dock = edge
        self._anchor = (edge_coord(vf, edge), along)
        self._scale = SLIVER_H / length
        for kit in self._kits.values():
            kit["chevron"].set_icon(CHEVRONS[edge], 11.0, "bold")
        self._show_sliver()

    def _expand_at(self, cx: float, cy: float) -> None:
        """Show the card centred on (cx, cy), clamped to that screen."""
        self._cancel_anim()
        self.dock = ""
        self._scale = 1.0
        screen = _screen_at(cx, cy)
        frame = coords.clamp_to((cx - self.width / 2, cy - self.height / 2, self.width, self.height), _vf(screen))
        self._show_form("card")
        self.set_frame_mac(frame)
        self.order_front()
        self._refresh_dock_arrow()

    def _remember(self) -> None:
        """Persist dock and position as fractions of the screen's usable area, so the same spot maps
        onto any screen size (and means the same on a Mac)."""
        f = self.frame_mac()
        vx, vy, vw, vh = vf = _vf(_screen_at(*_centre(f)))
        if self.dock:  # docked: y is the position along the edge, whichever edge
            fx, fy = 0.0, along_fraction(vf, self.dock, self._anchor[1])
        else:
            fx = (f[0] - vx) / max(1.0, vw - self.width)
            fy = (f[1] - vy) / max(1.0, vh - self.height)
        self.layout = {"dock": self.dock, "x": min(1.0, max(0.0, fx)), "y": min(1.0, max(0.0, fy))}
        self._fire("layout", self.dock, self.layout["x"], self.layout["y"])

    def place(self, screen=None) -> None:
        """Put the player where it was last left, on `screen` (default: the screen the mouse is on,
        where the text was just selected)."""
        screen = screen or _screen_under_mouse()
        vx, vy, vw, vh = vf = _vf(screen)
        saved = self.layout
        if saved.get("dock") in DOCKS:
            self._collapse(saved["dock"], screen, along_centre(vf, saved["dock"], float(saved.get("y", -1.0))))
            self._log_placed(screen)
            return
        if saved.get("x", -1) >= 0 and saved.get("y", -1) >= 0:
            x = vx + saved["x"] * (vw - self.width)
            y = vy + saved["y"] * (vh - self.height)
        else:
            x = vx + vw - self.width - MARGIN
            y = vy + MARGIN
        self._expand_at(x + self.width / 2, y + self.height / 2)
        self._log_placed(screen)

    def _log_placed(self, screen) -> None:
        """One breadcrumb per reading: where the player ended up, and on which screen."""
        x, y, w, h = self.frame_mac()
        name = screen.name() if screen is not None and hasattr(screen, "name") else "?"
        log.info("player placed dock=%s form=%s screen=%s frame=(%.0f, %.0f, %.0f, %.0f)", self.dock or "none",
                 self.form, name, x, y, w, h)

    # -- state -------------------------------------------------------------------

    def update(self, st: Status) -> None:
        """Snapshots arrive on the reader's threads. Keep only the newest and drain it once on the
        GUI thread: queuing every snapshot lets a stale one (an old session's idle) land after a
        newer one and hide a player that should be up."""
        with self._latest_lock:
            self._latest = st
            if self._drain_pending:
                return
            self._drain_pending = True
        on_main(self._drain)

    def _drain(self) -> None:
        with self._latest_lock:
            st = self._latest
            self._latest = None
            self._drain_pending = False
        if st is None:
            return
        if self.status_fn is not None:  # the queued snapshot may be stale by now; ask the reader
            try:
                st = dataclasses.replace(self.status_fn())
            except Exception:
                log.exception("status_fn failed")
        self._update(st)

    def _update(self, st: Status) -> None:
        if st.state == "idle":
            self._status = None
            if self.dictation_state in DICTATING:
                self._reading_spot = None  # nothing to bring back when the dictation ends
                return
            self._cancel_anim()
            self._hide_zones()
            if self.dock:
                self._show_sliver(show=False)  # so the next reading starts from the resting form
            log.info("player hidden (reading ended) dock=%s form=%s", self.dock, self.form)
            self.hide()
            self.show_idle()
            return
        self._status = st
        icon = "play.fill" if st.state == "paused" else "pause.fill"
        for i, btn in enumerate(self.buttons["toggle"]):  # the card's first, then each docked pill's
            btn.set_icon(icon, 20.0 if i == 0 else 14.0)
        self._speed = st.speed
        self.speed_btn.set_text(f"{st.speed:g}×")
        self.progress.set_text("Loading…" if st.state == "loading" else f"{st.index} of {st.total}")
        if not (self.scrub.scrubbing or any(k["dock_scrub"].scrubbing for k in self._kits.values())):
            self.elapsed.set_text(_fmt(st.position))
            self.remaining.set_text("-" + _fmt(max(0.0, st.duration - st.position)))
        frac = st.position / st.duration if st.duration > 0 else 0.0
        self.scrub.set_fraction(frac)
        for kit in self._kits.values():
            kit["dock_scrub"].set_fraction(frac)
        voice = st.provider.split("·")[-1].strip() if st.provider else ""
        self.badge.set_letter((voice[:1] or "S").upper())
        pickable = bool(self._voices()[0])
        self.badge.set_pickable(pickable)
        badge_tip = "Change voice" if pickable else st.provider
        if badge_tip != self._badge_tip:
            self._badge_tip = badge_tip
            self.badge.setToolTip(badge_tip)
        tip = f"{st.index} of {st.total}  ·  -{_fmt(max(0.0, st.duration - st.position))}"
        if tip != self._tip:
            self._tip = tip
            for kit in self._kits.values():
                kit["pill"].setToolTip(tip)
                kit["sliver"].setToolTip(tip)
        self._refresh_sliver()
        # A mouse-up that never arrived would leave the flag set and place() skipped forever.
        if self._moving and not self._button_down():
            log.warning("drag flag stuck; clearing")
            self._clear_drag()
        if self.dictation_state in DICTATING:
            return  # the dictation owns the window; the reading form comes back when it ends
        if st.session != self._session or not self.visible() or self.form in ("rest", "menu"):
            self._session = st.session
            if self.form in ("rest", "menu") and self._moving:
                self._clear_drag()  # a reading took over mid-drag of the idle pill
            if not self._moving:
                self.place()

    def _clear_drag(self) -> None:
        self._moving = False
        self._press = None
        self._dragging = False

    def _button_down(self) -> bool:
        return _left_button_down()  # replaced in tests

    # -- idle ---------------------------------------------------------------------------

    def show_idle(self, linger: bool = True) -> None:
        """Nothing playing: the resting squiggle on its edge, or nothing. With auto-hide it slides in
        (unless it is out already) and the watch takes over; `linger` keeps it out for LINGER_SECS,
        for every caller but the cursor closing the menu."""
        form = pick_form("idle" if self._status is None else "playing", self.dictation_state, bool(self.dock),
                         self.idle_pill)
        if form != "rest":
            self._stop_auto_watch()
            self._stop_wave_timer()
            if self.form in ("rest", "menu"):
                self._cancel_anim()
                self.hide()
            return
        out = self.form in ("rest", "menu") and not self._tucked  # on screen already: no second slide
        edge, fy = dictation_spot(self.layout, self.dictation_layout)
        self._place_rest(_vf(_screen_under_mouse()), edge, fy)
        if not self.auto_hide:
            self._stop_auto_watch()
            self.order_front()
            return
        self._away_since = None
        if linger:
            self._linger_until = self._now() + LINGER_SECS
        if not out:
            self._slide_rest(False, animate=False)
        self.order_front()
        if not out:
            self._slide_rest(True, animate=True)
        self._start_auto_watch()

    def _place_rest(self, vf, edge: str, frac: float) -> None:
        self._edge = edge
        self._show_form("rest")
        frame = edge_frame(vf, edge, frac, SLIVER_H, SLIVER_W, inset=0.0)
        self.set_frame_mac(frame)
        self.rest.set_state(0.0, False, edge)
        self._rest_anchor = anchor_of(frame, edge)
        self._slide_rest(True, animate=False)  # fully in; show_idle and _reveal slide it from there

    def set_auto_hide(self, on: bool) -> None:
        """Switch auto-hide live. Switched on, the squiggle lingers before it first slides away, so
        the change is visible; switched off, it simply stays out."""
        self.auto_hide = bool(on)
        if not on:
            self._stop_auto_watch()
        if self.form in ("rest", "menu") or (self._status is None and self.dictation_state not in DICTATING):
            self.show_idle()

    def set_wave(self, on: bool) -> None:
        """Switch the resting ripple live. The countdown keeps running and may_wave says no;
        switched off, a ripple in flight is cut back to the resting shape."""
        self.rest_wave = bool(on)
        if not on:
            for kit in self._kits.values():
                kit["rest"].stop_wave()

    def set_idle_pill(self, on: bool) -> None:
        self.idle_pill = bool(on)
        if self.form in ("rest", "menu") or (on and self._status is None and self.dictation_state not in DICTATING):
            self.show_idle()

    # -- auto-hide -------------------------------------------------------------------

    def _now(self) -> float:
        return time.monotonic()  # replaced in tests

    def _cursor(self) -> tuple[float, float]:
        return coords.cursor_pos_mac()  # replaced in tests

    def _start_auto_watch(self) -> None:
        """Poll the cursor while idle with auto-hide on: no enter event can tell a cursor
        approaching a window that is not on screen."""
        if self._auto_watch is not None:
            return
        timer = QTimer(self.panel)
        timer.setInterval(round(AUTO_HIDE_POLL * 1000))
        timer.timeout.connect(self._auto_tick)
        timer.start()
        self._auto_watch = timer

    def _stop_auto_watch(self) -> None:
        if self._auto_watch is not None:
            self._auto_watch.stop()
            self._auto_watch.deleteLater()
            self._auto_watch = None
        self._away_since = None

    def _auto_tick(self) -> None:
        """Hidden: slide in when the cursor comes near the spot on the screen it is on. Shown: slide
        out once tuck_due says so; never while the menu is open or during a drag."""
        if (not self.auto_hide or not self.idle_pill or self._status is not None
                or self.dictation_state in DICTATING or self.form not in ("rest", "menu")):
            return
        px, py = self._cursor()
        if self._tucked:
            self._was_inside = False
            if self._moving:
                return
            edge, fy = dictation_spot(self.layout, self.dictation_layout)
            vf = _vf(_screen_at(px, py))
            if near_frame(px, py, edge_frame(vf, edge, fy, SLIVER_H, SLIVER_W, inset=0.0)):
                self._reveal(vf, edge, fy)
            return
        if self.form != "rest" or self._moving:
            self._away_since = None
            self._was_inside = True  # leaving the menu or a drop: not an arrival on the squiggle
            return
        frame = coords.as_frame(self.frame_mac())
        inside = near_frame(px, py, frame, reach=0.0)
        arrived, self._was_inside = inside and not self._was_inside, inside
        due, self._away_since = tuck_due(near_frame(px, py, frame), self._now(), self._away_since, self._linger_until)
        if due:
            self._tuck()
        elif arrived and not self._button_down():
            # Slid in under the cursor: no enter event may come, so the arrival opens the menu here.
            self._open_menu()

    def _reveal(self, vf, edge: str, frac: float) -> None:
        """The cursor came near: slide the squiggle in. Caught mid slide-out at the same spot it
        turns around from where it is; anywhere else it is placed afresh."""
        target = edge_frame(vf, edge, frac, SLIVER_H, SLIVER_W, inset=0.0)
        here = _same_frame(self.frame_mac(), target)
        if not (here and self.visible() and self.form == "rest"):
            self._place_rest(vf, edge, frac)
            self._slide_rest(False, animate=False)
            self.order_front()
        self._tucked = False
        self._away_since = None
        self._slide_rest(True, animate=True)
        self._start_wave_timer()

    def _tuck(self) -> None:
        """Slide the squiggle out of the edge, then take the window off screen."""
        self._tucked = True
        self._away_since = None
        self._stop_wave_timer()
        self._hide_tip()
        self._slide_rest(False, animate=True, done=self._tucked_away)

    def _tucked_away(self) -> None:
        if self._tucked and self.form == "rest":
            self.hide()

    def _slide_rest(self, inward: bool, animate: bool, done: Callable[[], None] | None = None) -> None:
        """Slide the resting squiggle in from (or out past) its screen edge, fading as it goes. It
        moves inside the window, which stays put and clips it at the edge, so it never shows on a
        display beside this one."""
        rest = self.rest
        target = 0.0 if inward else SLIVER_W
        if self._slide_anim is not None:
            self._slide_anim.stop()
            self._slide_anim = None
        if not animate or theme.reduce_motion() or abs(rest.slide - target) < 1e-6:
            self._slide_token = None
            rest.set_slide(target)
            if done:
                done()
            return
        token = object()
        self._slide_token = token
        anim = QVariantAnimation(self.panel)
        anim.setStartValue(float(rest.slide))
        anim.setEndValue(float(target))
        anim.setDuration(round(SLIDE_SECS * 1000))
        anim.setEasingCurve(EASE_OUT if inward else EASE_IN)
        anim.valueChanged.connect(lambda v: rest.set_slide(float(v)))

        def finished() -> None:
            rest.set_slide(target)
            if self._slide_anim is anim:
                self._slide_anim = None
            if self._slide_token is token:
                self._slide_token = None
                if done:
                    done()
        anim.finished.connect(finished)
        self._slide_anim = anim
        anim.start()

    # -- the resting ripple -----------------------------------------------------------

    def _reduce_motion(self) -> bool:
        return theme.reduce_motion()  # replaced in tests

    def _start_wave_timer(self) -> None:
        """(Re)start the countdown to the next ripple. A timer firing one short animation each time,
        not one endless animation: between ripples nothing is drawn."""
        self._stop_wave_timer()
        timer = QTimer(self.panel)
        timer.setInterval(round(WAVE_EVERY * 1000))
        timer.setTimerType(Qt.TimerType.CoarseTimer)  # the system may fold its wakeups in with others
        timer.timeout.connect(self._wave_tick)
        timer.start()
        self._wave_timer = timer

    def _stop_wave_timer(self) -> None:
        """Stop the countdown and cut a ripple in flight back to the resting shape."""
        if self._wave_timer is not None:
            self._wave_timer.stop()
            self._wave_timer.deleteLater()
            self._wave_timer = None
        for kit in self._kits.values():
            kit["rest"].stop_wave()

    def _wave_tick(self) -> None:
        if may_wave(self.form, self.visible(), self._tucked, self._slide_token is not None, self._moving,
                    self._status is not None, self.dictation_state, self._reduce_motion(), self.rest_wave):
            self.rest.wave()

    def _rest_landing(self, mx: float, my: float):
        """Where the dragged idle pill lands if released now: (screen, vf, edge, fraction along it),
        the nearest edge of the screen under the mouse to the panel's centre. The drop and the hint
        while dragging both ask here, so they always agree."""
        screen = _screen_at(mx, my)
        vf = _vf(screen)
        cx, cy = _centre(self.frame_mac())
        edge = nearest_edge(vf, cx, cy)
        return screen, vf, edge, along_fraction(vf, edge, cx if is_flat(edge) else cy)

    def _hint_rest(self, mx: float, my: float) -> None:
        """While the idle pill is dragged, the faint hint around the spot it would land on: the same
        edge and place along it as the squiggle, kept on screen the same way."""
        screen, vf, edge, fy = self._rest_landing(mx, my)
        zone, other = (self.flat_zone, self.zone) if is_flat(edge) else (self.zone, self.flat_zone)
        other.hide()
        coord, centre = anchor_of(edge_frame(vf, edge, fy, SLIVER_H, SLIVER_W, inset=0.0), edge)
        (x, y), _ = docked_frame(edge, coord, centre,
                                 *((zone.width, zone.height) if is_flat(edge) else (zone.height, zone.width)),
                                 CAPSULE_INSET - 4)
        self._show_zone(zone, x, y, screen)
        self.order_front()

    def _drop_rest(self, mx: float, my: float) -> None:
        """The idle pill was dragged: snap it to the nearest edge of the screen it was dropped on
        (left, right or bottom), at that spot along it, and remember the spot (shared with the
        docked player)."""
        _screen, vf, edge, fy = self._rest_landing(mx, my)
        self._place_rest(vf, edge, fy)
        if self.auto_hide:  # a drop is a showing of its own: it lingers where it landed
            self._away_since = None
            self._linger_until = self._now() + LINGER_SECS
            self._start_auto_watch()
        if self.layout.get("dock") in DOCKS:
            self.layout = {"dock": edge, "x": 0.0, "y": fy}
            self._fire("layout", edge, 0.0, fy)
        else:
            self.dictation_layout = {"dock": edge, "y": fy}
            self._fire("dictation_layout", edge, fy)

    # -- dictation -----------------------------------------------------------------------

    def dictation_update(self, ds) -> None:
        """A DictationStatus, from any thread, up to 20 times a second while recording. The level
        is only stored (the wave's display timer reads it); the GUI thread hears about a state
        change once, however many updates arrive before it gets there."""
        self._level = float(getattr(ds, "level", 0.0))
        with self._dlock:
            if ds.state == self._dqueued:
                return
            self._dqueued = ds.state
            if self._dpending:
                return
            self._dpending = True
        _post(self._drain_dictation)

    def _drain_dictation(self) -> None:
        with self._dlock:
            state = self._dqueued
            self._dpending = False
        self._set_dictation(state)

    def _set_dictation(self, state: str) -> None:
        prev = self.dictation_state
        if state == prev:
            return
        self.dictation_state = state
        log.info("pill: dictation %s", state)
        if state in DICTATING:
            if prev not in DICTATING:
                self._enter_dictation()
            self.wave.set_mode("working" if state == "working" else "recording")
            self._set_hands_free(state == "locked", animate=True)
        elif prev in DICTATING:
            self._leave_dictation()

    def _enter_dictation(self) -> None:
        """Take the window for the dictate form, remembering where the reading form was."""
        self._cancel_anim()
        self._hide_zones()
        if self._moving:
            self._clear_drag()
        self._reading_spot = None
        if self._status is not None and self.visible() and self.form in ("card", "sliver", "pill"):
            self._reading_spot = (self.dock, _centre(self.frame_mac()), self._anchor[1])
        edge, fy = dictation_spot(self.layout, self.dictation_layout)
        self._edge = edge
        self._show_form("dictate")
        self._set_hands_free(False, animate=False)
        self.set_frame_mac(edge_frame(_vf(_screen_under_mouse()), edge, fy, FREE_H, CAP_W))
        self.wave.reset()
        self.wave.start()
        self.order_front()

    def _leave_dictation(self) -> None:
        """Back to the reading form (still paused) where it was, or to idle."""
        self.wave.stop()
        self._set_hands_free(False, animate=False)
        spot, self._reading_spot = self._reading_spot, None
        if self._status is None:
            self.hide()
            self.show_idle()
            return
        self._session = self._status.session
        if spot is None:
            self.place()
        elif spot[0]:
            self._collapse(spot[0], _screen_at(*spot[1]), spot[2])
        else:
            self._expand_at(*spot[1])

    def _set_hands_free(self, on: bool, animate: bool) -> None:
        """Grow the capsule to FREE_H with cancel and finish fading in (or shrink it back), about
        its centre, over GROW_SECS easing out."""
        if on == self.hands_free and animate:
            return
        self.hands_free = on
        if not on:
            self._hide_tip()
        if self._grow_anim is not None:
            self._grow_anim.stop()
            self._grow_anim = None
        kit, flat = self._kits[self._flat], self._flat
        buttons = (kit["cancel_btn"], kit["finish_btn"])
        length, alpha = (FREE_H, 1.0) if on else (COMPACT_H, 0.0)
        if on:
            for b in buttons:
                b.show()

        def settled() -> None:
            if not self.hands_free:
                for b in buttons:
                    b.hide()
                    b.set_hover(False, animate=False)
            self._settle_dictate(kit)

        if not animate or theme.reduce_motion():
            self._lay_dictate(kit, flat, length, alpha)
            settled()
            return
        from_len, from_alpha = kit["dictate_capsule"].length, buttons[0].alpha
        anim = QVariantAnimation(self.panel)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(round(GROW_SECS * 1000))
        anim.setEasingCurve(EASE_OUT)
        anim.valueChanged.connect(lambda v: self._lay_dictate(kit, flat, from_len + (length - from_len) * float(v),
                                                              from_alpha + (alpha - from_alpha) * float(v)))

        def finished() -> None:
            self._lay_dictate(kit, flat, length, alpha)
            if self._grow_anim is anim:
                self._grow_anim = None
                settled()
        anim.finished.connect(finished)
        self._grow_anim = anim
        anim.start()

    def toast_floor(self, gap: float = 12.0) -> float | None:
        """When the pill is on screen on the bottom edge, the lowest y (Mac-style, y up) a toast may
        start at so it sits above the pill; else None."""
        if not self.visible() or not is_flat(self.form_edge()):
            return None
        _, y, _, h = self.frame_mac()
        return y + h + gap

    # -- tooltip pill ------------------------------------------------------------------

    def _hover_tip(self, on: bool, view: QWidget, text: str, key: Callable[[], str] | None = None) -> None:
        if not on:
            self._hide_tip()
            return
        if self._moving or self.form not in ("menu", "dictate") or self.tip is None:
            return
        token = object()
        self._tip_token = token
        QTimer.singleShot(round(TIP_DELAY * 1000), lambda: self._show_tip(token, view, text, key))

    def _show_tip(self, token, view: QWidget, text: str, key: Callable[[], str] | None) -> None:
        if (token is not self._tip_token or self._moving or self.tip is None or not self.visible()
                or not view.isVisibleTo(self.panel)):
            return
        g = view.mapToGlobal(QPoint(0, 0))
        anchor = coords.mac_rect_from_qt((g.x(), g.y(), view.width(), view.height()))
        self.tip.show_beside(anchor, self._edge, text, key() if callable(key) else (key or ""))
        self._tip_up = True

    def _hide_tip(self) -> None:
        self._tip_token = None
        if self._tip_up and self.tip is not None:
            self.tip.hide()
        self._tip_up = False


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------


def _reading(state: str = "playing", index: int = 3, total: int = 9, position: float = 84.0, duration: float = 334.0,
             speed: float = 1.2, session: int = 1) -> Status:
    return Status(state=state, index=index, total=total, position=position, duration=duration, speed=speed,
                  provider="Kokoro · Heart", session=session)


def _preview(setup: Callable[[MiniPlayer], None]) -> QWidget:
    """A player with fake state, settled and still, for the gallery and the screenshots."""
    mp = MiniPlayer()
    mp.auto_hide = False
    mp.voices_fn = lambda: ([("heart", "Heart"), ("bella", "Bella")], "heart")
    setup(mp)
    if mp._scale_anim is not None:  # land any growth at its end
        mp._cancel_anim()
    return mp.panel


def _docked(edge: str, state: str = "playing", frac: float = 0.4, bloom: float | None = 0.3):
    def setup(mp: MiniPlayer) -> None:
        mp.layout = {"dock": edge, "x": 0.0, "y": 0.5}
        mp._update(_reading(state, position=frac * 334.0))
        mp.sliver.preview_moment(bloom=bloom if state == "playing" else None)
    return setup


def _hovered(edge: str):
    def setup(mp: MiniPlayer) -> None:
        mp.layout = {"dock": edge, "x": 0.0, "y": 0.5}
        mp._update(_reading())
        mp._open_pill()
        mp._cancel_anim()
        mp._set_scale(1.0, animate=False)
    return setup


def _idle(menu: bool):
    def setup(mp: MiniPlayer) -> None:
        mp.dictation_layout = {"dock": "bottom", "y": -1.0}
        mp.show_idle()
        if menu:
            mp._open_menu()
            mp._cancel_anim()
            mp._set_scale(1.0, animate=False)
    return setup


def _dictating(edge: str, state: str):
    """The dictate form on `edge` in `state`, its growth landed and its wave a few frames in at a
    middling microphone level, held still for the picture."""
    def setup(mp: MiniPlayer) -> None:
        mp.dictation_layout = {"dock": edge, "y": -1.0}
        mp._level = 0.5
        mp._set_dictation("recording")
        mp._set_dictation(state)
        mp._set_hands_free(state == "locked", animate=False)
        mp.wave.stop()
        mp.wave._last = mp.wave._breath_start = 0.0
        for i in range(1, 13):
            mp.wave.tick(i / 30)
    return setup


def _scrubbing(mp: MiniPlayer) -> None:
    mp._update(_reading())
    mp.scrub.set_hover(True)
    mp.scrub.scrubbing = True
    mp.scrub.bar.set_fraction(0.62)
    mp.scrub._layout()
    mp._scrub_preview(0.62)


def _drop_zone() -> QWidget:
    zone = DropZone()
    zone.order_front()
    return zone.panel


PREVIEWS = {
    "player.card": lambda: _preview(lambda mp: mp._update(_reading())),
    "player.card-paused": lambda: _preview(lambda mp: mp._update(_reading("paused"))),
    "player.card-loading": lambda: _preview(lambda mp: mp._update(_reading("loading", 0, 0, 0.0, 0.0, 1.0))),
    "player.card-scrubbing": lambda: _preview(_scrubbing),
    "player.card-long": lambda: _preview(lambda mp: mp._update(_reading(index=128, total=240, position=1834.0,
                                                                        duration=3460.0, speed=1.75))),
    "player.sliver": lambda: _preview(_docked("right")),
    "player.sliver-paused": lambda: _preview(_docked("right", "paused")),
    "player.sliver-bottom": lambda: _preview(_docked("bottom", frac=0.65)),
    "player.pill": lambda: _preview(_hovered("right")),
    "player.pill-flat": lambda: _preview(_hovered("bottom")),
    "player.idle": lambda: _preview(_idle(False)),
    "player.idle-menu": lambda: _preview(_idle(True)),
    "player.dictate": lambda: _preview(_dictating("right", "recording")),
    "player.dictate-flat": lambda: _preview(_dictating("bottom", "recording")),
    "player.dictate-hands-free": lambda: _preview(_dictating("right", "locked")),
    "player.dictate-hands-free-flat": lambda: _preview(_dictating("bottom", "locked")),
    "player.dictate-working": lambda: _preview(_dictating("right", "working")),
    "player.dictate-working-flat": lambda: _preview(_dictating("bottom", "working")),
    "player.drop-zone": _drop_zone,
}
