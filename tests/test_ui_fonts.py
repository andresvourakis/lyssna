"""The interface's fonts (lyssna/ui/theme.py and the appearance twins): the bundled Windows fonts
load under the right names, each theme font function gives the expected family, size, weight
and spacing in both font sets, tabular digits are tabular, the Windows spacing table covers
every text style, and on a Mac the corrected fonts are as wide as AppKit draws them. Also: the
reduced-motion setting is read through the platform twin."""

import ast
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF

from lyssna.mac import appearance as mac_appearance
from lyssna.win import appearance as win_appearance

REPO = Path(__file__).resolve().parent.parent
FONTS = REPO / "lyssna" / "ui" / "assets" / "fonts"
on_mac = pytest.mark.skipif(sys.platform != "darwin", reason="AppKit is the reference, so this needs a Mac")


@pytest.fixture
def theme(qapp):
    from lyssna.ui import theme

    yield theme
    theme.use_fonts(None)


# -- the bundled files -------------------------------------------------------------------------


def test_the_bundled_fonts_exist_with_their_licences():
    for path in win_appearance.font_files():
        assert path.is_file() and path.parent == FONTS, path
    for licence in ("Inter-LICENSE.txt", "Literata-LICENSE.txt"):
        assert "SIL Open Font License" in (FONTS / licence).read_text(encoding="utf-8")
    assert sorted(p.name for p in FONTS.glob("*.ttf")) == sorted(win_appearance.FONT_FILES)  # nothing unused ships


def test_the_bundled_fonts_load_under_the_expected_names(qapp):
    expected = {"Inter-Regular.ttf": ("Inter", "Regular"), "Inter-Medium.ttf": ("Inter", "Medium"),
                "Inter-SemiBold.ttf": ("Inter", "SemiBold"), "Inter-Bold.ttf": ("Inter", "Bold"),
                "Inter-Italic.ttf": ("Inter", "Italic"), "Literata36pt-SemiBold.ttf": ("Literata 36pt", "SemiBold")}
    for path in win_appearance.font_files():
        font_id = QFontDatabase.addApplicationFont(str(path))
        assert font_id >= 0, path
        family, style = expected[path.name]
        assert QFontDatabase.applicationFontFamilies(font_id) == [family]
        assert style in QFontDatabase.styles(family)
    assert {win_appearance.TEXT_FAMILY, win_appearance.SERIF_FAMILY} == {"Inter", "Literata 36pt"}


# -- the theme's font functions ----------------------------------------------------------------


@pytest.mark.parametrize("fontset", ["mac", "windows"])
def test_each_font_function_gives_the_family_size_weight_and_spacing(theme, fontset):
    theme.use_fonts(fontset)
    fonts = mac_appearance if fontset == "mac" else win_appearance
    weights = {"regular": QFont.Weight.Normal, "medium": QFont.Weight.Medium, "semibold": QFont.Weight.DemiBold,
               "bold": QFont.Weight.Bold}
    for kind, size, weight in theme.TEXT_STYLES:
        font = theme._style_font(kind, size, weight)
        assert font.family() == (fonts.SERIF_FAMILY if kind == "serif" else fonts.TEXT_FAMILY), (kind, size, weight)
        assert font.pixelSize() == size and font.weight() == weights[weight]
        assert font.italic() == (kind == "italic")
        assert font.letterSpacingType() == QFont.SpacingType.AbsoluteSpacing
        if fontset == "windows":
            assert font.letterSpacing() == pytest.approx(win_appearance.SPACING[(kind, size, weight)] / 64)


@on_mac
def test_the_mac_spacing_is_the_measured_correction(theme):
    theme.use_fonts("mac")
    font = theme.ui_font(13)
    plain = QFont(font)
    plain.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
    expected = mac_appearance.tracking("text", 13, "regular", QFontMetricsF(plain).horizontalAdvance)
    assert expected < -0.1  # AppKit tracks San Francisco tighter than Qt draws it at 13 px
    assert font.letterSpacing() == pytest.approx(expected, abs=1 / 64)  # Qt keeps spacing in 64ths of a pixel


def test_the_font_functions_keep_their_defaults_and_refuse_unknown_names(theme):
    assert theme.serif_font(22).weight() == QFont.Weight.DemiBold
    assert theme.digits_font(12).weight() == QFont.Weight.Normal
    with pytest.raises(ValueError):
        theme.ui_font(13, "black")
    with pytest.raises(ValueError):
        theme.use_fonts("linux")


def test_a_font_is_a_fresh_copy(theme):
    a = theme.ui_font(13)
    a.setBold(True)
    assert theme.ui_font(13).weight() == QFont.Weight.Normal


@pytest.mark.parametrize("fontset", ["mac", "windows"])
def test_digits_are_tabular(theme, fontset):
    if fontset == "mac" and sys.platform != "darwin":
        pytest.skip("San Francisco is only on a Mac")
    theme.use_fonts(fontset)
    for size, weight in ((11, "medium"), (12, "semibold"), (28, "semibold")):
        metrics = QFontMetricsF(theme.digits_font(size, weight))
        advances = {round(metrics.horizontalAdvance(d), 3) for d in "0123456789"}
        assert len(advances) == 1, (size, weight, advances)
    proportional = QFontMetricsF(theme.ui_font(12))
    assert proportional.horizontalAdvance("1") != proportional.horizontalAdvance("0")  # without tnum they differ


# -- the Windows table covers what the app uses ------------------------------------------------


def _literal_styles() -> set[tuple[str, int, str]]:
    """Every theme font call in lyssna/ui written with literal arguments, as a text style."""
    found = set()
    kinds = {"ui_font": "text", "digits_font": "digits", "serif_font": "serif"}
    defaults = {"ui_font": "regular", "digits_font": "regular", "serif_font": "semibold"}
    for path in (REPO / "lyssna" / "ui").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in kinds):
                continue
            args = node.args
            if not args or not isinstance(args[0], ast.Constant) or not isinstance(args[0].value, int | float):
                continue
            weight = defaults[node.func.attr]
            if len(args) > 1:
                if not isinstance(args[1], ast.Constant):
                    continue
                weight = args[1].value
            italic = any(k.arg == "italic" and isinstance(k.value, ast.Constant) and k.value.value for k in node.keywords)
            found.add(("italic" if italic else kinds[node.func.attr], round(args[0].value), weight))
    return found


def test_the_windows_spacing_table_covers_every_text_style(theme):
    from lyssna.ui import theme as theme_module

    assert set(theme_module.TEXT_STYLES) <= set(win_appearance.SPACING)
    literal = _literal_styles()
    assert len(literal) >= 5, literal  # the scan itself is not silently empty
    assert literal <= set(theme_module.TEXT_STYLES), literal - set(theme_module.TEXT_STYLES)


def test_a_style_missing_from_the_table_takes_the_nearest_size():
    assert win_appearance.tracking("text", 15, "medium", len) == win_appearance.SPACING[("text", 14, "medium")] / 64
    assert win_appearance.tracking("italic", 12, "semibold", len) == win_appearance.SPACING[("text", 12, "semibold")] / 64
    assert win_appearance.tracking("serif", 22, "regular", len) == 0.0


# -- the Mac's fonts match AppKit --------------------------------------------------------------

REFERENCE_TEXT = [mac_appearance.REFERENCE, "Your text stays on this Mac. One download of about 140 MB.",
                  "Select text in any app and press"]
REFERENCE_SERIF = ["Welcome to Lyssna", "What's new in Lyssna 0.3.1"]


@on_mac
def test_san_francisco_and_new_york_are_as_wide_as_appkit_draws_them(theme):
    theme.use_fonts("mac")
    checked = 0
    for kind, size, weight in theme.TEXT_STYLES:
        appkit = mac_appearance._appkit_font(kind, size, weight)
        font = theme._style_font(kind, size, weight)
        for text in REFERENCE_SERIF if kind == "serif" else REFERENCE_TEXT:
            want = mac_appearance._appkit_advance(text, appkit)
            got = QFontMetricsF(font).horizontalAdvance(text)
            assert abs(got - want) / want < 0.01, (kind, size, weight, text, got, want)
            checked += 1
    assert checked > 40


# -- text widths for sizing ------------------------------------------------------------------


@pytest.mark.parametrize("fontset", ["mac", "windows"])
def test_every_text_style_is_read_back_from_its_font(theme, fontset):
    theme.use_fonts(fontset)
    for style in theme.TEXT_STYLES:
        assert theme.text_style(theme._style_font(*style)) == style
    with pytest.raises(ValueError):
        theme.text_style(QFont("Arial", 13))  # an ad hoc font is never measured
    with pytest.raises(ValueError):
        theme.text_width("Save", theme.ui_font(15))  # a size the app does not use


def test_windows_sizes_with_qts_own_measure(theme):
    theme.use_fonts("windows")
    font = theme.ui_font(13, "medium")
    assert theme.text_width("Transcript cancelled", font) == QFontMetricsF(font).horizontalAdvance("Transcript cancelled")


@on_mac
def test_the_mac_sizes_with_appkits_own_measure(theme):
    import AppKit

    theme.use_fonts("mac")
    for text, (kind, size, weight) in (("Transcript cancelled", ("text", 13, "medium")), ("1,204", ("digits", 28, "semibold")),
                                       ("Welcome to Lyssna", ("serif", 20, "semibold")), ("heard as: matias", ("italic", 12, "regular"))):
        font = mac_appearance._appkit_font(kind, size, weight)
        want = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, {AppKit.NSFontAttributeName: font}).size().width
        assert theme.text_width(text, theme._style_font(kind, size, weight)) == pytest.approx(want, abs=1e-6), text
    assert ("Transcript cancelled", ("text", 13, "medium")) in mac_appearance._widths  # remembered


def test_the_mac_width_falls_back_to_qt_without_appkit(monkeypatch):
    def fail(*_args):
        raise ImportError("no AppKit here")

    monkeypatch.setattr(mac_appearance, "_appkit_font", fail)
    assert mac_appearance.text_width("never measured before", ("text", 13, "regular"), lambda t: 42.0) == 42.0


@on_mac
def test_new_york_is_loaded_from_the_system(theme):
    theme.use_fonts("mac")
    files = mac_appearance.font_files()
    assert files and files[0].is_file()
    from PySide6.QtGui import QRawFont

    assert QRawFont.fromFont(theme.serif_font(22)).familyName() == ".New York"


# -- reduced motion --------------------------------------------------------------------------


def test_reduce_motion_goes_through_the_twin(theme, monkeypatch):
    from lyssna import host

    for value in (True, False):
        monkeypatch.setattr(host.appearance, "reduce_motion", lambda value=value: value)
        assert theme.reduce_motion() is value


def test_the_windows_animation_setting_is_off_safely_elsewhere():
    if sys.platform != "win32":
        assert win_appearance.reduce_motion() is False  # no user32: the call fails and says "animate"
