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
    Listeners hear about it as about a system change."""
    global _override
    if scheme not in ("light", "dark", None):
        raise ValueError(f"unknown scheme {scheme!r}")
    before = is_dark()
    _override = scheme
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
