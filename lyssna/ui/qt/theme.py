"""Lyssna's colours and fonts for the Qt interface, the twin of ui/theme.py (and of the glow
colours in ui/widgets.py).

The Mac resolves its dynamic NSColors against the appearance at draw time. Qt has no dynamic
colours, so every colour here is a function that reads the current scheme when it is called:
call them inside paintEvent, never at construction, and a widget follows a light or dark switch
as soon as it repaints. on_scheme_change tells you when to repaint (FloatingPanel already does).

Every number comes from shared/tokens.py. Sizes are logical pixels: 13 pt on the Mac is 13 px
here, which is why the fonts use setPixelSize."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication

from ..shared.tokens import (
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

log = logging.getLogger("lyssna.ui.qt.theme")

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

_WEIGHTS = {
    "regular": QFont.Weight.Normal,
    "medium": QFont.Weight.Medium,
    "semibold": QFont.Weight.DemiBold,
    "bold": QFont.Weight.Bold,
}

_families: dict[str, str | None] = {}


def _first_family(names: tuple[str, ...]) -> str | None:
    """The first of `names` the font database has, or None."""
    key = "|".join(names)
    if key not in _families:
        have = set(QFontDatabase.families())
        _families[key] = next((n for n in names if n in have), None)
    return _families[key]


def _weight(weight: str) -> QFont.Weight:
    try:
        return _WEIGHTS[weight]
    except KeyError:
        raise ValueError(f"unknown font weight {weight!r}") from None


def ui_font(size: float, weight: str = "regular") -> QFont:
    """The system interface font at `size` logical pixels: Segoe UI Variable Text (or Segoe UI)
    on Windows, the platform's own elsewhere so the preview on a Mac looks native too."""
    if sys.platform == "win32":
        family = _first_family(("Segoe UI Variable Text", "Segoe UI"))
    else:
        # The platform's own; the offscreen platform (shots, tests) names a generic family that
        # does not exist, so on a Mac fall back to the system font by its real name.
        system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        family = _first_family((system, ".AppleSystemUIFont", "Helvetica Neue"))
    font = QFont(family) if family else QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(max(1, round(size)))
    font.setWeight(_weight(weight))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def digits_font(size: float, weight: str = "regular") -> QFont:
    """ui_font with tabular figures, so times and speeds do not jiggle as they count (the twin
    of the Mac's monospacedDigitSystemFont)."""
    font = ui_font(size, weight)
    try:
        font.setFeature(QFont.Tag("tnum"), 1)
    except (AttributeError, TypeError):  # older Qt: Segoe UI's figures are tabular anyway
        pass
    return font


def serif_font(size: float, weight: str = "semibold") -> QFont:
    """The window headline serif: Georgia on Windows. The Mac uses its system serif, New York;
    in the preview on a Mac we use New York when Qt can see it, else Georgia."""
    names = ("Georgia", "Times New Roman") if sys.platform == "win32" else ("New York", "Georgia", "Times New Roman")
    family = _first_family(names)
    font = QFont(family) if family else ui_font(size, weight)
    font.setPixelSize(max(1, round(size)))
    font.setWeight(_weight(weight))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


# -- motion ------------------------------------------------------------------------------------

SPI_GETCLIENTAREAANIMATION = 0x1042


def reduce_motion() -> bool:
    """Has the user turned animations off (Windows: Settings > Accessibility > Visual effects >
    Animation effects)? Always False off Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.user32.SystemParametersInfoW
        fn.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        fn.restype = wintypes.BOOL
        on = wintypes.BOOL(True)
        if not fn(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(on), 0):
            return False
        return not bool(on.value)
    except Exception:
        log.debug("could not read the animation setting", exc_info=True)
        return False
