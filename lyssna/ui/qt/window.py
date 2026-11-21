"""The base of Lyssna's ordinary titled windows on Windows (Setup, What's New), the twin of what
ui/setup_window.py and ui/history_window.py share on the Mac: EditingWindow, the surface
background, the mark and serif headline, the hairlines and the quiet text buttons.

Why a base: unlike the floating panels (panel.py), these windows take focus. They hold text
fields and buttons the keyboard reaches with Tab, they centre on the screen under the mouse a
little above the middle, and closing one only hides it, so the menu can show it again. Qt line
edits answer Ctrl+X/C/V/A/Z by themselves, so nothing like the Mac EditingWindow key handling
is needed here; Escape and Ctrl+W close the window.

Colours are theme.py's functions, read at paint time. Labels keep their colour as a function
too and take it again when light and dark mode flip. On Windows the title bar is asked to go
dark with the theme (DwmSetWindowAttribute), so it matches the surface below it."""

from __future__ import annotations

import html
import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget

from ..shared.setup import SCREEN_GAP
from ..shared.tokens import CREAM, INK
from . import coords, icons, theme
from .mark import MarkView, draw_mark
from .widgets import Color, resolve

log = logging.getLogger("lyssna.ui.qt.window")

MIN_H = 320.0       # the smallest a window gets on a short screen (the Mac setup window's floor)
LIFT = 0.08         # how far above the middle a window opens, as a share of the screen's height
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
SYMBOL_SMALLER = 2.0  # a symbol in front of a text button is this much smaller than the text (as on the Mac)
TEXT_DISABLED_ALPHA = 0.30


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
    with that fixed line height, as the Mac's paragraph styles do. The plain text is `value()`."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, size: float = 13.0, weight: str = "regular",
                 color: Color = theme.content, wrap: bool = False, line_height: float | None = None, font=None):
        super().__init__(parent)
        self.color = color
        self.line_height = line_height
        self._plain = ""
        self.setFont(font or theme.ui_font(size, weight))
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
        self.title.move(round(mw + self.mark_gap), round((row_h - th) / 2))
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
    title, in the title's colour (the heart on every support link)."""

    GAP = 4.0  # symbol to title

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

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        w = fm.horizontalAdvance(self.text()) + (self.symbol_size() + self.GAP if self.symbol else 0.0)
        return QSize(round(w + 2), round(fm.height() + 4))

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
        x = 1.0
        if self.symbol:
            s = self.symbol_size()
            icons.draw_icon(p, self.symbol, icons.icon_rect(x + s / 2, self.height() / 2, s), color, self.weight)
            x += s + self.GAP
        font = self.font()
        font.setUnderline(self.link and self.hovered)
        p.setFont(font)
        p.setPen(color)
        p.drawText(QRectF(x, 0, self.width() - x, self.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text())
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


def set_dark_title_bar(widget: QWidget, dark: bool) -> bool:
    """Ask Windows to draw this window's title bar dark (or light again). Windows 10 20H1 and
    later honour it; older ones ignore it. Does nothing elsewhere. Returns whether it took."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.dwmapi.DwmSetWindowAttribute
        fn.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        fn.restype = ctypes.c_long
        value = ctypes.c_int(1 if dark else 0)
        return fn(wintypes.HWND(int(widget.winId())), DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value),
                  ctypes.sizeof(value)) == 0
    except Exception:
        log.debug("could not set the title bar colour", exc_info=True)
        return False


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
            set_dark_title_bar(self, theme.is_dark())

    def showEvent(self, ev) -> None:
        set_dark_title_bar(self, theme.is_dark())
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
        the first time it opens, then raised and given the keyboard."""
        if not self.isVisible():
            x, y, w, h = coords.visible_frame(coords.screen_under_mouse())
            if w and h:
                left = x + (w - self.width()) / 2
                top = max(y, y + (h - self.height()) / 2 - h * LIFT)
                self.move(round(left), round(top))
        self.setVisible(True)
        self.raise_()
        self.activateWindow()

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
