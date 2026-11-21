"""Small notice panels that sit next to the pill, never part of it: the Windows twins of Toast and
Tip in legacy/ui/toast.py.

Toast: a one-line notice at the bottom centre of the screen ("Transcript cancelled" with Undo,
"Copied. Press Ctrl+V to paste", permission and download messages, errors). At most one action
pill; it fades in, stays a few seconds and fades out, and a new notice replaces the one on
screen (in place when both carry the same key, so a download's percentage counts up without
flickering). Text that does not fit is cut at the end, with the whole of it as the tooltip.

Tip: the pill-shaped tooltip beside the mic button and the hands-free buttons ("Dictate fn",
"Cancel esc"), on the inner side of the screen. It never takes the mouse.

Sizes, timings and the tip's placement come from layout/toast.py, which speaks the Mac's
coordinates (y up). Tip.show_beside takes its anchor in those coordinates too, as the player
passes it, and the toast's `floor` is a Mac-style y; both are converted here with coords. Both
panels are FloatingPanels, so clicking the toast's action never takes focus from the app the
user is typing in. Every colour comes from theme and is read at paint time."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QWidget

from . import coords, theme
from .layout.toast import (
    ACTION_H,
    ACTION_PAD,
    ACTION_RIGHT,
    BOTTOM,
    FADE_IN,
    FADE_OUT,
    MAX_W,
    PAD_L,
    TIP_H,
    TIP_PAD,
    TOAST_H,
    tip_origin,
    toast_width,
)
from .mainthread import later
from .panel import FloatingPanel
from .widgets import HoverView

log = logging.getLogger("lyssna.ui.toast")

TEXT_SIZE = 13.0


def text_width(text: str, font: QFont) -> float:
    return float(QFontMetricsF(font).horizontalAdvance(text))


class _Text(QWidget):
    """One line of text in runs of (text, font), in content(), vertically centred. With `elide`
    the line is cut at the end with an ellipsis when it does not fit."""

    def __init__(self, parent: QWidget, elide: bool = False):
        super().__init__(parent)
        self.runs: list[tuple[str, QFont]] = []
        self.elide = elide
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_runs(self, runs: list[tuple[str, QFont]]) -> None:
        self.runs = runs
        self.update()

    def text(self) -> str:
        return "".join(t for t, _ in self.runs)

    def shown_text(self) -> str:
        """What is drawn: the text, or its start and an ellipsis when cut."""
        if not self.elide or not self.runs:
            return self.text()
        text, font = self.runs[0]
        return QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, float(self.width()))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setPen(theme.content())
        x = 0.0
        h = float(self.height())
        runs = [(self.shown_text(), self.runs[0][1])] if self.elide and self.runs else self.runs
        for text, font in runs:
            p.setFont(font)
            w = text_width(text, font)
            p.drawText(QRectF(x, 0, max(w, self.width() - x), h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
            x += w
        p.end()


class _ActionPill(HoverView):
    """The toast's one action: a soft pill with a semibold label that glows on hover."""

    def __init__(self, parent: QWidget, clicked: Callable[[], None]):
        super().__init__(parent, fill=theme.fill_soft, radius=None)
        self.title = ""
        self.font_ = theme.ui_font(TEXT_SIZE, "semibold")
        self.clicked = clicked
        self.pressed = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_title(self, title: str) -> None:
        self.title = title
        self.update()

    def set_pressed(self, on: bool) -> None:
        self.pressed = bool(on)
        self.update()

    def paint_content(self, p: QPainter) -> None:
        if self.pressed:
            p.fillPath(self.shape_path(), theme.content(0.08))
        p.setPen(theme.content())
        p.setFont(self.font_)
        p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self.title)

    def perform_click(self) -> None:
        self.clicked()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.set_pressed(True)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.pressed:
            self.set_pressed(False)
            if self.rect().contains(ev.position().toPoint()):
                self.perform_click()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class Toast(FloatingPanel):
    width = 200.0
    height = TOAST_H
    corner_radius = TOAST_H / 2
    adaptive = True

    def __init__(self) -> None:
        self._notice = None
        self._key = ""
        self._token = None
        self._on_action: Callable[[], None] | None = None
        super().__init__()
        self.set_alpha(0.0)

    def build(self, content: QWidget) -> None:
        self.text_font = theme.ui_font(TEXT_SIZE, "medium")
        self.text = _Text(content, elide=True)
        self.action = _ActionPill(content, self._act)
        self.action.hide()

    def layout(self, text: str, action: str, max_w: float = MAX_W) -> float:
        """Size and place the text and the action for this notice; returns the toast's width."""
        # Whole pixels, so the window (which Qt keeps on whole pixels) matches the layout.
        action_w = math.ceil(text_width(action, self.action.font_)) if action else 0.0
        text_w = math.ceil(text_width(text, self.text_font)) + 4  # a little slack, as on the Mac
        width = toast_width(text_w, action_w)
        if width > max_w:
            max_w = math.floor(max_w)
            text_w -= width - max_w
            width = max_w
        self.text.set_runs([(text, self.text_font)])
        self.text.setToolTip(text if width >= max_w else "")
        self.text.setGeometry(round(PAD_L), 0, max(0, round(text_w)), round(TOAST_H))
        self.action.setVisible(bool(action))
        if action:
            pill_w = action_w + 2 * ACTION_PAD
            self.action.set_title(action)
            self.action.setGeometry(round(width - ACTION_RIGHT - pill_w), round((TOAST_H - ACTION_H) / 2), round(pill_w),
                                    round(ACTION_H))
        return width

    def present(self, notice, screen=None, floor: float | None = None) -> None:
        """Show a notice (lyssna.controller.Notice) on `screen` (a QScreen; default: the one
        under the mouse). `floor` is the lowest y it may start at, Mac-style (y up), so it sits
        above the pill when that is on the bottom edge."""
        screen = screen or coords.screen_under_mouse()
        vx, vy, vw, vh = coords.visible_frame(screen)
        same = bool(notice.key) and notice.key == self._key and self.visible() and self._token is not None
        width = self.layout(notice.text, notice.action, min(MAX_W, vw - 40))
        x = round(vx + (vw - width) / 2)
        top = vy + vh - BOTTOM - TOAST_H
        if floor is not None:
            top = min(top, coords.primary_height() - floor - TOAST_H)
        self.set_frame((x, round(top), width, TOAST_H))
        self._notice, self._key, self._on_action = notice, notice.key, notice.on_action
        self.action.set_hover(False, animate=False)
        token = object()
        self._token = token
        if not same:
            self.set_alpha(0.0)
            self.order_front()
            self.fade_to(1.0, FADE_IN)
        later(notice.seconds, self._expire, token)

    def _expire(self, token) -> None:
        if token is self._token:
            self.dismiss()

    def dismiss(self) -> None:
        token = object()  # whatever comes next is a different toast
        self._token = token
        self._key = ""

        def gone() -> None:
            if self._token is token:
                self.hide()
                self._token = None
        self.fade_to(0.0, FADE_OUT, gone)

    def _act(self) -> None:
        action = self._on_action
        self.dismiss()
        if action is not None:
            try:
                action()
            except Exception:
                log.exception("toast action failed")


class Tip(FloatingPanel):
    """ "Dictate  fn": the label in medium, the key in bold, on a small surface pill."""

    width = 100.0
    height = TIP_H
    corner_radius = TIP_H / 2
    adaptive = True

    def __init__(self) -> None:
        super().__init__()
        self.set_click_through(True)

    def build(self, content: QWidget) -> None:
        self.text = _Text(content)
        self.text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    @staticmethod
    def measure(text: str, key: str) -> tuple[list[tuple[str, QFont]], float]:
        """The tip's runs (the label in medium, the key in bold) and its width."""
        runs = [(text, theme.ui_font(TEXT_SIZE, "medium"))]
        if key:
            runs.append((f"  {key}", theme.ui_font(TEXT_SIZE, "bold")))
        text_w = math.ceil(sum(text_width(t, f) for t, f in runs))
        return runs, TIP_PAD + text_w + TIP_PAD

    def show_beside(self, anchor: tuple[float, float, float, float], edge: str, text: str, key: str) -> None:
        """Beside `anchor` (x, y, w, h, Mac-style: y up from the primary screen's bottom), on the
        inner side of `edge` ("left", "right" or "bottom")."""
        self.lay_out(anchor, edge, text, key)
        self.order_front()

    def lay_out(self, anchor: tuple[float, float, float, float], edge: str, text: str, key: str) -> None:
        """show_beside without showing: size the tip for this text and place it."""
        runs, width = self.measure(text, key)
        self.text.set_runs(runs)
        self.text.setGeometry(round(TIP_PAD), 0, round(width - 2 * TIP_PAD) + 2, round(TIP_H))
        x, y = tip_origin(anchor, edge, width)
        self.set_frame(coords.qt_rect_from_mac((x, y, width, TIP_H)))


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------


def _preview_toast(text: str, action: str = "", max_w: float = MAX_W) -> QWidget:
    t = Toast()
    width = t.layout(text, action, max_w)
    t.set_frame((0, 0, width, TOAST_H))
    t.set_alpha(1.0)
    return t.panel


def _preview_tips() -> QWidget:
    """A tip beside a left-edge, a right-edge and a bottom-edge button, placed by tip_origin as
    the player places them. The card stands in for the screen (Mac-style y up from its bottom)."""
    from .preview import card
    from .widgets import IconButton

    H = 150.0
    c = card(560, H)
    for edge, bx, by, text, key in (("left", 30.0, 30.0, "Dictate", "fn"), ("right", 496.0, 30.0, "Cancel", "esc"),
                                    ("bottom", 263.0, 86.0, "Finish", "fn")):
        b = IconButton("mic.fill", c, diameter=34, fill=theme.content, icon_size=15, icon_color=theme.surface)
        b.move(round(bx), round(by))
        runs, width = Tip.measure(text, key)
        x, y = tip_origin((bx, H - by - 34, 34.0, 34.0), edge, width)
        pill = _TipView(c, runs, width)
        pill.move(round(x), round(H - y - TIP_H))
    return c


def _preview_tip_panel() -> QWidget:
    tip = Tip()
    tip.lay_out((0.0, 0.0, 34.0, 34.0), "left", "Dictate", "fn")
    return tip.panel


class _TipView(QWidget):
    """A tip drawn as a plain widget, for the preview card (a real Tip is its own window)."""

    def __init__(self, parent: QWidget, runs, width: float):
        super().__init__(parent)
        self.resize(round(width), round(TIP_H))
        self.label = _Text(self)
        self.label.set_runs(runs)
        self.label.setGeometry(round(TIP_PAD), 0, round(width - 2 * TIP_PAD) + 2, round(TIP_H))

    def paintEvent(self, ev) -> None:
        from .widgets import rounded

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillPath(rounded(r, r.height() / 2), theme.surface(0.97))
        p.setPen(theme.hairline())
        p.drawPath(rounded(r, r.height() / 2))
        p.end()


PREVIEWS = {
    "toast": lambda: _preview_toast("Nothing selected"),
    "toast.action": lambda: _preview_toast("Transcript cancelled", "Undo"),
    "toast.long": lambda: _preview_toast("That key didn't work. Cartesia error 401: the API key you sent is not valid for this "
                                         "account, so check it in your dashboard and paste it again.", "Open Settings", 460),
    "toast.download": lambda: _preview_toast("Getting the speech model ready, 43%"),
    "tip": _preview_tips,
    "tip.panel": _preview_tip_panel,
}
