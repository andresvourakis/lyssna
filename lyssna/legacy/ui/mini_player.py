"""Transport card for the reading: a compact floating player with progress and controls:

    ┌────────────────────────────────────────────────┐
    │                                       (→|) (×) │  tuck into the side, close
    │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │  progress bar (click or drag to scrub)
    │  1:24              3 of 9                -4:10 │  times, chunk
    │                                                │
    │  (S)        ⏮      ( ▶ )      ⏭         1.2×  │  voice badge, prev/next sentence, speed
    └────────────────────────────────────────────────┘

Drag it anywhere; it never docks by being dragged. Its dock button tucks it into the nearest
left, right or bottom edge of the screen as a slim capsule that shows only progress. The
capsule can be dragged to another edge and docks there. Hover the capsule and
it grows into a pill of controls (close, chevron, prev, play/pause, next, progress), upright
on a side edge and lying flat along the bottom; move away and it shrinks back. Dragging it out of the edge, clicking the chevron, or
clicking the pill's background restores the card. Dock and position persist across
readings, and the player always shows up on the screen you selected text on.

It is also the one Lyssna pill for dictation (Andres's rule: never two pills on screen). One
window, one form in it at a time, every switch through _show_form:

    mode        form
    idle        rest: the slim sliver on a screen edge, neutral (nothing, ui.idle_pill off);
                while it sits in view and still, its wave ripples about every WAVE_EVERY
                seconds (not with ui.wave off or Reduce Motion, see may_wave); with ui.auto_hide
                it is out of sight until the cursor comes near its spot, then slides in from the
                edge, and slides away again once the cursor leaves;
                menu: hovered, it grows into dictate, read selection and history buttons
    reading     card, or sliver / pill when docked (as above)
    dictating   dictate: a capsule with the wave meter; hands-free it grows a cancel and a
                finish button; while the speech is turned into text the wave breathes

Auto-hide only ever hides the rest form: the window stays where the sliver rests and the wave
slides inside it, so the slide never shows on a neighbouring display. Whenever the rest form
comes out for any other reason than the cursor (launch, a reading or dictation ending, a drop,
auto-hide switched on), it lingers for LINGER_SECS so a new user sees where it lives
(near_frame and tuck_due hold these rules, without AppKit).

Dictation wins: when one starts during a reading the reading pauses and its form goes away,
and when it ends the reading form comes back where it was, still paused. The dictation forms
always sit on a screen edge: the edge the player is docked to, else the idle pill's own spot,
else the bottom centre (pick_form and dictation_spot hold these rules, without AppKit).

The three docks are one system. Every docked form is laid out along its long axis (`along`,
parallel to the screen edge) and across it (`across`, away from the edge): upright on the
left and right edges, lying flat on the bottom. lay(), oriented(), docked_frame() and
edge_frame() turn that into frames, so each form has one builder for both orientations.
The window keeps one set of views per orientation (built once) and _show_form swaps in the
set the form's edge needs. A position along an edge is saved as a fraction of that edge.

Colors are the brand cream and ink from theme.py, dynamic, so the card follows
light/dark mode automatically."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable

import AppKit
import objc
import Quartz
from AppKit import NSAnimationContext, NSEvent, NSMenu, NSMenuItem
from Foundation import NSOperationQueue, NSRunLoop, NSRunLoopCommonModes, NSTimer, NSValue

from ...player import SPEEDS, Status
from ...ui.layout.pill import (
    AUTO_HIDE_POLL,
    BADGE,
    BUTTON_HIT,
    CANCEL_D,
    CANCEL_TOP,
    CAP_W,
    CAPSULE_MAIN_H,  # noqa: F401  (re-exported)
    CLOSE,
    CLOSE_GAP,
    CLOSE_TOP,
    COMPACT_H,
    DICTATING,
    DISC,
    DOCK_BAR,
    DOCK_GAP,
    DOCK_GAP_V,
    DOCK_H,  # noqa: F401  (re-exported)
    DOCK_PLAY,
    DOCK_SKIP,
    DOCK_W,
    DOCKS,
    FINISH_BOTTOM,
    FINISH_D,
    FREE_H,
    GROW_SECS,
    HIDE_DELAY,  # noqa: F401  (re-exported)
    HOVER_OUT_DELAY,
    HOVER_POLL,
    HOVER_SECS,
    LINGER_SECS,
    MARGIN,
    MENU_DISC,
    MENU_H,
    MENU_MIC_H,
    MENU_MIC_W,
    MENU_W,
    PAD,
    PILL_H,
    PILL_W,
    PLAY,
    REVEAL_REACH,  # noqa: F401  (re-exported)
    SCRUB_H,
    SKIP,
    SLIDE_SECS,
    SNAP,  # noqa: F401  (re-exported)
    TIP_DELAY,
    WAVE_EVERY,
    WAVE_TOLERANCE,
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
from . import theme
from .mainthread import later, on_main
from .panel import (
    FloatingPanel,
    TintedView,
    clamp_to,
    label,
    screen_at,
    screen_under_mouse,
    set_symbol,
    symbol_button,
    visible_frame,
)
from .toast import Tip
from .wave import WAVE_H, WAVE_W, WaveView
from .widgets import (
    BAR_H,
    CAPSULE_H,
    CAPSULE_INSET,
    CAPSULE_W,
    SLIVER_H,
    SLIVER_W,
    HoverView,
    ScrubBar,
    Sliver,
    clear,
    white_glow,
)

log = logging.getLogger("lyssna.ui.player")

CHEVRONS = {"left": "chevron.right", "right": "chevron.left", "bottom": "chevron.up"}
ARROWS = {"left": "left", "right": "right", "bottom": "down"}  # arrow.<x>.to.line.compact


def _vf_of(f) -> tuple[float, float, float, float]:
    return (f.origin.x, f.origin.y, f.size.width, f.size.height)


def _vf(screen) -> tuple[float, float, float, float]:
    return _vf_of(visible_frame(screen))


class DropZone(FloatingPanel):
    """Faint content-coloured pill shown on a side edge while a drag hovers near it."""

    width = CAPSULE_W + 8
    height = CAPSULE_H + 8
    corner_radius = (CAPSULE_W + 8) / 2

    @objc.python_method
    def make_surface(self, rect, radius):
        return TintedView.make(rect, theme.content(0.28), radius, theme.CONTENT_DIM)

    def init(self):
        self = objc.super(DropZone, self).init()
        if self is None:
            return None
        self.panel.setHasShadow_(False)
        self.panel.setIgnoresMouseEvents_(True)
        return self


class FlatDropZone(DropZone):
    """The same hint lying flat along the bottom edge."""

    width = CAPSULE_H + 8
    height = CAPSULE_W + 8


# The views that exist once per orientation; _orient points these attributes at one set.
ORIENTED = ("pill", "pill_host", "dock_scrub", "chevron", "sliver", "rest", "menu", "menu_host", "menu_dictate",
            "menu_read", "menu_history", "menu_hovers", "dictate_host", "dictate_capsule", "wave",
            "cancel_bg", "cancel_disc", "cancel_btn", "finish_bg", "finish_disc", "finish_btn")


class MiniPlayer(FloatingPanel):
    width = 400.0
    height = CLOSE_TOP + CLOSE + CLOSE_GAP + BAR_H + 8 + 14 + 16 + PLAY + PAD  # 156
    corner_radius = 22.0
    adaptive = True    # brand surface colour, follows light/dark mode
    draggable = True

    def init(self):
        self = objc.super(MiniPlayer, self).init()
        if self is None:
            return None
        self.actions: dict[str, Callable] = {}
        self.layout = {"dock": "", "x": -1.0, "y": -1.0}  # last resting place; see place()
        self.dock = ""          # "" (card) | "left" | "right" | "bottom" (docked: sliver at rest, pill on hover)
        self.form = "card"      # card | sliver | pill: which content view is showing
        self._session = 0
        self._speed = 1.0
        self._status: Status | None = None
        self._scale = 1.0
        self._anchor = (0.0, 0.0)  # (edge coord, centre along the edge) the docked forms hang off
        self._watch = None  # hover poll while the pill is open
        self._last_chunk = (None, -1)  # (session, index) last shown, to bloom the sliver per sentence
        self._tip = ""
        self._pending_done = None  # completion token of the running scale animation
        self._dragging = False
        self.status_fn: Callable[[], Status] | None = None  # reader's live status, so a drain shows the truth
        self.voices_fn: Callable[[], tuple[list, str]] | None = None  # ([(id, label)], current id) for the badge menu
        self._latest: Status | None = None  # newest snapshot waiting for the main thread
        self._latest_lock = threading.Lock()
        self._drain_pending = False
        self.card = self.panel.contentView()
        self.zone = DropZone.alloc().init()
        self.flat_zone = FlatDropZone.alloc().init()

        # Dictation
        self.idle_pill = True                         # ui.idle_pill: the resting sliver when nothing else shows
        self.key_name = "fn"                          # the dictation key, as shown in the tips
        self.read_key = ""                            # the read-selection hotkey, as shown in its tip
        self.dictation_layout = {"dock": "", "y": -1.0}  # the idle pill's own spot (player not docked)
        self.dictation_state = "idle"                 # the session state the window shows
        self.hands_free = False
        self._level = 0.0                             # latest microphone level; the wave timer reads it
        self._dlock = threading.Lock()
        self._dqueued = "idle"                        # newest state handed to the main thread
        self._dpending = False
        self._edge = "bottom"                         # the edge the idle and dictation forms are on
        self._reading_spot = None                     # where the reading form was when a dictation took over
        self._tip_token = None
        self.tip = Tip.alloc().init()
        self._rest_anchor = (0.0, 0.0)                # (edge coord, centre along) of the resting sliver
        self.auto_hide = True                         # ui.auto_hide: the resting sliver hides until the cursor comes near
        self.rest_wave = True                         # ui.wave: the resting wave ripples now and then
        self._tucked = False                          # the resting sliver is hidden, or sliding out, by auto-hide
        self._auto_watch = None                       # the cursor poll while idle with auto-hide on
        self._away_since: float | None = None         # when the cursor left the sliver's surroundings
        self._linger_until = 0.0                      # the sliver stays out at least until then
        self._slide_token = None                      # completion token of the running slide
        self._was_inside = False                      # the cursor was on the sliver at the last auto-hide tick
        self._wave_timer = None                       # the resting wave's wave, while the rest form is up
        self._kits = {flat: self._build_kit(flat) for flat in (False, True)}
        self._flat = None
        self._orient(False)
        return self

    # -- building --------------------------------------------------------------------

    @objc.python_method
    def build(self, content):
        W, H = self.width, self.height
        self.buttons: dict[str, list] = {}
        self.hovers: list = []  # HoverViews, so glows can be reset when a form goes away
        primary = theme.CONTENT  # dynamic: label/button tints re-resolve per appearance on their own
        secondary = theme.CONTENT_DIM

        # Close button: its own strip in the top-right corner, with a faint disc so it
        # reads as a target (same tint as the speed pill).
        close_bg = HoverView.make(((W - PAD - CLOSE, H - CLOSE_TOP - CLOSE), (CLOSE, CLOSE)), theme.FILL_SOFT, CLOSE / 2)
        content.addSubview_(close_bg)
        close = symbol_button("xmark", self, "stop:", size=11.0, tint=secondary, weight=AppKit.NSFontWeightBold)
        close.setFrame_(((0, 0), (CLOSE, CLOSE)))
        close.setToolTip_("Stop")
        close_bg.addSubview_(close)
        self._button("stop", close)

        # Dock button beside it: collapses the card to the nearest screen edge (the arrow
        # points at the side it will go to). Dragging the card never docks it, so this is the way.
        dock_bg = HoverView.make(((W - PAD - CLOSE - 8 - CLOSE, H - CLOSE_TOP - CLOSE), (CLOSE, CLOSE)), theme.FILL_SOFT, CLOSE / 2)
        content.addSubview_(dock_bg)
        dock = symbol_button("arrow.right.to.line.compact", self, "dockSide:", size=11.0, tint=secondary, weight=AppKit.NSFontWeightBold)
        dock.setFrame_(((0, 0), (CLOSE, CLOSE)))
        dock.setToolTip_("Tuck into the side of the screen")
        dock_bg.addSubview_(dock)
        self.dock_btn = dock

        # Progress bar with a generous hit strip around it
        bar_y = H - CLOSE_TOP - CLOSE - CLOSE_GAP - BAR_H
        self.scrub = ScrubBar.alloc().initWithFrame_(((PAD, bar_y - (SCRUB_H - BAR_H) / 2), (W - 2 * PAD, SCRUB_H)))
        self.scrub.on_scrub = self._scrub_preview
        self.scrub.on_seek = lambda f: self._fire("seek_to", f)
        self.scrub.setToolTip_("Drag to scrub")
        content.addSubview_(self.scrub)

        # Times row: elapsed | chunk | remaining
        row_y = bar_y - 8 - 14
        self.elapsed = label("0:00", size=11.0, color=secondary)
        self.elapsed.setAlignment_(AppKit.NSTextAlignmentLeft)
        self.elapsed.setFrame_(((PAD, row_y), (80, 14)))
        content.addSubview_(self.elapsed)

        self.progress = label("", size=11.0, color=secondary)
        self.progress.setFrame_(((W / 2 - 60, row_y), (120, 14)))
        content.addSubview_(self.progress)

        self.remaining = label("-0:00", size=11.0, color=secondary)
        self.remaining.setAlignment_(AppKit.NSTextAlignmentRight)
        self.remaining.setFrame_(((W - PAD - 80, row_y), (80, 14)))
        content.addSubview_(self.remaining)

        # Transport row
        y = PAD
        cx = W / 2

        # Persona badge: the voice's initial. A click opens the voice menu (when the engine has one).
        badge = HoverView.make(((PAD, y + (PLAY - BADGE) / 2), (BADGE, BADGE)), theme.FILL_SOFT, BADGE / 2)
        content.addSubview_(badge)
        self.hovers.append(badge)
        self.badge_text = label("S", size=14.0, color=primary, weight=AppKit.NSFontWeightSemibold, mono=False)
        self.badge_text.setFrame_(((0, (BADGE - 17) / 2), (BADGE, 17)))
        badge.addSubview_(self.badge_text)
        badge_btn = AppKit.NSButton.buttonWithTitle_target_action_("", self, "voiceMenu:")
        badge_btn.setBordered_(False)
        badge_btn.setFrame_(((0, 0), (BADGE, BADGE)))
        badge.addSubview_(badge_btn)  # on top of the letter, so it takes the clicks
        self.badge = badge
        self.badge_btn = badge_btn
        self._badge_tip = ""

        back = symbol_button("backward.end.fill", self, "back:", size=17.0, tint=primary)
        self._hoverable(content, back, ((cx - PLAY / 2 - 22 - SKIP, y + (PLAY - SKIP) / 2), (SKIP, SKIP)))
        self._button("back", back)

        circle = HoverView.make(((cx - PLAY / 2, y), (PLAY, PLAY)), theme.CONTENT, PLAY / 2, glow=white_glow)
        content.addSubview_(circle)
        play = symbol_button("pause.fill", self, "toggle:", size=20.0, tint=theme.SURFACE)
        play.setFrame_(((0, 0), (PLAY, PLAY)))
        circle.addSubview_(play)
        self._button("toggle", play)

        fwd = symbol_button("forward.end.fill", self, "forward:", size=17.0, tint=primary)
        self._hoverable(content, fwd, ((cx + PLAY / 2 + 22, y + (PLAY - SKIP) / 2), (SKIP, SKIP)))
        self._button("forward", fwd)

        pill_bg = HoverView.make(((W - PAD - PILL_W, y + (PLAY - PILL_H) / 2), (PILL_W, PILL_H)), theme.FILL_SOFT, PILL_H / 2)
        content.addSubview_(pill_bg)
        pill = AppKit.NSButton.buttonWithTitle_target_action_("1.0×", self, "speedMenu:")
        pill.setBordered_(False)
        pill.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(12.0, AppKit.NSFontWeightSemibold))
        pill.setContentTintColor_(primary)
        pill.setFrame_(((0, 0), (PILL_W, PILL_H)))
        pill_bg.addSubview_(pill)
        self.speed_btn = pill
        self._button("speed", pill)

    @objc.python_method
    def _build_kit(self, flat: bool) -> dict:
        """Every docked form for one orientation, then a snapshot of their attributes."""
        edge = "bottom" if flat else "left"
        self.pill = AppKit.NSView.alloc().initWithFrame_(((0, 0), oriented(pill_len(flat), DOCK_W, edge)))
        self.pill.setWantsLayer_(True)
        self.build_pill(self.pill, flat)
        # The pill sits inside a transparent host that is the window's content view, so
        # the pill's own layer can be scaled with Core Animation (AppKit owns the content
        # view's layer and would fight a transform on it).
        self.pill_host = AppKit.NSView.alloc().initWithFrame_(((0, 0), oriented(pill_len(flat), DOCK_W, edge)))
        self.pill_host.setWantsLayer_(True)
        self.pill_host.addSubview_(self.pill)
        self.sliver = Sliver.alloc().initWithFrame_(((0, 0), oriented(SLIVER_H, SLIVER_W, edge)))
        self._track(self.sliver)
        self.rest = Sliver.alloc().initWithFrame_(((0, 0), oriented(SLIVER_H, SLIVER_W, edge)))
        self.rest.set_rest(True)
        self._track(self.rest)
        self.menu = AppKit.NSView.alloc().initWithFrame_(((0, 0), oriented(MENU_H, MENU_W, edge)))
        self.menu.setWantsLayer_(True)
        self.build_menu(self.menu, flat)
        self.menu_host = AppKit.NSView.alloc().initWithFrame_(((0, 0), oriented(MENU_H, MENU_W, edge)))  # see pill_host
        self.menu_host.setWantsLayer_(True)
        self.menu_host.addSubview_(self.menu)
        self.dictate_host = AppKit.NSView.alloc().initWithFrame_(((0, 0), oriented(FREE_H, CAP_W, edge)))
        self.dictate_host.setWantsLayer_(True)
        self.build_dictate(self.dictate_host, flat)
        return {name: getattr(self, name) for name in ORIENTED}

    @objc.python_method
    def _orient(self, flat: bool) -> None:
        """Point the docked-form attributes at the upright or the flat set of views."""
        if flat == self._flat:
            return
        self._flat = flat
        for name, view in self._kits[flat].items():
            setattr(self, name, view)

    @objc.python_method
    def build_pill(self, content, flat: bool):
        """The open docked layout: separate discs for close and expand beside the main
        capsule, all inside one transparent container so they scale together. Upright, top to
        bottom: close, expand, prev, play, next, progress; flat, the same left to right."""
        T = DOCK_W
        c = T / 2
        L = pill_main_len(flat)
        total = pill_len(flat)
        primary = theme.CONTENT
        secondary = theme.CONTENT_DIM

        main = self.make_surface(lay(0, L, 0, T, total, flat, flip=True), T / 2)
        content.addSubview_(main)

        a = 12.0
        if flat:  # the progress bar lies along the capsule
            frame = ((L - a - DOCK_BAR, (T - SCRUB_H) / 2), (DOCK_BAR, SCRUB_H))
            a += DOCK_BAR + 10
        else:
            frame = ((c - DOCK_BAR / 2, a - (SCRUB_H - BAR_H) / 2), (DOCK_BAR, SCRUB_H))
            a += BAR_H + 10
        self.dock_scrub = ScrubBar.alloc().initWithFrame_(frame)
        self.dock_scrub.knob_size = 10.0
        self.dock_scrub.on_scrub = self._scrub_preview
        self.dock_scrub.on_seek = lambda f: self._fire("seek_to", f)
        main.addSubview_(self.dock_scrub)

        fwd = symbol_button("forward.end.fill", self, "forward:", size=12.0, tint=primary)
        self._hoverable(main, fwd, lay(a, DOCK_SKIP, c - DOCK_SKIP / 2, DOCK_SKIP, L, flat, flip=True))
        self._button("forward", fwd)
        a += DOCK_SKIP + 4

        circle = HoverView.make(lay(a, DOCK_PLAY, c - DOCK_PLAY / 2, DOCK_PLAY, L, flat, flip=True), theme.CONTENT,
                                DOCK_PLAY / 2, glow=white_glow)
        self.hovers.append(circle)
        main.addSubview_(circle)
        play = symbol_button("pause.fill", self, "toggle:", size=14.0, tint=theme.SURFACE)
        play.setFrame_(((0, 0), (DOCK_PLAY, DOCK_PLAY)))
        circle.addSubview_(play)
        self._button("toggle", play)
        a += DOCK_PLAY + 4

        back = symbol_button("backward.end.fill", self, "back:", size=12.0, tint=primary)
        self._hoverable(main, back, lay(a, DOCK_SKIP, c - DOCK_SKIP / 2, DOCK_SKIP, L, flat, flip=True))
        self._button("back", back)

        def disc(a, name, action, tip):
            bg = self.make_surface(lay(a, DISC, c - DISC / 2, DISC, total, flat, flip=True), DISC / 2, hover=True)
            self.hovers.append(bg)
            content.addSubview_(bg)
            btn = symbol_button(name, self, action, size=11.0, tint=secondary, weight=AppKit.NSFontWeightBold)
            btn.setFrame_(((0, 0), (DISC, DISC)))
            btn.setToolTip_(tip)
            bg.addSubview_(btn)
            return btn

        a = L + DOCK_GAP_V
        self.chevron = disc(a, "chevron.right", "expand:", "Expand player")
        a += DISC + DOCK_GAP_V
        self._button("stop", disc(a, "xmark", "stop:", "Stop"))

        self._track(content)

    @objc.python_method
    def build_menu(self, content, flat: bool):
        """Idle, hovered: what grows out of the resting sliver. Upright, bottom to top; flat,
        left to right: a dictate capsule, a read-selection disc and a history disc, centred on
        one axis, in one transparent container (scaled as a unit, like the docked reading pill)."""
        c = MENU_W / 2
        self.menu_hovers = []

        def item(a, length, thick, symbol, size, tint, action, tip, key_fn, weight=AppKit.NSFontWeightSemibold):
            frame = lay(a, length, c - thick / 2, thick, MENU_H, flat)
            bg = self.make_surface(frame, thick / 2, hover=True)
            content.addSubview_(bg)
            btn = symbol_button(symbol, self, action, size=size, tint=tint, weight=weight)
            btn.setFrame_(((0, 0), frame[1]))
            btn.setToolTip_(None)  # the tooltip pill replaces the system tooltip
            bg.addSubview_(btn)
            bg.on_hover = lambda on: self._hover_tip(on, bg, tip, key_fn)
            self.menu_hovers.append(bg)
            return bg

        a = 0.0
        self.menu_dictate = item(a, MENU_MIC_H, MENU_MIC_W, "mic.fill", 14.0, theme.CONTENT, "menuDictate:", "Dictate",
                                 lambda: self.key_name)
        a += MENU_MIC_H + DOCK_GAP_V
        self.menu_read = item(a, MENU_DISC, MENU_DISC, "play.fill", 10.0, theme.CONTENT_DIM, "menuRead:",
                              "Read selection", lambda: self.read_key)
        a += MENU_DISC + DOCK_GAP_V
        self.menu_history = item(a, MENU_DISC, MENU_DISC, "clock.arrow.circlepath", 11.0, theme.CONTENT_DIM,
                                 "menuHistory:", "History", lambda: "")
        self._track(content)

    @objc.python_method
    def build_dictate(self, host, flat: bool):
        """Dictating: a capsule centred in a FREE_H long transparent host. Compact it is
        COMPACT_H long with only the wave; hands-free it grows to FREE_H about its centre and
        shows cancel (top, or left when flat) and finish (bottom, or right). The buttons hang
        off the capsule's ends (autoresizing), so they ride out with it as it grows and the
        capsule clips them. Each button's click area is a strip BUTTON_HIT long across the
        whole capsule, around its smaller disc."""
        T = CAP_W
        capsule = self.make_surface(lay((FREE_H - COMPACT_H) / 2, COMPACT_H, 0, T, FREE_H, flat), T / 2)
        capsule.setAutoresizesSubviews_(True)
        host.addSubview_(capsule)

        wave = WaveView.alloc().initWithFrame_(
            lay((COMPACT_H - WAVE_H) / 2, WAVE_H, (T - WAVE_W) / 2, WAVE_W, COMPACT_H, flat))
        wave.horizontal = flat
        wave.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin if flat
                                  else AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)
        wave.level_fn = lambda: self._level
        capsule.addSubview_(wave)

        def button(disc_along, d, fill, glow, symbol, size, tint, action, tip, key, mask):
            """A BUTTON_HIT long strip (the click area) with a d-point disc drawn in it, the disc
            at `disc_along` on the capsule's upright axis, and the button covering the strip."""
            strip_frame = lay(disc_along - (BUTTON_HIT - d) / 2, BUTTON_HIT, 0, T, COMPACT_H, flat, flip=True)
            strip = AppKit.NSView.alloc().initWithFrame_(strip_frame)
            strip.setAutoresizingMask_(mask)
            disc = HoverView.make(lay((BUTTON_HIT - d) / 2, d, (T - d) / 2, d, BUTTON_HIT, flat), fill, d / 2, glow=glow)
            strip.addSubview_(disc)
            btn = symbol_button(symbol, self, action, size=size, tint=tint, weight=AppKit.NSFontWeightBold)
            btn.setFrame_(((0, 0), strip_frame[1]))
            btn.setToolTip_(None)
            strip.addSubview_(btn)  # above the disc, so the whole strip takes the click
            disc.on_hover = lambda on: self._hover_tip(on, disc, tip, key)
            capsule.addSubview_(strip)
            return strip, disc, btn

        # Each keeps its distance to its own end of the capsule as it grows.
        self.cancel_bg, self.cancel_disc, self.cancel_btn = button(
            COMPACT_H - CANCEL_TOP - CANCEL_D, CANCEL_D, theme.FILL_SOFT, None, "xmark", 9.0, theme.CONTENT_DIM,
            "dictateCancel:", "Cancel", lambda: "esc", AppKit.NSViewMaxXMargin if flat else AppKit.NSViewMinYMargin)
        self.finish_bg, self.finish_disc, self.finish_btn = button(
            FINISH_BOTTOM, FINISH_D, theme.CONTENT, white_glow, "checkmark", 11.0, theme.SURFACE,
            "dictateFinish:", "Finish", lambda: self.key_name, AppKit.NSViewMinXMargin if flat else AppKit.NSViewMaxYMargin)
        for b in (self.cancel_bg, self.finish_bg):
            b.setAlphaValue_(0.0)
            b.setHidden_(True)
        self.dictate_capsule, self.wave = capsule, wave

    @objc.python_method
    def make_surface(self, rect, radius: float, hover: bool = False):
        if not hover:
            return objc.super(MiniPlayer, self).make_surface(rect, radius)
        return HoverView.make(rect, theme.surface(0.97), radius, theme.HAIRLINE)

    @objc.python_method
    def _hoverable(self, parent, btn, frame):
        """Sit a bare button inside a transparent HoverView so it lights up under the cursor."""
        w, h = frame[1]
        bg = HoverView.make(frame, clear, min(w, h) / 2)
        bg.addSubview_(btn)
        btn.setFrame_(((0, 0), (w, h)))
        parent.addSubview_(bg)
        self.hovers.append(bg)
        return bg

    @objc.python_method
    def _track(self, view) -> None:
        opts = (AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveAlways | AppKit.NSTrackingInVisibleRect)
        view.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(view.bounds(), opts, self, None))

    @objc.python_method
    def _button(self, name: str, btn) -> None:
        self.buttons.setdefault(name, []).append(btn)

    @objc.python_method
    def set_tooltips(self, tips: dict[str, str]) -> None:
        for name, tip in tips.items():
            for btn in self.buttons.get(name, []):
                btn.setToolTip_(tip)

    # -- actions -----------------------------------------------------------------

    @objc.python_method
    def _fire(self, name, *args):
        cb = self.actions.get(name)
        if cb:
            cb(*args)

    def back_(self, sender):
        self._fire("back")

    def toggle_(self, sender):
        self._fire("toggle")

    def forward_(self, sender):
        self._fire("forward")

    def stop_(self, sender):
        self._fire("stop")
        self._update(Status())  # hide right away, even if the reader was already idle

    def expand_(self, sender):
        self.expand()

    def menuDictate_(self, sender):
        self._close_menu_now()
        self._fire("dictate")

    def menuRead_(self, sender):
        self._close_menu_now()
        self._fire("read_selection")

    def menuHistory_(self, sender):
        self._close_menu_now()
        self._fire("history")

    def dictateCancel_(self, sender):
        self._hide_tip()
        self._fire("dictate_cancel")

    def dictateFinish_(self, sender):
        self._hide_tip()
        self._fire("dictate_stop")

    def dockSide_(self, sender):
        """Collapse the card onto the nearest edge of the screen it's on."""
        if self.dock:
            return
        edge, screen, along = self._nearest_edge()
        self._collapse(edge, screen, along)
        self._remember()

    @objc.python_method
    def _nearest_edge(self):
        f = self.panel.frame()
        cx, cy = f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2
        screen = screen_at(cx, cy)
        edge = nearest_edge(_vf(screen), cx, cy)
        return edge, screen, cx if is_flat(edge) else cy

    @objc.python_method
    def _refresh_dock_arrow(self) -> None:
        edge, _, _ = self._nearest_edge()
        set_symbol(self.dock_btn, f"arrow.{ARROWS[edge]}.to.line.compact", 11.0, AppKit.NSFontWeightBold)

    def speedMenu_(self, sender):
        menu = NSMenu.alloc().initWithTitle_("Speed")
        for s in SPEEDS:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(f"{s:g}×", "pickSpeed:", "")
            item.setTarget_(self)
            item.setRepresentedObject_(s)
            item.setState_(1 if abs(s - self._speed) < 0.01 else 0)
            menu.addItem_(item)
        origin = (0, sender.bounds().size.height + 4)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, origin, sender)

    def pickSpeed_(self, sender):
        self._fire("set_speed", float(sender.representedObject()))

    def voiceMenu_(self, sender):
        voices, current = self._voices()
        if not voices:
            return
        menu = NSMenu.alloc().initWithTitle_("Voice")
        for vid, text in voices:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(text, "pickVoice:", "")
            item.setTarget_(self)
            item.setRepresentedObject_(vid)
            item.setState_(1 if vid == current else 0)
            menu.addItem_(item)
        origin = (0, sender.bounds().size.height + 4)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, origin, sender)

    def pickVoice_(self, sender):
        self._fire("set_voice", str(sender.representedObject()))

    @objc.python_method
    def _voices(self) -> tuple[list, str]:
        if self.voices_fn is None:
            return [], ""
        try:
            return self.voices_fn()
        except Exception:
            log.exception("voices_fn failed")
            return [], ""

    @objc.python_method
    def _scrub_preview(self, f: float) -> None:
        st = self._status
        if st and st.duration > 0:
            self.elapsed.setStringValue_(_fmt(f * st.duration))
            self.remaining.setStringValue_("-" + _fmt((1 - f) * st.duration))

    # -- hover: sliver <-> pill -------------------------------------------------------

    def mouseEntered_(self, event):
        log.debug("mouseEntered form=%s dock=%s dragging=%s", self.form, self.dock, self._dragging)
        # Only grow into the pill for a reading that is still on screen: a late enter event
        # after the reading ended would otherwise bring the player back from the dead.
        if (self.dock and self.form == "sliver" and not self._dragging
                and self._status is not None and self.visible()):
            self._open_pill()
        elif (self.form == "rest" and not self._dragging and self._status is None
              and self.dictation_state not in DICTATING and self.visible() and not self._tucked):
            self._open_menu()

    def mouseExited_(self, event):
        pass  # closing is decided by _watch_hover: exit events fire spuriously while the pill animates

    @objc.python_method
    def _mouse_inside(self, margin: float = 6.0) -> bool:
        p = NSEvent.mouseLocation()
        f = self.panel.frame()
        return (f.origin.x - margin <= p.x <= f.origin.x + f.size.width + margin
                and f.origin.y - margin <= p.y <= f.origin.y + f.size.height + margin)

    @objc.python_method
    def _watch_hover(self) -> None:
        """While the pill is open, poll the cursor; shrink back once it has been outside
        for HOVER_OUT_DELAY. Polling is immune to the enter/exit churn that window resizes
        cause in tracking areas."""
        if self._watch is not None:
            self._watch.invalidate()
        state = {"outside_since": None}

        def tick(_timer):
            if self.form not in ("pill", "menu") or self._dragging:
                state["outside_since"] = None
                return
            for hv in (self.hovers if self.form == "pill" else self.menu_hovers):
                hv.sync()
            if self._mouse_inside():
                state["outside_since"] = None
                return
            now = time.monotonic()
            if state["outside_since"] is None:
                state["outside_since"] = now
            elif now - state["outside_since"] >= HOVER_OUT_DELAY:
                self._stop_watch()
                if self.form == "menu":
                    self._close_menu()
                else:
                    self._close_pill()

        self._watch = NSTimer.timerWithTimeInterval_repeats_block_(HOVER_POLL, True, tick)
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._watch, NSRunLoopCommonModes)

    @objc.python_method
    def _stop_watch(self) -> None:
        if self._watch is not None:
            self._watch.invalidate()
            self._watch = None

    @objc.python_method
    def form_edge(self, name: str | None = None) -> str:
        """The edge a form sits on: the dock for the reading's docked forms, the idle and
        dictation edge for those; "" for the card."""
        name = name or self.form
        if name in ("sliver", "pill"):
            return self.dock
        if name in ("rest", "menu", "dictate"):
            return self._edge
        return ""

    @objc.python_method
    def _show_form(self, name: str) -> None:
        """The one way the window changes form: put that form's view in and nothing else.
        A docked form comes from the view set for its edge's orientation."""
        if name != "dictate":
            for kit in self._kits.values():
                kit["wave"].stop()
        if name != "menu":
            for kit in self._kits.values():
                for hv in kit["menu_hovers"]:
                    hv.set_hover(False)
        edge = self.form_edge(name)
        if edge:
            self._orient(is_flat(edge))
        views = {"card": self.card, "pill": self.pill_host, "sliver": self.sliver,
                 "rest": self.rest, "menu": self.menu_host, "dictate": self.dictate_host}
        self._hide_tip()
        self._tucked = False  # a new form is a shown form; only _tuck hides the rest form
        self._slide_token = None  # and a slide still running belongs to the old one
        if name not in ("rest", "menu"):
            self._stop_auto_watch()  # a reading or a dictation owns the window: nothing to auto-hide
        if name == "rest":
            self._start_wave_timer()  # (re)shown: the countdown starts over
        else:
            self._stop_wave_timer()
        self.form = name
        self.draggable = name != "dictate"  # a drag would fight push-to-talk
        self.panel.setContentView_(views[name])

    @objc.python_method
    def _open_pill(self) -> None:
        self._show_form("pill")
        length = pill_len(is_flat(self.dock))
        self.panel.setFrame_display_(self._docked_frame(length, DOCK_W, DOCK_GAP), True)
        self.pill.setFrame_(((0, 0), oriented(length, DOCK_W, self.dock)))
        self._set_scale(SLIVER_H / length, animate=False)
        self.order_front()
        self._set_scale(1.0, animate=True)
        self._watch_hover()

    @objc.python_method
    def _close_pill(self) -> None:
        log.debug("close_pill form=%s dragging=%s", self.form, self._dragging)
        if self.form != "pill" or self._dragging:
            return
        # A pill that closes after the reading ended goes back to the sliver, but hidden.
        self._set_scale(SLIVER_H / pill_len(is_flat(self.dock)), animate=True,
                        done=lambda: self._show_sliver(show=self._status is not None))

    @objc.python_method
    def _open_menu(self) -> None:
        """The resting sliver grows into the idle menu, exactly as the docked reading sliver
        grows into its pill."""
        self._show_form("menu")
        self.panel.setFrame_display_(docked_frame(self._edge, *self._rest_anchor, MENU_H, MENU_W, DOCK_GAP), True)
        self.menu.setFrame_(((0, 0), oriented(MENU_H, MENU_W, self._edge)))
        self._set_scale(SLIVER_H / MENU_H, animate=False)
        self.order_front()
        self._set_scale(1.0, animate=True)
        self._watch_hover()

    @objc.python_method
    def _close_menu(self) -> None:
        if self.form != "menu" or self._dragging:
            return
        # The cursor closed it, so it is near: back to the sliver without a linger.
        self._set_scale(SLIVER_H / MENU_H, animate=True, done=lambda: self.show_idle(linger=False))

    @objc.python_method
    def _close_menu_now(self) -> None:
        """An item was clicked: back to the resting sliver at once, before its action runs."""
        if self.form == "menu":
            self._cancel_anim()
            self.show_idle(linger=False)

    @objc.python_method
    def _show_sliver(self, show: bool = True) -> None:
        if show and self.form == "pill" and self._dragging:
            return  # mid-drag the pill stays open; resetting the form while hiding is fine
        for hv in self.hovers:
            hv.set_hover(False)
        self._show_form("sliver")
        # Frame first, bounds second: AppKit keeps a view's bounds/frame ratio across frame
        # changes, so setting the bounds while the old (card-sized) frame is live would
        # leave the capsule drawn at a fraction of its size.
        self.panel.setFrame_display_(self._docked_frame(SLIVER_H, SLIVER_W, 0.0), True)
        self.sliver.setBoundsSize_(oriented(SLIVER_H, SLIVER_W, self.dock))
        if show:
            self.order_front()
        self._refresh_sliver()

    @objc.python_method
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

    @objc.python_method
    def _cancel_anim(self) -> None:
        self._stop_watch()
        self._pending_done = None
        for kit in self._kits.values():
            kit["pill"].layer().removeAllAnimations()
            kit["menu"].layer().removeAllAnimations()

    @objc.python_method
    def _scaled(self):
        """The view the hover animation scales, its size, and the edge it grows from: the idle
        menu while that form is up, else the docked reading pill."""
        if self.form == "menu":
            return (self.menu, *oriented(MENU_H, MENU_W, self._edge), self._edge)
        return (self.pill, *oriented(pill_len(is_flat(self.dock)), DOCK_W, self.dock or "left"), self.dock)

    @objc.python_method
    def _transform(self, s: float):
        """Scale about the middle of the side that meets the screen edge. AppKit pins
        backing layers' anchorPoint at (0, 0), so fold the anchor into a translation:
        T = translate(a - s·a) ∘ scale(s)."""
        _, w, h, edge = self._scaled()
        if is_flat(edge):
            ax, ay = w / 2, 0.0
        else:
            ax, ay = (0.0 if edge == "left" else w), h / 2
        t = Quartz.CATransform3DMakeTranslation(ax * (1 - s), ay * (1 - s), 0)
        return Quartz.CATransform3DScale(t, s, s, 1)

    @objc.python_method
    def _set_scale(self, s: float, animate: bool, done: Callable[[], None] | None = None) -> None:
        """Set the pill's layer scale, optionally animating from the current value on the
        render server (smooth, and no per-frame Python). `done` runs when it finishes."""
        layer = self._scaled()[0].layer()
        target = self._transform(s)
        # Start from where the pill visibly is: mid-flight that's the presentation layer,
        # otherwise the model value (the presentation layer lags a frame behind it).
        pres = layer.presentationLayer()
        current = pres.transform() if (pres is not None and layer.animationForKey_("scale") is not None) else layer.transform()
        log.debug("set_scale %.2f animate=%s", s, animate)
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        layer.removeAnimationForKey_("scale")
        if animate:
            anim = Quartz.CABasicAnimation.animationWithKeyPath_("transform")
            anim.setFromValue_(NSValue.valueWithCATransform3D_(current))
            anim.setToValue_(NSValue.valueWithCATransform3D_(target))
            anim.setDuration_(HOVER_SECS)
            anim.setTimingFunction_(Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseOut))
            token = object()
            self._pending_done = token

            def finished(*_):
                log.debug("scale animation finished (%.2f)", s)
                if self._pending_done is token:
                    self._pending_done = None
                    self.panel.invalidateShadow()
                    if done:
                        done()
            Quartz.CATransaction.setCompletionBlock_(finished)
            layer.addAnimation_forKey_(anim, "scale")
        layer.setTransform_(target)
        Quartz.CATransaction.commit()
        self._scale = s
        if not animate:
            self.panel.invalidateShadow()
            if done:
                done()

    @objc.python_method
    def _docked_frame(self, length: float, across: float, gap: float):
        """Frame of a form `length` long and `across` deep hanging off the dock anchor,
        `gap` points in from the edge."""
        return docked_frame(self.dock, *self._anchor, length, across, gap)

    # -- dragging / docking ---------------------------------------------------------

    @objc.python_method
    def drag_began(self) -> None:
        log.debug("drag_began form=%s dock=%s scale=%.2f", self.form, self.dock, self._scale)
        self._dragging = True
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

    @objc.python_method
    def drag_moved(self, mx: float, my: float) -> None:
        if self.form in ("rest", "menu"):
            self._hint_rest(mx, my)  # it follows the mouse; on release it snaps to the nearer edge
            return
        if not self.dock:
            return  # the card moves freely; only its dock button tucks it into an edge
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
        zone.show_at(x, y, screen=screen)
        self.order_front()

    @objc.python_method
    def _hide_zones(self) -> None:
        self.zone.hide()
        self.flat_zone.hide()

    @objc.python_method
    def drag_ended(self, mx: float, my: float) -> None:
        log.debug("drag_ended at (%.0f, %.0f) form=%s dock=%s", mx, my, self.form, self.dock)
        self._dragging = False
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
            f = self.panel.frame()
            vf = visible_frame(screen)
            self.panel.setFrameOrigin_(clamp_to(vf, f.origin.x, f.origin.y, self.width, self.height))
            self._refresh_dock_arrow()
        if self._status is None:  # the reading ended while the drag was still live
            self.hide()
        self._remember()  # the new dock/position sticks either way

    @objc.python_method
    def clicked(self) -> None:
        if self.form == "rest":
            self._open_menu()  # a click on the sliver opens the menu too (hover usually has)
        elif self.dock:
            self.expand()

    @objc.python_method
    def expand(self) -> None:
        """Chevron / click: open the card next to where the docked player sits."""
        if not self.dock:
            return
        (x, y), (w, h) = docked_frame(self.dock, *self._anchor, self.width if is_flat(self.dock) else self.height,
                                      self.height if is_flat(self.dock) else self.width, DOCK_GAP)
        self._expand_at(x + w / 2, y + h / 2)
        self._remember()

    @objc.python_method
    def _centre_along(self, edge: str) -> float:
        """The window's centre along `edge`: x for the bottom, y for the sides."""
        f = self.panel.frame()
        return f.origin.x + f.size.width / 2 if is_flat(edge) else f.origin.y + f.size.height / 2

    @objc.python_method
    def _edge_near(self, mx: float, my: float):
        """Which screen edge (if any) the panel is close enough to for docking."""
        screen = screen_at(mx, my)
        f = self.panel.frame()
        frame = ((f.origin.x, f.origin.y), (f.size.width, f.size.height))
        return snap_edge(_vf(screen), frame), screen

    @objc.python_method
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
            set_symbol(kit["chevron"], CHEVRONS[edge], 11.0, AppKit.NSFontWeightBold)
        self._show_sliver()

    @objc.python_method
    def _expand_at(self, cx: float, cy: float) -> None:
        """Show the card centred on (cx, cy), clamped to that screen."""
        self._cancel_anim()
        self.dock = ""
        self._scale = 1.0
        screen = screen_at(cx, cy)
        x, y = clamp_to(visible_frame(screen), cx - self.width / 2, cy - self.height / 2, self.width, self.height)
        self._show_form("card")
        self.panel.setFrame_display_(((x, y), (self.width, self.height)), True)
        self.order_front()
        self._refresh_dock_arrow()

    @objc.python_method
    def _remember(self) -> None:
        """Persist dock + position as fractions of the screen's usable area, so the same
        spot maps onto any screen size."""
        f = self.panel.frame()
        vf = visible_frame(screen_at(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2))
        if self.dock:  # docked: y is the position along the edge, whichever edge
            fx, fy = 0.0, along_fraction(_vf_of(vf), self.dock, self._anchor[1])
        else:
            fx = (f.origin.x - vf.origin.x) / max(1.0, vf.size.width - self.width)
            fy = (f.origin.y - vf.origin.y) / max(1.0, vf.size.height - self.height)
        self.layout = {"dock": self.dock, "x": min(1.0, max(0.0, fx)), "y": min(1.0, max(0.0, fy))}
        self._fire("layout", self.dock, self.layout["x"], self.layout["y"])

    @objc.python_method
    def place(self, screen=None) -> None:
        """Put the player where it was last left, on `screen` (default: the screen the
        mouse is on, i.e. where the text was just selected)."""
        screen = screen or screen_under_mouse()
        vf = visible_frame(screen)
        lay = self.layout
        if lay.get("dock") in DOCKS:
            self._collapse(lay["dock"], screen, along_centre(_vf_of(vf), lay["dock"], float(lay.get("y", -1.0))))
            self._log_placed(screen)
            return
        if lay.get("x", -1) >= 0 and lay.get("y", -1) >= 0:
            x = vf.origin.x + lay["x"] * (vf.size.width - self.width)
            y = vf.origin.y + lay["y"] * (vf.size.height - self.height)
        else:
            x = vf.origin.x + vf.size.width - self.width - MARGIN
            y = vf.origin.y + MARGIN
        self._expand_at(x + self.width / 2, y + self.height / 2)
        self._log_placed(screen)

    @objc.python_method
    def _log_placed(self, screen) -> None:
        """One breadcrumb per reading: where the player ended up, and on which screen."""
        f = self.panel.frame()
        log.info("player placed dock=%s form=%s screen=%s frame=(%.0f, %.0f, %.0f, %.0f)", self.dock or "none",
                 self.form, screen.localizedName(), f.origin.x, f.origin.y, f.size.width, f.size.height)

    # -- state -------------------------------------------------------------------

    @objc.python_method
    def update(self, st: Status) -> None:
        """Snapshots arrive on the reader's threads. Keep only the newest and drain it once
        on the main thread: queuing every snapshot lets a stale one (an old session's idle)
        land after a newer one and hide a player that should be up."""
        with self._latest_lock:
            self._latest = st
            if self._drain_pending:
                return
            self._drain_pending = True
        on_main(self._drain)

    @objc.python_method
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

    @objc.python_method
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
            set_symbol(btn, icon, 20.0 if i == 0 else 14.0)
        self._speed = st.speed
        self.speed_btn.setTitle_(f"{st.speed:g}×")
        self.progress.setStringValue_("Loading…" if st.state == "loading" else f"{st.index} of {st.total}")
        if not (self.scrub.scrubbing or any(k["dock_scrub"].scrubbing for k in self._kits.values())):
            self.elapsed.setStringValue_(_fmt(st.position))
            self.remaining.setStringValue_("-" + _fmt(max(0.0, st.duration - st.position)))
        frac = st.position / st.duration if st.duration > 0 else 0.0
        self.scrub.set_fraction(frac)
        for kit in self._kits.values():
            kit["dock_scrub"].set_fraction(frac)
        voice = st.provider.split("·")[-1].strip() if st.provider else ""
        self.badge_text.setStringValue_((voice[:1] or "S").upper())
        self.badge.setToolTip_(st.provider)
        pickable = bool(self._voices()[0])
        self.badge_btn.setEnabled_(pickable)
        self.badge.glow.setHidden_(not pickable)  # no hover light on a badge that does nothing
        badge_tip = "Change voice" if pickable else st.provider
        if badge_tip != self._badge_tip:  # same tracking-area churn as the pill tooltip below
            self._badge_tip = badge_tip
            self.badge_btn.setToolTip_(badge_tip)
        tip = f"{st.index} of {st.total}  ·  -{_fmt(max(0.0, st.duration - st.position))}"
        if tip != self._tip:  # rewriting a tooltip churns tracking areas; only do it when the text changes
            self._tip = tip
            for kit in self._kits.values():
                kit["pill"].setToolTip_(tip)
                kit["sliver"].setToolTip_(tip)
        self._refresh_sliver()
        # A mouse-up that never arrived would leave the flag set and place() skipped forever.
        if self._dragging and not (NSEvent.pressedMouseButtons() & 1):
            log.warning("drag flag stuck; clearing")
            self._dragging = False
            self.panel._press = None
            self.panel._dragging = False
        if self.dictation_state in DICTATING:
            return  # the dictation owns the window; the reading form comes back when it ends
        if st.session != self._session or not self.visible() or self.form in ("rest", "menu"):
            self._session = st.session
            if self.form in ("rest", "menu") and self._dragging:
                self._dragging = False  # a reading took over mid-drag of the idle pill
                self.panel._press = None
                self.panel._dragging = False
            if not self._dragging:
                self.place()

    # -- dictation -------------------------------------------------------------------

    @objc.python_method
    def show_idle(self, linger: bool = True) -> None:
        """Nothing playing and nothing being dictated: the resting sliver on its edge, or nothing.
        With auto-hide it slides in (unless it is out already) and the watch takes over; `linger`
        keeps it out for LINGER_SECS, for every caller but the cursor closing the menu."""
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
        self._place_rest(_vf(screen_under_mouse()), edge, fy)
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

    @objc.python_method
    def _place_rest(self, vf, edge: str, frac: float) -> None:
        self._edge = edge
        self._show_form("rest")
        frame = edge_frame(vf, edge, frac, SLIVER_H, SLIVER_W, inset=0.0)
        self.panel.setFrame_display_(frame, True)
        self.rest.setBoundsSize_(oriented(SLIVER_H, SLIVER_W, edge))  # after the frame: see _show_sliver
        self.rest.set_state(0.0, False, edge)
        self._rest_anchor = anchor_of(frame, edge)
        self._slide_rest(True, animate=False)  # fully in; show_idle and _reveal slide it from there

    @objc.python_method
    def set_auto_hide(self, on: bool) -> None:
        """Switch auto-hide live. Switched on, the sliver lingers before it first slides away,
        so the change is visible; switched off, it simply stays out."""
        self.auto_hide = bool(on)
        if not on:
            self._stop_auto_watch()
        if self.form in ("rest", "menu") or (self._status is None and self.dictation_state not in DICTATING):
            self.show_idle()

    @objc.python_method
    def set_wave(self, on: bool) -> None:
        """Switch the resting wave live. The countdown keeps running and may_wave says no;
        switched off, a wave in flight is cut back to the resting shape."""
        self.rest_wave = bool(on)
        if not on:
            for kit in self._kits.values():
                kit["rest"].stop_wave()

    # -- auto-hide -------------------------------------------------------------------

    @objc.python_method
    def _now(self) -> float:
        return time.monotonic()  # replaced in tests

    @objc.python_method
    def _cursor(self) -> tuple[float, float]:
        p = NSEvent.mouseLocation()  # replaced in tests
        return p.x, p.y

    @objc.python_method
    def _start_auto_watch(self) -> None:
        """Poll the cursor while idle with auto-hide on, like _watch_hover: tracking areas
        cannot see a cursor approaching a window that is not on screen."""
        if self._auto_watch is not None:
            return
        self._auto_watch = NSTimer.timerWithTimeInterval_repeats_block_(AUTO_HIDE_POLL, True,
                                                                         lambda _timer: self._auto_tick())
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._auto_watch, NSRunLoopCommonModes)

    @objc.python_method
    def _stop_auto_watch(self) -> None:
        if self._auto_watch is not None:
            self._auto_watch.invalidate()
            self._auto_watch = None
        self._away_since = None

    @objc.python_method
    def _auto_tick(self) -> None:
        """Hidden: slide in when the cursor comes near the spot on the screen it is on. Shown:
        slide out once tuck_due says so; never while the menu is open or during a drag."""
        if (not self.auto_hide or not self.idle_pill or self._status is not None
                or self.dictation_state in DICTATING or self.form not in ("rest", "menu")):
            return
        px, py = self._cursor()
        if self._tucked:
            self._was_inside = False
            if self._dragging:
                return
            edge, fy = dictation_spot(self.layout, self.dictation_layout)
            vf = _vf(screen_at(px, py))
            if near_frame(px, py, edge_frame(vf, edge, fy, SLIVER_H, SLIVER_W, inset=0.0)):
                self._reveal(vf, edge, fy)
            return
        if self.form != "rest" or self._dragging:
            self._away_since = None
            self._was_inside = True  # leaving the menu or a drop: not an arrival on the sliver
            return
        f = self.panel.frame()
        frame = ((f.origin.x, f.origin.y), (f.size.width, f.size.height))
        inside = near_frame(px, py, frame, reach=0.0)
        arrived, self._was_inside = inside and not self._was_inside, inside
        due, self._away_since = tuck_due(near_frame(px, py, frame), self._now(), self._away_since,
                                         self._linger_until)
        if due:
            self._tuck()
        elif arrived and not (NSEvent.pressedMouseButtons() & 1):
            # Slid in under the cursor: no enter event may come, so the arrival opens the menu here.
            self._open_menu()

    @objc.python_method
    def _reveal(self, vf, edge: str, frac: float) -> None:
        """The cursor came near: slide the sliver in. Caught mid slide-out at the same spot it
        turns around from where it is; anywhere else it is placed afresh."""
        target = edge_frame(vf, edge, frac, SLIVER_H, SLIVER_W, inset=0.0)
        f = self.panel.frame()
        here = ((f.origin.x, f.origin.y), (f.size.width, f.size.height)) == target
        if not (here and self.visible() and self.form == "rest"):
            self._place_rest(vf, edge, frac)
            self._slide_rest(False, animate=False)
            self.order_front()
        self._tucked = False
        self._away_since = None
        self._slide_rest(True, animate=True)
        self._start_wave_timer()

    @objc.python_method
    def _tuck(self) -> None:
        """Slide the sliver out of the edge, then take the window off screen."""
        self._tucked = True
        self._away_since = None
        self._stop_wave_timer()
        self._hide_tip()
        self._slide_rest(False, animate=True, done=self._tucked_away)

    @objc.python_method
    def _tucked_away(self) -> None:
        if self._tucked and self.form == "rest":
            self.hide()

    @objc.python_method
    def _slide_rest(self, inward: bool, animate: bool, done: Callable[[], None] | None = None) -> None:
        """Slide the resting wave in from (or out past) its screen edge, fading as it goes. It
        moves inside the window, which stays put and clips it at the edge, so the wave never
        shows on a display beside this one. The slide is the rest view's sublayerTransform and
        the wave's opacity: AppKit owns the view's own layer geometry, and the Sliver's
        updateLayer resets the wave's position and transform but neither of these."""
        host, wave = self.rest.layer(), self.rest.squiggle
        out = SLIVER_W
        dx, dy = {"left": (-out, 0.0), "right": (out, 0.0), "bottom": (0.0, -out)}[self._edge]
        target = Quartz.CATransform3DIdentity if inward else Quartz.CATransform3DMakeTranslation(dx, dy, 0)
        opacity = 1.0 if inward else 0.0
        moving = host.animationForKey_("slide") is not None
        pres, wpres = host.presentationLayer(), wave.presentationLayer()
        current = pres.sublayerTransform() if (moving and pres is not None) else host.sublayerTransform()
        current_opacity = wpres.opacity() if (moving and wpres is not None) else wave.opacity()
        token = object() if animate else None
        self._slide_token = token
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        host.removeAnimationForKey_("slide")
        wave.removeAnimationForKey_("fade")
        if animate:
            timing = Quartz.CAMediaTimingFunction.functionWithName_(
                Quartz.kCAMediaTimingFunctionEaseOut if inward else Quartz.kCAMediaTimingFunctionEaseIn)
            slide = Quartz.CABasicAnimation.animationWithKeyPath_("sublayerTransform")
            slide.setFromValue_(NSValue.valueWithCATransform3D_(current))
            slide.setToValue_(NSValue.valueWithCATransform3D_(target))
            fade = Quartz.CABasicAnimation.animationWithKeyPath_("opacity")
            fade.setFromValue_(current_opacity)
            fade.setToValue_(opacity)
            for anim in (slide, fade):
                anim.setDuration_(SLIDE_SECS)
                anim.setTimingFunction_(timing)

            def finished(*_):
                if self._slide_token is token:
                    self._slide_token = None
                    self.panel.invalidateShadow()
                    if done:
                        done()
            Quartz.CATransaction.setCompletionBlock_(finished)
            host.addAnimation_forKey_(slide, "slide")
            wave.addAnimation_forKey_(fade, "fade")
        host.setSublayerTransform_(target)
        wave.setOpacity_(opacity)
        Quartz.CATransaction.commit()
        if not animate:
            self.panel.invalidateShadow()
            if done:
                done()

    # -- the resting wave's wave -----------------------------------------------------------

    @objc.python_method
    def _reduce_motion(self) -> bool:
        return bool(AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())  # replaced in tests

    @objc.python_method
    def _start_wave_timer(self) -> None:
        """(Re)start the countdown to the next wave. A timer firing one short animation each
        time, not one endless animation: between waves the window server has nothing to draw."""
        self._stop_wave_timer()
        self._wave_timer = NSTimer.timerWithTimeInterval_repeats_block_(WAVE_EVERY, True,
                                                                        lambda _timer: self._wave_tick())
        self._wave_timer.setTolerance_(WAVE_TOLERANCE)
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._wave_timer, NSRunLoopCommonModes)

    @objc.python_method
    def _stop_wave_timer(self) -> None:
        """Stop the countdown and cut a wave in flight back to the resting shape."""
        if self._wave_timer is not None:
            self._wave_timer.invalidate()
            self._wave_timer = None
        for kit in self._kits.values():
            kit["rest"].stop_wave()

    @objc.python_method
    def _wave_tick(self) -> None:
        if may_wave(self.form, self.visible(), self._tucked, self._slide_token is not None, self._dragging,
                    self._status is not None, self.dictation_state, self._reduce_motion(), self.rest_wave):
            self.rest.wave()

    @objc.python_method
    def set_idle_pill(self, on: bool) -> None:
        self.idle_pill = bool(on)
        if self.form in ("rest", "menu") or (on and self._status is None and self.dictation_state not in DICTATING):
            self.show_idle()

    @objc.python_method
    def _rest_landing(self, mx: float, my: float):
        """Where the dragged idle pill lands if released now: (screen, vf, edge, fraction along
        it), the nearest edge of the screen under the mouse to the panel's centre. The drop and
        the hint while dragging both ask here, so they always agree."""
        screen = screen_at(mx, my)
        vf = _vf(screen)
        f = self.panel.frame()
        cx, cy = f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2
        edge = nearest_edge(vf, cx, cy)
        return screen, vf, edge, along_fraction(vf, edge, cx if is_flat(edge) else cy)

    @objc.python_method
    def _hint_rest(self, mx: float, my: float) -> None:
        """While the idle pill is dragged, the faint hint around the spot it would land on: the
        same edge and place along it as the resting sliver's capsule, kept on screen the same way."""
        screen, vf, edge, fy = self._rest_landing(mx, my)
        zone, other = (self.flat_zone, self.zone) if is_flat(edge) else (self.zone, self.flat_zone)
        other.hide()
        coord, centre = anchor_of(edge_frame(vf, edge, fy, SLIVER_H, SLIVER_W, inset=0.0), edge)
        (x, y), _ = docked_frame(edge, coord, centre,
                                 *((zone.width, zone.height) if is_flat(edge) else (zone.height, zone.width)),
                                 CAPSULE_INSET - 4)
        zone.show_at(x, y, screen=screen)
        self.order_front()

    @objc.python_method
    def _drop_rest(self, mx: float, my: float) -> None:
        """The idle pill was dragged: snap it to the nearest edge of the screen it was dropped
        on (left, right or bottom), at that spot along it, as the resting sliver, and remember
        the spot (shared with the docked player)."""
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

    @objc.python_method
    def dictation_update(self, ds) -> None:
        """A DictationStatus, on any thread, up to 20 times a second while recording. The level
        is only stored (the wave's display timer reads it); the main thread hears about a state
        change once, however many updates arrive before it gets there."""
        self._level = float(ds.level)
        with self._dlock:
            if ds.state == self._dqueued:
                return
            self._dqueued = ds.state
            if self._dpending:
                return
            self._dpending = True
        NSOperationQueue.mainQueue().addOperationWithBlock_(self._drain_dictation)  # never inline: may be in the key tap

    @objc.python_method
    def _drain_dictation(self) -> None:
        with self._dlock:
            state = self._dqueued
            self._dpending = False
        self._set_dictation(state)

    @objc.python_method
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

    @objc.python_method
    def _enter_dictation(self) -> None:
        """Take the window for the dictate form, remembering where the reading form was."""
        self._cancel_anim()
        self._hide_zones()
        if self._dragging:
            self._dragging = False
            self.panel._press = None
            self.panel._dragging = False
        self._reading_spot = None
        if self._status is not None and self.visible() and self.form in ("card", "sliver", "pill"):
            f = self.panel.frame()
            self._reading_spot = (self.dock, (f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2), self._anchor[1])
        edge, fy = dictation_spot(self.layout, self.dictation_layout)
        self._edge = edge
        self._show_form("dictate")
        self._set_hands_free(False, animate=False)
        self.panel.setFrame_display_(edge_frame(_vf(screen_under_mouse()), edge, fy, FREE_H, CAP_W), True)
        self.wave.reset()
        self.wave.start()
        self.order_front()

    @objc.python_method
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
            self._collapse(spot[0], screen_at(*spot[1]), spot[2])
        else:
            self._expand_at(*spot[1])

    @objc.python_method
    def _set_hands_free(self, on: bool, animate: bool) -> None:
        """Grow the capsule to FREE_H with cancel and finish (or shrink it back), about its centre."""
        if on == self.hands_free and animate:
            return
        self.hands_free = on
        if not on:
            self._hide_tip()
        length = FREE_H if on else COMPACT_H
        frame = lay((FREE_H - length) / 2, length, 0, CAP_W, FREE_H, self._flat)
        buttons = (self.cancel_bg, self.finish_bg)
        if on:
            for b in buttons:
                b.setHidden_(False)

        def settled(*_) -> None:
            if not self.hands_free:
                for b in buttons:
                    b.setHidden_(True)
            self.panel.invalidateShadow()

        if not animate:
            self.dictate_capsule.setFrame_(frame)
            for b in buttons:
                b.setAlphaValue_(1.0 if on else 0.0)
            settled()
            return

        def changes(ctx) -> None:
            ctx.setDuration_(GROW_SECS)
            ctx.setTimingFunction_(Quartz.CAMediaTimingFunction.functionWithName_(Quartz.kCAMediaTimingFunctionEaseOut))
            self.dictate_capsule.animator().setFrame_(frame)
            for b in buttons:
                b.animator().setAlphaValue_(1.0 if on else 0.0)
        NSAnimationContext.runAnimationGroup_completionHandler_(changes, settled)

    @objc.python_method
    def toast_anchor(self) -> tuple[tuple[float, float, float, float], str] | None:
        """What a toast sits beside (lyssna.ui.layout.toast.toast_origin): the pill's frame (x, y, w, h)
        and the edge it is docked on, while it is on screen on an edge; else None."""
        edge = self.form_edge()
        if not self.visible() or not edge:
            return None
        f = self.panel.frame()
        return (f.origin.x, f.origin.y, f.size.width, f.size.height), edge

    # -- tooltip pill ------------------------------------------------------------------

    @objc.python_method
    def _hover_tip(self, on: bool, view, text: str, key: Callable[[], str] | None = None) -> None:
        if not on:
            self._hide_tip()
            return
        if self._dragging or self.form not in ("menu", "dictate"):
            return
        token = object()
        self._tip_token = token
        later(TIP_DELAY, self._show_tip, token, view, text, key)

    @objc.python_method
    def _show_tip(self, token, view, text: str, key: Callable[[], str] | None) -> None:
        window = view.window()
        if token is not self._tip_token or self._dragging or window is None or not window.isVisible() or view.isHiddenOrHasHiddenAncestor():
            return
        r = window.convertRectToScreen_(view.convertRect_toView_(view.bounds(), None))
        self.tip.show_beside((r.origin.x, r.origin.y, r.size.width, r.size.height), self._edge, text,
                             key() if callable(key) else (key or ""))

    @objc.python_method
    def _hide_tip(self) -> None:
        self._tip_token = None
        if self.tip.visible():
            self.tip.hide()
