"""The motion audit: every animation of the legacy interface against its Qt twin.

Two parts:
  * the table (motion.md): for each animation, duration, delay, easing (control points), start
    and end values, repeat and autoreverse, and what triggers it, on both sides. Where the
    animation object can be built offscreen it is read back from the object itself (a
    CAAnimation on the legacy side, a QVariantAnimation on the Qt side, written to
    legacy_anims.json and qt_anims.json); the rest comes from reading the code, and says so.
  * filmstrips: 8 evenly spaced frames of each side for the animations both sides can be stepped
    through deterministically (legacy: the animation re-timed under a stopped clock and the
    presentation layer drawn; Qt: the QVariantAnimation set to each time, or the view's clock
    set). Light scheme only.

    python -m scripts.compare.motion --out DIR      (after both sides ran with --motion DIR/motion)"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRAMES = 8

# name: (seconds the strip covers, what the frames show)
STRIPS = {
    "pill-grow": (0.18, "the docked sliver grows into the pill (hover)"),
    "menu-grow": (0.18, "the resting squiggle grows into the idle menu (hover)"),
    "hands-free-grow": (0.18, "the dictation capsule grows to hands-free, cancel and finish fading in"),
    "slide-out": (0.18, "auto-hide: the squiggle slides out of the edge, fading"),
    "ripple": (2.8, "the resting squiggle's ripple"),
    "bloom": (0.75, "the sentence bloom at the sliver's waterline"),
    "working-breathe": (1.4, "the working wave's breathing opacity, one full breath"),
    "sliver-drift": (8.0, "the sliver's gradient drift and waterline glow over 8 s (no sparks)"),
    "hover-glow": (0.14, "the hover glow fading in on the card's previous-sentence button"),
}


def _select(only: str | None) -> list[str]:
    if not only:
        return list(STRIPS)
    pattern = only if any(c in only for c in "*?[") else f"*{only}*"
    picked = [n for n in STRIPS if fnmatch.fnmatch(n, pattern) or fnmatch.fnmatch(f"motion.{n}", pattern)]
    return picked


def _times(name: str) -> list[float]:
    span = STRIPS[name][0]
    return [round(span * i / (FRAMES - 1), 4) for i in range(FRAMES)]


# -- the legacy side ------------------------------------------------------------------------------


def _ca_info(anim) -> dict:
    """What a CAAnimation says about itself."""
    info = {"class": type(anim).__name__, "duration": round(float(anim.duration()), 4),
            "delay (beginTime)": round(float(anim.beginTime()), 4), "autoreverses": bool(anim.autoreverses()),
            "repeat": float(anim.repeatCount())}
    tf = anim.timingFunction()
    if tf is not None:
        import Quartz

        for name, pts in NAMED_TIMING.items():
            if tf.isEqual_(Quartz.CAMediaTimingFunction.functionWithName_(name)):
                info["easing"] = f"{name} cubic-bezier {pts}"
                break
        else:
            info["easing"] = f"custom ({_short(tf)})"
    else:
        info["easing"] = "none set: linear for an explicit animation"
    if hasattr(anim, "keyPath") and anim.keyPath():
        info["key path"] = str(anim.keyPath())
    for k in ("fromValue", "toValue"):
        if hasattr(anim, k):
            v = getattr(anim, k)()
            if v is not None:
                info[k] = _short(v)
    if hasattr(anim, "values") and anim.values() is not None:
        info["values"] = f"{len(anim.values())} keyframes"
    if hasattr(anim, "animations") and anim.animations() is not None:
        info["group"] = [_ca_info(a) for a in anim.animations()]
    return info


# Apple's named timing functions and their documented control points (x1, y1, x2, y2).
NAMED_TIMING = {"linear": (0.0, 0.0, 1.0, 1.0), "easeIn": (0.42, 0.0, 1.0, 1.0), "easeOut": (0.0, 0.0, 0.58, 1.0),
                "easeInEaseOut": (0.42, 0.0, 0.58, 1.0), "default": (0.25, 0.1, 0.25, 1.0)}


def _short(v) -> str:
    s = str(v).replace("\n", " ")
    s = " ".join(s.split())
    return s if len(s) < 90 else s[:89] + "…"


def _collect(layer, out: dict, path: str = "") -> None:
    for key in layer.animationKeys() or []:
        out[f"{path}{type(layer).__name__}:{key}"] = _ca_info(layer.animationForKey_(key))
    for i, sub in enumerate(layer.sublayers() or []):
        _collect(sub, out, f"{path}{i}/")


def legacy_filmstrips(out: Path, only: str | None) -> None:
    import AppKit
    import Quartz

    from scripts.compare import catalogue
    from scripts.compare import fixtures as fx
    from scripts.compare import legacy_side as L

    raw = out / "raw" / "legacy"
    raw.mkdir(parents=True, exist_ok=True)
    app = L.appearance("light")
    AppKit.NSApp.setAppearance_(app)
    anims: dict = {}

    def player():
        p = L._player_owner()
        p.panel.setAppearance_(app)
        return p

    def strip(name: str, view, keys: dict | None, setup=None) -> None:
        """Draw `view` at each time; `keys` maps animation keys to an age offset (None: every
        animation present is aged together)."""
        for i, t in enumerate(_times(name)):
            if setup is not None:
                setup(t)
            present: dict = {}
            stack = [view.layer()]
            while stack:
                layer = stack.pop()
                for k in layer.animationKeys() or []:
                    present[str(k)] = t
                stack.extend(layer.sublayers() or [])
            ages = present if keys is None else {k: t + off for k, off in keys.items()}
            L.render(view, app, raw / f"{name}-{i}.png", "layer", ages)

    for name in _select(only):
        try:
            found: dict = {}
            if name in ("pill-grow", "menu-grow"):
                p = player()
                if name == "pill-grow":
                    p.layout = {"dock": "right", "x": 0.0, "y": 0.5}
                    p._update(fx.reading())
                    p._open_pill()
                    host = p.pill
                else:
                    p.dictation_layout = {"dock": "bottom", "y": -1.0}
                    p.show_idle()
                    p._open_menu()
                    host = p.menu
                Quartz.CATransaction.flush()
                _collect(host.layer(), found)
                strip(name, p.panel.contentView(), {"scale": 0.0})
            elif name == "hands-free-grow":
                p = player()
                p.dictation_layout = {"dock": "right", "y": -1.0}
                p._level = 0.5
                p._set_dictation("recording")
                p.wave.stop()
                p._set_hands_free(True, animate=True)
                Quartz.CATransaction.flush()
                _collect(p.dictate_host.layer(), found)
                strip(name, p.panel.contentView(), None)
            elif name == "slide-out":
                p = player()
                p.dictation_layout = {"dock": "bottom", "y": -1.0}
                p.show_idle()
                p._tuck()
                Quartz.CATransaction.flush()
                _collect(p.rest.layer(), found)
                strip(name, p.panel.contentView(), {"slide": 0.0, "fade": 0.0})
            elif name == "ripple":
                p = player()
                p.dictation_layout = {"dock": "bottom", "y": -1.0}
                p.show_idle()
                p.rest.wave()
                Quartz.CATransaction.flush()
                _collect(p.rest.layer(), found)
                strip(name, p.panel.contentView(), {"wave": 0.0})
            elif name == "bloom":
                p = player()
                p.layout = {"dock": "right", "x": 0.0, "y": 0.5}
                p._update(fx.reading(position=0.4 * 334.0))
                p.sliver.pulse()
                Quartz.CATransaction.flush()
                found = {"bloom": _ca_info(p.sliver.bloom.animationForKey_("bloom"))}
                d = catalogue.DRIFT_T
                for i, t in enumerate(_times(name)):
                    L.render(p.panel.contentView(), app, raw / f"{name}-{i}.png", "layer",
                             {"hue": d, "split": d, "breathe": d, "bloom": t})
            elif name == "working-breathe":
                p = player()
                p.dictation_layout = {"dock": "right", "y": -1.0}
                p._level = 0.5
                p._set_dictation("recording")
                p._set_dictation("working")
                p.wave.stop()
                Quartz.CATransaction.flush()
                found = {"breathe": _ca_info(p.wave.shape.animationForKey_("breathe"))}
                strip(name, p.panel.contentView(), {"breathe": 0.0})
            elif name == "sliver-drift":
                p = player()
                p.layout = {"dock": "right", "x": 0.0, "y": 0.5}
                p._update(fx.reading(position=0.4 * 334.0))
                Quartz.CATransaction.flush()
                _collect(p.sliver.layer(), found)
                strip(name, p.panel.contentView(), {"hue": 0.0, "split": 0.0, "breathe": 0.0})
            elif name == "hover-glow":
                p = player()
                p._update(fx.reading())
                bg = p.hovers[1]  # the previous-sentence button's glow wrapper
                bg.set_hover(True)
                Quartz.CATransaction.flush()
                _collect(bg.layer(), found)
                strip(name, p.panel.contentView(), None)
            anims[name] = found
        except Exception as exc:
            import traceback

            anims[name] = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    # The scrub knob's implicit move and the toast's window fade, read without a strip.
    try:
        p = player()
        p._update(fx.reading())
        p.scrub.hovering = True
        p.scrub.setNeedsDisplay_(True)
        p.scrub.displayIfNeeded()
        p.scrub.set_fraction(0.7)
        p.scrub.setNeedsDisplay_(True)
        p.scrub.displayIfNeeded()
        Quartz.CATransaction.flush()
        found = {}
        _collect(p.scrub.layer(), found)
        anims["scrub-knob"] = found or {"note": "no animation object found on the scrub bar's layers"}
    except Exception as exc:
        anims["scrub-knob"] = {"error": str(exc)}
    (out / "legacy_anims.json").write_text(json.dumps(anims, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


# -- the Qt side ----------------------------------------------------------------------------------


def _qt_info(anim) -> dict:
    from PySide6.QtCore import QEasingCurve

    curve = anim.easingCurve()
    kind = curve.type()
    if kind == QEasingCurve.Type.BezierSpline:
        pts = [[round(p.x(), 3), round(p.y(), 3)] for p in curve.toCubicSpline()]
        easing = f"cubic-bezier {pts[0]} {pts[1]}" if len(pts) >= 2 else "bezier"
    else:
        easing = kind.name
    return {"class": type(anim).__name__, "duration": anim.duration() / 1000, "easing": easing,
            "loops": anim.loopCount(), "fromValue": _short(anim.startValue()), "toValue": _short(anim.endValue())}


def qt_filmstrips(out: Path, only: str | None) -> None:
    from PySide6.QtWidgets import QApplication

    from lyssna.ui import theme
    from scripts.compare import catalogue
    from scripts.compare import fixtures as fx
    from scripts.compare import qt_side as Q

    raw = out / "raw" / "qt"
    raw.mkdir(parents=True, exist_ok=True)
    theme.set_scheme("light")
    anims: dict = {}

    def shoot(name: str, owner, step) -> None:
        if owner.margin > 0:
            owner.set_shadow(False)
        for i, t in enumerate(_times(name)):
            step(t)
            QApplication.processEvents()
            Q.render(owner.panel, raw / f"{name}-{i}.png", False)

    for name in _select(only):
        try:
            mp = Q._player()
            if name in ("pill-grow", "menu-grow"):
                if name == "pill-grow":
                    mp.layout = {"dock": "right", "x": 0.0, "y": 0.5}
                    mp._update(fx.reading())
                    mp._open_pill()
                else:
                    mp.dictation_layout = {"dock": "bottom", "y": -1.0}
                    mp.show_idle()
                    mp._open_menu()
                anim = mp._scale_anim
                anim.pause()
                anims[name] = {"scale": _qt_info(anim)}
                shoot(name, mp, lambda t, a=anim: a.setCurrentTime(round(t * 1000)))
            elif name == "hands-free-grow":
                mp.dictation_layout = {"dock": "right", "y": -1.0}
                mp._level = 0.5
                mp._set_dictation("recording")
                mp.wave.stop()
                mp._set_hands_free(True, animate=True)
                anim = mp._grow_anim
                anim.pause()
                anims[name] = {"grow": _qt_info(anim)}
                shoot(name, mp, lambda t, a=anim: a.setCurrentTime(round(t * 1000)))
            elif name == "slide-out":
                mp.dictation_layout = {"dock": "bottom", "y": -1.0}
                mp.show_idle()
                mp._tuck()
                anim = mp._slide_anim
                anim.pause()
                anims[name] = {"slide": _qt_info(anim)}
                shoot(name, mp, lambda t, a=anim: a.setCurrentTime(min(round(t * 1000), a.duration() - 1)))
            elif name == "ripple":
                mp.dictation_layout = {"dock": "bottom", "y": -1.0}
                mp.show_idle()
                rest = mp.rest

                def ripple(t, rest=rest):
                    rest._ripple = t if t < 2.8 else None
                    rest.update()
                anims[name] = {"ripple": {"duration": 2.8, "easing": "phase = (1 - cos(pi u)) / 2 (ripple_phase)",
                                          "note": "a clock in Sliver.advance, no animation object"}}
                shoot(name, mp, ripple)
            elif name in ("bloom", "sliver-drift"):
                mp.layout = {"dock": "right", "x": 0.0, "y": 0.5}
                mp._update(fx.reading(position=0.4 * 334.0))
                sl = mp.sliver

                def moment(t, sl=sl, name=name):
                    if name == "bloom":
                        sl.preview_moment(t=catalogue.DRIFT_T, bloom=t)
                    else:
                        sl.preview_moment(t=t, bloom=None)
                    sl._sparks = []
                    sl.update()
                anims[name] = {"clock": {"note": "a clock in Sliver.advance (FPS 30), no animation object"}}
                shoot(name, mp, moment)
            elif name == "working-breathe":
                from lyssna.ui.wave import breathe

                mp.dictation_layout = {"dock": "right", "y": -1.0}
                mp._level = 0.5
                mp._set_dictation("recording")
                mp._set_dictation("working")
                mp.wave.stop()
                wave = mp.wave

                def br(t, wave=wave):
                    wave.opacity = breathe(t)
                    wave.update()
                anims[name] = {"breathe": {"note": "breathe(t) in lyssna/ui/wave.py, driven by WaveView.tick"}}
                shoot(name, mp, br)
            elif name == "hover-glow":
                mp._update(fx.reading())
                btn = mp.buttons["back"][0]
                btn.setVisible(True)
                mp.panel.show()
                btn.set_hover(True, animate=True)
                anim = btn._anim
                if anim is None:
                    raise RuntimeError("no hover animation (the button is not visible offscreen)")
                anim.pause()
                anims[name] = {"glow": _qt_info(anim)}
                shoot(name, mp, lambda t, a=anim: a.setCurrentTime(round(t * 1000)))
        except Exception as exc:
            import traceback

            anims[name] = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    theme.set_scheme(None)
    (out / "qt_anims.json").write_text(json.dumps(anims, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


# -- the table (from reading the code on both sides) ----------------------------------------------
# (animation, trigger, legacy, Qt, same?) The legacy column names the code; numbers are the
# constants both sides import from lyssna/ui/layout where they share one.

TABLE = [
    ("Pill growth (sliver to pill, hover)", "cursor enters the docked sliver",
     "CABasicAnimation transform on the pill layer, HOVER_SECS 0.18 s, kCAMediaTimingFunctionEaseOut (0,0,0.58,1), "
     "scale SLIVER_H/pill_len to 1, about the edge-side middle; shrink the same back after HOVER_OUT_DELAY",
     "QVariantAnimation on ScaleHost scale, HOVER_SECS, EASE_OUT bezier (0,0,0.58,1), same values; scales a "
     "picture of the pill", "same numbers; Qt scales a bitmap grabbed at scale 1 (soft while growing)"),
    ("Idle menu growth", "cursor enters the resting squiggle",
     "same transform animation on the menu layer; from SLIVER_H/MENU_H", "same QVariantAnimation; from SLIVER_H/menu_len, "
     "or MENU_POP 0.5 for a menu shorter than the sliver", "Qt adds the half-size start for a short menu; legacy has none"),
    ("Hands-free capsule growth", "dictation turns hands-free (locked)",
     "NSAnimationContext GROW_SECS 0.18, EaseOut; capsule frame COMPACT_H to FREE_H about its centre, cancel and finish "
     "strips alphaValue 0 to 1; buttons ride on autoresizing", "QVariantAnimation GROW_SECS, EASE_OUT; _lay_dictate re-lays "
     "capsule and buttons per frame, alpha 0 to 1", "same"),
    ("Hover glow", "cursor enters any HoverView",
     "implicit CALayer opacity on the glow sublayer, 0.14 s in, 0.22 s out, CATransaction timing not set (see the "
     "read-back row)", "QVariantAnimation GLOW_IN 0.14 / GLOW_OUT 0.22, InOutQuad", "durations same; easing see read-back"),
    ("Toast fade", "a notice shows or expires",
     "NSAnimationContext on the window's alphaValue, FADE_IN 0.15 s, FADE_OUT 0.25 s, timing not set (system default)",
     "QVariantAnimation on window opacity, same durations, InOutQuad", "durations same; Qt easing InOutQuad is a guess at "
     "the system default; the offscreen platform cannot set window opacity, so not measurable here"),
    ("Segmented control slide (history tabs)", "a tab is clicked",
     "NSTimer at FRAME, SEG_SLIDE 0.18 s, ease_out cubic 1-(1-t)^3, capsule x and width", "QVariantAnimation SEG_SLIDE, "
     "linear value fed through the same ease_out", "same"),
    ("Squiggle ripple", "every WAVE_EVERY s while resting (may_wave)",
     "CAKeyframeAnimation path on halo and line, WAVE_SECS 2.8 s, WAVE_FRAMES 72 key paths from wave_phases (eased "
     "cos), keyframes linear", "Sliver clock at WAVE_FPS 60, ripple_phase(age) the continuous form of wave_phases",
     "same curve; legacy is 72 linear keyframes, Qt continuous"),
    ("Sliver drift (hue, split, swing)", "while playing; frozen while paused",
     "_loop: CABasicAnimation autoreverse, repeat 1e9, ease-in-out; colors 6.7 s, locations 5.3 s, startPoint/endPoint "
     "8.1 s", "drift(t, secs) with EASE_IN_OUT on the same periods", "hue and split same; the legacy swing animations "
     "are given Python tuples (NSArray), which Core Animation cannot evaluate, so on the Mac the gradient ends most "
     "likely never swing while Qt swings them"),
    ("Waterline glow breathing", "while playing", "_loop opacity 0.45 to 1.0, 2.6 s, autoreverse, ease-in-out",
     "BREATHE_LOW + (1 - BREATHE_LOW) * drift(t, 2.6)", "same"),
    ("Waterline rise", "progress changes", "implicit 0.35 s, EaseOut, on clip frame and top frame (CATransaction)",
     "WATERLINE_SECS 0.35, EASE_OUT tween of the drawn fraction and dim", "same"),
    ("Pause dim", "pause", "clip opacity 0.45, inside the same 0.35 s EaseOut transaction", "DIM 0.45 in the same tween",
     "same"),
    ("Sentence bloom", "a new sentence while docked and playing",
     "CAAnimationGroup 0.75 s EaseOut: transform.scale 0.25 to 1, opacity 0.7 to 0", "BLOOM_SECS 0.75, EASE_OUT, radius "
     "0.25 to 1 of BLOOM/2, alpha 0.7 to 0", "same"),
    ("Sparks", "while playing", "CAEmitterLayer: birth 2.2/s x birthRate 1, life 2.4 +/- 0.8 s, speed 11 +/- 6, cone 0.35 "
     "rad, scale 0 + 0.34/s (range 0.08), alpha -0.42/s, additive", "the same numbers, simulated at 30 fps with a "
     "seeded random", "same numbers; not comparable as pixels (the emitter cannot be drawn offscreen)"),
    ("Working wave breathing", "dictation state working",
     "_loop opacity BREATHE_LOW 0.45 to 1.0, BREATHE_SECS/2 0.7 s each way, autoreverse, ease-in-out",
     "breathe(t): 0.45 to 1.0 and back over BREATHE_SECS 1.4 s, (1 - cos(pi tri)) / 2", "same period; cosine against the "
     "CA ease-in-out bezier, a small shape difference"),
    ("Wave meter", "recording", "NSTimer FPS, ease() toward target_amplitude(level), phase += SPEED dt", "QTimer FPS, the "
     "same functions", "same"),
    ("Auto-hide slide", "cursor leaves (tuck) or comes near (reveal)",
     "sublayerTransform translation by SLIVER_W and squiggle opacity, SLIDE_SECS 0.18, EaseIn out / EaseOut in",
     "QVariantAnimation on rest.slide, SLIDE_SECS, EASE_IN / EASE_OUT, opacity 1 - slide/SLIVER_W", "same"),
    ("Scrub bar thickening", "cursor over the scrub bar or scrubbing",
     "bar height BAR_H to 1.5 BAR_H set in updateLayer (no animation); knob CALayer frame and hidden set without "
     "disabling actions, so the knob's move rides the implicit 0.25 s action", "bar and knob change at once, no animation",
     "Qt has no knob glide (see the read-back row)"),
    ("Copy checkmark", "copy button in a history row", "symbol swaps to checkmark, back after COPIED_FOR 1.2 s, no "
     "animation", "the same swap after COPIED_FOR", "same"),
    ("Card frame moves", "tuck, expand, place", "setFrame_display_ without animation", "set_frame without animate",
     "same (Qt's set_frame animate path, InOutQuad 0.25 s, is unused here)"),
]


def _strip_sheet(name: str, out: Path) -> Path | None:
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    raw_l, raw_q = out / "raw" / "legacy", out / "raw" / "qt"
    left = [QImage(str(raw_l / f"{name}-{i}.png")) for i in range(FRAMES)]
    right = [QImage(str(raw_q / f"{name}-{i}.png")) for i in range(FRAMES)]
    if any(i.isNull() for i in left + right):
        return None
    cw = max(i.width() for i in left + right)
    ch = max(i.height() for i in left + right)
    gap, label = 16, 150
    W = label + FRAMES * (cw + gap)
    H = 70 + 2 * (ch + gap + 20)
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(128, 128, 128))
    p = QPainter(img)
    f = QFont("Menlo")
    f.setPixelSize(16)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(10, 24, f"{name}: {STRIPS[name][1]}")
    times = _times(name)
    for row, (title, pics) in enumerate((("legacy", left), ("Qt", right))):
        y = 50 + row * (ch + gap + 20)
        p.drawText(10, y + ch // 2, title)
        for i, pic in enumerate(pics):
            x = label + i * (cw + gap)
            if row == 0:
                p.drawText(x, 44, f"t={times[i]:g}s")
            p.drawImage(x, y, pic)
    p.end()
    path = out / f"{name}.png"
    img.save(str(path))
    return path


def _readback_rows(jl: dict, jq: dict) -> list[str]:
    rows = []
    for name in sorted(set(jl) | set(jq)):
        a, b = jl.get(name, {}), jq.get(name, {})
        rows.append(f"| {name} | {_cell(a)} | {_cell(b)} |")
    return rows


def _cell(d: dict) -> str:
    if not d:
        return "(none)"
    if "error" in d:
        return "could not read: " + d["error"]
    parts = []
    for key, info in d.items():
        if isinstance(info, dict):
            keep = {k: v for k, v in info.items() if k in ("duration", "easing", "autoreverses", "repeat", "loops",
                                                          "fromValue", "toValue", "key path", "values", "note",
                                                          "delay (beginTime)")}
            group = info.get("group")
            if group:
                keep["group"] = "; ".join(f"{g.get('key path')}: {g.get('fromValue')} to {g.get('toValue')}" for g in group)
            parts.append(f"**{key}**: " + ", ".join(f"{k} {v}" for k, v in keep.items()))
        else:
            parts.append(f"{key}: {info}")
    return "<br>".join(parts).replace("|", "/")


def main(argv: list[str] | None = None) -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication

    ap = argparse.ArgumentParser(prog="python -m scripts.compare.motion")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    app = QApplication.instance() or QApplication(["lyssna-compare"])  # noqa: F841
    out = Path(args.out)
    mdir = out / "motion"
    mdir.mkdir(parents=True, exist_ok=True)
    strips = []
    for name in _select(args.only):
        path = _strip_sheet(name, mdir)
        strips.append((name, path))

    def read(p: Path) -> dict:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
    jl, jq = read(mdir / "legacy_anims.json"), read(mdir / "qt_anims.json")
    md = ["# Motion audit: legacy against Qt\n",
          "## From the code\n",
          "| animation | trigger | legacy | Qt | same? |", "|---|---|---|---|---|"]
    md += [f"| {a} | {t} | {lg} | {qt} | {s} |" for a, t, lg, qt, s in TABLE]
    md += ["\n## Read back from the animation objects\n",
           "Built offscreen and asked: a CAAnimation on the legacy side, a QVariantAnimation on the Qt side. Easing is "
           "the cubic-bezier control points (P1, P2).\n", "| animation | legacy | Qt |", "|---|---|---|"]
    md += _readback_rows(jl, jq)
    md += ["\n## Filmstrips\n", f"{FRAMES} evenly spaced frames per side, light scheme, no shadow. Top row legacy, bottom "
           "row Qt.\n"]
    for name, path in strips:
        if path is None:
            md.append(f"- {name}: no filmstrip (a side could not render it; see the read-back errors)")
        else:
            md.append(f"### {name}\n\n{STRIPS[name][1]}\n\n![{name}](motion/{path.name})\n")
    (out / "motion.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"motion: {sum(1 for _, p in strips if p)} filmstrips, table at {out / 'motion.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
