"""The setup window: shown on first launch and from the menu (Setup…). Like the menu, it has
two sections, Listen and Dictate, behind a switch in the header, each with the same three steps:

    Listen   1  Permission   live Accessibility status, Open Settings, Relaunch
             2  Voice        the free voice on this Mac (download, progress, ready)
                             or the user's own API key (engine, Get a key, Save)
             3  Try it       the speak shortcut and a Read a Sample button
    Dictate  1  Permission   microphone access and the Globe key setting
             2  Engine       the speech models on this Mac (download, progress, ready)
                             or the user's own OpenAI and Anthropic keys (Get a key, Save)
             3  Try it       the dictation keys

Why one window and not alerts: a new user had one dense alert and then had to find
everything in the menu. Here every step shows its state, errors appear inline, and Listen's
steps 2 and 3 work before Accessibility is granted (the sample goes through speak_text, not
the selection).

It is a regular titled window, not a floating panel: it takes the keyboard (the key fields),
centres on the screen under the mouse and closing it never quits the app. The header (title
and switch) and the footer (status and Done) stay put; only the steps between them scroll,
when they are taller than the screen allows. Lyssna is an accessory app with no Edit menu,
so the window handles the editing shortcuts itself, or Cmd+V would do nothing in the key field.

Colours are the brand cream and ink from theme.py (green and red only for success and
error), all dynamic, so it follows light and dark mode. Buttons and radios are drawn here
rather than left to the system, whose versions take the blue accent colour.

Nothing here waits: key questions go through the controller's non-blocking checks (the
Keychain may be waiting on a password prompt), and the Globe key setting, which takes a
`defaults` call to read, is read on a worker thread a few times a minute."""

from __future__ import annotations

import logging
import threading
import time
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
    NSScrollView,
    NSSecureTextField,
    NSTextField,
    NSView,
    NSWindow,
    NSWorkspace,
)
from Foundation import NSURL, NSInsetRect, NSMakeRect, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

from ..controller import KEYBOARD_SETTINGS_URL, MIC_SETTINGS_URL
from ..dictation import cleaners, transcribers
from ..hotkeys import pretty, tokens
from ..mac.events import accessibility_trusted
from ..mac.globe import globe_key_action
from ..menu import LOCAL_HEADER, ONLINE_HEADER, key_name, privacy_symbol
from ..providers import REGISTRY, allowed, get_provider, kokoro
from . import theme
from .mainthread import on_main
from .mark import MarkView
from .panel import TintedView, screen_under_mouse
from .shared.setup import (
    CARD_PAD,
    CARD_R,
    CARD_X,
    DICTATE,
    DICTATION_KEY_ENGINES,
    DICTATION_KEYS,
    FIELD_H,
    FOOTER_H,
    GLOBE_POLL,
    ICON,
    INDENT,
    LISTEN,
    MARGIN,
    MARK_H,
    PILL_H,
    PILL_PAD,
    POLL,
    RADIO,
    SAMPLE,
    SCREEN_GAP,
    SEG_H,
    SEG_PAD,
    SUB_W,
    SUB_X,
    TEXT_W,
    TEXT_X,
    W,
    _size_text,
    footer_status,
    opening_tab,
    steps_left,
    tab_status,
)
from .widgets import BAR_H, ProgressBar

log = logging.getLogger("lyssna.ui.setup")

ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
CARD_BORDER_ON = theme.content(0.45)  # the chosen option card
PENDING = "exclamationmark.circle"  # beside a dictation check that still needs doing (a plain circle reads as a radio)

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


class TabButton(NSButton):
    """One segment of the Listen / Dictate switch: the tab's name and a small status, on a
    CONTENT fill with SURFACE text when chosen, bare dim text when not. A real radio button
    underneath (the two share an action, so AppKit groups them: arrow keys move between them
    and VoiceOver reads a radio group), drawn here because the system's takes the blue accent."""

    @classmethod
    @objc.python_method
    def make(cls, name: str, target, action: str) -> TabButton:
        b = cls.radioButtonWithTitle_target_action_(name, target, action)
        b.name = name
        b.status = ""
        b.cell().setAccessibilityRole_(AppKit.NSAccessibilityRadioButtonRole)
        return b

    @objc.python_method
    def set_status(self, status: str) -> None:
        self.status = status
        self.setAccessibilityLabel_(f"{self.name}, {status}" if status else self.name)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _text(self):
        on = bool(self.state())
        text = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_(self.name, {
            AppKit.NSFontAttributeName: _font(13, AppKit.NSFontWeightMedium),
            AppKit.NSForegroundColorAttributeName: theme.SURFACE if on else theme.CONTENT_DIM})
        if self.status:
            text.appendAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
                f"  {self.status}", {AppKit.NSFontAttributeName: _font(11),
                                     AppKit.NSForegroundColorAttributeName: theme.surface(0.75) if on else theme.content(0.45)}))
        return text

    @objc.python_method
    def preferred_width(self) -> float:
        return max(150.0, round(self._text().size().width) + 40.0)

    @objc.python_method
    def _path(self):
        return NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 7.0, 7.0)

    def drawRect_(self, dirty):
        if self.state():
            theme.CONTENT.set()
            self._path().fill()
        text = self._text()
        tw, th = text.size()
        w, h = self.bounds().size
        text.drawAtPoint_(((w - tw) / 2, (h - th) / 2))

    def mouseDown_(self, event):
        """The whole segment answers a click. The radio cell underneath only tracks over its own
        small circle and title, which sit unseen in the segment's left corner."""
        if self.isEnabled():
            self.performClick_(None)

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


def _engine_class(name: str):
    return transcribers.REGISTRY.get(name) or cleaners.REGISTRY[name]


class _KeyRow:
    """One dictation engine's key: its heading, Get a key, the field, Save and the status."""

    def __init__(self, name: str, job: str):
        self.name, self.job = name, job
        self.message = ("", "secondary")  # inline note under the field: (text, tone)
        self.saving = False


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
        self.globe_action: Callable[[], str] = globe_key_action  # read on a worker thread
        self._globe = "unknown"  # the last answer
        self._globe_read_at = 0.0
        self._globe_busy = False
        self.key_rows = [_KeyRow(name, job) for name, job in DICTATION_KEYS]
        self.tab = LISTEN  # the tab on show; the user's choice while the window stays open
        self._left = (0, 0)  # steps left on Listen and on Dictate, as of the last refresh
        self._dictate_done = (False, False)
        self._visible = set()  # the step views placed by the layout running now
        return self

    # -- showing -----------------------------------------------------------------------

    @objc.python_method
    def show(self, engine: str | None = None, dictation: str | None = None) -> None:
        """Bring the window up (building it the first time). `engine` opens Listen with that
        voice engine preselected under "Online, with your API key", for the menu's key entries;
        `dictation` opens Dictate with the online option picked and the cursor in that engine's
        key field."""
        if self.window is None:
            self._build()
        self._prepare(engine, dictation, opening=not self.window.isVisible())
        if not self.window.isVisible():
            vf = screen_under_mouse().visibleFrame()
            f = self.window.frame()
            x = vf.origin.x + (vf.size.width - f.size.width) / 2
            y = vf.origin.y + (vf.size.height - f.size.height) / 2 + vf.size.height * 0.08  # a touch above centre
            self.window.setFrameOrigin_((round(x), round(y)))
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        row = next((r for r in self.key_rows if r.name == dictation), None)
        if row is not None and not row.box.isHidden():
            self.content.scrollRectToVisible_(NSInsetRect(row.box.frame(), 0, -96))
            self.window.makeFirstResponder_(row.field)
        elif engine is not None and self.key_field.isEnabled():
            self.window.makeFirstResponder_(self.key_field)
        else:
            self.content.scrollPoint_((0, 0))
        if self._timer is None:
            self._timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(POLL, self, "tick:", None, True)
            NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    @objc.python_method
    def _prepare(self, engine: str | None, dictation: str | None, opening: bool) -> None:
        """Everything show() does short of putting the window on screen: the choices, a fresh
        read of the state, and the tab. The tab changes only when the window opens or the menu
        asks for a key; while it stays open the tab is the user's."""
        self._choose_radio(engine)
        self._choose_dictation(dictation)
        self._read_globe(force=True)
        self._refresh(force=True)
        if opening or engine is not None or dictation is not None:
            self._select_tab(opening_tab(engine, dictation, *self._left))

    def windowWillClose_(self, note):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def tick_(self, timer):
        self._refresh()

    @objc.python_method
    def _select_tab(self, tab: str) -> None:
        """Show `tab`'s steps, from the top."""
        self.tab = tab
        self.tab_listen.setState_(1 if tab == LISTEN else 0)
        self.tab_dictate.setState_(0 if tab == LISTEN else 1)
        self._refresh(force=True)
        self.content.scrollPoint_((0, 0))

    def chooseTab_(self, sender):
        """Both segments share this action, which is what makes AppKit treat them as one group."""
        self._select_tab(LISTEN if sender is self.tab_listen else DICTATE)

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
        # Header and footer stay put; the steps between them sit in a scroll view, so on a
        # short screen the window stops at its height and only the steps scroll.
        root = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 400))
        header = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 200))
        footer = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, W, FOOTER_H))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 200))
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setScrollerStyle_(AppKit.NSScrollerStyleOverlay)
        scroll.contentView().setDrawsBackground_(False)
        content = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 200))
        scroll.setDocumentView_(content)
        for view in (header, scroll, footer):
            root.addSubview_(view)
        win.setContentView_(root)
        self.window = win
        self.header = header
        self.scroll = scroll
        self.content = content
        self.footer = footer
        secondary = theme.CONTENT_DIM

        def add(view):
            content.addSubview_(view)
            return view

        # Header: the mark, the welcome, and the Listen / Dictate switch
        self.mark = MarkView.make(MARK_H)
        self.title = _label("Welcome to Lyssna", 20)
        self.title.setFont_(_serif(20))
        self.subtitle = _wrapping("Lyssna reads text aloud and types what you say. Set up each one below.", 13)
        self.subtitle.setAlignment_(AppKit.NSTextAlignmentCenter)
        self.tab_listen = TabButton.make("Listen", self, "chooseTab:")
        self.tab_dictate = TabButton.make("Dictate", self, "chooseTab:")
        self.tab_listen.setState_(1)
        self.switch = TintedView.make(NSMakeRect(0, 0, 10, SEG_H + 2 * SEG_PAD), theme.FILL_SOFT, 9.0)
        self.switch.setAccessibilityElement_(True)
        self.switch.setAccessibilityRole_(AppKit.NSAccessibilityRadioGroupRole)
        self.switch.setAccessibilityLabel_("Setup section")
        self.switch.addSubview_(self.tab_listen)
        self.switch.addSubview_(self.tab_dictate)
        self.header_line = TintedView.make(NSMakeRect(0, 0, W, 1), theme.HAIRLINE)
        for view in (self.mark, self.title, self.subtitle, self.switch, self.header_line):
            header.addSubview_(view)

        # The option cards go in first, so they sit behind the controls laid over them.
        self.card_local = add(self._card())
        self.card_key = add(self._card())
        self.card_dict_local = add(self._card())
        self.card_dict_online = add(self._card())

        # Listen 1: Permission
        self.icon1 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.head1 = add(_label("Permission", 14, AppKit.NSFontWeightSemibold))
        self.body1 = add(_wrapping(""))
        self.open_settings = add(_button("Open Settings…", self, "openSettings:"))
        self.relaunch_btn = add(_button("Relaunch Lyssna", self, "relaunch:"))
        self.sep1 = add(self._separator())

        # Listen 2: Voice
        self.icon2 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.head2 = add(_label("Voice", 14, AppKit.NSFontWeightSemibold))
        self.radio_local = add(_radio(LOCAL_HEADER, self, "chooseVoice:"))
        self.local_tag = add(self._tag("Free and private", privacy_symbol(True)))
        self.local_note = add(_wrapping("Your text stays on this Mac. One download of about 140 MB."))
        self.download = add(_button("Download", self, "download:"))
        self.progress = add(ProgressBar.alloc().initWithFrame_(NSMakeRect(0, 0, 200, BAR_H)))  # the player's bar, not the blue system one
        self.progress_text = add(_label("", 12, color=secondary))
        self.progress_text.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(12, AppKit.NSFontWeightRegular))
        self.ready = add(self._status_row("Ready", "checkmark.circle.fill", NSColor.systemGreenColor()))
        self.local_error = add(_wrapping("", 12, NSColor.systemRedColor()))

        self.radio_key = add(_radio(ONLINE_HEADER, self, "chooseVoice:"))
        self.key_tag = add(self._tag("", privacy_symbol(False)))
        self.key_note = add(_wrapping("Voices from Inworld, Cartesia, ElevenLabs or OpenAI."))
        self.engines = [n for n, c in REGISTRY.items() if c.needs_key]
        self.popup = add(NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 220, 26), False))
        self.popup.addItemsWithTitles_([REGISTRY[n].label for n in self.engines])
        self.popup.setTarget_(self)
        self.popup.setAction_("pickEngine:")
        self.popup.sizeToFit()
        self.get_key = add(self._get_key_button("getKey:"))
        self.key_box, self.key_field = self._key_field("saveKey:")
        add(self.key_box)
        self.save = add(_button("Save", self, "saveKey:"))
        self.spinner = add(self._spinner())
        self.key_status = add(_wrapping("", 12))
        self.local_only_note = add(_wrapping("Online voices are off because “Keep Everything on This Mac” is on."))
        self.sep2 = add(self._separator())

        # Listen 3: Try it
        self.icon3 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.head3 = add(_label("Try it", 14, AppKit.NSFontWeightSemibold))
        self.try_text = add(_label("Select text in any app and press", 13, color=secondary))
        self.keycap = add(self._keycap(self.ctl.cfg["hotkeys"]["speak"]))
        self.sample = add(_button("Read a Sample", self, "sample:"))
        self.try_note = add(_wrapping("The shortcut works once step 1 is done. The sample works now."))

        # Dictate 1: Permission
        section = self.ctl.cfg.get("dictation", {})
        hotkey = str(section.get("hotkey", "fn"))
        self.dicon1 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.dhead1 = add(_label("Permission", 14, AppKit.NSFontWeightSemibold))
        self.mic_row = add(self._status_row("", PENDING, secondary))
        self.mic_allow = add(_button("Allow", self, "allowMicrophone:"))
        self.mic_settings = add(_button("Open Settings", self, "openMicrophoneSettings:"))
        self.globe_row = add(self._status_row("", PENDING, secondary))
        self.globe_settings = add(_button("Open Keyboard Settings", self, "openKeyboardSettings:"))
        self.dsep1 = add(self._separator())

        # Dictate 2: Engine
        self.dicon2 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.dhead2 = add(_label("Engine", 14, AppKit.NSFontWeightSemibold))
        self.dict_local = add(_radio(LOCAL_HEADER, self, "chooseDictation:"))
        self.dict_local_tag = add(self._tag("Free and private", privacy_symbol(True)))
        self.dict_local_note = add(_wrapping(""))
        self.dict_download = add(_button("Download", self, "downloadModels:"))
        self.dict_progress = add(ProgressBar.alloc().initWithFrame_(NSMakeRect(0, 0, 200, BAR_H)))
        self.dict_progress_text = add(_label("", 12, color=secondary))
        self.dict_progress_text.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(12, AppKit.NSFontWeightRegular))
        self.dict_ready = add(self._status_row("Ready", "checkmark.circle.fill", NSColor.systemGreenColor()))
        self.dict_error = add(_wrapping("", 12, NSColor.systemRedColor()))
        self.dict_online = add(_radio("Online, with your API keys", self, "chooseDictation:"))
        vendors = [_engine_class(row.name).vendor for row in self.key_rows]
        self.dict_online_tag = add(self._tag(f"Sent to {vendors[0]} and {vendors[1]}", privacy_symbol(False)))
        self.dict_online_note = add(_wrapping(f"Best with names and unusual words. Your voice goes to {vendors[0]} and the "
                                              f"text to {vendors[1]} for cleanup. Each needs your own key."))
        for row in self.key_rows:
            cls = _engine_class(row.name)
            row.head = add(_label(f"{cls.vendor} ({row.job})", 13, AppKit.NSFontWeightMedium))
            row.get_key = add(self._get_key_button("getDictationKey:"))
            row.box, row.field = self._key_field("saveDictationKey:")
            add(row.box)
            row.save = add(_button("Save", self, "saveDictationKey:"))
            row.spinner = add(self._spinner())
            row.status = add(_wrapping("", 12))
        self.dict_local_only_note = add(_wrapping("Online dictation is off because “Keep Everything on This Mac” is on."))
        self.dsep2 = add(self._separator())

        # Dictate 3: Try it
        self.dicon3 = add(NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON)))
        self.dhead3 = add(_label("Try it", 14, AppKit.NSFontWeightSemibold))
        self.hold_text = add(_label("Hold", 13, color=secondary))
        self.hold_keys = add(self._keycaps([key_name(hotkey)]))
        self.hold_after = add(_label("and speak.", 13, color=secondary))
        self.dictate_body = add(_wrapping("Lyssna types what you said, cleaned up, wherever your cursor is.", 13))
        self.free_text = add(_label("Hands-free:", 13, color=secondary))
        self.free_keys = add(self._keycaps([key_name(hotkey), pretty(str(section.get("hands_free", "space")))]))

        # Footer: where things stand, and Done
        self.footer_line = TintedView.make(NSMakeRect(0, 0, W, 1), theme.HAIRLINE)
        self.footer_status = _label("", 12, color=secondary)
        self.done = _button("Done", self, "done:", primary=True)
        self.done.setKeyEquivalent_("\r")
        for view in (self.footer_line, self.footer_status, self.done):
            footer.addSubview_(view)
        for view in (self.done, self.sample, self.save, self.download, self.open_settings, self.relaunch_btn,
                     self.dict_download, self.mic_allow, *(row.save for row in self.key_rows)):
            _fit(view)
            f = view.frame().size
            view.setFrameSize_((max(f.width, 84.0), f.height))

    @objc.python_method
    def _get_key_button(self, action: str) -> NSButton:
        """"Get a key" with a small arrow: it opens the provider's page in the browser."""
        b = _button("Get a key", self, action)
        b.setImage_(_symbol("arrow.up.right", 10, AppKit.NSFontWeightSemibold))
        b.setImagePosition_(AppKit.NSImageTrailing)
        b.setImageHugsTitle_(True)
        b.sizeToFit()
        return b

    @objc.python_method
    def _key_field(self, action: str):
        """(box, field): a secure field sitting borderless inside a hairline box, since the
        system bezel has no brand colours. Return in the field runs `action`."""
        box = TintedView.make(NSMakeRect(0, 0, 260, FIELD_H), theme.SURFACE, 6.0, theme.HAIRLINE)
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, 22))
        field.setPlaceholderString_("Paste your API key")
        field.setTarget_(self)
        field.setAction_(action)
        field.setBezeled_(False)
        field.setBordered_(False)
        field.setDrawsBackground_(False)
        field.setFocusRingType_(AppKit.NSFocusRingTypeNone)  # the caret shows focus; a ring would be blue
        field.setFont_(_font(13))
        field.setTextColor_(theme.CONTENT)
        field.cell().setUsesSingleLineMode_(True)
        field.cell().setScrollable_(True)
        box.addSubview_(field)
        return box, field

    @objc.python_method
    def _spinner(self) -> NSProgressIndicator:
        spinner = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
        spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        spinner.setControlSize_(AppKit.NSControlSizeSmall)
        spinner.setDisplayedWhenStopped_(False)
        return spinner

    @objc.python_method
    def _separator(self) -> NSView:
        return TintedView.make(NSMakeRect(0, 0, W - 2 * MARGIN, 1), theme.HAIRLINE)

    @objc.python_method
    def _card(self) -> NSView:
        """An option card: a rounded hairline border around one choice, its note and its controls."""
        return TintedView.make(NSMakeRect(0, 0, TEXT_W, 40), NSColor.clearColor(), 10.0, theme.HAIRLINE)

    @objc.python_method
    def _tag(self, text: str, symbol: str) -> NSView:
        """A card's small dim privacy tag, e.g. a lock and "Free and private"."""
        row = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 16))
        row.image = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 2, 12, 12))
        row.image.setContentTintColor_(theme.CONTENT_DIM)
        row.label = _label("", 11, color=theme.CONTENT_DIM)
        row.label.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        row.addSubview_(row.image)
        row.addSubview_(row.label)
        self._set_tag(row, text, symbol)
        return row

    @objc.python_method
    def _set_tag(self, row, text: str, symbol: str) -> None:
        row.image.setImage_(_symbol(symbol, 10, AppKit.NSFontWeightMedium))
        row.label.setStringValue_(text)
        w, h = _fit(row.label)
        row.label.setFrameOrigin_((16, (16 - h) / 2))
        row.setFrameSize_((16 + w, 16))

    @objc.python_method
    def _size_key_box(self, box, field, width: float) -> None:
        """Size a key box and the borderless field inside it, text vertically centred."""
        box.setFrameSize_((width, FIELD_H))
        h = field.cell().cellSizeForBounds_(NSMakeRect(0, 0, width, 100)).height
        field.setFrame_(NSMakeRect(8, (FIELD_H - h) / 2, width - 16, h))

    @objc.python_method
    def _status_row(self, text: str, symbol: str, tint) -> NSView:
        """A small symbol followed by a few words, e.g. a green check and "Ready"."""
        row = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 18))
        row.image = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 1, 16, 16))
        row.label = _label("", 13, AppKit.NSFontWeightMedium)
        row.addSubview_(row.image)
        row.addSubview_(row.label)
        self._set_status_row(row, text, symbol, tint)
        return row

    @objc.python_method
    def _set_status_row(self, row, text: str, symbol: str, tint) -> None:
        row.image.setImage_(_symbol(symbol, 13, AppKit.NSFontWeightMedium))
        row.image.setContentTintColor_(tint)
        row.label.setStringValue_(text)
        w, h = _fit(row.label)
        row.label.setFrameOrigin_((20, (18 - h) / 2))
        row.setFrameSize_((20 + w, 18))

    @objc.python_method
    def _keycap(self, spec: str) -> NSView:
        """The shortcut as a row of key caps, e.g. [⌃] [⌥] [S]."""
        return self._keycaps([pretty(part) for part in tokens(spec)])

    @objc.python_method
    def _keycaps(self, labels: list[str]) -> NSView:
        """A row of key caps, one per label."""
        row = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 24))
        x = 0.0
        for text in labels:
            lbl = _label(text, 13, AppKit.NSFontWeightMedium)
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
    def _dictation_online_allowed(self) -> bool:
        return all(self._allowed(row.name) for row in self.key_rows)

    @objc.python_method
    def _allowed(self, name: str) -> bool:
        cfg = self.ctl.cfg
        return transcribers.allowed(name, cfg) if name in transcribers.REGISTRY else cleaners.allowed(name, cfg)

    @objc.python_method
    def _choose_dictation(self, engine: str | None) -> None:
        """Point the dictation choice at the online option when asked for an engine's key or
        when dictation uses an online engine now; else at the engines on this Mac."""
        section = self.ctl.cfg.get("dictation", {})
        in_use = {str(section.get("transcriber", "")), str(section.get("cleaner", "")) if section.get("cleanup", True) else ""}
        online = self._dictation_online_allowed() and (engine in DICTATION_KEY_ENGINES or bool(in_use & set(DICTATION_KEY_ENGINES)))
        self.dict_online.setState_(1 if online else 0)
        self.dict_local.setState_(0 if online else 1)
        if engine is not None:
            for row in self.key_rows:
                row.message = ("", "secondary")

    @objc.python_method
    def _read_globe(self, force: bool = False) -> None:
        """Read the Globe key setting on a worker thread, at most every GLOBE_POLL seconds;
        the next tick shows the answer."""
        if self._globe_busy or (not force and time.monotonic() - self._globe_read_at < GLOBE_POLL):
            return
        self._globe_busy = True

        def read() -> None:
            try:
                answer = self.globe_action()
            except Exception:
                log.debug("Globe key setting unreadable", exc_info=True)
                answer = "unknown"
            self._globe, self._globe_read_at, self._globe_busy = answer, time.monotonic(), False

        threading.Thread(target=read, daemon=True, name="lyssna-globe-read").start()

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
        permission_done = trusted and not needs_relaunch
        self._set_step_icon(self.icon1, 1, permission_done)

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
        voice_done = provider in REGISTRY and ctl.provider_configured(provider)
        self._set_step_icon(self.icon2, 2, voice_done)
        self._set_step_icon(self.icon3, 3, False)

        engine = self._engine()
        self._set_tag(self.key_tag, f"Text is sent to {get_provider(engine, ctl.cfg).recipient()}", privacy_symbol(False))
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

        dictation = self._refresh_dictation()

        self._left = (steps_left((permission_done, voice_done)), steps_left(self._dictate_done))
        self.tab_listen.set_status(tab_status(self._left[0]))
        self.tab_dictate.set_status(tab_status(self._left[1]))
        self.footer_status.setStringValue_(footer_status(*self._left))

        local_chosen = bool(self.radio_local.state())
        shape = {
            "tab": self.tab,
            "left": self._left,
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
            "texts": (self.body1.stringValue(), error, text, self._saving, self.key_tag.label.stringValue()),
            "dictation": dictation,
        }
        if force or shape != self._shape:
            self._shape = shape
            self._layout(shape)

    @objc.python_method
    def _refresh_dictation(self) -> tuple:
        """Dictate's live state (microphone, Globe key, models, keys) onto its views. Returns
        what decides its layout, so a change re-lays the window out, and leaves which of its
        steps are done in self._dictate_done."""
        ctl = self.ctl
        secondary = theme.CONTENT_DIM
        mic = ctl.microphone_status()
        if mic == "granted":
            self._set_status_row(self.mic_row, "Microphone allowed", "checkmark.circle.fill", NSColor.systemGreenColor())
        elif mic == "undetermined":
            self._set_status_row(self.mic_row, "Allow the microphone so Lyssna can hear you", PENDING, secondary)
        else:
            self._set_status_row(self.mic_row, "Microphone access is off. Turn it on in Settings.", PENDING, secondary)

        on_fn = str(ctl.cfg.get("dictation", {}).get("hotkey", "fn")) == "fn"
        globe_ok = not on_fn or self._globe == "nothing"
        if on_fn:
            self._read_globe()
            if globe_ok:
                self._set_status_row(self.globe_row, "Globe key set to Do Nothing", "checkmark.circle.fill",
                                     NSColor.systemGreenColor())
            else:
                self._set_status_row(self.globe_row, "Set “Press Globe key to” to Do Nothing", PENDING, secondary)

        local_only = ctl.local_only()
        if (local_only or not self._dictation_online_allowed()) and self.dict_online.state():
            self._choose_dictation(None)
        models = ctl.local_dictation_models()
        can_run = transcribers.REGISTRY[transcribers.LOCAL].can_run()
        self.dict_local.setEnabled_(can_run)
        self.dict_local_note.setStringValue_(
            "Your voice and text stay on this Mac. One download of "
            f"{_size_text(sum(m.total_bytes for m in models))}." if can_run else "")
        progress = ctl.dictation_download_progress()
        ready = can_run and all(m.present() for m in models)
        error = ""
        if not can_run:
            error = "Dictation on this Mac is not available in this build."
        elif progress is None and not ready:
            error = next((m.download().error for m in models if not m.present() and m.download().error), "")
        if progress is not None:
            self.dict_progress.set_fraction(progress)
            self.dict_progress_text.setStringValue_(f"{int(progress * 100)}%")
        self.dict_error.setStringValue_(error)
        self.dict_download.setTitle_("Try Again" if error and can_run else "Download")

        texts = []
        keys_saved = True
        for row in self.key_rows:
            state = ctl.dictation_key_state(row.name)
            keys_saved = keys_saved and state is True
            text, tone = row.message
            if not text and not row.saving:
                if state and self._in_use(row.name):
                    text, tone = "Using your saved key.", "good"
                elif state:
                    text = "A key is already saved. Paste a new one to replace it."
            row.status.setStringValue_(text)
            row.status.setTextColor_({"good": NSColor.systemGreenColor(), "bad": NSColor.systemRedColor()}.get(
                tone, theme.CONTENT_DIM))
            for view in (row.field, row.save):
                view.setEnabled_(not row.saving)
            texts.append((text, row.saving))

        local_chosen = bool(self.dict_local.state())
        self._dictate_done = (mic == "granted" and globe_ok, ready if local_chosen else keys_saved)
        self._set_step_icon(self.dicon1, 1, self._dictate_done[0])
        self._set_step_icon(self.dicon2, 2, self._dictate_done[1])
        self._set_step_icon(self.dicon3, 3, False)
        return (mic, on_fn, globe_ok, local_only or not self._dictation_online_allowed(), local_chosen,
                progress is not None, ready, error, can_run, tuple(texts), self.dict_local_note.stringValue())

    @objc.python_method
    def _in_use(self, name: str) -> bool:
        if name in transcribers.REGISTRY:
            return self.ctl.current_transcriber() == name
        return self.ctl.current_cleaner() == name

    # -- layout ------------------------------------------------------------------------
    #
    # Explicit frames, top to bottom. The helpers below place a view and note it as visible;
    # whatever in the steps was not placed is hidden afterwards.

    @objc.python_method
    def put(self, view, x, y, width=None) -> float:
        _, h = _fit(view, width) if width is not None or isinstance(view, (NSTextField, NSButton)) else view.frame().size
        view.setFrameOrigin_((x, y))
        self._visible.add(view)
        return h

    @objc.python_method
    def centre_row(self, views_x, y) -> float:
        """Place views on one line, vertically centred on the tallest; returns the line height."""
        h = max(v.frame().size.height for v, _ in views_x)
        for v, x in views_x:
            v.setFrameOrigin_((x, y + (h - v.frame().size.height) / 2))
            self._visible.add(v)
        return h

    @objc.python_method
    def _step(self, icon, head, y) -> float:
        hh = self.put(head, TEXT_X, y)
        icon.setFrameOrigin_((MARGIN, y + (hh - ICON) / 2))
        self._visible.add(icon)
        return hh

    @objc.python_method
    def _sep(self, view, y) -> float:
        """A hairline between steps, 16 below the step above; returns where the next step starts."""
        view.setFrame_(NSMakeRect(MARGIN, y + 16, W - 2 * MARGIN, 1))
        self._visible.add(view)
        return y + 33

    @objc.python_method
    def _option_head(self, radio, tag, y) -> float:
        """A card's first line: its radio on the left, its privacy tag on the right."""
        _fit(radio)
        room = CARD_R - CARD_X - radio.frame().size.width - 12
        if tag.frame().size.width > room:  # a long custom endpoint: the tag's words give way
            tag.label.setFrameSize_((max(0.0, room - 16), tag.label.frame().size.height))
            tag.setFrameSize_((room, tag.frame().size.height))
        return self.centre_row([(radio, CARD_X), (tag, CARD_R - tag.frame().size.width)], y)

    @objc.python_method
    def _place_card(self, card, top, bottom, chosen: bool) -> None:
        card.setFrame_(NSMakeRect(TEXT_X, top, TEXT_W, bottom - top))
        card.border_fn = (lambda: CARD_BORDER_ON) if chosen else (lambda: theme.HAIRLINE)
        card.setNeedsDisplay_(True)
        self._visible.add(card)

    @objc.python_method
    def _layout(self, show: dict) -> None:
        """Lay out the current tab's steps, then fit the window to header, steps and footer
        (keeping its top edge). Only the steps scroll when the screen is too short."""
        self._visible = set()
        y = 20.0
        if self.tab == LISTEN:
            y = self._layout_listen(show, y)
        else:
            y = self._layout_dictation(show["dictation"], y)
        y += 20
        for view in self.content.subviews():
            view.setHidden_(view not in self._visible)
        self.content.setFrameSize_((W, y))

        head_h = self._layout_header()
        self._layout_footer()
        total = head_h + y + FOOTER_H
        screen = self.window.screen() or screen_under_mouse()
        limit = screen.visibleFrame().size.height - 2 * SCREEN_GAP if screen is not None else total
        h = min(total, max(limit, 320.0))
        frame = self.window.frame()
        new = self.window.frameRectForContentRect_(NSMakeRect(0, 0, W, h))
        top = frame.origin.y + frame.size.height
        self.window.setFrame_display_(NSMakeRect(frame.origin.x, top - new.size.height, W, new.size.height), True)
        self.header.setFrame_(NSMakeRect(0, 0, W, head_h))
        self.scroll.setFrame_(NSMakeRect(0, head_h, W, h - head_h - FOOTER_H))
        self.footer.setFrame_(NSMakeRect(0, h - FOOTER_H, W, FOOTER_H))

    @objc.python_method
    def _layout_header(self) -> float:
        """The mark, the welcome and the switch, centred; returns the header's height."""
        y = 8.0
        mw = self.mark.frame().size.width
        self.mark.setFrameOrigin_(((W - mw) / 2, y))
        y += MARK_H + 10
        tw, th = _fit(self.title)
        self.title.setFrameOrigin_(((W - tw) / 2, y))
        y += th + 6
        _, sh = _fit(self.subtitle, W - 2 * MARGIN)
        self.subtitle.setFrameOrigin_((MARGIN, y))
        y += sh + 16
        seg_w = max(self.tab_listen.preferred_width(), self.tab_dictate.preferred_width())  # equal halves
        self.tab_listen.setFrame_(NSMakeRect(SEG_PAD, SEG_PAD, seg_w, SEG_H))
        self.tab_dictate.setFrame_(NSMakeRect(SEG_PAD + seg_w + 2, SEG_PAD, seg_w, SEG_H))
        track_w = 2 * seg_w + 2 + 2 * SEG_PAD
        self.switch.setFrame_(NSMakeRect(round((W - track_w) / 2), y, track_w, SEG_H + 2 * SEG_PAD))
        for view in (self.tab_listen, self.tab_dictate):
            view.setNeedsDisplay_(True)
        y += SEG_H + 2 * SEG_PAD + 18
        self.header_line.setFrame_(NSMakeRect(0, y, W, 1))
        return y + 1

    @objc.python_method
    def _layout_footer(self) -> None:
        """A hairline, the status line on the left and Done on the right."""
        self.footer_line.setFrame_(NSMakeRect(0, 0, W, 1))
        dw, dh = self.done.frame().size
        self.done.setFrameOrigin_((W - 20 - dw, (FOOTER_H - dh) / 2))
        _, sh = _fit(self.footer_status)
        self.footer_status.setFrameOrigin_((MARGIN, (FOOTER_H - sh) / 2))

    @objc.python_method
    def _layout_listen(self, show: dict, y: float) -> float:
        """Listen's three steps, from y down; returns where they end."""
        put, centre_row = self.put, self.centre_row

        # 1 Permission
        y += self._step(self.icon1, self.head1, y) + 4
        y += put(self.body1, TEXT_X, y, TEXT_W)
        row = []
        x = TEXT_X  # pills have no transparent inset, so their edge lines up with the text as is
        for flag, btn in (("open_settings", self.open_settings), ("relaunch", self.relaunch_btn)):
            if show[flag]:
                row.append((btn, x))
                x += btn.frame().size.width + 8
        if row:
            y += 8 + centre_row(row, y + 8)
        y = self._sep(self.sep1, y)

        # 2 Voice: two option cards
        y += self._step(self.icon2, self.head2, y) + 10
        top = y
        y += CARD_PAD + self._option_head(self.radio_local, self.local_tag, y + CARD_PAD) + 4
        y += put(self.local_note, SUB_X, y, SUB_W)
        if show["local_body"]:
            if show["download"]:
                y += 10 + centre_row([(self.download, SUB_X)], y + 10)
            elif show["progress"]:
                self.progress_text.setStringValue_("100%")  # measure the widest it gets
                tw, _ = _fit(self.progress_text)
                self.progress.setFrameSize_((SUB_W - tw - 10, BAR_H))
                self.progress_text.setFrameSize_((tw, self.progress_text.frame().size.height))
                self._refresh_progress_text()
                y += 10 + centre_row([(self.progress, SUB_X), (self.progress_text, SUB_X + SUB_W - tw)], y + 10)
            elif show["ready"]:
                y += 8 + centre_row([(self.ready, SUB_X)], y + 8)
            if show["local_error"]:
                y += 6 + put(self.local_error, SUB_X, y + 6, SUB_W)
        y += CARD_PAD
        self._place_card(self.card_local, top, y, show["local_body"])
        if show["radio_key"]:
            y += 8
            top = y
            y += CARD_PAD + self._option_head(self.radio_key, self.key_tag, y + CARD_PAD) + 4
            y += put(self.key_note, SUB_X, y, SUB_W)
            if show["key_body"]:
                y += 10
                self.popup.sizeToFit()
                y += centre_row([(self.popup, SUB_X - 3), (self.get_key, SUB_X + self.popup.frame().size.width + 10)], y) + 8
                save_w = self.save.frame().size.width
                self._size_key_box(self.key_box, self.key_field, SUB_W - save_w - 8)  # the Save pill's edge ends the row
                y += centre_row([(self.key_box, SUB_X), (self.save, CARD_R - save_w)], y)
                if show["key_status"]:
                    shift = 22.0 if self._saving else 0.0
                    h = put(self.key_status, SUB_X + shift, y + 8, SUB_W - shift)
                    if self._saving:
                        self.spinner.setFrameOrigin_((SUB_X, y + 8 + (h - 16) / 2))
                        self._visible.add(self.spinner)
                    y += 8 + h
            y += CARD_PAD
            self._place_card(self.card_key, top, y, not show["local_body"])
        if show["local_only_note"]:
            y += 8 + put(self.local_only_note, TEXT_X, y + 8, TEXT_W)
        y = self._sep(self.sep2, y)

        # 3 Try it
        y += self._step(self.icon3, self.head3, y) + 8
        _fit(self.try_text)
        y += centre_row([(self.try_text, TEXT_X), (self.keycap, TEXT_X + self.try_text.frame().size.width + 8)], y) + 10
        y += centre_row([(self.sample, TEXT_X)], y)
        if show["try_note"]:
            y += 6 + put(self.try_note, TEXT_X, y + 6, TEXT_W)
        return y

    @objc.python_method
    def _layout_dictation(self, state: tuple, y: float) -> float:
        """Dictate's three steps, from y down; returns where they end."""
        put, centre_row = self.put, self.centre_row
        mic, on_fn, globe_ok, online_off, local_chosen, downloading, ready, error, can_run, _, _ = state

        # 1 Permission
        y += self._step(self.dicon1, self.dhead1, y) + 8

        def check(status_row, pill, y):
            """A status line, with its pill when something is left to do: at the right end of
            the line when both fit, else under the words."""
            if pill is None:
                return y + centre_row([(status_row, TEXT_X)], y)
            pw = pill.frame().size.width
            if status_row.frame().size.width + 12 + pw <= TEXT_W:
                return y + centre_row([(status_row, TEXT_X), (pill, TEXT_X + TEXT_W - pw)], y)
            y += centre_row([(status_row, TEXT_X)], y)
            return y + 8 + centre_row([(pill, TEXT_X + 20)], y + 8)

        y = check(self.mic_row, {"undetermined": self.mic_allow, "denied": self.mic_settings}.get(mic), y)
        if on_fn:
            y = check(self.globe_row, None if globe_ok else self.globe_settings, y + 10)
        y = self._sep(self.dsep1, y)

        # 2 Engine: two option cards
        y += self._step(self.dicon2, self.dhead2, y) + 10
        top = y
        y += CARD_PAD + self._option_head(self.dict_local, self.dict_local_tag, y + CARD_PAD)
        if self.dict_local_note.stringValue():
            y += 4 + put(self.dict_local_note, SUB_X, y + 4, SUB_W)
        if local_chosen:
            if downloading:
                self.dict_progress_text.setStringValue_("100%")  # measure the widest it gets
                tw, _ = _fit(self.dict_progress_text)
                self.dict_progress.setFrameSize_((SUB_W - tw - 8, BAR_H))
                self.dict_progress_text.setFrameSize_((tw, self.dict_progress_text.frame().size.height))
                progress = self.ctl.dictation_download_progress()
                self.dict_progress_text.setStringValue_(f"{int((progress or 0) * 100)}%")
                self.dict_progress_text.setAlignment_(AppKit.NSTextAlignmentRight)
                y += 10 + centre_row([(self.dict_progress, SUB_X), (self.dict_progress_text, SUB_X + SUB_W - tw)], y + 10)
            elif ready:
                y += 8 + centre_row([(self.dict_ready, SUB_X)], y + 8)
            elif can_run:
                y += 10 + centre_row([(self.dict_download, SUB_X)], y + 10)
            if error:
                y += 6 + put(self.dict_error, SUB_X, y + 6, SUB_W)
        y += CARD_PAD
        self._place_card(self.card_dict_local, top, y, local_chosen)
        if online_off:
            y += 8 + put(self.dict_local_only_note, TEXT_X, y + 8, TEXT_W)
        else:
            y += 8
            top = y
            y += CARD_PAD + self._option_head(self.dict_online, self.dict_online_tag, y + CARD_PAD) + 4
            y += put(self.dict_online_note, SUB_X, y, SUB_W)
            if not local_chosen:
                for row in self.key_rows:
                    y += 14
                    _fit(row.head)
                    y += centre_row([(row.head, SUB_X), (row.get_key, CARD_R - row.get_key.frame().size.width)], y) + 8
                    save_w = row.save.frame().size.width
                    self._size_key_box(row.box, row.field, SUB_W - save_w - 8)  # the Save pill's edge ends the row
                    y += centre_row([(row.box, SUB_X), (row.save, CARD_R - save_w)], y)
                    if row.status.stringValue():
                        shift = 24.0 if row.saving else 0.0
                        h = put(row.status, SUB_X + shift, y + 8, SUB_W - shift)
                        if row.saving:
                            row.spinner.setFrameOrigin_((SUB_X, y + 8 + (h - 16) / 2))
                            self._visible.add(row.spinner)
                        y += 8 + h
            y += CARD_PAD
            self._place_card(self.card_dict_online, top, y, not local_chosen)
        y = self._sep(self.dsep2, y)

        # 3 Try it: Hold [fn] and speak.   Hands-free: [fn] [Space]
        y += self._step(self.dicon3, self.dhead3, y) + 8
        row, x = [], TEXT_X
        for view, gap in ((self.hold_text, 6), (self.hold_keys, 6), (self.hold_after, 0)):
            w, _ = _fit(view) if isinstance(view, NSTextField) else view.frame().size
            row.append((view, x))
            x += w + gap
        y += centre_row(row, y) + 6
        y += put(self.dictate_body, TEXT_X, y, TEXT_W) + 10
        _fit(self.free_text)
        y += centre_row([(self.free_text, TEXT_X), (self.free_keys, TEXT_X + self.free_text.frame().size.width + 6)], y)
        return y

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

    # -- step 4 actions -------------------------------------------------------------

    def allowMicrophone_(self, sender):
        self.ctl.request_microphone()  # macOS shows its prompt; the next tick shows the answer

    def openMicrophoneSettings_(self, sender):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(MIC_SETTINGS_URL))

    def openKeyboardSettings_(self, sender):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(KEYBOARD_SETTINGS_URL))
        self._globe_read_at = 0.0  # read the setting again soon after the user changes it

    def chooseDictation_(self, sender):
        """Both dictation radios share this action (their own group, apart from the voice's).
        An option switches the engines only when it can run now: the models are here, or a
        key is saved. Otherwise Download or Save does it."""
        local = sender is self.dict_local
        self.dict_local.setState_(1 if local else 0)
        self.dict_online.setState_(0 if local else 1)
        for row in self.key_rows:
            row.message = ("", "secondary")
        try:
            if local:
                if all(m.present() for m in self.ctl.local_dictation_models()):
                    self.ctl.use_local_dictation()
            else:
                for row in self.key_rows:
                    if self.ctl.dictation_key_state(row.name) is True:
                        self._use_dictation(row.name)
        except ValueError as e:
            log.info("dictation engine not switched: %s", e)
        self.on_change()
        self._refresh(force=True)
        if not local:
            self.window.makeFirstResponder_(self.key_rows[0].field)

    def downloadModels_(self, sender):
        try:
            self.ctl.use_local_dictation()  # starts the downloads; the progress bar follows them
        except ValueError as e:
            log.info("dictation engine not switched: %s", e)
        self.on_change()
        self._refresh(force=True)

    @objc.python_method
    def _use_dictation(self, name: str) -> None:
        if name in transcribers.REGISTRY:
            self.ctl.set_transcriber(name)
        else:
            self.ctl.set_cleaner(name)

    @objc.python_method
    def _row_for(self, sender) -> _KeyRow | None:
        return next((r for r in self.key_rows if sender in (r.get_key, r.field, r.save)), None)

    def getDictationKey_(self, sender):
        row = self._row_for(sender)
        url = _engine_class(row.name).key_url if row else ""
        if url:
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))

    def saveDictationKey_(self, sender):
        row = self._row_for(sender)
        if row is None or row.saving:
            return
        key = row.field.stringValue()
        if not key.strip():
            row.message = ("Paste your API key first.", "bad")
            self._refresh(force=True)
            return
        row.saving = True
        row.message = ("Checking the key\u2026", "secondary")
        row.spinner.startAnimation_(None)
        self._refresh(force=True)

        def check():
            error = self.ctl.add_dictation_key(row.name, key)  # one free request, then save
            on_main(self._dictation_saved, row, error)

        threading.Thread(target=check, daemon=True, name="lyssna-key-check").start()

    @objc.python_method
    def _dictation_saved(self, row: _KeyRow, error: str) -> None:
        row.saving = False
        row.spinner.stopAnimation_(None)
        if error:
            row.message = (error[:300], "bad")
        else:
            row.field.setStringValue_("")
            row.message = (f"Saved. Dictation now uses {_engine_class(row.name).vendor} for {row.job}.", "good")
            self.on_change()
        self._refresh(force=True)

    def done_(self, sender):
        self.window.performClose_(None)

    @objc.python_method
    def _use(self, engine: str) -> None:
        try:
            self.ctl.set_provider(engine)
        except ValueError as e:
            self._key_message = (str(e), "bad")
        self.on_change()
