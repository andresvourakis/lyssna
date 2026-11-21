"""The brand colours resolve to cream and ink in the right appearance."""

import AppKit
import pytest

from lyssna.legacy.ui import theme


def resolve(color, appearance_name: str) -> str:
    """The colour as #RRGGBB@alpha, drawn in the named appearance."""
    out = []
    srgb = AppKit.NSColorSpace.sRGBColorSpace()
    appearance = AppKit.NSAppearance.appearanceNamed_(appearance_name)
    appearance.performAsCurrentDrawingAppearance_(lambda: out.append(color.colorUsingColorSpace_(srgb)))
    r, g, b, a = out[0].getRed_green_blue_alpha_(None, None, None, None)
    return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}@{a:.2f}"


LIGHT, DARK = AppKit.NSAppearanceNameAqua, AppKit.NSAppearanceNameDarkAqua


@pytest.mark.parametrize(("color", "light", "dark"), [
    (theme.SURFACE, "#F4EFE6@1.00", "#0F0F11@1.00"),
    (theme.CONTENT, "#141414@1.00", "#F4EFE6@1.00"),
    (theme.CONTENT_DIM, "#141414@0.60", "#F4EFE6@0.60"),
    (theme.HAIRLINE, "#141414@0.12", "#F4EFE6@0.12"),
    (theme.FILL_SOFT, "#141414@0.08", "#F4EFE6@0.08"),
    (theme.surface(0.97), "#F4EFE6@0.97", "#0F0F11@0.97"),
])
def test_colours_follow_the_appearance(color, light, dark):
    assert resolve(color, LIGHT) == light
    assert resolve(color, DARK) == dark
