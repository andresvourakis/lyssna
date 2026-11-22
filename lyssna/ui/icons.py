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

# Stroke width per weight, as a fraction of the point size. Measured on the Mac's symbols at
# 100 pt (the cross, the chevrons, the arrows): an SF stroke at a weight is this wide at any size.
WEIGHTS = {
    "regular": 0.085,
    "medium": 0.102,
    "semibold": 0.118,
    "bold": 0.148,
    "heavy": 0.170,
}
DEFAULT_WEIGHT = "semibold"  # symbol_button's default on the Mac

# -- path helpers ------------------------------------------------------------------------------
# The icons are built around the box's centre (0, 0), y down, one unit per point, and moved into
# the unit box by icon_path. Each glyph's ink box, centre offset and ink amount were measured
# against the Mac's symbol at every size the interface uses (within half a pixel and about 5
# percent), so the drawing is our own and only the size, weight and position follow the Mac's.


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


def _inset(pts: list[tuple[float, float]], d: float) -> list[tuple[float, float]]:
    """The convex polygon `pts` with every edge moved d inwards."""
    n = len(pts)
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    sign = 1.0 if area > 0 else -1.0
    lines = []
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        nx, ny = -dy / length * sign, dx / length * sign  # the inward normal
        lines.append(((x0 + nx * d, y0 + ny * d), (dx, dy)))
    inner = []
    for i in range(n):
        (px, py), (dx, dy) = lines[i - 1]
        (qx, qy), (ex, ey) = lines[i]
        det = dx * ey - dy * ex
        t = ((qx - px) * ey - (qy - py) * ex) / det
        inner.append((px + dx * t, py + dy * t))
    return inner


def _rounded_poly(pts: list[tuple[float, float]], r: float) -> QPainterPath:
    """A filled convex polygon whose corners are rounded with radius r, its edges staying on the
    polygon's own edges: the polygon moved in by r on every side, then grown back by a round r."""
    poly = _line(*_inset(pts, r), closed=True)
    return poly.united(_stroke(poly, 2 * r))


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
    return QTransform(-1, 0, 0, 1, 0, 0).map(path)


def _rotate(path: QPainterPath, degrees: float) -> QPainterPath:
    return QTransform().rotate(degrees).map(path)


# -- the icons ---------------------------------------------------------------------------------
# Each takes the stroke width (from the weight) and returns a fillable path around (0, 0). The
# solid shapes keep one size at every weight; the Mac uses them at semibold only.


def _play(w: float) -> QPainterPath:
    return _rounded_poly([(-0.35, -0.49), (-0.35, 0.49), (0.53, 0.0)], 0.085)


def _pause(w: float) -> QPainterPath:
    return _union(_rrect(-0.33, -0.42, -0.06, 0.42, 0.065), _rrect(0.06, -0.42, 0.33, 0.42, 0.065))


def _forward_end(w: float) -> QPainterPath:
    tri = _rounded_poly([(-0.49, -0.445), (-0.49, 0.445), (0.29, 0.0)], 0.07)
    bar = _rrect(0.22, -0.39, 0.49, 0.39, 0.065)
    return _union(tri, bar)


def _backward_end(w: float) -> QPainterPath:
    return _mirror_x(_forward_end(w))


def _xmark(w: float) -> QPainterPath:
    e = 0.363 - 0.25 * w  # the strokes' ends: a bolder cross is a little shorter, as SF's is
    return _union(_stroke(_line((-e, -e), (e, e)), w), _stroke(_line((e, -e), (-e, e)), w))


def _checkmark(w: float) -> QPainterPath:
    w *= 1.06  # like the chevrons
    return _stroke(_line((-0.39, 0.04), (-0.107, 0.405 - w / 3), (0.374, -0.381 + w / 6)), w)


def _arrow_right_to_line(w: float) -> QPainterPath:
    tip = 0.31 - w / 2
    shaft = _line((-0.46 + w / 2, 0.0), (tip, 0.0))
    head = _line((-0.035, -0.275), (tip, 0.0), (-0.035, 0.275))
    bar = _line((0.45 - w / 2, -0.37 + w / 2), (0.45 - w / 2, 0.37 - w / 2))
    return _union(_stroke(shaft, w), _stroke(head, w * 0.92), _stroke(bar, w))


def _arrow_left_to_line(w: float) -> QPainterPath:
    return _mirror_x(_arrow_right_to_line(w))


def _arrow_down_to_line(w: float) -> QPainterPath:
    return _rotate(_arrow_right_to_line(w), 90)


def _chevron_right(w: float) -> QPainterPath:
    w *= 1.06  # SF's chevrons are a touch heavier than its crosses at the same weight
    return _stroke(_line((-0.20 + w / 2, -0.44 + w / 2), (0.32 - w / 2, 0.0), (-0.20 + w / 2, 0.44 - w / 2)), w)


def _chevron_left(w: float) -> QPainterPath:
    return _mirror_x(_chevron_right(w))


def _chevron_up(w: float) -> QPainterPath:
    w *= 1.06
    return _stroke(_line((-0.45 + w / 2, 0.26 - w / 2), (0.0, -0.26 + w / 2), (0.45 - w / 2, 0.26 - w / 2)), w)


def _chevron_down(w: float) -> QPainterPath:
    return _stroke(_line((-0.33, -0.15), (0.0, 0.15), (0.33, -0.15)), w)


def _mic(w: float) -> QPainterPath:
    body = _rrect(-0.18, -0.54, 0.18, 0.16, 0.18)
    w *= 0.92  # the stand is a little lighter than a semibold stroke
    h = w / 2
    cup = QPainterPath()
    cup.moveTo(-0.395 + h, -0.15 + h)
    cup.lineTo(-0.395 + h, 0.0)
    cup.arcTo(QRectF(-0.395 + h, 0.35 - h - 2 * (0.35 - h), 2 * (0.395 - h), 2 * (0.35 - h)), 180, 180)
    cup.lineTo(0.395 - h, -0.15 + h)
    stem = _line((0.0, 0.35 - h), (0.0, 0.49))
    base = _line((-0.285 + h, 0.54 - h), (0.285 - h, 0.54 - h))
    return _union(body, _stroke(cup, w), _stroke(stem, w), _stroke(base, w))


def _clock_arrow(w: float) -> QPainterPath:
    cx, cy, r = 0.005, 0.0, 0.50 - w / 2
    start, end = 165.0, -142.7  # the arc's ends, degrees counterclockwise from three o'clock
    arc = QPainterPath()
    arc.moveTo(cx + r * math.cos(math.radians(start)), cy - r * math.sin(math.radians(start)))
    arc.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), start, end - start)
    hw = w * 0.78  # the hands are drawn thinner than the ring
    hands = _line((cx, -0.28 + hw / 2), (cx, 0.03), (0.125, 0.185))
    head = _rounded_poly([(-0.607, -0.13), (-0.273, -0.13), (-0.44, 0.15)], 0.02)
    return _union(_stroke(arc, w), _stroke(hands, hw), head)


def _lock(w: float) -> QPainterPath:
    w *= 0.9  # the lock and the pages are drawn a little lighter than the arrows
    h = w / 2
    shackle = QPainterPath()
    shackle.moveTo(-0.25 + h, -0.05)
    shackle.lineTo(-0.25 + h, -0.23)
    shackle.arcTo(QRectF(-0.25 + h, -0.48 + h, 2 * (0.25 - h), 2 * (0.25 - h)), 180, -180)
    shackle.lineTo(0.25 - h, -0.05)
    body = _rrect(-0.335 + h, -0.09 + h, 0.335 - h, 0.48 - h, 0.07)
    return _union(_stroke(shackle, w), _stroke(body, w))


def _arrow_up_right(w: float) -> QPainterPath:
    h = w / 2
    e = 0.37 - h
    shaft = _line((-e, e), (e, -e))
    head = _line((-0.23 + h, -e), (e, -e), (e, 0.23 - h))
    return _union(_stroke(shaft, w), _stroke(head, w))


def _heart(w: float) -> QPainterPath:
    p = QPainterPath()
    p.moveTo(0.0, 0.45)
    p.cubicTo(-0.32, 0.27, -0.50, 0.07, -0.50, -0.16)
    p.cubicTo(-0.50, -0.34, -0.37, -0.45, -0.23, -0.45)
    p.cubicTo(-0.11, -0.45, -0.03, -0.39, 0.0, -0.32)
    p.cubicTo(0.03, -0.39, 0.11, -0.45, 0.23, -0.45)
    p.cubicTo(0.37, -0.45, 0.50, -0.34, 0.50, -0.16)
    p.cubicTo(0.50, 0.07, 0.32, 0.27, 0.0, 0.45)
    p.closeSubpath()
    return p


def _magnifyingglass(w: float) -> QPainterPath:
    c, r = -0.09, 0.39 - w / 2
    ring = _circle(c, c, r)
    hw = w * 1.36  # the handle is heavier than the ring
    a = c + r * math.sqrt(0.5)
    handle = _line((a, a), (0.475 - hw / 2, 0.475 - hw / 2))
    return _union(_stroke(ring, w), _stroke(handle, hw))


def _doc_outline(x0: float, y0: float, x1: float, y1: float, ear: float, fold_x: float, fold_y: float,
                 r: float) -> tuple[QPainterPath, QPainterPath]:
    """A page with its top right corner cut off `ear` along each side and folded down: the outline,
    and the fold's two edges (fold_x in from the right, fold_y down from the top)."""
    p = QPainterPath()
    p.moveTo(x0 + r, y0)
    p.lineTo(x1 - ear, y0)
    p.lineTo(x1, y0 + ear)
    p.lineTo(x1, y1 - r)
    p.quadTo(x1, y1, x1 - r, y1)
    p.lineTo(x0 + r, y1)
    p.quadTo(x0, y1, x0, y1 - r)
    p.lineTo(x0, y0 + r)
    p.quadTo(x0, y0, x0 + r, y0)
    p.closeSubpath()
    fold = _line((x1 - fold_x, y0), (x1 - fold_x, y0 + fold_y), (x1, y0 + fold_y))
    return p, fold


def _doc_on_doc(w: float) -> QPainterPath:
    w *= 0.9
    h = w / 2
    front, front_fold = _doc_outline(-0.51 + h, -0.37 + h, 0.26 - h, 0.61 - h, 0.33, 0.37, 0.37, 0.10)
    back, back_fold = _doc_outline(-0.27 + h, -0.61 + h, 0.50 - h, 0.37 - h, 0.31, 0.315, 0.335, 0.10)
    front_ink = _union(_stroke(front, w), _stroke(front_fold, w))
    covered = front.united(front_ink)  # the back page hides behind the whole front page
    back_ink = _union(_stroke(back, w), _stroke(back_fold, w)).subtracted(covered)
    return front_ink.united(back_ink)


def _ellipsis(w: float) -> QPainterPath:
    r = 0.04 + w * 0.59
    return _union(_circle(-0.36, 0.0, r), _circle(0.0, 0.0, r), _circle(0.36, 0.0, r))


def _exclamation_triangle(w: float) -> QPainterPath:
    outer = [(0.0, -0.64), (-0.62, 0.46), (0.62, 0.46)]  # sharp; the rounding takes the corners in
    rw, corner = w * 0.9, 0.16
    ring = _rounded_poly(outer, corner).subtracted(_rounded_poly(_inset(outer, rw), corner - rw))
    bar = _line((0.0, -0.20 + w / 2), (0.0, 0.13 - w / 2))
    dot = _circle(0.0, 0.24, w * 0.57)
    return ring.united(_stroke(bar, w)).united(dot)  # united, not _union: the ring's hole must stay open


def _gearshape(w: float) -> QPainterPath:
    pts = []
    r_out, r_in = 0.43, 0.33
    for k in range(8):
        base = k * 45.0
        for ang, r in ((base - 15, r_in), (base - 8, r_out), (base + 8, r_out), (base + 15, r_in)):
            a = math.radians(ang)
            pts.append((r * math.cos(a), -r * math.sin(a)))
    gear = _line(*pts, closed=True)
    hole = _circle(0.0, 0.0, 0.13)
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

# Per-size nudges, in logical pixels (x right, y down), for the icons whose centre still missed
# the Mac's by more than half a pixel after the shapes were tuned: AppKit sizes each symbol's
# image to whole quarter points and places it on the pixel grid, which moves its ink a little
# off the button's centre. The numbers are the Mac's ink-box centre minus ours, measured at 2x
# by the round 1B icon comparison (scratchpad icons1b/measure.py, after/table.md), keyed by
# (symbol, point size, weight).
OPTICAL: dict[tuple[str, float, str], tuple[float, float]] = {
    ("xmark", 11.0, "bold"): (-0.5, 0.5),                      # card and pill close
    ("clock.arrow.circlepath", 11.0, "semibold"): (-0.25, 0.5),  # idle menu history
    ("arrow.up.right", 10.0, "medium"): (-0.5, 0.5),           # setup privacy tag
    ("arrow.up.right", 10.0, "semibold"): (-0.5, 0.5),         # setup Get a key
}

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
        # Each path keeps its own fill rule: _union's winding fill, or the boolean operations' own.
        path = QTransform.fromTranslate(0.5, 0.5).map(fn(stroke_width(weight)))
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
    dx, dy = OPTICAL.get((name, side, weight or DEFAULT_WEIGHT), (0.0, 0.0))
    x = rect.x() + (rect.width() - side) / 2 + dx
    y = rect.y() + (rect.height() - side) / 2 + dy
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(x, y)
    painter.scale(side, side)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(path)
    painter.restore()


def icon_pixmap(name: str, size: float, color: QColor, weight: str | None = None, dpr: float | None = None,
                box: float | None = None) -> QPixmap:
    """The icon as a size x size pixmap (logical pixels) at `dpr`, default the primary screen's.
    With `box`, the pixmap is box x box with the size-point icon centred in it."""
    if dpr is None:
        screen = QGuiApplication.primaryScreen() if QGuiApplication.instance() is not None else None
        dpr = float(screen.devicePixelRatio()) if screen is not None else 1.0
    side = max(size, box or 0.0)
    px = max(1, math.ceil(side * dpr))
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_icon(p, name, icon_rect(side / 2, side / 2, size), color, weight)
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
