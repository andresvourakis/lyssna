"""Small notice panels that sit next to the pill, never part of it.

Toast: a one-line notice at the bottom centre of the screen ("Transcript cancelled" with
Undo, "Copied. Press ⌘V to paste", "Learned: Mattias" with Undo, permission and download
messages, errors). At most one action button; it fades in, stays a few seconds and fades out,
and a new notice replaces the one on screen (in place when both carry the same key, so a
download's percentage counts up without flickering).

Tip: the pill-shaped tooltip beside the mic button and the hands-free buttons ("Dictate fn",
"Cancel esc", "Finish fn"), on the inner side of the screen. It never takes the mouse.

Both are non-activating panels, so clicking the toast's button never takes focus from the
app the user is typing in. Every colour comes from theme."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

import AppKit
import objc
from AppKit import NSAnimationContext, NSAttributedString, NSFont, NSFontAttributeName, NSForegroundColorAttributeName

from ...ui.layout.toast import (
    ACTION_GAP,  # noqa: F401  (re-exported)
    ACTION_H,
    ACTION_PAD,
    ACTION_RIGHT,
    BOTTOM,
    FADE_IN,
    FADE_OUT,
    MAX_W,
    PAD_L,
    TIP_GAP,  # noqa: F401  (re-exported)
    TIP_H,
    TIP_PAD,
    TOAST_H,
    tip_origin,
    toast_width,
)
from . import theme
from .mainthread import later
from .panel import FloatingPanel, label, screen_under_mouse, visible_frame
from .widgets import HoverView

log = logging.getLogger("lyssna.ui.toast")


def _text_width(text: str, font) -> float:
    return float(NSAttributedString.alloc().initWithString_attributes_(text, {NSFontAttributeName: font}).size().width)


class Toast(FloatingPanel):
    width = 200.0
    height = TOAST_H
    corner_radius = TOAST_H / 2
    adaptive = True

    def init(self):
        self = objc.super(Toast, self).init()
        if self is None:
            return None
        self.panel.setAlphaValue_(0.0)
        self._notice = None
        self._key = ""
        self._token = None
        self._on_action: Callable[[], None] | None = None
        return self

    @objc.python_method
    def build(self, content):
        self.text = label("", size=13.0, color=theme.CONTENT, weight=AppKit.NSFontWeightMedium, mono=False)
        self.text.setAlignment_(AppKit.NSTextAlignmentLeft)
        self.text.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        content.addSubview_(self.text)
        self.action_bg = HoverView.make(((0, 0), (10, ACTION_H)), theme.FILL_SOFT, ACTION_H / 2)
        content.addSubview_(self.action_bg)
        self.action_btn = AppKit.NSButton.buttonWithTitle_target_action_("", self, "act:")
        self.action_btn.setBordered_(False)
        self.action_btn.setFont_(NSFont.systemFontOfSize_weight_(13.0, AppKit.NSFontWeightSemibold))
        self.action_btn.setContentTintColor_(theme.CONTENT)
        self.action_bg.addSubview_(self.action_btn)

    @objc.python_method
    def layout(self, text: str, action: str, max_w: float = MAX_W) -> float:
        """Size and place the text and the action for this notice; returns the toast's width."""
        font = self.text.font()
        bold = self.action_btn.font()
        # Whole points, so the window (which AppKit keeps on whole points) matches the layout.
        action_w = math.ceil(_text_width(action, bold)) if action else 0.0
        text_w = math.ceil(_text_width(text, font)) + 4  # a little slack: labels inset their text slightly
        width = toast_width(text_w, action_w)
        if width > max_w:
            max_w = math.floor(max_w)
            text_w -= width - max_w
            width = max_w
        self.text.setStringValue_(text)
        self.text.setToolTip_(text if width >= max_w else None)
        line_h = 17.0
        self.text.setFrame_(((PAD_L, (TOAST_H - line_h) / 2), (text_w, line_h)))
        self.action_bg.setHidden_(not action)
        if action:
            pill_w = action_w + 2 * ACTION_PAD
            self.action_bg.setFrame_(((width - ACTION_RIGHT - pill_w, (TOAST_H - ACTION_H) / 2), (pill_w, ACTION_H)))
            self.action_btn.setTitle_(action)
            self.action_btn.setFrame_(((0, 0), (pill_w, ACTION_H)))
        return width

    @objc.python_method
    def present(self, notice, screen=None, floor: float | None = None) -> None:
        """Show a notice (lyssna.controller.Notice) on `screen` (default: under the mouse).
        `floor` is the lowest y it may start at (above the pill when that sits on the bottom edge)."""
        screen = screen or screen_under_mouse()
        vf = visible_frame(screen)
        same = bool(notice.key) and notice.key == self._key and self.visible() and self._token is not None
        width = self.layout(notice.text, notice.action, min(MAX_W, vf.size.width - 40))
        x = round(vf.origin.x + (vf.size.width - width) / 2)
        y = max(vf.origin.y + BOTTOM, floor) if floor is not None else vf.origin.y + BOTTOM
        self.panel.setFrame_display_(((x, y), (width, TOAST_H)), True)
        self._notice, self._key, self._on_action = notice, notice.key, notice.on_action
        self.action_bg.set_hover(False)
        token = object()
        self._token = token
        if not same:
            self.panel.setAlphaValue_(0.0)
            self.order_front()
            self._fade(1.0, FADE_IN)
        else:
            self.panel.invalidateShadow()
        later(notice.seconds, self._expire, token)

    @objc.python_method
    def _expire(self, token) -> None:
        if token is self._token:
            self.dismiss()

    @objc.python_method
    def dismiss(self) -> None:
        token = object()  # whatever comes next is a different toast
        self._token = token
        self._key = ""

        def gone() -> None:
            if self._token is token:
                self.hide()
                self._token = None
        self._fade(0.0, FADE_OUT, gone)

    @objc.python_method
    def _fade(self, alpha: float, seconds: float, done: Callable[[], None] | None = None) -> None:
        def changes(ctx):
            ctx.setDuration_(seconds)
            self.panel.animator().setAlphaValue_(alpha)
        NSAnimationContext.runAnimationGroup_completionHandler_(changes, done)

    def act_(self, sender):
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

    def init(self):
        self = objc.super(Tip, self).init()
        if self is None:
            return None
        self.panel.setIgnoresMouseEvents_(True)
        return self

    @objc.python_method
    def build(self, content):
        self.text = label("", size=13.0, color=theme.CONTENT, mono=False)
        self.text.setLineBreakMode_(AppKit.NSLineBreakByClipping)
        content.addSubview_(self.text)

    @objc.python_method
    def show_beside(self, anchor: tuple[float, float, float, float], edge: str, text: str, key: str) -> None:
        medium = NSFont.systemFontOfSize_weight_(13.0, AppKit.NSFontWeightMedium)
        bold = NSFont.systemFontOfSize_weight_(13.0, AppKit.NSFontWeightBold)
        s = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: medium, NSForegroundColorAttributeName: theme.CONTENT})
        if key:
            s.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                f"  {key}", {NSFontAttributeName: bold, NSForegroundColorAttributeName: theme.CONTENT}))
        self.text.setAttributedStringValue_(s)
        text_w = float(s.size().width)
        width = TIP_PAD + text_w + TIP_PAD
        # The field gets a few points of room either side: a label insets its text slightly
        # and drops a run that does not fit whole.
        self.text.setFrame_(((TIP_PAD - 4, (TIP_H - 17) / 2), (text_w + 8, 17)))
        x, y = tip_origin(anchor, edge, width)
        self.panel.setFrame_display_(((x, y), (width, TIP_H)), True)
        self.order_front()
