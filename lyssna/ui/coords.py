"""Every coordinate conversion of the Qt interface, in one place. The Mac does the same job in
the screen helpers of legacy/ui/panel.py (Quartz to Cocoa); here there are three spaces:

1. Physical screen pixels, origin at the top-left of the primary monitor, negative on monitors
   to its left or above it. The Windows mouse hook (win/events.py) and UI Automation
   (win/capture.py, ax_selection bounds) report these.
2. Qt logical coordinates, origin at the top-left of the primary monitor, y growing downward.
   Every widget, window geometry and QCursor.pos() is in these. Each screen has its own scale
   factor (QScreen.devicePixelRatio()).
3. Mac-style rects, (x, y, w, h) with y growing upward from the bottom of the primary screen.
   The shared geometry in layout/pill.py, layout/toast.py and layout/button.py speaks this, so
   the Qt screens call those functions unchanged through to_mac_rect and from_mac_rect.

Rects are plain (x, y, w, h) tuples of floats. Functions that take a rect also accept a
QRect, a QRectF or a Mac frame ((x, y), (w, h)). Points are (x, y) tuples.

The pure functions (no QScreen) are at the top and are tested on any system. The readers
below them ask Qt about the screens and the cursor."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF
from PySide6.QtGui import QCursor, QGuiApplication, QScreen

from .. import host

Rect = tuple[float, float, float, float]

# -- pure --------------------------------------------------------------------------------------


def as_rect(rect) -> Rect:
    """(x, y, w, h) from a tuple, a Mac frame ((x, y), (w, h)), a QRect or a QRectF."""
    if isinstance(rect, (QRect, QRectF)):
        return (float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))
    if len(rect) == 2:
        (x, y), (w, h) = rect
        return (float(x), float(y), float(w), float(h))
    x, y, w, h = rect
    return (float(x), float(y), float(w), float(h))


def as_frame(rect) -> tuple[tuple[float, float], tuple[float, float]]:
    """The Mac frame form ((x, y), (w, h)) that layout/pill.py's frame functions take."""
    x, y, w, h = as_rect(rect)
    return ((x, y), (w, h))


def as_qrectf(rect) -> QRectF:
    x, y, w, h = as_rect(rect)
    return QRectF(x, y, w, h)


def to_mac_rect(qt_rect, primary_height: float) -> Rect:
    """A Qt logical rect (top-left origin, y down) as a Mac-style rect (bottom-left origin of
    the primary screen, y up). `primary_height` is the primary screen's logical height."""
    x, y, w, h = as_rect(qt_rect)
    return (x, primary_height - (y + h), w, h)


def from_mac_rect(mac_rect, primary_height: float) -> Rect:
    """The inverse of to_mac_rect: a Mac-style rect (or frame) as a Qt logical rect. The flip is
    its own inverse, so this is the same arithmetic."""
    return to_mac_rect(mac_rect, primary_height)


def to_mac_point(x: float, y: float, primary_height: float) -> tuple[float, float]:
    return (x, primary_height - y)


def from_mac_point(x: float, y: float, primary_height: float) -> tuple[float, float]:
    return (x, primary_height - y)


def flip_rect(rect, height: float) -> Rect:
    """A rect inside a parent `height` tall, between y-up (the Mac's view coordinates, as
    layout.pill.lay returns them) and y-down (Qt's widget coordinates). Its own inverse."""
    x, y, w, h = as_rect(rect)
    return (x, height - y - h, w, h)


def clamp_to(rect, frame) -> Rect:
    """`rect` moved (never resized) so it lies inside `frame`, both in the same space. The
    twin of the Mac clamp_to, which returns only the origin; take [:2] for that."""
    x, y, w, h = as_rect(rect)
    fx, fy, fw, fh = as_rect(frame)
    x = min(max(x, fx), fx + fw - w)
    y = min(max(y, fy), fy + fh - h)
    return (x, y, w, h)


def _contains(rect: Rect, x: float, y: float) -> bool:
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def _dist2(rect: Rect, x: float, y: float) -> float:
    rx, ry, rw, rh = rect
    dx = max(rx - x, 0.0, x - (rx + rw))
    dy = max(ry - y, 0.0, y - (ry + rh))
    return dx * dx + dy * dy


def physical_geometry(logical: Rect, scale: float) -> Rect:
    """A screen's rectangle in physical pixels from its Qt logical geometry and scale factor.

    Qt 6 on Windows (per-monitor DPI aware, as Qt sets itself up) keeps each screen's top-left
    corner at its physical position and divides only the size by the scale factor
    (QHighDpi::fromNativeScreenGeometry). So the physical rect has the same origin and a size
    `scale` times larger. With mixed scales this makes the logical desktop discontinuous (gaps
    or overlaps between monitors), which is why conversions pick the screen first, by physical
    position, and scale about that screen's origin."""
    x, y, w, h = logical
    return (x, y, w * scale, h * scale)


def map_physical_to_logical(x: float, y: float, screens: Sequence[tuple[Rect, float]]) -> tuple[float, float]:
    """A physical point to Qt logical coordinates, given each screen as (logical geometry, scale).
    The screen whose physical rect holds the point (else the nearest one) decides the scale,
    applied about that screen's origin."""
    if not screens:
        return (x, y)
    pick = None
    for logical, scale in screens:
        if _contains(physical_geometry(logical, scale), x, y):
            pick = (logical, scale)
            break
    if pick is None:
        pick = min(screens, key=lambda s: _dist2(physical_geometry(*s), x, y))
    (ox, oy, _, _), scale = pick
    scale = scale or 1.0
    return (ox + (x - ox) / scale, oy + (y - oy) / scale)


def map_logical_to_physical(x: float, y: float, screens: Sequence[tuple[Rect, float]]) -> tuple[float, float]:
    """The inverse of map_physical_to_logical: the screen whose logical rect holds the point
    (else the nearest) decides the scale."""
    if not screens:
        return (x, y)
    pick = next(((lg, s) for lg, s in screens if _contains(lg, x, y)), None)
    if pick is None:
        pick = min(screens, key=lambda s: _dist2(s[0], x, y))
    (ox, oy, _, _), scale = pick
    return (ox + (x - ox) * scale, oy + (y - oy) * scale)


def map_physical_rect_to_logical(rect, screens: Sequence[tuple[Rect, float]]) -> Rect:
    """A physical rect to logical: its top-left corner picks the screen and both corners scale
    about that screen's origin, so a selection that spills onto a neighbour keeps its size."""
    x, y, w, h = as_rect(rect)
    if not screens:
        return (x, y, w, h)
    lx, ly = map_physical_to_logical(x, y, screens)
    _, scale = next(((lg, s) for lg, s in screens if _contains(physical_geometry(lg, s), x, y)),
                    min(screens, key=lambda s: _dist2(physical_geometry(*s), x, y)))
    scale = scale or 1.0
    return (lx, ly, w / scale, h / scale)


# -- Qt readers --------------------------------------------------------------------------------


def _screens() -> list[QScreen]:
    return list(QGuiApplication.screens()) if QGuiApplication.instance() is not None else []


def _screen_table() -> list[tuple[Rect, float]]:
    return [(as_rect(s.geometry()), float(s.devicePixelRatio())) for s in _screens()]


def physical_to_logical(x: float, y: float) -> tuple[float, float]:
    """A point from the system's hooks or UI Automation in Qt logical coordinates. Only where
    those report physical pixels (host.window.POINTS_ARE_PHYSICAL: Windows) is there anything to
    convert; the Mac's event taps already speak logical units, so the point comes back unchanged."""
    if not host.window.POINTS_ARE_PHYSICAL:
        return (float(x), float(y))
    return map_physical_to_logical(float(x), float(y), _screen_table())


def logical_to_physical(x: float, y: float) -> tuple[float, float]:
    """A Qt logical point in the hooks' pixels (unchanged where those are logical)."""
    if not host.window.POINTS_ARE_PHYSICAL:
        return (float(x), float(y))
    return map_logical_to_physical(float(x), float(y), _screen_table())


def physical_rect_to_logical(rect) -> Rect:
    """A rect from UI Automation (ax_selection bounds) in Qt logical coordinates (unchanged
    where the system already reports logical units)."""
    if not host.window.POINTS_ARE_PHYSICAL:
        return as_rect(rect)
    return map_physical_rect_to_logical(rect, _screen_table())


def primary_screen() -> QScreen | None:
    return QGuiApplication.primaryScreen() if QGuiApplication.instance() is not None else None


def primary_height() -> float:
    """The primary screen's logical height: the y flip between Qt and Mac-style rects."""
    s = primary_screen()
    return float(s.geometry().height()) if s is not None else 0.0


def screen_at(x: float, y: float) -> QScreen | None:
    """The screen holding the logical point (x, y), else the nearest one."""
    screens = _screens()
    if not screens:
        return None
    for s in screens:
        if _contains(as_rect(s.geometry()), x, y):
            return s
    return min(screens, key=lambda s: _dist2(as_rect(s.geometry()), x, y))


def cursor_pos() -> tuple[float, float]:
    """The mouse in Qt logical coordinates."""
    p = QCursor.pos()
    return (float(p.x()), float(p.y()))


def screen_under_mouse() -> QScreen | None:
    return screen_at(*cursor_pos())


def visible_frame(screen: QScreen | None = None) -> Rect:
    """The usable area of `screen` (the whole screen minus the taskbar), Qt logical. Defaults to
    the screen the mouse is on, where the user is working."""
    s = screen or screen_under_mouse()
    return as_rect(s.availableGeometry()) if s is not None else (0.0, 0.0, 0.0, 0.0)


def screen_frame(screen: QScreen | None = None) -> Rect:
    """The whole of `screen` (taskbar included), Qt logical."""
    s = screen or screen_under_mouse()
    return as_rect(s.geometry()) if s is not None else (0.0, 0.0, 0.0, 0.0)


# -- the same readers in Mac-style coordinates, for layout/pill.py and friends ----------------


def visible_frame_mac(screen: QScreen | None = None) -> Rect:
    """visible_frame as a Mac-style rect: the `vf` the shared dock geometry takes."""
    return to_mac_rect(visible_frame(screen), primary_height())


def screen_frame_mac(screen: QScreen | None = None) -> Rect:
    return to_mac_rect(screen_frame(screen), primary_height())


def cursor_pos_mac() -> tuple[float, float]:
    """The mouse as a Mac-style point (the twin of NSEvent.mouseLocation)."""
    return to_mac_point(*cursor_pos(), primary_height())


def screen_at_mac(x: float, y: float) -> QScreen | None:
    """The screen holding a Mac-style point."""
    return screen_at(*from_mac_point(x, y, primary_height()))


def qt_rect_from_mac(mac_rect) -> Rect:
    """A Mac-style rect or frame (as the shared geometry returns it) as a Qt logical rect."""
    return from_mac_rect(mac_rect, primary_height())


def mac_rect_from_qt(qt_rect) -> Rect:
    return to_mac_rect(qt_rect, primary_height())


def point(p: QPoint | QPointF | Iterable[float]) -> tuple[float, float]:
    """(x, y) floats from a QPoint, a QPointF or a pair."""
    if isinstance(p, (QPoint, QPointF)):
        return (float(p.x()), float(p.y()))
    x, y = p
    return (float(x), float(y))
