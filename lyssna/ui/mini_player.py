"""Transport card for the reading: a compact floating player with progress and controls:

    ┌────────────────────────────────────────────────┐
    │                                       (→|) (×) │  tuck into the side, close
    │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │  progress bar (click or drag to scrub)
    │  1:24              3 of 9                -4:10 │  times, chunk
    │                                                │
    │  (S)        ⏮      ( ▶ )      ⏭         1.2×  │  voice badge, prev/next sentence, speed
    └────────────────────────────────────────────────┘

Drag it anywhere. Drop it on the left or right edge of a screen and it docks there as a
slim capsule against the screen edge that shows only progress. Hover the capsule and
it grows into a vertical pill of controls (close, chevron, prev, play/pause, next, progress);
move away and it shrinks back. Dragging it out of the edge, clicking the chevron, or
clicking the pill's background restores the card. Dock and position persist across
readings, and the player always shows up on the screen you selected text on.

It is also the one Lyssna pill for dictation (Andres's rule: never two pills on screen). One
window, one form in it at a time, every switch through _show_form:

    mode        form
    idle        rest: the slim sliver on a screen edge, neutral (nothing, ui.idle_pill off);
                menu: hovered, it grows into dictate, read selection and history buttons
    reading     card, or sliver / pill when docked (as above)
    dictating   dictate: a capsule with the wave meter; hands-free it grows a cancel and a
                finish button; while the speech is turned into text the wave breathes

Dictation wins: when one starts during a reading the reading pauses and its form goes away,
and when it ends the reading form comes back where it was, still paused. The dictation forms
always sit on a screen edge: the edge the player is docked to, else the mic button's own spot,
else the right edge (pick_form and dictation_spot hold these rules, without AppKit).

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

from ..player import SPEEDS, Status
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

MARGIN = 16.0
PAD = 18.0

SCRUB_H = 22.0    # hit area around the 4pt bar
PLAY = 50.0
SKIP = 34.0
BADGE = 32.0
PILL_W, PILL_H = 56.0, 30.0
CLOSE = 26.0      # close button hit area
CLOSE_TOP = 10.0  # inset from the card's top edge; right edge aligns with the content column (PAD)
CLOSE_GAP = 10.0  # room between the close disc and the progress bar

# Open dock (a vertical stack hugging the screen edge): a close disc, an expand disc, then the
# main capsule with prev / play / next and a sliver of progress.
DOCK_W = 46.0
DOCK_PLAY = 34.0
DOCK_SKIP = 26.0
DOCK_BAR = 24.0   # width of the little progress bar
DISC = 30.0       # the small stand-alone buttons
DOCK_GAP_V = 6.0  # between the discs and the capsule
CAPSULE_MAIN_H = 12 + BAR_H + 10 + DOCK_SKIP + 4 + DOCK_PLAY + 4 + DOCK_SKIP + 10  # 130
DOCK_H = CAPSULE_MAIN_H + (DOCK_GAP_V + DISC) * 2  # 202
DOCK_GAP = 6.0    # distance from the screen edge when the control pill is open
SNAP = 48.0       # drop within this many points of a screen edge to dock

HOVER_SECS = 0.18
HOVER_OUT_DELAY = 0.08  # seconds the cursor must be away before the open pill shrinks back
HOVER_POLL = 0.03

# Idle: at rest the slim sliver (SLIVER_W x SLIVER_H, flush with the edge); hovered, a stack of
# a dictate capsule and two discs, DOCK_GAP in from the edge.
MENU_MIC_W, MENU_MIC_H = 40.0, 56.0
MENU_W = MENU_MIC_W
MENU_H = MENU_MIC_H + (DOCK_GAP_V + DISC) * 2  # 128
# Dictating: a capsule CAP_W wide, CAPSULE_INSET from the screen edge.
CAP_W = 34.0
COMPACT_H = 60.0            # the dictate capsule while holding the key, and while working
FREE_H = 118.0              # hands-free: with cancel at the top and finish at the bottom
CANCEL_D, CANCEL_TOP = 22.0, 6.0
FINISH_D, FINISH_BOTTOM = 24.0, 5.0
BUTTON_HIT = 28.0           # the click area of cancel and finish, taller than their discs
GROW_SECS = 0.18
TIP_DELAY = 0.4             # hover this long before the tooltip pill shows
DICTATING = ("recording", "locked", "working")


def _fmt(seconds: float) -> str:
    seconds = max(0, round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def pick_form(reading: str, dictation: str, docked: bool, idle_pill: bool) -> str:
    """The form the pill shows: "dictate" while a dictation records or works (it wins over a
    reading), "card" or "sliver" during a reading, else the resting sliver "rest" (which opens
    into the idle menu on hover), or "" for nothing at all."""
    if dictation in DICTATING:
        return "dictate"
    if reading != "idle":
        return "sliver" if docked else "card"
    return "rest" if idle_pill else ""


def dictation_spot(layout: dict, own: dict) -> tuple[str, float]:
    """Which edge and height (a fraction of the screen, -1 for the middle) the dictation
    forms use: the docked player's, else the idle pill's own saved spot, else the right edge."""
    if layout.get("dock") in ("left", "right"):
        return layout["dock"], float(layout.get("y", -1.0))
    edge = own.get("dock")
    return (edge if edge in ("left", "right") else "right"), float(own.get("y", -1.0))


def edge_frame(vf: tuple[float, float, float, float], edge: str, fy: float, w: float, h: float,
               inset: float = CAPSULE_INSET):
    """Frame ((x, y), (w, h)) of a w x h form on `edge` of the visible frame vf (x, y, w, h),
    `inset` in from the edge (0 for the resting sliver, whose window is flush with the edge and
    draws its capsule CAPSULE_INSET in). fy places its centre the way the dock's saved position
    does (so it lines up with a docked player); below zero it is centred."""
    vx, vy, vw, vh = vf
    cy = vy + fy * (vh - DOCK_H) + DOCK_H / 2 if fy >= 0 else vy + vh / 2
    y = min(max(cy - h / 2, vy), vy + vh - h)
    x = vx + inset if edge == "left" else vx + vw - inset - w
    return ((x, y), (w, h))


def spot_fraction(vf: tuple[float, float, float, float], cy: float) -> float:
    """The inverse of edge_frame's height: a centre y as the saved fraction, 0..1."""
    _, vy, _, vh = vf
    return min(1.0, max(0.0, (cy - DOCK_H / 2 - vy) / max(1.0, vh - DOCK_H)))


def _vf(screen) -> tuple[float, float, float, float]:
    f = visible_frame(screen)
    return (f.origin.x, f.origin.y, f.size.width, f.size.height)


class DropZone(FloatingPanel):
    """Faint content-coloured pill shown on a screen edge while a drag hovers near it."""

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
        self.dock = ""          # "" (card) | "left" | "right" (docked: sliver at rest, pill on hover)
        self.form = "card"      # card | sliver | pill: which content view is showing
        self._session = 0
        self._speed = 1.0
        self._status: Status | None = None
        self._scale = 1.0
        self._anchor = (0.0, 0.0)  # (screen edge x, centre y) the docked forms hang off
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
        self.pill = AppKit.NSView.alloc().initWithFrame_(((0, 0), (DOCK_W, DOCK_H)))
        self.pill.setWantsLayer_(True)
        self.build_pill(self.pill)
        # The pill sits inside a transparent host that is the window's content view, so
        # the pill's own layer can be scaled with Core Animation (AppKit owns the content
        # view's layer and would fight a transform on it).
        self.pill_host = AppKit.NSView.alloc().initWithFrame_(((0, 0), (DOCK_W, DOCK_H)))
        self.pill_host.setWantsLayer_(True)
        self.pill_host.addSubview_(self.pill)
        self.sliver = Sliver.alloc().initWithFrame_(((0, 0), (SLIVER_W, SLIVER_H)))
        self._track(self.sliver)
        self.zone = DropZone.alloc().init()

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
        self._edge = "right"                          # the edge the dictation forms are on
        self._reading_spot = None                     # where the reading form was when a dictation took over
        self._tip_token = None
        self.tip = Tip.alloc().init()
        self._rest_anchor = (0.0, 0.0)                # (screen edge x, centre y) of the resting sliver
        self.rest = Sliver.alloc().initWithFrame_(((0, 0), (SLIVER_W, SLIVER_H)))
        self.rest.set_rest(True)
        self._track(self.rest)
        self.menu = AppKit.NSView.alloc().initWithFrame_(((0, 0), (MENU_W, MENU_H)))
        self.menu.setWantsLayer_(True)
        self.build_menu(self.menu)
        self.menu_host = AppKit.NSView.alloc().initWithFrame_(((0, 0), (MENU_W, MENU_H)))  # see pill_host
        self.menu_host.setWantsLayer_(True)
        self.menu_host.addSubview_(self.menu)
        self.dictate_host = AppKit.NSView.alloc().initWithFrame_(((0, 0), (CAP_W, FREE_H)))
        self.build_dictate(self.dictate_host)
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
        # points at the side it will go to), so dragging isn't the only way to tuck it away.
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
    def build_pill(self, content):
        """The open docked layout: separate discs for close and expand, stacked above the
        main capsule, all inside one transparent container so they scale together."""
        W = DOCK_W
        cx = W / 2
        primary = theme.CONTENT
        secondary = theme.CONTENT_DIM

        main = self.make_surface(((0, 0), (W, CAPSULE_MAIN_H)), W / 2)
        content.addSubview_(main)

        y = 12.0
        self.dock_scrub = ScrubBar.alloc().initWithFrame_(((cx - DOCK_BAR / 2, y - (SCRUB_H - BAR_H) / 2), (DOCK_BAR, SCRUB_H)))
        self.dock_scrub.knob_size = 10.0
        self.dock_scrub.on_scrub = self._scrub_preview
        self.dock_scrub.on_seek = lambda f: self._fire("seek_to", f)
        main.addSubview_(self.dock_scrub)
        y += BAR_H + 10

        fwd = symbol_button("forward.end.fill", self, "forward:", size=12.0, tint=primary)
        self._hoverable(main, fwd, ((cx - DOCK_SKIP / 2, y), (DOCK_SKIP, DOCK_SKIP)))
        self._button("forward", fwd)
        y += DOCK_SKIP + 4

        circle = HoverView.make(((cx - DOCK_PLAY / 2, y), (DOCK_PLAY, DOCK_PLAY)), theme.CONTENT, DOCK_PLAY / 2, glow=white_glow)
        self.hovers.append(circle)
        main.addSubview_(circle)
        play = symbol_button("pause.fill", self, "toggle:", size=14.0, tint=theme.SURFACE)
        play.setFrame_(((0, 0), (DOCK_PLAY, DOCK_PLAY)))
        circle.addSubview_(play)
        self._button("toggle", play)
        y += DOCK_PLAY + 4

        back = symbol_button("backward.end.fill", self, "back:", size=12.0, tint=primary)
        self._hoverable(main, back, ((cx - DOCK_SKIP / 2, y), (DOCK_SKIP, DOCK_SKIP)))
        self._button("back", back)

        def disc(y, name, action, tip):
            bg = self.make_surface(((cx - DISC / 2, y), (DISC, DISC)), DISC / 2, hover=True)
            self.hovers.append(bg)
            content.addSubview_(bg)
            btn = symbol_button(name, self, action, size=11.0, tint=secondary, weight=AppKit.NSFontWeightBold)
            btn.setFrame_(((0, 0), (DISC, DISC)))
            btn.setToolTip_(tip)
            bg.addSubview_(btn)
            return btn

        y = CAPSULE_MAIN_H + DOCK_GAP_V
        self.chevron = disc(y, "chevron.right", "expand:", "Expand player")
        y += DISC + DOCK_GAP_V
        self._button("stop", disc(y, "xmark", "stop:", "Stop"))

        self._track(content)

    @objc.python_method
    def build_menu(self, content):
        """Idle, hovered: the stack that grows out of the resting sliver, bottom to top a
        dictate capsule, a read-selection disc and a history disc, in one transparent container
        (scaled as a unit, like the docked reading pill)."""
        cx = MENU_W / 2
        self.menu_hovers = []

        def item(frame, radius, symbol, size, tint, action, tip, key_fn, weight=AppKit.NSFontWeightSemibold):
            bg = self.make_surface(frame, radius, hover=True)
            content.addSubview_(bg)
            btn = symbol_button(symbol, self, action, size=size, tint=tint, weight=weight)
            btn.setFrame_(((0, 0), frame[1]))
            btn.setToolTip_(None)  # the tooltip pill replaces the system tooltip
            bg.addSubview_(btn)
            bg.on_hover = lambda on: self._hover_tip(on, bg, tip, key_fn)
            self.menu_hovers.append(bg)
            return bg

        y = 0.0
        self.menu_dictate = item(((cx - MENU_MIC_W / 2, y), (MENU_MIC_W, MENU_MIC_H)), MENU_MIC_W / 2, "mic.fill", 15.0,
                                 theme.CONTENT, "menuDictate:", "Dictate", lambda: self.key_name)
        y += MENU_MIC_H + DOCK_GAP_V
        self.menu_read = item(((cx - DISC / 2, y), (DISC, DISC)), DISC / 2, "play.fill", 11.0, theme.CONTENT_DIM,
                              "menuRead:", "Read selection", lambda: self.read_key)
        y += DISC + DOCK_GAP_V
        self.menu_history = item(((cx - DISC / 2, y), (DISC, DISC)), DISC / 2, "clock.arrow.circlepath", 12.0,
                                 theme.CONTENT_DIM, "menuHistory:", "History", lambda: "")
        self._track(content)

    @objc.python_method
    def build_dictate(self, host):
        """Dictating: a capsule centred in a FREE_H tall transparent host. Compact it is
        COMPACT_H tall with only the wave; hands-free it grows to FREE_H about its centre and
        shows cancel (top) and finish (bottom). The buttons hang off the capsule's edges
        (autoresizing), so they ride out with it as it grows and the capsule clips them. Each
        button's click area is a full-width strip BUTTON_HIT tall around its smaller disc."""
        W = CAP_W
        capsule = self.make_surface(((0, (FREE_H - COMPACT_H) / 2), (W, COMPACT_H)), W / 2)
        capsule.setAutoresizesSubviews_(True)
        host.addSubview_(capsule)

        wave = WaveView.alloc().initWithFrame_((((W - WAVE_W) / 2, (COMPACT_H - WAVE_H) / 2), (WAVE_W, WAVE_H)))
        wave.setAutoresizingMask_(AppKit.NSViewMinYMargin | AppKit.NSViewMaxYMargin)
        wave.level_fn = lambda: self._level
        capsule.addSubview_(wave)

        def button(disc_y, d, fill, glow, symbol, size, tint, action, tip, key, mask):
            """A BUTTON_HIT tall strip (the click area) with a d-point disc drawn in it at disc_y
            (in capsule coordinates), and the button covering the whole strip."""
            strip_y = disc_y - (BUTTON_HIT - d) / 2
            strip = AppKit.NSView.alloc().initWithFrame_(((0, strip_y), (W, BUTTON_HIT)))
            strip.setAutoresizingMask_(mask)
            disc = HoverView.make((((W - d) / 2, (BUTTON_HIT - d) / 2), (d, d)), fill, d / 2, glow=glow)
            strip.addSubview_(disc)
            btn = symbol_button(symbol, self, action, size=size, tint=tint, weight=AppKit.NSFontWeightBold)
            btn.setFrame_(((0, 0), (W, BUTTON_HIT)))
            btn.setToolTip_(None)
            strip.addSubview_(btn)  # above the disc, so the whole strip takes the click
            disc.on_hover = lambda on: self._hover_tip(on, disc, tip, key)
            capsule.addSubview_(strip)
            return strip, disc, btn

        self.cancel_bg, self.cancel_disc, self.cancel_btn = button(
            COMPACT_H - CANCEL_TOP - CANCEL_D, CANCEL_D, theme.FILL_SOFT, None, "xmark", 9.0, theme.CONTENT_DIM,
            "dictateCancel:", "Cancel", lambda: "esc", AppKit.NSViewMinYMargin)  # keeps its distance to the top
        self.finish_bg, self.finish_disc, self.finish_btn = button(
            FINISH_BOTTOM, FINISH_D, theme.CONTENT, white_glow, "checkmark", 11.0, theme.SURFACE,
            "dictateFinish:", "Finish", lambda: self.key_name, AppKit.NSViewMaxYMargin)  # keeps its distance to the bottom
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
        edge, screen, cy = self._nearest_edge()
        self._collapse(edge, screen, cy)
        self._remember()

    @objc.python_method
    def _nearest_edge(self):
        f = self.panel.frame()
        cx, cy = f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2
        screen = screen_at(cx, cy)
        vf = visible_frame(screen)
        edge = "left" if cx - vf.origin.x < vf.origin.x + vf.size.width - cx else "right"
        return edge, screen, cy

    @objc.python_method
    def _refresh_dock_arrow(self) -> None:
        edge, _, _ = self._nearest_edge()
        set_symbol(self.dock_btn, f"arrow.{edge}.to.line.compact", 11.0, AppKit.NSFontWeightBold)

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
              and self.dictation_state not in DICTATING and self.visible()):
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
    def _show_form(self, name: str) -> None:
        """The one way the window changes form: put that form's view in and nothing else."""
        views = {"card": self.card, "pill": self.pill_host, "sliver": self.sliver,
                 "rest": self.rest, "menu": self.menu_host, "dictate": self.dictate_host}
        if name != "dictate":
            self.wave.stop()
        if name != "menu":
            for hv in self.menu_hovers:
                hv.set_hover(False)
        self._hide_tip()
        self.form = name
        self.draggable = name != "dictate"  # a drag would fight push-to-talk
        self.panel.setContentView_(views[name])

    @objc.python_method
    def _open_pill(self) -> None:
        self._show_form("pill")
        self.panel.setFrame_display_(self._docked_frame(DOCK_W, DOCK_H, DOCK_GAP), True)
        self.pill.setFrame_(((0, 0), (DOCK_W, DOCK_H)))
        self._set_scale(SLIVER_H / DOCK_H, animate=False)
        self.order_front()
        self._set_scale(1.0, animate=True)
        self._watch_hover()

    @objc.python_method
    def _close_pill(self) -> None:
        log.debug("close_pill form=%s dragging=%s", self.form, self._dragging)
        if self.form != "pill" or self._dragging:
            return
        # A pill that closes after the reading ended goes back to the sliver, but hidden.
        self._set_scale(SLIVER_H / DOCK_H, animate=True, done=lambda: self._show_sliver(show=self._status is not None))

    @objc.python_method
    def _open_menu(self) -> None:
        """The resting sliver grows into the idle menu, exactly as the docked reading sliver
        grows into its pill."""
        self._show_form("menu")
        ex, cy = self._rest_anchor
        x = ex + DOCK_GAP if self._edge == "left" else ex - DOCK_GAP - MENU_W
        self.panel.setFrame_display_(((x, cy - MENU_H / 2), (MENU_W, MENU_H)), True)
        self.menu.setFrame_(((0, 0), (MENU_W, MENU_H)))
        self._set_scale(SLIVER_H / MENU_H, animate=False)
        self.order_front()
        self._set_scale(1.0, animate=True)
        self._watch_hover()

    @objc.python_method
    def _close_menu(self) -> None:
        if self.form != "menu" or self._dragging:
            return
        self._set_scale(SLIVER_H / MENU_H, animate=True, done=self.show_idle)

    @objc.python_method
    def _close_menu_now(self) -> None:
        """An item was clicked: back to the resting sliver at once, before its action runs."""
        if self.form == "menu":
            self._cancel_anim()
            self.show_idle()

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
        self.panel.setFrame_display_(self._docked_frame(SLIVER_W, SLIVER_H, 0.0), True)
        self.sliver.setBoundsSize_((SLIVER_W, SLIVER_H))
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
        self.pill.layer().removeAllAnimations()
        self.menu.layer().removeAllAnimations()

    @objc.python_method
    def _scaled(self):
        """The view the hover animation scales, its size, and the edge it grows from: the idle
        menu while that form is up, else the docked reading pill."""
        if self.form == "menu":
            return self.menu, MENU_W, MENU_H, self._edge
        return self.pill, DOCK_W, DOCK_H, self.dock

    @objc.python_method
    def _transform(self, s: float):
        """Scale about the point where the pill meets the screen edge, vertically centred.
        AppKit pins backing layers' anchorPoint at (0, 0), so fold the anchor into a
        translation: T = translate(a - s·a) ∘ scale(s)."""
        _, w, h, edge = self._scaled()
        ax = 0.0 if edge == "left" else w
        ay = h / 2
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
    def _docked_frame(self, w: float, h: float, gap: float):
        """Frame of a w×h window hanging off the dock anchor (screen edge x, centre y),
        `gap` points in from the edge."""
        ex, cy = self._anchor
        x = ex + gap if self.dock == "left" else ex - gap - w
        return ((x, cy - h / 2), (w, h))

    # -- dragging / docking ---------------------------------------------------------

    @objc.python_method
    def drag_began(self) -> None:
        log.debug("drag_began form=%s dock=%s scale=%.2f", self.form, self.dock, self._scale)
        self._dragging = True
        if self.form in ("rest", "menu"):
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
            return  # it follows the mouse; on release it snaps to the nearer edge
        edge, screen = self._edge_near(mx, my)
        if edge:
            vf = visible_frame(screen)
            zx = vf.origin.x + CAPSULE_INSET - 4 if edge == "left" else vf.origin.x + vf.size.width - CAPSULE_INSET - CAPSULE_W - 4
            self.zone.show_at(zx, self._center_y() - self.zone.height / 2, screen=screen)
            self.order_front()
        else:
            self.zone.hide()

    @objc.python_method
    def drag_ended(self, mx: float, my: float) -> None:
        log.debug("drag_ended at (%.0f, %.0f) form=%s dock=%s", mx, my, self.form, self.dock)
        self._dragging = False
        if self.form in ("rest", "menu"):
            self._drop_rest(mx, my)
            return
        self.zone.hide()
        edge, screen = self._edge_near(mx, my)
        if edge:
            self._collapse(edge, screen, self._center_y())
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
        ex, cy = self._anchor
        cx = ex + DOCK_GAP + self.width / 2 if self.dock == "left" else ex - DOCK_GAP - self.width / 2
        self._expand_at(cx, cy)
        self._remember()

    @objc.python_method
    def _center_y(self) -> float:
        f = self.panel.frame()
        return f.origin.y + f.size.height / 2

    @objc.python_method
    def _edge_near(self, mx: float, my: float):
        """Which screen edge (if any) the panel is close enough to for docking."""
        screen = screen_at(mx, my)
        vf = visible_frame(screen)
        f = self.panel.frame()
        left = f.origin.x - vf.origin.x
        right = (vf.origin.x + vf.size.width) - (f.origin.x + f.size.width)
        if left < SNAP and left <= right:
            return "left", screen
        if right < SNAP:
            return "right", screen
        return "", screen

    @objc.python_method
    def _collapse(self, edge: str, screen, cy: float) -> None:
        """Dock on `edge` of `screen`, resting form, centred on cy."""
        self._cancel_anim()
        vf = visible_frame(screen)
        cy = min(max(cy, vf.origin.y + DOCK_H / 2), vf.origin.y + vf.size.height - DOCK_H / 2)  # room for the open pill
        self.dock = edge
        self._anchor = (vf.origin.x if edge == "left" else vf.origin.x + vf.size.width, cy)
        self._scale = SLIVER_H / DOCK_H
        set_symbol(self.chevron, "chevron.right" if edge == "left" else "chevron.left", 11.0, AppKit.NSFontWeightBold)
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
        if self.dock:
            fx, fy = 0.0, (self._anchor[1] - DOCK_H / 2 - vf.origin.y) / max(1.0, vf.size.height - DOCK_H)
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
        if lay.get("dock") in ("left", "right"):
            cy = vf.origin.y + lay["y"] * (vf.size.height - DOCK_H) + DOCK_H / 2 if lay["y"] >= 0 else vf.origin.y + vf.size.height / 2
            self._collapse(lay["dock"], screen, cy)
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
            self.zone.hide()
            if self.dock:
                self._show_sliver(show=False)  # so the next reading starts from the resting form
            log.info("player hidden (reading ended) dock=%s form=%s", self.dock, self.form)
            self.hide()
            self.show_idle()
            return
        self._status = st
        icon = "play.fill" if st.state == "paused" else "pause.fill"
        for btn, size in zip(self.buttons["toggle"], (20.0, 14.0), strict=False):
            set_symbol(btn, icon, size)
        self._speed = st.speed
        self.speed_btn.setTitle_(f"{st.speed:g}×")
        self.progress.setStringValue_("Loading…" if st.state == "loading" else f"{st.index} of {st.total}")
        if not (self.scrub.scrubbing or self.dock_scrub.scrubbing):
            self.elapsed.setStringValue_(_fmt(st.position))
            self.remaining.setStringValue_("-" + _fmt(max(0.0, st.duration - st.position)))
        frac = st.position / st.duration if st.duration > 0 else 0.0
        self.scrub.set_fraction(frac)
        self.dock_scrub.set_fraction(frac)
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
            self.pill.setToolTip_(tip)
            self.sliver.setToolTip_(tip)
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
    def show_idle(self) -> None:
        """Nothing playing and nothing being dictated: the resting sliver on its edge, or nothing."""
        form = pick_form("idle" if self._status is None else "playing", self.dictation_state, bool(self.dock),
                         self.idle_pill)
        if form != "rest":
            if self.form in ("rest", "menu"):
                self._cancel_anim()
                self.hide()
            return
        edge, fy = dictation_spot(self.layout, self.dictation_layout)
        self._place_rest(_vf(screen_under_mouse()), edge, fy)
        self.order_front()

    @objc.python_method
    def _place_rest(self, vf, edge: str, fy: float) -> None:
        self._edge = edge
        self._show_form("rest")
        frame = edge_frame(vf, edge, fy, SLIVER_W, SLIVER_H, inset=0.0)
        self.panel.setFrame_display_(frame, True)
        self.rest.setBoundsSize_((SLIVER_W, SLIVER_H))  # after the frame: see _show_sliver
        self.rest.set_state(0.0, False, edge)
        (x, y), (w, h) = frame
        self._rest_anchor = (x if edge == "left" else x + w, y + h / 2)

    @objc.python_method
    def set_idle_pill(self, on: bool) -> None:
        self.idle_pill = bool(on)
        if self.form in ("rest", "menu") or (on and self._status is None and self.dictation_state not in DICTATING):
            self.show_idle()

    @objc.python_method
    def _drop_rest(self, mx: float, my: float) -> None:
        """The idle pill was dragged: snap it to the nearer edge of the screen it was dropped
        on, at that height, as the resting sliver, and remember the spot (shared with the
        docked player)."""
        screen = screen_at(mx, my)
        vf = _vf(screen)
        f = self.panel.frame()
        cx, cy = f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2
        edge = "left" if cx - vf[0] < vf[0] + vf[2] - cx else "right"
        fy = spot_fraction(vf, cy)
        self._place_rest(vf, edge, fy)
        if self.layout.get("dock") in ("left", "right"):
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
        self.zone.hide()
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
        self.panel.setFrame_display_(edge_frame(_vf(screen_under_mouse()), edge, fy, CAP_W, FREE_H), True)
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
        h = FREE_H if on else COMPACT_H
        frame = ((0, (FREE_H - h) / 2), (CAP_W, h))
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
