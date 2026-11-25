"""The What's New window on Windows, the twin of legacy/ui/whats_new_window.py: this version's
highlights, opened from the menu after an update.

    [mark] What's new in Lyssna 0.3.0                 (mark + serif headline)
    •  one short line per highlight
    •  ...
    ------------------------------------------------
    This update was free. ♥ Support Lyssna     All Changes

It never opens on its own: lyssna.whatsnew decides when the menu offers it. The footer's left
side is the support sentence and its link with the heart (hidden while the website has no
address, see lyssna.support); All Changes opens the full changelog on GitHub. The measurements
are the Mac's, from layout/whats_new.py, and the height follows the text."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from .. import __version__, support, whatsnew
from ..menu import REPO_URL
from . import theme
from .layout.whats_new import (
    BULLET_W,
    CHANGES_TITLE,
    FOOTER_H,
    FOOTER_SIZE,
    HEAD_TO_NOTES,
    LABEL_INSET,
    MARGIN,
    MARK_GAP,
    MARK_H,
    NOTE_GAP,
    NOTE_LINE_H,
    NOTE_SIZE,
    NOTES_TO_FOOTER,
    SUPPORT_GAP,
    TITLE_SIZE,
    TOP,
    W,
)
from .layout.window import UPPER
from .window import Hairline, Header, Label, LyssnaWindow, TextButton, ink_left, place

CHANGES_URL = f"{REPO_URL}/blob/main/CHANGELOG.md"


class WhatsNewWindow(LyssnaWindow):
    """`version` is the running one unless a test sets it before prepare() or show()."""

    def __init__(self):
        super().__init__("What's New in Lyssna")
        self.version = __version__
        self.built = False

    def show(self) -> None:
        self.prepare()
        self.show_centered(UPPER)  # a little above the middle, by the Mac window's own rule

    def prepare(self) -> None:
        """Build the window if needed, without putting it on screen."""
        if not self.built:
            self._build()

    def _build(self) -> None:
        self.built = True
        inner = W - 2 * MARGIN

        self.heading = Header(whatsnew.window_title(self.version), self, mark_h=MARK_H, title_size=TITLE_SIZE,
                              mark_gap=MARK_GAP)
        self.heading.move(round(MARGIN), round(TOP))
        y = TOP + self.heading.fit(inner) + HEAD_TO_NOTES

        self.note_labels: list[Label] = []
        for i, text in enumerate(whatsnew.notes(self.version)):
            if i:
                y += NOTE_GAP
            bullet = Label("•", self, size=NOTE_SIZE, color=theme.content_dim, line_height=NOTE_LINE_H)
            bullet.fit()
            place(bullet, MARGIN - ink_left(bullet.font(), "•"), y)  # its ink on the margin
            label = Label(text, self, size=NOTE_SIZE, wrap=True, line_height=NOTE_LINE_H)
            _, h = label.fit(inner - BULLET_W)
            label.move(round(MARGIN + BULLET_W - LABEL_INSET), round(y))  # its text on the bullet's column
            self.note_labels.append(label)
            y += h
        y += NOTES_TO_FOOTER

        self.footer_line = Hairline(self)
        self.footer_line.setGeometry(0, round(y), round(W), 1)
        footer_top = y

        self.support_label = Label(support.WHATS_NEW_LINE, self, size=FOOTER_SIZE, color=theme.content_dim)
        _, lh = self.support_label.fit()
        left = MARGIN - ink_left(self.support_label.font(), self.support_label.value())  # its ink on the margin
        place(self.support_label, left, footer_top + (FOOTER_H - lh) / 2)
        self.support_button = TextButton(support.MENU_TITLE, self, size=FOOTER_SIZE, link=True, symbol=support.SYMBOL,
                                         clicked=self.open_support)
        bh = self.support_button.height()
        place(self.support_button, left + self.support_label.exact_width() + SUPPORT_GAP, footer_top + (FOOTER_H - bh) / 2)
        for view in (self.support_label, self.support_button):
            view.setVisible(support.enabled())

        self.changes_button = TextButton(CHANGES_TITLE, self, size=FOOTER_SIZE, weight="regular", clicked=self.open_changes)
        cw, ch = self.changes_button.width(), self.changes_button.height()
        # Its frame reaches past the margin, so its centred title ends about on it, as on the Mac.
        place(self.changes_button, W - MARGIN - cw + LABEL_INSET * 2, footer_top + (FOOTER_H - ch) / 2)

        self.setFixedSize(round(W), round(footer_top + FOOTER_H))

    def open_support(self) -> None:
        support.open_support("whats new")

    def open_changes(self) -> None:
        support._open(CHANGES_URL)


# -- previews (python -m lyssna.ui.preview) -------------------------------------------------

_PREVIEW_NOTES = (
    "Lyssna now runs on Windows. Select text anywhere and press Ctrl+Alt+S.",
    "The free voice on this PC reads without an account or a key.",
    "The player tucks into the edge of the screen when you are not using it.",
)


def _preview(with_support: bool) -> QWidget:
    """Three notes, with the support line or without it, whatever the environment says."""
    import os
    from unittest import mock

    with mock.patch.dict(whatsnew.NOTES, {"9.9.9": _PREVIEW_NOTES}), \
            mock.patch.object(support, "SITE_URL", "https://example.test" if with_support else ""), \
            mock.patch.dict(os.environ):
        os.environ.pop(support.SITE_ENV, None)
        w = WhatsNewWindow()
        w.version = "9.9.9"
        w.prepare()
    return w


PREVIEWS = {
    "whats-new": lambda: _preview(False),
    "whats-new.support": lambda: _preview(True),
}
