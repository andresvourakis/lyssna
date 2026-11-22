r"""The Windows side of the tray icon and its menu, for the Qt tray in lyssna/ui/tray.py. Twin of
lyssna/mac/statusbar.py, with the same functions; MenuBar in lyssna/legacy/app.py is the
specification both follow.

What Windows does instead of the Mac's menu bar:
- A tray icon cannot show text beside it, so what the Mac shows there goes into the tooltip
  ("Lyssna: Reading 2 of 5").
- The icon is tinted white or near-black for the taskbar, which follows "Choose your default
  Windows mode" (HKCU\...\Themes\Personalize, SystemUsesLightTheme) and can differ from the apps'
  mode. When the keyboard and mouse hooks did not start it carries a small amber dot.
- Windows menus have no section header item: a header is the greyed-out row in a small
  semibold font.
- Shortcuts are shown as text at the right of an item ("Ctrl+Alt+S").
- Quit shows no shortcut, as Windows tray menus do not.
- A .toml file usually has no app of its own, so the config file opens in Notepad.

PySide6 and winreg are imported inside the functions, so this module imports on any system and
the tests can hand it a fake winreg."""

from __future__ import annotations

import logging
import subprocess
import weakref
from collections.abc import Collection
from pathlib import Path

log = logging.getLogger("lyssna.win.statusbar")

ICON_PATH = Path(__file__).resolve().parent.parent / "ui" / "assets" / "menubar.png"
ICON_SIZES = (16, 20, 24, 32)  # 100, 125, 150 and 200 percent scaling
ICON_CROP = (2.0, 2.0, 36.0, 36.0)  # the part of menubar.png the tray icon shows
WARNING_DOT = (255, 185, 0)  # the dot on the tray icon when the hooks did not start
TASKBAR_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
QUIT_SHORTCUT = ""  # Windows tray menus show none
CLICK_OPENS_MENU = False  # only a right click opens a tray icon's menu by itself
MENU_SYMBOL_POINTS = 16.0  # the menu's icons fill the 16 px icon column of a Windows menu

_shown: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # tray -> (dark taskbar, warning) its icon shows


def taskbar_is_dark() -> bool:
    """Is the taskbar dark? Dark when the registry cannot be read: Windows 11's default."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TASKBAR_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
        return int(value) == 0
    except (ImportError, OSError, ValueError):
        log.debug("could not read the taskbar theme", exc_info=True)
        return True


def tray_pixmap(size: int, dark_taskbar: bool, warning: bool = False):
    """The logo `size` pixels square, white on a dark taskbar and near-black on a light one,
    with a small amber dot in the corner when `warning`."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

    src = QImage(str(ICON_PATH)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if not src.isNull():
        # The asset is a 40 px square with room around the glyph for the Mac menu bar; a tray
        # icon fills its square, so draw the middle 36 px of it.
        p.drawImage(QRectF(0, 0, size, size), src, QRectF(*ICON_CROP))
    # Tint: keep the glyph's alpha, replace its colour.
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(QRectF(0, 0, size, size), QColor(255, 255, 255) if dark_taskbar else QColor(0x1C, 0x1C, 0x1C))
    if warning:
        d = max(4.0, size * 0.34)
        ring = max(1.0, size / 16)
        x, y = size - d, size - d
        p.setPen(Qt.PenStyle.NoPen)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.drawEllipse(QRectF(x - ring, y - ring, d + 2 * ring, d + 2 * ring))  # a gap around the dot
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setBrush(QColor(*WARNING_DOT))
        p.drawEllipse(QRectF(x, y, d, d))
    p.end()
    return QPixmap.fromImage(img)


def tray_icon(dark_taskbar: bool, warning: bool = False):
    """The tray icon at every size Windows asks for, each drawn at that size so none is blurred."""
    from PySide6.QtGui import QIcon

    icon = QIcon()
    for n in ICON_SIZES:
        icon.addPixmap(tray_pixmap(n, dark_taskbar, warning))
    return icon


def set_icon(tray, warning: bool, force: bool = False) -> None:
    """Tint the icon for the taskbar (and dot it for `warning`); redrawn only when either changes."""
    want = (taskbar_is_dark(), warning)
    if force or _shown.get(tray) != want:
        _shown[tray] = want
        tray.setIcon(tray_icon(*want))


def show_status(tray, title: str, tooltip: str) -> None:
    """Nothing can show beside a tray icon: the tooltip says it."""
    if tray.toolTip() != tooltip:
        tray.setToolTip(tooltip)


def mark_headers(tray, titles: Collection[str]) -> None:
    """Windows menus have no native header; style_header drew them already."""


def style_header(action, menu) -> None:
    """A header row: greyed out (the action is disabled) in a semibold font one point smaller."""
    from PySide6.QtGui import QFont

    font = QFont(menu.font())
    size = font.pointSizeF()
    if size > 0:
        font.setPointSizeF(max(1.0, size - 1.0))
    else:
        font.setPixelSize(max(1, font.pixelSize() - 1))
    font.setWeight(QFont.Weight.DemiBold)
    action.setFont(font)


def shortcut_text(spec: str) -> str:
    """The text at the right of an item for a hotkey spec: "ctrl+alt+s" -> "Ctrl+Alt+S"."""
    from ..hotkeys import tokens
    from .hotkeys import pretty

    return pretty(tokens(spec)) if spec else ""


def open_text_file(path: Path) -> None:
    """Open a file in Notepad."""
    subprocess.Popen(["notepad.exe", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
