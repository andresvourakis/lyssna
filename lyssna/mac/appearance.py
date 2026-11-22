"""The Mac's fonts, its reduced-motion setting and its time format, for the Qt interface
(lyssna/ui/theme.py, the history's times).

Why: the Mac keeps Apple's fonts and must look as the AppKit interface did. Qt reaches San
Francisco as ".AppleSystemUIFont", but AppKit draws it with Apple's size-dependent tracking
and Qt does not, so Qt's text comes out 2 to 6 percent narrower between 11 and 20 px (and
wider at 28 px). tracking() measures the AppKit advance of a reference string for each text
style once and returns the per-character difference, which theme.py hands to Qt as letter
spacing: that brings Qt within about half a percent of AppKit on whole lines. The headline
serif, New York, is not in Qt's font list on its own: font_files() names its system file so
theme.py can add it, after which Qt knows it as ".New York".

AppKit and CoreText load inside the functions, so this module imports on any system. The
Windows twin (lyssna/win/appearance.py) offers the same names."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lyssna.mac.appearance")

TEXT_FAMILY = ".AppleSystemUIFont"  # San Francisco, as Qt names the system font
SERIF_FAMILY = ".New York"  # the system serif, once font_files() is loaded

# Every letter, the digits and two spaces: the advance the correction is measured on.
REFERENCE = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789"

_tracking: dict[tuple[str, int, str], float] = {}
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
