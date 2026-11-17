"""The setup window: one short checklist shown on first launch and from the menu (Setup…).

    1  Allow Lyssna to see your selection   live Accessibility status, Open Settings, Relaunch
    2  Choose a voice                       the free voice on this Mac (download, progress, ready)
                                            or the user's own API key (engine, Get a key, Save)
    3  Try it                               the speak shortcut and a Read a Sample button

Why one window and not alerts: a new user had one dense alert and then had to find
everything in the menu. Here every step shows its state, errors appear inline, and steps 2
and 3 work before Accessibility is granted (the sample goes through speak_text, not the
selection).

It is a regular titled window, not a floating panel: it takes the keyboard (the key field),
centres on the screen under the mouse and closing it never quits the app. Lyssna is an
accessory app with no Edit menu, so the window handles the editing shortcuts itself, or
Cmd+V would do nothing in the key field.

Colours are the brand cream and ink from theme.py (green and red only for success and
error), all dynamic, so it follows light and dark mode. Buttons and radios are drawn here
rather than left to the system, whose versions take the blue accent colour."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import AppKit
import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageSymbolConfiguration,
    NSImageView,
    NSPopUpButton,
    NSProgressIndicator,
    NSSecureTextField,
    NSTextField,
    NSView,
    NSWindow,
    NSWorkspace,
)
from Foundation import NSURL, NSInsetRect, NSMakeRect, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

from ..events import accessibility_trusted
from ..hotkeys import pretty, tokens
from ..providers import REGISTRY, allowed, kokoro
from . import theme
from .mainthread import on_main
from .mark import MarkView
from .panel import TintedView, screen_under_mouse
from .widgets import BAR_H, ProgressBar

log = logging.getLogger("lyssna.ui.setup")

ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
SAMPLE = "Hi, this is Lyssna. Select any text, press the shortcut, and I will read it to you."

W = 500.0          # window content width
MARGIN = 24.0
ICON = 22.0
TEXT_X = MARGIN + ICON + 12.0   # where every step's text starts
TEXT_W = W - TEXT_X - MARGIN
INDENT = 20.0      # radio content sits under the radio's title
MARK_H = 28.0      # the logo mark beside the headline
PILL_H = 26.0      # every button is a pill this tall
PILL_PAD = 14.0    # title to pill edge
RADIO = 16.0       # the drawn radio circle; its image is INDENT wide, so the title lines up with the text below
FIELD_H = 26.0     # the key field's box, level with the Save pill beside it
POLL = 0.3         # seconds between live status checks while the window is open

# Editing shortcuts an accessory app gets no menu for: key -> selector sent down the responder chain.
_EDIT_KEYS = {"x": "cut:", "c": "copy:", "v": "paste:", "a": "selectAll:", "z": "undo:"}


class FlippedView(NSView):
    """Top-left origin, so the layout reads top to bottom like the window does."""

    def isFlipped(self):
        return True


class EditingWindow(NSWindow):
    """NSWindow that answers Cmd+X/C/V/A/Z, Shift+Cmd+Z and Cmd+W without a main menu."""

    def performKeyEquivalent_(self, event):
        flags = event.modifierFlags() & AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        cmd, shift = AppKit.NSEventModifierFlagCommand, AppKit.NSEventModifierFlagShift
        key = (event.charactersIgnoringModifiers() or "").lower()
        if flags in (cmd, cmd | shift):
            if key == "w" and flags == cmd:
                self.performClose_(None)
                return True
            action = "redo:" if (key == "z" and flags & shift) else _EDIT_KEYS.get(key) if flags == cmd else None
            if action and NSApp.sendAction_to_from_(action, None, self):
                return True
        return objc.super(EditingWindow, self).performKeyEquivalent_(event)


class PillButton(NSButton):
    """A pill-shaped push button in the brand colours: primary is a CONTENT pill with
    SURFACE text, secondary a FILL_SOFT pill with a HAIRLINE border and CONTENT text.
    Drawn in drawRect_, so the dynamic colours re-resolve when the appearance flips; it
    stays a real NSButton for the keyboard, key equivalents and accessibility."""

    @classmethod
    @objc.python_method
    def make(cls, title: str, target, action: str, primary: bool = False) -> PillButton:
        b = cls.buttonWithTitle_target_action_(title, target, action)
        b.primary = primary
        b.setBordered_(False)
        b.setFont_(_font(13, AppKit.NSFontWeightMedium if primary else AppKit.NSFontWeightRegular))
        b.setContentTintColor_(theme.SURFACE if primary else theme.CONTENT)
        b.sizeToFit()
        return b

    def sizeToFit(self):
        objc.super(PillButton, self).sizeToFit()
        self.setFrameSize_((round(self.frame().size.width) + 2 * PILL_PAD, PILL_H))

    @objc.python_method
    def _path(self):
        r = NSInsetRect(self.bounds(), 0.5, 0.5)
        radius = r.size.height / 2
        return NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, radius, radius)

    def drawRect_(self, dirty):
        path = self._path()
        (theme.CONTENT if self.primary else theme.FILL_SOFT).set()
        path.fill()
        if not self.primary:
            theme.HAIRLINE.set()
            path.setLineWidth_(1.0)
            path.stroke()
        if self.isHighlighted():  # pressed: a light veil over the pill
            (theme.surface(0.18) if self.primary else theme.content(0.08)).set()
            path.fill()
        objc.super(PillButton, self).drawRect_(dirty)

    def drawFocusRingMask(self):
        self._path().fill()

    def focusRingMaskBounds(self):
        return self.bounds()


def _font(size: float, weight=AppKit.NSFontWeightRegular):
    return NSFont.systemFontOfSize_weight_(size, weight)


def _serif(size: float, weight=AppKit.NSFontWeightSemibold):
    """The system serif (New York), falling back to the system font if it is missing."""
    base = _font(size, weight)
    desc = base.fontDescriptor().fontDescriptorWithDesign_(AppKit.NSFontDescriptorSystemDesignSerif)
    return (NSFont.fontWithDescriptor_size_(desc, size) if desc is not None else None) or base


def _label(text: str, size: float = 13.0, weight=AppKit.NSFontWeightRegular, color=None) -> NSTextField:
    lbl = NSTextField.labelWithString_(text)
    lbl.setFont_(_font(size, weight))
    lbl.setTextColor_(color or theme.CONTENT)
    return lbl


def _wrapping(text: str, size: float = 12.0, color=None) -> NSTextField:
    lbl = NSTextField.wrappingLabelWithString_(text)
    lbl.setFont_(_font(size))
    lbl.setTextColor_(color or theme.CONTENT_DIM)
    lbl.setSelectable_(False)
    return lbl


def _symbol(name: str, size: float, weight=AppKit.NSFontWeightRegular):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    return img.imageWithSymbolConfiguration_(NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight))


def _button(title: str, target, action: str, primary: bool = False) -> NSButton:
    return PillButton.make(title, target, action, primary)


def _radio_image(on: bool):
    """The radio's circle: CONTENT with a SURFACE dot when chosen, a soft ring when not.
    A drawing-handler image runs at draw time, so it follows the appearance."""
    def draw(rect):
        circle = NSMakeRect(0, (rect.size.height - RADIO) / 2, RADIO, RADIO)
        r = NSInsetRect(circle, 0.5, 0.5)
        ring = NSBezierPath.bezierPathWithOvalInRect_(r)
        if on:
            theme.CONTENT.set()
            ring.fill()
            theme.SURFACE.set()
            NSBezierPath.bezierPathWithOvalInRect_(NSInsetRect(circle, 5.0, 5.0)).fill()
        else:
            theme.FILL_SOFT.set()
            ring.fill()
            theme.content(0.35).set()
            ring.setLineWidth_(1.0)
            ring.stroke()
        return True
    return NSImage.imageWithSize_flipped_drawingHandler_((INDENT, RADIO), False, draw)


def _radio(title: str, target, action: str) -> NSButton:
    """A system radio button (role, label, grouping by shared action) wearing our circle."""
    b = NSButton.radioButtonWithTitle_target_action_(title, target, action)
    b.setImage_(_radio_image(False))
    b.setAlternateImage_(_radio_image(True))
    b.cell().setAccessibilityRole_(AppKit.NSAccessibilityRadioButtonRole)  # custom images make AppKit report a checkbox
    attrs = {AppKit.NSFontAttributeName: _font(13), AppKit.NSForegroundColorAttributeName: theme.CONTENT}
    b.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(title, attrs))
    return b


def _fit(view, width: float | None = None) -> tuple[float, float]:
    """Size a view to its content (wrapping labels to `width`) and return that size."""
    if width is not None and isinstance(view, NSTextField):
        view.setPreferredMaxLayoutWidth_(width)
        h = view.cell().cellSizeForBounds_(NSMakeRect(0, 0, width, 10_000)).height
        view.setFrameSize_((width, h))
    elif hasattr(view, "sizeToFit"):
        view.sizeToFit()
    f = view.frame().size
    return f.width, f.height


class SetupWindow(NSObject):
    """Owns the window. Set `ctl`, `taps_ok` (did the hotkey taps start?), `relaunch` and
    `on_change` (the menu refreshes) after init, then call show()."""

    def init(self):
        self = objc.super(SetupWindow, self).init()
        if self is None:
            return None
        self.ctl = None
        self.taps_ok: Callable[[], bool] = lambda: True
        self.relaunch: Callable[[], None] = lambda: None
        self.on_change: Callable[[], None] = lambda: None
        self.window = None
        self._timer = None
        self._saving = False
        self._key_message = ("", "secondary")  # inline note under the key field: (text, tone)
        self._shape = None  # which rows were visible at the last layout
        return self

    # -- showing -----------------------------------------------------------------------

    @objc.python_method
    def show(self, engine: str | None = None) -> None:
        """Bring the window up (building it the first time). `engine` preselects that engine
        under "Use my own API key", for the menu's key entries."""
        if self.window is None:
            self._build()
        self._choose_radio(engine)
        self._refresh(force=True)
        if not self.window.isVisible():
            vf = screen_under_mouse().visibleFrame()
            f = self.window.frame()
            x = vf.origin.x + (vf.size.width - f.size.width) / 2
            y = vf.origin.y + (vf.size.height - f.size.height) / 2 + vf.size.height * 0.08  # a touch above centre
            self.window.setFrameOrigin_((round(x), round(y)))
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        if engine is not None and self.key_field.isEnabled():
            self.window.makeFirstResponder_(self.key_field)
        if self._timer is None:
            self._timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(POLL, self, "tick:", None, True)
            NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    def windowWillClose_(self, note):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def tick_(self, timer):
        self._refresh()

    # -- building ----------------------------------------------------------------------

    @objc.python_method
    def _build(self) -> None:
        style = AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
        win = EditingWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, 400), style, NSBackingStoreBuffered, False)
        win.setTitle_("Lyssna Setup")
        win.setTitlebarAppearsTransparent_(True)  # the title bar shows the surface below
        win.setBackgroundColor_(theme.SURFACE)
        win.setReleasedWhenClosed_(False)  # we keep it and show it again from the menu
        win.setDelegate_(self)
        content = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 400))
        win.setContentView_(content)
        self.window = win
        self.content = content
        secondary = theme.CONTENT_DIM

        def add(view):
            content.addSubview_(view)
            return view

        self.mark = add(MarkView.make(MARK_H))
        self.title = add(_label("Welcome to Lyssna", 20))
        self.title.setFont_(_serif(20))
        self.subtitle = add(_wrapping("Lyssna reads text aloud. Three quick steps and you are set. "
                                      "You can come back here any time from the menu.", 13))

        # Step 1
        self.icon1 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.head1 = add(_label("Allow Lyssna to see your selection", 14, AppKit.NSFontWeightSemibold))
        self.body1 = add(_wrapping(""))
        self.open_settings = add(_button("Open Settings…", self, "openSettings:"))
        self.relaunch_btn = add(_button("Relaunch Lyssna", self, "relaunch:"))
        self.sep1 = add(self._separator())

        # Step 2
        self.icon2 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.head2 = add(_label("Choose a voice", 14, AppKit.NSFontWeightSemibold))
        self.radio_local = add(_radio("On this Mac (free, private)", self, "chooseVoice:"))
        self.local_note = add(_wrapping("Your text stays on this Mac. One download of about 140 MB."))
        self.download = add(_button("Download", self, "download:"))
        self.progress = add(ProgressBar.alloc().initWithFrame_(NSMakeRect(0, 0, 200, BAR_H)))  # the player's bar, not the blue system one
        self.progress_text = add(_label("", 12, color=secondary))
        self.progress_text.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(12, AppKit.NSFontWeightRegular))
        self.ready = add(self._status_row("Ready", "checkmark.circle.fill", NSColor.systemGreenColor()))
        self.local_error = add(_wrapping("", 12, NSColor.systemRedColor()))

        self.radio_key = add(_radio("Use my own API key", self, "chooseVoice:"))
        self.key_note = add(_wrapping("Online voices from Inworld, Cartesia, ElevenLabs or OpenAI. "
                                      "Your text is sent to the service you pick."))
        self.engines = [n for n, c in REGISTRY.items() if c.needs_key]
        self.popup = add(NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 220, 26), False))
        self.popup.addItemsWithTitles_([REGISTRY[n].label for n in self.engines])
        self.popup.setTarget_(self)
        self.popup.setAction_("pickEngine:")
        self.popup.sizeToFit()
        self.get_key = add(_button("Get a key", self, "getKey:"))
        self.get_key.setImage_(_symbol("arrow.up.right", 10, AppKit.NSFontWeightSemibold))
        self.get_key.setImagePosition_(AppKit.NSImageTrailing)
        self.get_key.setImageHugsTitle_(True)
        self.get_key.sizeToFit()
        # The field sits borderless inside a hairline box: the system bezel has no brand colours.
        self.key_box = add(TintedView.make(NSMakeRect(0, 0, 260, FIELD_H), theme.SURFACE, 6.0, theme.HAIRLINE))
        self.key_field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, 22))
        self.key_field.setPlaceholderString_("Paste your API key")
        self.key_field.setTarget_(self)
        self.key_field.setAction_("saveKey:")
        self.key_field.setBezeled_(False)
        self.key_field.setBordered_(False)
        self.key_field.setDrawsBackground_(False)
        self.key_field.setFocusRingType_(AppKit.NSFocusRingTypeNone)  # the caret shows focus; a ring would be blue
        self.key_field.setFont_(_font(13))
        self.key_field.setTextColor_(theme.CONTENT)
        self.key_field.cell().setUsesSingleLineMode_(True)
        self.key_field.cell().setScrollable_(True)
        self.key_box.addSubview_(self.key_field)
        self.save = add(_button("Save", self, "saveKey:"))
        self.spinner = add(NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16)))
        self.spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.spinner.setControlSize_(AppKit.NSControlSizeSmall)
        self.spinner.setDisplayedWhenStopped_(False)
        self.key_status = add(_wrapping("", 12))
        self.local_only_note = add(_wrapping("Online voices are off because \u201cOnly Use Voices on This Mac\u201d is on."))
        self.sep2 = add(self._separator())

        # Step 3
        self.icon3 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.head3 = add(_label("Try it", 14, AppKit.NSFontWeightSemibold))
        self.try_text = add(_label("Select text in any app and press", 13, color=secondary))
        self.keycap = add(self._keycap(self.ctl.cfg["hotkeys"]["speak"]))
        self.sample = add(_button("Read a Sample", self, "sample:"))
        self.try_note = add(_wrapping("The shortcut works once step 1 is done. The sample works now."))

        self.sep_footer = add(self._separator())
        self.done = add(_button("Done", self, "done:", primary=True))
        self.done.setKeyEquivalent_("\r")
        for view in (self.done, self.sample, self.save, self.download, self.open_settings, self.relaunch_btn):
            _fit(view)
            f = view.frame().size
            view.setFrameSize_((max(f.width, 84.0), f.height))

    @objc.python_method
    def _separator(self) -> NSView:
        return TintedView.make(NSMakeRect(0, 0, W - 2 * MARGIN, 1), theme.HAIRLINE)

    @objc.python_method
    def _size_key_box(self, width: float) -> None:
        """Size the key box and the borderless field inside it, text vertically centred."""
        self.key_box.setFrameSize_((width, FIELD_H))
        h = self.key_field.cell().cellSizeForBounds_(NSMakeRect(0, 0, width, 100)).height
        self.key_field.setFrame_(NSMakeRect(8, (FIELD_H - h) / 2, width - 16, h))

    @objc.python_method
    def _status_row(self, text: str, symbol: str, tint) -> NSView:
        """A small symbol followed by a word, e.g. a green check and "Ready"."""
        row = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 18))
        img = NSImageView.imageViewWithImage_(_symbol(symbol, 13, AppKit.NSFontWeightMedium))
        img.setContentTintColor_(tint)
        img.setFrame_(NSMakeRect(0, 1, 16, 16))
        lbl = _label(text, 13, AppKit.NSFontWeightMedium)
        w, h = _fit(lbl)
        lbl.setFrameOrigin_((20, (18 - h) / 2))
        row.addSubview_(img)
        row.addSubview_(lbl)
        row.setFrameSize_((20 + w, 18))
        row.label = lbl
        return row

    @objc.python_method
    def _keycap(self, spec: str) -> NSView:
        """The shortcut as a row of key caps, e.g. [⌃] [⌥] [S]."""
        row = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 24))
        x = 0.0
        for part in tokens(spec):
            lbl = _label(pretty(part), 13, AppKit.NSFontWeightMedium)
            tw, th = _fit(lbl)
            cw = max(24.0, tw + 12)
            cap = TintedView.make(NSMakeRect(x, 0, cw, 24), theme.FILL_SOFT, 6.0, theme.HAIRLINE)
            lbl.setFrameOrigin_(((cw - tw) / 2, (24 - th) / 2))
            cap.addSubview_(lbl)
            row.addSubview_(cap)
            x += cw + 4
        row.setFrameSize_((x - 4, 24))
        return row

    # -- state -------------------------------------------------------------------------

    @objc.python_method
    def _choose_radio(self, engine: str | None) -> None:
        """Point the voice choice at `engine`, or at whatever the app reads with now."""
        current = engine or self.ctl.cfg.get("provider", "")
        online = current in self.engines and allowed(current, self.ctl.cfg)
        self.radio_key.setState_(1 if online else 0)
        self.radio_local.setState_(0 if online else 1)
        if online:
            self.popup.selectItemAtIndex_(self.engines.index(current))
        if engine is not None:
            self._key_message = ("", "secondary")

    @objc.python_method
    def _engine(self) -> str:
        return self.engines[max(0, self.popup.indexOfSelectedItem())]

    @objc.python_method
    def _set_step_icon(self, view, number: int, done: bool) -> None:
        if done:
            view.setImage_(_symbol("checkmark.circle.fill", 18, AppKit.NSFontWeightMedium))
            view.setContentTintColor_(NSColor.systemGreenColor())
        else:
            view.setImage_(_symbol(f"{number}.circle", 18, AppKit.NSFontWeightMedium))
            view.setContentTintColor_(theme.CONTENT_DIM)

    @objc.python_method
    def _refresh(self, force: bool = False) -> None:
        """Read the live state (permission, download, keys) and show it."""
        ctl = self.ctl
        trusted = accessibility_trusted()
        needs_relaunch = trusted and not self.taps_ok()
        if not trusted:
            self.body1.setStringValue_("The shortcut and the play button need this to read what you select. "
                                       "Click Open Settings and turn on Lyssna. If it is not in the list, "
                                       "click + and choose Lyssna.")
        elif needs_relaunch:
            self.body1.setStringValue_("Allowed. Relaunch Lyssna once so the shortcut starts working.")
        else:
            self.body1.setStringValue_("Allowed. The shortcut and the play button are ready.")
        self._set_step_icon(self.icon1, 1, trusted and not needs_relaunch)

        local_only = ctl.local_only()
        if local_only and self.radio_key.state():
            self._choose_radio(None)
        can_run = kokoro.KokoroProvider.can_run()
        self.radio_local.setEnabled_(can_run)
        progress = ctl.voice_download_progress()
        ready = can_run and ctl.provider_configured("kokoro")
        error = kokoro.DOWNLOAD.error if (progress is None and not ready) else ""
        if not can_run:
            error = "The voice on this Mac is not available in this build."
        if progress is not None:
            self.progress.set_fraction(progress)
            self.progress_text.setStringValue_(f"{int(progress * 100)}%")
        self.local_error.setStringValue_(error)
        self.download.setTitle_("Try Again" if error and can_run else "Download")

        provider = ctl.cfg.get("provider", "")
        self._set_step_icon(self.icon2, 2, provider in REGISTRY and ctl.provider_configured(provider))
        self._set_step_icon(self.icon3, 3, False)

        engine = self._engine()
        has_key = ctl.provider_configured(engine)
        text, tone = self._key_message
        if not text and not self._saving:
            if has_key and provider == engine:
                text, tone = f"Using {REGISTRY[engine].label} with your saved key.", "good"
            elif has_key:
                text = "A key is already saved. Paste a new one to replace it."
        self.key_status.setStringValue_(text)
        self.key_status.setTextColor_({"good": NSColor.systemGreenColor(), "bad": NSColor.systemRedColor()}.get(
            tone, theme.CONTENT_DIM))
        for view in (self.popup, self.key_field, self.save):
            view.setEnabled_(not self._saving)

        local_chosen = bool(self.radio_local.state())
        shape = {
            "relaunch": needs_relaunch,
            "open_settings": not trusted,
            "local_body": local_chosen,
            "download": local_chosen and progress is None and not ready and can_run,
            "progress": local_chosen and progress is not None,
            "ready": local_chosen and ready,
            "local_error": local_chosen and bool(error),
            "radio_key": not local_only,
            "key_body": not local_only and not local_chosen,
            "key_status": not local_only and not local_chosen and bool(text),
            "local_only_note": local_only,
            "try_note": not trusted,
            "texts": (self.body1.stringValue(), error, text, self._saving),
        }
        if force or shape != self._shape:
            self._shape = shape
            self._layout(shape)

    # -- layout ------------------------------------------------------------------------

    @objc.python_method
    def _layout(self, show: dict) -> None:
        """Stack the visible rows top to bottom, then fit the window to them (keeping its top edge)."""
        visible = set()

        def put(view, x, y, width=None):
            _, h = _fit(view, width) if width is not None or isinstance(view, (NSTextField, NSButton)) else view.frame().size
            view.setFrameOrigin_((x, y))
            visible.add(view)
            return h

        def centre_row(views_x, y):
            """Place views on one line, vertically centred on the tallest; returns the line height."""
            h = max(v.frame().size.height for v, _ in views_x)
            for v, x in views_x:
                v.setFrameOrigin_((x, y + (h - v.frame().size.height) / 2))
                visible.add(v)
            return h

        y = MARGIN
        _fit(self.title)
        mw = self.mark.frame().size.width
        y += centre_row([(self.mark, MARGIN), (self.title, MARGIN + mw + 10)], y) + 6
        y += put(self.subtitle, MARGIN, y, W - 2 * MARGIN) + 18

        def step(icon, head, y):
            hh = put(head, TEXT_X, y)
            icon.setFrameOrigin_((MARGIN, y + (hh - ICON) / 2))
            visible.add(icon)
            return hh

        # Step 1
        y += step(self.icon1, self.head1, y) + 3
        y += put(self.body1, TEXT_X, y, TEXT_W)
        row = []
        x = TEXT_X  # pills have no transparent inset, so their edge lines up with the text as is
        for flag, btn in (("open_settings", self.open_settings), ("relaunch", self.relaunch_btn)):
            if show[flag]:
                row.append((btn, x))
                x += btn.frame().size.width + 8
        if row:
            y += 8 + centre_row(row, y + 8)
        y += 16
        self.sep1.setFrame_(NSMakeRect(MARGIN, y, W - 2 * MARGIN, 1))
        visible.add(self.sep1)
        y += 16

        # Step 2
        y += step(self.icon2, self.head2, y) + 8
        _fit(self.radio_local)
        y += put(self.radio_local, TEXT_X, y) + 2
        if show["local_body"]:
            sub_x = TEXT_X + INDENT
            y += put(self.local_note, sub_x, y, TEXT_W - INDENT) + 6
            if show["download"]:
                y += centre_row([(self.download, sub_x)], y)
            elif show["progress"]:
                self.progress_text.setStringValue_("100%")  # measure the widest it gets
                tw, _ = _fit(self.progress_text)
                self.progress.setFrameSize_((TEXT_W - INDENT - tw - 10, BAR_H))
                self.progress_text.setFrameSize_((tw, self.progress_text.frame().size.height))
                self._refresh_progress_text()
                y += centre_row([(self.progress, sub_x), (self.progress_text, sub_x + TEXT_W - INDENT - tw)], y + 4) + 4
            elif show["ready"]:
                y += centre_row([(self.ready, sub_x)], y + 2) + 2
            if show["local_error"]:
                y += 4 + put(self.local_error, sub_x, y + 4, TEXT_W - INDENT)
            y += 4
        if show["radio_key"]:
            y += 8
            _fit(self.radio_key)
            y += put(self.radio_key, TEXT_X, y) + 2
        if show["key_body"]:
            sub_x = TEXT_X + INDENT
            sub_w = TEXT_W - INDENT
            y += put(self.key_note, sub_x, y, sub_w) + 8
            self.popup.sizeToFit()
            y += centre_row([(self.popup, sub_x - 3), (self.get_key, sub_x + self.popup.frame().size.width + 10)], y) + 8
            save_w = self.save.frame().size.width
            self._size_key_box(sub_w - save_w - 8)  # the Save pill's edge ends the row
            field_right = sub_x + self.key_box.frame().size.width
            y += centre_row([(self.key_box, sub_x), (self.save, field_right + 8)], y)
            if show["key_status"]:
                shift = 22.0 if self._saving else 0.0
                h = put(self.key_status, sub_x + shift, y + 8, sub_w - shift)
                if self._saving:
                    self.spinner.setFrameOrigin_((sub_x, y + 8 + (h - 16) / 2))
                    visible.add(self.spinner)
                y += 8 + h
        if show["local_only_note"]:
            y += 6 + put(self.local_only_note, TEXT_X, y + 6, TEXT_W)
        y += 16
        self.sep2.setFrame_(NSMakeRect(MARGIN, y, W - 2 * MARGIN, 1))
        visible.add(self.sep2)
        y += 16

        # Step 3
        y += step(self.icon3, self.head3, y) + 8
        _fit(self.try_text)
        y += centre_row([(self.try_text, TEXT_X), (self.keycap, TEXT_X + self.try_text.frame().size.width + 8)], y) + 10
        y += centre_row([(self.sample, TEXT_X)], y)
        if show["try_note"]:
            y += 6 + put(self.try_note, TEXT_X, y + 6, TEXT_W)
        y += 20

        # Footer
        self.sep_footer.setFrame_(NSMakeRect(0, y, W, 1))
        visible.add(self.sep_footer)
        y += 14
        dw = self.done.frame().size.width
        y += centre_row([(self.done, W - MARGIN - dw)], y)
        y += 14

        for view in self.content.subviews():
            view.setHidden_(view not in visible)
        frame = self.window.frame()
        new = self.window.frameRectForContentRect_(NSMakeRect(0, 0, W, y))
        top = frame.origin.y + frame.size.height
        self.window.setFrame_display_(NSMakeRect(frame.origin.x, top - new.size.height, W, new.size.height), True)

    @objc.python_method
    def _refresh_progress_text(self) -> None:
        progress = self.ctl.voice_download_progress()
        self.progress_text.setStringValue_(f"{int((progress or 0) * 100)}%")
        self.progress_text.setAlignment_(AppKit.NSTextAlignmentRight)

    # -- actions -----------------------------------------------------------------------

    def openSettings_(self, sender):
        accessibility_trusted(prompt=True)  # puts Lyssna in the list if it is not there yet
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(ACCESSIBILITY_PANE))

    def relaunch_(self, sender):
        self.relaunch()

    def chooseVoice_(self, sender):
        """Both radios share this action, which is what makes AppKit treat them as one group."""
        local = sender is self.radio_local
        self.radio_local.setState_(1 if local else 0)
        self.radio_key.setState_(0 if local else 1)
        self._key_message = ("", "secondary")
        if local:
            if self.ctl.provider_configured("kokoro") and self.ctl.cfg.get("provider") != "kokoro":
                self._use("kokoro")
        elif self.ctl.provider_configured(self._engine()):
            self._use(self._engine())
        self._refresh(force=True)
        if not local:
            self.window.makeFirstResponder_(self.key_field)

    def pickEngine_(self, sender):
        self._key_message = ("", "secondary")
        engine = self._engine()
        if self.ctl.provider_configured(engine):
            self._use(engine)
        self._refresh(force=True)

    def download_(self, sender):
        self._use("kokoro")  # starts the download; a reading asked for meanwhile waits for it
        self._refresh(force=True)

    def getKey_(self, sender):
        url = REGISTRY[self._engine()].key_url
        if url:
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))

    def saveKey_(self, sender):
        if self._saving:
            return
        engine, key = self._engine(), self.key_field.stringValue()
        if not key.strip():
            self._key_message = ("Paste your API key first.", "bad")
            self._refresh(force=True)
            return
        self._saving = True
        self._key_message = ("Checking the key…", "secondary")
        self.spinner.startAnimation_(None)
        self._refresh(force=True)

        def check():
            error = self.ctl.add_api_key(engine, key)  # one real synthesis, then save
            on_main(self._saved, engine, error)

        threading.Thread(target=check, daemon=True, name="lyssna-key-check").start()

    @objc.python_method
    def _saved(self, engine: str, error: str) -> None:
        self._saving = False
        self.spinner.stopAnimation_(None)
        if error:
            self._key_message = (error[:300], "bad")
        else:
            self.key_field.setStringValue_("")
            self._key_message = (f"Saved. Lyssna now reads with {REGISTRY[engine].label}.", "good")
            self.on_change()
        self._refresh(force=True)

    def sample_(self, sender):
        self.ctl.speak_text(SAMPLE)

    def done_(self, sender):
        self.window.performClose_(None)

    @objc.python_method
    def _use(self, engine: str) -> None:
        try:
            self.ctl.set_provider(engine)
        except ValueError as e:
            self._key_message = (str(e), "bad")
        self.on_change()
