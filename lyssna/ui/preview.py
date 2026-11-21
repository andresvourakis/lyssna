"""A gallery of the Qt interface's screens, and a screenshot tool, so the Windows look can be
reviewed on any computer (a Mac included) side by side with the Mac app.

    python -m lyssna.ui.preview               the gallery: every preview, live
    python -m lyssna.ui.preview --list        the preview names
    python -m lyssna.ui.preview <name>        one preview, live
    python -m lyssna.ui.preview --dark        force dark mode (or --light)
    python -m lyssna.ui.preview --shots DIR   every preview in both schemes to DIR/<name>-light.png
                                                 and DIR/<name>-dark.png, at 2x, then exit

Discovery: every module in lyssna.ui may define a module-level
`PREVIEWS: dict[str, Callable[[], QWidget]]`. Each factory builds its screen with fake state
and returns the widget to show: a plain widget, or a FloatingPanel's `.panel` window (shown
with its painted shadow). A new screen registers itself just by defining PREVIEWS; this file
never changes for it. card() and Card help lay out a preview on a surface."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import pkgutil
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QListWidget, QScrollArea, QVBoxLayout, QWidget

from . import theme

log = logging.getLogger("lyssna.ui.preview")

Factory = Callable[[], QWidget]
SCHEMES = ("light", "dark")
DPR = 2.0
PAD = 24          # backdrop around a plain widget in a shot
CHECK = 10        # checker square, logical pixels
CHECK_A, CHECK_B = QColor(0x80, 0x80, 0x80), QColor(0x8E, 0x8E, 0x8E)


# -- helpers for preview factories -------------------------------------------------------------


class Card(QWidget):
    """A surface-coloured card with a hairline border and painted captions, to set previews on.
    Children are placed by absolute geometry (parent=card)."""

    def __init__(self, w: float, h: float, radius: float = 14.0, parent: QWidget | None = None):
        super().__init__(parent)
        self.radius = radius
        self.captions: list[tuple[str, QRectF, Qt.AlignmentFlag]] = []
        self.setFixedSize(round(w), round(h))

    def caption(self, text: str, x: float, y: float, w: float = 120.0, h: float = 14.0,
                align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> None:
        """A dim 11 px label at (x, y), drawn in the current scheme's colour."""
        self.captions.append((text, QRectF(x, y, w, h), align))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(theme.hairline())
        p.setBrush(theme.surface())
        p.drawRoundedRect(r, self.radius, self.radius)
        p.setFont(theme.ui_font(11))
        p.setPen(theme.content_dim())
        for text, rect, align in self.captions:
            p.drawText(rect, int(align | Qt.AlignmentFlag.AlignVCenter), text)
        p.end()


def card(w: float, h: float, radius: float = 14.0) -> Card:
    return Card(w, h, radius)


# -- discovery ---------------------------------------------------------------------------------


def discover() -> dict[str, Factory]:
    """Every PREVIEWS entry of every module in lyssna.ui, by name (the layout rules draw
    nothing, so that package is not searched). A module that fails to import is logged and
    skipped, so one broken screen does not hide the rest."""
    import lyssna.ui as package

    found: dict[str, Factory] = {}
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if info.ispkg:
            continue
        name = f"{package.__name__}.{info.name}"
        try:
            module = importlib.import_module(name)
        except Exception:
            log.exception("could not import %s for its previews", name)
            continue
        for key, factory in (getattr(module, "PREVIEWS", None) or {}).items():
            if key in found:
                log.warning("preview %r is defined twice; keeping the first", key)
                continue
            found[key] = factory
    return found


# -- rendering ---------------------------------------------------------------------------------


def _is_panel_window(widget: QWidget) -> bool:
    from .panel import PanelWindow

    return isinstance(widget, PanelWindow)


def paint_backdrop(p: QPainter, w: float, h: float) -> None:
    """A neutral mid-grey checker, so translucency, shadows and rounded corners show."""
    p.fillRect(QRectF(0, 0, w, h), CHECK_A)
    for row in range(int(h // CHECK) + 1):
        for col in range(int(w // CHECK) + 1):
            if (row + col) % 2:
                p.fillRect(QRectF(col * CHECK, row * CHECK, CHECK, CHECK), CHECK_B)


def _prepare(widget: QWidget) -> None:
    widget.ensurePolished()
    if widget.layout() is not None:
        widget.layout().activate()
    if not widget.testAttribute(Qt.WidgetAttribute.WA_Resized):
        widget.adjustSize()


def render(widget: QWidget, dpr: float = DPR) -> QImage:
    """The widget on the checker backdrop, at `dpr`. A panel window brings its own margin (its
    shadow lives there), so it gets less padding than a plain widget."""
    _prepare(widget)
    pad = 12 if _is_panel_window(widget) else PAD
    w, h = widget.width() + 2 * pad, widget.height() + 2 * pad
    img = QImage(round(w * dpr), round(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    p = QPainter(img)
    paint_backdrop(p, w, h)
    widget.render(p, QPoint(pad, pad), QRegion(), QWidget.RenderFlag.DrawChildren)
    p.end()
    return img


def shots(directory: str | os.PathLike, names: list[str] | None = None) -> list[Path]:
    """Render every preview (or `names`) in light and dark to `directory`; returns the files."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    previews = discover()
    written: list[Path] = []
    try:
        for scheme in SCHEMES:
            theme.set_scheme(scheme)
            for name, factory in previews.items():
                if names and name not in names:
                    continue
                widget = factory()
                path = out / f"{name}-{scheme}.png"
                render(widget).save(str(path))
                written.append(path)
                widget.hide()
                widget.deleteLater()
                QApplication.processEvents()
    finally:
        theme.set_scheme(None)
    return written


# -- the live gallery --------------------------------------------------------------------------


class _Stage(QWidget):
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        paint_backdrop(p, self.width(), self.height())
        p.end()


class Gallery(QWidget):
    """A list of previews on the left; the chosen one lives on the right (a panel floats over
    the stage, as it would over the desktop)."""

    def __init__(self, previews: dict[str, Factory]):
        super().__init__()
        self.setWindowTitle("Lyssna for Windows: preview")
        self.previews = previews
        self.current: QWidget | None = None
        self.list = QListWidget()
        self.list.addItems(list(previews))
        self.list.setFixedWidth(200)
        self.stage = _Stage()
        self.stage.setMinimumSize(720, 520)
        self.host = QVBoxLayout(self.stage)
        self.host.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(self.stage)
        scroll.setWidgetResizable(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.list)
        row.addWidget(scroll, 1)
        self.list.currentTextChanged.connect(self.choose)
        if previews:
            self.list.setCurrentRow(0)

    def choose(self, name: str) -> None:
        if self.current is not None:
            self.current.hide()
            self.current.deleteLater()
            self.current = None
        factory = self.previews.get(name)
        if factory is None:
            return
        widget = factory()
        self.current = widget
        if _is_panel_window(widget):
            centre = self.stage.mapToGlobal(self.stage.rect().center())
            widget.move(centre.x() - widget.width() // 2, centre.y() - widget.height() // 2)
            widget.owner.order_front()
        else:
            self.host.addWidget(widget, 0, Qt.AlignmentFlag.AlignCenter)
            widget.show()

    def closeEvent(self, ev) -> None:
        if self.current is not None:
            self.current.hide()
        super().closeEvent(ev)


def _app() -> QApplication:
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication(sys.argv[:1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lyssna.ui.preview", description="Preview the Qt interface.")
    ap.add_argument("name", nargs="?", help="open only this preview")
    ap.add_argument("--list", action="store_true", help="print the preview names and exit")
    scheme = ap.add_mutually_exclusive_group()
    scheme.add_argument("--dark", action="store_true", help="force dark mode")
    scheme.add_argument("--light", action="store_true", help="force light mode")
    ap.add_argument("--shots", metavar="DIR", help="save every preview in both schemes as PNG files and exit")
    args = ap.parse_args(argv)

    if (args.shots or args.list) and QApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no windows flash up
    app = _app()
    previews = discover()

    if args.list:
        for name in previews:
            print(name)
        return 0
    if args.shots:
        files = shots(args.shots, [args.name] if args.name else None)
        print(f"wrote {len(files)} files to {args.shots}")
        return 0
    if args.dark or args.light:
        theme.set_scheme("dark" if args.dark else "light")
    if args.name:
        if args.name not in previews:
            print(f"no preview named {args.name!r}; try --list", file=sys.stderr)
            return 2
        previews = {args.name: previews[args.name]}
    gallery = Gallery(previews)
    gallery.resize(980, 620)
    gallery.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
