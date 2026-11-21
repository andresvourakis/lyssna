"""Vector twins of the SF Symbols the Mac interface uses (symbol_button and set_symbol in
legacy/ui/panel.py, called from legacy/ui/mini_player.py, legacy/ui/toast.py, legacy/ui/selection_button.py and the windows).

Windows has no SF Symbols, so each icon is drawn here as a QPainterPath in a unit box (0..1,
y down) that stands for the symbol's point size: an icon drawn into a 13 x 13 rect is the twin
of the 13 pt symbol. The style is one throughout, as SF's is: strokes with round caps and joins
whose width follows the weight, solid shapes with softly rounded corners, and each glyph sized
inside the box the way SF sizes it, so a 20 px play and a 17 px skip sit together as on the Mac.

Keys are the SF Symbol names, so shared code that returns a symbol name (the dock arrows, the
chevrons) works unchanged. An unknown name draws nothing and is logged once."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPainterPathStroker, QPixmap, QTransform

log = logging.getLogger("lyssna.ui.icons")

# Stroke width per weight, as a fraction of the point size (SF Symbols at 11 to 20 pt).
WEIGHTS = {
    "regular": 0.085,
    "medium": 0.100,
    "semibold": 0.115,
    "bold": 0.135,
    "heavy": 0.155,
}
DEFAULT_WEIGHT = "semibold"  # symbol_button's default on the Mac
_ROUND = 0.10                # corner rounding of the solid shapes (stroke width of their outline)

# -- path helpers ------------------------------------------------------------------------------


def _line(*pts: tuple[float, float], closed: bool = False) -> QPainterPath:
    p = QPainterPath()
    p.moveTo(*pts[0])
    for x, y in pts[1:]:
        p.lineTo(x, y)
    if closed:
        p.closeSubpath()
    return p


def _stroke(path: QPainterPath, width: float) -> QPainterPath:
    """The outline of `path` stroked `width` wide with round caps and joins, as a fillable path."""
    s = QPainterPathStroker()
    s.setWidth(width)
    s.setCapStyle(Qt.PenCapStyle.RoundCap)
    s.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return s.createStroke(path)


def _solid(*pts: tuple[float, float], round_: float = _ROUND) -> QPainterPath:
    """A filled polygon with rounded corners: the polygon plus its own round-joined outline, so
    it grows by round_/2 on every side."""
    poly = _line(*pts, closed=True)
    return poly.united(_stroke(poly, round_))


def _rrect(x0: float, y0: float, x1: float, y1: float, r: float) -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(QRectF(x0, y0, x1 - x0, y1 - y0), r, r)
    return p


def _circle(cx: float, cy: float, r: float) -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(QPointF(cx, cy), r, r)
    return p


def _union(*paths: QPainterPath) -> QPainterPath:
    out = QPainterPath()
    out.setFillRule(Qt.FillRule.WindingFill)
    for p in paths:
        out.addPath(p)
    return out


def _mirror_x(path: QPainterPath) -> QPainterPath:
    return QTransform(-1, 0, 0, 1, 1, 0).map(path)


def _rotate(path: QPainterPath, degrees: float) -> QPainterPath:
    t = QTransform().translate(0.5, 0.5).rotate(degrees).translate(-0.5, -0.5)
    return t.map(path)


# -- the icons ---------------------------------------------------------------------------------
# Each takes the stroke width (from the weight) and returns a fillable path in the unit box.


def _play(w: float) -> QPainterPath:
    return _solid((0.24, 0.15), (0.24, 0.85), (0.83, 0.50))


def _pause(w: float) -> QPainterPath:
    return _union(_rrect(0.21, 0.12, 0.40, 0.88, 0.06), _rrect(0.60, 0.12, 0.79, 0.88, 0.06))


def _backward_end(w: float) -> QPainterPath:
    bar = _rrect(0.13, 0.17, 0.25, 0.83, 0.05)
    tri = _solid((0.85, 0.22), (0.85, 0.78), (0.38, 0.50))
    return _union(bar, tri)


def _forward_end(w: float) -> QPainterPath:
    return _mirror_x(_backward_end(w))


def _xmark(w: float) -> QPainterPath:
    return _union(_stroke(_line((0.22, 0.22), (0.78, 0.78)), w), _stroke(_line((0.78, 0.22), (0.22, 0.78)), w))


def _checkmark(w: float) -> QPainterPath:
    return _stroke(_line((0.18, 0.53), (0.40, 0.76), (0.82, 0.23)), w)


def _mic(w: float) -> QPainterPath:
    body = _rrect(0.345, 0.06, 0.655, 0.60, 0.155)
    cup = QPainterPath()
    cup.moveTo(0.20, 0.42)
    cup.arcTo(QRectF(0.20, 0.14, 0.60, 0.60), 180, 180)
    stem = _line((0.50, 0.74), (0.50, 0.90))
    base = _line((0.35, 0.90), (0.65, 0.90))
    return _union(body, _stroke(cup, w), _stroke(stem, w), _stroke(base, w))


def _clock_arrow(w: float) -> QPainterPath:
    cx, cy, r = 0.53, 0.52, 0.35
    start = 150.0
    arc = QPainterPath()
    arc.moveTo(cx + r * math.cos(math.radians(start)), cy - r * math.sin(math.radians(start)))
    arc.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), start, -300)
    hands = _line((cx, cy - 0.20), (cx, cy), (cx + 0.13, cy + 0.08))
    # The arrowhead at the arc's start, pointing back along it into the gap on the left.
    a = math.radians(start)
    px, py = cx + r * math.cos(a), cy - r * math.sin(a)
    tx, ty = -math.sin(a), -math.cos(a)          # the arc's direction when run backwards
    nx, ny = -ty, tx
    tip = (px + tx * 0.11, py + ty * 0.11)
    b1 = (px - tx * 0.04 + nx * 0.12, py - ty * 0.04 + ny * 0.12)
    b2 = (px - tx * 0.04 - nx * 0.12, py - ty * 0.04 - ny * 0.12)
    head = _solid(tip, b1, b2, round_=w * 0.6)
    return _union(_stroke(arc, w), _stroke(hands, w), head)


def _arrow_left_to_line(w: float) -> QPainterPath:
    bar = _line((0.17, 0.22), (0.17, 0.78))
    shaft = _line((0.86, 0.50), (0.36, 0.50))
    head = _line((0.57, 0.29), (0.36, 0.50), (0.57, 0.71))
    return _union(_stroke(bar, w), _stroke(shaft, w), _stroke(head, w))


def _arrow_right_to_line(w: float) -> QPainterPath:
    return _mirror_x(_arrow_left_to_line(w))


def _arrow_down_to_line(w: float) -> QPainterPath:
    return _rotate(_arrow_left_to_line(w), -90)


def _chevron_left(w: float) -> QPainterPath:
    return _stroke(_line((0.62, 0.17), (0.33, 0.50), (0.62, 0.83)), w)


def _chevron_right(w: float) -> QPainterPath:
    return _mirror_x(_chevron_left(w))


def _chevron_up(w: float) -> QPainterPath:
    return _stroke(_line((0.17, 0.65), (0.50, 0.35), (0.83, 0.65)), w)


def _chevron_down(w: float) -> QPainterPath:
    return _stroke(_line((0.17, 0.35), (0.50, 0.65), (0.83, 0.35)), w)


def _lock(w: float) -> QPainterPath:
    shackle = QPainterPath()
    shackle.moveTo(0.33, 0.45)
    shackle.lineTo(0.33, 0.30)
    shackle.arcTo(QRectF(0.33, 0.13, 0.34, 0.34), 180, -180)
    shackle.lineTo(0.67, 0.45)
    body = _rrect(0.22, 0.45, 0.78, 0.89, 0.09)
    return _union(_stroke(shackle, w), _stroke(body, w))


def _arrow_up_right(w: float) -> QPainterPath:
    shaft = _line((0.25, 0.75), (0.75, 0.25))
    head = _line((0.36, 0.25), (0.75, 0.25), (0.75, 0.64))
    return _union(_stroke(shaft, w), _stroke(head, w))


def _heart(w: float) -> QPainterPath:
    p = QPainterPath()
    p.moveTo(0.50, 0.87)
    p.cubicTo(0.20, 0.66, 0.06, 0.50, 0.06, 0.34)
    p.cubicTo(0.06, 0.21, 0.16, 0.13, 0.28, 0.13)
    p.cubicTo(0.38, 0.13, 0.46, 0.19, 0.50, 0.27)
    p.cubicTo(0.54, 0.19, 0.62, 0.13, 0.72, 0.13)
    p.cubicTo(0.84, 0.13, 0.94, 0.21, 0.94, 0.34)
    p.cubicTo(0.94, 0.50, 0.80, 0.66, 0.50, 0.87)
    p.closeSubpath()
    return p


def _magnifyingglass(w: float) -> QPainterPath:
    ring = _circle(0.43, 0.43, 0.27)
    handle = _line((0.635, 0.635), (0.85, 0.85))
    return _union(_stroke(ring, w), _stroke(handle, w * 1.3))


def _doc_on_doc(w: float) -> QPainterPath:
    front = _rrect(0.17, 0.30, 0.66, 0.90, 0.08)
    back = QPainterPath()
    back.moveTo(0.34, 0.30)
    back.lineTo(0.34, 0.18)
    back.quadTo(0.34, 0.10, 0.42, 0.10)
    back.lineTo(0.75, 0.10)
    back.quadTo(0.83, 0.10, 0.83, 0.18)
    back.lineTo(0.83, 0.64)
    back.quadTo(0.83, 0.72, 0.75, 0.72)
    back.lineTo(0.66, 0.72)
    return _union(_stroke(front, w), _stroke(back, w))


def _ellipsis(w: float) -> QPainterPath:
    r = 0.045 + w * 0.3
    return _union(_circle(0.18, 0.5, r), _circle(0.50, 0.5, r), _circle(0.82, 0.5, r))


def _exclamation_triangle(w: float) -> QPainterPath:
    tri = _line((0.50, 0.13), (0.08, 0.86), (0.92, 0.86), closed=True)
    bar = _line((0.50, 0.40), (0.50, 0.61))
    dot = _circle(0.50, 0.74, w * 0.62)
    return _union(_stroke(tri, w), _stroke(bar, w), dot)


def _gearshape(w: float) -> QPainterPath:
    pts = []
    r_out, r_in, c = 0.43, 0.33, 0.5
    for k in range(8):
        base = k * 45.0
        for ang, r in ((base - 15, r_in), (base - 8, r_out), (base + 8, r_out), (base + 15, r_in)):
            a = math.radians(ang)
            pts.append((c + r * math.cos(a), c - r * math.sin(a)))
    gear = _line(*pts, closed=True)
    hole = _circle(c, c, 0.13)
    return _union(_stroke(gear, w), _stroke(hole, w))


ICONS: dict[str, Callable[[float], QPainterPath]] = {
    "play.fill": _play,
    "pause.fill": _pause,
    "backward.end.fill": _backward_end,
    "forward.end.fill": _forward_end,
    "xmark": _xmark,
    "checkmark": _checkmark,
    "mic.fill": _mic,
    "clock.arrow.circlepath": _clock_arrow,
    "arrow.left.to.line.compact": _arrow_left_to_line,
    "arrow.right.to.line.compact": _arrow_right_to_line,
    "arrow.down.to.line.compact": _arrow_down_to_line,
    "chevron.left": _chevron_left,
    "chevron.right": _chevron_right,
    "chevron.up": _chevron_up,
    "chevron.down": _chevron_down,
    "lock": _lock,
    "arrow.up.right": _arrow_up_right,
    "heart.fill": _heart,
    "magnifyingglass": _magnifyingglass,
    "doc.on.doc": _doc_on_doc,
    "ellipsis": _ellipsis,
    "exclamationmark.triangle": _exclamation_triangle,
    "gearshape": _gearshape,
}

NAMES = tuple(ICONS)

_cache: dict[tuple[str, str], QPainterPath] = {}
_unknown: set[str] = set()


def stroke_width(weight: str | None) -> float:
    return WEIGHTS.get(weight or DEFAULT_WEIGHT, WEIGHTS[DEFAULT_WEIGHT])


def icon_path(name: str, weight: str | None = None) -> QPainterPath:
    """The icon's fillable path in the unit box (0..1, y down); empty for an unknown name."""
    weight = weight or DEFAULT_WEIGHT
    key = (name, weight)
    if key not in _cache:
        fn = ICONS.get(name)
        if fn is None:
            if name not in _unknown:
                _unknown.add(name)
                log.warning("no icon named %r", name)
            return QPainterPath()
        path = fn(stroke_width(weight))
        path.setFillRule(Qt.FillRule.WindingFill)
        _cache[key] = path
    return _cache[key]


def icon_rect(cx: float, cy: float, size: float) -> QRectF:
    """The size x size box an icon of `size` points is drawn into, centred on (cx, cy)."""
    return QRectF(cx - size / 2, cy - size / 2, size, size)


def draw_icon(painter: QPainter, name: str, rect: QRectF, color: QColor, weight: str | None = None) -> None:
    """Fill icon `name` into `rect` (the symbol's point-size box, normally square) in `color`.
    A non-square rect gets the icon at its shorter side, centred."""
    path = icon_path(name, weight)
    if path.isEmpty():
        return
    side = min(rect.width(), rect.height())
    x = rect.x() + (rect.width() - side) / 2
    y = rect.y() + (rect.height() - side) / 2
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(x, y)
    painter.scale(side, side)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(path)
    painter.restore()


def icon_pixmap(name: str, size: float, color: QColor, weight: str | None = None, dpr: float | None = None) -> QPixmap:
    """The icon as a size x size pixmap (logical pixels) at `dpr`, default the primary screen's."""
    if dpr is None:
        screen = QGuiApplication.primaryScreen() if QGuiApplication.instance() is not None else None
        dpr = float(screen.devicePixelRatio()) if screen is not None else 1.0
    px = max(1, math.ceil(size * dpr))
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_icon(p, name, QRectF(0, 0, size, size), color, weight)
    p.end()
    return pm


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------


def _preview_grid(sizes: tuple[float, ...] = (13.0, 20.0), weight: str | None = None):
    """Every icon at each size with its name, on a card."""
    from PySide6.QtWidgets import QWidget

    from . import theme

    cols, cell_w, cell_h, pad = 3, 250.0, 40.0, 16.0
    rows = math.ceil(len(NAMES) / cols)

    class Grid(QWidget):
        def paintEvent(self, ev) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(theme.hairline())
            p.setBrush(theme.surface())
            p.drawRoundedRect(r, 14, 14)
            p.setFont(theme.ui_font(11))
            for i, name in enumerate(NAMES):
                x = pad + (i % cols) * cell_w
                y = pad + (i // cols) * cell_h
                cx = x + 10
                for s in sizes:
                    # a faint box shows the point-size square the icon is drawn into
                    box = icon_rect(cx + s / 2, y + cell_h / 2, s)
                    p.fillRect(box, theme.content(0.05))
                    draw_icon(p, name, box, theme.content(), weight)
                    cx += s + 12
                p.setPen(theme.content_dim())
                p.drawText(QRectF(cx + 4, y, cell_w - (cx - x), cell_h), int(Qt.AlignmentFlag.AlignVCenter), name)
            p.end()

    g = Grid()
    g.setFixedSize(round(2 * pad + cols * cell_w), round(2 * pad + rows * cell_h))
    return g


PREVIEWS = {
    "icons": lambda: _preview_grid(),
    "icons.bold": lambda: _preview_grid((11.0, 17.0), "bold"),
}
