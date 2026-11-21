"""The setup window on Windows, the twin of legacy/ui/setup_window.py: shown on first launch and from
the menu (Setup…). The Mac file is the specification; this one draws the same window in Qt.

What differs from the Mac, on purpose:
  * Reading needs no permission on Windows (there is no Accessibility setting), so Listen has
    two steps, Voice and Try it. When the keyboard shortcuts could not start, one warning row
    with Relaunch Lyssna sits above the steps instead.
  * The first Windows release has no dictation. With features["dictate"] off there is no
    Listen / Dictate switch and the footer speaks only for Listen. The window keeps the tab
    idea (self.tab, the per-tab layout and status) so the Dictate tab can be added beside
    Listen later without a rewrite.
  * Wording that names the computer says "PC" (lyssna.host.DEVICE); key caps use the Windows
    key names (Ctrl, Alt, S).

Everything else is as on the Mac: the two option cards (the free voice on this computer, with
its download, progress, Ready and error; or the user's own key, with the engine, Get a key,
the key field and Save), Read a Sample, a fixed header and footer with only the steps
scrolling, and Done as the primary button, default on Enter. Saving a key makes one real
request to a paid service, so it runs on a worker thread and reports back on the GUI thread;
nothing here waits.

Controls are drawn here rather than left to the system style, whose versions take the blue
accent colour: the radios, the option cards, the engine dropdown, the key field, the step
icons and the key caps. All colours are read at paint time, so the window follows light and
dark mode."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPalette, QPen
from PySide6.QtWidgets import QButtonGroup, QComboBox, QFrame, QLineEdit, QRadioButton, QScrollArea, QWidget

from .. import host
from ..hotkeys import tokens
from ..menu import ONLINE_HEADER, privacy_symbol
from ..providers import REGISTRY, allowed, get_provider, kokoro
from ..win import hotkeys as win_keys
from . import icons, theme
from .layout.setup import (
    CARD_PAD,
    CARD_R,
    CARD_X,
    FIELD_H,
    FOOTER_H,
    ICON,
    INDENT,
    LISTEN,
    MARGIN,
    MARK_H,
    POLL,
    RADIO,
    SAMPLE,
    SUB_W,
    SUB_X,
    TEXT_W,
    TEXT_X,
    W,
    steps_left,
    tab_status,
)
from .mainthread import on_main
from .widgets import BAR_H, PillButton, ProgressBar, rounded
from .window import Hairline, Header, Label, LyssnaWindow, bad, css, good

log = logging.getLogger("lyssna.ui.setup")

TITLE_SIZE = 20.0    # the serif headline
HEADER_TOP = 24.0    # the title bar to the mark
STEPS_TOP = 20.0     # the header to the first step
CARD_RADIUS = 10.0
FIELD_RADIUS = 6.0
MIN_BUTTON_W = 84.0  # Done, Read a Sample, Save, Download, Relaunch: never narrower than this
TONES = {"good": good, "bad": bad}  # an inline note's colour; anything else is dim


def card_border_on() -> QColor:
    """The chosen option card's border."""
    return theme.content(0.45)


# -- controls ----------------------------------------------------------------------------------


class Radio(QRadioButton):
    """A radio in the brand colours: content with a surface dot when chosen, a soft ring when
    not, the title INDENT in so it lines up with the card's text below. A real QRadioButton
    for the keyboard and accessibility."""

    def __init__(self, title: str, parent: QWidget):
        super().__init__(title, parent)
        self.setFont(theme.ui_font(13))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.resize(self.sizeHint())

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        return QSize(round(INDENT + fm.horizontalAdvance(self.text()) + 2), round(max(RADIO, fm.height()) + 2))

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
        p.setFont(self.font())
        p.drawText(QRectF(INDENT, 0, self.width() - INDENT, self.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text())
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
    private". Squeezed narrower than its words, the words end in an ellipsis."""

    H = 16

    def __init__(self, text: str, symbol: str, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.text = ""
        self.symbol = symbol
        self.set(text, symbol)

    def natural_width(self) -> int:
        return round(16 + QFontMetricsF(theme.ui_font(11)).horizontalAdvance(self.text) + 1)

    def set(self, text: str, symbol: str) -> None:
        self.text, self.symbol = text, symbol
        self.resize(self.natural_width(), self.H)
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = theme.content_dim()
        icons.draw_icon(p, self.symbol, QRectF(0, 2, 12, 12), color, "medium")
        font = theme.ui_font(11)
        p.setFont(font)
        p.setPen(color)
        room = max(0, self.width() - 16)
        words = QFontMetricsF(font).elidedText(self.text, Qt.TextElideMode.ElideRight, room)
        p.drawText(QRectF(16, 0, room, self.H), int(Qt.AlignmentFlag.AlignVCenter), words)
        p.end()


def _draw_check_disc(p: QPainter, rect: QRectF, color: QColor) -> None:
    """A filled disc with a check in the surface colour: the twin of checkmark.circle.fill."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(rect)
    s = rect.width() * 0.55
    icons.draw_icon(p, "checkmark", icons.icon_rect(rect.center().x(), rect.center().y(), s), QColor(255, 255, 255), "bold")


class StepIcon(QWidget):
    """A step's number in a ring, or a green check once the step is done."""

    def __init__(self, number: int, parent: QWidget):
        super().__init__(parent)
        self.number = number
        self.done = False
        self.setFixedSize(round(ICON), round(ICON))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_done(self, done: bool) -> None:
        if done != self.done:
            self.done = done
            self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        d = 19.0
        circle = QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d)
        if self.done:
            _draw_check_disc(p, circle, good())
        else:
            p.setPen(QPen(theme.content_dim(), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle.adjusted(0.75, 0.75, -0.75, -0.75))
            p.setFont(theme.ui_font(11, "semibold"))
            p.drawText(circle, int(Qt.AlignmentFlag.AlignCenter), str(self.number))
        p.end()


class StatusRow(QWidget):
    """A small icon and a few words, e.g. a green check and "Ready"."""

    def __init__(self, text: str, parent: QWidget, *, kind: str = "check"):
        super().__init__(parent)
        self.kind = kind
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label = Label(text, self, size=13, weight="medium")
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.label.set_text(text)
        w, h = self.label.fit()
        self.label.move(20, round((18 - h) / 2))
        self.resize(20 + w, 18)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _draw_check_disc(p, QRectF(1, 2, 14, 14), good())
        p.end()


class KeyCaps(QWidget):
    """A shortcut as a row of key caps, e.g. [Ctrl] [Alt] [S]."""

    H, GAP, MIN_W = 24, 4, 24

    def __init__(self, labels: list[str], parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.labels: list[str] = []
        self.set_labels(labels)

    def _cap_widths(self) -> list[float]:
        fm = QFontMetricsF(theme.ui_font(13, "medium"))
        return [max(float(self.MIN_W), round(fm.horizontalAdvance(t)) + 12.0) for t in self.labels]

    def set_labels(self, labels: list[str]) -> None:
        self.labels = list(labels)
        widths = self._cap_widths()
        self.resize(round(sum(widths) + self.GAP * max(0, len(widths) - 1)), self.H)
        self.setAccessibleName(" ".join(self.labels))
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(theme.ui_font(13, "medium"))
        x = 0.0
        for text, w in zip(self.labels, self._cap_widths(), strict=True):
            r = QRectF(x, 0, w, self.H)
            path = rounded(r.adjusted(0.5, 0.5, -0.5, -0.5), 6.0)
            p.fillPath(path, theme.fill_soft())
            p.setPen(QPen(theme.hairline(), 1.0))
            p.drawPath(path)
            p.setPen(theme.content())
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), text)
            x += w + self.GAP
        p.end()


class IconPillButton(PillButton):
    """A secondary pill with a small icon after its title: "Get a key" and its arrow."""

    ICON_SIZE, ICON_GAP = 10.0, 4.0

    def __init__(self, title: str, icon: str, parent: QWidget, *, clicked: Callable[[], None] | None = None):
        self.icon_name = icon
        super().__init__(title, parent, clicked=clicked, focusable=True)

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        return QSize(base.width() + round(self.ICON_SIZE + self.ICON_GAP), base.height())

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded(r, r.height() / 2)
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.fillPath(path, theme.fill_soft())
        p.setPen(QPen(theme.hairline(), 1.0))
        p.drawPath(path)
        if self.isDown():
            p.fillPath(path, theme.content(0.08))
        if self.hasFocus():
            ring = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            p.setPen(QPen(theme.content(0.45), 2.0))
            p.drawPath(rounded(ring, ring.height() / 2))
        fm = QFontMetricsF(self.font())
        tw = fm.horizontalAdvance(self.text())
        x = (self.width() - tw - self.ICON_GAP - self.ICON_SIZE) / 2
        p.setPen(theme.content())
        p.setFont(self.font())
        p.drawText(QRectF(x, 0, tw + 2, self.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text())
        icons.draw_icon(p, self.icon_name, icons.icon_rect(x + tw + self.ICON_GAP + self.ICON_SIZE / 2, self.height() / 2,
                                                             self.ICON_SIZE), theme.content(), "semibold")
        p.end()


class Dropdown(QComboBox):
    """The engine picker: a soft rounded box with the choice and a chevron, the twin of the
    Mac's pop-up button. Its list is styled to match (restyle() after a scheme change)."""

    PAD, CHEVRON = 10.0, 9.0

    def __init__(self, items: list[str], parent: QWidget):
        super().__init__(parent)
        self.setFont(theme.ui_font(13))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.addItems(items)
        self.restyle()
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        widest = max((fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=60.0)
        return QSize(round(widest + 3 * self.PAD + self.CHEVRON), round(FIELD_H))

    def restyle(self) -> None:
        view = self.view()
        view.setStyleSheet(
            f"QAbstractItemView {{ background: {css(theme.surface())}; color: {css(theme.content())};"
            f" border: 1px solid {css(theme.hairline())}; outline: 0; padding: 4px;"
            f" selection-background-color: {css(theme.content(0.10))}; selection-color: {css(theme.content())}; }}")
        view.setFont(self.font())

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded(r, FIELD_RADIUS)
        p.fillPath(path, theme.fill_soft())
        p.setPen(QPen(theme.content(0.45) if self.hasFocus() else theme.hairline(), 1.0))
        p.drawPath(path)
        p.setPen(theme.content())
        p.setFont(self.font())
        p.drawText(QRectF(self.PAD, 0, self.width() - 2 * self.PAD - self.CHEVRON, self.height()),
                   int(Qt.AlignmentFlag.AlignVCenter), self.currentText())
        cx = self.width() - self.PAD - self.CHEVRON / 2
        icons.draw_icon(p, "chevron.down", icons.icon_rect(cx, self.height() / 2, self.CHEVRON), theme.content_dim(), "bold")
        p.end()


class KeyField(QLineEdit):
    """The API key field: hidden characters in a hairline box with a radius of 6, on the surface."""

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
        # shows its overlapping corner segments as dots.
        self.setStyleSheet(
            f"QLineEdit {{ background: {css(theme.surface())}; color: {css(theme.content())};"
            f" border: 1px solid {css(_on_surface(theme.hairline()))}; border-radius: {round(FIELD_RADIUS)}px; padding: 0 7px;"
            f" selection-background-color: {css(theme.content(0.25))}; selection-color: {css(theme.content())}; }}"
            f" QLineEdit:focus {{ border-color: {css(_on_surface(theme.content(0.45)))}; }}"
            f" QLineEdit:disabled {{ color: {css(theme.content_dim())}; }}")
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, theme.content(0.40))
        self.setPalette(pal)


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

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(theme.content(0.15), 2.0))
        p.drawEllipse(r)
        pen = QPen(theme.content_dim(), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, -self.angle * 16, 100 * 16)
        p.end()


def _pill(title: str, parent: QWidget, clicked: Callable[[], None], primary: bool = False) -> PillButton:
    """A focusable pill, never narrower than MIN_BUTTON_W."""
    b = PillButton(title, parent, primary=primary, clicked=clicked, focusable=True)
    _min_width(b)
    return b


def _min_width(b: PillButton) -> None:
    hint = b.sizeHint()
    b.setFixedSize(max(hint.width(), round(MIN_BUTTON_W)), hint.height())


def footer_text(left: dict[str, int]) -> str:
    """The footer's line for the tabs this release has, e.g. "Listen ready" or
    "Listen: 1 step left" (with Dictate too, the Mac's shared footer_status wording)."""
    names = {LISTEN: "Listen"}
    parts = []
    for tab, n in left.items():
        name = names.get(tab, tab.capitalize())
        parts.append(f"{name} ready" if not n else f"{name}: {tab_status(n)}")
    return " · ".join(parts)


# -- the window --------------------------------------------------------------------------------


class SetupWindow(LyssnaWindow):
    """Set `ctl`, `taps_ok` (did the keyboard shortcuts start?), `relaunch` and `on_change` (the
    menu refreshes) after construction, then call show(). `features["dictate"]` stays False in
    the first Windows release: Listen is then the only tab and there is no switch."""

    def __init__(self, ctl=None):
        super().__init__("Lyssna Setup")
        self.ctl = ctl
        self.taps_ok: Callable[[], bool] = lambda: True
        self.relaunch: Callable[[], None] = lambda: None
        self.on_change: Callable[[], None] = lambda: None
        self.features: dict = {"dictate": False}
        self.built = False
        self.tab = LISTEN  # the tab on show; the only one until the Dictate tab is added
        self.tabs = (LISTEN,)
        self._saving = False
        self._key_message = ("", "secondary")  # inline note under the key field: (text, tone)
        self._shape: dict | None = None  # what was visible at the last layout
        self._left: dict[str, int] = {LISTEN: 0}
        self._visible: set[QWidget] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(round(POLL * 1000))
        self._timer.timeout.connect(self.tick)

    # -- showing -----------------------------------------------------------------------------

    def show(self, engine: str | None = None, dictation: str | None = None) -> None:
        """Bring the window up (building it the first time). `engine` opens it with that voice
        engine preselected under "Online, with your API key", for the menu's key entries, and
        the cursor in the key field. `dictation` is for the Dictate tab, not in this release."""
        self.prepare(engine, dictation)
        self.show_centered()
        if engine is not None and self.key_field.isEnabled() and not self.key_field.isHidden():
            self.key_field.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.scroll_area.verticalScrollBar().setValue(0)
            self.done.setFocus(Qt.FocusReason.OtherFocusReason)
        self._timer.start()

    def prepare(self, engine: str | None = None, dictation: str | None = None) -> None:
        """Everything show() does short of putting the window on screen: build it if needed,
        point the choices and read the state afresh."""
        if not self.built:
            self._build()
        self._choose_radio(engine)
        self._refresh(force=True)

    def hideEvent(self, ev) -> None:
        self._timer.stop()
        super().hideEvent(ev)

    def tick(self) -> None:
        self._refresh()

    # -- building ----------------------------------------------------------------------------

    def _build(self) -> None:
        self.built = True
        self.device = host.DEVICE  # wording that names the computer: "Mac" or "PC"
        place = host.MENU_PLACE
        # D5 adds DICTATE here (and its switch in the header) when features["dictate"] is on.
        self.tabs = (LISTEN,)

        self.header = QWidget(self)
        self.heading = Header("Welcome to Lyssna", self.header, mark_h=MARK_H, title_size=TITLE_SIZE,
                              subtitle=f"Lyssna reads text aloud. Two quick steps and you are set. "
                                       f"You can come back here any time from {place}.")
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

        self.footer = QWidget(self)
        self.footer_line = Hairline(self.footer)
        self.footer_status = Label("", self.footer, size=12, color=theme.content_dim)
        self.done = _pill("Done", self.footer, self.close, primary=True)

        # The option cards go in first, so they sit behind the controls laid over them.
        self.card_local = OptionCard(c)
        self.card_key = OptionCard(c)

        # Above the steps, only when the keyboard shortcuts could not start
        self.warn_icon = WarningIcon(c)
        self.warn_text = Label("Lyssna could not start its keyboard shortcuts. Quit other tools that capture "
                               "the keyboard, then relaunch.", c, size=13, wrap=True)
        self.relaunch_btn = _pill("Relaunch Lyssna", c, lambda: self.relaunch())
        self.sep0 = Hairline(c)

        # 1 Voice
        self.icon1 = StepIcon(1, c)
        self.head1 = Label("Voice", c, size=14, weight="semibold")
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
        self.sep1 = Hairline(c)

        self.voice_group = QButtonGroup(self)
        self.voice_group.setExclusive(True)
        for radio in (self.radio_local, self.radio_key):
            self.voice_group.addButton(radio)
            radio.clicked.connect(lambda _checked=False, r=radio: self.on_choose_voice(r))
        self.radio_local.setChecked(True)

        # 2 Try it
        self.icon2 = StepIcon(2, c)
        self.head2 = Label("Try it", c, size=14, weight="semibold")
        self.try_text = Label("Select text in any app and press", c, size=13, color=theme.content_dim)
        self.keycap = KeyCaps(self._key_names(self.ctl.cfg["hotkeys"]["speak"]), c)
        self.sample = _pill("Read a Sample", c, self.on_sample)

        self.card_local.lower()
        self.card_key.lower()
        self.restyle()

    @staticmethod
    def _key_names(spec: str) -> list[str]:
        """One cap per key, by its Windows name: "ctrl+alt+s" -> ["Ctrl", "Alt", "S"]."""
        return [win_keys.pretty([part]) for part in tokens(spec)]

    def restyle(self) -> None:
        if not self.built:
            return
        self.popup.restyle()
        self.key_field.restyle()
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

    def _refresh(self, force: bool = False) -> None:
        """Read the live state (shortcuts, download, keys) and show it."""
        ctl = self.ctl
        taps_ok = bool(self.taps_ok())

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
        self.download.setText("Try Again" if error and can_run else "Download")
        _min_width(self.download)

        provider = ctl.cfg.get("provider", "")
        voice_done = provider in REGISTRY and ctl.provider_configured(provider)
        self.icon1.set_done(voice_done)
        self.icon2.set_done(False)

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

        # The shortcuts are not a step on Windows, but Listen is not ready while they are down.
        self._left = {LISTEN: steps_left((taps_ok, voice_done))}
        self.footer_status.set_text(footer_text(self._left))

        local_chosen = self.radio_local.isChecked()
        shape = {
            "tab": self.tab,
            "left": tuple(self._left.values()),
            "warning": not taps_ok,
            "local_body": local_chosen,
            "download": local_chosen and progress is None and not ready and can_run,
            "progress": local_chosen and progress is not None,
            "ready": local_chosen and ready,
            "local_error": local_chosen and bool(error),
            "radio_key": not local_only,
            "key_body": not local_only and not local_chosen,
            "key_status": not local_only and not local_chosen and bool(text),
            "local_only_note": local_only,
            "texts": (error, text, self._saving, self.key_tag.text, self.download.text()),
        }
        if force or shape != self._shape:
            self._shape = shape
            self._layout(shape)

    # -- layout ------------------------------------------------------------------------------
    #
    # Explicit geometry, top to bottom, as on the Mac. The helpers below place a view and note
    # it as visible; whatever in the steps was not placed is hidden afterwards.

    def put(self, view: QWidget, x: float, y: float, width: float | None = None) -> float:
        if isinstance(view, Label):
            _, h = view.fit(width)
        else:
            h = view.height()
        view.move(round(x), round(y))
        self._visible.add(view)
        return h

    def centre_row(self, views_x: list[tuple[QWidget, float]], y: float) -> float:
        """Place views on one line, vertically centred on the tallest; returns the line height.
        The views keep the size they have (fit labels first)."""
        h =max(v.height() for v, _ in views_x)
        for v, x in views_x:
            v.move(round(x), round(y + (h - v.height()) / 2))
            self._visible.add(v)
        return h

    def _step(self, icon: StepIcon, head: Label, y: float) -> float:
        hh = self.put(head, TEXT_X, y)
        icon.move(round(MARGIN), round(y + (hh - ICON) / 2))
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

    def _layout(self, show: dict) -> None:
        """Lay out the current tab's steps, then fit the window to header, steps and footer.
        Only the steps scroll when the screen is too short."""
        self._visible = set()
        y = STEPS_TOP
        if self.tab == LISTEN:
            y = self._layout_listen(show, y)
        y += 20
        for view in self.content.children():
            if isinstance(view, QWidget):
                view.setVisible(view in self._visible)
        self.content.setFixedSize(round(W), round(y))

        head_h = self._layout_header()
        h = self.fit_height(W, head_h + y + FOOTER_H)
        body_h = h - head_h - FOOTER_H
        self.header.setGeometry(0, 0, round(W), round(head_h))
        self.header_line.setVisible(y > body_h)  # a line under the header only while the steps scroll under it
        self.scroll_area.setGeometry(0, round(head_h), round(W), round(body_h))
        self._layout_footer(h)

    def _layout_header(self) -> float:
        """The mark, the welcome and the sentence under it; returns the header's height."""
        hh = self.heading.fit(W - 2 * MARGIN)
        self.heading.move(round(MARGIN), round(HEADER_TOP))
        y = HEADER_TOP + hh + 4
        self.header_line.setGeometry(0, round(y), round(W), 1)
        return y + 1

    def _layout_footer(self, h: float) -> None:
        """A hairline, the status line on the left and Done on the right."""
        self.footer.setGeometry(0, round(h - FOOTER_H), round(W), round(FOOTER_H))
        self.footer_line.setGeometry(0, 0, round(W), 1)
        self.done.move(round(W - 20 - self.done.width()), round((FOOTER_H - self.done.height()) / 2))
        _, sh = self.footer_status.fit()
        self.footer_status.move(round(MARGIN), round((FOOTER_H - sh) / 2))

    def _layout_listen(self, show: dict, y: float) -> float:
        """Listen's steps (and the shortcuts warning above them), from y down; returns where they end."""
        put, centre_row = self.put, self.centre_row

        if show["warning"]:
            th = put(self.warn_text, TEXT_X, y, TEXT_W)
            self.warn_icon.move(round(MARGIN + (ICON - self.warn_icon.width()) / 2), round(y + 1))
            self._visible.add(self.warn_icon)
            y += th + 10
            y += centre_row([(self.relaunch_btn, TEXT_X)], y)
            y = self._sep(self.sep0, y)

        # 1 Voice: two option cards
        y += self._step(self.icon1, self.head1, y) + 10
        top = y
        y += CARD_PAD + self._option_head(self.radio_local, self.local_tag, y + CARD_PAD) + 4
        y += put(self.local_note, SUB_X, y, SUB_W)
        if show["local_body"]:
            if show["download"]:
                y += 10 + centre_row([(self.download, SUB_X)], y + 10)
            elif show["progress"]:
                text = self.progress_text.value()
                self.progress_text.set_text("100%")  # measure the widest it gets
                tw, th = self.progress_text.fit()
                self.progress_text.set_text(text)
                self.progress_text.resize(tw, th)
                self.progress.resize(round(SUB_W - tw - 10), round(BAR_H))
                y += 10 + centre_row([(self.progress, SUB_X), (self.progress_text, SUB_X + SUB_W - tw)], y + 10)
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
                y += centre_row([(self.popup, SUB_X), (self.get_key, SUB_X + self.popup.width() + 10)], y) + 8
                save_w = self.save.width()
                self.key_field.setFixedWidth(round(SUB_W - save_w - 8))  # the Save pill's edge ends the row
                y += centre_row([(self.key_field, SUB_X), (self.save, CARD_R - save_w)], y)
                if show["key_status"]:
                    shift = 22.0 if self._saving else 0.0
                    h = put(self.key_status, SUB_X + shift, y + 8, SUB_W - shift)
                    if self._saving:
                        self.spinner.move(round(SUB_X), round(y + 8 + (h - Spinner.SIZE) / 2))
                        self._visible.add(self.spinner)
                    y += 8 + h
            y += CARD_PAD
            self._place_card(self.card_key, top, y, not show["local_body"])
        if show["local_only_note"]:
            y += 8 + put(self.local_only_note, TEXT_X, y + 8, TEXT_W)
        y = self._sep(self.sep1, y)

        # 2 Try it
        y += self._step(self.icon2, self.head2, y) + 8
        tw, _ = self.try_text.fit()
        y += centre_row([(self.try_text, TEXT_X), (self.keycap, TEXT_X + tw + 8)], y) + 10
        y += centre_row([(self.sample, TEXT_X)], y)
        return y

    # -- actions -----------------------------------------------------------------------------

    def keyPressEvent(self, ev) -> None:
        """Enter is Done, as the Mac's default button, except in the key field (Enter saves there)."""
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.focusWidget() is not self.key_field:
            self.done.click()
            ev.accept()
            return
        super().keyPressEvent(ev)

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


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------


class _FakeController:
    """Just what the setup window reads, with no network and no real engine."""

    def __init__(self, *, provider: str = "kokoro", ready: bool = False, progress: float | None = None,
                 keys: tuple[str, ...] = (), local_only: bool = False):
        self.cfg = {"provider": provider, "hotkeys": {"speak": "ctrl+alt+s"}, "local_only": local_only}
        self.ready = ready
        self.progress = progress
        self.keys = set(keys)
        self._local_only = local_only

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


def _preview(ctl: _FakeController, *, engine: str | None = None, taps_ok: bool = True, error: str = "",
             message: tuple[str, str] | None = None, saving: bool = False, key: str = "") -> QWidget:
    """A setup window built against `ctl`, worded for a PC, never put on screen."""
    from unittest import mock

    with mock.patch.object(host, "DEVICE", "PC"), mock.patch.object(host, "MENU_PLACE", "the Lyssna icon by the clock"), \
            mock.patch.object(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True)), \
            mock.patch.object(kokoro.DOWNLOAD, "error", error):
        w = SetupWindow(ctl)
        w.taps_ok = lambda: taps_ok
        w.prepare(engine)
        if key:
            w.key_field.setText(key)
        if saving:
            w._saving = True
            w._key_message = ("Checking the key…", "secondary")
            w.spinner.start()
        elif message is not None:
            w._key_message = message
        w._refresh(force=True)
    return w


PREVIEWS = {
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
}
