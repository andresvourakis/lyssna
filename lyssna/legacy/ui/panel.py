"""A borderless, always-on-top, non-activating panel with a dark rounded background.
Base for the selection button and the mini player."""

from __future__ import annotations

import logging
import time

import AppKit
import objc
import Quartz
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSControl,
    NSEvent,
    NSFont,
    NSImage,
    NSImageOnly,
    NSImageSymbolConfiguration,
    NSPanel,
    NSScreen,
    NSTextField,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSMakeRect, NSObject

from ...ui.layout.panel import DRAG_SLOP
from ...ui.layout.tokens import PANEL_BG as BG
from . import theme
from .mainthread import later

log = logging.getLogger("lyssna.ui.panel")

FG = NSColor.whiteColor()
DIM = NSColor.colorWithWhite_alpha_(1.0, 0.6)
VERIFY_DELAY = 0.25  # seconds after an order-front before we ask the window server what it did
REBUILD_COOLDOWN = 2.0  # seconds; a genuine failure must not turn into a rebuild loop


class TintedView(NSView):
    """Layer-backed view whose background/border colours are resolved in updateLayer, so
    dynamic system colours re-resolve when the appearance flips between light and dark."""

    @classmethod
    @objc.python_method
    def make(cls, frame, color, radius: float = 0.0, border=None):
        v = cls.alloc().initWithFrame_(frame)
        v.color_fn = color if callable(color) else (lambda: color)
        v.border_fn = border if (border is None or callable(border)) else (lambda: border)
        v.radius = radius
        v.setWantsLayer_(True)
        v.layer().setMasksToBounds_(True)
        return v

    def wantsUpdateLayer(self):
        return True

    def updateLayer(self):
        layer = self.layer()
        layer.setBackgroundColor_(self.color_fn().CGColor())
        layer.setCornerRadius_(self.radius)
        if self.border_fn is not None:
            layer.setBorderWidth_(1.0)
            layer.setBorderColor_(self.border_fn().CGColor())


# -- screens -----------------------------------------------------------------------
#
# Quartz (event taps, AX) uses a top-left origin on the *primary* display; Cocoa uses a
# bottom-left origin on the same display. Converting between them only ever needs the
# primary display's height, whichever screen the point is on.


def screen_height() -> float:
    return NSScreen.screens()[0].frame().size.height


def _contains(frame, x: float, y: float) -> bool:
    return (frame.origin.x <= x < frame.origin.x + frame.size.width
            and frame.origin.y <= y < frame.origin.y + frame.size.height)


def screen_at(x: float, y: float):
    """The NSScreen containing the Cocoa point (x, y); nearest screen if none contains it."""
    screens = NSScreen.screens()
    for s in screens:
        if _contains(s.frame(), x, y):
            return s
    def dist(s):
        f = s.frame()
        dx = max(f.origin.x - x, 0, x - (f.origin.x + f.size.width))
        dy = max(f.origin.y - y, 0, y - (f.origin.y + f.size.height))
        return dx * dx + dy * dy
    return min(screens, key=dist) if screens else NSScreen.mainScreen()


def screen_at_quartz(qx: float, qy: float):
    return screen_at(qx, screen_height() - qy)


def screen_under_mouse():
    p = NSEvent.mouseLocation()
    return screen_at(p.x, p.y)


def visible_frame(screen=None):
    """Usable area of `screen` (below the menu bar, above the Dock), Cocoa coordinates.
    Defaults to the screen the mouse is on, which is where the user is working."""
    return (screen or screen_under_mouse()).visibleFrame()


def clamp_to(vf, x: float, y: float, w: float, h: float) -> tuple[float, float]:
    x = min(max(x, vf.origin.x), vf.origin.x + vf.size.width - w)
    y = min(max(y, vf.origin.y), vf.origin.y + vf.size.height - h)
    return x, y


# -- widgets -----------------------------------------------------------------------


def symbol_button(name: str, target: NSObject, action: str, size: float = 13.0, weight=AppKit.NSFontWeightSemibold, tint=None) -> NSButton:
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, name)
    cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight)
    img = img.imageWithSymbolConfiguration_(cfg)
    btn = NSButton.buttonWithImage_target_action_(img, target, action)
    btn.setBordered_(False)
    btn.setImagePosition_(NSImageOnly)
    btn.setContentTintColor_(tint or FG)
    btn.setToolTip_(name)
    return btn


def set_symbol(btn: NSButton, name: str, size: float, weight=AppKit.NSFontWeightSemibold) -> None:
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, name)
    cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight)
    btn.setImage_(img.imageWithSymbolConfiguration_(cfg))


def label(text: str, size: float = 12.0, color=FG, weight=AppKit.NSFontWeightMedium, mono: bool = True) -> NSTextField:
    lbl = NSTextField.labelWithString_(text)
    font = NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight) if mono else NSFont.systemFontOfSize_weight_(size, weight)
    lbl.setFont_(font)
    lbl.setTextColor_(color)
    lbl.setAlignment_(AppKit.NSTextAlignmentCenter)
    return lbl


# -- panels ------------------------------------------------------------------------


class DragPanel(NSPanel):
    """NSPanel that lets its owner move it by dragging anywhere, buttons included. We do the
    drag ourselves (rather than movableByWindowBackground) so the owner hears about every
    move and, crucially, about the drop: that's when it decides whether to snap to an edge.

    A press that never travels more than DRAG_SLOP is a click: it is forwarded to the
    control under the mouse (performClick:), or to the owner if there is none. Views that
    set `consumes_mouse` (the scrub bar) get the raw events and are never drag handles."""

    def initWithContentRect_styleMask_backing_defer_(self, rect, style, backing, defer):
        self = objc.super(DragPanel, self).initWithContentRect_styleMask_backing_defer_(rect, style, backing, defer)
        if self is None:
            return None
        self.owner = None
        self._press = None   # (mouse screen point, panel origin, control hit) while a press is live
        self._dragging = False
        return self

    @objc.python_method
    def _consumes(self, view) -> bool:
        while view is not None:
            if getattr(view, "consumes_mouse", False):
                return True
            view = view.superview()
        return False

    def sendEvent_(self, event):
        t = event.type()
        owner = self.owner
        if owner is None or not getattr(owner, "draggable", False):
            return objc.super(DragPanel, self).sendEvent_(event)
        if t == AppKit.NSEventTypeLeftMouseDown:
            hit = self.contentView().hitTest_(event.locationInWindow())
            if not self._consumes(hit):
                m = NSEvent.mouseLocation()
                o = self.frame().origin
                self._press = ((m.x, m.y), (o.x, o.y), hit if isinstance(hit, NSControl) else None)
                self._dragging = False
                return
        elif t == AppKit.NSEventTypeLeftMouseDragged and self._press is not None:
            (mx, my), (ox, oy), _ = self._press
            m = NSEvent.mouseLocation()
            dx, dy = m.x - mx, m.y - my
            if not self._dragging and abs(dx) < DRAG_SLOP and abs(dy) < DRAG_SLOP:
                return
            if not self._dragging:
                self._dragging = True
                owner.drag_began()
            self.setFrameOrigin_((ox + dx, oy + dy))
            owner.drag_moved(m.x, m.y)
            return
        elif t == AppKit.NSEventTypeLeftMouseUp and self._press is not None:
            _, _, control = self._press
            self._press = None
            if self._dragging:
                self._dragging = False
                m = NSEvent.mouseLocation()
                owner.drag_ended(m.x, m.y)
            elif control is not None and control.isEnabled():
                control.performClick_(None)
            else:
                owner.clicked()
            return
        objc.super(DragPanel, self).sendEvent_(event)


class FloatingPanel(NSObject):
    """Owns an NSPanel. Subclasses build content in `build(content_view)` and use `self.panel`."""

    width = 100.0
    height = 40.0
    corner_radius = 12.0
    adaptive = False   # True: the brand surface (cream or ink, follows light/dark mode) instead of the fixed dark BG
    draggable = False  # True: the user can drag the panel around; see DragPanel

    def init(self):
        self = objc.super(FloatingPanel, self).init()
        if self is None:
            return None
        rect = NSMakeRect(0, 0, self.width, self.height)
        self._last_rebuild = 0.0
        self.panel = self._make_window(rect)
        content = self.make_surface(rect, self.corner_radius)
        self.panel.setContentView_(content)
        self.build(content)
        return self

    @objc.python_method
    def _make_window(self, rect) -> DragPanel:
        """A panel in this class's style. Also used to rebuild the window from scratch, so
        every setting that makes the window float above everything lives here."""
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = DragPanel.alloc().initWithContentRect_styleMask_backing_defer_(rect, style, NSBackingStoreBuffered, False)
        panel.owner = self
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setReleasedWhenClosed_(False)  # we keep the reference; closing must not free it under us
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        return panel

    @objc.python_method
    def make_surface(self, rect, radius: float) -> TintedView:
        """A rounded background view in this panel's style."""
        if self.adaptive:
            bg = theme.surface(0.97)
            border = theme.HAIRLINE
        else:
            bg = NSColor.colorWithRed_green_blue_alpha_(*BG)
            border = NSColor.colorWithWhite_alpha_(1.0, 0.10)
        return TintedView.make(rect, bg, radius, border)

    @objc.python_method
    def build(self, content: NSView) -> None:  # override
        pass

    # Drag hooks (only called when `draggable`); override in subclasses.
    @objc.python_method
    def drag_began(self) -> None:
        pass

    @objc.python_method
    def drag_moved(self, mx: float, my: float) -> None:
        pass

    @objc.python_method
    def drag_ended(self, mx: float, my: float) -> None:
        pass

    @objc.python_method
    def clicked(self) -> None:
        pass

    @objc.python_method
    def show_at(self, x: float, y: float, screen=None) -> None:
        """Place the panel's bottom-left corner at Cocoa coordinates (x, y), clamped to the
        screen that point is on (or `screen`)."""
        vf = visible_frame(screen or screen_at(x, y))
        x, y = clamp_to(vf, x, y, self.width, self.height)
        self.panel.setFrameOrigin_((x, y))
        self.order_front()

    # -- getting on screen, and staying there ---------------------------------------
    #
    # After a display is unplugged macOS can drop our all-Spaces windows from *every*
    # Space. AppKit still reports them visible and orderFrontRegardless() returns happily,
    # but the window server has them on no Space and never draws them. Rebuilding the
    # NSPanel is the only way back, so every order-front checks itself shortly after.

    @objc.python_method
    def order_front(self) -> None:
        self.panel.orderFrontRegardless()
        later(VERIFY_DELAY, self._verify_on_screen, self.panel)

    @objc.python_method
    def _on_screen(self) -> bool:
        """Does the window server list this window as on screen? The only check verified
        against the broken state; isVisible() lies there, and isOnActiveSpace was not tested."""
        number = self.panel.windowNumber()
        infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
        return any(info.get(Quartz.kCGWindowNumber) == number for info in infos)

    @objc.python_method
    def _verify_on_screen(self, panel) -> None:
        """`panel` is the window we ordered front: if it was replaced or hidden meanwhile
        there is nothing to judge."""
        if panel is not self.panel or not self.panel.isVisible():
            return
        if self._on_screen():
            return
        name, number = type(self).__name__, self.panel.windowNumber()
        log.warning("%s window %d not on screen after order-front; rebuilding", name, number)
        now = time.monotonic()
        if now - self._last_rebuild < REBUILD_COOLDOWN:
            log.warning("%s window %d: rebuilt %.1fs ago, not rebuilding again", name, number, now - self._last_rebuild)
            return
        self._last_rebuild = now
        self._rebuild_window()
        self.panel.orderFrontRegardless()
        later(VERIFY_DELAY, self._confirm_rebuild, self.panel)

    @objc.python_method
    def _confirm_rebuild(self, panel) -> None:
        """One look at the rebuilt window, for the log only: no second rebuild."""
        if panel is not self.panel or not self.panel.isVisible():
            return
        name, number = type(self).__name__, self.panel.windowNumber()
        if self._on_screen():
            log.info("%s window %d rebuilt and on screen", name, number)
        else:
            log.error("%s window %d still not on screen after rebuild", name, number)

    @objc.python_method
    def _rebuild_window(self) -> None:
        """Move this panel's content view into a brand new window at the same frame. A fresh
        NSPanel with the same settings does land on every Space, which is the whole point."""
        old = self.panel
        frame = old.frame()
        content = old.contentView()
        new = self._make_window(NSMakeRect(0, 0, frame.size.width, frame.size.height))
        # Subclasses change these two after init (the drop zone casts no shadow and takes no
        # clicks) and animations leave the alpha somewhere in between.
        new.setHasShadow_(old.hasShadow())
        new.setIgnoresMouseEvents_(old.ignoresMouseEvents())
        new.setAlphaValue_(old.alphaValue())
        new.setFrame_display_(frame, False)
        new.setContentView_(content)
        old.orderOut_(None)
        old.close()
        self.panel = new

    @objc.python_method
    def hide(self) -> None:
        self.panel.orderOut_(None)

    @objc.python_method
    def visible(self) -> bool:
        return bool(self.panel.isVisible())

    @objc.python_method
    def contains_screen_point(self, qx: float, qy: float) -> bool:
        """Quartz (top-left origin) point inside the panel's frame?"""
        if not self.visible():
            return False
        f = self.panel.frame()
        cy = screen_height() - qy
        return f.origin.x <= qx <= f.origin.x + f.size.width and f.origin.y <= cy <= f.origin.y + f.size.height
