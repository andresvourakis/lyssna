"""The History window, the twin of legacy/ui/history_window.py: what you dictated, and the
dictionary Lyssna uses to spell names. The Mac file is the specification; this one draws the
same window in Qt.

    History                                           (mark + serif headline)
    1,204          18,930          412                 words this week, in total, dictations
    Lyssna is free and made by one person. ♥ Support it   (past 1,000 words; see lyssna.support)
    ( Dictations | Dictionary )                        one segmented control
    Dictations: search, then the list grouped by day; each row copies, shows the original
                transcript, takes a fix ("Fix a Word…", which teaches the dictionary) or goes
    Dictionary: add a word, the words most used first, where each came from
    Kept for 30 days on this Mac                 Clear History…

It reads the history store and the vocabulary, both of which can change while it is open (a
dictation lands, the edit watcher learns a word, `lyssna vocab` runs), so it polls their files
twice a second while it is open and redraws only when something changed. A fix being typed is
never swept away by such a change: the list is rebuilt once the fix is saved or cancelled.

The list is a scroll area of one widget per row, at most ROW_CAP of them (Show Older adds the
next ROW_CAP), as on the Mac. Why not a list view with a delegate: rows hold live controls (the
hover icons, the fix field with Save and Cancel, Show Older), which a delegate can only fake,
and the cap already bounds the count. What keeps hundreds of rows smooth is that a row is not
a stack of QLabels: it paints its own text from a QTextLayout made once per width (rich-text
labels would lay out an HTML document per label on every resize), and only the two hover
icons are child widgets. Qt paints only the rows in view.

Wording that names the computer or its dictation key follows host.DEVICE and the configured
key (menu.key_name), so a PC reads "on this PC". Every measurement, string and rule is
layout/history.py's, shared with the Mac window. Dictated text is shown here and never logged."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QCursor, QFontMetricsF, QGuiApplication, QPainter, QPen, QTextLayout
from PySide6.QtWidgets import QFrame, QLineEdit, QMenu, QMessageBox, QScrollArea, QTextEdit, QWidget

from .. import config, host, support
from ..dictation import history as history_mod
from ..dictation import vocabulary as vocabulary_mod
from ..dictation.learn import find_corrections
from ..menu import key_name
from . import coords, theme
from .icons import draw_icon, icon_rect
from .layout import mark as mark_layout
from .layout.history import (
    BUTTON_GAP,
    COPIED_FOR,
    EDIT_TO_BUTTONS,
    EMPTY_DICTIONARY,
    EMPTY_MARK_ALPHA,
    EMPTY_MARK_GAP,
    EMPTY_MARK_H,
    EMPTY_MAX_LINES,
    EMPTY_SEARCH,
    EMPTY_SIZE,
    EMPTY_STEP,
    EMPTY_W,
    FIELD_H,
    FIELD_PAD,
    FIELD_RADIUS,
    FIELD_SIZE,
    FIELD_TO_LIST,
    FOCUS_ALPHA,
    FOOTER_H,
    FOOTER_SIZE,
    HEAD_TO_STATS,
    HEADER_ABOVE,
    HEADER_ABOVE_FIRST,
    HEADER_BELOW,
    HEADER_SIZE,
    HEARD_GAP,
    HEARD_SIZE,
    ICON_BTN,
    ICON_GAP,
    ICON_SIZE,
    ICON_SIZE_SMALL,
    ICONS_TO_TEXT,
    LABEL_INSET,
    LIST_BOTTOM,
    LIST_TOP,
    MARK_GAP,
    MARK_H,
    MIN_H,
    MIN_W,
    NOTE_GAP,
    NOTE_SIZE,
    NOTE_TOP,
    PILL_ABOVE,
    POLL,
    ROW_CAP,
    ROW_GAP,
    ROW_PAD_X,
    ROW_PAD_Y,
    ROW_RADIUS,
    SEARCH_ICON,
    SEARCH_ICON_GAP,
    SEG_H,
    SEG_INSET,
    SEG_PAD,
    SEG_SIZE,
    SEG_SLIDE,
    SMALL_PILL_H,
    SOURCE_BADGE,
    SOURCE_SIZE,
    SOURCE_TO_ICON,
    STAT_LABEL_SIZE,
    STAT_SIZE,
    STATS_TO_SUPPORT,
    STATS_TO_TABS,
    SUPPORT_GAP,
    SUPPORT_SIZE,
    TABS,
    TABS_TO_FIELD,
    TERM_SIZE,
    TEXT_LINE_H,
    TEXT_LINES,
    TEXT_SIZE,
    TIME_SIZE,
    TIME_W,
    TITLE_SIZE,
    TOP,
    H,
    W,
    column,
    day_label,
    ease_out,
    empty_dictations,
    figure,
    learned_text,
    retention_text,
)
from .mark import draw_mark
from .widgets import IconButton, PillButton, rounded
from .window import Hairline, Header, Label, LyssnaWindow, TextButton, css, ink_left, place, selection_style

log = logging.getLogger("lyssna.ui.history")

SEARCH_ICON_BOX = 16.0  # the magnifier's box in the search field (the Mac's image view)
ADD_MIN_W = 64.0        # the Add pill is never narrower than this


# -- small pure helpers and the seams tests patch -------------------------------------------------


def copy_to_clipboard(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def later(delay: float, fn: Callable[[], None], context: QWidget) -> None:
    """Call fn on the GUI thread after `delay` seconds, unless `context` is gone by then."""
    QTimer.singleShot(round(delay * 1000), context, fn)


def time_label(when: datetime) -> str:
    """The time of day in the user's own 12 or 24 hour style, from the system's own formatter
    (host.appearance.short_time): on a Mac, Qt's system locale misses the region's 24-hour clock."""
    return host.appearance.short_time(when)


# -- painted text ---------------------------------------------------------------------------------


class _Text:
    """One paragraph laid out for a width and painted by its row: the twin of the Mac's
    NSTextField labels, without a widget each. The layout is kept until the width changes.
    With `line_h` every line has that fixed height and the glyphs sit at its bottom, as the
    Mac's fixed paragraph line height puts them."""

    def __init__(self, text: str, font, color: Callable, line_h: float | None = None, center: bool = False):
        self.value = text
        self._text = text.replace("\r\n", "\n").replace("\n", " ")  # one paragraph, as the rows always showed it
        self.font = font
        self.color = color
        self.line_h = line_h
        self.center = center
        self._width: float | None = None
        self._lines: list[str] = []
        self._baselines: list[float] = []  # each line's baseline from the paragraph's top
        self._steps: list[float] = []  # each line's bottom from the paragraph's top
        self._clamped: dict[int, list[str]] = {}

    def _lay(self, width: float) -> list[str]:
        """The paragraph's lines on lines `width` wide (theme.break_lines: AppKit's on the Mac),
        with their baselines: each line `line_h` tall (else the font's line height) with its
        glyphs at the bottom, where the Mac's fixed paragraph line height puts them: the
        baseline the descent, rounded up to whole points, above the line's foot (TextKit's rule;
        16 in a 19 point line at 14 pt, where Qt's own line metrics would give 15.5)."""
        if self._width == width:
            return self._lines
        lines = theme.break_lines(self._text, self.font, max(1.0, width)) if self._text else []
        y, steps, baselines = 0.0, [], []
        for text in lines:
            ascent, descent, height = self._metrics(text)
            step = self.line_h or height
            baselines.append(y + step - math.ceil(round(descent, 3)) if self.line_h else y + ascent)
            y += step
            steps.append(y)
        self._width, self._lines, self._steps, self._baselines, self._clamped = width, lines, steps, baselines, {}
        return lines

    def _metrics(self, text: str) -> tuple[float, float, float]:
        """A line's ascent, descent and height as Qt's text layout gives them (a fallback font
        for an emoji can make a line taller)."""
        layout = QTextLayout(text or " ", self.font)
        layout.beginLayout()
        line = layout.createLine()
        layout.endLayout()
        return line.ascent(), line.descent(), line.height()

    def lines(self, width: float) -> int:
        return len(self._lay(width))

    def height(self, width: float, max_lines: int = 0) -> float:
        self._lay(width)
        if not self._steps:
            return 0.0
        n = min(len(self._steps), max_lines) if max_lines else len(self._steps)
        return self._steps[n - 1]

    def natural_width(self) -> float:
        return theme.text_width(self._text, self.font)

    def first_baseline(self, width: float) -> float:
        self._lay(width)
        if not self._baselines:
            return QFontMetricsF(self.font).ascent()
        return self._baselines[0]

    def paint(self, p: QPainter, x: float, y: float, width: float, max_lines: int = 0) -> None:
        """Draw at (x, y), wrapped to `width`. With `max_lines`, the last line shown ends in an
        ellipsis when more text follows, cut where the Mac's truncatesLastVisibleLine cuts it."""
        lines = full = self._lay(width)
        if max_lines and len(lines) > max_lines:
            if max_lines not in self._clamped:
                self._clamped[max_lines] = theme.break_lines(self._text, self.font, max(1.0, width), max_lines)
            lines = self._clamped[max_lines]
        p.setPen(self.color())
        for i, (text, baseline) in enumerate(zip(lines, self._baselines, strict=False)):
            cut = text != full[i]
            shown = min(theme.text_width(text, self.font), width)
            left = x + (width - shown) / 2 if self.center else x
            theme.draw_text_at(p, left, y + baseline, text, self.font, width, cut)


def _wrap(width: float) -> float:
    """The width text wraps to inside a label `width` wide: the Mac's label cell keeps its text
    LABEL_INSET in from each side, so lines break where they break on the Mac."""
    return max(1.0, width - 2 * LABEL_INSET)


# -- controls -------------------------------------------------------------------------------------


class Segments(QWidget):
    """Two tabs in one track: a filled capsule marks the chosen one and slides to the other in
    SEG_SLIDE seconds, easing out. `on_change(index)` runs when the user picks a tab."""

    def __init__(self, titles: list[str], on_change: Callable[[int], None], parent: QWidget):
        super().__init__(parent)
        self.titles = titles
        self.on_change = on_change
        self.font_ = theme.ui_font(SEG_SIZE, "medium")
        self.widths = [round(theme.text_width(t, self.font_)) + 2 * SEG_PAD for t in titles]
        self.lefts = [sum(self.widths[:i]) for i in range(len(self.widths))]
        self.selected = 0
        self.pos_ = (self.lefts[0], self.widths[0])  # the capsule as drawn: (x, width)
        self.anim: QVariantAnimation | None = None
        self.setFixedSize(round(sum(self.widths)), round(SEG_H))
        self.setAccessibleName(" | ".join(titles))

    def select(self, index: int, animated: bool = True) -> None:
        if index == self.selected and self.anim is None:
            return
        self.selected = index
        target = (self.lefts[index], self.widths[index])
        self._stop()
        if not animated or not self.isVisible() or theme.reduce_motion():
            self.pos_ = target
            self.update()
            return
        start = self.pos_
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(round(SEG_SLIDE * 1000))

        def step(t) -> None:
            k = ease_out(float(t))
            self.pos_ = (start[0] + (target[0] - start[0]) * k, start[1] + (target[1] - start[1]) * k)
            self.update()

        anim.valueChanged.connect(step)
        anim.finished.connect(self._finished)
        self.anim = anim
        anim.start()

    def _finished(self) -> None:
        self.anim = None
        self.pos_ = (self.lefts[self.selected], self.widths[self.selected])
        self.update()

    def _stop(self) -> None:
        if self.anim is not None:
            self.anim.stop()
            self.anim = None

    def mousePressEvent(self, ev) -> None:
        x = ev.position().x()
        for i, (left, w) in enumerate(zip(self.lefts, self.widths, strict=True)):
            if left <= x < left + w and i != self.selected:
                self.select(i)
                self.on_change(i)
                break
        ev.accept()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillPath(rounded(QRectF(self.rect()), SEG_H / 2), theme.fill_soft())
        x, w = self.pos_
        cap = QRectF(x + SEG_INSET, SEG_INSET, w - 2 * SEG_INSET, SEG_H - 2 * SEG_INSET)
        p.fillPath(rounded(cap, cap.height() / 2), theme.content())
        for i, title in enumerate(self.titles):
            p.setPen(theme.surface() if i == self.selected else theme.content())
            theme.draw_text(p, QRectF(self.lefts[i], 0, self.widths[i], SEG_H), Qt.AlignmentFlag.AlignCenter, title, self.font_)
        p.end()


class FieldBox(QWidget):
    """The filled, rounded backing of a text field, with a hairline while the field has focus,
    and optionally the magnifier at its left (the search field)."""

    def __init__(self, parent: QWidget, icon: str = ""):
        super().__init__(parent)
        self.icon = icon
        self.focused = False
        self.resize(200, round(FIELD_H))

    def set_focused(self, on: bool) -> None:
        if self.focused != on:
            self.focused = on
            self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        p.fillPath(rounded(r, FIELD_RADIUS), theme.fill_soft())
        if self.focused:
            p.setPen(QPen(theme.content(FOCUS_ALPHA), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(rounded(r.adjusted(0.5, 0.5, -0.5, -0.5), FIELD_RADIUS - 0.5))
        if self.icon:
            draw_icon(p, self.icon, icon_rect(FIELD_PAD + SEARCH_ICON_BOX / 2, FIELD_H / 2, SEARCH_ICON), theme.content_dim(),
                      "regular")
        p.end()


class LineField(QLineEdit):
    """A borderless one-line field that lights up its box's focus hairline while it has focus.
    The system's frame and focus ring would take the blue accent; the box draws both."""

    def __init__(self, placeholder: str, box: FieldBox):
        super().__init__(box)
        self.box = box
        self.setFrame(False)
        self.setFont(theme.ui_font(FIELD_SIZE))
        self.setPlaceholderText(placeholder)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.restyle()

    def restyle(self) -> None:
        # The hint in the dim colour, as the Mac's placeholder string is; in the style sheet, because
        # a style sheet that sets `color` replaces the palette's placeholder with it at half alpha.
        self.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; padding: 0; color: {css(theme.content())};"
            f" placeholder-text-color: {css(theme.content_dim())}; }}" + selection_style("QLineEdit"))

    def focusInEvent(self, ev) -> None:
        self.box.set_focused(True)
        super().focusInEvent(ev)

    def focusOutEvent(self, ev) -> None:
        self.box.set_focused(False)
        super().focusOutEvent(ev)


class FixField(QTextEdit):
    """The multi-line field a fix is typed in. Return saves and Escape cancels (the Mac's
    insertNewline: and cancelOperation:); it grows with its text."""

    def __init__(self, box: FieldBox, on_save: Callable[[], None], on_cancel: Callable[[], None],
                 on_change: Callable[[], None]):
        super().__init__(box)
        self.box = box
        self.on_save, self.on_cancel = on_save, on_cancel
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setFont(theme.ui_font(TEXT_SIZE))
        self.document().setDocumentMargin(0)
        self.viewport().setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.restyle()
        self.textChanged.connect(on_change)

    def restyle(self) -> None:
        self.setStyleSheet(
            f"QTextEdit {{ background: transparent; border: none; color: {css(theme.content())}; }}"
            + selection_style("QTextEdit"))

    def text(self) -> str:
        return self.toPlainText()

    def wanted_height(self, width: float) -> float:
        """Its lines at `width`, each the font's height rounded up to whole pixels: the Mac's field
        cell is 17 a line for 14 px text where Qt's document would be 16.5, a pixel short over two."""
        doc = self.document().clone()
        doc.setTextWidth(width)
        doc.documentLayout().documentSize()  # lays the document out
        lines, block = 0, doc.begin()
        while block.isValid():
            lines += max(1, block.layout().lineCount())
            block = block.next()
        return max(1, lines) * math.ceil(QFontMetricsF(self.font()).height())

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            ev.accept()
            self.on_save()
            return
        if ev.key() == Qt.Key.Key_Escape:
            ev.accept()
            self.on_cancel()
            return
        super().keyPressEvent(ev)

    def focusInEvent(self, ev) -> None:
        self.box.set_focused(True)
        super().focusInEvent(ev)

    def focusOutEvent(self, ev) -> None:
        self.box.set_focused(False)
        super().focusOutEvent(ev)


class RowIcon(IconButton):
    """A symbol in a 28 pt circle: dim at rest, full colour on a soft disc under the cursor
    (the Mac's HistoryIconButton). The hover shows at once, as on the Mac."""

    def __init__(self, icon: str, size: float, tip: str, clicked: Callable[[], None], parent: QWidget):
        super().__init__(icon, parent, diameter=ICON_BTN, icon_size=size, icon_color=theme.content_dim, weight="regular",
                         glow=theme.fill_soft, tooltip=tip, clicked=clicked)
        self.setAccessibleName(tip)

    def set_hover(self, on: bool, animate: bool = False) -> None:
        super().set_hover(on, animate=False)
        self.icon_color = theme.content if self.hovering() else theme.content_dim
        self.update()


def menu_style() -> str:
    """The More menu in the brand colours: the system's menu highlight would take the blue accent."""
    return (f"QMenu {{ background: {css(theme.surface())}; color: {css(theme.content())};"
            f" border: 1px solid {css(theme.hairline())}; border-radius: 6px; padding: 4px; }}"
            f" QMenu::item {{ padding: 4px 20px 4px 12px; border-radius: 4px; }}"
            f" QMenu::item:selected {{ background: {css(theme.content(0.10))}; color: {css(theme.content())}; }}"
            f" QMenu::item:disabled {{ color: {css(theme.content(0.30))}; }}")


def _pill(title: str, parent: QWidget, clicked: Callable[[], None], primary: bool = False,
          height: float | None = None) -> PillButton:
    b = PillButton(title, parent, primary=primary, clicked=clicked, focusable=True)
    if height is not None:
        b.setFixedSize(b.width(), round(height))
    return b


# -- list items: each is one widget that lays itself out for a width ------------------------------
#
# An item spans the content column plus ROW_PAD_X on both sides (the hover tint reaches into
# the margin), and its content starts ROW_PAD_X in, on the column's edge.


class Item(QWidget):
    kind = ""
    # How far below its whole-pixel place the Mac's view of this item sits (a row above it may
    # be a half point tall): what the item paints itself is moved down by it.
    paint_dy = 0.0

    def painter(self) -> QPainter:
        """A painter on the item, moved to the Mac's fractional place (paint_dy)."""
        p = QPainter(self)
        p.translate(0.0, self.paint_dy)
        return p

    def layout_for(self, width: float, available: float = 0.0) -> float:
        """Size the item for `width`; returns its height. `available` is the list's visible height."""
        raise NotImplementedError

    def texts(self) -> list[str]:
        """The text it shows now, top to bottom (for tests and accessibility checks)."""
        return []


class Row(Item):
    """A list row: a soft tint and its action icons while the cursor is over it, and a click
    anywhere outside a button calls `on_click`."""

    def __init__(self, parent: QWidget, on_click: Callable[[], None] | None = None):
        super().__init__(parent)
        self.on_click = on_click
        self.hover_views: list[QWidget] = []
        self.hovered = False

    def enterEvent(self, ev) -> None:
        self.set_hover(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self.set_hover(False)
        super().leaveEvent(ev)

    def set_hover(self, on: bool) -> None:
        self.hovered = on
        for view in self.hover_views:
            view.setVisible(on)
        self.update()

    def mousePressEvent(self, ev) -> None:
        if self.on_click is not None and ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self.on_click is not None and ev.button() == Qt.MouseButton.LeftButton and self.rect().contains(ev.position().toPoint()):
            ev.accept()
            self.on_click()
            return
        super().mouseReleaseEvent(ev)

    def paint_hover(self, p: QPainter) -> None:
        if self.hovered:
            p.fillPath(rounded(QRectF(self.rect()), ROW_RADIUS), theme.fill_soft())


class DayHeader(Item):
    kind = "header"

    def __init__(self, parent: QWidget, text: str, first: bool):
        super().__init__(parent)
        self.text = text
        self.top = HEADER_ABOVE_FIRST if first else HEADER_ABOVE - ROW_GAP
        self.label = _Text(text, theme.ui_font(HEADER_SIZE, "semibold"), theme.content_dim)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def layout_for(self, width: float, available: float = 0.0) -> float:
        h = self.label.height(100_000)
        height = self.top + h + HEADER_BELOW
        self.resize(round(width), round(height))
        return height

    def texts(self) -> list[str]:
        return [self.text]

    def paintEvent(self, ev) -> None:
        p = self.painter()
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.label.paint(p, ROW_PAD_X, self.top, 100_000)
        p.end()


class Note(Item):
    """A calm line, under a faint mark, in the middle of an empty list."""

    kind = "empty"

    def __init__(self, parent: QWidget, text: str):
        super().__init__(parent)
        self.text = text
        self.label = _Text(text, theme.ui_font(EMPTY_SIZE), theme.content_dim, center=True)
        self.mark_w = round(EMPTY_MARK_H * mark_layout.MARK_W / mark_layout.MARK_H)
        self.mark_rect = QRectF()
        self.text_rect = QRectF()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def layout_for(self, width: float, available: float = 0.0) -> float:
        # Balanced lines: as wide as the text needs to split evenly, never a lone last word.
        limit = _wrap(min(width - 2 * ROW_PAD_X, EMPTY_W))
        natural = self.label.natural_width() + 1
        one_line = self.label.height(100_000)
        lines = max(1, int(-(-natural // limit)))
        while True:
            w = min(limit, natural / lines)
            h = self.label.height(w)
            while h > lines * one_line + 1 and w < limit:  # word breaks pushed a word down
                w = min(limit, w + EMPTY_STEP)
                h = self.label.height(w)
            if h <= lines * one_line + 1 or lines >= EMPTY_MAX_LINES:
                break
            lines += 1
        block = EMPTY_MARK_H + EMPTY_MARK_GAP + h
        height = max(available, block + 2 * PILL_ABOVE)
        top = round((height - block) / 2)
        self.mark_rect = QRectF(round((width - self.mark_w) / 2), top, self.mark_w, EMPTY_MARK_H)
        self.text_rect = QRectF((width - w) / 2, top + EMPTY_MARK_H + EMPTY_MARK_GAP, w, h)
        self.resize(round(width), round(height))
        return height

    def texts(self) -> list[str]:
        return [self.text]

    def paintEvent(self, ev) -> None:
        p = self.painter()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw_mark(p, self.mark_rect, theme.content(EMPTY_MARK_ALPHA))
        self.label.paint(p, self.text_rect.x(), self.text_rect.y(), self.text_rect.width())
        p.end()


class PillItem(Item):
    """A pill centred on its own line (Show Older)."""

    kind = "pill"

    def __init__(self, parent: QWidget, title: str, clicked: Callable[[], None]):
        super().__init__(parent)
        self.button = _pill(title, self, clicked)

    def layout_for(self, width: float, available: float = 0.0) -> float:
        top = PILL_ABOVE - ROW_GAP
        self.button.move(round((width - self.button.width()) / 2), round(top))
        height = top + self.button.height()
        self.resize(round(width), round(height))
        return height

    def texts(self) -> list[str]:
        return [self.button.text()]


class DictationRow(Row):
    kind = "dictation"

    def __init__(self, parent: QWidget, owner: HistoryWindow, entry: history_mod.Entry):
        self.editing = owner._editing == entry.id
        super().__init__(parent, None if self.editing else (lambda: owner._toggle_expanded(entry.id)))
        self.owner = owner
        self.entry = entry
        self.entry_id = entry.id
        self.expanded = entry.id in owner._expanded
        self.show_original = entry.id in owner._originals and bool(entry.raw)
        self.learned = owner._learned.get(entry.id, "")

        self.time = _Text(time_label(entry.when()), theme.digits_font(TIME_SIZE), theme.content_dim)
        self.text = _Text(entry.text, theme.ui_font(TEXT_SIZE), theme.content, TEXT_LINE_H)
        self.original = _Text(entry.raw, theme.ui_font(NOTE_SIZE, italic=True), theme.content_dim)
        self.learned_label = _Text(self.learned, theme.ui_font(NOTE_SIZE), theme.content_dim)

        self.copy = RowIcon("doc.on.doc", ICON_SIZE, "Copy", lambda: owner.copy_entry(entry.id, self.copy), self)
        self.more = RowIcon("ellipsis", ICON_SIZE, "More", lambda: owner.more_menu(entry.id, self.more), self)
        for b in (self.copy, self.more):
            b.hide()
        self.hover_views = [self.copy, self.more]

        self.edit_box = self.edit_field = self.save = self.cancel = None
        if self.editing:
            self.hover_views = []
            self.edit_box = FieldBox(self)
            self.edit_field = FixField(self.edit_box, owner.save_fix, owner.cancel_fix, owner._fix_changed)
            self.edit_field.setPlainText(owner._edit_text if owner._edit_text is not None else entry.text)
            self.save = _pill("Save", self, owner.save_fix, primary=True, height=SMALL_PILL_H)
            self.cancel = _pill("Cancel", self, owner.cancel_fix, height=SMALL_PILL_H)

        self.text_x = ROW_PAD_X + TIME_W
        self.text_w = 0.0
        self.text_h = 0.0
        self.original_y = self.learned_y = 0.0

    def layout_for(self, width: float, available: float = 0.0) -> float:
        text_x = self.text_x
        icons_w = 2 * ICON_BTN + ICON_GAP
        self.text_w = _wrap(max(120.0, width - text_x - ROW_PAD_X - icons_w - ICONS_TO_TEXT))
        top = ROW_PAD_Y

        y = top
        if self.editing:
            # The filled box bleeds FIELD_PAD to the left, so the text inside stays on the text
            # column, and starts half its extra height above the line the text was on.
            pad = (FIELD_H - TEXT_LINE_H) / 2
            box_x = text_x - FIELD_PAD
            box_w = width - ROW_PAD_X - box_x
            inner = box_w - 2 * FIELD_PAD
            h = max(TEXT_LINE_H, self.edit_field.wanted_height(inner))
            box_h = h + 2 * pad
            self.edit_box.setGeometry(round(box_x), round(y - pad), round(box_w), round(box_h))
            self.edit_field.setGeometry(round(FIELD_PAD), round(pad), round(inner), round(h))
            y += h + pad + EDIT_TO_BUTTONS
            self.save.move(round(text_x), round(y))
            self.cancel.move(round(text_x + self.save.width() + BUTTON_GAP), round(y))
            y += SMALL_PILL_H
        else:
            full = self.text.height(self.text_w)
            self.text_h = full if self.expanded else min(full, TEXT_LINES * TEXT_LINE_H)
            y += self.text_h
        if self.show_original:
            self.original_y = y + NOTE_TOP
            y += NOTE_TOP + self.original.height(self.text_w)
        if self.learned:
            self.learned_y = y + NOTE_TOP
            y += NOTE_TOP + self.learned_label.height(100_000)
        y += ROW_PAD_Y

        icon_y = top + TEXT_LINE_H / 2 - ICON_BTN / 2
        more_x = width - ROW_PAD_X + (ICON_BTN - ICON_SIZE) / 2 - ICON_BTN
        self.more.move(round(more_x), round(icon_y))
        self.copy.move(round(more_x - ICON_GAP - ICON_BTN), round(icon_y))
        self.resize(round(width), round(y))
        return y

    def texts(self) -> list[str]:
        out = [self.time.value]
        if self.editing:
            out.append(self.edit_field.text())
        else:
            out.append(self.text.value)
        if self.show_original:
            out.append(self.original.value)
        if self.learned:
            out.append(self.learned)
        return out

    def paintEvent(self, ev) -> None:
        p = self.painter()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.paint_hover(p)
        # The time shares the text's first baseline, as the Mac's two labels with one fixed line height do.
        baseline = ROW_PAD_Y + (self.text.first_baseline(self.text_w) if not self.editing else
                                TEXT_LINE_H - QFontMetricsF(self.text.font).descent())
        p.setPen(theme.content_dim())
        theme.draw_text_at(p, ROW_PAD_X, baseline, self.time.value, self.time.font)
        if not self.editing:
            self.text.paint(p, self.text_x, ROW_PAD_Y, self.text_w, 0 if self.expanded else TEXT_LINES)
        if self.show_original:
            self.original.paint(p, self.text_x, self.original_y, self.text_w)
        if self.learned:
            self.learned_label.paint(p, self.text_x, self.learned_y, 100_000)
        p.end()


class TermRow(Row):
    kind = "term"

    def __init__(self, parent: QWidget, owner: HistoryWindow, entry: vocabulary_mod.Entry):
        super().__init__(parent)
        self.entry = entry
        self.term = entry.term
        self.label = _Text(entry.term, theme.ui_font(TERM_SIZE, "medium"), theme.content)
        heard = sorted(entry.heard_as, key=lambda k: (-entry.heard_as[k], k))
        self.heard_text = f"heard as: {', '.join(heard)}" if heard else ""
        self.heard = _Text(self.heard_text, theme.ui_font(HEARD_SIZE), theme.content_dim)
        self.badge_text = SOURCE_BADGE.get(entry.source, "Added")
        self.source = _Text(self.badge_text, theme.ui_font(SOURCE_SIZE), theme.content_dim)
        self.delete = RowIcon("xmark", ICON_SIZE_SMALL, "Delete", lambda: owner.remove_term(entry.term), self)
        self.delete.hide()
        self.hover_views = [self.delete]
        self.text_w = 0.0
        self.label_w = 0.0
        self.source_x = self.source_y = 0.0

    def layout_for(self, width: float, available: float = 0.0) -> float:
        sw = self.source.natural_width()
        sh = self.source.height(100_000)
        icon_x = width - ROW_PAD_X + (ICON_BTN - ICON_SIZE) / 2 - ICON_BTN
        self.source_x = icon_x - SOURCE_TO_ICON - sw
        self.text_w = max(80.0, self.source_x - LABEL_INSET - ICONS_TO_TEXT - ROW_PAD_X)
        y = ROW_PAD_Y
        h = self.label.height(100_000)
        self.label_w = min(self.text_w, self.label.natural_width() + 1)
        first_line = h
        y += h
        if self.heard_text:
            y += HEARD_GAP + self.heard.height(_wrap(self.text_w))
        y += ROW_PAD_Y
        mid = ROW_PAD_Y + first_line / 2
        self.source_y = mid - sh / 2
        self.delete.move(round(icon_x), round(mid - ICON_BTN / 2))
        self.resize(round(width), round(y))
        return y

    def texts(self) -> list[str]:
        return [self.term, *([self.heard_text] if self.heard_text else []), self.badge_text]

    def paintEvent(self, ev) -> None:
        p = self.painter()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.paint_hover(p)
        p.save()
        p.setClipRect(QRectF(ROW_PAD_X, 0, self.label_w, self.height()))  # a long term is cut, as the Mac's label clips
        self.label.paint(p, ROW_PAD_X, ROW_PAD_Y, 100_000)
        p.restore()
        if self.heard_text:
            self.heard.paint(p, ROW_PAD_X, ROW_PAD_Y + self.label.height(100_000) + HEARD_GAP, _wrap(self.text_w))
        self.source.paint(p, self.source_x, self.source_y, 100_000)
        p.end()


# -- the window ---------------------------------------------------------------------------------


class HistoryWindow(LyssnaWindow):
    """The app sets `ctl` after construction, then calls show(). `store`, `vocabulary` and
    `now` are the shared history, the shared dictionary and the clock unless a test sets them."""

    def __init__(self):
        super().__init__("Lyssna History")
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, True)  # resizable, and it minimises as on the Mac
        self.ctl = None
        self.store: history_mod.HistoryStore | None = None
        self.vocabulary: vocabulary_mod.Vocabulary | None = None
        self.now: Callable[[], datetime] = lambda: datetime.now().astimezone()
        self.built = False
        self._timer = QTimer(self)
        self._timer.setInterval(round(POLL * 1000))
        self._timer.timeout.connect(self.tick)
        self._stamps = None
        self._dirty = False          # something changed while a fix was being typed
        self._tab = "dictations"
        self._query = ""
        self._cap = ROW_CAP
        self._expanded: set[str] = set()
        self._originals: set[str] = set()
        self._learned: dict[str, str] = {}
        self._editing: str | None = None
        self._edit_text: str | None = None
        self._add_note = ""
        self._support_shown = False
        self._menu: QMenu | None = None
        self.items: list[Item] = []

    def _store(self) -> history_mod.HistoryStore:
        return self.store or history_mod.shared()

    def _vocab(self) -> vocabulary_mod.Vocabulary:
        return self.vocabulary or vocabulary_mod.shared()

    def _key_name(self) -> str:
        """The dictation key as the menus write it ("fn" on the Mac by default)."""
        cfg = getattr(self.ctl, "cfg", None) or {}
        hotkey = (cfg.get("dictation") or {}).get("hotkey") or config.DEFAULTS["dictation"]["hotkey"]
        return key_name(str(hotkey))

    # -- showing -------------------------------------------------------------------------

    def show(self) -> None:
        """Bring the window up (building it the first time), fresh from the store."""
        self.prepare()
        if not self.isVisible():
            x, y, w, h = coords.visible_frame(coords.screen_under_mouse())
            if w and h:  # centred on the screen under the mouse, as the Mac window opens
                self.move(round(x + (w - self.width()) / 2), round(max(y, y + (h - self.height()) / 2)))
        self.setVisible(True)
        self.raise_()
        self.activateWindow()
        self._timer.start()

    def prepare(self) -> None:
        """Build the window if needed and fill it from the store, without putting it on screen."""
        if not self.built:
            self._build()
        self._refresh(force=True)

    def closeEvent(self, ev) -> None:
        """Closing forgets what was open in the list (the Mac's windowWillClose_)."""
        self._timer.stop()
        self._editing = self._edit_text = None
        self._learned.clear()
        self._originals.clear()
        self._expanded.clear()
        self._add_note = ""
        self._cap = ROW_CAP
        super().closeEvent(ev)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._layout()

    def tick(self) -> None:
        self._refresh()

    # -- building ------------------------------------------------------------------------

    def _build(self) -> None:
        self.built = True
        self.setMinimumSize(round(MIN_W), round(MIN_H))
        self.resize(round(W), round(H))

        self.heading = Header("History", self, mark_h=MARK_H, title_size=TITLE_SIZE, mark_gap=MARK_GAP)
        self.title = self.heading.title

        self.stat_numbers: list[Label] = []
        self.stat_labels: list[Label] = []
        for caption in ("words this week", "words in total", "dictations"):
            self.stat_numbers.append(Label("0", self, font=theme.digits_font(STAT_SIZE, "semibold")))
            self.stat_labels.append(Label(caption, self, size=STAT_LABEL_SIZE, color=theme.content_dim))

        # Under the totals, once they are worth a look: one quiet sentence and its link.
        self.support_label = Label(support.HISTORY_LINE, self, size=SUPPORT_SIZE, color=theme.content_dim)
        self.support_button = TextButton(support.HISTORY_ACTION, self, size=SUPPORT_SIZE, link=True, symbol=support.SYMBOL,
                                         clicked=self.open_support)

        self.tabs = Segments(["Dictations", "Dictionary"], lambda i: self.select_tab(TABS[i]), self)

        # Dictations: the search field.
        self.search_box = FieldBox(self, icon="magnifyingglass")
        self.search_field = LineField("Search", self.search_box)
        self.search_field.textChanged.connect(self._search_changed)

        # Dictionary: the add row.
        self.add_box = FieldBox(self)
        self.add_field = LineField("Add a word or name", self.add_box)
        self.add_field.returnPressed.connect(self.add_term)
        self.add_button = _pill("Add", self, self.add_term, primary=True, height=FIELD_H)
        self.add_note_label = Label("", self, size=NOTE_SIZE, color=theme.content_dim)

        self.sep_top = Hairline(self)

        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setWidgetResizable(False)
        self.scroll.viewport().setAutoFillBackground(False)
        self.doc = QWidget()
        self.scroll.setWidget(self.doc)
        self.doc.setAutoFillBackground(False)  # setWidget turned it on; the window's surface shows through

        self.sep_bottom = Hairline(self)
        self.retention = Label("", self, size=FOOTER_SIZE, color=theme.content_dim)
        # Regular weight, like the retention line beside it.
        self.clear_button = TextButton("Clear History…", self, size=FOOTER_SIZE, weight="regular", clicked=self.clear_history)
        self.restyle()

    def restyle(self) -> None:
        if not self.built:
            return
        for field in (self.search_field, self.add_field):
            field.restyle()
        for item in self.items:
            if isinstance(item, DictationRow) and item.edit_field is not None:
                item.edit_field.restyle()
        # A thin bar over the right margin; the list is laid out for the full width, so the
        # column does not shift when the bar comes and goes (the Mac's overlay scroller).
        self.scroll.verticalScrollBar().setStyleSheet(
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}"
            f" QScrollBar::handle:vertical {{ background: {css(theme.content(0.25))}; border-radius: 3px; min-height: 24px; }}"
            f" QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f" QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}")

    # -- state ---------------------------------------------------------------------------

    def _vocab_stamp(self):
        try:
            st = self._vocab().path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _current_stamps(self):
        store = self._store()
        return store.stamp(), self._vocab_stamp(), store.days()

    def _refresh(self, force: bool = False) -> None:
        """Redraw when the store, the dictionary or the retention setting changed (or always,
        with force). A fix being typed is not swept away by a change elsewhere: the list is
        rebuilt once it is saved or cancelled."""
        if not self.built:
            return
        stamps = self._current_stamps()
        if not force and stamps == self._stamps:
            return
        self._update_header()
        if force or self._editing is None:
            self._rebuild_list()
        else:
            self._dirty = True
            self._layout()  # the new totals, around the fix in progress
        self._stamps = self._current_stamps()  # reading may have trimmed old entries

    def _update_header(self) -> None:
        stats = self._store().stats()
        values = (stats["words_this_week"], stats["words_total"], stats["dictations"])
        for label, value in zip(self.stat_numbers, values, strict=True):
            label.set_text(figure(value))
        self.retention.set_text(retention_text(self._store().days(), host.DEVICE))
        self._support_shown = support.in_history(stats["words_total"])

    def _rebuild_list(self) -> None:
        if self._editing is not None:
            row = self._row(self._editing)
            if row is not None and row.edit_field is not None:
                self._edit_text = row.edit_field.text()
        for item in self.items:
            item.hide()
            item.deleteLater()  # never at once: the click that asked for this may still be running in it
        self.items = self._dictation_items() if self._tab == "dictations" else self._dictionary_items()
        for item in self.items:
            item.show()
        self._dirty = False
        self._layout()
        if self._editing is not None:
            row = self._row(self._editing)
            if row is not None and row.edit_field is not None:
                field = row.edit_field
                field.setFocus(Qt.FocusReason.OtherFocusReason)
                cursor = field.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                field.setTextCursor(cursor)
        self._sync_hover()

    def _sync_hover(self) -> None:
        """Light the row under a still cursor (new rows hear no enter event until it moves)."""
        if not self.isVisible():
            return
        pos = self.doc.mapFromGlobal(QCursor.pos())
        for item in self.items:
            if isinstance(item, Row):
                item.set_hover(item.geometry().contains(pos))

    def _dictation_items(self) -> list[Item]:
        store = self._store()
        entries = store.search(self._query)
        has_any = bool(entries) or bool(self._query.strip() and store.entries())
        self.clear_button.setEnabled(bool(store.entries()))
        if not entries:
            return [Note(self.doc, EMPTY_SEARCH if has_any else empty_dictations(self._key_name()))]
        today = self.now().date()
        items: list[Item] = []
        last_day = None
        for entry in entries[: self._cap]:
            day = entry.when().date()
            if day != last_day:
                items.append(DayHeader(self.doc, day_label(day, today), first=last_day is None))
                last_day = day
            items.append(DictationRow(self.doc, self, entry))
        if len(entries) > self._cap:
            items.append(PillItem(self.doc, "Show Older", self.show_older))
        return items

    def _dictionary_items(self) -> list[Item]:
        entries = self._vocab().entries()
        return [TermRow(self.doc, self, e) for e in entries] or [Note(self.doc, EMPTY_DICTIONARY)]

    def _row(self, entry_id: str) -> DictationRow | None:
        return next((i for i in self.items if isinstance(i, DictationRow) and i.entry_id == entry_id), None)

    # -- layout --------------------------------------------------------------------------

    def _layout(self) -> None:
        if not self.built:
            return
        cw, ch = float(self.width()), float(self.height())
        x0, inner = column(cw)

        y = TOP
        hh = self.heading.fit(inner)
        self.heading.move(round(x0), round(y))
        y += hh + HEAD_TO_STATS

        col = inner / 3
        stats_h = 0.0
        for i, (number, label) in enumerate(zip(self.stat_numbers, self.stat_labels, strict=True)):
            _, nh = number.fit()
            _, lh = label.fit()
            left = x0 + i * col  # where the ink of both lines starts
            place(number, left - ink_left(number.font(), number.value()), y)
            place(label, left - ink_left(label.font(), label.value()), y + nh)
            stats_h = max(stats_h, nh + lh)
        y += stats_h
        for view in (self.support_label, self.support_button):
            view.setVisible(self._support_shown)
        if self._support_shown:
            y += STATS_TO_SUPPORT
            _, lh = self.support_label.fit()
            sx = x0 - ink_left(self.support_label.font(), self.support_label.value())
            place(self.support_label, sx, y)
            self.support_button.resize(self.support_button.sizeHint())
            bh = self.support_button.height()
            # After the label's frame as the Mac sizes it, on half points.
            place(self.support_button, sx + self.support_label.exact_width() + SUPPORT_GAP, y + (lh - bh) / 2)
            y += lh
        y += STATS_TO_TABS

        self.tabs.move(round(x0), round(y))
        y += SEG_H + TABS_TO_FIELD

        dictations = self._tab == "dictations"
        self.search_box.setVisible(dictations)
        for view in (self.add_box, self.add_button):
            view.setVisible(not dictations)
        if dictations:
            self.search_box.setGeometry(round(x0), round(y), round(inner), round(FIELD_H))
            text_x = FIELD_PAD + SEARCH_ICON_BOX + SEARCH_ICON_GAP - LABEL_INSET  # QLineEdit keeps its text 2 px in, like the Mac's cell
            self._place_field(self.search_field, text_x, inner - text_x - FIELD_PAD)
        else:
            bw = max(self.add_button.sizeHint().width(), ADD_MIN_W)
            self.add_button.setFixedSize(round(bw), round(FIELD_H))
            self.add_button.move(round(x0 + inner - bw), round(y))
            box_w = inner - bw - BUTTON_GAP
            self.add_box.setGeometry(round(x0), round(y), round(box_w), round(FIELD_H))
            self._place_field(self.add_field, FIELD_PAD - LABEL_INSET, box_w - 2 * FIELD_PAD + LABEL_INSET)
        y += FIELD_H
        self.add_note_label.set_text(self._add_note)
        show_note = not dictations and bool(self._add_note)
        self.add_note_label.setVisible(show_note)
        if show_note:
            _, nh = self.add_note_label.fit()
            self.add_note_label.move(round(x0 + FIELD_PAD - LABEL_INSET), round(y + NOTE_GAP))  # its text on the field's
            y += NOTE_GAP + nh
        y += FIELD_TO_LIST

        self.sep_top.setGeometry(0, round(y), round(cw), 1)
        footer_top = ch - FOOTER_H
        self.sep_bottom.setGeometry(0, round(footer_top), round(cw), 1)
        self.scroll.setGeometry(0, round(y + 1), round(cw), round(max(0.0, footer_top - y - 1)))

        _, rh = self.retention.fit()
        place(self.retention, x0 - LABEL_INSET, footer_top + (FOOTER_H - rh) / 2)  # its text on the column
        self.clear_button.resize(self.clear_button.sizeHint())
        cbw, cbh = self.clear_button.width(), self.clear_button.height()
        # Its frame reaches past the column, so its centred title ends about on it, as on the Mac.
        place(self.clear_button, x0 + inner - cbw + LABEL_INSET * 2, footer_top + (FOOTER_H - cbh) / 2)
        self.clear_button.setVisible(dictations)

        self._layout_list()

    @staticmethod
    def _place_field(field: QWidget, x: float, width: float) -> None:
        h = field.sizeHint().height()
        field.setGeometry(round(x), round((FIELD_H - h) / 2), round(width), h)

    def _layout_list(self) -> None:
        # The full width of the scroll area, not its viewport: the scroll bar then lies over the
        # right margin instead of pushing the column left.
        width = float(self.scroll.width())
        visible = float(self.scroll.height())
        x0, inner = column(width)
        row_w = inner + 2 * ROW_PAD_X
        headers = any(i.kind == "header" for i in self.items)
        y = 0.0 if headers or (self.items and self.items[0].kind == "empty") else LIST_TOP
        for item in self.items:
            h = item.layout_for(row_w, visible if item.kind == "empty" else 0.0)
            item.move(round(x0 - ROW_PAD_X), round(y))
            item.paint_dy = y - round(y)
            y += h + (ROW_GAP if item.kind in ("dictation", "term") else 0.0)
        if not (self.items and self.items[-1].kind == "empty"):
            y += LIST_BOTTOM
        self.doc.resize(round(width), round(max(y, visible)))

    # -- actions: tabs and search -------------------------------------------------------------

    def select_tab(self, tab: str) -> None:
        """Show "dictations" or "dictionary"."""
        if tab not in TABS:
            return
        self.tabs.select(TABS.index(tab))
        if tab == self._tab:
            return
        self._tab = tab
        self._update_header()
        self._rebuild_list()
        self.scroll.verticalScrollBar().setValue(0)

    def _search_changed(self, text: str) -> None:
        self._query = text
        self._cap = ROW_CAP
        self._rebuild_list()

    def _fix_changed(self) -> None:
        if self._editing is not None:
            self._layout_list()  # the fix field grows with its text

    def open_support(self) -> None:
        support.open_support("history")

    def show_older(self) -> None:
        self._cap += ROW_CAP
        self._rebuild_list()

    # -- actions: a dictation ------------------------------------------------------------------

    def _toggle_expanded(self, entry_id: str) -> None:
        if entry_id in self._expanded:
            self._expanded.discard(entry_id)
        else:
            self._expanded.add(entry_id)
        self._rebuild_list()

    def copy_entry(self, entry_id: str, button: RowIcon) -> None:
        entry = self._store().get(entry_id)
        if entry is None:
            return
        copy_to_clipboard(entry.text)
        button.set_icon("checkmark")
        later(COPIED_FOR, lambda: button.set_icon("doc.on.doc"), button)

    def more_menu(self, entry_id: str, button: QWidget) -> None:
        entry = self._store().get(entry_id)
        if entry is None:
            return
        self._menu = self._more_menu(entry)
        self._menu.popup(button.mapToGlobal(QPoint(0, button.height() + 4)))

    def _more_menu(self, entry: history_mod.Entry) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(False)
        menu.setStyleSheet(menu_style())
        shown = entry.id in self._originals
        for title, action, enabled in (
            ("Hide Original" if shown else "Show Original", self.toggle_original, bool(entry.raw)),
            ("Fix a Word…", self.fix_word, True),
            ("Delete", self.delete_entry, True),
        ):
            item = menu.addAction(title)
            item.setEnabled(enabled)
            item.triggered.connect(lambda _checked=False, fn=action, entry_id=entry.id: fn(entry_id))
        return menu

    def toggle_original(self, entry_id: str) -> None:
        if entry_id in self._originals:
            self._originals.discard(entry_id)
        else:
            self._originals.add(entry_id)
        self._rebuild_list()

    def fix_word(self, entry_id: str) -> None:
        self._editing = entry_id
        self._edit_text = None
        self._learned.pop(entry_id, None)
        self._rebuild_list()
        row = self._row(entry_id)
        if row is not None and row.edit_field is not None:
            row.edit_field.selectAll()  # as the Mac's field takes the keyboard: the whole text, ready to fix

    def delete_entry(self, entry_id: str) -> None:
        if self._editing == entry_id:
            self._editing = self._edit_text = None
        self._store().delete(entry_id)
        self._refresh(force=True)

    def save_fix(self) -> None:
        entry_id = self._editing
        row = self._row(entry_id) if entry_id else None
        if row is None or row.edit_field is None:
            return
        new_text = row.edit_field.text().strip()
        old_text = row.entry.text
        self._editing = self._edit_text = None
        learned: list[str] = []
        if new_text and new_text != old_text:
            vocab = self._vocab()
            for observed, corrected in find_corrections(old_text, new_text):
                term = vocab.learn(observed, corrected)
                if term and term not in learned:
                    learned.append(term)
            self._store().update_text(entry_id, new_text)
        if learned:
            self._learned[entry_id] = learned_text(learned)
        else:
            self._learned.pop(entry_id, None)
        log.info("history: a dictation was fixed by hand (%d terms learned)", len(learned))
        self._refresh(force=True)

    def cancel_fix(self) -> None:
        self._editing = self._edit_text = None
        self._refresh(force=True)

    def clear_history(self) -> None:
        def clear() -> None:
            self._store().clear()
            self._editing = self._edit_text = None
            self._refresh(force=True)

        self._confirm("Delete all saved dictations?", "Word totals are kept.", "Delete", clear)

    def _confirm(self, message: str, info: str, ok: str, then: Callable[[], None]) -> None:
        """Ask over the window (a sheet on the Mac) and call `then` if the user agrees."""
        box = QMessageBox(self)
        box.setWindowTitle("Lyssna")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setText(message)
        box.setInformativeText(info)
        yes = box.addButton(ok, QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.setEscapeButton(no)
        box.setWindowModality(Qt.WindowModality.WindowModal)

        def done(_result: int) -> None:
            if box.clickedButton() is yes:
                then()
            box.deleteLater()

        box.finished.connect(done)
        box.open()

    # -- actions: the dictionary ----------------------------------------------------------------

    def add_term(self) -> None:
        text = self.add_field.text().strip()
        if not text:
            return
        term = self._vocab().add(text)
        if term is None:
            self._add_note = "That is too long for the dictionary."
        else:
            self._add_note = ""
            self.add_field.setText("")
        self._refresh(force=True)

    def remove_term(self, term: str) -> None:
        self._vocab().remove(term)
        self._refresh(force=True)


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------
#
# A real store and dictionary in a fresh temporary folder (never the user's), with a fixed
# clock, so the shots are the same every time.

_PREVIEW_NOW = datetime(2026, 11, 17, 16, 0)  # a Tuesday, local time
_PREVIEW_ENTRIES = (  # (days ago, hour, minute, text, original)
    (0, 15, 42, "Can you send the updated deck to Mattias before the call? He wants to check the numbers on slide four.",
     "can you send the updated deck to matias before the call he wants to check the numbers on slide four"),
    (0, 11, 8, "Booked the train to Uppsala for Thursday morning. Back the same evening.", ""),
    (0, 9, 30, "Notes from the standup. The build is green again after the font fix. Parakeet is now the default "
               "on Apple silicon and the cleanup model stays under two seconds on every machine we tried. Next we "
               "look at the Windows installer, the history window and the dictionary tab, then the side by side "
               "comparison with the Mac app. Kokoro needs one more pass on long sentences. Someone should also look "
               "at why the pill flickers when the screen sleeps, and we still owe the design review a short write-up.",
     ""),
    (1, 18, 5, "Thanks, that works for me. Let's keep the meeting at three.", ""),
    (1, 10, 12, "Remind me to call the dentist tomorrow.", ""),
    (4, 14, 20, "The quarterly summary is in the shared folder.", ""),
)


class _PreviewStore(history_mod.HistoryStore):
    """The store with header totals worth looking at (past the support line's threshold)."""

    def stats(self) -> dict:
        return {"words_this_week": 1204, "words_total": 18930, "dictations": 412, "minutes": 0.0, "wpm": 0}


def _preview_window(*, entries: bool = True, terms: bool = True, width: float = W, height: float = H,
                    supporter: bool = True) -> HistoryWindow:
    import tempfile
    from datetime import timedelta
    from pathlib import Path
    from unittest import mock

    from ..dictation.types import DictationResult

    folder = Path(tempfile.mkdtemp(prefix="lyssna-history-preview-"))
    clock = {"t": _PREVIEW_NOW.astimezone()}
    store = _PreviewStore(path=folder / "history.json", days=lambda: 30, now=lambda: clock["t"])
    vocab = vocabulary_mod.Vocabulary(path=folder / "vocabulary.json")
    if entries:
        for days_ago, hour, minute, text, raw in reversed(_PREVIEW_ENTRIES):
            clock["t"] = (_PREVIEW_NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute).astimezone()
            store.add(DictationResult(text=text, raw=raw or text.lower(), source="model", transcriber="parakeet",
                                      cleaner="s1-mini", audio_seconds=2.0, seconds={}, words=len(text.split())))
        clock["t"] = _PREVIEW_NOW.astimezone()
    if terms:
        vocab.add("Parakeet")
        vocab.learn("Matias", "Mattias")
        vocab.learn("Mathias", "Mattias")
        vocab.used("Mattias Mattias")
        vocab.add("Uppsala")
        vocab.learn("cocoro", "Kokoro")
    w = HistoryWindow()
    w.store, w.vocabulary, w.now = store, vocab, lambda: clock["t"]
    with mock.patch.object(support, "SITE_URL", "https://example.test" if supporter else ""):
        w.prepare()
    w.resize(round(width), round(height))
    w._layout()
    return w


def _first_row(w: HistoryWindow) -> DictationRow:
    return next(i for i in w.items if isinstance(i, DictationRow))


def _preview_dictations() -> QWidget:
    return _preview_window()


def _preview_expanded() -> QWidget:
    w = _preview_window(supporter=False)
    rows = [i for i in w.items if isinstance(i, DictationRow)]
    long_row = next(r for r in rows if len(r.entry.text) > 300)
    w._expanded.add(long_row.entry_id)
    w._originals.add(rows[0].entry_id)
    w._rebuild_list()
    return w


def _preview_learned() -> QWidget:
    w = _preview_window(supporter=False)
    row = _first_row(w)
    w._learned[row.entry_id] = learned_text(["Mattias"])
    w._rebuild_list()
    return w


def _preview_hover() -> QWidget:
    w = _preview_window(supporter=False)
    _first_row(w).set_hover(True)
    rows = [i for i in w.items if isinstance(i, DictationRow)]
    rows[1].set_hover(True)
    rows[1].copy.set_icon("checkmark")  # just copied
    rows[1].more.set_hover(True)
    return w


class _Composite(QWidget):
    """A window with a menu drawn over it where it would pop up (a menu is its own window, so
    a shot of the window alone would not show it)."""

    def __init__(self, base: QWidget, overlay: QWidget, at: QPoint):
        super().__init__()
        self.base, self.overlay, self.at = base, overlay, at
        self.resize(max(base.width(), at.x() + overlay.width()), max(base.height(), at.y() + overlay.height()))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, _sharp(self.base))
        p.drawPixmap(self.at, _sharp(self.overlay))
        p.end()


def _sharp(widget: QWidget):
    """The widget as a pixmap at twice its size, as the shots are taken."""
    from PySide6.QtGui import QPixmap

    pm = QPixmap(widget.size() * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    widget.render(pm, QPoint(0, 0))
    return pm


def _preview_more_menu() -> QWidget:
    w = _preview_window(supporter=False)
    row = _first_row(w)
    row.set_hover(True)
    row.more.set_hover(True)
    menu = w._more_menu(row.entry)
    menu.ensurePolished()
    menu.adjustSize()
    at = row.more.mapTo(w, QPoint(0, row.more.height() + 4))
    return _Composite(w, menu, at)


def _preview_fix() -> QWidget:
    w = _preview_window(supporter=False)
    row = _first_row(w)
    w.fix_word(row.entry_id)
    row = w._row(row.entry_id)
    row.edit_field.setPlainText(row.entry.text.replace("Mattias", "Matias"))
    w._layout_list()
    row.edit_box.set_focused(True)
    return w


def _preview_search(query: str) -> QWidget:
    w = _preview_window(supporter=False)
    w.search_field.setText(query)
    w.search_box.set_focused(True)
    return w


def _preview_dictionary(terms: bool = True, note: str = "") -> QWidget:
    w = _preview_window(supporter=False, terms=terms)
    w.select_tab("dictionary")
    if note:
        w._add_note = note
        w._layout()
    if terms:
        next(i for i in w.items if isinstance(i, TermRow)).set_hover(True)
    return w


PREVIEWS = {
    "history.dictations": _preview_dictations,
    "history.expanded": _preview_expanded,
    "history.learned": _preview_learned,
    "history.hover": _preview_hover,
    "history.more-menu": _preview_more_menu,
    "history.fix": _preview_fix,
    "history.search": lambda: _preview_search("mattias"),
    "history.search-none": lambda: _preview_search("nothing like it"),
    "history.empty": lambda: _preview_window(entries=False, supporter=False),
    "history.dictionary": _preview_dictionary,
    "history.dictionary-empty": lambda: _preview_dictionary(terms=False),
    "history.dictionary-too-long": lambda: _preview_dictionary(note="That is too long for the dictionary."),
    "history.wide": lambda: _preview_window(width=1040.0, height=720.0),
}
