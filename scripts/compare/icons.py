"""The icons family: every SF Symbol the legacy interface configures (symbol, point size,
weight), plus the 13 pt regular default the menu bar menu gives its row images, against the Qt
icon drawn the way the Qt twin of that call site draws it (lyssna/ui/icons.py, same size, same
weight). Round 1B's measurement (icons1b/measure.py), folded in.

Legacy (legacy_side process): NSImage.imageWithSystemSymbolName with an
NSImageSymbolConfiguration(pointSize, weight), drawn black at 2x with the image's size box
centred on a CANVAS-point canvas (what a borderless NSButton with NSImageOnly does).
Qt (qt_side process): icons.draw_icon into icons.icon_rect(centre, size), black, at 2x.
compose: reads both pictures' alpha and reports, per row, the ink box (alpha at least half) in
points, the box centre's offset from the canvas centre, and the ink amount (summed alpha, in 2x
pixels), with an overlay picture: today's Mac only in red, the new interface only in blue, both
in near-black."""

from __future__ import annotations

import json
from pathlib import Path

CANVAS = 48.0  # points
SCALE = 2
INK_BOX = 128  # alpha at or above this counts for the ink box

# The verdict: a row matches when both box sides and the centre are within BOX_TOL points and
# the ink amount within INK_TOL percent.
BOX_TOL = 0.5
INK_TOL = 5.0

# (symbol, point size, weight, where the legacy code uses it)
ROWS = [
    ("play.fill", 20, "semibold", "player card: play"),
    ("pause.fill", 20, "semibold", "player card: pause"),
    ("play.fill", 14, "semibold", "docked pill: play"),
    ("pause.fill", 14, "semibold", "docked pill: pause"),
    ("play.fill", 13, "semibold", "play button beside a selection"),
    ("play.fill", 10, "semibold", "idle menu: read"),
    ("backward.end.fill", 17, "semibold", "player card: previous sentence"),
    ("forward.end.fill", 17, "semibold", "player card: next sentence"),
    ("backward.end.fill", 12, "semibold", "docked pill: previous sentence"),
    ("forward.end.fill", 12, "semibold", "docked pill: next sentence"),
    ("xmark", 11, "bold", "player card and pill: close"),
    ("xmark", 9, "bold", "dictation: cancel"),
    ("xmark", 12, "regular", "dictionary: delete a word"),
    ("checkmark", 11, "bold", "dictation: finish"),
    ("checkmark", 14, "regular", "history: copied"),
    ("arrow.right.to.line.compact", 11, "bold", "player card: tuck to the right"),
    ("arrow.left.to.line.compact", 11, "bold", "player card: tuck to the left"),
    ("arrow.down.to.line.compact", 11, "bold", "player card: tuck to the bottom"),
    ("chevron.right", 11, "bold", "docked pill: expand (left edge)"),
    ("chevron.left", 11, "bold", "docked pill: expand (right edge)"),
    ("chevron.up", 11, "bold", "docked pill: expand (bottom edge)"),
    ("mic.fill", 14, "semibold", "idle menu: dictate"),
    ("clock.arrow.circlepath", 11, "semibold", "idle menu: history"),
    ("doc.on.doc", 14, "regular", "history: copy"),
    ("ellipsis", 14, "regular", "history: more"),
    ("magnifyingglass", 13, "regular", "history: search"),
    ("heart.fill", 10, "medium", "support links (history, What's New)"),
    ("lock", 10, "medium", "setup: privacy tag"),
    ("arrow.up.right", 10, "medium", "setup: privacy tag"),
    ("arrow.up.right", 10, "semibold", "setup: Get a key"),
    ("checkmark.circle.fill", 18, "medium", "setup: ready"),
    ("lock", 13, "regular", "menu: privacy line (menu default size)"),
    ("arrow.up.right", 13, "regular", "menu: privacy line (menu default size)"),
    ("heart.fill", 13, "regular", "menu: support (menu default size)"),
    ("exclamationmark.triangle", 13, "regular", "menu: accessibility warning (menu default size)"),
]


def row_name(name: str, pt: float, weight: str) -> str:
    return f"icons.{name}-{pt:g}-{weight}"


def selected(only: str | None) -> list[tuple]:
    from . import catalogue

    names = catalogue.select_names([row_name(*r[:3]) for r in ROWS], only)
    return [r for r in ROWS if row_name(*r[:3]) in names]


# -- the legacy side (AppKit) ---------------------------------------------------------------------


def legacy_side(raw: Path, only: str | None) -> int:
    import AppKit

    weights = {"regular": AppKit.NSFontWeightRegular, "medium": AppKit.NSFontWeightMedium,
               "semibold": AppKit.NSFontWeightSemibold, "bold": AppKit.NSFontWeightBold}
    raw.mkdir(parents=True, exist_ok=True)
    px = int(CANVAS * SCALE)
    n = 0
    for name, pt, weight, _use in selected(only):
        img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, name)
        if img is None:
            continue
        img = img.imageWithSymbolConfiguration_(
            AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(pt, weights[weight]))
        s = img.size()
        rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
            None, px, px, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 32)
        rep.setSize_((CANVAS, CANVAS))
        AppKit.NSGraphicsContext.saveGraphicsState()
        AppKit.NSGraphicsContext.setCurrentContext_(AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
        img.drawInRect_(((CANVAS / 2 - s.width / 2, CANVAS / 2 - s.height / 2), (s.width, s.height)))
        AppKit.NSGraphicsContext.restoreGraphicsState()
        data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
        data.writeToFile_atomically_(str(raw / f"{row_name(name, pt, weight)}.png"), True)
        n += 1
    return n


# -- the Qt side ----------------------------------------------------------------------------------


def qt_side(raw: Path, only: str | None) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from lyssna.ui import icons

    raw.mkdir(parents=True, exist_ok=True)
    px = int(CANVAS * SCALE)
    n = 0
    missing = {}
    for name, pt, weight, _use in selected(only):
        if name not in getattr(icons, "ICONS", {name: None}):
            missing[row_name(name, pt, weight)] = "the new interface has no icon by this name"
            continue
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(SCALE)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        try:
            icons.draw_icon(p, name, icons.icon_rect(CANVAS / 2, CANVAS / 2, pt), QColor(0, 0, 0), weight)
        except Exception as exc:  # the new interface has no icon by this name
            missing[row_name(name, pt, weight)] = f"{type(exc).__name__}: {exc}"
            p.end()
            continue
        p.end()
        img.save(str(raw / f"{row_name(name, pt, weight)}.png"))
        n += 1
    (raw / "missing.json").write_text(json.dumps(missing, indent=1), encoding="utf-8")
    return n


# -- the measurement (compose process) --------------------------------------------------------------


def _alpha(path: Path):
    import numpy as np
    from PySide6.QtGui import QImage

    img = QImage(str(path))
    if img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    a = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())
    return a[:, : img.width() * 4].reshape(img.height(), img.width(), 4)[..., 3].astype(np.int32)


def stats(a) -> dict:
    import numpy as np

    ys, xs = np.nonzero(a >= INK_BOX)
    if len(xs) == 0:
        return {"w": 0.0, "h": 0.0, "dx": 0.0, "dy": 0.0, "ink": 0.0}
    x0, x1, y0, y1 = xs.min() / SCALE, (xs.max() + 1) / SCALE, ys.min() / SCALE, (ys.max() + 1) / SCALE
    c = CANVAS / 2
    return {"w": float(x1 - x0), "h": float(y1 - y0), "dx": float((x0 + x1) / 2 - c), "dy": float((y0 + y1) / 2 - c),
            "ink": round(float(a.sum() / 255.0), 1)}


def overlay(al, aq, path: Path, zoom: int = 4) -> None:
    """Today's Mac alone in red, the new interface alone in blue, both near-black; green cross:
    the canvas centre."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage, QPainter

    fl, fq = al / 255.0, aq / 255.0
    rgb = np.empty((*al.shape, 3), dtype=np.float32)
    rgb[..., 0] = 255 * (1 - fq)
    rgb[..., 1] = 255 * (1 - np.maximum(fl, fq))
    rgb[..., 2] = 255 * (1 - fl)
    rgb = np.repeat(np.repeat(np.clip(rgb, 0, 255).astype(np.uint8), zoom, axis=0), zoom, axis=1)
    h, w = rgb.shape[:2]
    img = QImage(np.ascontiguousarray(rgb).data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    p = QPainter(img)
    p.setPen(QColor(0, 170, 0))
    p.drawLine(w // 2, 0, w // 2, h)
    p.drawLine(0, h // 2, w, h // 2)
    p.end()
    img.save(str(path))


def compose(out: Path, only: str | None) -> list[dict]:
    """Measure every row both sides drew; write icons.md and the overlays under icons/.
    Returns the rows (for the report and review.html)."""
    raw_l, raw_q = out / "raw" / "legacy" / "icons", out / "raw" / "qt" / "icons"
    if not raw_l.exists() and not raw_q.exists():
        return []
    sheets = out / "icons"
    sheets.mkdir(parents=True, exist_ok=True)
    try:
        missing = json.loads((raw_q / "missing.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        missing = {}
    rows = []
    for name, pt, weight, use in selected(only):
        key = row_name(name, pt, weight)
        al, aq = _alpha(raw_l / f"{key}.png"), _alpha(raw_q / f"{key}.png")
        r = {"key": key, "symbol": name, "pt": pt, "weight": weight, "use": use}
        if al is None or aq is None:
            r["missing"] = missing.get(key) or ("not drawn by today's Mac app" if al is None else "not drawn")
            rows.append(r)
            continue
        sl, sq = stats(al), stats(aq)
        pct = 100.0 * (sq["ink"] - sl["ink"]) / sl["ink"] if sl["ink"] else 0.0
        worst = max(abs(sl["w"] - sq["w"]), abs(sl["h"] - sq["h"]), abs(sl["dx"] - sq["dx"]), abs(sl["dy"] - sq["dy"]))
        r.update(legacy=sl, qt=sq, ink_pct=round(pct, 1), box_off=round(worst, 2),
                 ok=bool(worst <= BOX_TOL and abs(pct) <= INK_TOL), overlay=f"icons/{key}.png")
        overlay(al, aq, sheets / f"{key}.png")
        rows.append(r)
    lines = ["# Icons: today's Mac symbols against the new interface's icons\n",
             f"Each symbol at the size and weight the legacy code gives it, drawn black at 2x on a {CANVAS:g} pt canvas, "
             f"centred. Box: the ink (alpha at least {INK_BOX}/255) in points; dx, dy: the box centre from the canvas "
             f"centre; ink: summed alpha in 2x pixels. Matches: every box side and the centre within {BOX_TOL:g} pt and "
             f"the ink within {INK_TOL:g} percent. Overlays in icons/: red today's Mac only, blue the new interface "
             "only.\n",
             "| symbol | pt | weight | use | legacy w x h | Qt w x h | legacy dx,dy | Qt dx,dy | legacy ink | Qt ink | ink % "
             "| matches |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "missing" in r:
            lines.append(f"| {r['symbol']} | {r['pt']:g} | {r['weight']} | {r['use']} | | | | | | | | no: {r['missing']} |")
            continue
        sl, sq = r["legacy"], r["qt"]
        lines.append(f"| {r['symbol']} | {r['pt']:g} | {r['weight']} | {r['use']} | {sl['w']:g} x {sl['h']:g} | "
                     f"{sq['w']:g} x {sq['h']:g} | {sl['dx']:+g},{sl['dy']:+g} | {sq['dx']:+g},{sq['dy']:+g} | "
                     f"{sl['ink']:.0f} | {sq['ink']:.0f} | {r['ink_pct']:+.1f} | {'yes' if r['ok'] else 'NO'} |")
    ok = sum(1 for r in rows if r.get("ok"))
    lines.append(f"\n{ok} of {len(rows)} within tolerance.")
    (out / "icons.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "icons.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows
