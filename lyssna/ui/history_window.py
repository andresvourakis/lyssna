"""The History window: what you dictated, and the dictionary Lyssna uses to spell names.

    History                                           (mark + serif headline)
    1,204          18,930          412                 words this week, in total, dictations
    Lyssna is free and made by one person. ♥ Support it   (past 1,000 words; see lyssna.support)
    ( Dictations | Dictionary )                        one segmented control
    Dictations: search, then the list grouped by day; each row copies, shows the original
                transcript, takes a fix ("Fix a Word…", which teaches the dictionary) or goes;
                a dictation that failed shows "Couldn't transcribe" with Retry and More
                always visible, and a Retry that works turns it into a normal row (not pasted)
    Dictionary: add a word, the words most used first, where each came from
    Kept for 30 days on this Mac                 Clear History…

It reads the history store and the vocabulary, both of which can change while it is open (a
dictation lands, the edit watcher learns a word, `lyssna vocab` runs), so it polls their files
a couple of times a second while it is visible and redraws only when something changed.

The look follows the setup window: the brand surface, a serif headline, system font for the
rest, everything drawn in the brand colours (nothing takes the blue accent), hairlines between
the three bands. Spacing sits on an 8 pt grid; every measurement is a named constant below.
Rows are built for at most ROW_CAP entries at a time, with a Show Older pill for the rest, so a
long history stays smooth. Dictated text is shown here and never logged."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

import AppKit
import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSFont,
    NSFontManager,
    NSImageView,
    NSMenu,
    NSMenuItem,
    NSMutableParagraphStyle,
    NSPasteboard,
    NSScrollView,
    NSTextField,
    NSView,
)
from Foundation import (
    NSAttributedString,
    NSDate,
    NSDateFormatter,
    NSInsetRect,
    NSMakeRect,
    NSObject,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSTimer,
)

from .. import support
from ..dictation import history as history_mod
from ..dictation import vocabulary as vocabulary_mod
from ..dictation.learn import find_corrections
from . import mark as mark_mod
from . import theme
from .mainthread import later, on_main
from .mark import MarkView, draw_mark
from .panel import TintedView, screen_under_mouse
from .setup_window import EditingWindow, FlippedView, PillButton, _fit, _font, _label, _serif, _symbol, _wrapping

log = logging.getLogger("lyssna.ui.history")

# -- measurements (8 pt grid) -------------------------------------------------------------------

# Window
W, H = 560.0, 680.0           # default content size
MIN_W, MIN_H = 480.0, 520.0
MARGIN = 32.0                 # left and right of all content
TOP = 28.0                    # above the headline
WIDE = 720.0                  # past this window width the content column stops growing...
COLUMN_MAX = 656.0            # ...at this width, centred

# Header
MARK_H = 28.0
TITLE_SIZE = 22.0
MARK_GAP = 10.0               # mark to headline
HEAD_TO_STATS = 24.0
STAT_SIZE = 28.0
STAT_LABEL_SIZE = 12.0
STATS_TO_SUPPORT = 12.0       # the totals to the support line under them
SUPPORT_SIZE = 12.0
SUPPORT_GAP = 2.0             # the sentence to its action
SYMBOL_SMALLER = 2.0          # a symbol in front of a text button is this much smaller than the text
STATS_TO_TABS = 28.0
LABEL_INSET = 2.0             # an NSTextField label draws its text this far in from its frame

# Segmented control
SEG_H = 32.0
SEG_PAD = 18.0                # label to segment edge
SEG_INSET = 3.0               # selection capsule inside the track
SEG_SIZE = 13.0
SEG_SLIDE = 0.18              # seconds for the capsule to slide
TABS_TO_FIELD = 16.0

# Fields
FIELD_H = 34.0
FIELD_RADIUS = 10.0
FIELD_PAD = 12.0              # text and icon in from the field's edge
FIELD_SIZE = 13.0
SEARCH_ICON = 13.0
SEARCH_ICON_GAP = 8.0         # magnifier to text
FOCUS_ALPHA = 0.35            # the focus hairline, in CONTENT at this alpha
BUTTON_GAP = 8.0              # field to Add, Save to Cancel
FIELD_TO_LIST = 16.0
NOTE_GAP = 8.0                # field to the note under it

# List
ROW_PAD_X = 16.0              # inside the hover background, left and right
ROW_PAD_Y = 12.0              # top and bottom
ROW_RADIUS = 10.0
ROW_GAP = 4.0                 # between rows
TEXT_SIZE = 14.0
TEXT_LINE_H = 19.0            # 14 pt at about 1.35
TEXT_LINES = 4                # a dictation shows this many lines until clicked
TIME_SIZE = 11.0
TIME_W = 56.0                 # the time column
ICON_BTN = 28.0               # the row icon buttons' circular hit area
ICON_SIZE = 14.0
ICON_SIZE_SMALL = 12.0        # the dictionary's delete
ICON_GAP = 4.0                # between the two icons
ICONS_TO_TEXT = 16.0          # text stops this far from the icons
NOTE_SIZE = 12.0              # the original transcript and "Learned:"
NOTE_TOP = 4.0
HEADER_SIZE = 12.0
HEADER_ABOVE = 24.0
HEADER_ABOVE_FIRST = 16.0
HEADER_BELOW = 8.0
LIST_TOP = 8.0                # above the first dictionary row
LIST_BOTTOM = 16.0
TERM_SIZE = 14.0
HEARD_SIZE = 12.0
HEARD_GAP = 2.0
SOURCE_SIZE = 11.0
SOURCE_TO_ICON = 4.0
PILL_ABOVE = 24.0             # Show Older
EDIT_TO_BUTTONS = 8.0
SMALL_PILL_H = 28.0           # Save, Cancel
EMPTY_SIZE = 14.0
EMPTY_W = 320.0
EMPTY_MARK_H = 28.0
EMPTY_MARK_ALPHA = 0.25
EMPTY_MARK_GAP = 12.0
EMPTY_MAX_LINES = 6
EMPTY_STEP = 4.0              # balanced lines: widen by this until the text fits its line count

# Footer
FOOTER_H = 44.0
FOOTER_SIZE = 12.0
DISABLED_ALPHA = 0.30         # a text button with nothing to do

# Behaviour
ROW_CAP = 300                 # rows built at once; Show Older adds this many more
POLL = 0.5                    # seconds between checks for changes while the window is open
COPIED_FOR = 1.2              # seconds the copy icon shows a check
FRAME = 1 / 60                # animation tick

FAILED_TEXT = "Couldn't transcribe. {n} s of audio"
RETRY = "Retry"
RETRYING = "Retrying..."
EMPTY_DICTATIONS = "Your dictations will appear here. Hold fn and speak."
EMPTY_SEARCH = "No dictations match."
EMPTY_DICTIONARY = "Words Lyssna should know: names, products, jargon. It also learns when you correct a dictation."
SOURCE_BADGE = {"learned": "Learned", "manual": "Added"}  # any other source reads "Added"
TABS = ("dictations", "dictionary")


# -- small pure helpers (tested on their own) ----------------------------------------------------


def copy_to_clipboard(text: str) -> None:
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, AppKit.NSPasteboardTypeString)


def figure(n: int) -> str:
    """A whole number with thousands separators: 18930 -> "18,930"."""
    return f"{int(n):,}"


def retention_text(days: int) -> str:
    if days == 0:
        return "History is off"
    if days < 0:
        return "Kept on this Mac until you clear it"
    return f"Kept for {days} day{'s' if days != 1 else ''} on this Mac"


def day_label(day: date, today: date) -> str:
    """"Today", "Yesterday", "Mon 16 Nov", or "Mon 16 Nov 2025" for an earlier year."""
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    label = f"{day:%a} {day.day} {day:%b}"
    return label if day.year == today.year else f"{label} {day.year}"


def failed_text(audio_seconds: float) -> str:
    """The dim line of a failed row: "Couldn't transcribe. 12 s of audio"."""
    return FAILED_TEXT.format(n=max(1, round(audio_seconds)))


def learned_text(terms: list[str]) -> str:
    return f"Learned: {', '.join(terms)}" if terms else ""


def column(width: float) -> tuple[float, float]:
    """The content column for a window this wide: (left edge, width). Margins on both sides, and
    past WIDE the column keeps COLUMN_MAX and centres."""
    if width > WIDE:
        w = COLUMN_MAX
        return round((width - w) / 2), w
    return MARGIN, width - 2 * MARGIN


def ease_out(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


_time_formatter = None


def time_label(when: datetime) -> str:
    """The time of day in the user's own 12 or 24 hour style."""
    global _time_formatter
    if _time_formatter is None:
        _time_formatter = NSDateFormatter.alloc().init()
        _time_formatter.setLocalizedDateFormatFromTemplate_("jmm")
    return str(_time_formatter.stringFromDate_(NSDate.dateWithTimeIntervalSince1970_(when.timestamp())))


# -- text helpers -----------------------------------------------------------------------------


def _italic(size: float):
    return NSFontManager.sharedFontManager().convertFont_toHaveTrait_(_font(size), AppKit.NSItalicFontMask)


def _tabular(size: float, weight=AppKit.NSFontWeightRegular):
    return NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight)


def _styled(text: str, font, color, line_h: float | None = None, align=None) -> NSAttributedString:
    """Text with a font, a colour and (optionally) a fixed line height, so lines sit on an even
    rhythm and two labels with the same line height share their baselines' grid."""
    para = NSMutableParagraphStyle.alloc().init()
    para.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    if line_h:
        para.setMinimumLineHeight_(line_h)
        para.setMaximumLineHeight_(line_h)
    if align is not None:
        para.setAlignment_(align)
    attrs = {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: color,
             AppKit.NSParagraphStyleAttributeName: para}
    return NSAttributedString.alloc().initWithString_attributes_(text, attrs)


def _styled_label(text: str, font, color, line_h: float | None = None, wrap: bool = True, align=None) -> NSTextField:
    lbl = NSTextField.wrappingLabelWithString_("") if wrap else NSTextField.labelWithString_("")
    lbl.setSelectable_(False)
    lbl.setAttributedStringValue_(_styled(text, font, color, line_h, align))
    return lbl


def _ink_left(label) -> float:
    """How far right of its frame a label's first glyph actually starts: the cell's inset plus
    the glyph's side bearing (larger at bigger sizes). Used to line labels up by their ink."""
    s = label.attributedStringValue()
    if s.length() == 0:
        return LABEL_INSET
    opts = AppKit.NSStringDrawingUsesLineFragmentOrigin | AppKit.NSStringDrawingUsesDeviceMetrics
    r = s.boundingRectWithSize_options_context_((10_000, 10_000), opts, None)
    return LABEL_INSET + r.origin.x


def _height(label, width: float) -> float:
    label.setPreferredMaxLayoutWidth_(width)
    return label.cell().cellSizeForBounds_(NSMakeRect(0, 0, width, 100_000)).height


# -- views ---------------------------------------------------------------------------------------


class HistorySurfaceView(FlippedView):
    """The window's content view. Paints the surface itself, so an offscreen snapshot of the
    view looks like the window."""

    def drawRect_(self, dirty):
        theme.SURFACE.set()
        AppKit.NSRectFill(dirty)


class HistoryEmptyMark(NSView):
    """The Lyssna mark, faint, above an empty list."""

    def drawRect_(self, dirty):
        draw_mark(self.bounds(), theme.content(EMPTY_MARK_ALPHA))

    def isAccessibilityElement(self):
        return False


def _tracking(view) -> None:
    opts = AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveAlways | AppKit.NSTrackingInVisibleRect
    view.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
        NSMakeRect(0, 0, 0, 0), opts, view, None))


class HistoryIconButton(NSButton):
    """A symbol in a 28 pt circle: dim at rest, full colour on a soft disc under the cursor."""

    @classmethod
    @objc.python_method
    def make(cls, symbol: str, size: float, target, action: str, tip: str) -> HistoryIconButton:
        b = cls.buttonWithImage_target_action_(_symbol(symbol, size), target, action)
        b.symbol = symbol
        b.symbol_size = size
        b.hovered = False
        b.setBordered_(False)
        b.setImagePosition_(AppKit.NSImageOnly)
        b.setContentTintColor_(theme.CONTENT_DIM)
        b.setFrameSize_((ICON_BTN, ICON_BTN))
        b.setToolTip_(tip)
        b.cell().setAccessibilityLabel_(tip)
        _tracking(b)
        return b

    @objc.python_method
    def set_symbol(self, symbol: str) -> None:
        self.symbol = symbol
        self.setImage_(_symbol(symbol, self.symbol_size))

    @objc.python_method
    def set_hover(self, on: bool) -> None:
        self.hovered = on
        self.setContentTintColor_(theme.CONTENT if on else theme.CONTENT_DIM)
        self.setNeedsDisplay_(True)

    def mouseEntered_(self, event):
        self.set_hover(True)

    def mouseExited_(self, event):
        self.set_hover(False)

    def drawRect_(self, dirty):
        if self.hovered or self.isHighlighted():
            theme.FILL_SOFT.set()
            NSBezierPath.bezierPathWithOvalInRect_(self.bounds()).fill()
        objc.super(HistoryIconButton, self).drawRect_(dirty)


class HistoryTextButton(NSButton):
    """A quiet text button: dim, full colour under the cursor, no pill and no outline. The title
    is an attributed string in the brand colour (a borderless button's tint does not reach it).
    With `link`, it reads as a link inside a dim sentence instead: full colour at rest and
    underlined under the cursor. `symbol` puts an SF Symbol in front of the title, in the
    title's colour (the heart on every support link)."""

    @classmethod
    @objc.python_method
    def make(cls, title: str, target, action: str, size: float = FOOTER_SIZE,
             weight=AppKit.NSFontWeightMedium, link: bool = False, symbol: str = "") -> HistoryTextButton:
        b = cls.buttonWithTitle_target_action_(title, target, action)
        b.setBordered_(False)
        if symbol:
            b.setImage_(_symbol(symbol, size - SYMBOL_SMALLER, weight))
            b.setImagePosition_(AppKit.NSImageLeading)
            b.setImageHugsTitle_(True)
            b.setContentTintColor_(theme.CONTENT)
        b.text = title
        b.text_font = _font(size, weight)
        b.link = link
        b.hovered = False
        b._paint()
        b.sizeToFit()
        _tracking(b)
        return b

    @objc.python_method
    def _paint(self) -> None:
        if not self.isEnabled():
            color = theme.content(DISABLED_ALPHA)
        else:
            color = theme.CONTENT if self.hovered or self.link else theme.CONTENT_DIM
        attrs = {AppKit.NSFontAttributeName: self.text_font, AppKit.NSForegroundColorAttributeName: color}
        if self.link and self.hovered:
            attrs[AppKit.NSUnderlineStyleAttributeName] = AppKit.NSUnderlineStyleSingle
        self.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(self.text, attrs))

    @objc.python_method
    def set_active(self, on: bool) -> None:
        self.setEnabled_(on)
        self._paint()

    def mouseEntered_(self, event):
        self.hovered = True
        self._paint()

    def mouseExited_(self, event):
        self.hovered = False
        self._paint()


class HistoryFieldBox(FlippedView):
    """The filled, rounded backing of a text field, with a hairline while the field has focus."""

    @classmethod
    @objc.python_method
    def make(cls) -> HistoryFieldBox:
        v = cls.alloc().initWithFrame_(NSMakeRect(0, 0, 200, FIELD_H))
        v.focused = False
        return v

    @objc.python_method
    def set_focused(self, on: bool) -> None:
        if self.focused != on:
            self.focused = on
            self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), FIELD_RADIUS, FIELD_RADIUS)
        theme.FILL_SOFT.set()
        path.fill()
        if self.focused:
            ring = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSInsetRect(self.bounds(), 0.5, 0.5), FIELD_RADIUS - 0.5, FIELD_RADIUS - 0.5)
            ring.setLineWidth_(1.0)
            theme.content(FOCUS_ALPHA).set()
            ring.stroke()


class HistoryTextField(NSTextField):
    """A borderless field that lights up its box's focus hairline while it is being edited."""

    @classmethod
    @objc.python_method
    def make(cls, placeholder: str, target, action: str | None, box, multiline: bool = False) -> HistoryTextField:
        f = cls.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 20))
        f.box = box
        if placeholder:
            attrs = {AppKit.NSFontAttributeName: _font(FIELD_SIZE), AppKit.NSForegroundColorAttributeName: theme.CONTENT_DIM}
            f.setPlaceholderAttributedString_(NSAttributedString.alloc().initWithString_attributes_(placeholder, attrs))
        f.setBezeled_(False)
        f.setBordered_(False)
        f.setDrawsBackground_(False)
        f.setFocusRingType_(AppKit.NSFocusRingTypeNone)  # the box draws focus; a system ring would be blue
        f.setFont_(_font(TEXT_SIZE if multiline else FIELD_SIZE))
        f.setTextColor_(theme.CONTENT)
        f.cell().setUsesSingleLineMode_(not multiline)
        f.cell().setScrollable_(not multiline)
        f.cell().setWraps_(multiline)
        f.setDelegate_(target)
        if action:
            f.setTarget_(target)
            f.setAction_(action)
        box.addSubview_(f)
        return f

    def becomeFirstResponder(self):
        ok = objc.super(HistoryTextField, self).becomeFirstResponder()
        if ok:
            self.box.set_focused(True)
        return ok

    def textDidEndEditing_(self, note):
        objc.super(HistoryTextField, self).textDidEndEditing_(note)
        self.box.set_focused(False)


class HistorySegments(FlippedView):
    """Two tabs in one track: a filled capsule marks the chosen one and slides to the other.
    Drawn in drawRect_ (the capsule's position is animated by a timer), so the dynamic colours
    re-resolve with the appearance. `on_change(index)` runs when the user picks a tab."""

    @classmethod
    @objc.python_method
    def make(cls, titles: list[str], on_change: Callable[[int], None]) -> HistorySegments:
        v = cls.alloc().initWithFrame_(NSMakeRect(0, 0, 100, SEG_H))
        v.titles = titles
        v.on_change = on_change
        v.font = _font(SEG_SIZE, AppKit.NSFontWeightMedium)
        widths = []
        for t in titles:
            s = NSAttributedString.alloc().initWithString_attributes_(t, {AppKit.NSFontAttributeName: v.font})
            widths.append(round(s.size().width) + 2 * SEG_PAD)
        v.widths = widths
        v.lefts = [sum(widths[:i]) for i in range(len(widths))]
        v.selected = 0
        v.pos = (v.lefts[0], widths[0])   # the capsule as drawn: (x, width)
        v.anim = None
        v.timer = None
        v.setFrameSize_((sum(widths), SEG_H))
        v.setAccessibilityRole_(AppKit.NSAccessibilityTabGroupRole)
        return v

    @objc.python_method
    def select(self, index: int, animated: bool = True) -> None:
        if index == self.selected and self.anim is None:
            return
        self.selected = index
        target = (self.lefts[index], self.widths[index])
        if not animated or self.window() is None or not self.window().isVisible():
            self._stop()
            self.pos = target
            self.setNeedsDisplay_(True)
            return
        self.anim = (self.pos, target, NSDate.date().timeIntervalSince1970())
        if self.timer is None:
            self.timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(FRAME, self, "step:", None, True)
            NSRunLoop.mainRunLoop().addTimer_forMode_(self.timer, NSRunLoopCommonModes)

    @objc.python_method
    def _stop(self) -> None:
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.anim = None

    def step_(self, timer):
        if self.anim is None:
            self._stop()
            return
        (x0, w0), (x1, w1), began = self.anim
        t = (NSDate.date().timeIntervalSince1970() - began) / SEG_SLIDE
        k = ease_out(t)
        self.pos = (x0 + (x1 - x0) * k, w0 + (w1 - w0) * k)
        if t >= 1:
            self.pos = (x1, w1)
            self._stop()
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        for i, (left, w) in enumerate(zip(self.lefts, self.widths, strict=True)):
            if left <= p.x < left + w and i != self.selected:
                self.select(i)
                self.on_change(i)
                return

    def drawRect_(self, dirty):
        b = self.bounds()
        r = SEG_H / 2
        theme.FILL_SOFT.set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, r, r).fill()
        x, w = self.pos
        cap = NSMakeRect(x + SEG_INSET, SEG_INSET, w - 2 * SEG_INSET, SEG_H - 2 * SEG_INSET)
        cr = cap.size.height / 2
        theme.CONTENT.set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(cap, cr, cr).fill()
        for i, title in enumerate(self.titles):
            color = theme.SURFACE if i == self.selected else theme.CONTENT
            s = NSAttributedString.alloc().initWithString_attributes_(
                title, {AppKit.NSFontAttributeName: self.font, AppKit.NSForegroundColorAttributeName: color})
            size = s.size()
            s.drawAtPoint_((self.lefts[i] + (self.widths[i] - size.width) / 2, (SEG_H - size.height) / 2))


class HistoryRowView(FlippedView):
    """A list row: a soft tint and its action icons while the cursor is over it, and a click
    anywhere outside a button calls `on_click`."""

    @classmethod
    @objc.python_method
    def make(cls, on_click: Callable[[], None] | None = None) -> HistoryRowView:
        v = cls.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 30))
        v.on_click = on_click
        v.hover_views = []
        v.hovered = False
        _tracking(v)
        return v

    def mouseEntered_(self, event):
        self.set_hover(True)

    def mouseExited_(self, event):
        self.set_hover(False)

    @objc.python_method
    def set_hover(self, on: bool) -> None:
        self.hovered = on
        for view in self.hover_views:
            view.setHidden_(not on)
        self.setNeedsDisplay_(True)

    def hitTest_(self, point):
        hit = objc.super(HistoryRowView, self).hitTest_(point)
        if isinstance(hit, NSTextField) and not hit.isEditable():
            return self  # labels pass the click to the row
        return hit

    def mouseUp_(self, event):
        if self.on_click is not None and event.clickCount() == 1:
            self.on_click()

    def drawRect_(self, dirty):
        if self.hovered:
            theme.FILL_SOFT.set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), ROW_RADIUS, ROW_RADIUS).fill()


def _pill(title: str, target, action: str, primary: bool = False, height: float | None = None):
    b = PillButton.make(title, target, action, primary)
    if height is not None:
        b.setFrameSize_((b.frame().size.width, height))
    return b


# -- list items: each owns one view and lays it out for a width ----------------------------------
#
# An item's view spans the content column plus ROW_PAD_X on both sides (the hover tint reaches
# into the margin), and its content starts ROW_PAD_X in, on the column's edge.


class _Item:
    kind = ""
    view: NSView

    def layout(self, width: float, available: float = 0.0) -> float:
        raise NotImplementedError


class _Header(_Item):
    kind = "header"

    def __init__(self, text: str, first: bool):
        self.text = text
        self.top = HEADER_ABOVE_FIRST if first else HEADER_ABOVE - ROW_GAP
        self.view = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 20))
        self.label = _label(text, HEADER_SIZE, AppKit.NSFontWeightSemibold, theme.CONTENT_DIM)
        self.view.addSubview_(self.label)

    def layout(self, width: float, available: float = 0.0) -> float:
        _, h = _fit(self.label)
        self.label.setFrameOrigin_((ROW_PAD_X - LABEL_INSET, self.top))
        height = self.top + h + HEADER_BELOW
        self.view.setFrameSize_((width, height))
        return height


class _Note(_Item):
    """A calm line, under a faint mark, in the middle of an empty list."""

    kind = "empty"

    def __init__(self, text: str):
        self.text = text
        self.view = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 40))
        mark_w = round(EMPTY_MARK_H * mark_mod.MARK_W / mark_mod.MARK_H)
        self.mark = HistoryEmptyMark.alloc().initWithFrame_(NSMakeRect(0, 0, mark_w, EMPTY_MARK_H))
        self.label = _styled_label(text, _font(EMPTY_SIZE), theme.CONTENT_DIM, align=AppKit.NSTextAlignmentCenter)
        self.view.addSubview_(self.mark)
        self.view.addSubview_(self.label)

    def layout(self, width: float, available: float = 0.0) -> float:
        # Balanced lines: as wide as the text needs to split evenly, never a lone last word.
        limit = min(width - 2 * ROW_PAD_X, EMPTY_W)
        natural = self.label.attributedStringValue().size().width + 2 * LABEL_INSET
        one_line = _height(self.label, 100_000)
        lines = max(1, int(-(-natural // limit)))
        while True:
            w = min(limit, natural / lines)
            h = _height(self.label, w)
            while h > lines * one_line + 1 and w < limit:  # word breaks pushed a word down
                w = min(limit, w + EMPTY_STEP)
                h = _height(self.label, w)
            if h <= lines * one_line + 1 or lines >= EMPTY_MAX_LINES:
                break
            lines += 1
        block = EMPTY_MARK_H + EMPTY_MARK_GAP + h
        height = max(available, block + 2 * PILL_ABOVE)
        top = round((height - block) / 2)
        self.mark.setFrameOrigin_((round((width - self.mark.frame().size.width) / 2), top))
        self.label.setFrame_(NSMakeRect((width - w) / 2, top + EMPTY_MARK_H + EMPTY_MARK_GAP, w, h))
        self.view.setFrameSize_((width, height))
        return height


class _PillItem(_Item):
    """A pill centred on its own line (Show Older)."""

    kind = "pill"

    def __init__(self, button):
        self.button = button
        self.view = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 40))
        self.view.addSubview_(button)

    def layout(self, width: float, available: float = 0.0) -> float:
        bw, bh = self.button.frame().size.width, self.button.frame().size.height
        top = PILL_ABOVE - ROW_GAP
        self.button.setFrameOrigin_(((width - bw) / 2, top))
        self.view.setFrameSize_((width, top + bh))
        return top + bh


class _DictationRow(_Item):
    kind = "dictation"

    def __init__(self, owner: HistoryWindow, entry: history_mod.Entry):
        self.entry = entry
        self.entry_id = entry.id
        self.expanded = entry.id in owner._expanded
        self.show_original = entry.id in owner._originals and bool(entry.raw)
        self.editing = owner._editing == entry.id
        self.learned = owner._learned.get(entry.id, "")
        self.view = HistoryRowView.make(None if self.editing else (lambda: owner._toggle_expanded(entry.id)))
        add = self.view.addSubview_

        self.time = _styled_label(time_label(entry.when()), _tabular(TIME_SIZE), theme.CONTENT_DIM, TEXT_LINE_H, wrap=False)
        add(self.time)

        self.text = _styled_label(entry.text, _font(TEXT_SIZE), theme.CONTENT, TEXT_LINE_H)
        self.text.cell().setTruncatesLastVisibleLine_(True)
        self.text.setMaximumNumberOfLines_(0 if self.expanded else TEXT_LINES)
        add(self.text)

        self.original = _styled_label(entry.raw, _italic(NOTE_SIZE), theme.CONTENT_DIM)
        add(self.original)
        self.original.setHidden_(not self.show_original)

        self.learned_label = _label(self.learned, NOTE_SIZE, color=theme.CONTENT_DIM)
        add(self.learned_label)
        self.learned_label.setHidden_(not self.learned)

        self.copy = HistoryIconButton.make("doc.on.doc", ICON_SIZE, owner, "copyEntry:", "Copy")
        self.more = HistoryIconButton.make("ellipsis", ICON_SIZE, owner, "moreMenu:", "More")
        for b in (self.copy, self.more):
            b.setIdentifier_(entry.id)
            add(b)
            b.setHidden_(True)
        self.view.hover_views = [self.copy, self.more]

        self.edit_box = self.edit_field = self.save = self.cancel = None
        if self.editing:
            self.text.setHidden_(True)
            self.view.hover_views = []
            self.edit_box = HistoryFieldBox.make()
            self.edit_field = HistoryTextField.make("", owner, None, self.edit_box, multiline=True)
            self.edit_field.setStringValue_(owner._edit_text if owner._edit_text is not None else entry.text)
            self.edit_field.setIdentifier_(entry.id)
            add(self.edit_box)
            self.save = _pill("Save", owner, "saveFix:", primary=True, height=SMALL_PILL_H)
            self.cancel = _pill("Cancel", owner, "cancelFix:", height=SMALL_PILL_H)
            for b in (self.save, self.cancel):
                b.setIdentifier_(entry.id)
                add(b)

    def layout(self, width: float, available: float = 0.0) -> float:
        text_x = ROW_PAD_X + TIME_W
        icons_w = 2 * ICON_BTN + ICON_GAP
        text_w = max(120.0, width - text_x - ROW_PAD_X - icons_w - ICONS_TO_TEXT)
        top = ROW_PAD_Y

        y = top
        if self.editing:
            # The filled box bleeds FIELD_PAD to the left, so the text inside stays on the text
            # column, and starts half its extra height above the line the text was on.
            pad = (FIELD_H - TEXT_LINE_H) / 2
            box_x = text_x - FIELD_PAD
            box_w = width - ROW_PAD_X - box_x
            inner = box_w - 2 * FIELD_PAD + 2 * LABEL_INSET
            h = max(TEXT_LINE_H, self.edit_field.cell().cellSizeForBounds_(NSMakeRect(0, 0, inner, 100_000)).height)
            box_h = h + 2 * pad
            self.edit_box.setFrame_(NSMakeRect(box_x, y - pad, box_w, box_h))
            self.edit_field.setFrame_(NSMakeRect(FIELD_PAD - LABEL_INSET, pad, inner, h))
            y += h + pad + EDIT_TO_BUTTONS
            self.save.setFrameOrigin_((text_x, y))
            self.cancel.setFrameOrigin_((text_x + self.save.frame().size.width + BUTTON_GAP, y))
            y += SMALL_PILL_H
        else:
            full = _height(self.text, text_w)
            h = full if self.expanded else min(full, TEXT_LINES * TEXT_LINE_H)
            self.text.setFrame_(NSMakeRect(text_x - LABEL_INSET, y, text_w, h))
            y += h
        if self.show_original:
            oh = _height(self.original, text_w)
            self.original.setFrame_(NSMakeRect(text_x - LABEL_INSET, y + NOTE_TOP, text_w, oh))
            y += NOTE_TOP + oh
        if self.learned:
            _, lh = _fit(self.learned_label)
            self.learned_label.setFrameOrigin_((text_x - LABEL_INSET, y + NOTE_TOP))
            y += NOTE_TOP + lh
        y += ROW_PAD_Y

        # The time has the text's fixed line height, which puts both baselines at the same
        # depth in the line box: same top, same baseline.
        _fit(self.time)
        self.time.setFrameOrigin_((ROW_PAD_X - LABEL_INSET, top))
        icon_y = top + TEXT_LINE_H / 2 - ICON_BTN / 2
        self.more.setFrameOrigin_((width - ROW_PAD_X + (ICON_BTN - ICON_SIZE) / 2 - ICON_BTN, icon_y))
        self.copy.setFrameOrigin_((self.more.frame().origin.x - ICON_GAP - ICON_BTN, icon_y))
        self.view.setFrameSize_((width, y))
        return y


class _FailedRow(_Item):
    """A dictation whose transcription failed: the time, a dim line saying so, and a Retry pill
    and the More button, both always shown (a hover-only Retry would be easy to miss). While a
    retry runs the pill reads Retrying... and does nothing."""

    kind = "dictation"

    def __init__(self, owner: HistoryWindow, entry: history_mod.Entry):
        self.entry = entry
        self.entry_id = entry.id
        self.retrying = entry.id in owner._retrying
        self.view = HistoryRowView.make()
        add = self.view.addSubview_

        self.time = _styled_label(time_label(entry.when()), _tabular(TIME_SIZE), theme.CONTENT_DIM, TEXT_LINE_H, wrap=False)
        add(self.time)
        self.text = _styled_label(failed_text(entry.audio_seconds), _font(TEXT_SIZE), theme.CONTENT_DIM, TEXT_LINE_H)
        self.text.cell().setTruncatesLastVisibleLine_(True)
        self.text.setMaximumNumberOfLines_(1)
        add(self.text)

        self.retry = _pill(RETRYING if self.retrying else RETRY, owner, "retryEntry:", height=SMALL_PILL_H)
        self.retry.setEnabled_(not self.retrying)
        self.more = HistoryIconButton.make("ellipsis", ICON_SIZE, owner, "moreMenu:", "More")
        for b in (self.retry, self.more):
            b.setIdentifier_(entry.id)
            add(b)

    def layout(self, width: float, available: float = 0.0) -> float:
        text_x = ROW_PAD_X + TIME_W
        top = ROW_PAD_Y
        icon_y = top + TEXT_LINE_H / 2 - ICON_BTN / 2
        more_x = width - ROW_PAD_X + (ICON_BTN - ICON_SIZE) / 2 - ICON_BTN
        self.more.setFrameOrigin_((more_x, icon_y))
        pw, ph = self.retry.frame().size.width, self.retry.frame().size.height
        pill_x = more_x - ICON_GAP - pw
        self.retry.setFrameOrigin_((pill_x, top + TEXT_LINE_H / 2 - ph / 2))
        text_w = max(80.0, pill_x - ICONS_TO_TEXT - text_x)
        self.text.setFrame_(NSMakeRect(text_x - LABEL_INSET, top, text_w, TEXT_LINE_H))
        _fit(self.time)
        self.time.setFrameOrigin_((ROW_PAD_X - LABEL_INSET, top))
        height = top + TEXT_LINE_H + ROW_PAD_Y
        self.view.setFrameSize_((width, height))
        return height


class _TermRow(_Item):
    kind = "term"

    def __init__(self, owner: HistoryWindow, entry: vocabulary_mod.Entry):
        self.entry = entry
        self.term = entry.term
        self.view = HistoryRowView.make()
        add = self.view.addSubview_
        self.label = _label(entry.term, TERM_SIZE, AppKit.NSFontWeightMedium)
        add(self.label)
        heard = sorted(entry.heard_as, key=lambda k: (-entry.heard_as[k], k))
        self.heard = _wrapping(f"heard as: {', '.join(heard)}" if heard else "", HEARD_SIZE)
        add(self.heard)
        self.heard.setHidden_(not heard)

        self.badge_text = SOURCE_BADGE.get(entry.source, "Added")
        self.source = _label(self.badge_text, SOURCE_SIZE, color=theme.CONTENT_DIM)
        add(self.source)

        self.delete = HistoryIconButton.make("xmark", ICON_SIZE_SMALL, owner, "removeTerm:", "Delete")
        self.delete.setIdentifier_(entry.term)
        add(self.delete)
        self.delete.setHidden_(True)
        self.view.hover_views = [self.delete]

    def layout(self, width: float, available: float = 0.0) -> float:
        sw, sh = _fit(self.source)
        icon_x = width - ROW_PAD_X + (ICON_BTN - ICON_SIZE) / 2 - ICON_BTN
        source_x = icon_x - SOURCE_TO_ICON - sw + LABEL_INSET
        text_w = max(80.0, source_x - ICONS_TO_TEXT - ROW_PAD_X)
        y = ROW_PAD_Y
        _, h = _fit(self.label)
        self.label.setFrame_(NSMakeRect(ROW_PAD_X - LABEL_INSET, y, min(text_w, self.label.frame().size.width), h))
        first_line = h
        y += h
        if not self.heard.isHidden():
            hh = _height(self.heard, text_w)
            self.heard.setFrame_(NSMakeRect(ROW_PAD_X - LABEL_INSET, y + HEARD_GAP, text_w, hh))
            y += HEARD_GAP + hh
        y += ROW_PAD_Y
        mid = ROW_PAD_Y + first_line / 2
        self.source.setFrameOrigin_((source_x, mid - sh / 2))
        self.delete.setFrameOrigin_((icon_x, mid - ICON_BTN / 2))
        self.view.setFrameSize_((width, y))
        return y


# -- the window --------------------------------------------------------------------------------


class HistoryWindow(NSObject):
    """Owns the window. The app sets `ctl` after init, then calls show(). `store`, `vocabulary`
    and `now` are the shared history, the shared dictionary and the clock unless a test sets
    them."""

    def init(self):
        self = objc.super(HistoryWindow, self).init()
        if self is None:
            return None
        self.ctl = None
        self.store: history_mod.HistoryStore | None = None
        self.vocabulary: vocabulary_mod.Vocabulary | None = None
        self.now: Callable[[], datetime] = lambda: datetime.now().astimezone()
        self.window = None
        self._timer = None
        self._stamps = None
        self._dirty = False          # something changed while a fix was being typed
        self._tab = "dictations"
        self._query = ""
        self._cap = ROW_CAP
        self._expanded: set[str] = set()
        self._originals: set[str] = set()
        self._learned: dict[str, str] = {}
        self._editing: str | None = None
        self._edit_text: str | None = None
        self._retrying: set[str] = set()   # failed entries whose retry is running; kept across a close
        self._add_note = ""
        self._support_shown = False
        self.items: list[_Item] = []
        return self

    @objc.python_method
    def _store(self) -> history_mod.HistoryStore:
        return self.store or history_mod.shared()

    @objc.python_method
    def _vocab(self) -> vocabulary_mod.Vocabulary:
        return self.vocabulary or vocabulary_mod.shared()

    # -- showing -----------------------------------------------------------------------

    @objc.python_method
    def show(self) -> None:
        """Bring the window up (building it the first time), fresh from the store."""
        self.prepare()
        if not self.window.isVisible():
            vf = screen_under_mouse().visibleFrame()
            f = self.window.frame()
            x = vf.origin.x + (vf.size.width - f.size.width) / 2
            y = vf.origin.y + (vf.size.height - f.size.height) / 2
            self.window.setFrameOrigin_((round(x), round(max(vf.origin.y, y))))
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        if self._timer is None:
            self._timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(POLL, self, "tick:", None, True)
            NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    @objc.python_method
    def prepare(self) -> None:
        """Build the window if needed and fill it from the store, without putting it on screen."""
        if self.window is None:
            self._build()
        self._refresh(force=True)

    def windowWillClose_(self, note):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        self._editing = self._edit_text = None
        self._learned.clear()
        self._originals.clear()
        self._expanded.clear()
        self._add_note = ""
        self._cap = ROW_CAP

    def windowDidResize_(self, note):
        self._layout()

    def tick_(self, timer):
        self._refresh()

    # -- building ----------------------------------------------------------------------

    @objc.python_method
    def _build(self) -> None:
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskResizable | AppKit.NSWindowStyleMaskMiniaturizable)
        win = EditingWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False)
        win.setTitle_("Lyssna History")
        win.setTitlebarAppearsTransparent_(True)
        win.setBackgroundColor_(theme.SURFACE)
        win.setReleasedWhenClosed_(False)
        win.setContentMinSize_((MIN_W, MIN_H))
        win.setDelegate_(self)
        content = HistorySurfaceView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        win.setContentView_(content)
        self.window = win
        self.content = content
        add = content.addSubview_

        self.mark = MarkView.make(MARK_H)
        add(self.mark)
        self.title = _label("History", TITLE_SIZE)
        self.title.setFont_(_serif(TITLE_SIZE))
        add(self.title)

        self.stat_numbers, self.stat_labels = [], []
        for caption in ("words this week", "words in total", "dictations"):
            number = _label("0", STAT_SIZE)
            number.setFont_(_tabular(STAT_SIZE, AppKit.NSFontWeightSemibold))
            label = _label(caption, STAT_LABEL_SIZE, color=theme.CONTENT_DIM)
            add(number)
            add(label)
            self.stat_numbers.append(number)
            self.stat_labels.append(label)

        # Under the totals, once they are worth a look: one quiet sentence and its link.
        self.support_label = _label(support.HISTORY_LINE, SUPPORT_SIZE, color=theme.CONTENT_DIM)
        add(self.support_label)
        self.support_button = HistoryTextButton.make(support.HISTORY_ACTION, self, "openSupport:",
                                                     size=SUPPORT_SIZE, link=True, symbol=support.SYMBOL)
        add(self.support_button)

        self.tabs = HistorySegments.make(["Dictations", "Dictionary"], lambda i: self.select_tab(TABS[i]))
        add(self.tabs)

        # Dictations: the search field.
        self.search_box = HistoryFieldBox.make()
        self.search_icon = NSImageView.imageViewWithImage_(_symbol("magnifyingglass", SEARCH_ICON))
        self.search_icon.setContentTintColor_(theme.CONTENT_DIM)
        self.search_box.addSubview_(self.search_icon)
        self.search_field = HistoryTextField.make("Search", self, None, self.search_box)
        add(self.search_box)

        # Dictionary: the add row.
        self.add_box = HistoryFieldBox.make()
        self.add_field = HistoryTextField.make("Add a word or name", self, "addTerm:", self.add_box)
        add(self.add_box)
        self.add_button = _pill("Add", self, "addTerm:", primary=True, height=FIELD_H)
        add(self.add_button)
        self.add_note_label = _label("", NOTE_SIZE, color=theme.CONTENT_DIM)
        add(self.add_note_label)

        self.sep_top = TintedView.make(NSMakeRect(0, 0, W, 1), theme.HAIRLINE)
        add(self.sep_top)

        self.scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 300))
        self.scroll.setDrawsBackground_(False)
        self.scroll.contentView().setDrawsBackground_(False)
        self.scroll.setHasVerticalScroller_(True)
        self.scroll.setAutohidesScrollers_(True)
        self.scroll.setBorderType_(AppKit.NSNoBorder)
        self.doc = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 300))
        self.scroll.setDocumentView_(self.doc)
        add(self.scroll)

        self.sep_bottom = TintedView.make(NSMakeRect(0, 0, W, 1), theme.HAIRLINE)
        add(self.sep_bottom)
        self.retention = _label("", FOOTER_SIZE, color=theme.CONTENT_DIM)
        add(self.retention)
        # Regular weight, like the retention line beside it.
        self.clear_button = HistoryTextButton.make("Clear History…", self, "clearHistory:",
                                                   weight=AppKit.NSFontWeightRegular)
        add(self.clear_button)

    # -- state -------------------------------------------------------------------------

    @objc.python_method
    def _vocab_stamp(self):
        try:
            st = self._vocab().path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    @objc.python_method
    def _current_stamps(self):
        store = self._store()
        return store.stamp(), self._vocab_stamp(), store.days()

    @objc.python_method
    def _refresh(self, force: bool = False) -> None:
        """Redraw when the store, the dictionary or the retention setting changed (or always,
        with force). A fix being typed is not swept away by a change elsewhere: the list is
        rebuilt once it is saved or cancelled."""
        if self.window is None:
            return
        stamps = self._current_stamps()
        if not force and stamps == self._stamps:
            return
        self._update_header()
        if force or self._editing is None:
            self._rebuild_list()
        else:
            self._dirty = True
        self._stamps = self._current_stamps()  # reading may have trimmed old entries

    @objc.python_method
    def _update_header(self) -> None:
        stats = self._store().stats()
        values = (stats["words_this_week"], stats["words_total"], stats["dictations"])
        for label, value in zip(self.stat_numbers, values, strict=True):
            label.setStringValue_(figure(value))
        self.retention.setStringValue_(retention_text(self._store().days()))
        self._support_shown = support.in_history(stats["words_total"])

    @objc.python_method
    def _rebuild_list(self) -> None:
        if self._editing is not None:
            row = self._row(self._editing)
            if row is not None and row.edit_field is not None:
                self._edit_text = row.edit_field.stringValue()
        for view in list(self.doc.subviews()):
            view.removeFromSuperview()
        self.items = self._dictation_items() if self._tab == "dictations" else self._dictionary_items()
        for item in self.items:
            self.doc.addSubview_(item.view)
        self._dirty = False
        self._layout()
        if self._editing is not None:
            row = self._row(self._editing)
            if row is not None and row.edit_field is not None:
                self.window.makeFirstResponder_(row.edit_field)

    @objc.python_method
    def _dictation_items(self) -> list[_Item]:
        store = self._store()
        entries = store.search(self._query)
        has_any = bool(entries) or bool(self._query.strip() and store.entries())
        self.clear_button.set_active(bool(store.entries()))
        if not entries:
            return [_Note(EMPTY_SEARCH if has_any else EMPTY_DICTATIONS)]
        today = self.now().date()
        items: list[_Item] = []
        last_day = None
        for entry in entries[: self._cap]:
            day = entry.when().date()
            if day != last_day:
                items.append(_Header(day_label(day, today), first=last_day is None))
                last_day = day
            items.append(_FailedRow(self, entry) if entry.failed else _DictationRow(self, entry))
        if len(entries) > self._cap:
            items.append(_PillItem(_pill("Show Older", self, "showOlder:")))
        return items

    @objc.python_method
    def _dictionary_items(self) -> list[_Item]:
        entries = self._vocab().entries()
        return [_TermRow(self, e) for e in entries] or [_Note(EMPTY_DICTIONARY)]

    @objc.python_method
    def _row(self, entry_id: str) -> _DictationRow | None:
        return next((i for i in self.items if isinstance(i, _DictationRow) and i.entry_id == entry_id), None)

    # -- layout ------------------------------------------------------------------------

    @objc.python_method
    def _layout(self) -> None:
        if self.window is None:
            return
        size = self.content.frame().size
        cw, ch = size.width, size.height
        x0, inner = column(cw)

        y = TOP
        _fit(self.title)
        mw = self.mark.frame().size.width
        th = self.title.frame().size.height
        row_h = max(MARK_H, th)
        self.mark.setFrameOrigin_((x0, y + (row_h - MARK_H) / 2))
        self.title.setFrameOrigin_((x0 + mw + MARK_GAP - LABEL_INSET, y + (row_h - th) / 2))
        y += row_h + HEAD_TO_STATS

        col = inner / 3
        stats_h = 0.0
        for i, (number, label) in enumerate(zip(self.stat_numbers, self.stat_labels, strict=True)):
            _, nh = _fit(number)
            _, lh = _fit(label)
            left = x0 + i * col  # where the ink of both lines starts
            number.setFrameOrigin_((left - _ink_left(number), y))
            label.setFrameOrigin_((left - _ink_left(label), y + nh))
            stats_h = max(stats_h, nh + lh)
        y += stats_h
        for view in (self.support_label, self.support_button):
            view.setHidden_(not self._support_shown)
        if self._support_shown:
            y += STATS_TO_SUPPORT
            lw, lh = _fit(self.support_label)
            self.support_label.setFrameOrigin_((x0 - _ink_left(self.support_label), y))
            self.support_button.sizeToFit()
            bh = self.support_button.frame().size.height
            self.support_button.setFrameOrigin_((x0 - _ink_left(self.support_label) + lw + SUPPORT_GAP,
                                                 y + (lh - bh) / 2))
            y += lh
        y += STATS_TO_TABS

        self.tabs.setFrameOrigin_((x0, y))
        y += SEG_H + TABS_TO_FIELD

        dictations = self._tab == "dictations"
        self.search_box.setHidden_(not dictations)
        for view in (self.add_box, self.add_button):
            view.setHidden_(dictations)
        if dictations:
            self.search_box.setFrame_(NSMakeRect(x0, y, inner, FIELD_H))
            self.search_icon.setFrame_(NSMakeRect(FIELD_PAD, (FIELD_H - 16) / 2, 16, 16))
            text_x = FIELD_PAD + 16 + SEARCH_ICON_GAP - LABEL_INSET
            self._place_field(self.search_field, text_x, inner - text_x - FIELD_PAD)
        else:
            bw = max(self.add_button.frame().size.width, 64.0)
            self.add_button.setFrame_(NSMakeRect(x0 + inner - bw, y, bw, FIELD_H))
            box_w = inner - bw - BUTTON_GAP
            self.add_box.setFrame_(NSMakeRect(x0, y, box_w, FIELD_H))
            self._place_field(self.add_field, FIELD_PAD - LABEL_INSET, box_w - 2 * FIELD_PAD + LABEL_INSET)
        y += FIELD_H
        self.add_note_label.setStringValue_(self._add_note)
        show_note = not dictations and bool(self._add_note)
        self.add_note_label.setHidden_(not show_note)
        if show_note:
            _, nh = _fit(self.add_note_label)
            self.add_note_label.setFrameOrigin_((x0 + FIELD_PAD - LABEL_INSET, y + NOTE_GAP))
            y += NOTE_GAP + nh
        y += FIELD_TO_LIST

        self.sep_top.setFrame_(NSMakeRect(0, y, cw, 1))
        footer_top = ch - FOOTER_H
        self.sep_bottom.setFrame_(NSMakeRect(0, footer_top, cw, 1))
        self.scroll.setFrame_(NSMakeRect(0, y + 1, cw, max(0.0, footer_top - y - 1)))

        _, rh = _fit(self.retention)
        self.retention.setFrameOrigin_((x0 - LABEL_INSET, footer_top + (FOOTER_H - rh) / 2))
        self.clear_button.sizeToFit()
        cbw, cbh = self.clear_button.frame().size.width, self.clear_button.frame().size.height
        self.clear_button.setFrameOrigin_((x0 + inner - cbw + LABEL_INSET * 2, footer_top + (FOOTER_H - cbh) / 2))
        self.clear_button.setHidden_(not dictations)

        self._layout_list()

    @objc.python_method
    def _place_field(self, field, x: float, width: float) -> None:
        h = field.cell().cellSizeForBounds_(NSMakeRect(0, 0, width, 100)).height
        field.setFrame_(NSMakeRect(x, (FIELD_H - h) / 2, width, h))

    @objc.python_method
    def _layout_list(self) -> None:
        width = self.scroll.contentSize().width
        x0, inner = column(width)
        row_w = inner + 2 * ROW_PAD_X
        headers = any(i.kind == "header" for i in self.items)
        y = 0.0 if headers or (self.items and self.items[0].kind == "empty") else LIST_TOP
        for item in self.items:
            available = self.scroll.contentSize().height if item.kind == "empty" else 0.0
            h = item.layout(row_w, available)
            item.view.setFrameOrigin_((x0 - ROW_PAD_X, y))
            y += h + (ROW_GAP if item.kind in ("dictation", "term") else 0.0)
        if not (self.items and self.items[-1].kind == "empty"):
            y += LIST_BOTTOM
        self.doc.setFrameSize_((width, max(y, self.scroll.contentSize().height)))

    # -- actions: tabs and search --------------------------------------------------------

    @objc.python_method
    def select_tab(self, tab: str) -> None:
        """Show "dictations" or "dictionary"."""
        if tab not in TABS:
            return
        self.tabs.select(TABS.index(tab))
        if tab == self._tab:
            return
        self._tab = tab
        self._update_header()
        self._rebuild_list()
        self.scroll.contentView().scrollToPoint_((0, 0))
        self.scroll.reflectScrolledClipView_(self.scroll.contentView())

    def controlTextDidChange_(self, note):
        field = note.object()
        if field is self.search_field:
            self._query = self.search_field.stringValue()
            self._cap = ROW_CAP
            self._rebuild_list()
        elif self._editing is not None and field.identifier() == self._editing:
            self._layout_list()  # the fix field grows with its text

    def control_textView_doCommandBySelector_(self, control, text_view, selector):
        """In the fix field, Return saves and Escape cancels."""
        if self._editing is None or control.identifier() != self._editing:
            return False
        name = selector.decode() if isinstance(selector, bytes) else str(selector)
        if name == "insertNewline:":
            self.saveFix_(control)
            return True
        if name == "cancelOperation:":
            self.cancelFix_(control)
            return True
        return False

    def openSupport_(self, sender):
        support.open_support("history")

    def showOlder_(self, sender):
        self._cap += ROW_CAP
        self._rebuild_list()

    # -- actions: a dictation ------------------------------------------------------------

    @objc.python_method
    def _toggle_expanded(self, entry_id: str) -> None:
        if entry_id in self._expanded:
            self._expanded.discard(entry_id)
        else:
            self._expanded.add(entry_id)
        self._rebuild_list()

    def copyEntry_(self, sender):
        entry = self._store().get(str(sender.identifier()))
        if entry is None:
            return
        copy_to_clipboard(entry.text)
        sender.set_symbol("checkmark")
        later(COPIED_FOR, lambda: sender.set_symbol("doc.on.doc"))

    def moreMenu_(self, sender):
        entry_id = str(sender.identifier())
        entry = self._store().get(entry_id)
        if entry is None:
            return
        menu = self._more_menu(entry)
        origin = (0, sender.bounds().size.height + 4)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, origin, sender)

    @objc.python_method
    def _more_menu(self, entry: history_mod.Entry) -> NSMenu:
        menu = NSMenu.alloc().initWithTitle_("More")
        menu.setAutoenablesItems_(False)
        shown = entry.id in self._originals
        if entry.failed:
            rows = ((RETRY, "retryEntry:", entry.id not in self._retrying), ("Delete", "deleteEntry:", True))
        else:
            rows = (
                ("Hide Original" if shown else "Show Original", "toggleOriginal:", bool(entry.raw)),
                ("Fix a Word…", "fixWord:", True),
                ("Delete", "deleteEntry:", True),
            )
        for title, action, enabled in rows:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(self)
            item.setRepresentedObject_(entry.id)
            item.setEnabled_(enabled)
            menu.addItem_(item)
        return menu

    def toggleOriginal_(self, sender):
        entry_id = str(sender.representedObject())
        if entry_id in self._originals:
            self._originals.discard(entry_id)
        else:
            self._originals.add(entry_id)
        self._rebuild_list()

    def fixWord_(self, sender):
        self._editing = str(sender.representedObject())
        self._edit_text = None
        self._learned.pop(self._editing, None)
        self._rebuild_list()

    def retryEntry_(self, sender):
        """Run a failed dictation again, without pasting: the row fills in place when it works.
        From the row's pill (its identifier) or the More menu (its represented object)."""
        entry_id = str(sender.representedObject() if isinstance(sender, NSMenuItem) else sender.identifier())
        if self.ctl is None or entry_id in self._retrying:
            return
        self._retrying.add(entry_id)
        self._rebuild_list()
        started = self.ctl.retry_dictation(entry_id, paste=False,
                                           on_done=lambda ok: on_main(self._retry_done, entry_id, ok))
        if not started:
            self._retry_done(entry_id, False)

    @objc.python_method
    def _retry_done(self, entry_id: str, ok: bool) -> None:
        """A retry finished. Worked: the store changed and the row redraws as a dictation.
        Failed: the row stays (the toast said why) and its pill reads Retry again."""
        self._retrying.discard(entry_id)
        self._refresh(force=True)

    def deleteEntry_(self, sender):
        entry_id = str(sender.representedObject())
        if self._editing == entry_id:
            self._editing = self._edit_text = None
        self._store().delete(entry_id)
        self._refresh(force=True)

    def saveFix_(self, sender):
        entry_id = self._editing
        row = self._row(entry_id) if entry_id else None
        if row is None or row.edit_field is None:
            return
        new_text = row.edit_field.stringValue().strip()
        old_text = row.entry.text
        self._editing = self._edit_text = None
        learned: list[str] = []
        if new_text and new_text != old_text:
            vocab = self._vocab()
            for observed, corrected in find_corrections(old_text, new_text):
                term = vocab.learn(observed, corrected)
                if term and term not in learned:
                    learned.append(term)
            self._store().update_text(entry_id, new_text)
        if learned:
            self._learned[entry_id] = learned_text(learned)
        else:
            self._learned.pop(entry_id, None)
        log.info("history: a dictation was fixed by hand (%d terms learned)", len(learned))
        self._refresh(force=True)

    def cancelFix_(self, sender):
        self._editing = self._edit_text = None
        self._refresh(force=True)

    def clearHistory_(self, sender):
        def clear():
            self._store().clear()
            self._editing = self._edit_text = None
            self._refresh(force=True)

        self._confirm("Delete all saved dictations?", "Word totals are kept.", "Delete", clear)

    @objc.python_method
    def _confirm(self, message: str, info: str, ok: str, then: Callable[[], None]) -> None:
        """Ask on a sheet over the window and call `then` if the user agrees."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.setInformativeText_(info)
        button = alert.addButtonWithTitle_(ok)
        if hasattr(button, "setHasDestructiveAction_"):
            button.setHasDestructiveAction_(True)
        alert.addButtonWithTitle_("Cancel")

        def done(response):
            if response == AppKit.NSAlertFirstButtonReturn:
                then()

        alert.beginSheetModalForWindow_completionHandler_(self.window, done)

    # -- actions: the dictionary ---------------------------------------------------------

    def addTerm_(self, sender):
        text = self.add_field.stringValue().strip()
        if not text:
            return
        term = self._vocab().add(text)
        if term is None:
            self._add_note = "That is too long for the dictionary."
        else:
            self._add_note = ""
            self.add_field.setStringValue_("")
        self._refresh(force=True)

    def removeTerm_(self, sender):
        self._vocab().remove(str(sender.identifier()))
        self._refresh(force=True)
