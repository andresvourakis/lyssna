"""Windows's fonts, its animation setting and its time format, for the Qt interface (lyssna/ui/theme.py,
the history's times).

Why these fonts: the Mac uses Apple's San Francisco and New York, which may not be used on
Windows, so the app bundles the closest free matches (both SIL Open Font License, shipped as
the original files with their licences in lyssna/ui/assets/fonts/):

- Text: Inter 4.1, the static hinted TrueType files (hinted for ClearType) in the weights the
  app uses, regular, medium, semibold and bold, plus italic for the history's original
  transcripts. Measured at 11 to 14 px against San Francisco by AppKit, its stems are the same
  thickness at the same weight (so no weight is swapped) and its x-height about 2 percent taller.
- Headlines: Literata 36pt SemiBold. Picked by measuring the headlines ("Welcome to Lyssna",
  "History", "What's new in Lyssna 0.3.1" at 20 and 22 px) against New York by AppKit.
  New York's x-height is 0.494 of the size, cap height 0.71 and stroke contrast 2.6 (the
  thick side of an "o" over its thin top). Literata 36pt: 0.511, 0.71, 2.5, widths +0.9 to
  +5.4 percent. Source Serif 4: 0.477, 0.665, 2.75, widths -4.5 to -6 percent and visibly
  lighter. Newsreader 16pt: 0.432, 0.67, 2.75, widths -8 to -11.5 percent. Libre Caslon Text:
  0.523, 0.761 (capitals far too tall), 2.9, widths +3.7 to +5.4 percent. Literata 36pt is the
  closest on every count and by eye has New York's weight and sturdy serifs.

SPACING tunes each text style's width to the Mac's: per style, the letter spacing (in 64ths of
a pixel, Qt's own step) that brings the bundled font's width for the app's own wording closest
to the Mac's, measured on a Mac with AppKit (Windows has nothing to measure against). The
comments give what remains: the mean and the worst difference over that wording, and the
worst before tuning. Inter's figures are wider than San Francisco's and its punctuation
narrower, which one spacing cannot even out, so the digit styles keep the largest gaps.

ctypes loads inside reduce_motion() and Qt inside short_time(), so this module imports on any system. The Mac twin
(lyssna/mac/appearance.py) offers the same names."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lyssna.win.appearance")

TEXT_FAMILY = "Inter"
SERIF_FAMILY = "Literata 36pt"
SECURE_BULLET: int | None = None  # a hidden character as Qt's style shows it (the Mac twin names AppKit's)
TIGHTEN_EM = 0.0  # Qt does not tighten a truncated line (the Mac twin describes AppKit's)

FONT_DIR = Path(__file__).resolve().parent.parent / "ui" / "assets" / "fonts"
FONT_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf", "Inter-Italic.ttf",
              "Literata36pt-SemiBold.ttf")

# (kind, pixel size, weight) -> letter spacing in 64ths of a pixel.
SPACING: dict[tuple[str, int, str], int] = {
    ("text", 11, "regular"): 2,           # 0.58% mean, +1.93% worst (before -2.37%)
    ("text", 11, "semibold"): 9,          # 0.56% mean, +1.79% worst (before -3.68%)
    ("text", 12, "regular"): -2,          # 0.60% mean, +1.99% worst (before +2.45%)
    ("text", 12, "medium"): 3,            # 0.56% mean, +1.82% worst (before -2.21%)
    ("text", 12, "semibold"): 5,          # 0.57% mean, +1.75% worst (before -2.50%)
    ("text", 13, "regular"): -8,          # 0.59% mean, -1.96% worst (before +3.54%)
    ("text", 13, "medium"): -2,           # 0.56% mean, +1.80% worst (before +2.22%)
    ("text", 13, "semibold"): 1,          # 0.57% mean, +1.85% worst (before +1.65%)
    ("text", 13, "bold"): 6,              # 0.59% mean, +2.16% worst (before -2.44%)
    ("text", 14, "regular"): -12,         # 0.61% mean, +2.05% worst (before +4.47%)
    ("text", 14, "medium"): -6,           # 0.58% mean, +1.88% worst (before +3.07%)
    ("text", 14, "semibold"): -4,         # 0.60% mean, +1.76% worst (before +2.53%)
    ("italic", 12, "regular"): -5,        # 0.97% mean, +3.77% worst (before +4.93%)
    ("digits", 11, "regular"): -7,        # 1.83% mean, +4.55% worst (before +6.40%)
    ("digits", 11, "medium"): 3,          # 1.70% mean, +4.51% worst (before +3.73%)
    ("digits", 12, "regular"): -12,       # 1.83% mean, +4.73% worst (before +7.67%)
    ("digits", 12, "semibold"): 7,        # 1.91% mean, +4.32% worst (before -4.00%)
    ("digits", 28, "semibold"): -33,      # 2.40% mean, +5.06% worst (before +8.56%)
    ("serif", 20, "semibold"): -16,       # 1.37% mean, +2.60% worst (before +5.05%)
    ("serif", 22, "semibold"): -20,       # 1.38% mean, +2.60% worst (before +5.39%)
}


def font_files() -> list[Path]:
    """The bundled font files, for QFontDatabase.addApplicationFont."""
    return [FONT_DIR / name for name in FONT_FILES]


def tracking(kind: str, size: int, weight: str, qt_advance: Callable[[str], float]) -> float:
    """The letter spacing, in pixels per character, for this style, from SPACING. A style the
    table lacks takes the nearest size of the same kind and weight (italic falls back to text),
    else none. qt_advance is unused here: the Mac twin measures with it."""
    for k in (kind, "text") if kind == "italic" else (kind,):
        sizes = [s for (kk, s, w) in SPACING if kk == k and w == weight]
        if sizes:
            nearest = min(sizes, key=lambda s: (abs(s - size), s))
            return SPACING[(k, nearest, weight)] / 64
    return 0.0


def text_width(text: str, style: tuple[str, int, str], qt_advance: Callable[[str], float]) -> float:
    """How wide `text` is in the text style, for sizing frames: Qt's own measure of the bundled
    font, qt_advance. The Mac twin asks AppKit instead; Windows has nothing to match but itself."""
    return qt_advance(text)


def break_lines(text: str, style: tuple[str, int, str], width: float, max_lines: int,
                qt_lines: Callable[[str, float, int], list[str]]) -> list[str]:
    """The lines a label `width` wide shows of `text`: Qt's own breaks, qt_lines. The Mac twin
    asks AppKit instead; Windows has nothing to match but itself."""
    return qt_lines(text, width, max_lines)


def truncate(text: str, style: tuple[str, int, str], width: float, qt_elide: Callable[[str, float], str]) -> str:
    """One line of `text` cut to `width` with an ellipsis: Qt's own, qt_elide."""
    return qt_elide(text, width)


def text_offsets(text: str, style: tuple[str, int, str]) -> list[float] | None:
    """None: Windows has no other interface's positions to follow, so Qt places every glyph."""
    return None


# The colours Windows draws for the Mac's named system colours: the Mac's success and error
# colours, an empty key field's hint as Qt drew it (the text colour at half alpha) and a
# disabled text button as it was drawn (the text colour at 0.30), and selected text on the text
# colour at a quarter, as the fields have always drawn it.
SYSTEM_COLORS: dict[tuple[str, bool], tuple[int, int, int, float]] = {
    ("red", False): (255, 59, 48, 1.0), ("red", True): (255, 69, 58, 1.0),
    ("green", False): (52, 199, 89, 1.0), ("green", True): (48, 209, 88, 1.0),
    ("placeholder", False): (20, 20, 20, 0.5), ("placeholder", True): (244, 239, 230, 0.5),
    ("disabled", False): (20, 20, 20, 0.3), ("disabled", True): (244, 239, 230, 0.3),
    ("selection", False): (20, 20, 20, 0.25), ("selection", True): (244, 239, 230, 0.25),
    ("selection_inactive", False): (20, 20, 20, 0.25), ("selection_inactive", True): (244, 239, 230, 0.25),
    ("selected_text", False): (20, 20, 20, 1.0), ("selected_text", True): (244, 239, 230, 1.0),
}


def system_color(name: str, dark: bool) -> tuple[int, int, int, float]:
    """The colour for one of the Mac's named system colours (see the Mac twin's names) as
    (r, g, b, alpha): fixed values, Windows has no such named colours."""
    return SYSTEM_COLORS[(name, dark)]


def short_time(when: datetime) -> str:
    """The time of day in the user's own 12 or 24 hour style: the system locale's short time
    format, which on Windows is the one set under Settings > Time & language > Region."""
    from PySide6.QtCore import QLocale, QTime

    local = when.astimezone()
    return QLocale.system().toString(QTime(local.hour, local.minute), QLocale.FormatType.ShortFormat)


SPI_GETCLIENTAREAANIMATION = 0x1042


def reduce_motion() -> bool:
    """Has the user turned animations off (Settings > Accessibility > Visual effects >
    Animation effects)?"""
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
