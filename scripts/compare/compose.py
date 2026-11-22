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

from scripts.compare import catalogue  # noqa: E402

TOL = 32          # a channel difference above this counts (of 255)
MATTE = (128, 128, 128)
SCALE = 2.0


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


def _font(f) -> str:
    if not f:
        return ""
    return f"{f['family']} {f['size']:g} {f['weight']}" + (" italic" if f.get("italic") else "")


def compare_items(pairs) -> list[dict]:
    """One row per differing property of a matched element."""
    rows = []
    for a, b in pairs:
        name = a["key"] if a["key"] != "shape" else f"shape {a['cls']}/{b['cls']}"
        fa, fb = a["frame"], b["frame"]
        for k, label in enumerate(("x", "y", "w", "h")):
            delta = fb[k] - fa[k]
            if abs(delta) >= 1.0:
                rows.append({"element": name, "prop": label, "legacy": f"{fa[k]:g}", "qt": f"{fb[k]:g}",
                             "delta": f"{delta:+g}"})
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
            rows = compare_items(matched)
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
            results.append(r)
            cd = copy_diff(strings(ml), strings(mq))
            if scheme == "light" and cd:
                copy_sections.append((name, cd))
            struct_sections.append(r)

    results.sort(key=lambda r: (-(r["real"] + (0.5 if not r["size_same"] else 0.0)), -r["diff"]))
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

    # report.md
    rep = ["# Side-by-side comparison: report\n",
           f"{len(results)} pictures compared ({len({r['pair'] for r in results})} pairs, light and dark). "
           f"Ranked worst first by the share of pixels that differ beyond a 1 px shift (a size mismatch adds half a "
           f"point). Tolerance {TOL}/255 per channel. Sheets in sheets/, structure in structure.md, copy in "
           "copy.md, menus in menus.md, motion in motion.md.\n",
           table_md([{"rank": i + 1, "pair": r["pair"], "scheme": r["scheme"], "size legacy": r["size_legacy"],
                      "size Qt": r["size_qt"], "diff %": r["diff"], "beyond 1 px %": r["real"], "box": r["box"],
                      "struct diffs": r["struct"]} for i, r in enumerate(results)],
                    ["rank", "pair", "scheme", "size legacy", "size Qt", "diff %", "beyond 1 px %", "box",
                     "struct diffs"]),
           "\n## Not rendered on one side\n"]
    rep.append(table_md(failed, ["pair", "side", "why"]) if failed else "None: every pair rendered on both sides.\n")
    rep.append("\n## What each side could not draw faithfully\n")
    notes = Counter()
    for r in results:
        for n in r["notes_legacy"]:
            notes[f"legacy: {n}"] += 1
        for n in r["notes_qt"]:
            notes[f"Qt: {n}"] += 1
    rep += [f"- {n} ({c} pictures)" for n, c in notes.most_common()]
    (out / "report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")

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
