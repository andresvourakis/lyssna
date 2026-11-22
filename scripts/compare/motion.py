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
    "sliver-drift": (8.0, "the sliver's gradient drift (hue, split, swing) and waterline glow over 8 s (no sparks)"),
    "hover-glow": (0.14, "the hover glow fading in on the card's previous-sentence button"),
    "scrub-progress": (0.25, "scrub bar, not hovered: playback moves the playhead from 25 to 60 percent (the fill glides)"),
    "scrub-progress-hover": (0.25, "scrub bar, hovered: playback moves the playhead from 25 to 60 percent (fill and knob)"),
    "scrub-hover-in": (0.25, "scrub bar at 25 percent: the cursor enters (the bar thickens, the knob fades in)"),
    "scrub-drag": (0.25, "scrub bar, held: one drag step from 25 to 60 percent"),
}
SCRUB = {  # strip: (hovering before, scrubbing before, what changes)
    "scrub-progress": (False, False, "progress"),
    "scrub-progress-hover": (True, False, "progress"),
    "scrub-hover-in": (False, False, "hover"),
    "scrub-drag": (True, True, "drag"),
}
SCRUB_FROM, SCRUB_TO = 0.25, 0.6  # playhead fractions (the reading is 334 s long)


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


def bezier(x1: float, y1: float, x2: float, y2: float, x: float) -> float:
    """A CSS / Core Animation cubic-bezier timing function at progress `x` (0..1)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    def at(a: float, b: float, u: float) -> float:
        return 3 * a * u * (1 - u) ** 2 + 3 * b * u * u * (1 - u) + u ** 3

    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if at(x1, x2, mid) < x:
            lo = mid
        else:
            hi = mid
    return at(y1, y2, (lo + hi) / 2)


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
    readback: dict = {}

    def player():
        p = L._player_owner()
        p.panel.setAppearance_(app)
        return p

    def strip(name: str, view, keys: dict | None, setup=None, method: str = "layer") -> None:
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
            L.render(view, app, raw / f"{name}-{i}.png", method, ages)

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
                # The capsule's frame animation is on its layer and is aged with the rest; the
                # buttons' alphaValue animator does nothing in a window that is never shown, so
                # the fade is evaluated here on the same curve the code asks for (GROW_SECS,
                # ease-out) and set per frame.
                from lyssna.legacy.ui.mini_player import GROW_SECS

                def fade(t, p=p, secs=GROW_SECS):
                    alpha = bezier(*NAMED_TIMING["easeOut"], min(1.0, t / secs))
                    for b in (p.cancel_bg, p.finish_bg):
                        b.setAlphaValue_(alpha)
                found["buttons alphaValue"] = {"note": f"0 to 1 over {GROW_SECS} s, easeOut, evaluated per frame by the "
                                                       "harness (the animator does not run offscreen)"}
                strip(name, p.panel.contentView(), None, fade)
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
                for i, t in enumerate(_times(name)):  # Core Animation's renderer: the swing is drawn too
                    L.render(p.panel.contentView(), app, raw / f"{name}-{i}.png", "ca",
                             {"hue": d, "split": d, "breathe": d, "swing": d, "swing2": d, "bloom": t})
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
                strip(name, p.panel.contentView(), {"hue": 0.0, "split": 0.0, "breathe": 0.0, "swing": 0.0, "swing2": 0.0},
                      method="ca")
            elif name in SCRUB:
                found = legacy_scrub(name, player(), raw, app, readback)
            elif name == "hover-glow":
                p = player()
                p._update(fx.reading())
                bg = p.hovers[1]  # the previous-sentence button's glow wrapper
                bg.set_hover(True)
                Quartz.CATransaction.flush()
                _collect(bg.layer(), found)
                if not found:
                    # set_hover sets the glow's opacity in a CATransaction of 0.14 s; in a window
                    # that is never shown no implicit action is attached, so the frames would all
                    # show the finished glow. Evaluated per frame instead: 0.14 s on Core
                    # Animation's default curve (what round 1B read back from the implicit action).
                    def glow(t, layer=bg.glow):
                        Quartz.CATransaction.begin()
                        Quartz.CATransaction.setDisableActions_(True)
                        layer.setOpacity_(bezier(*NAMED_TIMING["default"], min(1.0, t / 0.14)))
                        Quartz.CATransaction.commit()
                    found["glow opacity"] = {"note": "0 to 1 over 0.14 s, default curve, evaluated per frame by the "
                                                     "harness (no implicit action offscreen)"}
                    strip(name, p.panel.contentView(), None, glow)
                else:
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
    (out / "legacy_scrub.json").write_text(json.dumps(readback, indent=1), encoding="utf-8")


def legacy_scrub(name: str, p, raw: Path, app, readback: dict) -> dict:
    """One scrub strip on the legacy card: settle the start state (display, then drop the
    implicit animations that display made), make the change through the code's own path, let
    Core Animation attach its implicit actions, and age them all together frame by frame. The
    fill's width and the knob's centre are read back from the presentation layers."""
    import Quartz

    from scripts.compare import fixtures as fx
    from scripts.compare import legacy_side as L

    hover, held, change = SCRUB[name]
    p._update(fx.reading(position=SCRUB_FROM * 334.0))
    s = p.scrub
    s.hovering, s.scrubbing = hover, held

    def display() -> None:
        s.setNeedsDisplay_(True)
        p.panel.contentView().layoutSubtreeIfNeeded()
        p.panel.displayIfNeeded()
        s.displayIfNeeded()
        s.bar.displayIfNeeded()
        Quartz.CATransaction.flush()

    def clear(layer) -> None:
        layer.removeAllAnimations()
        for sub in layer.sublayers() or []:
            clear(sub)

    display()
    clear(s.layer())
    if change == "progress":
        p._update(fx.reading(position=SCRUB_TO * 334.0))
    elif change == "hover":
        s.mouseEntered_(None)
    else:  # a drag step: what ScrubBar._move does with the cursor's fraction
        s.bar.set_fraction(SCRUB_TO)
        s.setNeedsDisplay_(True)
    display()
    found: dict = {}
    _collect(s.layer(), found)
    # Showing the knob (hidden to shown) gets an implicit CATransition, a cross-fade from nothing.
    # Its starting picture is taken when the transaction commits, so a re-timed copy cannot be
    # drawn later; the fade is evaluated here instead, on the transition's own duration and curve.
    fade = s.knob.animationForKey_("transition")
    if fade is not None:
        tf = fade.timingFunction()
        pts = NAMED_TIMING["linear"]
        for named, points in NAMED_TIMING.items():
            if tf is not None and tf.isEqual_(Quartz.CAMediaTimingFunction.functionWithName_(named)):
                pts = points
        secs = float(fade.duration())
        s.knob.removeAnimationForKey_("transition")
        found["knob fade"] = {"note": f"CATransition fade, {secs:g} s, curve {pts}: evaluated per frame as the knob's "
                                      "opacity by the harness"}
    rows = []
    for i, t in enumerate(_times(name)):
        if fade is not None:
            Quartz.CATransaction.begin()
            Quartz.CATransaction.setDisableActions_(True)
            s.knob.setOpacity_(bezier(*pts, min(1.0, t / secs)))
            Quartz.CATransaction.commit()
        ages = {}
        stack = [s.layer()]
        while stack:
            layer = stack.pop()
            for k in layer.animationKeys() or []:
                ages[str(k)] = t
            stack.extend(layer.sublayers() or [])
        L.render(s, app, raw / f"{name}-{i}.png", "ca", ages)  # the knob's fade is a CATransition
        fill = s.bar.fill.presentationLayer() or s.bar.fill
        knob = s.knob.presentationLayer() or s.knob
        rows.append({"t": t, "fill_w": round(float(fill.bounds().size.width), 2),
                     "fill_h": round(float(fill.bounds().size.height), 2),
                     "knob_x": round(float(knob.position().x), 2) if not s.knob.isHidden() else None,
                     "knob_alpha": round(float(s.knob.opacity()), 3) if not s.knob.isHidden() else 0.0})
    readback[name] = rows
    return found


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
    readback: dict = {}

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
            elif name in SCRUB:
                anims[name] = qt_scrub(name, mp, raw, readback)
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
        if Q.GUARD is not None:
            Q.GUARD.check(f"motion {name}")
    theme.set_scheme(None)
    (out / "qt_anims.json").write_text(json.dumps(anims, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "qt_scrub.json").write_text(json.dumps(readback, indent=1), encoding="utf-8")


def qt_scrub(name: str, mp, raw: Path, readback: dict) -> dict:
    """One scrub strip on the Qt card, the twin of legacy_scrub: the panel is shown (kept off
    the screen by qt_side.NoScreen, so the bar counts as on screen and may glide), the start
    state is drawn once and let settle, the change goes through the code's own path, and every
    animation it started is paused and stepped to each frame's time."""
    from PySide6.QtCore import QAbstractAnimation, QVariantAnimation
    from PySide6.QtWidgets import QApplication

    from scripts.compare import fixtures as fx
    from scripts.compare import qt_side as Q

    hover, held, change = SCRUB[name]
    mp._update(fx.reading(position=SCRUB_FROM * 334.0))
    mp.panel.show()
    s = mp.scrub
    s.scrubbing = held
    if hover:
        s.set_hover(True)
    s._layout()

    def running() -> list:
        return [a for w in (s, s.bar, s.knob) for a in w.findChildren(QVariantAnimation)
                if a.state() != QAbstractAnimation.State.Stopped]

    Q.render(s, raw / f"{name}-settle.png", False)  # painted once, as on screen
    for a in running():
        a.setCurrentTime(a.duration())
        a.stop()
    s._layout()
    QApplication.processEvents()
    if change == "progress":
        mp._update(fx.reading(position=SCRUB_TO * 334.0))
    elif change == "hover":
        s.set_hover(True)
    else:  # a drag step: what a mouse move does while the bar is held
        s._move(SCRUB_TO * s.width())
    anims = running()
    for a in anims:
        a.pause()
    rows = []
    for i, t in enumerate(_times(name)):
        for a in anims:
            a.setCurrentTime(min(round(t * 1000), a.duration()))
        Q.render(s, raw / f"{name}-{i}.png", False)
        row = {"t": t}
        try:
            row["fill_w"] = round(float(s.bar.fill_size()[0]), 2)
            row["fill_h"] = round(float(s.bar.fill_size()[1]), 2)
            row["knob_x"] = round(float(s._knob_pos.value()[0]), 2) if s.knob.isVisibleTo(s) else None
            row["knob_alpha"] = round(float(s._knob_alpha.value()[0]), 3)
        except Exception as exc:  # the bar's internals changed: the pictures still tell
            row["note"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    readback[name] = rows
    return {f"anim {i}": _qt_info(a) for i, a in enumerate(anims)} or {"note": "no animation started: the change jumps"}


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
     "8.1 s", "drift(t, secs) with EASE_IN_OUT on the same periods", "same: the legacy swing values are Python tuples, "
     "which renderInContext cannot evaluate but Core Animation's own renderer (the window server's) does, so the "
     "harness draws the legacy sliver with CARenderer and the swing is in both pictures"),
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
    if cw > 3 * ch:
        return _strip_sheet_tall(name, out, left, right, cw, ch)
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


def _strip_sheet_tall(name: str, out: Path, left: list, right: list, cw: int, ch: int) -> Path:
    """A wide, thin subject (the scrub bar): one row per frame, today's Mac on the left."""
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    gap, label, head = 16, 110, 60
    W = label + 2 * (cw + gap)
    H = head + FRAMES * (ch + gap)
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(128, 128, 128))
    p = QPainter(img)
    f = QFont("Menlo")
    f.setPixelSize(16)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(10, 24, f"{name}: {STRIPS[name][1]}")
    p.drawText(label, 50, "legacy")
    p.drawText(label + cw + gap, 50, "Qt")
    for i, t in enumerate(_times(name)):
        y = head + i * (ch + gap)
        p.drawText(10, y + ch // 2 + 6, f"t={t:g}s")
        p.drawImage(label, y, left[i])
        p.drawImage(label + cw + gap, y, right[i])
    p.end()
    path = out / f"{name}.png"
    img.save(str(path))
    return path


def progress(name: str, side_raw: Path) -> list[float] | None:
    """How far each frame is from the first towards the last, from the pictures alone: 1 minus
    the summed pixel difference to the last frame over that of the first frame. 0 at the start,
    1 at the end; the same measure on both sides, whatever the animation is."""
    import numpy as np
    from PySide6.QtGui import QImage

    frames = []
    for i in range(FRAMES):
        img = QImage(str(side_raw / f"{name}-{i}.png"))
        if img.isNull():
            return None
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        a = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())
        frames.append(a[:, : img.width() * 4].astype(np.float32))
    total = float(np.abs(frames[0] - frames[-1]).sum())
    if total == 0:
        return [1.0] * FRAMES
    return [round(1 - float(np.abs(f - frames[-1]).sum()) / total, 3) for f in frames]


def scrub_section(mdir: Path) -> list[str]:
    """The scrub bar's read-back: per frame, the fill's width and the knob's centre on both
    sides (logical px), and the pictures' progress (see progress())."""
    def read(p: Path) -> dict:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
    jl, jq = read(mdir / "legacy_scrub.json"), read(mdir / "qt_scrub.json")
    md = ["\n## Scrub bar read-back\n",
          "The playhead changes after the bar was first drawn and settled, through each side's own code path. Each "
          "cell is legacy / Qt, logical px. Fill: the fill's size; knob: the knob's centre (blank while hidden) and "
          "opacity. Progress: from the pictures, 0 at the "
          "first frame and 1 at the last; a jump shows as 1 from the second frame on.\n"]
    for name in SCRUB:
        if name not in jl and name not in jq:
            continue
        pl, pq = progress(name, mdir / "raw" / "legacy"), progress(name, mdir / "raw" / "qt")
        md += [f"### {name}\n\n{STRIPS[name][1]}\n", "| t (s) | fill w (legacy / Qt) | fill h | knob x | knob alpha | "
               "progress |", "|---|---|---|---|---|---|"]

        def both(a: dict, b: dict, key: str) -> str:
            va, vb = a.get(key), b.get(key)
            return f"{'' if va is None else va} / {'' if vb is None else vb}"
        for i, t in enumerate(_times(name)):
            a = (jl.get(name) or [{}] * FRAMES)[i]
            b = (jq.get(name) or [{}] * FRAMES)[i]
            md.append(f"| {t:g} | {both(a, b, 'fill_w')} | {both(a, b, 'fill_h')} | {both(a, b, 'knob_x')} | "
                      f"{both(a, b, 'knob_alpha')} | {pl[i] if pl else ''} / {pq[i] if pq else ''} |")
        md.append("")
    return md


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
    md += scrub_section(mdir)
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
