"""Render the Lyssna logo: a flat mark of a lowercase "l" stem followed by a hand-drawn
wave, in black and cream on a rounded tile. Writes an .icns plus a PNG preview next to it,
the dark and light logos for the docs, and the menu bar template glyph.

Usage: uv run python scripts/make_icon.py out.icns"""

import math
import subprocess
import sys
import tempfile
from pathlib import Path

import AppKit
from AppKit import NSBezierPath, NSColor, NSImage
from Foundation import NSMakeRect

INK = NSColor.colorWithRed_green_blue_alpha_(0x0F / 255, 0x0F / 255, 0x11 / 255, 1)    # #0F0F11
CREAM = NSColor.colorWithRed_green_blue_alpha_(0xF4 / 255, 0xEF / 255, 0xE6 / 255, 1)  # #F4EFE6
BLACK = NSColor.colorWithRed_green_blue_alpha_(0x14 / 255, 0x14 / 255, 0x14 / 255, 1)  # #141414

# The mark's bounding box on the 512-unit design canvas (y down), stroke included.
MARK_X, MARK_Y, MARK_W, MARK_H = 110, 136, 292, 240


def draw_mark(s: float, dx: float, dy: float, h: float, color: NSColor) -> None:
    """Draw the mark from 512-unit design coordinates (y down) into the current context,
    mapped as x -> dx + x*s and y -> h - (dy + y*s), since AppKit's y points up."""
    def pt(x: float, y: float) -> tuple[float, float]:
        return (dx + x * s, h - (dy + y * s))

    color.set()
    sx, sy = pt(110, 376)
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(sx, sy, 48 * s, 240 * s), 24 * s, 24 * s).fill()

    wave = NSBezierPath.bezierPath()
    wave.moveToPoint_(pt(210, 256))
    steps = 160
    for i in range(1, steps + 1):
        t = i / steps
        wave.lineToPoint_(pt(210 + 170 * t, 256 - 52 * math.sin(2 * math.pi * t)))
    wave.setLineWidth_(44 * s)
    wave.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    wave.setLineJoinStyle_(AppKit.NSLineJoinStyleRound)
    wave.stroke()


def tile(size: int, fill: NSColor = INK, mark: NSColor = CREAM) -> NSImage:
    """The logo on its rounded tile; dark (the app icon) by default."""
    s = size / 512
    img = NSImage.alloc().initWithSize_((size, size))
    img.lockFocus()
    fill.set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(51 * s, 51 * s, 410 * s, 410 * s), 94 * s, 94 * s).fill()
    draw_mark(s, 0, 0, size, mark)
    img.unlockFocus()
    return img


def menubar_glyph(size: int) -> NSImage:
    """Monochrome template glyph for the menu bar: the mark alone, centred with about 3 px
    of margin. Drawn in black on transparent; macOS recolours template images for light/dark bars."""
    s = (size - 6) / max(MARK_W, MARK_H)
    dx = (size - MARK_W * s) / 2 - MARK_X * s
    dy = (size - MARK_H * s) / 2 - MARK_Y * s
    img = NSImage.alloc().initWithSize_((size, size))
    img.lockFocus()
    draw_mark(s, dx, dy, size, NSColor.blackColor())
    img.unlockFocus()
    return img


def save_png(img: NSImage, path: Path, size: int) -> None:
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, size, size, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0
    )
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)
    img.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(0, 0, size, size), NSMakeRect(0, 0, img.size().width, img.size().height), AppKit.NSCompositingOperationSourceOver, 1.0
    )
    AppKit.NSGraphicsContext.restoreGraphicsState()
    rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None).writeToFile_atomically_(str(path), True)


def main(out: str) -> None:
    AppKit.NSApplication.sharedApplication()
    with tempfile.TemporaryDirectory() as d:
        iconset = Path(d) / "Lyssna.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            save_png(tile(size), iconset / f"icon_{size}x{size}.png", size)
            save_png(tile(size * 2), iconset / f"icon_{size}x{size}@2x.png", size * 2)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", out], check=True)
    save_png(tile(512), Path(out).with_suffix(".png"), 512)
    root = Path(__file__).resolve().parent.parent
    images = root / "docs" / "images"
    images.mkdir(parents=True, exist_ok=True)
    save_png(tile(512), images / "logo-dark.png", 512)
    save_png(tile(512, CREAM, BLACK), images / "logo-light.png", 512)
    assets = root / "lyssna" / "ui" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    save_png(menubar_glyph(40), assets / "menubar.png", 40)  # shown at 20pt, so this is the @2x


if __name__ == "__main__":
    main(sys.argv[1])
