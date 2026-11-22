"""Lyssna's colours and fonts for the Qt interface, the twin of legacy/ui/theme.py (and of the glow
colours in legacy/ui/widgets.py).

The Mac resolves its dynamic NSColors against the appearance at draw time. Qt has no dynamic
colours, so every colour here is a function that reads the current scheme when it is called:
call them inside paintEvent, never at construction, and a widget follows a light or dark switch
as soon as it repaints. on_scheme_change tells you when to repaint (FloatingPanel already does).

Every number comes from layout/tokens.py. Sizes are logical pixels: 13 pt on the Mac is 13 px
here, which is why the fonts use setPixelSize.

The fonts come from host.appearance: Apple's on the Mac, tuned to AppKit's widths, and the
bundled free matches (Inter, Literata) on Windows, tuned to the Mac's. use_fonts() forces one
set, so the preview on a Mac can show what Windows will draw."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from types import ModuleType

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from .. import host
from .layout.tokens import (
    CLEAR_GLOW_ALPHA,
    CONTENT_DIM_ALPHA,
    CREAM,
    FILL_SOFT_ALPHA,
    HAIRLINE_ALPHA,
    INK,
    INK_TEXT,
    PANEL_BG,
    SQUIGGLE_FILL,
    SQUIGGLE_OUTLINE,
    WHITE_GLOW_ALPHA,
)

log = logging.getLogger("lyssna.ui.theme")

# -- light and dark ----------------------------------------------------------------------------

_override: str | None = None  # "light" or "dark" forced by set_scheme (the preview), None to follow the system


class _Notifier(QObject):
    changed = Signal()


_notifier: _Notifier | None = None
_hooked = False


def _get_notifier() -> _Notifier:
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    _hook()
    return _notifier


def _hook() -> None:
    """Forward the system's colour scheme changes once a QGuiApplication exists."""
    global _hooked
    if _hooked or QGuiApplication.instance() is None:
        return
    _hooked = True
    QGuiApplication.styleHints().colorSchemeChanged.connect(_system_changed)


def _system_changed(*_args) -> None:
    if _override is None and _notifier is not None:
        _notifier.changed.emit()


def is_dark() -> bool:
    """Is the interface drawn in dark mode now? The preview's override wins, else the system's
    scheme (light when there is no application yet or the system does not say)."""
    if _override is not None:
        return _override == "dark"
    if QGuiApplication.instance() is None:
        return False
    _hook()
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def set_scheme(scheme: str | None) -> None:
    """Force "light" or "dark" (for the preview and tests), or None to follow the system again.
    Listeners hear about it as about a system change.

    Qt is asked for the same scheme, so the forced scheme draws as the system would draw it:
    on the Mac, Qt rasterises its glyph masks dark on light when the application's scheme is
    light and light on dark otherwise, and Core Text thickens light glyphs more than dark ones
    (AppKit does the same by the text's own colour). Our colours alone in light mode on a Mac
    set to dark would come out about 12 percent heavier than the app ever draws them."""
    global _override
    if scheme not in ("light", "dark", None):
        raise ValueError(f"unknown scheme {scheme!r}")
    before = is_dark()
    _override = scheme
    if QGuiApplication.instance() is not None:
        wanted = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark, None: Qt.ColorScheme.Unknown}[scheme]
        QGuiApplication.styleHints().setColorScheme(wanted)
    if is_dark() != before and _notifier is not None:
        _notifier.changed.emit()


def on_scheme_change(callback: Callable[[], None]) -> Callable[[], None]:
    """Call `callback()` on the GUI thread whenever light or dark mode flips. Returns a function
    that unsubscribes it."""
    n = _get_notifier()
    n.changed.connect(callback)

    def unsubscribe() -> None:
        try:
            n.changed.disconnect(callback)
        except (RuntimeError, TypeError, SystemError):  # SystemError: the subscriber's C++ side went first, at exit
            pass
    return unsubscribe


# -- colours -----------------------------------------------------------------------------------


def rgba(rgb: tuple[int, int, int], alpha: float = 1.0) -> QColor:
    """A QColor from a 0-255 RGB tuple and an alpha in 0..1."""
    r, g, b = rgb
    return QColor.fromRgbF(r / 255, g / 255, b / 255, max(0.0, min(1.0, alpha)))


def surface(alpha: float = 1.0) -> QColor:
    """Window and card background: cream in light mode, ink in dark."""
    return rgba(INK if is_dark() else CREAM, alpha)


def content(alpha: float = 1.0) -> QColor:
    """Text, icons and the accent: ink in light mode, cream in dark."""
    return rgba(CREAM if is_dark() else INK_TEXT, alpha)


def content_dim() -> QColor:
    """Secondary text."""
    return content(CONTENT_DIM_ALPHA)


def hairline() -> QColor:
    """Borders and separators."""
    return content(HAIRLINE_ALPHA)


def fill_soft() -> QColor:
    """Soft pills, keycaps, tracks."""
    return content(FILL_SOFT_ALPHA)


def clear_glow() -> QColor:
    """The hover glow on brand surfaces and bare buttons: content at a low alpha."""
    return content(CLEAR_GLOW_ALPHA)


def white_glow() -> QColor:
    """The hover glow on content-filled discs (the play button)."""
    return QColor.fromRgbF(1.0, 1.0, 1.0, WHITE_GLOW_ALPHA)


def squiggle_fill() -> QColor:
    """The idle squiggle's line, the same in both modes."""
    return QColor.fromRgbF(*SQUIGGLE_FILL)


def squiggle_outline() -> QColor:
    """The idle squiggle's soft white halo, the same in both modes."""
    return QColor.fromRgbF(*SQUIGGLE_OUTLINE)


def panel_bg() -> QColor:
    """The fixed dark background of a floating panel that does not use the brand surface."""
    return QColor.fromRgbF(*PANEL_BG)


def secure_bullet() -> int | None:
    """The character a password field shows for each hidden one (a code point), or None for
    the style's own: the Mac's secure field bullet on the Mac."""
    return (_forced or host.appearance).SECURE_BULLET


def system_color(name: str) -> QColor:
    """One of the Mac's named system colours for the current scheme: "red" (errors), "green"
    (success), "placeholder" (an empty field's hint), "disabled" (a disabled button's title),
    "selection", "selection_inactive" and "selected_text" (selected text in an active and an
    inactive window).
    On the Mac the system's own values
    (host.appearance), on Windows fixed ones."""
    r, g, b, a = (_forced or host.appearance).system_color(name, is_dark())
    return QColor.fromRgbF(r / 255, g / 255, b / 255, a)


def panel_border() -> QColor:
    """The border of a panel on panel_bg (white at 0.10, as on the Mac)."""
    return QColor.fromRgbF(1.0, 1.0, 1.0, 0.10)


# -- fonts -------------------------------------------------------------------------------------
#
# A text style is (kind, pixel size, weight). kind is "text" (the system font), "italic", "digits"
# (tabular figures, the twin of the Mac's monospacedDigitSystemFont) or "serif" (the headlines).

_WEIGHTS = {
    "regular": QFont.Weight.Normal,
    "medium": QFont.Weight.Medium,
    "semibold": QFont.Weight.DemiBold,
    "bold": QFont.Weight.Bold,
}

# Every text style the app uses, from the AppKit interface (lyssna/legacy/ui): the setup and
# history windows, the player, the toasts and What's new. The Windows twin's spacing table is
# measured for exactly these, and a test holds the two together; a new style goes here first.
TEXT_STYLES: tuple[tuple[str, int, str], ...] = (
    ("text", 11, "regular"), ("text", 11, "semibold"),
    ("text", 12, "regular"), ("text", 12, "medium"), ("text", 12, "semibold"),
    ("text", 13, "regular"), ("text", 13, "medium"), ("text", 13, "semibold"), ("text", 13, "bold"),
    ("text", 14, "regular"), ("text", 14, "medium"), ("text", 14, "semibold"),
    ("italic", 12, "regular"),
    ("digits", 11, "regular"), ("digits", 11, "medium"), ("digits", 12, "regular"), ("digits", 12, "semibold"),
    ("digits", 28, "semibold"),
    ("serif", 20, "semibold"), ("serif", 22, "semibold"),
)

FONT_SETS = {"mac": "lyssna.mac.appearance", "windows": "lyssna.win.appearance"}

_forced: ModuleType | None = None  # the font set use_fonts() chose, None for this computer's
_loaded: set[ModuleType] = set()  # the font sets whose files are in Qt's font database
_fonts: dict[tuple[ModuleType, str, int, str], QFont] = {}


def use_fonts(name: str | None) -> None:
    """Draw with the "mac" or "windows" font set whatever the computer (the preview and tests),
    or None for this computer's own."""
    global _forced
    if name is not None and name not in FONT_SETS:
        raise ValueError(f"unknown font set {name!r}")
    _forced = importlib.import_module(FONT_SETS[name]) if name else None
    _fonts.clear()


def _weight(weight: str) -> QFont.Weight:
    try:
        return _WEIGHTS[weight]
    except KeyError:
        raise ValueError(f"unknown font weight {weight!r}") from None


def _load(fonts: ModuleType) -> None:
    """Add the font set's files to Qt's font database, once, when there is an application."""
    if fonts in _loaded or QGuiApplication.instance() is None:
        return
    _loaded.add(fonts)
    for path in fonts.font_files():
        if QFontDatabase.addApplicationFont(str(path)) < 0:
            log.warning("could not load the font %s", path)


def _font(kind: str, size: float, weight: str) -> QFont:
    fonts = _forced or host.appearance
    px = max(1, round(size))
    key = (fonts, kind, px, weight)
    if key not in _fonts:
        _weight(weight)  # an unknown weight fails here, before anything is measured
        _load(fonts)
        font = QFont(fonts.SERIF_FAMILY if kind == "serif" else fonts.TEXT_FAMILY)
        font.setPixelSize(px)
        font.setWeight(_weight(weight))
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        font.setItalic(kind == "italic")
        if kind == "digits":
            font.setFeature(QFont.Tag("tnum"), 1)
        gap = fonts.tracking(kind, px, weight, QFontMetricsF(font).horizontalAdvance)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, gap)
        _fonts[key] = font
    return QFont(_fonts[key])


def ui_font(size: float, weight: str = "regular", *, italic: bool = False) -> QFont:
    """The interface font at `size` logical pixels: San Francisco on the Mac (as AppKit draws
    it), Inter on Windows. `italic` is the history's original-transcript style."""
    return _font("italic" if italic else "text", size, weight)


def digits_font(size: float, weight: str = "regular") -> QFont:
    """ui_font with tabular figures, so times and speeds do not jiggle as they count (the twin
    of the Mac's monospacedDigitSystemFont)."""
    return _font("digits", size, weight)


def serif_font(size: float, weight: str = "semibold") -> QFont:
    """The window headline serif: New York on the Mac, Literata on Windows."""
    return _font("serif", size, weight)


def _style_font(kind: str, size: int, weight: str) -> QFont:
    if kind == "serif":
        return serif_font(size, weight)
    if kind == "digits":
        return digits_font(size, weight)
    return ui_font(size, weight, italic=kind == "italic")


def text_style(font: QFont) -> tuple[str, int, str]:
    """The TEXT_STYLES entry a font from this module was made for, read from the font itself (a
    widget's font() is a resolved copy, so it cannot be looked up by identity). A font that is
    not one of the styles raises ValueError: every text-driven size measures a known style."""
    fonts = _forced or host.appearance
    if font.family() == fonts.SERIF_FAMILY:
        kind = "serif"
    elif font.isFeatureSet(QFont.Tag("tnum")):
        kind = "digits"
    else:
        kind = "italic" if font.italic() else "text"
    weight = next((name for name, w in _WEIGHTS.items() if w == font.weight()), "")
    style = (kind, font.pixelSize(), weight)
    if style not in TEXT_STYLES:
        raise ValueError(f"{font.family()} {font.pixelSize()} px weight {font.weight()} is not one of the text styles")
    return style


def text_width(text: str, font: QFont) -> float:
    """How wide `text` is in `font` (one of the text styles) for sizing a frame to it: on the
    Mac the width AppKit gives, so frames land on the AppKit interface's whole points; on
    Windows Qt's own. Drawing still lays out glyphs with Qt's advances, so measure with
    QFontMetricsF where a glyph's position inside a frame is wanted."""
    fonts = _forced or host.appearance
    style = text_style(font)
    return fonts.text_width(text, style, QFontMetricsF(_style_font(*style)).horizontalAdvance)


def _qt_lines(text: str, font: QFont, width: float, max_lines: int) -> list[str]:
    """Qt's own lines of `text` on lines `width` wide, the last one shown elided when more text
    follows: what Windows draws."""
    from PySide6.QtGui import QTextLayout, QTextOption

    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    spans = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1.0, width))
        spans.append((line.textStart(), line.textStart() + line.textLength()))
    layout.endLayout()
    if max_lines and len(spans) > max_lines:
        rest = " ".join(text[spans[max_lines - 1][0]:].replace(" ", " ").split())
        last = QFontMetricsF(font).elidedText(rest, Qt.TextElideMode.ElideRight, width)
        return [text[a:b].rstrip() for a, b in spans[: max_lines - 1]] + [last]
    return [text[a:b].rstrip() for a, b in spans]


def break_lines(text: str, font: QFont, width: float, max_lines: int = 0) -> list[str]:
    """The lines a wrapping label shows of `text` in `font` (one of the text styles) on lines
    `width` wide, with `max_lines` the last one cut with an ellipsis when more text follows. On
    the Mac they are AppKit's lines, so a paragraph breaks at the very words it broke at in the
    AppKit interface; on Windows Qt's. Draw each with draw_text_at, capped at `width`."""
    fonts = _forced or host.appearance
    style = text_style(font)
    plain = _style_font(*style)
    return fonts.break_lines(text, style, width, max_lines, lambda t, w, n: _qt_lines(t, plain, w, n))


def elide(text: str, font: QFont, width: float) -> str:
    """`text` on one line `width` wide, cut with an ellipsis when it does not fit: on the Mac
    where an AppKit label that truncates its tail cuts it, on Windows where Qt does."""
    fonts = _forced or host.appearance
    style = text_style(font)
    plain = _style_font(*style)
    return fonts.truncate(text, style, width,
                          lambda t, w: QFontMetricsF(plain).elidedText(t, Qt.TextElideMode.ElideRight, w))


ELLIPSIS = "…"
SPACINGS_KEPT = 4096  # draw_text_at's memory of spacings, emptied when full
_spacings: dict[tuple, tuple[float, float]] = {}


def drawn_width(text: str, font: QFont, width: float | None = None, cut: bool = False) -> float:
    """How wide a line of `text` in `font` is drawn: text_width (on the Mac AppKit's own width of
    that very string). When that is wider than `width` (the truncated last line of a wrapping
    label), the line is squeezed as the platform squeezes it: on the Mac every kept character
    loses the font set's TIGHTEN_EM of the font size (AppKit's tightening, all of it), elsewhere
    the line is fitted to `width`. `cut`: the text ends in an ellipsis that a cut added, which
    AppKit sets at the kept text's own advance, so the two are measured apart. A font that is not
    one of the text styles is drawn at Qt's own width."""
    kept = len(text) - (len(ELLIPSIS) if cut and text.endswith(ELLIPSIS) else 0)
    try:
        if kept < len(text):
            w = text_width(text[:kept], font) + text_width(ELLIPSIS, font)
        else:
            w = text_width(text, font)
    except ValueError:
        return QFontMetricsF(font).horizontalAdvance(text)
    if width is None or w <= width:
        return w
    tighten = (_forced or host.appearance).TIGHTEN_EM
    return w - tighten * font.pixelSize() * kept if tighten else width


def _spaced(text: str, font: QFont, target: float) -> QFont:
    """`font` with the letter spacing, and then the word spacing, that make `text` `target`
    wide. Qt keeps both in 1/64 pixel steps, so letter spacing alone misses by up to half a
    step per letter (a pixel over 70 letters); the spaces take up what is left."""
    key = (text, font.toString(), font.isFeatureSet(QFont.Tag("tnum")), round(target, 3))
    hit = _spacings.get(key)
    if hit is None:
        letters, words = font.letterSpacing(), font.wordSpacing()
        now = QFontMetricsF(font).horizontalAdvance(text)
        probe = QFont(font)
        probe.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letters + 1.0)
        per = QFontMetricsF(probe).horizontalAdvance(text) - now  # pixels added per pixel of letter spacing
        if per > 0:
            letters = round((letters + (target - now) / per) * 64) / 64
        probe = QFont(font)
        probe.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letters)
        left = target - QFontMetricsF(probe).horizontalAdvance(text)
        probe.setWordSpacing(words + 1.0)
        per = QFontMetricsF(probe).horizontalAdvance(text) - (target - left)  # pixels added per pixel of word spacing
        if per > 0:
            words += left / per
        if len(_spacings) >= SPACINGS_KEPT:
            _spacings.clear()
        hit = _spacings[key] = (letters, words)
    out = QFont(font)
    out.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, hit[0])
    out.setWordSpacing(hit[1])
    return out


def _offsets(text: str, font: QFont, cut: bool) -> list[float] | None:
    """The x of each character of `text` as the Mac draws it (host.appearance.text_offsets), the
    ellipsis of a cut at the kept text's end; None on Windows or for a font outside the styles."""
    fonts = _forced or host.appearance
    try:
        style = text_style(font)
    except ValueError:
        return None
    if cut and text.endswith(ELLIPSIS):
        kept = fonts.text_offsets(text[: -len(ELLIPSIS)], style)
        return None if kept is None else [*kept, kept[-1] + text_width(ELLIPSIS, font)]
    return fonts.text_offsets(text, style)


def _one_glyph_each(text: str) -> bool:
    """Can every character of `text` be drawn on its own? Not a combining mark, a joiner or a
    variation selector (they shape with their neighbours), nor anything beyond the basic plane
    (emoji, often sequences); such text is placed word by word instead."""
    import unicodedata

    return all(ord(ch) <= 0xFFFF and not 0xD800 <= ord(ch) <= 0xDFFF and not 0xFE00 <= ord(ch) <= 0xFE0F
               and unicodedata.category(ch) not in ("Mn", "Me", "Mc", "Cf") for ch in text)


def draw_text_at(p: QPainter, x: float, baseline: float, text: str, font: QFont, width: float | None = None,
                 cut: bool = False) -> float:
    """Draw one line of `text` with its first glyph's origin at (x, baseline), drawn_width wide.
    On the Mac every letter goes where AppKit puts it (host.appearance.text_offsets), all
    squeezed alike when the line is (a truncated last line); text whose characters shape
    together (combining marks, emoji) goes word by word, each word at AppKit's start and width.
    Where there are no AppKit positions (Windows) the line is spaced to its width as a whole;
    there the widths agree and Qt draws the line as it is. Returns the width drawn."""
    from PySide6.QtCore import QPointF

    if not text:
        return 0.0
    target = drawn_width(text, font, width, cut)
    offsets = _offsets(text, font, cut) if font.letterSpacingType() == QFont.SpacingType.AbsoluteSpacing else None
    if offsets is None or offsets[-1] <= 0:
        natural = QFontMetricsF(font).horizontalAdvance(text)
        if abs(target - natural) >= 0.01 and font.letterSpacingType() == QFont.SpacingType.AbsoluteSpacing:
            font = _spaced(text, font, target)
        p.setFont(font)
        p.drawText(QPointF(x, baseline), text)
        return QFontMetricsF(font).horizontalAdvance(text)
    # A squeezed line (a truncated last line, see drawn_width) loses the same amount after every
    # letter, as AppKit tightens it; the ellipsis a cut added keeps its width.
    kept = len(text) - (len(ELLIPSIS) if cut and text.endswith(ELLIPSIS) else 0)
    step = (offsets[-1] - target) / max(1, kept)
    at = [offset - min(i, kept) * step for i, offset in enumerate(offsets)]
    if _one_glyph_each(text) and not font.underline():
        # Every letter where AppKit puts it: AppKit's kerning, and nothing evened out.
        p.setFont(font)
        for i, ch in enumerate(text):
            if ch != " ":
                p.drawText(QPointF(x + at[i], baseline), ch)
        return target
    i, n = 0, len(text)
    while i < n:
        if text[i] == " ":
            i += 1
            continue
        j = text.find(" ", i)
        j = n if j < 0 else j
        word = text[i:j]
        want = at[j] - at[i]
        natural = QFontMetricsF(font).horizontalAdvance(word)
        p.setFont(_spaced(word, font, want) if abs(want - natural) >= 0.01 else font)
        p.drawText(QPointF(x + at[i], baseline), word)
        i = j
    return target


def draw_text(p: QPainter, rect: QRectF, flags, text: str, font: QFont, cut: bool = False) -> float:
    """draw_text_at for one line placed in `rect` as QPainter.drawText places it with `flags`
    (left, right or centred; top, bottom or centred). Returns the width drawn."""
    fm = QFontMetricsF(font)
    w = drawn_width(text, font, None, cut)
    if _offsets(text, font, cut) is None and abs(w - fm.horizontalAdvance(text)) < 0.01:
        p.setFont(font)  # nothing to follow (Windows): Qt places the line itself, as it always has
        p.drawText(rect, int(flags), text)
        return w
    flags = Qt.AlignmentFlag(int(flags))
    if flags & Qt.AlignmentFlag.AlignRight:
        x = rect.right() - w
    elif flags & Qt.AlignmentFlag.AlignHCenter:
        x = rect.left() + (rect.width() - w) / 2
    else:
        x = rect.left()
    if flags & Qt.AlignmentFlag.AlignBottom:
        top = rect.bottom() - fm.height()
    elif flags & Qt.AlignmentFlag.AlignVCenter:
        top = rect.top() + (rect.height() - fm.height()) / 2
    else:
        top = rect.top()
    return draw_text_at(p, x, top + fm.ascent(), text, font, None, cut)


# -- motion ------------------------------------------------------------------------------------


def reduce_motion() -> bool:
    """Has the user asked the system for less motion? Animations then jump to their end."""
    return host.appearance.reduce_motion()


# -- previews (python -m lyssna.ui.preview) ----------------------------------------------------

_SAMPLES = {
    "text": "Select text in any app and press",
    "italic": "The original transcript, as it was heard",
    "digits": "0:00  1:11 / 10:08  -0:42  1.25×  1,204",
    "serif": "Welcome to Lyssna",
}


class _FontSheet(QWidget):
    """Every text style: its name, a line of the app's wording, and digits."""

    ROW_GAP = 10.0
    LABEL_W = 150.0

    def __init__(self):
        super().__init__()
        rows = sum(max(18.0, size * 1.5) + self.ROW_GAP for _kind, size, _weight in TEXT_STYLES)
        self.setFixedSize(760, round(rows + 52))

    def paintEvent(self, ev) -> None:
        fonts = _forced or host.appearance
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), surface())
        p.setPen(content_dim())
        p.setFont(ui_font(11))
        p.drawText(QRectF(16, 12, 720, 18), int(Qt.AlignmentFlag.AlignVCenter),
                   f"{fonts.TEXT_FAMILY} and {fonts.SERIF_FAMILY}")
        y = 40.0
        for kind, size, weight in TEXT_STYLES:
            h = max(18.0, size * 1.5)
            p.setPen(content_dim())
            p.setFont(ui_font(11))
            p.drawText(QRectF(16, y, self.LABEL_W, h), int(Qt.AlignmentFlag.AlignVCenter), f"{kind} {size} {weight}")
            p.setPen(content())
            font = _style_font(kind, size, weight)
            p.setFont(font)
            sample = _SAMPLES[kind] if kind == "digits" else f"{_SAMPLES[kind]}  0123456789"
            p.drawText(QRectF(16 + self.LABEL_W, y, 760 - self.LABEL_W - 24, h), int(Qt.AlignmentFlag.AlignVCenter), sample)
            y += h + self.ROW_GAP
        p.end()


PREVIEWS = {"fonts": _FontSheet}
