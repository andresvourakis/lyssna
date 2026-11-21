"""A frameless, translucent, always-on-top window that never takes focus: the base of the play
button, the player, the toasts and the tips. The twin of FloatingPanel and DragPanel in
ui/panel.py.

Never taking focus is the point. The play button and the player sit over other apps while the
user's text stays selected there, so showing or clicking one must not activate it: Qt's Tool,
frameless, stay-on-top and does-not-accept-focus flags plus WA_ShowWithoutActivating, and on
Windows WS_EX_NOACTIVATE and WS_EX_TOOLWINDOW (no taskbar button) on the native window.

As on the Mac, FloatingPanel owns its window (`self.panel`, a PanelWindow) instead of being one,
so subclasses keep plain attributes like `width` and read like their Mac twins. Subclasses lay
out their content in build(content); `content` is a transparent widget covering the surface.

The surface is a rounded rectangle with a 1 px hairline, painted here. Windows draws no shadow
under a translucent frameless window, so the panel paints a soft one itself inside a transparent
margin around the surface: the window is larger than the surface by `margin` on every side.
Every geometry method (show_at, set_frame, frame, contains_screen_point) speaks in SURFACE
coordinates, Qt logical pixels with the origin top-left, so callers never think about the margin.
The *_mac variants take and return Mac-style rects (y up) for the shared geometry.

Dragging follows DragPanel.sendEvent_: only when `draggable`; widgets marked
`consumes_mouse = True` (the scrub bar) get their own events; a press that travels less than
shared.layout.DRAG_SLOP is a click, delivered to the button under the mouse or else to clicked().

The Mac panel also repairs itself after a macOS window-server quirk; Windows has no such quirk,
so that part is not ported."""

from __future__ import annotations

import logging
import sys
import weakref
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QWidget,
)

from ..shared.layout import DRAG_SLOP
from . import coords, theme

log = logging.getLogger("lyssna.ui.qt.panel")

# The painted shadow: a wide soft layer and a tight contact layer, both a little below the
# surface, close to what macOS draws under a borderless panel. MARGIN holds the softer one.
MARGIN = 18.0
SHADOW_LAYERS = (  # (blur radius, y offset, alpha)
    (14.0, 4.0, 0.30),
    (3.0, 0.5, 0.14),
)
FRAME_SECS = 0.25  # NSAnimationContext's default, for animated set_frame

# Windows extended styles.
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0020, 0x0040
HWND_TOPMOST = -1

WINDOW_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)


# -- Windows native styles ---------------------------------------------------------------------


def _user32():
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    long_ptr = ctypes.c_ssize_t
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongPtrW.restype = long_ptr
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr]
    u.SetWindowLongPtrW.restype = long_ptr
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.SetWindowPos.restype = wintypes.BOOL
    return u


def set_ex_style(win_id: int, add: int, remove: int = 0) -> None:
    """OR `add` into (and clear `remove` from) a native window's extended style. Windows only."""
    if sys.platform != "win32":
        return
    try:
        u = _user32()
        hwnd = int(win_id)
        style = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        new = (style | add) & ~remove
        if new != style:
            u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
            u.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    except Exception:
        log.warning("could not set the window's extended style", exc_info=True)


def _bring_to_top(win_id: int) -> None:
    """Topmost and shown, without activating. Windows only."""
    try:
        u = _user32()
        u.SetWindowPos(int(win_id), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    except Exception:
        log.warning("could not bring the window to the top", exc_info=True)


# -- the shadow --------------------------------------------------------------------------------


def _blur(img: QImage, radius: float) -> QImage:
    """`img` blurred by `radius` logical pixels (QGraphicsBlurEffect's Gaussian-like blur)."""
    dpr = img.devicePixelRatio()
    scene = QGraphicsScene()
    pixmap = QPixmap.fromImage(img)
    pixmap.setDevicePixelRatio(1.0)  # the scene works in device pixels; keeping the ratio shrinks the shadow on scaled displays
    item = QGraphicsPixmapItem(pixmap)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius * dpr)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(0, 0, out.width(), out.height()), QRectF(0, 0, img.width(), img.height()))
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def shadow_image(shape: QPainterPath, size: tuple[float, float], offset: tuple[float, float], dpr: float) -> QImage:
    """The panel's shadow for `shape` (in surface coordinates) on a transparent image `size`
    logical pixels big, with the surface's top-left at `offset`."""
    w, h = size
    img = QImage(max(1, round(w * dpr)), max(1, round(h * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for blur, dy, alpha in SHADOW_LAYERS:
        layer = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.GlobalColor.transparent)
        q = QPainter(layer)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        q.translate(offset[0], offset[1] + dy)
        q.fillPath(shape, QColor.fromRgbF(0, 0, 0, alpha))
        q.end()
        p.drawImage(QPointF(0, 0), _blur(layer, blur))
    p.end()
    return img


# -- the window --------------------------------------------------------------------------------


class PanelWindow(QWidget):
    """The native window a FloatingPanel owns. Paints the shadow and the surface; the owner's
    content widget sits on top of the surface."""

    def __init__(self, owner: FloatingPanel):
        super().__init__(None, WINDOW_FLAGS)
        self._owner = owner  # strong: whoever keeps only the window keeps the panel
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)  # the preview on a Mac
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)  # it is never the active window
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._shadow_key = None
        self._shadow: QImage | None = None

    @property
    def owner(self) -> FloatingPanel | None:
        return self._owner

    def event(self, ev) -> bool:
        if ev.type() in (QEvent.Type.WinIdChange, QEvent.Type.Show):
            owner = self.owner
            if owner is not None:
                owner._apply_native_styles()
        return super().event(ev)

    def paintEvent(self, ev) -> None:
        owner = self.owner
        if owner is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if owner.margin > 0:
            shape = owner.shadow_path()
            if not shape.isEmpty():
                p.drawImage(QPointF(0, 0), self._shadow_for(shape, owner.margin, p.device().devicePixelRatioF()))
        if owner._paint_surface:
            owner.paint_surface(p, owner.surface_rect())
        p.end()

    def _shadow_for(self, shape: QPainterPath, margin: float, dpr: float) -> QImage:
        b = shape.boundingRect()
        key = (self.width(), self.height(), dpr, margin, round(b.x(), 2), round(b.y(), 2), round(b.width(), 2),
               round(b.height(), 2), shape.elementCount())
        if key != self._shadow_key or self._shadow is None:
            self._shadow = shadow_image(shape, (self.width(), self.height()), (margin, margin), dpr)
            self._shadow_key = key
        return self._shadow


class _DragFilter(QObject):
    """One application-wide event filter that hands each FloatingPanel the mouse presses,
    moves and releases inside its window before its child widgets see them (the twin of
    DragPanel.sendEvent_)."""

    TYPES = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove,
             QEvent.Type.MouseButtonRelease)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() not in self.TYPES or not isinstance(obj, QWidget):
            return False
        win = obj.window()
        if not isinstance(win, PanelWindow):
            return False
        owner = win.owner
        if owner is None:
            return False
        try:
            return owner._mouse(obj, ev)
        except Exception:
            log.exception("panel mouse handling failed")
            return False


_filter: _DragFilter | None = None


def _install_filter() -> None:
    global _filter
    app = QApplication.instance()
    if _filter is None and app is not None:
        _filter = _DragFilter()
        app.installEventFilter(_filter)


def _color(c) -> QColor | None:
    return c() if callable(c) else c


# -- the panel ---------------------------------------------------------------------------------


class FloatingPanel:
    """Owns a PanelWindow (`self.panel`). Subclasses set the class attributes, build content in
    build(content) and override the drag hooks. All geometry is the surface's, Qt logical."""

    width = 100.0
    height = 40.0
    corner_radius = 12.0
    adaptive = False   # True: the brand surface (cream or ink, follows light/dark mode) instead of the fixed dark one
    draggable = False  # True: the user can drag the panel around
    shadow = True      # False: no painted shadow and no margin (the drop-zone hint)

    def __init__(self) -> None:
        _install_filter()
        self.margin = MARGIN if self.shadow else 0.0
        self._paint_surface = True
        self._click_through = False
        self._alpha = 1.0
        self._press = None  # (cursor global, surface origin, control) while a press is live
        self._dragging = False
        self._frame_anim: QVariantAnimation | None = None
        self._alpha_anim: QVariantAnimation | None = None
        self.fill_color: QColor | Callable[[], QColor] | None = None    # overrides the surface fill
        self.border_color: QColor | Callable[[], QColor] | None = None  # overrides the border (no_border: none)
        self.no_border = False
        self.panel = PanelWindow(self)
        self.content = QWidget(self.panel)
        self.content.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._size = (float(self.width), float(self.height))
        self.panel.resize(round(self.width + 2 * self.margin), round(self.height + 2 * self.margin))
        self._place_content()
        self.panel.winId()  # the native window exists from here on, so its styles can be set
        self._apply_native_styles()
        ref = weakref.ref(self)

        def repaint() -> None:
            me = ref()
            if me is not None:
                me.panel.update()
        self._unsubscribe = theme.on_scheme_change(repaint)
        self.build(self.content)

    # -- for subclasses -------------------------------------------------------------------------

    def build(self, content: QWidget) -> None:  # override
        pass

    def drag_began(self) -> None:  # override
        pass

    def drag_moved(self, x: float, y: float) -> None:  # override; the cursor, Qt logical
        pass

    def drag_ended(self, x: float, y: float) -> None:  # override; the cursor, Qt logical
        pass

    def clicked(self) -> None:  # override; a click that hit no button
        pass

    def surface_fill(self) -> QColor:
        """The surface colour, read at paint time: `fill_color` when set, else the adaptive brand
        surface or the fixed dark one."""
        if self.fill_color is not None:
            return _color(self.fill_color)
        return theme.surface(0.97) if self.adaptive else theme.panel_bg()

    def surface_border(self) -> QColor | None:
        if self.no_border:
            return None
        if self.border_color is not None:
            return _color(self.border_color)
        return theme.hairline() if self.adaptive else theme.panel_border()

    def surface_radius(self) -> float:
        return float(self.corner_radius)

    def surface_shape(self, rect: QRectF | None = None) -> QPainterPath:
        """The surface's outline in surface coordinates (or in `rect`)."""
        w, h = self._size
        rect = rect or QRectF(0, 0, w, h)
        r = min(self.surface_radius(), rect.width() / 2, rect.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        return path

    def paint_surface(self, painter: QPainter, rect: QRectF) -> None:
        """Paint the surface into `rect` (window coordinates). Override for another look."""
        painter.fillPath(self.surface_shape(rect), self.surface_fill())
        border = self.surface_border()
        if border is not None and border.alpha() > 0:
            painter.setPen(QPen(border, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.surface_shape(rect.adjusted(0.5, 0.5, -0.5, -0.5)))

    def shadow_path(self) -> QPainterPath:
        """What casts the shadow, in surface coordinates: the surface when it is painted, else
        the content's `shadow_path()` when it has one (a form that draws its own capsules)."""
        if self._paint_surface:
            return self.surface_shape()
        fn = getattr(self.content, "shadow_path", None)
        return fn() if callable(fn) else QPainterPath()

    # -- geometry (surface coordinates, Qt logical) ---------------------------------------------

    def surface_rect(self) -> QRectF:
        """The surface inside the window (window coordinates): the window less the margin."""
        w, h = self._size
        return QRectF(self.margin, self.margin, w, h)

    def frame(self) -> coords.Rect:
        """The surface on screen, (x, y, w, h), Qt logical, top-left origin."""
        g = self.panel.geometry()
        w, h = self._size
        return (float(g.x()) + self.margin, float(g.y()) + self.margin, w, h)

    def frame_mac(self) -> coords.Rect:
        """frame() as a Mac-style rect (y up), for the shared geometry."""
        return coords.mac_rect_from_qt(self.frame())

    def set_frame(self, rect, animate: bool = False, duration: float = FRAME_SECS, done: Callable[[], None] | None = None) -> None:
        """Move and size the surface to `rect` (x, y, w, h, a QRect(F) or a Mac frame tuple; Qt
        logical), optionally animated (ease in and out, `done` called at the end)."""
        target = coords.as_rect(rect)
        if self._frame_anim is not None:
            self._frame_anim.stop()
            self._frame_anim = None
        if not animate or theme.reduce_motion() or duration <= 0 or not self.visible():
            self._apply_frame(target)
            if done is not None:
                done()
            return
        anim = QVariantAnimation()
        anim.setStartValue(coords.as_qrectf(self.frame()))
        anim.setEndValue(coords.as_qrectf(target))
        anim.setDuration(round(duration * 1000))
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.valueChanged.connect(lambda v: self._apply_frame(coords.as_rect(v)))

        def finished() -> None:
            self._apply_frame(target)
            if self._frame_anim is anim:
                self._frame_anim = None
            if done is not None:
                done()
        anim.finished.connect(finished)
        self._frame_anim = anim
        anim.start()

    def set_frame_mac(self, mac_rect, animate: bool = False, duration: float = FRAME_SECS,
                      done: Callable[[], None] | None = None) -> None:
        """set_frame from a Mac-style rect or frame (as shared/pill.py returns them)."""
        self.set_frame(coords.qt_rect_from_mac(mac_rect), animate, duration, done)

    def _apply_frame(self, rect: coords.Rect) -> None:
        x, y, w, h = rect
        m = self.margin
        self._size = (max(0.0, w), max(0.0, h))
        self.panel.setGeometry(round(x - m), round(y - m), max(1, round(w + 2 * m)), max(1, round(h + 2 * m)))
        self._place_content()
        self.panel.update()

    def _place_content(self) -> None:
        w, h = self._size
        self.content.setGeometry(round(self.margin), round(self.margin), max(0, round(w)), max(0, round(h)))

    def show_at(self, x: float, y: float, screen=None) -> None:
        """Place the surface's top-left corner at (x, y), Qt logical, clamped to the visible frame
        of the screen that point is on (or `screen`), and bring it to the front."""
        w, h = self._size
        vf = coords.visible_frame(screen or coords.screen_at(x, y))
        if vf[2] > 0 and vf[3] > 0:
            x, y, _, _ = coords.clamp_to((x, y, w, h), vf)
        self._apply_frame((x, y, w, h))
        self.order_front()

    def show_at_mac(self, x: float, y: float, screen=None) -> None:
        """show_at for a Mac-style bottom-left corner (as shared/button.place returns it)."""
        w, h = self._size
        qx, qy, _, _ = coords.qt_rect_from_mac((x, y, w, h))
        self.show_at(qx, qy, screen)

    def contains_screen_point(self, x: float, y: float, physical: bool = True) -> bool:
        """Is the screen point inside the surface? By default the point is in physical pixels, as
        the Windows mouse hook reports it (the twin of the Mac taking the tap's Quartz point);
        pass physical=False for a Qt logical point."""
        if not self.visible():
            return False
        if physical:
            x, y = coords.physical_to_logical(x, y)
        fx, fy, fw, fh = self.frame()
        return fx <= x <= fx + fw and fy <= y <= fy + fh

    # -- showing --------------------------------------------------------------------------------

    def order_front(self) -> None:
        """Show above everything without taking focus."""
        if not self.panel.isVisible():
            self.panel.show()
        if sys.platform == "win32":
            _bring_to_top(self.panel.winId())
        else:
            self.panel.raise_()

    def hide(self) -> None:
        if self._frame_anim is not None:
            self._frame_anim.stop()
            self._frame_anim = None
        self.panel.hide()

    def visible(self) -> bool:
        return bool(self.panel.isVisible())

    def set_content(self, widget: QWidget, surface: bool = True) -> None:
        """Swap the content widget (the Mac setContentView_). The old one is hidden, not
        destroyed, so a form can come back. With surface=False the panel paints no surface and
        the widget draws its own (a sliver, a stack of capsules); its optional shadow_path()
        then says what casts the shadow."""
        if widget is not self.content:
            self.content.hide()
            widget.setParent(self.panel)
            self.content = widget
        self._paint_surface = surface
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._place_content()
        widget.show()
        self.panel.update()

    def set_shadow(self, on: bool) -> None:
        """Turn the painted shadow on or off, keeping the surface where it is."""
        frame = self.frame()
        self.margin = MARGIN if on else 0.0
        self._apply_frame(frame)

    def set_click_through(self, on: bool) -> None:
        """Let every click fall through to the window below (the tip, the drop-zone hint)."""
        self._click_through = bool(on)
        self.panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self._click_through)
        handle = self.panel.windowHandle()
        if handle is not None:
            handle.setFlag(Qt.WindowType.WindowTransparentForInput, self._click_through)
        self._apply_native_styles()

    def click_through(self) -> bool:
        return self._click_through

    def set_alpha(self, alpha: float) -> None:
        if self._alpha_anim is not None:
            self._alpha_anim.stop()
            self._alpha_anim = None
        self._set_alpha(alpha)

    def alpha(self) -> float:
        return self._alpha

    def _set_alpha(self, alpha: float) -> None:
        self._alpha = max(0.0, min(1.0, float(alpha)))
        self.panel.setWindowOpacity(self._alpha)

    def fade_to(self, alpha: float, seconds: float, done: Callable[[], None] | None = None) -> None:
        """Animate the window's opacity to `alpha` over `seconds` (the Mac animator fade)."""
        if self._alpha_anim is not None:
            self._alpha_anim.stop()
            self._alpha_anim = None
        if seconds <= 0 or theme.reduce_motion():
            self._set_alpha(alpha)
            if done is not None:
                done()
            return
        anim = QVariantAnimation()
        anim.setStartValue(float(self._alpha))
        anim.setEndValue(float(alpha))
        anim.setDuration(round(seconds * 1000))
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.valueChanged.connect(lambda v: self._set_alpha(v))

        def finished() -> None:
            self._set_alpha(alpha)
            if self._alpha_anim is anim:
                self._alpha_anim = None
            if done is not None:
                done()
        anim.finished.connect(finished)
        self._alpha_anim = anim
        anim.start()

    # -- native styles --------------------------------------------------------------------------

    def _apply_native_styles(self) -> None:
        """Windows: no activation, no taskbar button, and click-through when asked. Qt may
        rebuild the native window or its styles, so this runs again on show and on a new winId."""
        if sys.platform != "win32":
            return
        add = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        remove = 0
        if self._click_through:
            add |= WS_EX_TRANSPARENT
        else:
            remove = WS_EX_TRANSPARENT
        set_ex_style(self.panel.winId(), add, remove)

    # -- dragging -------------------------------------------------------------------------------

    def _consumes(self, widget: QWidget) -> bool:
        w = widget
        while w is not None and w is not self.panel:
            if getattr(w, "consumes_mouse", False):
                return True
            w = w.parentWidget()
        return False

    def _control(self, widget: QWidget):
        """The button the press landed on: a QAbstractButton or one of ours (perform_click)."""
        w = widget
        while w is not None and w is not self.panel:
            if isinstance(w, QAbstractButton) or callable(getattr(w, "perform_click", None)):
                return w
            w = w.parentWidget()
        return None

    def _set_pressed(self, control, on: bool) -> None:
        fn = getattr(control, "set_pressed", None) if control is not None else None
        if callable(fn):
            fn(on)

    def _mouse(self, widget: QWidget, ev) -> bool:
        """Called by the drag filter for every mouse event inside the window. True eats it."""
        t = ev.type()
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if ev.button() != Qt.MouseButton.LeftButton or not self.draggable or self._consumes(widget):
                return False
            local = self.panel.mapFromGlobal(ev.globalPosition())
            if not self.surface_rect().contains(local):
                return True  # a press in the shadow: nothing there
            g = ev.globalPosition()
            x, y, _, _ = self.frame()
            control = self._control(widget)
            if control is not None and not control.isEnabled():
                control = None
            self._press = ((g.x(), g.y()), (x, y), control)
            self._dragging = False
            self._set_pressed(control, True)
            return True
        if self._press is None:
            return False
        if t == QEvent.Type.MouseMove:
            (mx, my), (ox, oy), control = self._press
            g = ev.globalPosition()
            dx, dy = g.x() - mx, g.y() - my
            if not self._dragging and abs(dx) < DRAG_SLOP and abs(dy) < DRAG_SLOP:
                return True
            if not self._dragging:
                self._dragging = True
                self._set_pressed(control, False)
                self.drag_began()
            w, h = self._size
            self._apply_frame((ox + dx, oy + dy, w, h))
            self.drag_moved(g.x(), g.y())
            return True
        if t == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
            _, _, control = self._press
            self._press = None
            if self._dragging:
                self._dragging = False
                g = ev.globalPosition()
                self.drag_ended(g.x(), g.y())
            else:
                self._set_pressed(control, False)
                if control is not None and control.isEnabled():
                    if isinstance(control, QAbstractButton):
                        control.click()
                    else:
                        control.perform_click()
                else:
                    self.clicked()
            return True
        return False

    def dragging(self) -> bool:
        return self._dragging


# -- previews (python -m lyssna.ui.qt.preview) -------------------------------------------------


def _preview_panel(adaptive: bool = True, w: float = 300.0, h: float = 96.0, radius: float = 16.0,
                   text: str = "Reading from Safari", shadow: bool = True, fill=None, border=None) -> QWidget:
    from PySide6.QtWidgets import QLabel

    class Preview(FloatingPanel):
        pass

    Preview.width, Preview.height, Preview.corner_radius = w, h, radius
    Preview.adaptive, Preview.shadow = adaptive, shadow
    fp = Preview()
    if fill is not None:
        fp.fill_color = fill
    if border is not None:
        fp.border_color = border
    if text:
        label = QLabel(text, fp.content)
        label.setFont(theme.ui_font(13, "medium"))
        color = theme.content() if adaptive else QColor(255, 255, 255)
        label.setStyleSheet(f"color: {color.name(QColor.NameFormat.HexArgb)}; background: transparent;")
        label.setGeometry(18, 0, round(w) - 36, round(h))
    return fp.panel


PREVIEWS = {
    "panel": lambda: _preview_panel(),
    "panel.toast": lambda: _preview_panel(w=260.0, h=44.0, radius=22.0, text="Copied. Press Ctrl+V to paste"),
    "panel.fixed-dark": lambda: _preview_panel(adaptive=False, w=220.0, h=60.0, radius=12.0, text="Fixed dark surface"),
    "panel.drop-zone": lambda: _preview_panel(w=16.0, h=64.0, radius=8.0, text="", shadow=False,
                                              fill=lambda: theme.content(0.28), border=theme.content_dim),
}
