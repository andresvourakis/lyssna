"""Measure each pair and lay out what a person reads: a comparison sheet per pair and scheme,
the structural table, the copy audit, the menu diff, index.html and report.md.

    python -m scripts.compare.compose --out DIR

Reads DIR/raw/legacy and DIR/raw/qt (written by the two sides). Runs on Qt's offscreen platform
(QImage for the files and the drawing, numpy for the numbers); never imports AppKit.

The numbers, per pair and scheme:
  * both sizes (a mismatch is a finding of its own; the smaller picture is padded at its right
    and bottom, so both stay anchored top-left),
  * diff: the share of pixels whose largest channel differs by more than TOL (of 255), both
    pictures laid on the same mid-grey matte, so a transparency difference counts too,
  * beyond 1 px: the share that still differs when the Qt picture may move by one device pixel
    in any direction (this drops antialiasing fringes and half-pixel shifts; what is left is a
    real difference in shape, colour or place), and the box around it, in logical pixels,
  * the elements matched by icon, text or role, and their frames, fonts and colours."""

from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from scripts.compare import catalogue, icons, review  # noqa: E402

TOL = review.TOL  # a channel difference above this counts (of 255)
MATTE = (128, 128, 128)
SCALE = 2.0


# What the harness still cannot measure reliably: listed at the top of report.md so nobody reads
# one of these as a product difference.
UNRELIABLE = [
    "Window shadows: the window server draws the legacy panels' shadow and it cannot be captured offscreen; the Qt "
    "panels' painted shadow is switched off for the pictures. Shadows are compared by their numbers only.",
    "The sliver's sparks (the legacy CAEmitterLayer) are not drawn at a frozen moment; both sides leave them out.",
    "A spinning progress indicator's motion; pictures show its first frame.",
    "Three legacy fades that AppKit or Core Animation only run on screen are evaluated by the harness from the "
    "code's own duration and curve, not drawn by the system: the hands-free cancel and finish buttons "
    "(NSAnimationContext alphaValue, ease-out), the scrub knob's fade-in (an implicit CATransition, default curve) "
    "and the hover glow (an implicit opacity action in a 0.14 s CATransaction, default curve). Their timing is "
    "therefore the code's stated timing, not a measurement.",
    "Animation 'progress' (motion.md, review.html) is measured from the pictures: how far each frame is from the "
    "first towards the last. For a looping drift (the sliver's 8 s strip) it is not a timing measure; read the "
    "frames themselves.",
    "During the hands-free growth the legacy filmstrip draws cancel and finish at their final places inside the growing "
    "capsule (only the capsule and the wave get frame animations offscreen); on screen AppKit may carry them along "
    "with the capsule. The Qt buttons ride with the capsule. Treat button positions in the middle frames as unverified.",
    "history.fix: both fields have their whole text selected, as the app does after Fix a Word, and both draw the "
    "selection in the system's inactive-window colour, since the window is never key and the app never active here. "
    "In use, both draw it in the user's highlight colour (see the note on the pair).",
    "Text frames: an AppKit field's frame includes its cell's 2 px insets and a Qt label may span its whole row, so the "
    "x/y/w/h rows of text elements in structure.md are definitions, not differences. Text placement is compared by its "
    "ink instead (the 'ink' rows), and only those reach the verdicts.",
    "Elements listed as only on one side in structure.md (shapes Qt paints without a widget of their own, legacy "
    "separators, the setup key caps, radio images): usually the same thing drawn differently, not missing. They do "
    "not reach the verdicts; the pixels do.",
    "Text field frames: a bezel-less AppKit field inside a drawn box against a Qt line edit or text edit; their frames "
    "are definitions too and do not reach the verdicts. An empty field is compared by its placeholder's ink, a field "
    "with selected text (history.fix) not by ink at all.",
    "Text a widget paints itself without a text element per paragraph (the history rows' folded dictations, the "
    "setup key caps) has no ink rows; it reaches the verdicts only through the pixels. A folded dictation that ends "
    "at a different word (history: 'Windows in…' against 'Windows …') is visible in the pictures but not named.",
    "Title bars of the windows: the system's on both sides, not in the pictures.",
    "Tooltips: the one a hover shows is compared. A legacy view whose whole area lies under a subview with its own "
    "tooltip never shows its own (AppKit shows the deepest view's), so that stored tooltip is listed in the legacy "
    "elements as tooltip_never_shown and not compared (the player's voice badge 'Kokoro · Heart', under its "
    "button's 'Change voice').",
    "Menus are compared row by row (title, order, shortcut, tick, heading, enabled); an NSMenu cannot be drawn without "
    "opening it on screen, so their look is not compared.",
    "The toast's window fade: the legacy window alpha cannot be animated offscreen; compared from the code.",
    "The Qt pictures follow the main screen's pixel density (Retina, 2.0, on this run; written to raw/qt/screen.json). "
    "On a 1x main screen the Qt side would draw at 1x into the 2x pictures.",
]


# -- pictures -----------------------------------------------------------------------------------


def load(path: Path, background: str | None = None):
    """The PNG as an (h, w, 4) float array, premultiplied, 0..255; `background` (#RRGGBBAA)
    is laid underneath first (a titled window's own background)."""
    from PySide6.QtGui import QImage

    img = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine())
    arr = arr[:, : w * 4].reshape(h, w, 4).astype(np.float32)
    if background:
        r, g, b, a = (int(background[i:i + 2], 16) for i in (1, 3, 5, 7))
        under = np.array([r * a / 255, g * a / 255, b * a / 255, a], dtype=np.float32)
        arr = arr + under * (1 - arr[..., 3:4] / 255)
    return arr


def on_matte(arr, h: int, w: int):
    """RGB on the mid-grey matte, padded to (h, w) with the matte."""
    out = np.empty((h, w, 3), dtype=np.float32)
    out[...] = MATTE
    ah, aw = arr.shape[:2]
    rgb = arr[..., :3] + np.array(MATTE, dtype=np.float32) * (1 - arr[..., 3:4] / 255)
    out[:ah, :aw] = rgb
    return out


def measure(a, b) -> dict:
    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    A, B = on_matte(a, h, w), on_matte(b, h, w)
    d = np.abs(A - B).max(axis=2)
    diff = d > TOL
    best = d.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            shifted = np.roll(np.roll(B, dy, axis=0), dx, axis=1)
            best = np.minimum(best, np.abs(A - shifted).max(axis=2))
    real = best > TOL
    total = float(h * w)

    def box(mask):
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            return None
        return [round(float(xs.min()) / SCALE, 1), round(float(ys.min()) / SCALE, 1), round(float(xs.max() + 1) / SCALE, 1),
                round(float(ys.max() + 1) / SCALE, 1)]

    return {"diff": round(100 * diff.sum() / total, 2), "real": round(100 * real.sum() / total, 2),
            "mean": round(float(d.mean()), 2), "box": box(real), "box_all": box(diff), "A": A, "B": B, "mask": real}


def ink(rgb, dark: bool):
    lum = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255
    return lum if dark else 1 - lum


def overlay(A, B, dark: bool):
    """Legacy-only ink in red, Qt-only in cyan, shared ink in grey to black."""
    a, b = ink(A, dark), ink(B, dark)
    out = np.empty(A.shape, dtype=np.float32)
    out[..., 0] = 255 * (1 - b)
    out[..., 1] = 255 * (1 - a)
    out[..., 2] = 255 * (1 - a)
    return out


def to_qimage(rgb):
    from PySide6.QtGui import QImage

    arr = np.ascontiguousarray(np.clip(rgb, 0, 255).astype(np.uint8))
    h, w = arr.shape[:2]
    img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return img.copy()


def sheet(path: Path, title: str, lines: list[str], A, B, dark: bool, box) -> None:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

    h, w = A.shape[:2]
    gap, head = 24, 30 + 22 * (len(lines) + 1)
    W = 3 * w + 4 * gap
    H = head + 26 + h + gap
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(236, 236, 236))
    p = QPainter(img)
    f = QFont("Menlo")
    f.setPixelSize(18)
    p.setFont(f)
    p.setPen(QColor(20, 20, 20))
    p.drawText(gap, 30, title)
    f.setPixelSize(15)
    p.setFont(f)
    for i, line in enumerate(lines):
        p.drawText(gap, 30 + 22 * (i + 1), line)
    labels = ("legacy (AppKit)", "Qt", "overlay: red legacy only, cyan Qt only, box: differs beyond 1 px")
    for i, (label, pic) in enumerate(zip(labels, (A, B, overlay(A, B, dark)), strict=True)):
        x = gap + i * (w + gap)
        p.drawText(x, head + 16, label)
        p.drawImage(x, head + 26, to_qimage(pic))
        if i == 2 and box:
            p.setPen(QPen(QColor(220, 0, 200), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            x0, y0, x1, y1 = (v * SCALE for v in box)
            p.drawRect(QRectF(x + x0, head + 26 + y0, x1 - x0, y1 - y0))
            p.setPen(QColor(20, 20, 20))
    p.end()
    img.save(str(path))


# -- structure ----------------------------------------------------------------------------------


def _short(s: str, n: int = 48) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def items(elements: list[dict], side: str) -> list[dict]:
    """The comparable things on one side: controls, texts and filled shapes. On the legacy
    side a filled container holding exactly one text or icon becomes one item (a disc around a
    symbol, a pill around a title), which is how the Qt side draws them: one widget."""
    els = [dict(e) for e in elements]
    consumed: set[int] = set()
    if side == "legacy":
        for i, e in enumerate(els):
            if e["depth"] == 0 or not (e.get("fill") or e.get("border")):
                continue
            inner = []
            for j in range(i + 1, len(els)):
                if els[j]["depth"] <= e["depth"]:
                    break
                if els[j].get("text") or els[j].get("icon"):
                    inner.append(j)
            if len(inner) == 1 and not (els[inner[0]].get("fill")):
                c = els[inner[0]]
                for k in ("text", "icon", "font", "color", "tooltip", "enabled", "role", "placeholder"):
                    if k in c and k not in e:
                        e[k] = c[k]
                e["inner_frame"] = c["frame"]
                consumed.add(inner[0])
        # An image-only button laid over a filled disc as its sibling (the hands-free cancel and
        # finish strips: the button spans the whole click strip): the disc is the visible item.
        for i, e in enumerate(els):
            if i in consumed or e["depth"] == 0 or not e.get("fill") or e.get("icon") or e.get("text"):
                continue
            x, y, w, h = e["frame"]
            for j in range(i + 1, len(els)):
                o = els[j]
                if o["depth"] < e["depth"]:
                    break
                if o["depth"] != e["depth"] or j in consumed:
                    continue
                ox, oy, ow, oh = o["frame"]
                if o.get("icon") and not o.get("text") and ox <= x and oy <= y and ox + ow >= x + w and oy + oh >= y + h:
                    for k in ("icon", "color", "tooltip", "enabled", "role"):
                        if k in o and k not in e:
                            e[k] = o[k]
                    consumed.add(j)
                    break
    out = []
    for i, e in enumerate(els):
        if i in consumed or e["depth"] == 0 or e.get("painted"):
            continue
        if e.get("text"):
            key = "text:" + _short(e["text"], 60)
        elif e.get("icon"):
            key = "icon:" + e["icon"]
        elif e.get("role") in ("bar", "scrub", "mark", "wave", "sliver", "spinner", "field", "dropdown"):
            key = "role:" + e["role"]
        elif (e.get("fill") and e["fill"][-2:] != "00") or e.get("border"):
            key = "shape"
        else:
            continue
        e["key"] = key
        out.append(e)
    return out


def _iou(a, b) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def match(L: list[dict], Q: list[dict]):
    pairs, used = [], set()
    by_key: dict[str, list[int]] = {}
    for j, q in enumerate(Q):
        by_key.setdefault(q["key"], []).append(j)
    left = []
    for e in L:
        if e["key"] == "shape":
            left.append(e)
            continue
        cands = [j for j in by_key.get(e["key"], []) if j not in used]
        if not cands:
            left.append(e)
            continue
        j = min(cands, key=lambda j: abs(Q[j]["frame"][0] - e["frame"][0]) + abs(Q[j]["frame"][1] - e["frame"][1]))
        used.add(j)
        pairs.append((e, Q[j]))
    unmatched_l = []
    for e in left:
        cands = [(j, _iou(e["frame"], Q[j]["frame"])) for j in range(len(Q))
                 if j not in used and (Q[j]["key"] == "shape" or e["key"] == "shape")]
        cands = [c for c in cands if c[1] > 0.6]
        if cands:
            j = max(cands, key=lambda c: c[1])[0]
            used.add(j)
            pairs.append((e, Q[j]))
        else:
            unmatched_l.append(e)
    unmatched_q = [Q[j] for j in range(len(Q)) if j not in used]
    return pairs, unmatched_l, unmatched_q


# The same system fonts under the names each toolkit reports.
FAMILIES = {".AppleSystemUIFont": "SF Pro", ".SF NS": "SF Pro", "System Font": "SF Pro", ".AppleSystemUIFontSerif": "New York",
            ".New York": "New York", ".AppleSystemUIFontMonospaced": "SF Mono", ".SF NS Mono": "SF Mono"}


def _font(f) -> str:
    if not f:
        return ""
    family = FAMILIES.get(f["family"], f["family"])
    return f"{family} {f['size']:g} {f['weight']}" + (" italic" if f.get("italic") else "")


def compare_items(pairs) -> list[dict]:
    """One row per differing property of a matched element."""
    rows = []
    for a, b in pairs:
        name = a["key"] if a["key"] != "shape" else f"shape {a['cls']}/{b['cls']}"
        fa, fb = a["frame"], b["frame"]
        # A plain text's frame and any field's frame differ by definition between the toolkits;
        # the text is placed by its ink instead (ink_rows). Marked, kept for the table.
        definition = inked(a, b) or "field" in (a.get("role"), b.get("role"))
        for k, label in enumerate(("x", "y", "w", "h")):
            delta = fb[k] - fa[k]
            if abs(delta) >= 1.0:
                rows.append({"element": name, "prop": label + (" (frame)" if definition else ""), "legacy": f"{fa[k]:g}",
                             "qt": f"{fb[k]:g}", "delta": f"{delta:+g}"})
        for prop in ("font", "color", "fill", "border", "radius", "enabled"):
            va, vb = a.get(prop), b.get(prop)
            if prop == "font":
                if va and vb:
                    sa, sb = _font(va), _font(vb)
                    if sa != sb:
                        rows.append({"element": name, "prop": "font", "legacy": sa, "qt": sb, "delta": ""})
                continue
            if va is None or vb is None:
                continue
            if prop == "radius":
                if abs(float(va) - float(vb)) >= 0.5:
                    rows.append({"element": name, "prop": prop, "legacy": f"{va:g}", "qt": f"{vb:g}",
                                 "delta": f"{vb - va:+g}"})
                continue
            if str(va) != str(vb):
                delta = ""
                if prop in ("color", "fill", "border") and len(va) == 9 and len(vb) == 9:
                    ca = [int(va[i:i + 2], 16) for i in (1, 3, 5, 7)]
                    cb = [int(vb[i:i + 2], 16) for i in (1, 3, 5, 7)]
                    if max(abs(x - y) for x, y in zip(ca, cb, strict=True)) <= 2:
                        continue
                    delta = f"alpha {ca[3] / 255:.2f} vs {cb[3] / 255:.2f}" if ca[:3] == cb[:3] else ""
                rows.append({"element": name, "prop": prop, "legacy": str(va), "qt": str(vb), "delta": delta})
    return rows


# -- text ink -----------------------------------------------------------------------------------
#
# Where a text's ink lands, from the pictures: frames are not comparable for text (an AppKit
# field's frame includes its cell's 2 px insets, a Qt label may span its whole row, a painted
# run has its painting widget's frame), the ink is. The region is the legacy frame grown by
# INK_GROW_X and INK_GROW_Y, the same region on both pictures.

INK_GROW_X, INK_GROW_Y = 3.0, 2.0  # logical px around the legacy frame
INK_LEVEL = 0.5                      # ink: at least this share of the region's strongest contrast (half coverage,
                                     # so the box does not grow with heavier antialiasing)
INK_STEP = 1.0                       # an ink edge that moved this far (logical px) or more is a difference
INK_AMOUNT_TOL = 8.0                 # percent: more or less ink than this is lighter or heavier text


def inked(a: dict, b: dict) -> bool:
    """Is this matched element a plain text, placed by its ink? Not a filled pill or a dropdown,
    whose region holds a fill or a bezel besides the text (those keep their frames)."""
    return (a["key"].startswith("text:") and not (a.get("fill") or b.get("fill") or a.get("border") or b.get("border"))
            and "dropdown" not in (a.get("role"), b.get("role")))


def text_ink(arr, box) -> dict | None:
    """The ink box (logical, x0 y0 x1 y1) and amount of the text inside `box` of a matte picture."""
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = (round(v * SCALE) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    lum = arr[y0:y1, x0:x1] @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    d = np.abs(lum - np.median(lum))
    if d.max() < 40:
        return None
    mask = d >= INK_LEVEL * d.max()
    cols, rows = np.nonzero(mask.any(axis=0))[0], np.nonzero(mask.any(axis=1))[0]
    return {"box": [(x0 + cols[0]) / SCALE, (y0 + rows[0]) / SCALE, (x0 + cols[-1] + 1) / SCALE, (y0 + rows[-1] + 1) / SCALE],
            "amount": float(d.sum() / 255.0)}


def ink_rows(A, B, pairs) -> list[dict]:
    """One row per text whose ink edge moved INK_STEP or more, or whose ink amount differs by
    more than INK_AMOUNT_TOL percent."""
    rows = []
    for a, b in pairs:  # the region is the legacy one, on both pictures
        key = a["key"]
        if a.get("selected") or b.get("selected"):
            continue  # a selection's highlight is not the text (and its colour depends on the window being key)
        if a.get("role") == "field" and not key.startswith("text:"):
            if not a.get("placeholder"):
                continue
            key = "text:" + a["placeholder"]  # an empty field: its placeholder is what shows
        elif not inked(a, b):
            continue
        x, y, w, h = a["frame"]
        region = (x - INK_GROW_X, y - INK_GROW_Y, x + w + INK_GROW_X, y + h + INK_GROW_Y)
        ia, ib = text_ink(A, region), text_ink(B, region)
        if ia is None or ib is None:
            if ia is not None:
                rows.append({"element": key, "prop": "ink", "legacy": "drawn", "qt": "nothing near the legacy place",
                             "delta": ""})
            continue
        for k, label in enumerate(("ink left", "ink top", "ink right", "ink bottom")):
            delta = ib["box"][k] - ia["box"][k]
            if abs(delta) >= INK_STEP:
                rows.append({"element": key, "prop": label, "legacy": f"{ia['box'][k]:g}", "qt": f"{ib['box'][k]:g}",
                             "delta": f"{delta:+g}"})
        pct = 100 * (ib["amount"] - ia["amount"]) / ia["amount"] if ia["amount"] else 0.0
        if abs(pct) > INK_AMOUNT_TOL:
            rows.append({"element": key, "prop": "ink amount", "legacy": f"{ia['amount']:.0f}",
                         "qt": f"{ib['amount']:.0f}", "delta": f"{pct:+.0f}%"})
    return rows


# -- copy ---------------------------------------------------------------------------------------

SYMBOLISH = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)+$")


def strings(meta: dict) -> list[tuple[str, str]]:
    out = []
    if meta.get("title"):
        out.append(("window title", meta["title"]))
    for e in meta.get("elements", []):
        if e.get("text"):
            t = e["text"]
            if re.fullmatch(r"•+", t):
                continue
            out.append(("text", t))
        if e.get("placeholder"):
            out.append(("placeholder", e["placeholder"]))
        if e.get("tooltip"):
            out.append(("tooltip", e["tooltip"]))
        for it in e.get("items", []):
            out.append(("list item", it))
    return out


def copy_diff(a: list[tuple[str, str]], b: list[tuple[str, str]]) -> list[dict]:
    ca, cb = Counter(a), Counter(b)
    missing = list((ca - cb).elements())
    extra = list((cb - ca).elements())
    rows = []
    for kind, text in missing:
        best, score = None, 0.0
        for k2, t2 in extra:
            if k2 != kind:
                continue
            s = difflib.SequenceMatcher(None, text, t2).ratio()
            if s > score:
                best, score = (k2, t2), s
        if best is not None and score >= 0.6:
            extra.remove(best)
            rows.append({"what": "differs", "kind": kind, "legacy": text, "qt": best[1]})
        else:
            note = " (a symbol name used as a tooltip)" if kind == "tooltip" and SYMBOLISH.match(text) else ""
            rows.append({"what": "missing in Qt" + note, "kind": kind, "legacy": text, "qt": ""})
    for kind, text in extra:
        rows.append({"what": "extra in Qt", "kind": kind, "legacy": "", "qt": text})
    return rows


# -- menus --------------------------------------------------------------------------------------

KEYS = {" ": "Space", "\uf700": "↑", "\uf701": "↓", "\uf702": "←", "\uf703": "→"}


def menu_lines(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        if r.get("hidden"):
            continue
        if r.get("separator"):
            out.append("----")
            continue
        sc = r.get("shortcut", "")
        for k, v in KEYS.items():
            sc = sc.replace(k, v)
        flags = []
        if r.get("header"):
            flags.append("header")
        elif not r.get("enabled"):
            flags.append("disabled")
        if r.get("checked"):
            flags.append("checked")
        if r.get("submenu"):
            flags.append("submenu")
        if r.get("image"):
            flags.append("icon")
        out.append(r["title"] + (f"   [{sc}]" if sc else "") + (f"   ({', '.join(flags)})" if flags else ""))
    while out and out[-1] == "----":
        out.pop()
    return out


# -- the run ------------------------------------------------------------------------------------


def _esc(s) -> str:
    return html.escape(str(s))


def table_md(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return ""
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols) + " |")
    return "\n".join(out)


def table_html(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return ""
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_esc(r.get(c, ''))}</td>" for c in cols) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    ap = argparse.ArgumentParser(prog="python -m scripts.compare.compose")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    app = QApplication.instance() or QApplication(["lyssna-compare"])  # noqa: F841
    out = Path(args.out)
    raw_l, raw_q = out / "raw" / "legacy", out / "raw" / "qt"
    sheets = out / "sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    pairs_by_name = {p.name: p for p in catalogue.select(args.only)}
    results, failed, copy_sections, struct_sections = [], [], [], []
    for name, pair in pairs_by_name.items():
        for scheme in catalogue.SCHEMES:
            stem = f"{name}-{scheme}"
            try:
                ml = json.loads((raw_l / f"{stem}.json").read_text(encoding="utf-8"))
                mq = json.loads((raw_q / f"{stem}.json").read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                failed.append({"pair": stem, "side": "both", "why": f"no output: {exc.filename}"})
                continue
            bad = [(s, m["error"]) for s, m in (("legacy", ml), ("qt", mq)) if m.get("error")]
            if bad:
                for side, why in bad:
                    failed.append({"pair": stem, "side": side, "why": why})
                continue
            a = load(raw_l / f"{stem}.png", ml.get("background"))
            b = load(raw_q / f"{stem}.png")
            m = measure(a, b)
            L, Q = items(ml["elements"], "legacy"), items(mq["elements"], "qt")
            matched, only_l, only_q = match(L, Q)
            rows = compare_items(matched) + ink_rows(m["A"], m["B"], matched)
            size_l = "x".join(f"{v:g}" for v in ml["size"])
            size_q = "x".join(f"{v:g}" for v in mq["size"])
            r = {"pair": name, "scheme": scheme, "family": pair.family, "size_legacy": size_l, "size_qt": size_q,
                 "size_same": size_l == size_q, "diff": m["diff"], "real": m["real"], "mean": m["mean"], "box": m["box"],
                 "struct": len(rows), "matched": len(matched), "only_legacy": len(only_l), "only_qt": len(only_q),
                 "moment": pair.moment, "notes_legacy": ml.get("notes", []), "notes_qt": mq.get("notes", []),
                 "method": ml.get("method", "")}
            lines = [f"size legacy {size_l}, Qt {size_q}" + ("" if r["size_same"] else "   SIZE DIFFERS")
                     + f"   differing pixels {m['diff']}%   beyond 1 px {m['real']}%   mean {m['mean']}",
                     f"box beyond 1 px (logical x0,y0,x1,y1): {m['box']}   structure: {len(matched)} matched, "
                     f"{len(rows)} property differences, {len(only_l)} legacy only, {len(only_q)} Qt only",
                     f"moment: {_short(pair.moment, 150)}"]
            sheet(sheets / f"{stem}.png", f"{name}  ({scheme})", lines, m["A"], m["B"], scheme == "dark", m["box"])
            r["rows"] = rows
            r["only_l"] = [f"{e['key']} @ {e['frame']}" for e in only_l]
            r["only_q"] = [f"{e['key']} @ {e['frame']}" for e in only_q]
            cd = copy_diff(strings(ml), strings(mq))
            r["copy"] = cd
            cut_l = {e["text"] for e in ml["elements"] if e.get("cut") and e.get("text")}
            texts_q = {e["text"] for e in mq["elements"] if e.get("text")}
            # Only whole labels count: a paint may also elide fragments (a history row folding a
            # long dictation to its last shown line), which is not a label cut short.
            cut_q = {t: v for t, v in mq.get("elided", {}).items() if t in texts_q}
            r["cut"] = {"qt_only": {t: v for t, v in cut_q.items() if t not in cut_l},
                        "legacy_only": sorted(t for t in cut_l if t not in cut_q)}
            r["box_all"] = m["box_all"]
            review.save_pictures(out, stem, m["A"], m["B"], ml["size"], mq["size"])
            results.append(r)
            if scheme == "light" and cd:
                copy_sections.append((name, cd))
            struct_sections.append(r)

    # Worst first by the share of pixels that differ beyond a one-pixel shift. A size mismatch is
    # already in that share (the smaller picture is padded with the matte), so it adds nothing.
    results.sort(key=lambda r: (-r["real"], -r["diff"]))

    def read(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
    ink_l, ink_q, screen = read(raw_l / "ink.json"), read(raw_q / "ink.json"), read(raw_q / "screen.json")
    bias = review.text_bias(ink_l, ink_q)
    for r in results:
        r["verdict"], r["reasons"] = review.verdict(r, bias)
    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")

    # Menus
    menu_md, menu_html = [], []
    try:
        jl = json.loads((raw_l / "menus.json").read_text(encoding="utf-8"))
        jq = json.loads((raw_q / "menus.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        jl, jq = {}, {}
    for name in catalogue.MENUS:
        if name not in jl and name not in jq:
            continue
        la, qa = menu_lines(jl.get(name, [])), menu_lines(jq.get(name, []))
        diff = list(difflib.unified_diff(la, qa, "legacy", "qt", lineterm="", n=50))
        status = "same" if la == qa else "differs"
        menu_md.append(f"### {name}: {status}\n\n```diff\n" + ("\n".join(diff) if diff else "\n".join(la)) + "\n```\n")
        menu_html.append(f"<h3>{_esc(name)}: {status}</h3><pre>{_esc(chr(10).join(diff) if diff else chr(10).join(la))}</pre>")
    (out / "menus.md").write_text("# Menus: structure, legacy against Qt\n\nVisible rows only. Each row: title, "
                                  "[shortcut], (header, disabled, checked, submenu, icon). A unified diff where they "
                                  "differ.\n\n" + "\n".join(menu_md), encoding="utf-8")

    # Copy audit
    copy_md = ["# Copy audit: every user-facing string, legacy against Qt\n",
               "Per screen state (light scheme; dark has the same strings). Kinds: window title, visible text, "
               "placeholder, tooltip, list item. Strings equal on both sides are not listed. Masked key fields are "
               "left out.\n"]
    for name, cd in copy_sections:
        copy_md.append(f"## {name}\n\n" + table_md(cd, ["what", "kind", "legacy", "qt"]) + "\n")
    (out / "copy.md").write_text("\n".join(copy_md), encoding="utf-8")

    # Structure
    st_md = ["# Structure: matched elements, legacy against Qt\n",
             "Elements matched by icon, text or role (shapes by overlap). Frames are logical pixels, top-left "
             "origin, in the picture's own coordinates. Only differing properties are listed; delta is Qt minus "
             "legacy.\n"]
    for r in struct_sections:
        st_md.append(f"## {r['pair']} ({r['scheme']})\n\n{r['matched']} matched.\n")
        if r["rows"]:
            st_md.append(table_md(r["rows"], ["element", "prop", "legacy", "qt", "delta"]) + "\n")
        if r["only_l"]:
            st_md.append("Legacy only: " + "; ".join(r["only_l"]) + "\n")
        if r["only_q"]:
            st_md.append("Qt only: " + "; ".join(r["only_q"]) + "\n")
    (out / "structure.md").write_text("\n".join(st_md), encoding="utf-8")

    icon_rows = icons.compose(out, args.only)

    # report.md
    rep = ["# Side-by-side comparison: report\n",
           "## Read this first: what the harness still cannot measure reliably\n",
           "Nothing in this list is a product difference; do not read the numbers it names as one.\n"]
    rep += [f"- {u}" for u in UNRELIABLE]
    rep.append("\n## Text weight check\n")
    if ink_l.get("cases") and ink_q.get("cases"):
        rep.append(f"\"{catalogue.INK_TEXT}\" in the interface font, {catalogue.INK_SIZE:g} {catalogue.INK_WEIGHT}, at 2x, "
                   f"AppKit against Qt on the {ink_q['platform']} platform. Round 1A measured 1535 on Qt's offscreen platform "
                   "(light on dark), about 10 percent light; the Qt side now draws on the real platform.\n")
        for case, a in ink_l["cases"].items():
            b = ink_q["cases"].get(case)
            if b is not None:
                rep.append(f"- {case}: AppKit {a}, Qt {b} ({100 * (b - a) / a:+.1f} percent)")
        rep.append("\nCore Text thickens light glyphs more than dark ones. AppKit chooses by the text's colour; Qt "
                   "chooses by the application's colour scheme (it rasterises dark on light in a light app). The Qt "
                   "side asks Qt for each picture's scheme, as a Mac in that appearance would have it, so the two "
                   "cases above are the app's real weights. Text drawn against its scheme (light text on a dark fill "
                   "in light mode, such as a primary pill's title) keeps the scheme's thickening in Qt.\n")
    tink = [r for r in struct_sections if r["pair"] == "toast.text"]
    for r in tink:
        rep.append(f"- toast.text ({r['scheme']}): " + (", ".join(f"{x['prop']} {x['legacy']} / {x['qt']} ({x['delta']})"
                                                         for x in r["rows"] if x["prop"].startswith("ink"))
                                                         or "the text's ink box and amount agree (within 1 px and "
                                                            f"{INK_AMOUNT_TOL:g} percent)"))
    rep.append("\n## Nothing on screen\n")
    if screen:
        rep.append(f"Qt side on the {screen.get('platform')} platform, device pixel ratio {screen.get('device_pixel_ratio')}; "
                   f"windows Qt was asked to show and kept off the screen: {screen.get('shows_kept_off_screen') or 'none'}; "
                   f"windows the window server listed on screen: {screen.get('on_screen') or 'none'}; app found active: "
                   f"{screen.get('app_active') or 'never'}.\n")
    rep += [f"## Ranking\n\n{len(results)} pictures compared ({len({r['pair'] for r in results})} pairs, light and dark). "
            f"Ranked worst first by the share of pixels that differ beyond a 1 px shift (a size mismatch is already part of "
            f"that share). Tolerance {TOL}/255 per channel. Sheets in sheets/, structure in structure.md, copy in copy.md, "
            "menus in menus.md, motion in motion.md, icons in icons.md, the owner's page in review.html.\n",
            table_md([{"rank": i + 1, "pair": r["pair"], "scheme": r["scheme"], "size legacy": r["size_legacy"],
                       "size Qt": r["size_qt"], "diff %": r["diff"], "beyond 1 px %": r["real"], "box": r["box"],
                       "struct diffs": r["struct"], "verdict": r["verdict"]} for i, r in enumerate(results)],
                     ["rank", "pair", "scheme", "size legacy", "size Qt", "diff %", "beyond 1 px %", "box", "struct diffs",
                      "verdict"]),
            "\n## Not rendered on one side\n"]
    rep.append(table_md(failed, ["pair", "side", "why"]) if failed else "None: every pair rendered on both sides.\n")
    if icon_rows:
        ok = sum(1 for r in icon_rows if r.get("ok"))
        rep.append(f"\n## Icons\n\n{ok} of {len(icon_rows)} within tolerance; see icons.md.\n")
    rep.append("\n## What each side could not draw faithfully\n")
    notes = Counter()
    for r in results:
        for n in r["notes_legacy"]:
            notes[f"legacy: {n}"] += 1
        for n in r["notes_qt"]:
            notes[f"Qt: {n}"] += 1
    rep += [f"- {n} ({c} pictures)" for n, c in notes.most_common()]
    (out / "report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    review.write(out, results, failed, icon_rows, jl, jq, args.only, bias)

    # index.html
    css = ("body{font:13px -apple-system,sans-serif;margin:20px;background:#fafafa}table{border-collapse:collapse;"
           "margin:8px 0}td,th{border:1px solid #ccc;padding:2px 6px;text-align:left;vertical-align:top}"
           "img{max-width:100%;border:1px solid #ddd}.bad{color:#b00}section{margin:28px 0}pre{background:#eee;"
           "padding:8px;overflow:auto}")
    parts = [f"<!doctype html><meta charset=utf-8><title>Lyssna: legacy against Qt</title><style>{css}</style>",
             "<h1>Lyssna: today's Mac interface against the Qt interface</h1>",
             f"<p>{len(results)} pictures, worst first. Diff: pixels differing by more than {TOL}/255. Beyond 1 px: "
             "still differing when Qt may shift one device pixel. Links: <a href=report.md>report.md</a>, "
             "<a href=findings.md>findings.md</a>, <a href=structure.md>structure.md</a>, <a href=copy.md>copy.md</a>, "
             "<a href=menus.md>menus.md</a>, <a href=motion.md>motion.md</a>.</p><table><tr><th>#</th><th>pair</th>"
             "<th>scheme</th><th>size legacy</th><th>size Qt</th><th>diff %</th><th>beyond 1 px %</th><th>box</th>"
             "<th>struct</th></tr>"]
    for i, r in enumerate(results):
        anchor = f"{r['pair']}-{r['scheme']}"
        cls = "" if r["size_same"] else " class=bad"
        parts.append(f"<tr><td>{i + 1}</td><td><a href='#{_esc(anchor)}'>{_esc(r['pair'])}</a></td><td>{r['scheme']}</td>"
                     f"<td{cls}>{r['size_legacy']}</td><td{cls}>{r['size_qt']}</td><td>{r['diff']}</td><td>{r['real']}</td>"
                     f"<td>{_esc(r['box'])}</td><td>{r['struct']}</td></tr>")
    parts.append("</table>")
    if failed:
        parts.append("<h2>Not rendered</h2>" + table_html(failed, ["pair", "side", "why"]))
    for r in results:
        anchor = f"{r['pair']}-{r['scheme']}"
        parts.append(f"<section id='{_esc(anchor)}'><h2>{_esc(r['pair'])} ({r['scheme']})</h2>"
                     f"<p>moment: {_esc(r['moment'])}<br>legacy notes: {_esc('; '.join(r['notes_legacy']))}<br>"
                     f"Qt notes: {_esc('; '.join(r['notes_qt']))}</p><img loading=lazy src='sheets/{_esc(anchor)}.png'>"
                     + table_html(r["rows"], ["element", "prop", "legacy", "qt", "delta"])
                     + (f"<p>Legacy only: {_esc('; '.join(r['only_l']))}</p>" if r["only_l"] else "")
                     + (f"<p>Qt only: {_esc('; '.join(r['only_q']))}</p>" if r["only_q"] else "") + "</section>")
    parts.append("<h2>Menus</h2>" + "".join(menu_html))
    (out / "index.html").write_text("".join(parts), encoding="utf-8")
    print(f"compose: {len(results)} sheets, {len(failed)} failures; index at {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
