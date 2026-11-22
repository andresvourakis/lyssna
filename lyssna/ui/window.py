"""The base of Lyssna's ordinary titled windows on Windows (Setup, What's New), the twin of what
legacy/ui/setup_window.py and legacy/ui/history_window.py share on the Mac: EditingWindow, the surface
background, the mark and serif headline, the hairlines and the quiet text buttons.

Why a base: unlike the floating panels (panel.py), these windows take focus. They hold text
fields and buttons the keyboard reaches with Tab, they centre on the screen under the mouse a
little above the middle, and closing one only hides it, so the menu can show it again. Qt line
edits answer Ctrl+X/C/V/A/Z by themselves, so nothing like the Mac EditingWindow key handling
is needed here; Escape and Ctrl+W close the window.

Colours are theme.py's functions, read at paint time. Labels keep their colour as a function
too and take it again when light and dark mode flip. The title bar is asked to go dark with the
theme through the platform twin (host.window.set_dark_title_bar: DwmSetWindowAttribute on
Windows; the Mac does it by itself), so it matches the surface below it."""

from __future__ import annotations

import html
import logging
import math
from collections.abc import Callable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QFontMetricsF, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget

from .. import host
from . import coords, icons, theme
from .layout.setup import SCREEN_GAP
from .layout.tokens import CREAM, INK
from .mark import MarkView, draw_mark
from .widgets import Color, resolve

log = logging.getLogger("lyssna.ui.window")

MIN_H = 320.0       # the smallest a window gets on a short screen (the Mac setup window's floor)
LIFT = 0.08         # how far above the middle a window opens, as a share of the screen's height
SYMBOL_SMALLER = 2.0  # a symbol in front of a text button is this much smaller than the text (as on the Mac)
TEXT_DISABLED_ALPHA = 0.30
# An AppKit label's cell draws its text this far in from each side of its frame, so a label sized
# to its text is the text's advance plus twice this, and a wrapping label breaks its lines this far
# inside its width. Qt labels here keep the same inset, so frames, positions and every "does it
# fit" decision come out as the Mac's (the rule is frame arithmetic, so it holds with any font).
TEXT_INSET = 2.0


def text_frame_width(advance: float) -> int:
    """The width of a label frame around text `advance` wide (theme.text_width, AppKit's own
    measure on the Mac): what AppKit's sizeToFit gives, on whole pixels (AppKit keeps half
    points on a Retina screen, Qt whole pixels)."""
    return math.ceil(round(advance + 2 * TEXT_INSET, 3))


def half_point_width(advance: float) -> float:
    """The same frame as AppKit really sizes it on a Retina screen: rounded up to half points,
    not whole ones. Only a widget that paints its own content can use it (painting at the half
    point inside a whole-pixel frame); child widgets keep text_frame_width."""
    return math.ceil(round(2 * (advance + 2 * TEXT_INSET), 3)) / 2


def ink_left(font, text: str) -> float:
    """How far right of its frame a label's first glyph starts: the text inset plus the glyph's
    side bearing (larger at bigger sizes). Used to line labels up by their ink, as the Mac does."""
    if not text:
        return TEXT_INSET
    return TEXT_INSET + QFontMetricsF(font).tightBoundingRect(text[0]).left()


# -- the two status colours --------------------------------------------------------------------
#
# The Mac uses systemGreen and systemRed for success and error, which shift a little in dark
# mode. Windows has no such named colours, so these are the Mac's values.


def good() -> QColor:
    """Success: the Mac's systemGreen."""
    return QColor(48, 209, 88) if theme.is_dark() else QColor(52, 199, 89)


def bad() -> QColor:
    """Errors: the Mac's systemRed."""
    return QColor(255, 69, 58) if theme.is_dark() else QColor(255, 59, 48)


def css(color: QColor) -> str:
    """A QColor as a style sheet rgba()."""
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


# -- small views -------------------------------------------------------------------------------


class Label(QLabel):
    """A label in the brand colours. `color` is a colour or a colour function, taken again on
    restyle() (the window calls it when the scheme flips). With `line_height` the text is set
    with that fixed line height, as the Mac's paragraph styles do. The plain text is `value()`.
    Like an AppKit label it keeps its text TEXT_INSET in from each side of its frame."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, size: float = 13.0, weight: str = "regular",
                 color: Color = theme.content, wrap: bool = False, line_height: float | None = None, font=None):
        super().__init__(parent)
        self.color = color
        self.line_height = line_height
        self._plain = ""
        self.setFont(font or theme.ui_font(size, weight))
        inset = round(TEXT_INSET)
        self.setContentsMargins(inset, 0, inset, 0)
        self.setWordWrap(wrap)
        self.setTextFormat(Qt.TextFormat.RichText if line_height else Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.set_text(text)
        self.restyle()

    def set_text(self, text: str) -> None:
        self._plain = text
        if self.line_height:
            super().setText(f'<div style="line-height:{round(self.line_height)}px">{html.escape(text)}</div>')
        else:
            super().setText(text)

    def value(self) -> str:
        return self._plain

    def set_color(self, color: Color) -> None:
        self.color = color
        self.restyle()

    def restyle(self) -> None:
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.WindowText, resolve(self.color) or theme.content())
        self.setPalette(pal)

    def sizeHint(self) -> QSize:
        """One plain line: the AppKit label frame, the advance plus the insets, one line tall.
        Wrapping and fixed line height labels keep Qt's own measure (margins included)."""
        if self.wordWrap() or self.line_height:
            return super().sizeHint()
        return QSize(text_frame_width(theme.text_width(self._plain, self.font())), QFontMetrics(self.font()).height())

    def exact_width(self) -> float:
        """One plain line's frame width as AppKit has it, on half points (half_point_width): where
        whatever follows on the line starts on the Mac."""
        return half_point_width(theme.text_width(self._plain, self.font()))

    def fit(self, width: float | None = None) -> tuple[int, int]:
        """Size the label to its text (wrapped to `width` when it wraps); returns the size."""
        if width is not None and self.wordWrap():
            w = round(width)
            self.resize(w, self.heightForWidth(w) if self._plain else 0)
        else:
            self.resize(self.sizeHint())
        return self.width(), self.height()


class Hairline(QWidget):
    """A 1 px line in the hairline colour: separators, and the edges of a fixed header and footer."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.resize(10, 1)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.hairline())
        p.end()


class Header(QWidget):
    """A window's headline: the mark and a serif headline on one line, and an optional dim
    sentence under them, all left-aligned (the What's New and History look)."""

    def __init__(self, title: str, parent: QWidget | None = None, *, mark_h: float = 28.0, title_size: float = 22.0,
                 mark_gap: float = 10.0, subtitle: str = "", subtitle_size: float = 13.0, subtitle_gap: float = 8.0):
        super().__init__(parent)
        self.mark_gap = mark_gap
        self.subtitle_gap = subtitle_gap
        self.mark = MarkView(mark_h, self)
        self.title = Label(title, self, font=theme.serif_font(title_size))
        self.subtitle = Label(subtitle, self, size=subtitle_size, color=theme.content_dim, wrap=True)
        self.subtitle.setVisible(bool(subtitle))

    def fit(self, width: float) -> float:
        """Lay out for `width`; returns the height."""
        mw, mh = self.mark.width(), self.mark.height()
        _, th = self.title.fit()
        row_h = max(mh, th)
        self.mark.move(0, round((row_h - mh) / 2))
        self.title.move(round(mw + self.mark_gap - TEXT_INSET), round((row_h - th) / 2))  # its text mark_gap from the mark
        h = float(row_h)
        if self.subtitle.value():
            _, sh = self.subtitle.fit(width)
            self.subtitle.move(0, round(h + self.subtitle_gap))
            h += self.subtitle_gap + sh
        self.resize(round(width), round(h))
        return h


class TextButton(QAbstractButton):
    """A quiet text button, the twin of HistoryTextButton: dim, full colour under the cursor, no
    pill and no outline. With `link` it reads as a link inside a dim sentence instead: full
    colour at rest and underlined under the cursor. `symbol` puts an icon in front of the
    title, in the title's colour (the heart on every support link).

    Sized as AppKit sizes a borderless button: the title alone is a label frame (the advance
    plus TEXT_INSET each side, the title centred in it); with a symbol it is PAD, the symbol's
    image (two wider than the symbol), PAD, the title on whole pixels, and PAD again."""

    PAD = 2.0  # the cell's padding around the symbol's image and after the title

    def __init__(self, title: str, parent: QWidget | None = None, *, size: float = 12.0, weight: str = "medium",
                 link: bool = False, symbol: str = "", clicked: Callable[[], None] | None = None):
        super().__init__(parent)
        self.setText(title)
        self.setFont(theme.ui_font(size, weight))
        self.text_size = size
        self.weight = weight
        self.link = link
        self.symbol = symbol
        self.hovered = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        if clicked is not None:
            self.clicked.connect(lambda _checked=False: clicked())
        self.resize(self.sizeHint())

    def symbol_size(self) -> float:
        return self.text_size - SYMBOL_SMALLER

    def image_width(self) -> float:
        """The symbol's image: an SF Symbol's image is about two wider than its point size."""
        return self.symbol_size() + 2.0

    def title_x(self) -> float:
        """Where the title's box starts: after the symbol's image, or at the left edge."""
        return self.PAD + self.image_width() + self.PAD if self.symbol else 0.0

    def sizeHint(self) -> QSize:
        advance = theme.text_width(self.text(), self.font())
        if self.symbol:
            w = self.title_x() + math.ceil(round(advance, 3)) + self.PAD
        else:
            w = text_frame_width(advance)
        return QSize(round(w), QFontMetrics(self.font()).height())

    def color(self) -> QColor:
        if not self.isEnabled():
            return theme.content(TEXT_DISABLED_ALPHA)
        return theme.content() if self.hovered or self.link else theme.content_dim()

    def enterEvent(self, ev) -> None:
        self.hovered = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self.hovered = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = self.color()
        x = self.title_x()
        if self.symbol:
            s = self.symbol_size()
            icons.draw_icon(p, self.symbol, icons.icon_rect(self.PAD + self.image_width() / 2, self.height() / 2, s), color,
                            self.weight)
        font = self.font()
        font.setUnderline(self.link and self.hovered)
        p.setFont(font)
        p.setPen(color)
        p.drawText(QRectF(x, 0, self.width() - x - (self.PAD if self.symbol else 0.0), self.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter), self.text())
        if self.hasFocus():
            p.setPen(theme.content(0.45))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        p.end()


# -- the window --------------------------------------------------------------------------------


def app_icon() -> QIcon:
    """Lyssna's icon for title bars and the taskbar: the cream mark on the ink tile."""
    icon = QIcon()
    for side in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        pm = QPixmap(side, side)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.rgba(INK))
        r = side * 0.22
        p.drawRoundedRect(QRectF(0, 0, side, side), r, r)
        inset = side * 0.22
        draw_mark(p, QRectF(inset, inset, side - 2 * inset, side - 2 * inset), theme.rgba(CREAM))
        p.end()
        icon.addPixmap(pm)
    return icon


class LyssnaWindow(QWidget):
    """A normal titled window with a close button only, on the brand surface. Subclasses lay
    out their own children and call fit_height() with the height they need; restyle() is
    their hook for anything that does not repaint by itself when the scheme flips (style
    sheets)."""

    def __init__(self, title: str, parent: QWidget | None = None):
        flags = (Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
                 | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowCloseButtonHint)
        super().__init__(parent, flags)
        self.setWindowTitle(title)
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)  # kept and shown again from the menu
        unsubscribe = theme.on_scheme_change(self._scheme_changed)
        self.destroyed.connect(lambda *_: unsubscribe())

    # -- look --------------------------------------------------------------------------------

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.surface())
        p.end()

    def restyle(self) -> None:
        """Override: re-apply anything that holds colours (style sheets). Labels are done."""

    def _scheme_changed(self) -> None:
        for label in self.findChildren(Label):
            label.restyle()
        self.restyle()
        for child in self.findChildren(QWidget):
            child.update()
        self.update()
        if self.isVisible():
            host.window.set_dark_title_bar(self, theme.is_dark())

    def showEvent(self, ev) -> None:
        host.window.set_dark_title_bar(self, theme.is_dark())
        super().showEvent(ev)

    # -- size and place ----------------------------------------------------------------------

    def height_limit(self) -> float:
        """The tallest this window may be: its screen's usable height less SCREEN_GAP above and below."""
        screen = self.screen() if self.isVisible() else coords.screen_under_mouse()
        if screen is None:
            return float("inf")
        return coords.visible_frame(screen)[3] - 2 * SCREEN_GAP

    def fit_height(self, width: float, wanted: float, floor: float = MIN_H) -> int:
        """Make the window `width` wide and `wanted` tall, but no taller than the screen allows
        (and never shorter than `floor` for that). The top edge stays put. Returns the height."""
        h = round(min(wanted, max(self.height_limit(), floor)))
        self.setFixedSize(round(width), h)
        return h

    def show_centered(self) -> None:
        """Bring the window up: centred on the screen under the mouse a little above the middle
        the first time it opens, then raised and given the keyboard, with the app brought to
        the front (on a Mac an app with no Dock icon does not come forward by itself)."""
        if not self.isVisible():
            x, y, w, h = coords.visible_frame(coords.screen_under_mouse())
            if w and h:
                left = x + (w - self.width()) / 2
                top = max(y, y + (h - self.height()) / 2 - h * LIFT)
                self.move(round(left), round(top))
        self.setVisible(True)
        self.raise_()
        self.activateWindow()
        host.window.activate_app()

    # -- closing -----------------------------------------------------------------------------

    def closeEvent(self, ev) -> None:
        """Closing hides the window (it is shown again from the menu) and never quits the app."""
        ev.ignore()
        self.hide()

    def keyPressEvent(self, ev) -> None:
        ctrl = ev.modifiers() & Qt.KeyboardModifier.ControlModifier
        if ev.key() == Qt.Key.Key_Escape or (ctrl and ev.key() == Qt.Key.Key_W):
            self.close()
            ev.accept()
            return
        super().keyPressEvent(ev)
