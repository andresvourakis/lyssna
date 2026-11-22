"""The Mac's fonts, its reduced-motion setting, its time format and its symbols, for the Qt
interface (lyssna/ui/theme.py, the history's times, lyssna/ui/icons.py).

Why: the Mac keeps Apple's fonts and must look as the AppKit interface did. Qt reaches San
Francisco as ".AppleSystemUIFont", but AppKit draws it with Apple's size-dependent tracking
and Qt does not, so Qt's text comes out 2 to 6 percent narrower between 11 and 20 px (and
wider at 28 px). tracking() measures the AppKit advance of a reference string for each text
style once and returns the per-character difference, which theme.py hands to Qt as letter
spacing: that brings Qt within about half a percent of AppKit on whole lines. The headline
serif, New York, is not in Qt's font list on its own: font_files() names its system file so
theme.py can add it, after which Qt knows it as ".New York". text_width() gives the exact
AppKit width of a string, so frames sized to text come out as the AppKit interface's.

AppKit and CoreText load inside the functions, so this module imports on any system. The
Windows twin (lyssna/win/appearance.py) offers the same names."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lyssna.mac.appearance")

TEXT_FAMILY = ".AppleSystemUIFont"  # San Francisco, as Qt names the system font
SERIF_FAMILY = ".New York"  # the system serif, once font_files() is loaded
# The character a secure field shows for each hidden one: AppKit's NSSecureTextFieldCell draws
# San Francisco's own bullet at U+F79A (8.1 wide at 13 pt), larger than the usual U+2022.
SECURE_BULLET: int | None = 0xF79A

# Every letter, the digits and two spaces: the advance the correction is measured on.
REFERENCE = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789"

_tracking: dict[tuple[str, int, str], float] = {}
WIDTHS_KEPT = 4096  # text_width's memory, emptied when full
_widths: dict[tuple[str, tuple[str, int, str]], float] = {}
_time_formatter = None


def font_files() -> list[Path]:
    """New York's file, found through the same serif descriptor the AppKit interface used, for
    QFontDatabase.addApplicationFont. Empty when the system does not have it."""
    try:
        import CoreText

        url = CoreText.CTFontCopyAttribute(_appkit_font("serif", 20, "semibold"), CoreText.kCTFontURLAttribute)
        return [Path(str(url.path()))] if url is not None else []
    except Exception:
        log.debug("could not find the system serif's file", exc_info=True)
        return []


def _appkit_font(kind: str, size: int, weight: str):
    """The NSFont the AppKit interface used for this text style (lyssna/legacy/ui)."""
    import AppKit

    w = {"regular": AppKit.NSFontWeightRegular, "medium": AppKit.NSFontWeightMedium,
         "semibold": AppKit.NSFontWeightSemibold, "bold": AppKit.NSFontWeightBold}[weight]
    if kind == "digits":
        return AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(size, w)
    base = AppKit.NSFont.systemFontOfSize_weight_(size, w)
    if kind == "italic":
        return AppKit.NSFontManager.sharedFontManager().convertFont_toHaveTrait_(base, AppKit.NSItalicFontMask)
    if kind == "serif":
        desc = base.fontDescriptor().fontDescriptorWithDesign_(AppKit.NSFontDescriptorSystemDesignSerif)
        return (AppKit.NSFont.fontWithDescriptor_size_(desc, size) if desc is not None else None) or base
    return base


def _appkit_advance(text: str, font) -> float:
    import AppKit

    s = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, {AppKit.NSFontAttributeName: font})
    return float(s.size().width)


def tracking(kind: str, size: int, weight: str, qt_advance: Callable[[str], float]) -> float:
    """The letter spacing, in pixels per character, that makes Qt's text as wide as AppKit's
    for this style: kind is "text", "italic", "digits" or "serif"; qt_advance measures a
    string in the Qt font before any spacing. Measured once per style, then remembered; 0.0
    when AppKit cannot be asked."""
    key = (kind, size, weight)
    if key not in _tracking:
        try:
            target = _appkit_advance(REFERENCE, _appkit_font(kind, size, weight))
            _tracking[key] = (target - qt_advance(REFERENCE)) / len(REFERENCE)
        except Exception:
            log.debug("could not measure the %s %s %s font in AppKit", kind, size, weight, exc_info=True)
            return 0.0
    return _tracking[key]


def text_width(text: str, style: tuple[str, int, str], qt_advance: Callable[[str], float]) -> float:
    """How wide AppKit draws `text` in the text style (kind, size, weight), in the very font the
    AppKit interface created for that style, for sizing frames. The letter spacing in tracking()
    evens Qt out over a whole line, but single strings still differ from AppKit by up to 0.6 px,
    which moves a frame by a whole point wherever a rounding boundary lies in between; asking
    AppKit itself gives the Mac's frames exactly. Remembered per text and style; qt_advance
    (Qt's measure of the text) answers when AppKit cannot be asked."""
    key = (text, style)
    width = _widths.get(key)
    if width is None:
        try:
            width = _appkit_advance(text, _appkit_font(*style))
        except Exception:
            log.debug("could not measure %r in AppKit", text, exc_info=True)
            return qt_advance(text)
        if len(_widths) >= WIDTHS_KEPT:
            _widths.clear()  # dictations and counting percentages would otherwise grow it forever
        _widths[key] = width
    return width


OFFSETS_KEPT = 2048  # text_offsets' memory, emptied when full
_offsets: dict[tuple[str, tuple[str, int, str]], list[float]] = {}


def text_offsets(text: str, style: tuple[str, int, str]) -> list[float] | None:
    """Where AppKit puts each character of a line of `text` in the text style: the x of the
    caret before each character, and the line's end (len(text) + 1 values), kerning included.
    A label draws each word at the Mac's own x with it, rather than spreading the line's width
    evenly. None when AppKit cannot be asked."""
    key = (text, style)
    offsets = _offsets.get(key)
    if offsets is None:
        try:
            import CoreText

            line = CoreText.CTLineCreateWithAttributedString(_attributed(text, _appkit_font(*style)))
            units = [0]  # Core Text counts UTF-16 units: where each character of `text` starts in them
            for ch in text:
                units.append(units[-1] + (2 if ord(ch) > 0xFFFF else 1))
            offsets = [float(CoreText.CTLineGetOffsetForStringIndex(line, u, None)[0]) for u in units]
        except Exception:
            log.debug("could not place %r in AppKit", text, exc_info=True)
            return None
        if len(_offsets) >= OFFSETS_KEPT:
            _offsets.clear()
        _offsets[key] = offsets
    return offsets


# Where AppKit's labels end a line, so a Qt label draws the very lines the AppKit label drew.
#
# Breaking: an NSTextField wrapping label breaks its lines as TextKit does (word wrapping, a word
# longer than the line split anywhere), on a line as wide as its frame less the cell's insets.
# Truncating, measured on real AppKit labels (drawn to PDF and to pixels) at 11 to 14 pt over
# hundreds of widths. A single line set to truncate its tail keeps the most characters for which
# the kept text and the ellipsis, measured apart (the ellipsis sits at the kept text's advance,
# without kerning), fit the line. The last line a wrapping label shows (truncatesLastVisibleLine)
# first tightens its letters to keep more, and is then drawn with every kept character TIGHTEN_EM
# of the font size narrower (the whole allowance, not just enough to fit: measured on the fields'
# pixels at 39 of 40 widths). Where that last line is cut is asked of TextKit 2 (_last_line): an
# NSTextLayoutManager whose container truncates its one line, with the paragraph allowing the
# default tightening, keeps exactly the characters the label keeps (29856 of 29856 clamped
# labels, 11 to 14 pt, 150 to 460 wide in 1 pt steps). TextKit 1's truncation does not tighten.
TIGHTEN_EM = 0.01368
# TextKit lets a line run over its width by about a thousandth of a point that the label's cell does
# not allow (a line 290.001 wide fits 290 in TextKit, not in the label).
WRAP_SLACK = 0.0005
ELLIPSIS = "…"
LINES_KEPT = 2048  # break_lines' memory, emptied when full
_lines: dict[tuple, list[str]] = {}


def _attributed(text: str, font):
    import AppKit

    para = AppKit.NSMutableParagraphStyle.alloc().init()
    para.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(
        text, {AppKit.NSFontAttributeName: font, AppKit.NSParagraphStyleAttributeName: para})


def _wrap(text: str, font, width: float) -> list[tuple[int, int]]:
    """The (start, end) of each line TextKit makes of `text` on lines `width` wide."""
    import AppKit

    storage = AppKit.NSTextStorage.alloc().initWithAttributedString_(_attributed(text, font))
    manager = AppKit.NSLayoutManager.alloc().init()
    container = AppKit.NSTextContainer.alloc().initWithSize_((max(1.0, width - WRAP_SLACK), 1e7))
    container.setLineFragmentPadding_(0)
    manager.addTextContainer_(container)
    storage.addLayoutManager_(manager)
    glyphs = manager.glyphRangeForTextContainer_(container)
    out, i, end = [], glyphs.location, glyphs.location + glyphs.length
    while i < end:
        _rect, line = manager.lineFragmentRectForGlyphAtIndex_effectiveRange_(i, None)
        chars = manager.characterRangeForGlyphRange_actualGlyphRange_((line.location, line.length), None)[0]
        out.append((chars.location, chars.location + chars.length))
        i = line.location + line.length
    return out


def _cut(text: str, font, width: float) -> str:
    """`text` on one line `width` wide as AppKit shows it when it truncates the tail: whole if
    it fits, else the most characters that fit and an ellipsis."""

    def fits(kept: str, ellipsis: bool) -> bool:
        if not kept:
            return True
        need = _appkit_advance(kept, font) + (_appkit_advance(ELLIPSIS, font) if ellipsis else 0.0)
        return need - width <= 1e-6

    if fits(text, False):
        return text
    lo, hi = 0, len(text)  # the longest prefix that fits with the ellipsis, by bisection (widths grow with length)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(text[:mid].rstrip(), True):
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ELLIPSIS


def _last_line(text: str, font, width: float) -> str:
    """`text` on the last line of a wrapping label `width` wide that truncates its last visible line,
    as TextKit 2 lays it out: whole if it fits, tightened if need be, else the characters it keeps
    and an ellipsis. TextKit 2 reports no truncated range, but the characters it hides have no
    place of their own on the line: every one sits back at x 0, after the ellipsis's slot."""
    import AppKit

    para = AppKit.NSMutableParagraphStyle.alloc().init()
    para.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    para.setAllowsDefaultTighteningForTruncation_(True)
    storage = AppKit.NSTextContentStorage.alloc().init()
    manager = AppKit.NSTextLayoutManager.alloc().init()
    storage.addTextLayoutManager_(manager)
    container = AppKit.NSTextContainer.alloc().initWithSize_((max(1.0, width - WRAP_SLACK), 1e7))
    container.setLineFragmentPadding_(0)
    container.setMaximumNumberOfLines_(1)
    container.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
    manager.setTextContainer_(container)
    storage.setAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
        text, {AppKit.NSFontAttributeName: font, AppKit.NSParagraphStyleAttributeName: para}))
    manager.ensureLayoutForRange_(storage.documentRange())
    fragments = []

    def visit(fragment) -> bool:
        fragments.extend(fragment.textLineFragments())
        return False  # the first line is the only one shown

    manager.enumerateTextLayoutFragmentsFromLocation_options_usingBlock_(storage.documentRange().location(), 0, visit)
    line = fragments[0]
    r = line.characterRange()
    start, end = r.location, r.location + r.length
    prev = -1.0
    for i in range(start, end):
        x = float(line.locationForCharacterAtIndex_(i).x)
        if i > start and x <= prev:
            return text[start:i - 1].rstrip() + ELLIPSIS  # i - 1 is the ellipsis's slot
        prev = x
    if end < len(text.rstrip()):
        raise ValueError("TextKit 2 left text off its one line without an ellipsis")
    return text[start:end].rstrip()


def truncate(text: str, style: tuple[str, int, str], width: float, qt_elide: Callable[[str, float], str]) -> str:
    """One line of `text` in the text style, cut to `width` with an ellipsis as an AppKit label
    that truncates its tail cuts it. qt_elide answers when AppKit cannot."""
    try:
        return _cut(text, _appkit_font(*style), width)
    except Exception:
        log.debug("could not truncate in AppKit", exc_info=True)
        return qt_elide(text, width)


def break_lines(text: str, style: tuple[str, int, str], width: float, max_lines: int,
                qt_lines: Callable[[str, float, int], list[str]]) -> list[str]:
    """The lines an AppKit wrapping label `width` wide (its frame less the cell's insets) shows
    of `text` in the text style, without their trailing spaces. With `max_lines` the last line
    shown is cut with an ellipsis when more text follows, as a label with truncatesLastVisibleLine
    does. Remembered per text, style, width and line count; qt_lines answers when AppKit cannot."""
    key = (text, style, round(width, 3), max_lines)
    lines = _lines.get(key)
    if lines is None:
        try:
            font = _appkit_font(*style)
            spans = _wrap(text, font, width)
            if max_lines and len(spans) > max_lines:
                rest = text[spans[max_lines - 1][0]:].replace("\n", " ")
                lines = [text[a:b].rstrip() for a, b in spans[: max_lines - 1]] + [_last_line(rest, font, width)]
            else:
                lines = [text[a:b].rstrip() for a, b in spans]
        except Exception:
            log.debug("could not break lines in AppKit", exc_info=True)
            return qt_lines(text, width, max_lines)
        if len(_lines) >= LINES_KEPT:
            _lines.clear()
        _lines[key] = lines
    return list(lines)


# The system's own symbols (SF Symbols), for the Qt interface's icons (lyssna/ui/icons.py). Apple's
# symbols may only be used on Apple platforms, so the interface carries drawn outlines of its own
# for Windows; on the Mac it draws the very images the AppKit interface's buttons and image views
# showed. Measured on the legacy pixels at 2x: an NSButton or NSImageView draws a symbol image
# at its own whole-point size, drawn black into a bitmap of that size and tinted through its
# alpha by contentTintColor, which is what symbol_image() returns (to within 1 of 255).
SYMBOL_WEIGHTS = {"regular": "NSFontWeightRegular", "medium": "NSFontWeightMedium",
                  "semibold": "NSFontWeightSemibold", "bold": "NSFontWeightBold", "heavy": "NSFontWeightHeavy"}
SYMBOLS_KEPT = 512  # symbol_image's memory of tinted images, emptied when full
_symbol_masks: dict[tuple[str, float, str, float], object] = {}  # the untinted bitmaps (NSBitmapImageRep), or None
_symbols: dict[tuple, object] = {}


def _srgb_rep(pw: int, ph: int, w: float, h: float):
    """An empty premultiplied RGBA bitmap of pw x ph pixels for a w x h point drawing, tagged sRGB so
    the colours drawn into it are not converted on the way in."""
    import AppKit

    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, pw, ph, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 32)
    rep = rep.bitmapImageRepByRetaggingWithColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
    rep.setSize_((w, h))
    return rep


def _draw_into(rep, draw: Callable[[], None]) -> None:
    import AppKit

    AppKit.NSGraphicsContext.saveGraphicsState()
    try:
        AppKit.NSGraphicsContext.setCurrentContext_(AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
        draw()
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()


def _symbol_mask(name: str, size: float, weight: str, dpr: float):
    """The symbol configured as the AppKit interface configured it (imageWithSystemSymbolName and
    NSImageSymbolConfiguration's point size and weight), drawn black at its own size; None when the
    system has no symbol by that name."""
    key = (name, size, weight, dpr)
    if key not in _symbol_masks:
        import AppKit

        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        rep = None
        if image is not None:
            config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
                size, getattr(AppKit, SYMBOL_WEIGHTS[weight]))
            image = image.imageWithSymbolConfiguration_(config)
            w, h = float(image.size().width), float(image.size().height)
            rep = _srgb_rep(max(1, math.ceil(w * dpr - 1e-6)), max(1, math.ceil(h * dpr - 1e-6)), w, h)
            _draw_into(rep, lambda: image.drawInRect_(((0, 0), (w, h))))
        _symbol_masks[key] = rep
    return _symbol_masks[key]


def symbol_image(name: str, size: float, weight: str, color: tuple[int, int, int, float], dpr: float):
    """The system symbol `name` at `size` points and `weight` ("regular" to "heavy"), tinted
    `color` (r, g, b in 0..255 and alpha in 0..1, sRGB), as a QImage at device pixel ratio `dpr`
    whose logical size is the symbol image's own (an NSButton or NSImageView centres that box).
    None when the system has no such symbol or AppKit cannot draw it: the caller then draws its
    own outline. Remembered per symbol, size, weight, colour and ratio."""
    key = (name, float(size), weight, tuple(color), float(dpr))
    if key in _symbols:
        return _symbols[key]
    try:
        import AppKit
        from PySide6.QtGui import QImage

        mask = _symbol_mask(name, float(size), weight, float(dpr))
        if mask is None:
            image = None
        else:
            w, h = float(mask.size().width), float(mask.size().height)
            pw, ph = mask.pixelsWide(), mask.pixelsHigh()
            rep = _srgb_rep(pw, ph, w, h)
            r, g, b, a = color
            tint = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)

            def draw() -> None:
                # A template image tinted by contentTintColor: the colour, through the symbol's coverage.
                mask.drawInRect_(((0, 0), (w, h)))
                tint.set()
                AppKit.NSRectFillUsingOperation(((0, 0), (w, h)), AppKit.NSCompositingOperationSourceIn)

            _draw_into(rep, draw)
            row = rep.bytesPerRow()
            data = bytes(rep.bitmapData())[: row * ph]
            image = QImage(data, pw, ph, row, QImage.Format.Format_RGBA8888_Premultiplied).copy()
            image.setDevicePixelRatio(float(dpr))
    except Exception:
        log.debug("could not draw the system symbol %s", name, exc_info=True)
        return None
    if len(_symbols) >= SYMBOLS_KEPT:
        _symbols.clear()  # a fading colour would otherwise grow it forever
    _symbols[key] = image
    return image


SYSTEM_COLORS = {"red": "systemRedColor", "green": "systemGreenColor", "placeholder": "placeholderTextColor",
                 "disabled": "disabledControlTextColor", "selection": "selectedTextBackgroundColor",
                 "selection_inactive": "unemphasizedSelectedTextBackgroundColor", "selected_text": "selectedTextColor"}
_colors: dict[tuple[str, bool], tuple[int, int, int, float]] = {}


def system_color(name: str, dark: bool) -> tuple[int, int, int, float]:
    """One of the system's named colours the AppKit interface used ("red" for errors, "green"
    for success, "placeholder" for an empty field's hint, "disabled" for a disabled button's
    title, which AppKit draws in it whatever colour the title has; "selection", "selection_inactive"
    and "selected_text" for selected text in an active and an inactive window, the first being
    the user's highlight colour) as (r, g, b, alpha) in sRGB, resolved
    for light or dark mode as AppKit resolves it: the values follow the macOS version and the
    user's contrast setting (systemRed is #FF383C in light mode on macOS 26, #FF3B30 before)."""
    key = (name, dark)
    if key not in _colors:
        try:
            import AppKit

            look = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua if dark else AppKit.NSAppearanceNameAqua)
            out = []

            def resolve() -> None:
                c = getattr(AppKit.NSColor, SYSTEM_COLORS[name])().colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
                out.append((round(c.redComponent() * 255), round(c.greenComponent() * 255), round(c.blueComponent() * 255),
                            round(float(c.alphaComponent()), 3)))

            look.performAsCurrentDrawingAppearance_(resolve)
            _colors[key] = out[0]
        except Exception:
            log.debug("could not resolve the system colour %s", name, exc_info=True)
            from lyssna.win.appearance import system_color as fixed

            return fixed(name, dark)
    return _colors[key]


def reduce_motion() -> bool:
    """Has the user asked for less motion (System Settings > Accessibility > Display > Reduce
    motion)? The same setting the AppKit interface read."""
    try:
        import AppKit

        return bool(AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        log.debug("could not read the reduce motion setting", exc_info=True)
        return False


def short_time(when: datetime) -> str:
    """The time of day in the user's own 12 or 24 hour style, as the AppKit interface wrote it:
    NSDateFormatter with the template "jmm". Qt's QLocale.system() is not used here because on
    a Mac it follows the language alone (en_US gives "3:42 PM") and misses the region and the
    24-hour setting that the system formatter follows ("15:42" for a Swedish region)."""
    global _time_formatter
    import AppKit

    if _time_formatter is None:
        _time_formatter = AppKit.NSDateFormatter.alloc().init()
        _time_formatter.setLocalizedDateFormatFromTemplate_("jmm")
    return str(_time_formatter.stringFromDate_(AppKit.NSDate.dateWithTimeIntervalSince1970_(when.timestamp())))
