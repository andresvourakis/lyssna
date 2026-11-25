"""Reusable pieces of the floating panels: the hover-glow view, round icon buttons, text pill
buttons, and the progress and scrub bars. The twin of legacy/ui/widgets.py (HoverView, ProgressBar,
ScrubBar) and of the PillButton in legacy/ui/setup_window.py; the docked sliver is built with the
player in mini_player.py.

Colours may be given as a QColor or as a function returning one (theme.content, theme.fill_soft,
...). Functions are read at paint time, so everything follows light and dark mode after an
update(), as the Mac's dynamic colours do. Sizes come from layout/wave.py and layout/setup.py.

Buttons never take focus (Qt.NoFocus): they live in panels that must not activate. Inside a
draggable FloatingPanel the panel itself decides click versus drag and calls perform_click()
(or QAbstractButton.click()); elsewhere each button handles its own press and release.
Animations jump to their end when theme.reduce_motion() is on."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QPushButton, QWidget

from .. import host
from . import icons, theme
from .layout.setup import PILL_H, PILL_PAD
from .layout.wave import BAR_H, KNOB
from .motion import PacedAnimation
from .sliver import EASE_DEFAULT


def button_focus() -> Qt.FocusPolicy:
    """The focus a button or radio in a normal window takes: on the Mac only from Tab, as an
    NSButton does, so a click leaves no focus ring behind it (host.CLICK_TAKES_FOCUS); on
    Windows from a click too."""
    return Qt.FocusPolicy.StrongFocus if host.CLICK_TAKES_FOCUS else Qt.FocusPolicy.TabFocus


Color = QColor | Callable[[], QColor] | None

GLOW_IN, GLOW_OUT = 0.14, 0.22  # seconds the hover glow takes to fade in and out
DISABLED_ALPHA = 0.35           # a disabled button's icon or label
# The Mac's progress fill and scrub knob are bare Core Animation layers whose frame changes are
# not wrapped in disabled actions, so each change glides over the implicit 0.25 s, default curve.
GLIDE_SECS = 0.25


def resolve(color: Color) -> QColor | None:
    """A QColor from a colour or a colour function (None stays None)."""
    return color() if callable(color) else color


def _with_alpha(color: QColor, factor: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * factor)))
    return c


def pressed_tint(color: QColor) -> QColor:
    """`color` as AppKit draws a pressed borderless button's symbol: NSColor's pressed system
    effect, measured on the Mac per channel. In light mode it darkens (white to 215, mid grey
    128 to 98, black stays black); in dark mode it lightens by 46 of 255 (black to 46, mid grey
    to 174, anything from 209 up to white)."""
    c = QColor(color)
    if theme.is_dark():
        r, g, b = (min(1.0, v + 46 / 255) for v in (c.redF(), c.greenF(), c.blueF()))
    else:
        r, g, b = (0.843 * v ** 1.14 for v in (c.redF(), c.greenF(), c.blueF()))
    return QColor.fromRgbF(r, g, b, c.alphaF())


def rounded(rect: QRectF, radius: float) -> QPainterPath:
    r = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path


class HoverView(QWidget):
    """A rounded shape that lifts a translucent glow over itself while the cursor is inside, so
    it brightens softly on hover (in over 0.14 s, out over 0.22 s, on Core Animation's default
    curve: the Mac sets only the duration on that implicit animation). With no fill it works as
    a transparent wrapper that lights up behind a bare button.

    fill, border, glow: colours or colour functions; radius: the corner radius (None for a
    capsule, half the shorter side). on_hover(bool), when set, hears every hover change."""

    def __init__(self, parent: QWidget | None = None, fill: Color = None, radius: float | None = 0.0,
                 border: Color = None, glow: Color = theme.clear_glow):
        super().__init__(parent)
        self.fill = fill
        self.border = border
        self.radius = radius
        self.glow = glow
        self.on_hover: Callable[[bool], None] | None = None
        self._on = False
        self._level = 0.0
        self._anim: QVariantAnimation | None = None
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    # -- shape --------------------------------------------------------------------------------

    def shape_rect(self) -> QRectF:
        """The rect the fill and glow are painted in (the whole view; IconButton centres a disc)."""
        return QRectF(self.rect())

    def shape_radius(self) -> float:
        r = self.shape_rect()
        return min(r.width(), r.height()) / 2 if self.radius is None else float(self.radius)

    def shape_path(self) -> QPainterPath:
        return rounded(self.shape_rect(), self.shape_radius())

    def glow_level(self) -> float:
        """The glow's opacity now, 0..1."""
        return self._level

    def hovering(self) -> bool:
        return self._on

    # -- painting -----------------------------------------------------------------------------

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.paint_shape(p)
        self.paint_content(p)
        p.end()

    def paint_shape(self, p: QPainter) -> None:
        path = self.shape_path()
        fill = resolve(self.fill)
        if fill is not None and fill.alpha() > 0:
            p.fillPath(path, fill)
        border = resolve(self.border)
        if border is not None and border.alpha() > 0:
            p.setPen(QPen(border, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(rounded(self.shape_rect().adjusted(0.5, 0.5, -0.5, -0.5), max(0.0, self.shape_radius() - 0.5)))
        glow = resolve(self.glow)
        if glow is not None and self._level > 0.0:
            p.fillPath(path, _with_alpha(glow, self._level))

    def paint_content(self, p: QPainter) -> None:  # override: drawn above the glow
        pass

    # -- hover --------------------------------------------------------------------------------

    def enterEvent(self, ev) -> None:
        self.set_hover(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self.set_hover(False)
        super().leaveEvent(ev)

    def set_hover(self, on: bool, animate: bool = True) -> None:
        on = bool(on) and self.isEnabled()
        if on == self._on and (animate or self._level == (1.0 if on else 0.0)):
            return
        self._on = on
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        target = 1.0 if on else 0.0
        if not animate or theme.reduce_motion() or not self.isVisible():
            self._set_level(target)
        else:
            anim = PacedAnimation(self)
            anim.setStartValue(float(self._level))
            anim.setEndValue(target)
            anim.setDuration(round((GLOW_IN if on else GLOW_OUT) * 1000))
            anim.setEasingCurve(EASE_DEFAULT)
            anim.valueChanged.connect(self._set_level)
            self._anim = anim
            anim.start()
        if self.on_hover is not None:
            self.on_hover(on)

    def _set_level(self, v) -> None:
        self._level = float(v)
        self.update()

    def sync(self) -> None:
        """Set the glow from where the cursor really is (enter and leave can be lost while a
        window moves or changes form under a still mouse)."""
        if not self.isVisible():
            self.set_hover(False)
            return
        local = self.mapFromGlobal(QCursor.pos())
        self.set_hover(self.rect().contains(local))


class IconButton(HoverView):
    """A round button holding one icon (the twin of a symbol_button in a HoverView disc).

    icon: an icons.py name; diameter: the disc; hit: a larger square click area around it (the
    view's size, defaults to the diameter); fill: the disc colour or None for a bare icon that
    only glows on hover; icon_size and weight as for the SF Symbol; clicked: called on a click.
    Pressed, only the icon changes, as on a borderless NSButton: it takes the pressed tint (the
    disc stays as it is). Disabled, the icon dims and nothing glows."""

    def __init__(self, icon: str, parent: QWidget | None = None, *, diameter: float = 26.0, hit: float | None = None,
                 fill: Color = None, glow: Color = theme.clear_glow, icon_size: float = 13.0,
                 icon_color: Color = theme.content, weight: str | None = None, tooltip: str = "",
                 clicked: Callable[[], None] | None = None, border: Color = None):
        super().__init__(parent, fill=fill, radius=None, border=border, glow=glow)
        self.icon = icon
        self.diameter = float(diameter)
        self.icon_size = float(icon_size)
        self.icon_color = icon_color
        self.weight = weight
        self.clicked = clicked
        self.pressed = False
        side = max(self.diameter, float(hit or 0.0))
        self.resize(round(side), round(side))
        if tooltip:
            self.setToolTip(tooltip)

    def sizeHint(self) -> QSize:
        return self.size()

    def shape_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        d = min(self.diameter, w, h)
        return QRectF((w - d) / 2, (h - d) / 2, d, d)

    def set_icon(self, name: str, size: float | None = None, weight: str | None = None) -> None:
        """Change the icon (the Mac set_symbol)."""
        self.icon = name
        if size is not None:
            self.icon_size = float(size)
        if weight is not None:
            self.weight = weight
        self.update()

    def set_pressed(self, on: bool) -> None:
        self.pressed = bool(on)
        self.update()

    def changeEvent(self, ev) -> None:
        if ev.type() == ev.Type.EnabledChange:
            if not self.isEnabled():
                self.set_hover(False, animate=False)
            self.update()
        super().changeEvent(ev)

    def paint_content(self, p: QPainter) -> None:
        r = self.shape_rect()
        color = resolve(self.icon_color) or theme.content()
        if not self.isEnabled():
            color = _with_alpha(color, DISABLED_ALPHA)
        elif self.pressed:
            color = pressed_tint(color)
        icons.draw_icon(p, self.icon, icons.icon_rect(r.center().x(), r.center().y(), self.icon_size), color, self.weight)

    def perform_click(self) -> None:
        if self.isEnabled() and self.clicked is not None:
            self.clicked()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.isEnabled():
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


# A disabled Mac pill keeps its fill and outline; only the button cell's title (and image) dims,
# to half (measured on the legacy setup window's disabled Save, in both schemes).
DISABLED_TITLE_ALPHA = 0.5


class PillButton(QPushButton):
    """A pill-shaped push button in the brand colours, the twin of PillButton in
    legacy/ui/setup_window.py: primary is a content pill with surface text (13 medium), secondary a
    fill_soft pill with a hairline border and content text (13 regular). Pressed, a light veil
    lies over it. A real QPushButton underneath for the keyboard and accessibility; it takes no
    focus unless focusable=True (in a normal window, never in a floating panel)."""

    def __init__(self, title: str, parent: QWidget | None = None, *, primary: bool = False,
                 clicked: Callable[[], None] | None = None, focusable: bool = False):
        super().__init__(title, parent)
        self.primary = primary
        self.setFont(theme.ui_font(13, "medium" if primary else "regular"))
        self.setFocusPolicy(button_focus() if focusable else Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if clicked is not None:
            self.clicked.connect(lambda _checked=False: clicked())
        self.size_to_fit()

    def size_to_fit(self) -> None:
        """The Mac's width for the title, PILL_H tall (PillButton.sizeToFit)."""
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        # NSButton.sizeToFit makes a borderless button's frame the title's advance plus its
        # cell's 2 pt inset each side, rounded up to whole points; the Mac's PillButton then
        # adds PILL_PAD each side ("Download" in 13 pt: 60.04 advance, 65, 93 wide).
        w = theme.text_width(self.text(), self.font())
        return QSize(math.ceil(w + 4.0 - 1e-6) + 2 * round(PILL_PAD), round(PILL_H))

    def setText(self, text: str) -> None:
        super().setText(text)
        self.size_to_fit()

    def retitle(self, text: str) -> None:
        """Change the title and keep the frame, as NSButton.setTitle_ does: the Mac's Download
        button stays Download's width when it says Try Again."""
        super().setText(text)
        self.update()

    def set_pressed(self, on: bool) -> None:
        self.setDown(bool(on))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded(r, r.height() / 2)
        p.fillPath(path, theme.content() if self.primary else theme.fill_soft())
        if not self.primary:
            p.setPen(QPen(theme.hairline(), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        if self.isDown():
            p.fillPath(path, theme.surface(0.18) if self.primary else theme.content(0.08))
        if self.hasFocus():
            ring = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            p.setPen(QPen(theme.content(0.45), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(rounded(ring, ring.height() / 2))
        title = theme.surface() if self.primary else theme.content()
        if not self.isEnabled():
            title.setAlphaF(title.alphaF() * DISABLED_TITLE_ALPHA)
        p.setPen(title)
        theme.draw_text(p, QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.text(), self.font())
        p.end()


class _Glide:
    """Values shown on their way to a live target: either at the target, or easing there over
    GLIDE_SECS on Core Animation's default curve from where they were shown when it changed (an
    implicit layer animation, which also starts from the value on screen when it interrupts
    another). `target` returns the values now, `changed` repaints."""

    def __init__(self, owner: QWidget, target: Callable[[], tuple[float, ...]], changed: Callable[[], None]):
        self.owner = owner
        self.target = target
        self.changed = changed
        self._from: tuple[float, ...] | None = None
        self._to: tuple[float, ...] | None = None
        self._e = 1.0
        self._anim: QVariantAnimation | None = None

    def value(self) -> tuple[float, ...]:
        to = self.target()
        if self._from is None:
            return to
        return tuple(a + (b - a) * self._e for a, b in zip(self._from, to, strict=True))

    def gliding(self) -> bool:
        return self._from is not None

    def start(self, shown: tuple[float, ...], animate: bool) -> None:
        """The target may have changed; `shown` is what was on screen just before. Glide from
        it, or jump. A glide already heading to the same target carries on undisturbed."""
        to = self.target()
        if animate and self._anim is not None and to == self._to:
            return
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        self._from = None
        if animate and not theme.reduce_motion() and tuple(shown) != to:
            self._from, self._to, self._e = tuple(shown), to, 0.0
            anim = PacedAnimation(self.owner)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setDuration(round(GLIDE_SECS * 1000))
            anim.setEasingCurve(EASE_DEFAULT)
            anim.valueChanged.connect(self._step)
            anim.finished.connect(lambda: self._done(anim))
            self._anim = anim
            anim.start()
        self.changed()

    def _step(self, e) -> None:
        self._e = float(e)
        self.changed()

    def _done(self, anim: QVariantAnimation) -> None:
        if self._anim is anim:
            self._anim = None
            self._from = None
            self.changed()


class ProgressBar(QWidget):
    """A rounded track (content at 0.15) with a content fill from the left. The fill is never
    narrower than the bar is tall, so a just-started reading shows a round dot.

    As on the Mac, a new fraction (and a new bar height, when the scrub bar thickens) glides the
    fill there over 0.25 s from where it is shown, once the bar is on screen (a drag too, as on
    the Mac); set_fraction(f, animate=False) jumps. The fill sits on the track's bottom edge and
    is clipped to the track, like the Mac's fill layer inside the bar's rounded, clipping layer."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.fraction = 0.0
        self._painted = False
        self.paint_dy = 0.0  # set_paint_offset: the bar drawn this far below the widget's top
        self._fill = _Glide(self, self.target_fill, self.update)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.resize(100, round(BAR_H))

    def thickness(self) -> float:
        """The bar's height: the widget's, less the pixel added for a paint offset."""
        return float(self.height()) - (1.0 if self.paint_dy else 0.0)

    def set_paint_offset(self, dx: float, dy: float = 0.0) -> None:
        """Draw the bar `dy` (0 to 1) below the widget's top, where the Mac's bar sits on a half
        point: the widget grows a pixel to hold it. `dx` is not used (the bar's x is whole)."""
        bar = self.thickness()
        self.paint_dy = max(0.0, min(1.0, dy))
        self.resize(self.width(), round(bar + (1.0 if self.paint_dy else 0.0)))
        self.update()

    def may_glide(self) -> bool:
        return self._painted and self.isVisible()

    def set_fraction(self, f: float, animate: bool = True) -> None:
        shown = self._fill.value()
        self.fraction = max(0.0, min(1.0, float(f)))
        self._fill.start(shown, animate and self.may_glide())

    def target_fill(self) -> tuple[float, float]:
        """The fill's (width, height) for the fraction and the bar's size now."""
        w, h = float(self.width()), self.thickness()
        return min(w, max(h, w * self.fraction)), h

    def fill_size(self) -> tuple[float, float]:
        """The fill's (width, height) as shown now (mid-glide or settled)."""
        return self._fill.value()

    def fill_width(self) -> float:
        return self.fill_size()[0]

    def paintEvent(self, ev) -> None:
        self._painted = True
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(0.0, self.paint_dy)
        h = self.thickness()
        track = rounded(QRectF(0, 0, self.width(), h), h / 2)
        p.fillPath(track, theme.content(0.15))
        fw, fh = self.fill_size()
        p.setClipPath(track)
        p.fillPath(rounded(QRectF(0, h - fh, fw, fh), fh / 2), theme.content())
        p.end()


class _Knob(QWidget):
    """The playhead knob: a content disc with a 2 px surface ring and a small soft shadow. It
    fades in and out (alpha) as the Mac's knob layer does when it is shown or hidden."""

    PAD = 3.0  # room for the shadow

    def __init__(self, parent: QWidget, size: float):
        super().__init__(parent)
        self.k = float(size)
        self.alpha = 0.0
        side = round(self.k + 2 * self.PAD)
        self.resize(side, side)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(self.alpha)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = self.k / 2
        shadow = QRadialGradient(QPointF(c.x(), c.y() + 1.0), r + 2.0)
        shadow.setColorAt(0.0, QColor.fromRgbF(0, 0, 0, 0.25))
        shadow.setColorAt(r / (r + 2.0), QColor.fromRgbF(0, 0, 0, 0.18))
        shadow.setColorAt(1.0, QColor.fromRgbF(0, 0, 0, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shadow)
        p.drawEllipse(QPointF(c.x(), c.y() + 1.0), r + 2.0, r + 2.0)
        p.setBrush(theme.surface())
        p.drawEllipse(c, r, r)
        p.setBrush(theme.content())
        p.drawEllipse(c, r - 2.0, r - 2.0)
        p.end()


class ScrubBar(QWidget):
    """A transparent strip holding a ProgressBar; click or drag anywhere on it to seek. Hovered
    or scrubbing, the bar thickens to 1.5 times and a round knob shows at the playhead.
    on_scrub(fraction) fires while dragging (for the time preview), on_seek(fraction) on release.

    The motion is the Mac's, whose fill and knob are layers with implicit animations: the bar's
    track thickens at once while the fill's height glides after it; the knob fades in or out
    over 0.25 s; and playback glides the fill, and the knob while hovered, to the new playhead.
    The knob only follows the playhead while it shows, so on the next hover it slides from where
    it was last shown while it fades in. A drag glides them too, 0.25 s behind the cursor (the
    Mac's drag sets the same layer frames, read back as 0.25 s position and bounds animations),
    while the player's preview times follow the cursor at once. The knob's fade is a Core
    Animation transition, so one that interrupts another starts afresh from fully hidden or
    fully shown, not from the opacity on screen."""

    consumes_mouse = True  # tells FloatingPanel this is a control, not a drag handle

    def __init__(self, parent: QWidget | None = None, knob: float = KNOB):
        super().__init__(parent)
        self.knob_size = float(knob)
        self.bar = ProgressBar(self)
        self.knob = _Knob(self, self.knob_size)
        self.knob.hide()
        self.on_scrub: Callable[[float], None] | None = None
        self.on_seek: Callable[[float], None] | None = None
        self.scrubbing = False
        self.hovering = False
        self._knob_x = 0.0      # the knob's centre as last laid out (the Mac's model position)
        self._knob_on = False
        self._knob_pos = _Glide(self, lambda: (self._knob_x,), self._place_knob)
        self._knob_alpha = _Glide(self, lambda: (1.0 if self._knob_on else 0.0,), self._place_knob)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._layout()

    @property
    def fraction(self) -> float:
        return self.bar.fraction

    def active(self) -> bool:
        return self.hovering or self.scrubbing

    def bar_height(self) -> float:
        return BAR_H * 1.5 if self.active() else BAR_H

    def _layout(self, animate: bool = False) -> None:
        """Lay the bar and the knob out for the state now; `animate` glides what the Mac's
        implicit animations glide (the fill's height, the knob's place and its fade)."""
        animate = animate and self.bar.may_glide()
        w, h = float(self.width()), float(self.height())
        bh = self.bar_height()
        fill_shown = self.bar.fill_size()
        self.bar.setGeometry(0, round((h - bh) / 2), round(w), round(bh))
        self.bar._fill.start(fill_shown, animate)
        k = self.knob_size
        x_shown = self._knob_pos.value()
        alpha_before = (1.0 if self._knob_on else 0.0,)  # a transition starts from the old state, not the shown one
        self._knob_x = min(max(w * self.bar.fraction, k / 2), w - k / 2) if w > 0 else 0.0
        self._knob_on = self.active()
        self._knob_pos.start(x_shown, animate)
        self._knob_alpha.start(alpha_before, animate)
        self.knob.raise_()

    def _place_knob(self) -> None:
        alpha = self._knob_alpha.value()[0]
        self.knob.alpha = alpha
        self.knob.setVisible(alpha > 0.0 or self._knob_on)
        x = self._knob_pos.value()[0]
        self.knob.move(round(x - self.knob.width() / 2), round((self.height() - self.knob.height()) / 2))
        self.knob.update()

    def resizeEvent(self, ev) -> None:
        self._layout()
        super().resizeEvent(ev)

    def enterEvent(self, ev) -> None:
        self.hovering = True
        self._layout(animate=True)
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self.hovering = False
        self._layout(animate=True)
        super().leaveEvent(ev)

    def set_hover(self, on: bool) -> None:
        self.hovering = bool(on)
        self._layout(animate=True)

    def sync(self) -> None:
        """Set the hover from where the cursor really is (the panel polls this where Qt sends no
        enter and leave events); nothing moves unless it changed."""
        on = self.isVisible() and self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if on != self.hovering:
            self.set_hover(on)

    def _fraction_at(self, x: float) -> float:
        w = float(self.width())
        return max(0.0, min(1.0, x / w)) if w > 0 else 0.0

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(ev)
        self.scrubbing = True
        self._move(ev.position().x())
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if self.scrubbing:
            self._move(ev.position().x())
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton or not self.scrubbing:
            return super().mouseReleaseEvent(ev)
        f = self._fraction_at(ev.position().x())
        self.scrubbing = False
        self.bar.set_fraction(f)
        self._layout(animate=True)  # released off the bar, it thins and the knob fades out
        if self.on_seek:
            self.on_seek(f)
        ev.accept()

    def _move(self, x: float) -> None:
        f = self._fraction_at(x)
        self.bar.set_fraction(f)
        self._layout(animate=True)
        if self.on_scrub:
            self.on_scrub(f)

    def set_fraction(self, f: float) -> None:
        """The playhead from playback; ignored while the user scrubs. The fill glides there; the
        knob follows only while the bar is hovered (the Mac redraws the strip only then)."""
        if not self.scrubbing:
            self.bar.set_fraction(f)
            if self.hovering:
                self._layout(animate=True)


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------

STATES = ("normal", "hover", "pressed", "disabled")


def _set_state(widget: QWidget, state: str) -> None:
    if state == "hover" and isinstance(widget, HoverView):
        widget.set_hover(True, animate=False)
    elif state == "pressed":
        widget.set_pressed(True)
    elif state == "disabled":
        widget.setEnabled(False)


def _preview_buttons() -> QWidget:
    """Each button kind of the player and the windows, in each state, as they sit on a card."""
    from .preview import card

    col_w, row_h, left, top = 92.0, 64.0, 150.0, 34.0
    kinds = [
        ("play  50", lambda parent: IconButton("pause.fill", parent, diameter=50, fill=theme.content, glow=theme.white_glow,
                                               icon_size=20, icon_color=theme.surface)),
        ("skip  34", lambda parent: IconButton("backward.end.fill", parent, diameter=34, icon_size=17)),
        ("close  26", lambda parent: IconButton("xmark", parent, diameter=26, fill=theme.fill_soft, icon_size=11,
                                                icon_color=theme.content_dim, weight="bold")),
        ("finish  24", lambda parent: IconButton("checkmark", parent, diameter=24, hit=28, fill=theme.content,
                                                 glow=theme.white_glow, icon_size=11, icon_color=theme.surface, weight="bold")),
        ("primary", lambda parent: PillButton("Save", parent, primary=True)),
        ("secondary", lambda parent: PillButton("Cancel", parent)),
    ]
    c = card(left + col_w * len(STATES) + 10, top + row_h * len(kinds) + 10)
    for j, state in enumerate(STATES):
        c.caption(state, left + j * col_w, 12, col_w, align=Qt.AlignmentFlag.AlignHCenter)
    for i, (title, make) in enumerate(kinds):
        y = top + i * row_h
        c.caption(title, 18, y, 120, row_h)
        for j, state in enumerate(STATES):
            b = make(c)
            b.move(round(left + j * col_w + (col_w - b.width()) / 2), round(y + (row_h - b.height()) / 2))
            _set_state(b, state)
    return c


def _preview_bars() -> QWidget:
    """Progress bars at several fractions, and scrub bars at rest and hovered."""
    from .layout.pill import SCRUB_H
    from .preview import card

    fractions = (0.0, 0.02, 0.35, 0.75, 1.0)
    w, left = 280.0, 70.0
    c = card(left + w + 24, 22 + 26 * len(fractions) + 40 + SCRUB_H + 20)
    y = 22.0
    for f in fractions:
        c.caption(f"{f:g}", 18, y - 6, 50, 14)
        bar = ProgressBar(c)
        bar.setGeometry(round(left), round(y), round(w), round(BAR_H))
        bar.set_fraction(f)
        y += 26
    for label, hover in (("scrub", False), ("hover", True)):
        c.caption(label, 18, y + (SCRUB_H - 14) / 2, 50, 14)
        s = ScrubBar(c)
        s.setGeometry(round(left), round(y), round(w), round(SCRUB_H))
        s.set_fraction(0.42)
        if hover:
            s.set_hover(True)
        y += 40
    return c


def _preview_hover_surface() -> QWidget:
    """A hover surface (the pill and the menu capsules), at rest and glowing."""
    from .preview import card

    c = card(240, 110)
    for i, on in enumerate((False, True)):
        v = HoverView(c, fill=lambda: theme.surface(0.97), radius=None, border=theme.hairline)
        v.setGeometry(40 + i * 100, 18, 46, 74)
        v.set_hover(on, animate=False)
    return c


PREVIEWS = {
    "buttons": _preview_buttons,
    "bars": _preview_bars,
    "hover-surface": _preview_hover_surface,
}
