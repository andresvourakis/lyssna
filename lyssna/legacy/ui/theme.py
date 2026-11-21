"""Lyssna's two brand colours, cream and ink, as dynamic NSColors.

Each colour resolves against the appearance it is drawn in, so text and button tints
follow light and dark mode on their own. Layer colours (.CGColor()) do not: resolve them
in updateLayer, as TintedView and HoverView do, so they are re-read when the appearance
flips. The accent is CONTENT itself; there is no third colour."""

from __future__ import annotations

import AppKit
from AppKit import NSColor

from ...ui.layout.tokens import CONTENT_DIM_ALPHA, CREAM, FILL_SOFT_ALPHA, HAIRLINE_ALPHA, INK, INK_TEXT


def _is_dark(appearance) -> bool:
    names = [AppKit.NSAppearanceNameAqua, AppKit.NSAppearanceNameDarkAqua]
    return appearance.bestMatchFromAppearancesWithNames_(names) == AppKit.NSAppearanceNameDarkAqua


def _srgb(rgb: tuple[int, int, int], alpha: float) -> NSColor:
    r, g, b = rgb
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, alpha)


def _dynamic(name: str, light: tuple[int, int, int], dark: tuple[int, int, int], alpha: float) -> NSColor:
    lo, hi = _srgb(light, alpha), _srgb(dark, alpha)
    return NSColor.colorWithName_dynamicProvider_(name, lambda appearance: hi if _is_dark(appearance) else lo)


def surface(alpha: float = 1.0) -> NSColor:
    """Window and card background: cream in light mode, ink in dark."""
    return _dynamic(f"lyssna.surface.{alpha:g}", CREAM, INK, alpha)


def content(alpha: float = 1.0) -> NSColor:
    """Text, icons and the accent: ink in light mode, cream in dark."""
    return _dynamic(f"lyssna.content.{alpha:g}", INK_TEXT, CREAM, alpha)


SURFACE = surface()
CONTENT = content()
CONTENT_DIM = content(CONTENT_DIM_ALPHA)  # secondary text
HAIRLINE = content(HAIRLINE_ALPHA)        # borders and separators
FILL_SOFT = content(FILL_SOFT_ALPHA)      # soft pills, keycaps, tracks
