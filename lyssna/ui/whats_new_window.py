"""The What's New window: this version's highlights, opened from the menu after an update.

    [mark] What's new in Lyssna 0.3.0                 (mark + serif headline)
    •  one short line per highlight
    •  ...
    ------------------------------------------------
    This update was free. ♥ Support Lyssna     All Changes

It never opens on its own: lyssna.whatsnew decides when the menu offers it. The footer's left
side is the support sentence and its link (hidden while the website has no address, see
lyssna.support); All Changes opens the full changelog on GitHub.

The look follows the History window: the brand surface, a serif headline, the system font for
the rest, a hairline above the footer. The height follows the text. Every measurement is a
named constant below."""

from __future__ import annotations

import AppKit
import objc
from AppKit import NSApp, NSBackingStoreBuffered
from Foundation import NSMakeRect, NSObject

from .. import __version__, support, whatsnew
from ..menu import REPO_URL
from . import theme
from .history_window import HistorySurfaceView, HistoryTextButton, _ink_left, _styled_label
from .mark import MarkView
from .panel import TintedView, screen_under_mouse
from .setup_window import EditingWindow, _fit, _font, _label, _serif

# -- measurements (8 pt grid) -------------------------------------------------------------------

W = 460.0                     # content width; the height follows the text
MARGIN = 32.0
TOP = 28.0
MARK_H = 28.0
TITLE_SIZE = 22.0
MARK_GAP = 10.0               # mark to headline
LABEL_INSET = 2.0             # an NSTextField label draws its text this far in from its frame
HEAD_TO_NOTES = 24.0
NOTE_SIZE = 14.0
NOTE_LINE_H = 20.0
NOTE_GAP = 12.0               # between highlights
BULLET_W = 18.0               # the bullet's column
NOTES_TO_FOOTER = 28.0
FOOTER_H = 48.0
FOOTER_SIZE = 12.0
SUPPORT_GAP = 2.0             # the sentence to its link

CHANGES_TITLE = "All Changes"
CHANGES_URL = f"{REPO_URL}/blob/main/CHANGELOG.md"


class WhatsNewWindow(NSObject):
    """Owns the window. `version` is the running one unless a test sets it before show()."""

    def init(self):
        self = objc.super(WhatsNewWindow, self).init()
        if self is None:
            return None
        self.version = __version__
        self.window = None
        return self

    @objc.python_method
    def show(self) -> None:
        self.prepare()
        if not self.window.isVisible():
            vf = screen_under_mouse().visibleFrame()
            f = self.window.frame()
            x = vf.origin.x + (vf.size.width - f.size.width) / 2
            y = vf.origin.y + (vf.size.height - f.size.height) * 0.6  # a little above the middle
            self.window.setFrameOrigin_((round(x), round(max(vf.origin.y, y))))
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def prepare(self) -> None:
        """Build the window if needed, without putting it on screen."""
        if self.window is None:
            self._build()

    @objc.python_method
    def _build(self) -> None:
        style = AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
        win = EditingWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, 300), style, NSBackingStoreBuffered, False)
        win.setTitle_("What's New in Lyssna")
        win.setTitlebarAppearsTransparent_(True)
        win.setBackgroundColor_(theme.SURFACE)
        win.setReleasedWhenClosed_(False)
        content = HistorySurfaceView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 300))
        win.setContentView_(content)
        self.window = win
        self.content = content
        add = content.addSubview_
        inner = W - 2 * MARGIN

        self.mark = MarkView.make(MARK_H)
        add(self.mark)
        self.title = _label(whatsnew.window_title(self.version), TITLE_SIZE)
        self.title.setFont_(_serif(TITLE_SIZE))
        add(self.title)
        _fit(self.title)
        mw = self.mark.frame().size.width
        th = self.title.frame().size.height
        row_h = max(MARK_H, th)
        y = TOP
        self.mark.setFrameOrigin_((MARGIN, y + (row_h - MARK_H) / 2))
        self.title.setFrameOrigin_((MARGIN + mw + MARK_GAP - LABEL_INSET, y + (row_h - th) / 2))
        y += row_h + HEAD_TO_NOTES

        self.note_labels = []
        for i, text in enumerate(whatsnew.notes(self.version)):
            if i:
                y += NOTE_GAP
            bullet = _styled_label("•", _font(NOTE_SIZE), theme.CONTENT_DIM, NOTE_LINE_H, wrap=False)
            _fit(bullet)
            bullet.setFrameOrigin_((MARGIN - _ink_left(bullet), y))
            add(bullet)
            label = _styled_label(text, _font(NOTE_SIZE), theme.CONTENT, NOTE_LINE_H)
            _, h = _fit(label, inner - BULLET_W)
            label.setFrameOrigin_((MARGIN + BULLET_W - LABEL_INSET, y))
            add(label)
            self.note_labels.append(label)
            y += h
        y += NOTES_TO_FOOTER

        add(TintedView.make(NSMakeRect(0, y, W, 1), theme.HAIRLINE))
        footer_top = y

        self.support_label = _label(support.WHATS_NEW_LINE, FOOTER_SIZE, color=theme.CONTENT_DIM)
        add(self.support_label)
        self.support_button = HistoryTextButton.make(support.MENU_TITLE, self, "openSupport:", link=True,
                                                     symbol=support.SYMBOL)
        add(self.support_button)
        lw, lh = _fit(self.support_label)
        left = MARGIN - _ink_left(self.support_label)
        self.support_label.setFrameOrigin_((left, footer_top + (FOOTER_H - lh) / 2))
        bh = self.support_button.frame().size.height
        self.support_button.setFrameOrigin_((left + lw + SUPPORT_GAP, footer_top + (FOOTER_H - bh) / 2))
        for view in (self.support_label, self.support_button):
            view.setHidden_(not support.enabled())

        self.changes_button = HistoryTextButton.make(CHANGES_TITLE, self, "openChanges:",
                                                     weight=AppKit.NSFontWeightRegular)
        add(self.changes_button)
        cw, ch = self.changes_button.frame().size.width, self.changes_button.frame().size.height
        self.changes_button.setFrameOrigin_((W - MARGIN - cw + LABEL_INSET * 2, footer_top + (FOOTER_H - ch) / 2))

        # The content view is flipped, so growing or shrinking the window keeps the layout.
        win.setContentSize_((W, footer_top + FOOTER_H))

    def openSupport_(self, sender):
        support.open_support("whats new")

    def openChanges_(self, sender):
        support._open(CHANGES_URL)
