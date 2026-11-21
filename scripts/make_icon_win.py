"""Draw Lyssna's Windows icon (scripts/Lyssna.ico): the cream mark on the ink rounded tile, the same
picture the app uses for its window icon (app_icon in lyssna/ui/window.py).

    python scripts/make_icon_win.py [OUTPUT]      default: scripts/Lyssna.ico

It runs on any system with PySide6 (no AppKit). Each size is drawn at that size, encoded as a PNG
and packed into one .ico by hand, because Qt's ICO writer is a plugin that is not always present.
Windows Vista and later read PNG entries in an .ico."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw(side: int) -> bytes:
    """One square tile, `side` pixels wide, as PNG bytes. The geometry is app_icon's."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter

    from lyssna.ui import theme
    from lyssna.ui.layout.tokens import CREAM, INK
    from lyssna.ui.mark import draw_mark

    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.rgba(INK))
    r = side * 0.22
    p.drawRoundedRect(QRectF(0, 0, side, side), r, r)
    inset = side * 0.22
    draw_mark(p, QRectF(inset, inset, side - 2 * inset, side - 2 * inset), theme.rgba(CREAM))
    p.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"could not encode the {side} px icon as PNG")
    buffer.close()
    return bytes(data.data())


def pack_ico(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico file holding each (side, png_bytes). The header is ICONDIR (reserved, type 1 for
    icons, count), then one 16-byte ICONDIRENTRY per image, then the images. A width or height of
    0 in an entry means 256."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for side, png in images:
        if not 1 <= side <= 256:
            raise ValueError(f"an .ico entry must be 1 to 256 pixels, not {side}")
        dim = 0 if side == 256 else side
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return header + entries + blobs


def read_ico(data: bytes) -> list[tuple[int, int, bytes]]:
    """The (width, height, image bytes) entries of an .ico, for checking what pack_ico wrote."""
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1:
        raise ValueError("not an .ico file")
    out = []
    for i in range(count):
        w, h, _colors, _res, _planes, _bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        out.append((w or 256, h or 256, data[offset:offset + size]))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    out = Path(argv[0]) if argv else ROOT / "scripts" / "Lyssna.ico"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window, no display needed
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841  (painting wants one)
    data = pack_ico([(side, draw(side)) for side in SIZES])
    entries = read_ico(data)
    if [w for w, _h, _png in entries] != list(SIZES) or any(not png.startswith(b"\x89PNG") for _w, _h, png in entries):
        print(f"make_icon_win: FAILED, the icon written does not read back as {len(SIZES)} PNG entries", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"make_icon_win: wrote {out} ({len(data)} bytes, sizes {', '.join(str(s) for s in SIZES)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
