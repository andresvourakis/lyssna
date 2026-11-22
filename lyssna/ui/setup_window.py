"""The setup window, the twin of legacy/ui/setup_window.py (which is its specification): shown on
first launch and from the menu (Setup…). Like the menu it has two sections, Listen and Dictate,
behind a switch in the header:

    Listen   1  Permission   live Accessibility status, Open Settings, Relaunch (where needed)
             2  Voice        the free voice on this computer (download, progress, ready)
                             or the user's own API key (engine, Get a key, Save)
             3  Try it       the speak shortcut and a Read a Sample button
    Dictate  1  Permission   microphone access and, on a Globe key, its system setting
             2  Engine       the speech models on this computer (download, progress, ready)
                             or the user's own OpenAI and Anthropic keys (Get a key, Save)
             3  Try it       the dictation keys (and the double tap, where it is on)

Why one window and not alerts: every step shows its state, errors appear inline, and Listen's
Voice and Try it work before the permission is granted (the sample goes through speak_text,
not the selection).

Where the two systems differ, the window asks a capability in lyssna.host, never the system:
  * host.NEEDS_ACCESSIBILITY: the Mac's Permission step heads Listen. Without it (Windows)
    Listen has two steps, Voice and Try it, and when the keyboard shortcuts could not start,
    one warning row with Relaunch Lyssna sits above them instead.
  * host.HAS_GLOBE_KEY: Dictate's Permission step also checks the Globe key setting while
    dictation is on fn.
  * host.DEVICE names the computer ("Mac", "PC"), and key caps take the system's own key
    names from host.hotkey_table ([⌃] [⌥] [S] on a Mac, [Ctrl] [Alt] [S] on a PC).

It is a regular titled window: it takes the keyboard (the key fields), centres on the screen
under the mouse and closing it never quits the app. The header (mark, title, switch) and the
footer (status and Done) stay put; only the steps between them scroll when they are taller
than the screen allows. Saving a key makes one request to the service, so it runs on a worker
thread and reports back on the GUI thread; the Globe key setting, which takes a `defaults`
call to read, is read on a worker thread at most every GLOBE_POLL seconds. Nothing here waits.

Controls are drawn here rather than left to the system style, whose versions take the blue
accent colour: the switch, the radios, the option cards, the engine dropdown, the key fields,
the step icons and the key caps. All colours are read at paint time, so the window follows
light and dark mode."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from types import ModuleType

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QComboBox, QFrame, QLineEdit, QRadioButton, QScrollArea, QWidget

from .. import config, host
from ..dictation import cleaners, transcribers
from ..hotkeys import tokens
from ..menu import ONLINE_HEADER, privacy_symbol
from ..providers import REGISTRY, allowed, get_provider, kokoro
from . import icons, theme
from .layout.setup import (
    CARD_PAD,
    CARD_R,
    CARD_X,
    DICTATE,
    DICTATION_KEY_ENGINES,
    DICTATION_KEYS,
    FIELD_H,
    FOOTER_H,
    GLOBE_POLL,
    ICON,
    INDENT,
    LISTEN,
    MARGIN,
    MARK_H,
    PILL_H,
    PILL_PAD,
    POLL,
    RADIO,
    SAMPLE,
    SEG_H,
    SEG_PAD,
    SUB_W,
    SUB_X,
    TEXT_W,
    TEXT_X,
    W,
    _size_text,
    footer_status,
    opening_tab,
    steps_left,
    tab_status,
)
from .mainthread import on_main
from .mark import MarkView
from .widgets import BAR_H, DISABLED_TITLE_ALPHA, PillButton, ProgressBar, rounded
from .window import (
    TEXT_INSET,
    Hairline,
    Label,
    LyssnaWindow,
    bad,
    css,
    good,
    half_point_width,
    place,
    selection_style,
    text_frame_width,
)

log = logging.getLogger("lyssna.ui.setup")

TITLE_SIZE = 20.0    # the serif headline
HEADER_TOP = 8.0     # the title bar to the mark
STEPS_TOP = 20.0     # the header to the first step
CARD_RADIUS = 10.0
FIELD_RADIUS = 6.0
SEG_RADIUS = 7.0     # a chosen segment of the switch
TRACK_RADIUS = 9.0   # the switch's track
MIN_BUTTON_W = 84.0  # Done, Read a Sample, Save, Download, Allow, Relaunch...: never narrower than this
TONES = {"good": good, "bad": bad}  # an inline note's colour; anything else is dim
GLOBE_KEY = "fn"     # the dictation key that doubles as the Globe key, where the computer has one


def card_border_on() -> QColor:
    """The chosen option card's border."""
    return theme.content(0.45)


# -- key names ---------------------------------------------------------------------------------


def key_caps(spec: str, table: ModuleType | None = None) -> list[str]:
    """One cap per key of a shortcut, named by the system's key table (host.hotkey_table, the
    one lyssna.hotkeys.pretty reads): "ctrl+alt+s" is [⌃] [⌥] [S] on a Mac, [Ctrl] [Alt] [S] on a PC."""
    table = table or host.hotkey_table
    return [table.pretty([part]) for part in tokens(spec)]


def dictation_caps(hotkey: str, table: ModuleType | None = None) -> list[str]:
    """The dictation key's caps: its name from the table's DICTATION_KEY_NAMES (what
    menu.key_name shows: "fn", "right ⌥", "right Alt"), or one cap per key for a chord."""
    table = table or host.hotkey_table
    names = table.DICTATION_KEY_NAMES
    return [names[hotkey]] if hotkey in names else key_caps(hotkey, table)


# -- controls ----------------------------------------------------------------------------------


class Radio(QRadioButton):
    """A radio in the brand colours: content with a surface dot when chosen, a soft ring when
    not. A real QRadioButton for the keyboard and accessibility. Sized as AppKit sizes the
    Mac's radio: its INDENT wide image, TITLE_GAP, then the title as a label frame (the advance
    plus TEXT_INSET each side), RADIO tall. The title is drawn at the start of its box, where
    the Mac's button cell draws it."""

    TITLE_GAP = 2.0  # the image to the title's box

    def __init__(self, title: str, parent: QWidget):
        super().__init__(title, parent)
        self.setFont(theme.ui_font(13))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.resize(self.sizeHint())

    def sizeHint(self) -> QSize:
        advance = theme.text_width(self.text(), self.font())
        return QSize(round(INDENT + self.TITLE_GAP) + text_frame_width(advance), round(RADIO))

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        circle = QRectF(0, (self.height() - RADIO) / 2, RADIO, RADIO)
        ring = circle.adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.content())
            p.drawEllipse(ring)
            p.setBrush(theme.surface())
            p.drawEllipse(circle.adjusted(5, 5, -5, -5))
        else:
            p.setPen(QPen(theme.content(0.35), 1.0))
            p.setBrush(theme.fill_soft())
            p.drawEllipse(ring)
        if self.hasFocus():
            p.setPen(QPen(theme.content(0.45), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle.adjusted(-2, -2, 2, 2))
        p.setPen(theme.content())
        x = INDENT + self.TITLE_GAP
        theme.draw_text(p, QRectF(x, 0, self.width() - x, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text(), self.font())
        p.end()


class TabButton(QRadioButton):
    """One segment of the Listen / Dictate switch: the tab's name and a small status, on a
    content fill with surface text when chosen, bare dim text when not. A real radio button
    underneath (the two sit in one exclusive group, so arrow keys move between them and a
    screen reader reads a radio group); the whole segment takes the click."""

    def __init__(self, name: str, parent: QWidget):
        super().__init__(name, parent)
        self.name = name
        self.status = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setAccessibleName(name)

    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.setAccessibleName(f"{self.name}, {status}" if status else self.name)
            self.update()

    def _parts(self) -> tuple[str, str]:
        return self.name, f"  {self.status}" if self.status else ""

    def preferred_width(self) -> float:
        name, status = self._parts()
        w = theme.text_width(name, theme.ui_font(13, "medium"))
        w += theme.text_width(status, theme.ui_font(11))
        return max(150.0, round(w) + 40.0)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        on = self.isChecked()
        path = rounded(QRectF(self.rect()), SEG_RADIUS)
        if on:
            p.fillPath(path, theme.content())
        if self.hasFocus():
            p.setPen(QPen(theme.content(0.45), 2.0))
            p.drawPath(rounded(QRectF(self.rect()).adjusted(1, 1, -1, -1), SEG_RADIUS - 1))
        name, status = self._parts()
        big, small = theme.ui_font(13, "medium"), theme.ui_font(11)
        fb = QFontMetricsF(big)
        nw = theme.text_width(name, big)  # each run as wide as AppKit drew it
        sw = theme.text_width(status, small) if status else 0.0
        x = (self.width() - nw - sw) / 2
        base = (self.height() - fb.height()) / 2 + fb.ascent()  # one line: both runs share a baseline
        p.setPen(theme.surface() if on else theme.content_dim())
        theme.draw_text_at(p, x, base, name, big)
        if status:
            p.setPen(theme.surface(0.75) if on else theme.content(0.45))
            theme.draw_text_at(p, x + nw, base, status, small)
        p.end()


class SwitchTrack(QWidget):
    """The switch's soft rounded track behind its two segments."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAccessibleName("Setup section")

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillPath(rounded(QRectF(self.rect()), TRACK_RADIUS), theme.fill_soft())
        p.end()


class OptionCard(QWidget):
    """An option card: a rounded hairline border around one choice, its note and its controls.
    The chosen one's border is stronger. It sits behind the controls laid over it."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.chosen = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_chosen(self, on: bool) -> None:
        self.chosen = bool(on)
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(card_border_on() if self.chosen else theme.hairline(), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rounded(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), CARD_RADIUS - 0.5))
        p.end()


class Tag(QWidget):
    """A card's small dim privacy tag: an icon and a few words, e.g. a lock and "Free and
    private". Squeezed narrower than its words, the words end in an ellipsis. The words are a
    label frame TEXT_X in (the advance plus TEXT_INSET each side), as on the Mac."""

    H = 16
    TEXT_X = 16  # the icon's box and its gap

    def __init__(self, text: str, symbol: str, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.text = ""
        self.symbol = symbol
        self.set(text, symbol)

    def exact_width(self) -> float:
        """The Mac's width, on half points: its label frame is sized to half points (half_point_width)."""
        return self.TEXT_X + half_point_width(theme.text_width(self.text, theme.ui_font(11)))

    def natural_width(self) -> int:
        return math.ceil(self.exact_width())

    def set(self, text: str, symbol: str) -> None:
        self.text, self.symbol = text, symbol
        self.resize(self.natural_width(), self.H)
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = theme.content_dim()
        # The tag sits against the card's right edge, so its whole-pixel frame starts up to half a
        # point left of the Mac's: draw from where the Mac's half-point frame starts.
        exact = self.exact_width()
        if self.width() >= exact:
            p.translate(self.width() - exact, 0)
        # The Mac's 10 pt symbol, centred in its 12 x 12 image view at (0, 2).
        icons.draw_icon(p, self.symbol, icons.icon_rect(6, 8, 10), color, "medium")
        font = theme.ui_font(11)
        p.setPen(color)
        x = self.TEXT_X + TEXT_INSET
        room = max(0.0, min(float(self.width()), exact) - x - TEXT_INSET)
        words = theme.elide(self.text, font, room)  # cut only where the Mac's label would cut it
        theme.draw_text(p, QRectF(x, 0, room + TEXT_INSET, self.H), Qt.AlignmentFlag.AlignVCenter, words, font, words != self.text)
        p.end()


def _draw_check_disc(p: QPainter, rect: QRectF, color: QColor) -> None:
    """A filled disc with a check in white: the twin of checkmark.circle.fill."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(rect)
    s = rect.width() * 0.52  # the 13 pt checkmark.circle.fill's check: 6 wide in its 13 disc
    icons.draw_icon(p, "checkmark", icons.icon_rect(rect.center().x(), rect.center().y(), s), QColor(255, 255, 255), "bold")


def _draw_pending(p: QPainter, rect: QRectF, color: QColor) -> None:
    """A ring with an exclamation mark: the twin of exclamationmark.circle, beside a dictation
    check that still needs doing (a plain ring would read as a radio)."""
    w = rect.width() * 0.09
    p.setPen(QPen(color, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(rect.adjusted(w / 2, w / 2, -w / 2, -w / 2))
    cx, top, h = rect.center().x(), rect.top(), rect.height()
    pen = QPen(color, w * 1.1)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(cx, top + h * 0.27), QPointF(cx, top + h * 0.56))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx, top + h * 0.72), w * 0.75, w * 0.75)


class StepIcon(QWidget):
    """A step's number in a ring, or a green check once the step is done."""

    def __init__(self, number: int, parent: QWidget):
        super().__init__(parent)
        self.number = number
        self.done = False
        self.setFixedSize(round(ICON), round(ICON))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.paint_offset = QPointF(0.0, 0.0)  # see window.place()

    def set_paint_offset(self, dx: float, dy: float = 0.0) -> None:
        self.paint_offset = QPointF(dx, dy)
        self.update()

    def set_done(self, done: bool) -> None:
        if done != self.done:
            self.done = done
            self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(self.paint_offset)
        # The Mac's 18 pt checkmark.circle.fill or numbered circle fills its 22 x 22 image view;
        # where there is no such symbol (Windows) the disc and ring are drawn.
        centre = QPointF(self.width() / 2, self.height() / 2)
        if self.done:
            symbol, tint = "checkmark.circle.fill", good()
        else:
            symbol, tint = f"{self.number}.circle", theme.content_dim()
        if icons.draw_symbol(p, symbol, centre, 18, tint, "medium"):
            p.end()
            return
        d = 19.0
        circle = QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d)
        if self.done:
            _draw_check_disc(p, circle, good())
        else:
            p.setPen(QPen(theme.content_dim(), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle.adjusted(0.75, 0.75, -0.75, -0.75))
            theme.draw_text(p, circle, Qt.AlignmentFlag.AlignCenter, str(self.number), theme.ui_font(11, "semibold"))
        p.end()


class StatusRow(QWidget):
    """A small icon and a few words: a green check and "Ready", or the dim pending mark and a
    check that still needs doing."""

    def __init__(self, text: str, parent: QWidget, *, done: bool = True):
        super().__init__(parent)
        self.done = done
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label = Label(text, self, size=13, weight="medium")
        self.set(text, done)

    def set(self, text: str, done: bool) -> None:
        self.done = done
        self.label.set_text(text)
        w, h = self.label.fit()
        self.label.move(20, round((18 - h) / 2))
        self.resize(20 + w, 18)
        self.update()

    def set_text(self, text: str) -> None:
        self.set(text, self.done)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(1.5, 2.5, 13, 13)  # the Mac's 13 pt symbol, centred in its 16 x 16 image view at (0, 1)
        symbol, tint = ("checkmark.circle.fill", good()) if self.done else ("exclamationmark.circle", theme.content_dim())
        if icons.draw_symbol(p, symbol, rect.center(), 13, tint, "medium"):
            p.end()
            return
        if self.done:
            _draw_check_disc(p, rect, good())
        else:
            _draw_pending(p, rect, theme.content_dim())
        p.end()


class KeyCaps(QWidget):
    """A row of key caps, one per label, e.g. [⌃] [⌥] [S] or [Ctrl] [Alt] [S]. The caps are the
    Mac's, on half points (their labels' frames are), and the row is drawn `offset` in from the
    frame's left edge: the Mac's row often starts on a half point, which a widget cannot."""

    H, GAP, MIN_W = 24, 4, 24

    def __init__(self, labels: list[str], parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.labels: list[str] = []
        self.offset = 0.0
        self.set_labels(labels)

    def _cap_widths(self) -> list[float]:
        """Each cap: its label's frame (the advance plus the insets, on half points, as on the Mac) and 12, or MIN_W."""
        font = theme.ui_font(13, "medium")
        return [max(float(self.MIN_W), half_point_width(theme.text_width(t, font)) + 12.0) for t in self.labels]

    def exact_width(self) -> float:
        widths = self._cap_widths()
        return sum(widths) + self.GAP * max(0, len(widths) - 1)

    def set_offset(self, offset: float) -> None:
        """Draw the row `offset` in from the left edge (under a pixel), the frame wide enough for it."""
        self.offset = offset
        self.resize(math.ceil(round(self.exact_width() + offset, 3)), self.H)
        self.update()

    def set_labels(self, labels: list[str]) -> None:
        self.labels = list(labels)
        self.set_offset(self.offset)
        self.setAccessibleName(" ".join(self.labels))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = theme.ui_font(13, "medium")
        x = self.offset
        for text, w in zip(self.labels, self._cap_widths(), strict=True):
            r = QRectF(x, 0, w, self.H)
            path = rounded(r.adjusted(0.5, 0.5, -0.5, -0.5), 6.0)
            p.fillPath(path, theme.fill_soft())
            p.setPen(QPen(theme.hairline(), 1.0))
            p.drawPath(path)
            p.setPen(theme.content())
            theme.draw_text(p, r, Qt.AlignmentFlag.AlignCenter, text, font)
            x += w + self.GAP
        p.end()


class IconPillButton(PillButton):
    """A secondary pill with a small icon after its title: "Get a key" and its arrow. Sized as
    the Mac's: PILL_PAD, the title as a label frame (the advance plus TEXT_INSET each side), the
    icon's IMAGE_W wide image, PILL_PAD. The icon starts where the title's frame ends."""

    ICON_SIZE, IMAGE_W = 10.0, 12.0

    def __init__(self, title: str, icon: str, parent: QWidget, *, clicked: Callable[[], None] | None = None):
        self.icon_name = icon
        super().__init__(title, parent, clicked=clicked, focusable=True)

    def title_width(self) -> int:
        return text_frame_width(theme.text_width(self.text(), self.font()))

    def sizeHint(self) -> QSize:
        return QSize(round(2 * PILL_PAD + self.IMAGE_W) + self.title_width(), round(PILL_H))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded(r, r.height() / 2)
        p.fillPath(path, theme.fill_soft())
        p.setPen(QPen(theme.hairline(), 1.0))
        p.drawPath(path)
        if self.isDown():
            p.fillPath(path, theme.content(0.08))
        if self.hasFocus():
            ring = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            p.setPen(QPen(theme.content(0.45), 2.0))
            p.drawPath(rounded(ring, ring.height() / 2))
        x = (self.width() - self.title_width() - self.IMAGE_W) / 2  # PILL_PAD at the natural width
        tw = self.title_width()
        title = theme.content(1.0 if self.isEnabled() else DISABLED_TITLE_ALPHA)  # a disabled Mac pill dims its title only
        p.setPen(title)
        theme.draw_text(p, QRectF(x + TEXT_INSET, 0, tw, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text(), self.font())
        icons.draw_icon(p, self.icon_name, icons.icon_rect(x + tw + self.ICON_SIZE / 2, self.height() / 2, self.ICON_SIZE),
                        title, "semibold")
        p.end()


class Dropdown(QComboBox):
    """The engine picker, drawn as the Mac's pop-up button (NSPopUpButton, regular size): a
    filled rounded bezel with a fine shadow, the choice in the system's text colour and the
    up and down chevrons at the right. Its frame is the Mac's too, bezel insets included:
    25 tall, as wide as the widest item plus EXTRA, with the bezel BEZEL_L in from the left,
    BEZEL_T from the top and 20 tall. Measured on the legacy window's picture. Its list is
    styled to match the window (restyle() after a scheme change)."""

    H = 25
    EXTRA = 67           # the frame beyond the widest item's advance (whole points), as AppKit's sizeToFit
    BEZEL_L, BEZEL_R, BEZEL_T, BEZEL_H = 3.0, 4.0, 1.0, 20.0
    RADIUS = 5.0
    TITLE_X, TITLE_Y = 11.0, 2.0     # the title's box in the frame; the text sits at its top
    ARROWS_FROM_RIGHT = 13.75        # the chevrons' centre, from the frame's right edge
    ARROW_HALF_W, ARROW_NEAR, ARROW_FAR = 3.0, 2.0, 4.5  # each chevron: half width, and its ends from the bezel's middle
    ARROW_PEN = 1.5
    # Disabled, the Mac's pop-up button keeps a faint bezel and dims its title and chevrons far
    # more than its bezel: (bezel and shadow strength, title alpha, chevron alpha) in light and
    # dark mode, measured on the legacy window's picture (title and chevron ink against enabled).
    DISABLED = ((0.18, 0.31, 0.29), (0.52, 0.36, 0.34))

    def __init__(self, items: list[str], parent: QWidget):
        super().__init__(parent)
        self.setFont(theme.ui_font(13))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.addItems(items)
        self.restyle()
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        widest = max((theme.text_width(self.itemText(i), self.font()) for i in range(self.count())), default=60.0)
        return QSize(math.ceil(round(widest, 3)) + self.EXTRA, self.H)

    def restyle(self) -> None:
        view = self.view()
        view.setStyleSheet(
            f"QAbstractItemView {{ background: {css(theme.surface())}; color: {css(theme.content())};"
            f" border: 1px solid {css(theme.hairline())}; outline: 0; padding: 4px;"
            f" selection-background-color: {css(theme.content(0.10))}; selection-color: {css(theme.content())}; }}")
        view.setFont(self.font())

    @staticmethod
    def _system(alpha: float) -> QColor:
        """The system's label colour: black in light mode, white in dark, at `alpha`."""
        c = QColor(255, 255, 255) if theme.is_dark() else QColor(0, 0, 0)
        c.setAlphaF(alpha)
        return c

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        on = self.isEnabled()
        strength, title_alpha, arrow_alpha = (1.0, 0.98, 0.85) if on else self.DISABLED[int(theme.is_dark())]
        p.setOpacity(strength)
        bezel = QRectF(self.BEZEL_L, self.BEZEL_T, self.width() - self.BEZEL_L - self.BEZEL_R, self.BEZEL_H)
        r = self.RADIUS
        # The fine shadow: a faint ring all round and a little more under the bottom edge.
        for rect, alpha in ((bezel.adjusted(-0.5, -0.5, 0.5, 0.5), 0.10), (bezel.translated(0, 1.0), 0.08),
                            (bezel.translated(0, 1.5), 0.04)):
            p.fillPath(rounded(rect, r + 0.5), QColor(0, 0, 0, round(alpha * 255)))
        shape = rounded(bezel, r)
        if theme.is_dark():
            p.fillPath(shape, self._system(0.25))
            # A light top edge, half a point thick, following the corners.
            p.fillPath(shape.subtracted(rounded(bezel.translated(0, 0.5), r)), self._system(0.20))
        else:
            p.fillPath(shape, QColor(255, 255, 255))
        if self.hasFocus():
            p.setPen(QPen(theme.content(0.45), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(rounded(bezel.adjusted(-1, -1, 1, 1), r + 1))
        p.setOpacity(1.0)
        font = self.font()
        p.setPen(self._system(title_alpha))
        right = self.width() - self.ARROWS_FROM_RIGHT - self.ARROW_HALF_W - 4
        text = theme.elide(self.currentText(), font, right - self.TITLE_X)
        theme.draw_text_at(p, self.TITLE_X, self.TITLE_Y + QFontMetricsF(font).ascent(), text, font, None, text != self.currentText())
        pen = QPen(self._system(arrow_alpha), self.ARROW_PEN)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy, hw = self.width() - self.ARROWS_FROM_RIGHT, bezel.center().y(), self.ARROW_HALF_W
        for sign in (-1, 1):  # up, then down
            near, far = cy + sign * self.ARROW_NEAR, cy + sign * self.ARROW_FAR
            p.drawPolyline([QPointF(cx - hw, near), QPointF(cx, far), QPointF(cx + hw, near)])
        p.end()


# The Mac's key field, disabled while a key is checked, keeps its text colour and draws it at
# half alpha (measured on the legacy window's picture: the bullets' solid centres).
DISABLED_FIELD_ALPHA = 0.5


class KeyField(QLineEdit):
    """An API key field: hidden characters in a hairline box with a radius of 6, on the surface."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setPlaceholderText("Paste your API key")
        self.setFont(theme.ui_font(13))
        self.setFixedHeight(round(FIELD_H))
        self.setFrame(False)
        self.restyle()

    def keyPressEvent(self, ev) -> None:
        """Enter saves (returnPressed) and stops here, so it does not also reach the window as Done."""
        super().keyPressEvent(ev)
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            ev.accept()

    def restyle(self) -> None:
        # The borders are mixed into the surface as solid colours: a translucent style sheet border
        # shows its overlapping corner segments as dots. The hint is the system's placeholder
        # colour, as on the Mac's secure field; it goes in the style sheet because a style sheet
        # that sets `color` replaces the palette's placeholder colour with that colour at half alpha.
        self.setStyleSheet(
            f"QLineEdit {{ background: {css(theme.surface())}; color: {css(theme.content())};"
            f" placeholder-text-color: {css(theme.system_color('placeholder'))};"
            f" border: 1px solid {css(_on_surface(theme.hairline()))}; border-radius: {round(FIELD_RADIUS)}px; padding: 0 7px;"
            + (f" lineedit-password-character: {theme.secure_bullet()};" if theme.secure_bullet() else "")
            + f" }}{selection_style('QLineEdit')}"
            f" QLineEdit:focus {{ border-color: {css(_on_surface(theme.content(0.45)))}; }}"
            f" QLineEdit:disabled {{ color: {css(theme.content(DISABLED_FIELD_ALPHA))}; }}")


def _on_surface(color: QColor) -> QColor:
    """A translucent colour as the solid colour it shows on the window's surface."""
    a, base = color.alphaF(), theme.surface()
    mix = [round(c * a + b * (1 - a)) for c, b in zip(color.getRgb()[:3], base.getRgb()[:3], strict=True)]
    return QColor(*mix)


class Spinner(QWidget):
    """A small turning arc while a key is checked."""

    SIZE = 16

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.angle = 0
        self.running = False
        self.paint_offset = QPointF(0.0, 0.0)  # see window.place()
        self._timer = QTimer(self)
        self._timer.setInterval(16 if not theme.reduce_motion() else 250)
        self._timer.timeout.connect(self._step)

    def start(self) -> None:
        self.running = True
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self.running = False
        self._timer.stop()
        self.update()

    def _step(self) -> None:
        self.angle = (self.angle + 8) % 360
        self.update()

    def set_paint_offset(self, dx: float, dy: float = 0.0) -> None:
        self.paint_offset = QPointF(dx, dy)
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(self.paint_offset)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(theme.content(0.15), 2.0))
        p.drawEllipse(r)
        pen = QPen(theme.content_dim(), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, -self.angle * 16, 100 * 16)
        p.end()


class WarningIcon(QWidget):
    """The warning triangle beside the shortcuts row, in the error colour."""

    SIZE = 18

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        icons.draw_icon(p, "exclamationmark.triangle", QRectF(self.rect()), bad(), "medium")
        p.end()


def _pill(title: str, parent: QWidget, clicked: Callable[[], None], primary: bool = False, min_w: bool = True) -> PillButton:
    """A focusable pill, never narrower than MIN_BUTTON_W unless min_w is off."""
    b = PillButton(title, parent, primary=primary, clicked=clicked, focusable=True)
    if min_w:
        _min_width(b)
    return b


def _min_width(b: PillButton) -> None:
    hint = b.sizeHint()
    b.setFixedSize(max(hint.width(), round(MIN_BUTTON_W)), hint.height())


def _engine_class(name: str):
    return transcribers.REGISTRY.get(name) or cleaners.REGISTRY[name]


class _KeyRow:
    """One dictation engine's key: its heading, Get a key, the field, Save and the status."""

    def __init__(self, name: str, job: str):
        self.name, self.job = name, job
        self.message = ("", "secondary")  # inline note under the field: (text, tone)
        self.saving = False


# -- the window --------------------------------------------------------------------------------


class SetupWindow(LyssnaWindow):
    """Set `ctl`, `taps_ok` (did the keyboard shortcuts start?), `relaunch` and `on_change` (the
    menu refreshes) after construction, then call show(). `accessibility_trusted`,
    `globe_action` and `key_table` are the system's by default; tests and previews replace them."""

    def __init__(self, ctl=None):
        super().__init__("Lyssna Setup")
        self.ctl = ctl
        self.taps_ok: Callable[[], bool] = lambda: True
        self.relaunch: Callable[[], None] = lambda: None
        self.on_change: Callable[[], None] = lambda: None
        self.accessibility_trusted: Callable[..., bool] = _accessibility_trusted
        self.globe_action: Callable[[], str] = _globe_action  # read on a worker thread
        self.key_table: ModuleType | None = None  # host.hotkey_table unless set
        self.built = False
        self.tab = LISTEN  # the tab on show; the user's choice while the window stays open
        self._saving = False
        self._key_message = ("", "secondary")  # inline note under the key field: (text, tone)
        self._shape: dict | None = None  # what was visible at the last layout
        self._left = (0, 0)  # steps left on Listen and on Dictate, as of the last refresh
        self._dictate_done = (False, False)
        self._globe = "unknown"  # the last answer
        self._globe_read_at = 0.0
        self._globe_busy = False
        self.key_rows = [_KeyRow(name, job) for name, job in DICTATION_KEYS]
        self._visible: set[QWidget] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(round(POLL * 1000))
        self._timer.timeout.connect(self.tick)

    # -- showing -----------------------------------------------------------------------------

    def show(self, engine: str | None = None, dictation: str | None = None) -> None:
        """Bring the window up (building it the first time). `engine` opens Listen with that
        voice engine preselected under "Online, with your API key", for the menu's key entries;
        `dictation` opens Dictate with the online option picked and the cursor in that engine's
        key field."""
        self.prepare(engine, dictation, opening=not self.isVisible())
        self.show_centered()
        row = next((r for r in self.key_rows if r.name == dictation), None)
        if row is not None and not row.field.isHidden():
            self.scroll_area.ensureWidgetVisible(row.field, 0, 96)
            row.field.setFocus(Qt.FocusReason.OtherFocusReason)
        elif engine is not None and self.key_field.isEnabled() and not self.key_field.isHidden():
            self.key_field.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.scroll_area.verticalScrollBar().setValue(0)
            self.done.setFocus(Qt.FocusReason.OtherFocusReason)
        self._timer.start()

    def prepare(self, engine: str | None = None, dictation: str | None = None, opening: bool = True) -> None:
        """Everything show() does short of putting the window on screen: build it if needed,
        point the choices, read the state afresh, and pick the tab. The tab changes only when
        the window opens or the menu asks for a key; while it stays open the tab is the user's."""
        if not self.built:
            self._build()
        self._choose_radio(engine)
        self._choose_dictation(dictation)
        self._read_globe(force=True)
        self._refresh(force=True)
        if opening or engine is not None or dictation is not None:
            self._select_tab(opening_tab(engine, dictation, *self._left))

    def hideEvent(self, ev) -> None:
        self._timer.stop()
        super().hideEvent(ev)

    def tick(self) -> None:
        self._refresh()

    def _select_tab(self, tab: str) -> None:
        """Show `tab`'s steps, from the top."""
        self.tab = tab
        (self.tab_listen if tab == LISTEN else self.tab_dictate).setChecked(True)
        self._refresh(force=True)
        self.scroll_area.verticalScrollBar().setValue(0)

    def _on_tab(self, tab: str, checked: bool) -> None:
        if checked and tab != self.tab:
            self._select_tab(tab)

    # -- building ----------------------------------------------------------------------------

    def _build(self) -> None:
        """Every view, once. Which steps exist follows the capabilities read here."""
        self.built = True
        self.device = host.DEVICE  # wording that names the computer: "Mac" or "PC"
        self.needs_accessibility = bool(host.NEEDS_ACCESSIBILITY)
        self.has_globe = bool(host.HAS_GLOBE_KEY)
        keys = self.key_table or host.hotkey_table
        self.key_table = keys

        # Header: the mark, the welcome, and the Listen / Dictate switch
        self.header = QWidget(self)
        self.mark = MarkView(MARK_H, self.header)
        self.title = Label("Welcome to Lyssna", self.header, font=theme.serif_font(TITLE_SIZE))
        subtitle = "Lyssna reads text aloud and types what you say. Set up each one below."
        self.subtitle = Label(subtitle, self.header, size=13, color=theme.content_dim, wrap=True)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.switch = SwitchTrack(self.header)
        self.tab_listen = TabButton("Listen", self.switch)
        self.tab_dictate = TabButton("Dictate", self.switch)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        for tab, button in ((LISTEN, self.tab_listen), (DICTATE, self.tab_dictate)):
            self.tab_group.addButton(button)
            button.toggled.connect(lambda checked, t=tab: self._on_tab(t, checked))
        self.tab_listen.setChecked(True)
        self.header_line = Hairline(self.header)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.viewport().setAutoFillBackground(False)
        self.content = QWidget()
        self.scroll_area.setWidget(self.content)
        self.content.setAutoFillBackground(False)  # setWidget turned it on; the window's surface shows through
        c = self.content

        # Footer: where things stand, and Done
        self.footer = QWidget(self)
        self.footer_line = Hairline(self.footer)
        self.footer_status = Label("", self.footer, size=12, color=theme.content_dim)
        self.done = _pill("Done", self.footer, self.close, primary=True)

        # The option cards go in first, so they sit behind the controls laid over them.
        self.card_local = OptionCard(c)
        self.card_key = OptionCard(c)
        self.cards = [self.card_local, self.card_key]

        number = 1
        self.relaunch_btn = _pill("Relaunch Lyssna", c, lambda: self.relaunch())
        if self.needs_accessibility:
            # Listen 1: Permission
            self.icon_permission = StepIcon(number, c)
            self.head_permission = Label("Permission", c, size=14, weight="semibold")
            self.body_permission = Label("", c, size=12, color=theme.content_dim, wrap=True)
            self.open_settings = _pill("Open Settings…", c, self.on_open_settings)
            self.sep_permission = Hairline(c)
            number += 1
        else:
            # Above the steps, only when the keyboard shortcuts could not start
            self.warn_icon = WarningIcon(c)
            self.warn_text = Label("Lyssna could not start its keyboard shortcuts. Quit other tools that capture "
                                   "the keyboard, then relaunch.", c, size=13, wrap=True)
            self.sep_warning = Hairline(c)

        # Listen: Voice
        self.icon_voice = StepIcon(number, c)
        self.head_voice = Label("Voice", c, size=14, weight="semibold")
        self.radio_local = Radio(f"On this {self.device}", c)
        self.local_tag = Tag("Free and private", privacy_symbol(True), c)
        self.local_note = Label(f"Your text stays on this {self.device}. One download of about {kokoro.DOWNLOAD_SIZE_TEXT}.", c, size=12,
                                color=theme.content_dim, wrap=True)
        self.download = _pill("Download", c, self.on_download)
        self.progress = ProgressBar(c)  # the player's bar, not the blue system one
        self.progress.resize(200, round(BAR_H))
        self.progress_text = Label("", c, font=theme.digits_font(12), color=theme.content_dim)
        self.progress_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ready = StatusRow("Ready", c)
        self.local_error = Label("", c, size=12, color=bad, wrap=True)

        self.radio_key = Radio(ONLINE_HEADER, c)
        self.key_tag = Tag("", privacy_symbol(False), c)
        self.key_note = Label("Voices from Inworld, Cartesia, ElevenLabs or OpenAI.", c, size=12,
                              color=theme.content_dim, wrap=True)
        self.engines = [n for n, cls in REGISTRY.items() if cls.needs_key]
        self.popup = Dropdown([REGISTRY[n].label for n in self.engines], c)
        self.popup.activated.connect(lambda _i: self.on_pick_engine())
        self.get_key = IconPillButton("Get a key", "arrow.up.right", c, clicked=self.on_get_key)
        self.key_field = KeyField(c)
        self.key_field.returnPressed.connect(self.on_save_key)
        self.save = _pill("Save", c, self.on_save_key)
        self.spinner = Spinner(c)
        self.key_status = Label("", c, size=12, color=theme.content_dim, wrap=True)
        self.local_only_note = Label(f"Online voices are off because “Keep Everything on This {self.device}” is on.",
                                     c, size=12, color=theme.content_dim, wrap=True)
        self.sep_voice = Hairline(c)

        self.voice_group = QButtonGroup(self)
        self.voice_group.setExclusive(True)
        for radio in (self.radio_local, self.radio_key):
            self.voice_group.addButton(radio)
            radio.clicked.connect(lambda _checked=False, r=radio: self.on_choose_voice(r))
        self.radio_local.setChecked(True)

        # Listen: Try it
        self.icon_try = StepIcon(number + 1, c)
        self.head_try = Label("Try it", c, size=14, weight="semibold")
        self.try_text = Label("Select text in any app and press", c, size=13, color=theme.content_dim)
        self.keycap = KeyCaps(key_caps(self.ctl.cfg["hotkeys"]["speak"], keys), c)
        self.sample = _pill("Read a Sample", c, self.on_sample)
        self.try_note = Label("The shortcut works once step 1 is done. The sample works now.", c, size=12,
                              color=theme.content_dim, wrap=True)

        self._build_dictation(c)

        for card in self.cards:
            card.lower()
        self.restyle()

    def _build_dictation(self, c: QWidget) -> None:
        """Dictate's three steps."""
        self.card_dict_local = OptionCard(c)
        self.card_dict_online = OptionCard(c)
        self.cards += [self.card_dict_local, self.card_dict_online]

        # Dictate 1: Permission
        self.dicon1 = StepIcon(1, c)
        self.dhead1 = Label("Permission", c, size=14, weight="semibold")
        self.mic_row = StatusRow("", c, done=False)
        self.mic_allow = _pill("Allow", c, self.on_allow_microphone)
        self.mic_settings = _pill("Open Settings", c, self.on_open_microphone_settings, min_w=False)
        self.globe_row = StatusRow("", c, done=False)
        self.globe_settings = _pill("Open Keyboard Settings", c, self.on_open_keyboard_settings, min_w=False)
        self.dsep1 = Hairline(c)

        # Dictate 2: Engine
        self.dicon2 = StepIcon(2, c)
        self.dhead2 = Label("Engine", c, size=14, weight="semibold")
        self.dict_local = Radio(f"On this {self.device}", c)
        self.dict_local_tag = Tag("Free and private", privacy_symbol(True), c)
        self.dict_local_note = Label("", c, size=12, color=theme.content_dim, wrap=True)
        self.dict_download = _pill("Download", c, self.on_download_models)
        self.dict_progress = ProgressBar(c)
        self.dict_progress.resize(200, round(BAR_H))
        self.dict_progress_text = Label("", c, font=theme.digits_font(12), color=theme.content_dim)
        self.dict_progress_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.dict_ready = StatusRow("Ready", c)
        self.dict_error = Label("", c, size=12, color=bad, wrap=True)
        self.dict_online = Radio("Online, with your API keys", c)
        vendors = [_engine_class(row.name).vendor for row in self.key_rows]
        self.dict_online_tag = Tag(f"Sent to {vendors[0]} and {vendors[1]}", privacy_symbol(False), c)
        self.dict_online_note = Label(f"Best with names and unusual words. Your voice goes to {vendors[0]} and the "
                                      f"text to {vendors[1]} for cleanup. Each needs your own key.", c, size=12,
                                      color=theme.content_dim, wrap=True)
        for row in self.key_rows:
            cls = _engine_class(row.name)
            row.head = Label(f"{cls.vendor} ({row.job})", c, size=13, weight="medium")
            row.get_key = IconPillButton("Get a key", "arrow.up.right", c, clicked=lambda r=row: self.on_get_dictation_key(r))
            row.field = KeyField(c)
            row.field.returnPressed.connect(lambda r=row: self.on_save_dictation_key(r))
            row.save = _pill("Save", c, lambda r=row: self.on_save_dictation_key(r))
            row.spinner = Spinner(c)
            row.status = Label("", c, size=12, color=theme.content_dim, wrap=True)
        self.dict_local_only_note = Label(f"Online dictation is off because “Keep Everything on This {self.device}” is on.",
                                          c, size=12, color=theme.content_dim, wrap=True)
        self.dsep2 = Hairline(c)

        self.dict_group = QButtonGroup(self)  # their own group, apart from the voice's
        self.dict_group.setExclusive(True)
        for radio in (self.dict_local, self.dict_online):
            self.dict_group.addButton(radio)
            radio.clicked.connect(lambda _checked=False, r=radio: self.on_choose_dictation(r))
        self.dict_local.setChecked(True)

        # Dictate 3: Try it
        self.dicon3 = StepIcon(3, c)
        self.dhead3 = Label("Try it", c, size=14, weight="semibold")
        self.hold_text = Label("Hold", c, size=13, color=theme.content_dim)
        self.hold_keys = KeyCaps([], c)
        self.hold_after = Label("and speak.", c, size=13, color=theme.content_dim)
        self.dictate_body = Label("Lyssna types what you said, cleaned up, wherever your cursor is.", c, size=13,
                                  color=theme.content_dim, wrap=True)
        self.free_text = Label("Hands-free:", c, size=13, color=theme.content_dim)
        self.free_keys = KeyCaps([], c)
        self.tap_text = Label("Or tap", c, size=13, color=theme.content_dim)  # only where the double tap is on
        self.tap_keys = KeyCaps([], c)
        self.tap_after = Label("twice.", c, size=13, color=theme.content_dim)
        self._set_dictation_caps()

    def restyle(self) -> None:
        if not self.built:
            return
        self.popup.restyle()
        for field in self.findChildren(KeyField):
            field.restyle()
        self.scroll_area.verticalScrollBar().setStyleSheet(
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}"
            f" QScrollBar::handle:vertical {{ background: {css(theme.content(0.25))}; border-radius: 3px; min-height: 24px; }}"
            f" QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f" QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}")

    # -- state -------------------------------------------------------------------------------

    def _choose_radio(self, engine: str | None) -> None:
        """Point the voice choice at `engine`, or at whatever the app reads with now."""
        current = engine or self.ctl.cfg.get("provider", "")
        online = current in self.engines and allowed(current, self.ctl.cfg)
        (self.radio_key if online else self.radio_local).setChecked(True)
        if online:
            self.popup.setCurrentIndex(self.engines.index(current))
        if engine is not None:
            self._key_message = ("", "secondary")

    def _engine(self) -> str:
        return self.engines[max(0, self.popup.currentIndex())]

    def _dictation_section(self) -> dict:
        return self.ctl.cfg.get("dictation", {})

    def _dictation_hotkey(self) -> str:
        return str(self._dictation_section().get("hotkey", config.DEFAULTS["dictation"]["hotkey"]))

    def _double_tap(self) -> bool:
        """Do two quick taps of the dictation key start hands-free? On by default where
        host.DICTATION_DOUBLE_TAP_DEFAULT is (the PC), and the [dictation] double_tap setting."""
        return bool(self._dictation_section().get("double_tap", host.DICTATION_DOUBLE_TAP_DEFAULT))

    def _set_dictation_caps(self) -> None:
        """The dictation key and hands-free caps, from the config, named for this system."""
        hold = dictation_caps(self._dictation_hotkey(), self.key_table)
        hands_free = str(self._dictation_section().get("hands_free", config.DEFAULTS["dictation"]["hands_free"]))
        if hold != self.hold_keys.labels:
            self.hold_keys.set_labels(hold)
        if hold != self.tap_keys.labels:
            self.tap_keys.set_labels(hold)
        free = [*hold, *key_caps(hands_free, self.key_table)]
        if free != self.free_keys.labels:
            self.free_keys.set_labels(free)

    def _dictation_online_allowed(self) -> bool:
        return all(self._allowed(row.name) for row in self.key_rows)

    def _allowed(self, name: str) -> bool:
        cfg = self.ctl.cfg
        return transcribers.allowed(name, cfg) if name in transcribers.REGISTRY else cleaners.allowed(name, cfg)

    def _choose_dictation(self, engine: str | None) -> None:
        """Point the dictation choice at the online option when asked for an engine's key or
        when dictation uses an online engine now; else at the engines on this computer."""
        section = self._dictation_section()
        in_use = {str(section.get("transcriber", "")), str(section.get("cleaner", "")) if section.get("cleanup", True) else ""}
        online = self._dictation_online_allowed() and (engine in DICTATION_KEY_ENGINES or bool(in_use & set(DICTATION_KEY_ENGINES)))
        (self.dict_online if online else self.dict_local).setChecked(True)
        if engine is not None:
            for row in self.key_rows:
                row.message = ("", "secondary")

    def _read_globe(self, force: bool = False) -> None:
        """Read the Globe key setting on a worker thread, at most every GLOBE_POLL seconds;
        the next tick shows the answer."""
        if not self.has_globe or self._globe_busy or (not force and time.monotonic() - self._globe_read_at < GLOBE_POLL):
            return
        self._globe_busy = True

        def read() -> None:
            try:
                answer = self.globe_action()
            except Exception:
                log.debug("Globe key setting unreadable", exc_info=True)
                answer = "unknown"
            self._globe, self._globe_read_at, self._globe_busy = answer, time.monotonic(), False

        threading.Thread(target=read, daemon=True, name="lyssna-globe-read").start()

    def _refresh(self, force: bool = False) -> None:
        """Read the live state (permission, shortcuts, download, keys) and show it."""
        ctl = self.ctl
        taps_ok = bool(self.taps_ok())
        trusted = bool(self.accessibility_trusted()) if self.needs_accessibility else True
        needs_relaunch = trusted and not taps_ok
        body = ""
        if self.needs_accessibility:
            if not trusted:
                body = ("The shortcut and the play button need this to read what you select. "
                        "Click Open Settings and turn on Lyssna. If it is not in the list, "
                        "click + and choose Lyssna.")
            elif needs_relaunch:
                body = "Allowed. Relaunch Lyssna once so the shortcut starts working."
            else:
                body = "Allowed. The shortcut and the play button are ready."
            self.body_permission.set_text(body)
        permission_done = trusted and not needs_relaunch  # without the permission: the shortcuts started
        if self.needs_accessibility:
            self.icon_permission.set_done(permission_done)

        local_only = ctl.local_only()
        if local_only and self.radio_key.isChecked():
            self._choose_radio(None)
        can_run = kokoro.KokoroProvider.can_run()
        self.radio_local.setEnabled(can_run)
        progress = ctl.voice_download_progress()
        ready = can_run and ctl.provider_configured("kokoro")
        error = kokoro.DOWNLOAD.error if (progress is None and not ready) else ""
        if not can_run:
            error = f"The voice on this {self.device} is not available in this build."
        if progress is not None:
            self.progress.set_fraction(progress)
            self.progress_text.set_text(f"{int(progress * 100)}%")
        self.local_error.set_text(error)
        self.download.retitle("Try Again" if error and can_run else "Download")  # keeps Download's width, as on the Mac

        provider = ctl.cfg.get("provider", "")
        voice_done = provider in REGISTRY and ctl.provider_configured(provider)
        self.icon_voice.set_done(voice_done)
        self.icon_try.set_done(False)

        engine = self._engine()
        self.key_tag.set(f"Text is sent to {get_provider(engine, ctl.cfg).recipient()}", privacy_symbol(False))
        has_key = ctl.provider_configured(engine)
        text, tone = self._key_message
        if not text and not self._saving:
            if has_key and provider == engine:
                text, tone = f"Using {REGISTRY[engine].label} with your saved key.", "good"
            elif has_key:
                text = "A key is already saved. Paste a new one to replace it."
        self.key_status.set_text(text)
        self.key_status.set_color(TONES.get(tone, theme.content_dim))
        for view in (self.popup, self.key_field, self.save):
            view.setEnabled(not self._saving)

        dictation = self._refresh_dictation()

        self._left = (steps_left((permission_done, voice_done)), steps_left(self._dictate_done))
        self.tab_listen.set_status(tab_status(self._left[0]))
        self.tab_dictate.set_status(tab_status(self._left[1]))
        self.footer_status.set_text(footer_status(*self._left))

        local_chosen = self.radio_local.isChecked()
        shape = {
            "tab": self.tab,
            "left": self._left,
            "relaunch": self.needs_accessibility and needs_relaunch,
            "open_settings": not trusted,
            "warning": not self.needs_accessibility and not taps_ok,
            "local_body": local_chosen,
            "download": local_chosen and progress is None and not ready and can_run,
            "progress": local_chosen and progress is not None,
            "ready": local_chosen and ready,
            "local_error": local_chosen and bool(error),
            "radio_key": not local_only,
            "key_body": not local_only and not local_chosen,
            "key_status": not local_only and not local_chosen and bool(text),
            "local_only_note": local_only,
            "try_note": not trusted,
            "texts": (body, error, text, self._saving, self.key_tag.text, self.download.text()),
            "dictation": dictation,
        }
        if force or shape != self._shape:
            self._shape = shape
            self._layout(shape)

    def _refresh_dictation(self) -> tuple:
        """Dictate's live state (microphone, Globe key, models, keys) onto its views. Returns
        what decides its layout, so a change re-lays the window out, and leaves which of its
        steps are done in self._dictate_done."""
        ctl = self.ctl
        mic = ctl.microphone_status()
        if mic == "granted":
            self.mic_row.set("Microphone allowed", True)
        elif mic == "undetermined":
            self.mic_row.set("Allow the microphone so Lyssna can hear you", False)
        else:
            self.mic_row.set("Microphone access is off. Turn it on in Settings.", False)

        self._set_dictation_caps()
        on_globe = self.has_globe and self._dictation_hotkey() == GLOBE_KEY
        globe_ok = not on_globe or self._globe == "nothing"
        if on_globe:
            self._read_globe()
            if globe_ok:
                self.globe_row.set("Globe key set to Do Nothing", True)
            else:
                self.globe_row.set("Set “Press Globe key to” to Do Nothing", False)

        local_only = ctl.local_only()
        if (local_only or not self._dictation_online_allowed()) and self.dict_online.isChecked():
            self._choose_dictation(None)
        models = ctl.local_dictation_models()
        can_run = transcribers.REGISTRY[transcribers.LOCAL].can_run()
        self.dict_local.setEnabled(can_run)
        self.dict_local_note.set_text(
            f"Your voice and text stay on this {self.device}. One download of "
            f"{_size_text(sum(m.total_bytes for m in models))}." if can_run else "")
        progress = ctl.dictation_download_progress()
        ready = can_run and all(m.present() for m in models)
        error = ""
        if not can_run:
            error = f"Dictation on this {self.device} is not available in this build."
        elif progress is None and not ready:
            error = next((m.download().error for m in models if not m.present() and m.download().error), "")
        if progress is not None:
            self.dict_progress.set_fraction(progress)
            self.dict_progress_text.set_text(f"{int(progress * 100)}%")
        self.dict_error.set_text(error)
        self.dict_download.retitle("Try Again" if error and can_run else "Download")  # keeps Download's width, as on the Mac

        texts = []
        keys_saved = True
        for row in self.key_rows:
            state = ctl.dictation_key_state(row.name)
            keys_saved = keys_saved and state is True
            text, tone = row.message
            if not text and not row.saving:
                if state and self._in_use(row.name):
                    text, tone = "Using your saved key.", "good"
                elif state:
                    text = "A key is already saved. Paste a new one to replace it."
            row.status.set_text(text)
            row.status.set_color(TONES.get(tone, theme.content_dim))
            for view in (row.field, row.save):
                view.setEnabled(not row.saving)
            texts.append((text, row.saving))

        local_chosen = self.dict_local.isChecked()
        self._dictate_done = (mic == "granted" and globe_ok, ready if local_chosen else keys_saved)
        self.dicon1.set_done(self._dictate_done[0])
        self.dicon2.set_done(self._dictate_done[1])
        self.dicon3.set_done(False)
        return (mic, on_globe, globe_ok, local_only or not self._dictation_online_allowed(), local_chosen,
                progress is not None, ready, error, can_run, tuple(texts), self.dict_local_note.value(),
                self.mic_row.label.value(), tuple(self.free_keys.labels), self.dict_download.text(), self._double_tap())

    def _in_use(self, name: str) -> bool:
        if name in transcribers.REGISTRY:
            return self.ctl.current_transcriber() == name
        return self.ctl.current_cleaner() == name

    # -- layout ------------------------------------------------------------------------------
    #
    # Explicit geometry, top to bottom, as on the Mac. The helpers below place a view and note
    # it as visible; whatever in the steps was not placed is hidden afterwards.

    def put(self, view: QWidget, x: float, y: float, width: float | None = None) -> float:
        if isinstance(view, Label):
            _, h = view.fit(width)
        else:
            h = view.height()
        place(view, x, y)
        self._visible.add(view)
        return h

    def centre_row(self, views_x: list[tuple[QWidget, float]], y: float) -> float:
        """Place views on one line, vertically centred on the tallest; returns the line height.
        The views keep the size they have (fit labels first)."""
        h = max(v.height() for v, _ in views_x)
        for v, x in views_x:
            if isinstance(v, KeyCaps):  # it draws itself, so it can start on the Mac's half point
                left = math.floor(x)
                v.set_offset(x - left)
                x = left
            if isinstance(v, ProgressBar):  # it draws itself, so it can sit on the Mac's half point
                top = y + (h - v.thickness()) / 2
                v.move(round(x), math.floor(top))
                v.set_paint_offset(0.0, top - math.floor(top))
                self._visible.add(v)
                continue
            place(v, x, y + (h - v.height()) / 2)
            self._visible.add(v)
        return h

    def _step(self, icon: StepIcon, head: Label, y: float) -> float:
        hh = self.put(head, TEXT_X, y)
        place(icon, MARGIN, y + (hh - ICON) / 2)
        self._visible.add(icon)
        return hh

    def _sep(self, view: Hairline, y: float) -> float:
        """A hairline between steps, 16 below the step above; returns where the next step starts."""
        view.setGeometry(round(MARGIN), round(y + 16), round(W - 2 * MARGIN), 1)
        self._visible.add(view)
        return y + 33

    def _option_head(self, radio: Radio, tag: Tag, y: float) -> float:
        """A card's first line: its radio on the left, its privacy tag on the right."""
        radio.resize(radio.sizeHint())
        room = CARD_R - CARD_X - radio.width() - 12
        tag.resize(min(tag.natural_width(), max(0, round(room))), Tag.H)  # a long custom endpoint: the words give way
        return self.centre_row([(radio, CARD_X), (tag, CARD_R - tag.width())], y)

    def _place_card(self, card: OptionCard, top: float, bottom: float, chosen: bool) -> None:
        card.setGeometry(round(TEXT_X), round(top), round(TEXT_W), round(bottom - top))
        card.set_chosen(chosen)
        self._visible.add(card)

    def _progress_row(self, bar: ProgressBar, label: Label, gap: float, y: float) -> float:
        """A progress bar and its percentage, the label as wide as "100%" gets; returns the line height."""
        text = label.value()
        label.set_text("100%")  # measure the widest it gets
        tw, th = label.fit()
        label.set_text(text)
        label.resize(tw, th)
        bar.set_paint_offset(0.0, 0.0)
        bar.resize(round(SUB_W - tw - gap), round(BAR_H))
        return self.centre_row([(bar, SUB_X), (label, SUB_X + SUB_W - tw)], y)

    def _layout(self, show: dict) -> None:
        """Lay out the current tab's steps, then fit the window to header, steps and footer
        (keeping its top edge). Only the steps scroll when the screen is too short."""
        self._visible = set()
        y = STEPS_TOP
        if self.tab == LISTEN:
            y = self._layout_listen(show, y)
        else:
            y = self._layout_dictation(show["dictation"], y)
        y += 20
        for view in self.content.children():
            if isinstance(view, QWidget):
                view.setVisible(view in self._visible)
        self.content.setFixedSize(round(W), round(y))

        head_h = self._layout_header()
        h = self.fit_height(W, head_h + y + FOOTER_H)
        self.header.setGeometry(0, 0, round(W), round(head_h))
        self.scroll_area.setGeometry(0, round(head_h), round(W), round(h - head_h - FOOTER_H))
        self._layout_footer(h)

    def _layout_header(self) -> float:
        """The mark, the welcome and the switch, centred; returns the header's height."""
        y = HEADER_TOP
        self.mark.move(round((W - self.mark.width()) / 2), round(y))
        y += MARK_H + 10
        _, th = self.title.fit()
        place(self.title, (W - self.title.exact_width()) / 2, y)  # centred on its half-point frame, as on the Mac
        y += th + 6
        _, sh = self.subtitle.fit(W - 2 * MARGIN)
        self.subtitle.move(round(MARGIN), round(y))
        y += sh + 16
        seg_w = max(self.tab_listen.preferred_width(), self.tab_dictate.preferred_width())  # equal halves
        self.tab_listen.setGeometry(round(SEG_PAD), round(SEG_PAD), round(seg_w), round(SEG_H))
        self.tab_dictate.setGeometry(round(SEG_PAD + seg_w + 2), round(SEG_PAD), round(seg_w), round(SEG_H))
        track_w = 2 * seg_w + 2 + 2 * SEG_PAD
        self.switch.setGeometry(round((W - track_w) / 2), round(y), round(track_w), round(SEG_H + 2 * SEG_PAD))
        y += SEG_H + 2 * SEG_PAD + 18
        self.header_line.setGeometry(0, round(y), round(W), 1)
        return y + 1

    def _layout_footer(self, h: float) -> None:
        """A hairline, the status line on the left and Done on the right."""
        self.footer.setGeometry(0, round(h - FOOTER_H), round(W), round(FOOTER_H))
        self.footer_line.setGeometry(0, 0, round(W), 1)
        self.done.move(round(W - 20 - self.done.width()), round((FOOTER_H - self.done.height()) / 2))
        _, sh = self.footer_status.fit()
        place(self.footer_status, MARGIN, (FOOTER_H - sh) / 2)

    def _layout_listen(self, show: dict, y: float) -> float:
        """Listen's steps (and, without a permission step, the shortcuts warning above them),
        from y down; returns where they end."""
        put, centre_row = self.put, self.centre_row

        if self.needs_accessibility:
            # Permission
            y += self._step(self.icon_permission, self.head_permission, y) + 4
            y += put(self.body_permission, TEXT_X, y, TEXT_W)
            row = []
            x = TEXT_X  # pills have no transparent inset, so their edge lines up with the text as is
            for flag, btn in (("open_settings", self.open_settings), ("relaunch", self.relaunch_btn)):
                if show[flag]:
                    row.append((btn, x))
                    x += btn.width() + 8
            if row:
                y += 8 + centre_row(row, y + 8)
            y = self._sep(self.sep_permission, y)
        elif show["warning"]:
            th = put(self.warn_text, TEXT_X, y, TEXT_W)
            self.warn_icon.move(round(MARGIN + (ICON - self.warn_icon.width()) / 2), round(y + 1))
            self._visible.add(self.warn_icon)
            y += th + 10
            y += centre_row([(self.relaunch_btn, TEXT_X)], y)
            y = self._sep(self.sep_warning, y)

        # Voice: two option cards
        y += self._step(self.icon_voice, self.head_voice, y) + 10
        top = y
        y += CARD_PAD + self._option_head(self.radio_local, self.local_tag, y + CARD_PAD) + 4
        y += put(self.local_note, SUB_X, y, SUB_W)
        if show["local_body"]:
            if show["download"]:
                y += 10 + centre_row([(self.download, SUB_X)], y + 10)
            elif show["progress"]:
                y += 10 + self._progress_row(self.progress, self.progress_text, 10, y + 10)
            elif show["ready"]:
                y += 8 + centre_row([(self.ready, SUB_X)], y + 8)
            if show["local_error"]:
                y += 6 + put(self.local_error, SUB_X, y + 6, SUB_W)
        y += CARD_PAD
        self._place_card(self.card_local, top, y, show["local_body"])
        if show["radio_key"]:
            y += 8
            top = y
            y += CARD_PAD + self._option_head(self.radio_key, self.key_tag, y + CARD_PAD) + 4
            y += put(self.key_note, SUB_X, y, SUB_W)
            if show["key_body"]:
                y += 10
                # The bezel's left edge on SUB_X, as on the Mac; Get a key after the whole frame.
                y += centre_row([(self.popup, SUB_X - Dropdown.BEZEL_L), (self.get_key, SUB_X + self.popup.width() + 10)], y) + 8
                save_w = self.save.width()
                self.key_field.setFixedWidth(round(SUB_W - save_w - 8))  # the Save pill's edge ends the row
                y += centre_row([(self.key_field, SUB_X), (self.save, CARD_R - save_w)], y)
                if show["key_status"]:
                    shift = 22.0 if self._saving else 0.0
                    h = put(self.key_status, SUB_X + shift, y + 8, SUB_W - shift)
                    if self._saving:
                        place(self.spinner, SUB_X, y + 8 + (h - Spinner.SIZE) / 2)
                        self._visible.add(self.spinner)
                    y += 8 + h
            y += CARD_PAD
            self._place_card(self.card_key, top, y, not show["local_body"])
        if show["local_only_note"]:
            y += 8 + put(self.local_only_note, TEXT_X, y + 8, TEXT_W)
        y = self._sep(self.sep_voice, y)

        # Try it
        y += self._step(self.icon_try, self.head_try, y) + 8
        self.try_text.fit()
        y += centre_row([(self.try_text, TEXT_X), (self.keycap, TEXT_X + self.try_text.exact_width() + 8)], y) + 10
        y += centre_row([(self.sample, TEXT_X)], y)
        if self.needs_accessibility and show["try_note"]:
            y += 6 + put(self.try_note, TEXT_X, y + 6, TEXT_W)
        return y

    def _layout_dictation(self, state: tuple, y: float) -> float:
        """Dictate's three steps, from y down; returns where they end."""
        put, centre_row = self.put, self.centre_row
        mic, on_globe, globe_ok, online_off, local_chosen, downloading, ready, error, can_run = state[:9]

        # 1 Permission
        y += self._step(self.dicon1, self.dhead1, y) + 8

        def check(status_row: StatusRow, pill: PillButton | None, y: float) -> float:
            """A status line, with its pill when something is left to do: at the right end of
            the line when both fit, else under the words."""
            if pill is None:
                return y + centre_row([(status_row, TEXT_X)], y)
            pw = pill.width()
            if status_row.width() + 12 + pw <= TEXT_W:
                return y + centre_row([(status_row, TEXT_X), (pill, TEXT_X + TEXT_W - pw)], y)
            y += centre_row([(status_row, TEXT_X)], y)
            return y + 8 + centre_row([(pill, TEXT_X + 20)], y + 8)

        y = check(self.mic_row, {"undetermined": self.mic_allow, "denied": self.mic_settings}.get(mic), y)
        if on_globe:
            y = check(self.globe_row, None if globe_ok else self.globe_settings, y + 10)
        y = self._sep(self.dsep1, y)

        # 2 Engine: two option cards
        y += self._step(self.dicon2, self.dhead2, y) + 10
        top = y
        y += CARD_PAD + self._option_head(self.dict_local, self.dict_local_tag, y + CARD_PAD)
        if self.dict_local_note.value():
            y += 4 + put(self.dict_local_note, SUB_X, y + 4, SUB_W)
        if local_chosen:
            if downloading:
                y += 10 + self._progress_row(self.dict_progress, self.dict_progress_text, 8, y + 10)
            elif ready:
                y += 8 + centre_row([(self.dict_ready, SUB_X)], y + 8)
            elif can_run:
                y += 10 + centre_row([(self.dict_download, SUB_X)], y + 10)
            if error:
                y += 6 + put(self.dict_error, SUB_X, y + 6, SUB_W)
        y += CARD_PAD
        self._place_card(self.card_dict_local, top, y, local_chosen)
        if online_off:
            y += 8 + put(self.dict_local_only_note, TEXT_X, y + 8, TEXT_W)
        else:
            y += 8
            top = y
            y += CARD_PAD + self._option_head(self.dict_online, self.dict_online_tag, y + CARD_PAD) + 4
            y += put(self.dict_online_note, SUB_X, y, SUB_W)
            if not local_chosen:
                for row in self.key_rows:
                    y += 14
                    row.head.fit()
                    y += centre_row([(row.head, SUB_X), (row.get_key, CARD_R - row.get_key.width())], y) + 8
                    save_w = row.save.width()
                    row.field.setFixedWidth(round(SUB_W - save_w - 8))  # the Save pill's edge ends the row
                    y += centre_row([(row.field, SUB_X), (row.save, CARD_R - save_w)], y)
                    if row.status.value():
                        shift = 24.0 if row.saving else 0.0
                        h = put(row.status, SUB_X + shift, y + 8, SUB_W - shift)
                        if row.saving:
                            place(row.spinner, SUB_X, y + 8 + (h - Spinner.SIZE) / 2)
                            self._visible.add(row.spinner)
                        y += 8 + h
            y += CARD_PAD
            self._place_card(self.card_dict_online, top, y, not local_chosen)
        y = self._sep(self.dsep2, y)

        # 3 Try it: Hold [key] and speak.   Hands-free: [key] [Space]   (Or tap [key] twice.)
        y += self._step(self.dicon3, self.dhead3, y) + 8
        y += self._caps_row((self.hold_text, self.hold_keys, self.hold_after), y) + 6
        y += put(self.dictate_body, TEXT_X, y, TEXT_W) + 10
        self.free_text.fit()
        y += centre_row([(self.free_text, TEXT_X), (self.free_keys, TEXT_X + self.free_text.exact_width() + 6)], y)
        if self._double_tap():
            y += 6 + self._caps_row((self.tap_text, self.tap_keys, self.tap_after), y)
        return y

    def _caps_row(self, views: tuple[Label, KeyCaps, Label], y: float) -> float:
        """A sentence with key caps in it ("Hold [fn] and speak."), from TEXT_X; returns its height."""
        row_x, x = [], TEXT_X
        for view, gap in zip(views, (6, 6, 0), strict=True):
            if isinstance(view, Label):
                view.fit()
            w = view.exact_width()  # the Mac's half-point frames, so each view starts where the Mac's does
            row_x.append((view, x))
            x += w + gap
        return self.centre_row(row_x, y)

    # -- actions -----------------------------------------------------------------------------

    def keyPressEvent(self, ev) -> None:
        """Enter is Done, as the Mac's default button, except in a key field (Enter saves there)."""
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not isinstance(self.focusWidget(), KeyField):
            self.done.click()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def on_open_settings(self) -> None:
        self.accessibility_trusted(prompt=True)  # puts Lyssna in the list if it is not there yet
        if host.ACCESSIBILITY_SETTINGS_URL:
            host.open_target(host.ACCESSIBILITY_SETTINGS_URL)

    def on_choose_voice(self, radio: Radio) -> None:
        local = radio is self.radio_local
        self._key_message = ("", "secondary")
        if local:
            if self.ctl.provider_configured("kokoro") and self.ctl.cfg.get("provider") != "kokoro":
                self._use("kokoro")
        elif self.ctl.provider_configured(self._engine()):
            self._use(self._engine())
        self._refresh(force=True)
        if not local:
            self.key_field.setFocus(Qt.FocusReason.OtherFocusReason)

    def on_pick_engine(self) -> None:
        self._key_message = ("", "secondary")
        engine = self._engine()
        if self.ctl.provider_configured(engine):
            self._use(engine)
        self._refresh(force=True)

    def on_download(self) -> None:
        self._use("kokoro")  # starts the download; a reading asked for meanwhile waits for it
        self._refresh(force=True)

    def on_get_key(self) -> None:
        url = REGISTRY[self._engine()].key_url
        if url:
            host.open_target(url)

    def on_save_key(self) -> None:
        if self._saving:
            return
        engine, key = self._engine(), self.key_field.text()
        if not key.strip():
            self._key_message = ("Paste your API key first.", "bad")
            self._refresh(force=True)
            return
        self._saving = True
        self._key_message = ("Checking the key…", "secondary")
        self.spinner.start()
        self._refresh(force=True)

        def check() -> None:
            try:
                error = self.ctl.add_api_key(engine, key)  # one real synthesis, then save
            except Exception as e:  # never leave the spinner turning; the key is not logged
                log.warning("checking a %s key failed: %s", engine, type(e).__name__)
                error = f"That key didn't work. {e}"
            on_main(self._saved, engine, error)

        threading.Thread(target=check, daemon=True, name="lyssna-key-check").start()

    def _saved(self, engine: str, error: str) -> None:
        self._saving = False
        self.spinner.stop()
        if error:
            self._key_message = (error[:300], "bad")
        else:
            self.key_field.clear()
            self._key_message = (f"Saved. Lyssna now reads with {REGISTRY[engine].label}.", "good")
            self.on_change()
        self._refresh(force=True)

    def on_sample(self) -> None:
        self.ctl.speak_text(SAMPLE)

    def _use(self, engine: str) -> None:
        try:
            self.ctl.set_provider(engine)
        except ValueError as e:
            self._key_message = (str(e), "bad")
        self.on_change()

    # -- dictation actions -------------------------------------------------------------------

    def on_allow_microphone(self) -> None:
        self.ctl.request_microphone()  # the system shows its prompt; the next tick shows the answer

    def on_open_microphone_settings(self) -> None:
        host.open_target(host.MIC_SETTINGS_URL)

    def on_open_keyboard_settings(self) -> None:
        if host.KEYBOARD_SETTINGS_URL:
            host.open_target(host.KEYBOARD_SETTINGS_URL)
        self._globe_read_at = 0.0  # read the setting again soon after the user changes it

    def on_choose_dictation(self, radio: Radio) -> None:
        """An option switches the engines only when it can run now: the models are here, or a
        key is saved. Otherwise Download or Save does it."""
        local = radio is self.dict_local
        for row in self.key_rows:
            row.message = ("", "secondary")
        try:
            if local:
                if all(m.present() for m in self.ctl.local_dictation_models()):
                    self.ctl.use_local_dictation()
            else:
                for row in self.key_rows:
                    if self.ctl.dictation_key_state(row.name) is True:
                        self._use_dictation(row.name)
        except ValueError as e:
            log.info("dictation engine not switched: %s", e)
        self.on_change()
        self._refresh(force=True)
        if not local:
            self.key_rows[0].field.setFocus(Qt.FocusReason.OtherFocusReason)

    def on_download_models(self) -> None:
        try:
            self.ctl.use_local_dictation()  # starts the downloads; the progress bar follows them
        except ValueError as e:
            log.info("dictation engine not switched: %s", e)
        self.on_change()
        self._refresh(force=True)

    def _use_dictation(self, name: str) -> None:
        if name in transcribers.REGISTRY:
            self.ctl.set_transcriber(name)
        else:
            self.ctl.set_cleaner(name)

    def on_get_dictation_key(self, row: _KeyRow) -> None:
        url = _engine_class(row.name).key_url
        if url:
            host.open_target(url)

    def on_save_dictation_key(self, row: _KeyRow) -> None:
        if row.saving:
            return
        key = row.field.text()
        if not key.strip():
            row.message = ("Paste your API key first.", "bad")
            self._refresh(force=True)
            return
        row.saving = True
        row.message = ("Checking the key…", "secondary")
        row.spinner.start()
        self._refresh(force=True)

        def check() -> None:
            try:
                error = self.ctl.add_dictation_key(row.name, key)  # one free request, then save
            except Exception as e:  # never leave the spinner turning; the key is not logged
                log.warning("checking a %s key failed: %s", row.name, type(e).__name__)
                error = f"That key didn't work. {e}"
            on_main(self._dictation_saved, row, error)

        threading.Thread(target=check, daemon=True, name="lyssna-key-check").start()

    def _dictation_saved(self, row: _KeyRow, error: str) -> None:
        row.saving = False
        row.spinner.stop()
        if error:
            row.message = (error[:300], "bad")
        else:
            row.field.clear()
            row.message = (f"Saved. Dictation now uses {_engine_class(row.name).vendor} for {row.job}.", "good")
            self.on_change()
        self._refresh(force=True)


def _accessibility_trusted(prompt: bool = False) -> bool:
    """The system's answer: may Lyssna read the selection and watch the keys? Asked only where
    host.NEEDS_ACCESSIBILITY."""
    return bool(host.events.accessibility_trusted(prompt=prompt))


def _globe_action() -> str:
    """The system's Globe key setting ("nothing" is what dictation on fn needs). Asked only
    where host.HAS_GLOBE_KEY."""
    globe = host.globe
    return globe.globe_key_action() if globe is not None else "unknown"


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------


class _FakeModel:
    """A dictation model as the window reads it: its size, whether it is here, its download's error."""

    def __init__(self, total_bytes: int, present: bool, error: str = ""):
        self.total_bytes = total_bytes
        self._present = present
        self.error = error

    def present(self) -> bool:
        return self._present

    def download(self):
        return self


class _FakeController:
    """Just what the setup window reads, with no network and no real engine."""

    def __init__(self, *, provider: str = "kokoro", ready: bool = False, progress: float | None = None,
                 keys: tuple[str, ...] = (), local_only: bool = False, mic: str = "granted", models: bool = False,
                 dict_progress: float | None = None, dict_keys: tuple[str, ...] = (), online: bool = False,
                 hotkey: str = "fn", double_tap: bool | None = None):
        dictation = {"transcriber": "openai" if online else "parakeet", "cleaner": "haiku" if online else "s1-mini",
                     "cleanup": True, "hotkey": hotkey, "hands_free": "space",
                     **({} if double_tap is None else {"double_tap": double_tap})}
        self.cfg = {"provider": provider, "hotkeys": {"speak": "ctrl+alt+s"}, "local_only": local_only, "dictation": dictation}
        self.ready = ready
        self.progress = progress
        self.keys = set(keys)
        self._local_only = local_only
        self.mic = mic
        self.models = models
        self.dict_progress = dict_progress
        self.dict_keys = set(dict_keys)

    def local_only(self) -> bool:
        return self._local_only

    def voice_download_progress(self) -> float | None:
        return self.progress

    def provider_configured(self, name: str) -> bool:
        if name == "kokoro":
            return self.ready
        return name in self.keys or not REGISTRY[name].needs_key

    def set_provider(self, name: str) -> None:
        self.cfg["provider"] = name

    def add_api_key(self, name: str, key: str) -> str:
        return ""

    def speak_text(self, text: str) -> bool:
        return True

    def microphone_status(self) -> str:
        return self.mic

    def request_microphone(self) -> None:
        self.mic = "granted"

    def local_dictation_models(self) -> list:
        from ..dictation import models

        return [_FakeModel(m.total_bytes, self.models) for m in (models.PARAKEET_V2, models.S1_MINI)]

    def dictation_download_progress(self) -> float | None:
        return self.dict_progress

    def use_local_dictation(self) -> None:
        self.cfg["dictation"].update(transcriber="parakeet", cleaner="s1-mini")

    def dictation_key_state(self, name: str) -> bool:
        return name in self.dict_keys

    def add_dictation_key(self, name: str, key: str) -> str:
        return ""

    def set_transcriber(self, name: str) -> None:
        self.cfg["dictation"]["transcriber"] = name

    def set_cleaner(self, name: str) -> None:
        self.cfg["dictation"]["cleaner"] = name

    def current_transcriber(self) -> str:
        name = self.cfg["dictation"]["transcriber"]
        return name if name in self.dict_keys or not transcribers.REGISTRY[name].needs_key else ""

    def current_cleaner(self) -> str:
        name = self.cfg["dictation"]["cleaner"]
        return name if name in self.dict_keys or not cleaners.REGISTRY[name].needs_key else ""


def _key_table(system: str) -> ModuleType:
    """The key names a preview shows: the PC's table anywhere; the Mac's where it can load
    (it needs Quartz), else this computer's own."""
    if system == "pc":
        from ..win import hotkeys as win_hotkeys

        return win_hotkeys
    try:
        from ..mac import hotkeys as mac_hotkeys
    except ImportError:
        return host.hotkey_table
    return mac_hotkeys


def _preview(ctl: _FakeController, *, system: str = "pc", tab: str = LISTEN, engine: str | None = None,
             dictation: str | None = None, trusted: bool = True, taps_ok: bool = True, globe: str = "nothing",
             error: str = "", message: tuple[str, str] | None = None, saving: bool = False, key: str = "",
             dict_saving: int | None = None, dict_message: tuple[int, str, str] | None = None) -> QWidget:
    """A setup window built against `ctl` with the capabilities of `system` ("mac" or "pc")
    forced, never put on screen."""
    from unittest import mock

    mac = system == "mac"
    local = transcribers.REGISTRY[transcribers.LOCAL]
    with mock.patch.object(host, "DEVICE", "Mac" if mac else "PC"), \
            mock.patch.object(host, "MENU_PLACE", "the menu bar" if mac else "the Lyssna icon by the clock"), \
            mock.patch.object(host, "NEEDS_ACCESSIBILITY", mac), mock.patch.object(host, "HAS_GLOBE_KEY", mac), \
            mock.patch.object(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True)), \
            mock.patch.object(local, "can_run", classmethod(lambda cls: True)), \
            mock.patch.object(kokoro.DOWNLOAD, "error", error):
        w = SetupWindow(ctl)
        w.height_limit = lambda: float("inf")  # the whole window in the shot, whatever screen renders it
        w.key_table = _key_table(system)
        w.taps_ok = lambda: taps_ok
        w.accessibility_trusted = lambda prompt=False: trusted
        w.globe_action = lambda: globe
        w._globe = globe
        w.prepare(engine, dictation)
        if engine is None and dictation is None:
            w._select_tab(tab)
        if key:
            w.key_field.setText(key)
        if saving:
            w._saving = True
            w._key_message = ("Checking the key…", "secondary")
            w.spinner.start()
        elif message is not None:
            w._key_message = message
        if dict_saving is not None:
            row = w.key_rows[dict_saving]
            row.field.setText("sk-preview")
            row.saving = True
            row.message = ("Checking the key…", "secondary")
            row.spinner.start()
        if dict_message is not None:
            index, text, tone = dict_message
            w.key_rows[index].message = (text, tone)
        w._refresh(force=True)
    return w


_DONE = {"ready": True, "mic": "granted", "models": True}  # every step done on both tabs

PREVIEWS = {
    # Listen on a PC: no permission step
    "setup.download": lambda: _preview(_FakeController()),
    "setup.downloading": lambda: _preview(_FakeController(progress=0.42)),
    "setup.ready": lambda: _preview(_FakeController(ready=True)),
    "setup.download-error": lambda: _preview(_FakeController(), error="The download stopped. Check your connection."),
    "setup.online": lambda: _preview(_FakeController(ready=True), engine="openai"),
    "setup.checking-key": lambda: _preview(_FakeController(ready=True), engine="openai", saving=True, key="sk-preview"),
    "setup.key-saved": lambda: _preview(_FakeController(ready=True, provider="openai", keys=("openai",)), engine="openai",
                                        message=("Saved. Lyssna now reads with OpenAI TTS.", "good")),
    "setup.local-only": lambda: _preview(_FakeController(ready=True, local_only=True)),
    "setup.shortcuts-warning": lambda: _preview(_FakeController(ready=True), taps_ok=False),
    # Listen on a Mac: the permission step in each of its states
    "setup.mac.not-allowed": lambda: _preview(_FakeController(), system="mac", trusted=False),
    "setup.mac.relaunch": lambda: _preview(_FakeController(ready=True), system="mac", taps_ok=False),
    "setup.mac.ready": lambda: _preview(_FakeController(**_DONE), system="mac"),
    # Dictate on a Mac: the microphone, the Globe key, the models, the keys
    "setup.dictate.mic-undetermined": lambda: _preview(_FakeController(ready=True, mic="undetermined"), system="mac",
                                                       tab=DICTATE),
    "setup.dictate.mic-denied": lambda: _preview(_FakeController(ready=True, mic="denied"), system="mac", tab=DICTATE),
    "setup.dictate.globe": lambda: _preview(_FakeController(ready=True), system="mac", tab=DICTATE, globe="emoji"),
    "setup.dictate.download": lambda: _preview(_FakeController(ready=True), system="mac", tab=DICTATE),
    "setup.dictate.downloading": lambda: _preview(_FakeController(ready=True, dict_progress=0.42), system="mac", tab=DICTATE),
    "setup.dictate.ready": lambda: _preview(_FakeController(**_DONE), system="mac", tab=DICTATE),
    "setup.dictate.online": lambda: _preview(_FakeController(ready=True, models=True, online=True, dict_keys=("openai",)),
                                             system="mac", dictation="haiku", dict_saving=1),
    "setup.dictate.local-only": lambda: _preview(_FakeController(ready=True, local_only=True), system="mac", tab=DICTATE),
    # Dictate on a PC: no Globe key row, the PC's key names
    "setup.pc.dictate": lambda: _preview(_FakeController(ready=True, mic="undetermined", hotkey="right_control"),
                                         tab=DICTATE),
    "setup.pc.dictate-double-tap": lambda: _preview(_FakeController(ready=True, models=True, hotkey="ctrl+win",
                                                                    double_tap=True), tab=DICTATE),
    "setup.pc.dictate-online": lambda: _preview(_FakeController(ready=True, models=True, online=True,
                                                                dict_keys=("openai", "haiku")), dictation="openai",
                                                dict_message=(0, "Saved. Dictation now uses OpenAI for transcription.", "good")),
}
