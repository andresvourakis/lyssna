"""The Lyssna mark (an "l" stem followed by one wave), drawn as vectors in the content colour,
the twin of ui/mark.py. The geometry is shared/mark.py's, in the Mac's y-up coordinates, so it
is flipped here at the boundary into Qt's y-down widget coordinates."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..shared.mark import MARK_H, MARK_W, mark_geometry
from . import theme


def draw_mark(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Paint the mark into `rect` (widget coordinates, y down) in `color`, as large as it fits
    and centred."""
    # mark_geometry centres the mark in its rect, so a rect at the origin and a flip about its
    # height give the same placement in y-down coordinates.
    w, h = rect.width(), rect.height()
    (sx, sy, sw, sh, radius), points, line_width = mark_geometry(((0.0, 0.0), (w, h)))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(rect.x(), rect.y())
    stem = QPainterPath()
    stem.addRoundedRect(QRectF(sx, h - sy - sh, sw, sh), radius, radius)
    painter.fillPath(stem, color)
    wave = QPainterPath()
    x0, y0 = points[0]
    wave.moveTo(x0, h - y0)
    for x, y in points[1:]:
        wave.lineTo(x, h - y)
    pen = QPen(color, line_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(wave)
    painter.restore()


class MarkView(QWidget):
    """The mark in content(), `height` tall and as wide as the mark's proportions make it.
    Decoration only: the headline beside it says the name."""

    def __init__(self, height: float, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(round(height * MARK_W / MARK_H), round(height))

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        draw_mark(p, QRectF(self.rect()), theme.content())
        p.end()


# -- previews (python -m lyssna.ui.qt.preview) -------------------------------------------------


def _preview_mark() -> QWidget:
    from .preview import card

    c = card(176, 80)
    x = 32
    for h in (28, 30):
        m = MarkView(h, c)
        m.move(x, round((80 - h) / 2))
        c.caption(str(h), x, 62, m.width(), 14, Qt.AlignmentFlag.AlignHCenter)
        x += m.width() + 40
    return c


PREVIEWS = {"mark": _preview_mark}
