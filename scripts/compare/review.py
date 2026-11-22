"""review.html: the comparison for the owner, in plain words. One self-contained page (the
pictures sit beside it in review/, icons/ and motion/raw/), one section per screen family, each
screen as today's Mac app beside the new interface, light and dark, with a one-line verdict.
The technical tables (report.md, structure.md) stay for us.

The verdict thresholds are the constants below; the page states them once at the bottom."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import numpy as np

# -- the verdict thresholds ------------------------------------------------------------------------

TOL = 32                # of 255: a pixel whose colour differs by more than this counts as different
IDENTICAL_SHARE = 0.05  # percent of pixels that may differ (by more than TOL) for "Identical"
MATCH_SHARE = 0.10      # percent of pixels that may still differ after a one-pixel shift for "within one pixel"
STEP = 1.0              # pixels: a size or position difference this large or larger is named
INK_SHARE = 8.0         # percent: a text with this much more or less ink is called heavier or lighter
MOTION_TIMING = 0.10    # share of the movement: frames further apart than this move differently
MOTION_LOOK = 0.5       # percent of pixels: a frame differing more than this (after a one-pixel shift) looks different
SCRUB_STEP = 1.0        # pixels: the scrub bar's fill or knob further apart than this at any frame
WRAP_STEP = 8.0         # pixels: a text whose longest line grows or shrinks this much breaks at another word

MAC, NEW = "Today's Mac app", "The new interface"

FAMILIES = [  # (family, title, what it is)
    ("player", "Player", "The reading player as a card, while text is read aloud."),
    ("docked", "Docked player", "The player tucked into the edge of the screen: the thin bar, the small pill and the drop zones."),
    ("dictation", "Dictation pill", "The resting squiggle, its menu, and the pill while you dictate."),
    ("button", "Play button", "The round button that appears next to selected text."),
    ("toast", "Toasts and tips", "Short notices at the bottom of the screen and the hints beside the pill."),
    ("setup", "Setup", "The setup window, step by step."),
    ("history", "History", "The history window with dictations and the dictionary."),
    ("whats-new", "What's New", "The window shown after an update."),
    ("logo", "Logo", "The Lyssna mark at the top of the windows."),
    ("menu", "Menus", "The menu bar menu and the small menus, compared row by row."),
    ("icons", "Icons", "Every icon at the size it is used, enlarged."),
    ("animations", "Animations", "Each movement as eight moments in time, left to right."),
]


def family_of(pair: str) -> str:
    if pair.startswith("player.card"):
        return "player"
    if pair.startswith(("player.sliver", "player.pill", "player.drop-zone")):
        return "docked"
    if pair.startswith(("player.idle", "player.dictate", "wave.")):
        return "dictation"
    if pair.startswith("selection-button"):
        return "button"
    if pair.startswith(("toast.", "tip.")):
        return "toast"
    if pair.startswith("mark."):
        return "logo"
    for prefix, fam in (("setup.", "setup"), ("history.", "history"), ("whats-new", "whats-new")):
        if pair.startswith(prefix):
            return fam
    return "player"


TITLES = {
    "player.card": "Reading", "player.card-paused": "Paused", "player.card-loading": "Getting ready to read",
    "player.card-long": "A long text, at 1.75 times speed", "player.card-scrubbing": "Dragging along the progress bar",
    "player.sliver": "Docked on the right, reading", "player.sliver-paused": "Docked on the right, paused",
    "player.sliver-bottom": "Docked at the bottom, reading", "player.sliver-left": "Docked on the left, reading",
    "player.pill": "The small pill on the right edge", "player.pill-flat": "The small pill on the bottom edge",
    "player.drop-zone": "Where the player can be docked (side)", "player.drop-zone-flat": "Where the player can be docked (bottom)",
    "player.idle": "Resting squiggle at the bottom", "player.idle-side": "Resting squiggle on the side",
    "player.idle-menu": "Its menu, at the bottom", "player.idle-menu-side": "Its menu, on the side",
    "player.dictate": "Listening (side)", "player.dictate-flat": "Listening (bottom)",
    "player.dictate-hands-free": "Hands-free, with cancel and finish (side)",
    "player.dictate-hands-free-flat": "Hands-free, with cancel and finish (bottom)",
    "player.dictate-working": "Turning speech into text (side)", "player.dictate-working-flat": "Turning speech into text (bottom)",
    "wave.recording": "The wave while listening (upright)", "wave.recording-flat": "The wave while listening (lying down)",
    "wave.working": "The wave while working (upright)", "wave.working-flat": "The wave while working (lying down)",
    "selection-button": "At rest", "selection-button.hover": "With the pointer over it",
    "toast.text": "A short notice", "toast.action": "A notice with an Undo button", "toast.long": "A long notice, cut short",
    "toast.download": "Download progress", "tip.left": "Hint on the left", "tip.right": "Hint on the right",
    "tip.bottom": "Hint below", "mark.28": "The mark, 28 tall", "mark.30": "The mark, 30 tall",
    "history.dictations": "Dictations", "history.expanded": "A long dictation opened, and an original shown",
    "history.learned": "After a fix taught a new word", "history.hover": "Pointer over a dictation",
    "history.fix": "Fixing a word", "history.search": "Searching", "history.search-none": "A search with no results",
    "history.empty": "Nothing dictated yet", "history.dictionary": "The dictionary", "history.dictionary-empty": "An empty dictionary",
    "history.dictionary-too-long": "A word that is too long", "history.wide": "A wide window",
    "whats-new": "The notes", "whats-new.support": "The notes with the support link",
}


def title(pair: str) -> str:
    if pair in TITLES:
        return TITLES[pair]
    if pair.startswith("setup."):
        words = pair.split(".", 1)[1].replace(".", ": ").replace("-", " ")
        return words[0].upper() + words[1:]
    return pair


def _esc(s) -> str:
    return html.escape(str(s))


def _px(v: float) -> str:
    v = abs(v)
    n = f"{round(v * 2) / 2:g}"
    return f"{n} pixel" + ("" if n == "1" else "s")


# -- pictures --------------------------------------------------------------------------------------


def save_pictures(out: Path, stem: str, A, B, size_l, size_q, scale: float = 2.0) -> None:
    """Each side's picture on the grey backdrop, cropped to its own size, as review/STEM-mac.png
    and review/STEM-new.png."""
    from PySide6.QtGui import QImage

    folder = out / "review"
    folder.mkdir(parents=True, exist_ok=True)
    for arr, size, side in ((A, size_l, "mac"), (B, size_q, "new")):
        w, h = round(size[0] * scale), round(size[1] * scale)
        rgb = np.ascontiguousarray(np.clip(arr[:h, :w], 0, 255).astype(np.uint8))
        img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], 3 * rgb.shape[1], QImage.Format.Format_RGB888).copy()
        img.save(str(folder / f"{stem}-{side}.png"))


# -- verdicts --------------------------------------------------------------------------------------

ICON_WORDS = {"xmark": "close icon", "checkmark": "tick icon", "play.fill": "play icon", "pause.fill": "pause icon",
              "backward.end.fill": "previous icon", "forward.end.fill": "next icon", "mic.fill": "microphone icon",
              "clock.arrow.circlepath": "history icon", "doc.on.doc": "copy icon", "ellipsis": "more icon",
              "magnifyingglass": "search icon", "heart.fill": "heart icon", "lock": "lock icon",
              "arrow.up.right": "arrow icon", "exclamationmark.triangle": "warning icon"}
ROLE_WORDS = {"bar": "the progress bar", "scrub": "the progress bar", "mark": "the logo mark", "wave": "the wave",
              "sliver": "the docked bar", "spinner": "the spinner", "field": "the text field", "dropdown": "the drop-down"}


def thing(key: str) -> str:
    if key.startswith("text:"):
        t = key[5:]
        return f"“{t if len(t) <= 40 else t[:39] + '…'}”"
    if key.startswith("icon:"):
        name = key[5:]
        return "the " + ICON_WORDS.get(name, name.replace(".", " ") + " icon")
    if key.startswith("role:"):
        return ROLE_WORDS.get(key[5:], "the " + key[5:])
    return "a shape"


def _num(s) -> float | None:
    try:
        return float(str(s).rstrip("%"))
    except ValueError:
        return None


def _font_parts(s: str) -> tuple[str, float, str] | None:
    m = re.match(r"(.+) ([\d.]+) (\w+)", s or "")
    return (m.group(1), float(m.group(2)), m.group(3)) if m else None


def element_phrases(rows: list[dict], bias: float = 0.0) -> list[tuple[float, str]]:
    """(weight, phrase) per element that differs, from the structure rows. Text is placed by its
    ink (the frame rows of a text are definitions that differ between the toolkits, not what
    shows); other elements by their frames."""
    by: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        key = r["element"]
        if key not in by:
            by[key] = {}
            order.append(key)
        by[key][r["prop"]] = r
    out = []
    for key in order:
        props = by[key]
        parts, weight = [], 0.0

        def d(p: str, props: dict = props) -> float:
            v = _num(props[p]["delta"]) if p in props else None
            return v or 0.0
        # Frame rows marked "(frame)" are definitions, not differences (compose.compare_items):
        # plain texts and fields are placed by their ink rows; the rest by their frames.
        if any(p.startswith("ink ") and p != "ink amount" for p in props):
            dl, dr, dt, db = d("ink left"), d("ink right"), d("ink top"), d("ink bottom")
            shift_x, grow_x = (dl + dr) / 2, dr - dl
            shift_y, grow_y = (dt + db) / 2, db - dt
        else:
            shift_x, shift_y, grow_x, grow_y = d("x"), d("y"), d("w"), d("h")
        inked = any(p.startswith("ink ") and p != "ink amount" for p in props)
        if inked and abs(grow_x) >= WRAP_STEP and abs(d("ink left")) < STEP:
            # The left edge stays and the ink grows or shrinks by a word: the lines break elsewhere.
            parts.append(f"breaks its lines at a different word (its longest line is {_px(grow_x)} "
                         f"{'longer' if grow_x > 0 else 'shorter'})")
            weight = max(weight, abs(grow_x))
            shift_x = grow_x = 0.0
        if abs(shift_x) >= STEP:
            parts.append(f"sits {_px(shift_x)} further {'right' if shift_x > 0 else 'left'}")
        if abs(shift_y) >= STEP:
            parts.append(f"sits {_px(shift_y)} {'lower' if shift_y > 0 else 'higher'}")
        if abs(grow_x) >= STEP:
            parts.append(f"is {_px(grow_x)} {'wider' if grow_x > 0 else 'narrower'}")
        if abs(grow_y) >= STEP:
            parts.append(f"is {_px(grow_y)} {'taller' if grow_y > 0 else 'shorter'}")
        weight = max(weight, abs(shift_x), abs(shift_y), abs(grow_x), abs(grow_y))
        if "ink" in props:
            parts.append("is missing from where today's Mac app draws it")
            weight = max(weight, 10.0)
        if "ink amount" in props:
            pct = _num(props["ink amount"]["delta"]) or 0.0
            if abs(bias) >= INK_SHARE and pct * bias > 0 and abs(pct - bias) < INK_SHARE:
                pct = 0.0  # the app-wide text weight difference (text_bias), said once per screen instead
            if abs(pct) >= INK_SHARE:
                parts.append(f"looks {'heavier' if pct > 0 else 'lighter'} ({abs(pct):.0f} percent "
                             f"{'more' if pct > 0 else 'less'} ink)")
                weight = max(weight, abs(pct) / 10)
        if "font" in props:
            a, b = _font_parts(props["font"]["legacy"]), _font_parts(props["font"]["qt"])
            if a and b:
                if a[0] != b[0]:
                    parts.append(f"uses a different typeface ({b[0]} instead of {a[0]})")
                if abs(a[1] - b[1]) >= 0.5:
                    parts.append(f"is set {abs(b[1] - a[1]):g} point{'s' if abs(b[1] - a[1]) != 1 else ''} "
                                 f"{'larger' if b[1] > a[1] else 'smaller'} ({b[1]:g} instead of {a[1]:g})")
                if a[2] != b[2]:
                    parts.append(f"is {b[2]} where today's Mac app uses {a[2]}")
                weight = max(weight, 2.0)
        for prop, words in (("color", "a different colour"), ("fill", "a different background colour"),
                            ("border", "a different outline colour")):
            if prop in props:
                parts.append(f"has {words}")
                weight = max(weight, 1.5)
        if "radius" in props:
            parts.append("has rounder corners" if (_num(props["radius"]["delta"]) or 0) > 0 else "has less rounded corners")
            weight = max(weight, 1.0)
        if "enabled" in props:
            parts.append("can be clicked on one side only")
            weight = max(weight, 5.0)
        if parts:
            name = thing(key)
            out.append((weight, name[0].upper() + name[1:] + " " + _join(parts)))
    return out


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _area(box, size) -> str:
    if not box:
        return ""
    w, h = (float(v) for v in size.split("x"))
    x0, y0, x1, y1 = box
    if (x1 - x0) * (y1 - y0) > 0.5 * w * h:
        return "across most of it"
    cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
    v = "top" if cy < 1 / 3 else "bottom" if cy > 2 / 3 else "middle"
    hz = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "centre"
    return f"near the {v} {hz}" if (v, hz) != ("middle", "centre") else "in the middle"


def text_bias(ink_l: dict, ink_q: dict) -> dict[str, float]:
    """Per scheme, how much more ink the new interface's text has than today's Mac app's, from
    the text weight check: light mode is dark text on light, dark mode light text on dark."""
    out = {}
    for scheme, case in (("light", "dark on light"), ("dark", "light on dark")):
        a, b = (ink_l.get("cases") or {}).get(case), (ink_q.get("cases") or {}).get(case)
        if a and b:
            out[scheme] = 100 * (b - a) / a
    return out


def verdict(r: dict, bias: dict[str, float] | None = None) -> tuple[str, list[str]]:
    """The one-line verdict for one picture (a pair in one scheme) and its reasons. `bias` is
    text_bias(): an app-wide text weight difference is named once, not for every text."""
    reasons: list[tuple[float, str]] = []
    b = (bias or {}).get(r["scheme"], 0.0)
    if abs(b) >= INK_SHARE and any(x["prop"] == "ink amount" and (_num(x["delta"]) or 0) * b > 0
                                   for x in r.get("rows", [])):
        reasons.append((1.9, f"its text is drawn about {abs(b):.0f} percent {'heavier' if b > 0 else 'lighter'}, as "
                             f"everywhere in {r['scheme']} mode"))
    if not r["size_same"]:
        (wl, hl), (wq, hq) = ((float(v) for v in r[k].split("x")) for k in ("size_legacy", "size_qt"))
        bits = []
        if abs(wq - wl) >= STEP:
            bits.append(f"{_px(wq - wl)} {'wider' if wq > wl else 'narrower'}")
        if abs(hq - hl) >= STEP:
            bits.append(f"{_px(hq - hl)} {'taller' if hq > hl else 'shorter'}")
        if bits:
            reasons.append((20.0, f"The new one is {_join(bits)} ({r['size_qt'].replace('x', ' by ')} against "
                                  f"{r['size_legacy'].replace('x', ' by ')})"))
    reasons += element_phrases(r.get("rows", []), b)
    cut = r.get("cut") or {}
    for text, shown in cut.get("qt_only", {}).items():
        reasons.append((40.0, f"“{text}” is cut short in the new one (it shows “{shown}”); today's Mac app shows it whole"))
    for text in cut.get("legacy_only", []):
        reasons.append((40.0, f"“{text}” is cut short in today's Mac app but shown whole in the new one"))
    for c in r.get("copy", []):
        if "symbol name" in c["what"]:
            continue  # AppKit's default tooltip for an image button: its symbol's name, never meant as copy
        kind = "" if c["kind"] == "text" else f" ({c['kind']})"
        if c["what"] == "differs":
            reasons.append((30.0, f"The wording differs{kind}: “{c['legacy']}” in today's Mac app, “{c['qt']}” in the new one"))
        elif c["what"].startswith("missing"):
            reasons.append((30.0, f"Today's Mac app shows “{c['legacy']}”{kind}; the new one does not"))
        else:
            reasons.append((30.0, f"The new one shows “{c['qt']}”{kind}, today's Mac app does not"))
    reasons.sort(key=lambda x: -x[0])
    texts = [t for _, t in reasons]
    if texts:
        texts[0] = texts[0][0].upper() + texts[0][1:]
    if not texts:
        if r["real"] <= 0.0 and r["diff"] <= IDENTICAL_SHARE:
            return "Identical", []
        if r["real"] <= MATCH_SHARE:
            return "Matches within one pixel", []
        line = (f"Small drawing differences: {r['real']:g} percent of the picture still differs after a one-pixel shift, "
                f"{_area(r['box'], r['size_legacy'])}")
        return line, [line]
    return "; ".join(texts[:3]) + (f"; and {len(texts) - 3} more" if len(texts) > 3 else ""), texts


# -- animations ------------------------------------------------------------------------------------


LOOPS = ("sliver-drift",)  # a drift with no start and end: judged by its moments' pictures, not by progress


def motion_verdicts(out: Path, only: str | None) -> list[dict]:
    from . import compose, motion

    mdir = out / "motion"
    rows = []
    try:
        jl = json.loads((mdir / "legacy_scrub.json").read_text(encoding="utf-8"))
        jq = json.loads((mdir / "qt_scrub.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        jl, jq = {}, {}
    for name in motion._select(only):
        raw_l, raw_q = mdir / "raw" / "legacy", mdir / "raw" / "qt"
        if not all((raw_l / f"{name}-{i}.png").exists() and (raw_q / f"{name}-{i}.png").exists()
                   for i in range(motion.FRAMES)):
            rows.append({"name": name, "verdict": "Could not be drawn on both sides", "frames": 0})
            continue
        pl, pq = motion.progress(name, raw_l), motion.progress(name, raw_q)
        looks = []
        for i in range(motion.FRAMES):
            m = compose.measure(compose.load(raw_l / f"{name}-{i}.png"), compose.load(raw_q / f"{name}-{i}.png"))
            looks.append(m["real"])
        times = motion._times(name)
        gap = [abs(a - b) for a, b in zip(pl, pq, strict=True)] if pl and pq else []
        reasons = []
        if name in jl and name in jq:
            worst = 0.0
            for a, b in zip(jl[name], jq[name], strict=True):
                for k in ("fill_w", "fill_h", "knob_x"):
                    if a.get(k) is not None and b.get(k) is not None:
                        worst = max(worst, abs(a[k] - b[k]))
                if a.get("knob_alpha") is not None and b.get("knob_alpha") is not None:
                    worst = max(worst, 0.0 if abs(a["knob_alpha"] - b["knob_alpha"]) < 0.05 else SCRUB_STEP * 2)
            if worst > SCRUB_STEP:
                reasons.append(f"the bar and knob are up to {_px(worst)} apart during the movement")
            timing_line = f"the bar and knob stay within {_px(worst)} of today's Mac app at every moment"
        elif name in LOOPS:
            timing_line = "every moment looks the same"
        elif gap and max(gap) > MOTION_TIMING:
            i = int(np.argmax(gap))
            ahead = pq[i] > pl[i]
            reasons.append(f"the new one is {'ahead' if ahead else 'behind'} by up to {round(100 * gap[i])} percent of the "
                           f"movement (at {times[i]:g} seconds)")
            timing_line = ""
        else:
            timing_line = "the timing matches"
        worst_look = max(looks)
        if worst_look > MOTION_LOOK:
            i = int(np.argmax(looks))
            reasons.append(f"at {times[i]:g} seconds {worst_look:g} percent of the picture differs")
        if reasons:
            v = "Differs: " + "; ".join(reasons)
        else:
            v = "Moves the same way: " + timing_line
        rows.append({"name": name, "verdict": v, "frames": motion.FRAMES, "times": times, "ok": not reasons,
                     "what": motion.STRIPS[name][1]})
    return rows


MOTION_TITLES = {"pill-grow": "The docked bar grows into the pill", "menu-grow": "The squiggle grows into its menu",
                 "hands-free-grow": "Dictation turns hands-free: the pill grows, cancel and finish fade in",
                 "slide-out": "The squiggle slides out of the edge", "ripple": "The squiggle's ripple",
                 "bloom": "The glow when a new sentence starts", "working-breathe": "The wave breathing while it works",
                 "sliver-drift": "The colour drifting in the docked bar (8 seconds)",
                 "hover-glow": "A button lighting up under the pointer",
                 "scrub-progress": "The progress bar moving on while reading",
                 "scrub-progress-hover": "The progress bar moving on with the pointer over it",
                 "scrub-hover-in": "The pointer reaches the progress bar: it thickens and the knob fades in",
                 "scrub-drag": "Dragging the progress bar"}


# -- menus -----------------------------------------------------------------------------------------

MENU_TITLES = {"menu.idle": "Menu bar menu", "menu.reading": "Menu bar menu while reading", "menu.speed": "Speed",
               "menu.voice": "Voice", "menu.provider": "Reading engine", "menu.transcription": "Transcription",
               "menu.cleanup": "Clean-up", "menu.settings": "Settings", "menu.keys": "Keys",
               "popup.speed": "Player: speed", "popup.voice": "Player: voice", "popup.history-more": "History: more"}
FLAG_WORDS = {"header": "heading", "disabled": "greyed out", "checked": "ticked", "submenu": "opens more",
              "icon": "with an icon"}


def _menu_row(line: str) -> str:
    if line == "----":
        return "<li class=sep></li>"
    m = re.match(r"(.*?)(?:   \[(.*?)\])?(?:   \((.*)\))?$", line)
    t, keys, flags = m.group(1), m.group(2) or "", m.group(3) or ""
    words = ", ".join(FLAG_WORDS.get(f.strip(), f.strip()) for f in flags.split(",") if f.strip())
    return (f"<li><span>{_esc(t)}</span>" + (f"<kbd>{_esc(keys)}</kbd>" if keys else "")
            + (f"<em>{_esc(words)}</em>" if words else "") + "</li>")


# -- the page --------------------------------------------------------------------------------------

CSS = """
:root{--cream:#F4EFE6;--ink:#141414;--soft:rgba(20,20,20,.62);--line:rgba(20,20,20,.12)}
*{box-sizing:border-box}
body{margin:0;background:var(--cream);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,
 "Helvetica Neue",Arial,sans-serif}
main{max-width:1180px;margin:0 auto;padding:48px 32px 96px}
h1{font-size:32px;margin:0 0 8px;font-weight:650}
h2{font-size:24px;margin:64px 0 4px;padding-top:24px;border-top:1px solid var(--line);font-weight:650}
h3{font-size:17px;margin:0 0 4px;font-weight:600}
p.lead{color:var(--soft);margin:0 0 20px}
.summary{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 24px;margin:24px 0}
.summary ul{margin:8px 0 0;padding-left:20px}
.summary li{margin:4px 0}
nav a{color:var(--ink);margin-right:14px;white-space:nowrap}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 24px;margin:20px 0}
.verdict{margin:0 0 16px;color:var(--soft)}
.verdict b{color:var(--ink);font-weight:600}
.ok{color:#2f6b3a}
.pairs{display:flex;flex-wrap:wrap;gap:24px 32px}
figure{margin:0}
figcaption{font-size:13px;color:var(--soft);margin-bottom:6px}
figure img{display:block;border-radius:6px;max-width:100%;height:auto}
.scheme{font-size:13px;font-weight:600;margin:14px 0 6px;color:var(--soft)}
.icons{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
.icon{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px}
.icon .pics{display:flex;gap:16px;margin:8px 0}
.icon img{width:96px;height:96px;background:#fff;border:1px solid var(--line);border-radius:8px;image-rendering:auto}
.frames{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px;align-items:flex-start}
.frames img{flex:none;border-radius:4px;background:#808080}
.menus{display:flex;gap:32px;flex-wrap:wrap}
.menus ul{list-style:none;margin:0;padding:6px 0;min-width:260px;background:var(--cream);border-radius:10px}
.menus li{display:flex;gap:12px;padding:3px 14px;font-size:14px}
.menus li span{flex:1}
.menus li kbd{font:inherit;color:var(--soft)}
.menus li em{font-style:normal;color:var(--soft);font-size:12px}
.menus li.sep{border-top:1px solid var(--line);margin:4px 0;padding:0}
footer{margin-top:64px;padding-top:24px;border-top:1px solid var(--line);color:var(--soft);font-size:14px}
"""


def _img(src: str, width: float, alt: str) -> str:
    return f"<img loading=lazy src='{_esc(src)}' width={round(width)} alt='{_esc(alt)}'>"


def _shown_width(size: list) -> float:
    w = float(size[0])
    return w * 2 if w < 120 else w * 1.5 if w < 300 else min(w, 540)


def write(out: Path, results: list[dict], failed: list[dict], icon_rows: list[dict], menus_l: dict, menus_q: dict,
          only: str | None, bias: dict[str, float] | None = None) -> None:
    from . import catalogue, compose

    by_pair: dict[str, dict[str, dict]] = {}
    for r in results:
        by_pair.setdefault(r["pair"], {})[r["scheme"]] = r
    order = [p.name for p in catalogue.select(only) if p.name in by_pair]
    sections: dict[str, list[str]] = {f: [] for f, _, _ in FAMILIES}
    counts = {"identical": 0, "within": 0, "differs": 0}
    remaining: list[str] = []
    for pair in order:
        rs = by_pair[pair]
        verdicts = {s: rs[s]["verdict"] for s in catalogue.SCHEMES if s in rs}
        vals = set(verdicts.values())
        if vals == {"Identical"}:
            counts["identical"] += 1
        elif vals <= {"Identical", "Matches within one pixel"}:
            counts["within"] += 1
        else:
            counts["differs"] += 1
        if len(vals) == 1:
            line = next(iter(vals))
        else:
            line = "; ".join(f"{s.capitalize()}: {v}" for s, v in verdicts.items())
        good = vals <= {"Identical", "Matches within one pixel"}
        if not good:
            reasons = []
            for s in catalogue.SCHEMES:
                for t in rs.get(s, {}).get("reasons", []):
                    small = t.startswith("Small drawing differences")
                    if t not in reasons and not (small and any(x.startswith("Small drawing") for x in reasons)):
                        reasons.append(t)
            said = "; ".join(reasons[:4])
            said = said[:1].upper() + said[1:]
            remaining.append(f"<li><b>{_esc(FAM_TITLE[family_of(pair)])}, {_esc(title(pair).lower())}:</b> "
                             f"{_esc(said)}{'; and more' if len(reasons) > 4 else ''}</li>")
        rows = [f"<div class=card><h3>{_esc(title(pair))}</h3><p class=verdict><b class={'ok' if good else ''}>"
                f"{_esc(line)}</b></p>"]
        for s in catalogue.SCHEMES:
            if s not in rs:
                continue
            r = rs[s]
            sl = [float(v) for v in r["size_legacy"].split("x")]
            sq = [float(v) for v in r["size_qt"].split("x")]
            stem = f"{pair}-{s}"
            rows.append(f"<p class=scheme>{'Light' if s == 'light' else 'Dark'}</p><div class=pairs>"
                        f"<figure><figcaption>{MAC}</figcaption>{_img(f'review/{stem}-mac.png', _shown_width(sl), MAC)}</figure>"
                        f"<figure><figcaption>{NEW}</figcaption>{_img(f'review/{stem}-new.png', _shown_width(sq), NEW)}</figure>"
                        "</div>")
        rows.append("</div>")
        sections[family_of(pair)].append("".join(rows))

    # Menus
    menu_ok = 0
    menu_total = 0
    for name in catalogue.select_names(catalogue.MENUS, only):
        if name not in menus_l and name not in menus_q:
            continue
        menu_total += 1
        la, qa = compose.menu_lines(menus_l.get(name, [])), compose.menu_lines(menus_q.get(name, []))
        if la == qa:
            menu_ok += 1
            v, good = "Identical: every row, shortcut, tick and heading is the same", True
        else:
            n = sum(1 for a, b in zip(la, qa, strict=False) if a != b) + abs(len(la) - len(qa))
            v, good = f"{n} row{'s' if n != 1 else ''} differ", False
            remaining.append(f"<li><b>Menus, {_esc(MENU_TITLES.get(name, name).lower())}:</b> {_esc(v)}</li>")
        sections["menu"].append(
            f"<div class=card><h3>{_esc(MENU_TITLES.get(name, name))}</h3><p class=verdict><b class={'ok' if good else ''}>"
            f"{_esc(v)}</b></p><div class=menus><figure><figcaption>{MAC}</figcaption><ul>"
            + "".join(_menu_row(x) for x in la) + f"</ul></figure><figure><figcaption>{NEW}</figcaption><ul>"
            + "".join(_menu_row(x) for x in qa) + "</ul></figure></div></div>")

    # Icons
    icon_ok = 0
    not_compared = 0
    cards = []
    for r in icon_rows:
        head = f"<h3>{_esc(r['use'][0].upper() + r['use'][1:])}</h3>"
        if "missing" in r:
            # The new interface draws this one itself, not from its icon set (the setup window's
            # green check disc): it is compared in the screens that show it, not here.
            v, good = "Not compared here: the new interface draws this one itself; it shows in the screens above", True
            not_compared += 1
            pics = ""
        else:
            sl, sq = r["legacy"], r["qt"]
            parts = []
            for key, more, less in (("w", "wider", "narrower"), ("h", "taller", "shorter")):
                dlt = sq[key] - sl[key]
                if abs(dlt) > 0.5:
                    parts.append(f"{_px(dlt)} {more if dlt > 0 else less}")
            for key, more, less in (("dx", "further right", "further left"), ("dy", "lower", "higher")):
                dlt = sq[key] - sl[key]
                if abs(dlt) > 0.5:
                    parts.append(f"{_px(dlt)} {more if dlt > 0 else less}")
            if abs(r["ink_pct"]) > 5:
                parts.append(f"{abs(r['ink_pct']):.0f} percent {'heavier' if r['ink_pct'] > 0 else 'lighter'}")
            good = bool(r.get("ok"))
            v = "Matches" if good else "The new icon is " + _join(parts) if parts else "Matches"
            if good:
                icon_ok += 1
            else:
                remaining.append(f"<li><b>Icons, {_esc(r['use'])}:</b> {_esc(v)}</li>")
            pics = (f"<div class=pics><figure><figcaption>{MAC}</figcaption>"
                    f"<img loading=lazy src='raw/legacy/icons/{_esc(r['key'])}.png' alt=''></figure>"
                    f"<figure><figcaption>{NEW}</figcaption><img loading=lazy src='raw/qt/icons/{_esc(r['key'])}.png' alt=''>"
                    "</figure></div>")
        cards.append(f"<div class=icon>{head}<p class=verdict><b class={'ok' if good else ''}>{_esc(v)}</b></p>{pics}</div>")
    if cards:
        sections["icons"].append("<div class=icons>" + "".join(cards) + "</div>")

    # Animations
    motions = motion_verdicts(out, only)
    motion_ok = 0
    for m in motions:
        good = bool(m.get("ok"))
        motion_ok += good
        if not good:
            remaining.append(f"<li><b>Animations, {_esc(MOTION_TITLES.get(m['name'], m['name']).lower())}:</b> "
                             f"{_esc(m['verdict'])}</li>")
        strips = ""
        for side, label in (("legacy", MAC), ("qt", NEW)):
            imgs = ""
            for i in range(m["frames"]):
                path = out / "motion" / "raw" / side / f"{m['name']}-{i}.png"
                from PySide6.QtGui import QImage

                w = QImage(str(path)).width() / 2
                imgs += _img(f"motion/raw/{side}/{m['name']}-{i}.png", w * (2 if w < 120 else 1), "")
            strips += f"<p class=scheme>{label}</p><div class=frames>{imgs}</div>"
        sections["animations"].append(
            f"<div class=card><h3>{_esc(MOTION_TITLES.get(m['name'], m['name']))}</h3><p class=verdict>"
            f"<b class={'ok' if good else ''}>{_esc(m['verdict'])}</b></p>{strips}</div>")

    screens = sum(counts.values())
    summary = [f"<p>{screens} screens were compared, each in light and dark mode. <b>{counts['identical']}</b> are identical, "
               f"<b>{counts['within']}</b> match within one pixel, and <b>{counts['differs']}</b> still differ.",
               f" {menu_ok} of {menu_total} menus, {icon_ok} of {len(icon_rows) - not_compared} icons and {motion_ok} of "
               f"{len(motions)} animations match.</p>"]
    if failed:
        summary.append(f"<p>{len(failed)} pictures could not be drawn on one side and are left out.</p>")
    general = []
    for scheme, b in (bias or {}).items():
        if abs(b) >= INK_SHARE:
            general.append(f"<li><b>All text in {scheme} mode</b> is drawn about {abs(b):.0f} percent "
                           f"{'heavier' if b > 0 else 'lighter'} in the new interface than in today's Mac app "
                           f"(the same words, letter for letter, measured on one line of text).</li>")
        else:
            general.append(f"<li>Text weight in {scheme} mode matches today's Mac app (within "
                           f"{max(1, round(abs(b)))} percent).</li>")
    summary.append("<p><b>What still differs</b></p><ul>" + "".join(general) + ("".join(remaining) or "<li>Nothing.</li>")
                   + "</ul>")
    nav = " ".join(f"<a href='#{f}'>{_esc(t)}</a>" for f, t, _ in FAMILIES if sections[f])
    body = [f"<h1>Lyssna: {MAC.lower()} next to the new interface</h1>",
            "<p class=lead>Each screen is shown as it looks in today's Mac app and in the new interface, in the same "
            "state, in light and dark mode.</p>", f"<div class=summary>{''.join(summary)}</div>", f"<nav>{nav}</nav>"]
    for f, t, what in FAMILIES:
        if sections[f]:
            body.append(f"<h2 id='{f}'>{_esc(t)}</h2><p class=lead>{_esc(what)}</p>" + "".join(sections[f]))
    body.append(
        "<footer><p><b>How the verdicts are decided.</b> Identical: fewer than "
        f"{IDENTICAL_SHARE:g} percent of the pixels differ, and none by more than a one-pixel shift. Matches within one "
        f"pixel: after allowing every pixel to shift by one, fewer than {MATCH_SHARE:g} percent still differ, and no "
        f"part is out of place by {STEP:g} pixel or more. A pixel counts as different when its colour is off by more than "
        f"{TOL} of 255 levels. Text is called lighter or heavier when it has more than {INK_SHARE:g} percent less or more "
        f"ink. An icon matches when its outline and centre are within half a pixel and its ink within 5 percent. An "
        f"animation moves the same way when every one of its eight moments is within {round(MOTION_TIMING * 100)} "
        f"percent of the movement and no moment differs in more than {MOTION_LOOK:g} percent of its pixels (the progress "
        f"bar: within {SCRUB_STEP:g} pixel). Pixels are screen points; the pictures are taken at Retina sharpness. "
        "Shadows under floating windows, the docked bar's sparks and the title bars are not part of the pictures.</p>"
        "</footer>")
    page = (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>Lyssna: {MAC.lower()} next to the new interface</title><style>{CSS}</style><main>"
            + "".join(body) + "</main></html>")
    (out / "review.html").write_text(page, encoding="utf-8")


FAM_TITLE = {f: t for f, t, _ in FAMILIES}
