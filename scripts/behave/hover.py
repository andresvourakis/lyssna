"""Hover timing: the cursor onto a docked form (the reading's sliver, or the resting squiggle) and
off again, measured from the frames and from the app's own log.

What is measured, per trial:
  * cross: when the cursor event that first lands on the form's surface was posted (the frame the
    app's `ui` reply gives, the shadow margin not included);
  * the app's first reaction in its log (legacy: mouseEntered; new: the probe on mouse_entered,
    and before it the tracking area's enter);
  * the first frame that differs from the one before the crossing, when the picture settles on
    the open form, and how many distinct pictures it went through (the animation's cadence);
  * the same on the way out, from the moment the cursor is more than 6 points off the open form
    (the app's own rule for "outside").

The cursor glides as a mouse reports: one event every 8 ms at `speed` points a second, or, in
"jump" mode, one event straight from outside onto the form (no crossing of the window's margin)."""

from __future__ import annotations

import statistics
import time
from itertools import pairwise
from pathlib import Path

from . import drive, frames
from .live import LiveApp

READING = ("This sentence is here so the reader has something to read while the harness measures the player. " * 30).strip()
APPROACH = 220.0     # points from the form's outer edge where each approach starts
DWELL = 1.2          # seconds the cursor stays on the open form
AFTER = 1.0          # seconds recorded after leaving
LEAVE_MARGIN = 6.0   # the app's _mouse_inside margin
QT_MARGIN = 18.0     # the Qt panel's shadow margin around the surface (ui/panel.py MARGIN)

ENTER_MARKERS = {"legacy": ("lyssna.ui.player", "mouseEntered"), "new": ("behave.probe", "probe mouse_entered")}


def start_reading(app: LiveApp, pause: bool = True) -> bool:
    app.send({"cmd": "speak", "text": READING})
    if not app.wait_for(lambda: app.status().get("state") == "playing", 30):
        return False
    if pause:
        time.sleep(0.4)
        app.send({"cmd": "pause"})
        app.wait_for(lambda: app.status().get("state") == "paused", 5)
    time.sleep(0.8)
    return True


def _edge_of(frame: tuple[float, float, float, float], dock: str) -> dict:
    """Where the approach starts and ends and how to read the crossing, for a form on `dock`."""
    x, y, w, h = frame
    sw, sh = drive.display_size()
    if dock == "right":
        cy = y + h / 2
        return {"start": (x - APPROACH, cy), "end": (x + w / 2, cy), "inside": lambda px, py: px >= x,
                "region": (sw - 110, max(0, cy - 160), 110, 320)}
    if dock == "left":
        cy = y + h / 2
        return {"start": (x + w + APPROACH, cy), "end": (x + w / 2, cy), "inside": lambda px, py: px <= x + w,
                "region": (0, max(0, cy - 160), 110, 320)}
    cx = x + w / 2
    return {"start": (cx, y - APPROACH), "end": (cx, y + h / 2), "inside": lambda px, py: py >= y,
            "region": (max(0, cx - 160), sh - 110, 320, 110)}


def _first(trail, test) -> float | None:
    for t, px, py in trail:
        if test(px, py):
            return t
    return None


def _log_times(app: LiveApp, since: float, until: float) -> dict:
    lines = [ln for ln in app.log_lines(since) if ln[0] <= until]
    logger, text = ENTER_MARKERS[app.interface]
    out: dict = {"enter": None, "area_enter": None, "scale_open": None, "scale_close": None, "anim_done": [],
                 "paints": [], "steps": []}
    for t, _level, name, msg in lines:
        if out["enter"] is None and name == logger and msg.startswith(text):
            out["enter"] = t
        if out["area_enter"] is None and name == "behave.probe" and msg.startswith("probe area") and msg.endswith("entered"):
            out["area_enter"] = t
        if msg.startswith("set_scale 1.00 animate=True") and out["scale_open"] is None:
            out["scale_open"] = t
        if msg.startswith("set_scale") and msg != "set_scale 1.00 animate=True" and "animate=True" in msg and out["scale_open"]:
            out["scale_close"] = out["scale_close"] or t
        if msg.startswith("scale animation finished"):
            out["anim_done"].append(t)
        if msg.startswith("probe scale_paint"):
            out["paints"].append(t)
        if msg.startswith("probe scale_step"):
            out["steps"].append(t)
    return out


def _cadence(times: list[float], start: float | None, end: float | None) -> dict:
    if start is None or end is None:
        return {}
    ts = [t for t in times if start <= t <= end]
    gaps = [b - a for a, b in pairwise(ts)]
    if not gaps:
        return {"count": len(ts)}
    return {"count": len(ts), "median_gap_ms": round(statistics.median(gaps) * 1000, 1),
            "max_gap_ms": round(max(gaps) * 1000, 1), "span_ms": round((ts[-1] - ts[0]) * 1000, 1)}


def _ms(t: float | None, ref: float | None) -> float | None:
    return None if t is None or ref is None else round((t - ref) * 1000, 1)


def measure(app: LiveApp, out: Path, dock: str, trials: int = 5, mode: str = "glide", speed: float = 800.0,
            label: str = "sliver") -> dict:
    """Hover the form now on screen `trials` times; returns every trial and the medians."""
    pf = app.player_frame()
    if not pf:
        return {"error": "no player on screen"}
    frame = drive.to_top_left(pf)
    geo = _edge_of(frame, dock)
    margin = QT_MARGIN if app.interface == "new" else 0.0
    x, y, w, _h = frame
    window_test = {"right": lambda px, py: px >= x - margin, "left": lambda px, py: px <= x + w + margin,
                   "bottom": lambda px, py: py >= y - margin}[dock]
    results = []
    for n in range(1, trials + 1):
        drive.move(*geo["start"])
        time.sleep(0.9)
        rec = frames.Recorder(geo["region"]).start()
        time.sleep(0.4)
        if mode == "jump":
            ex, ey = geo["end"]
            t = drive.move(ex, ey)
            trail = [(t, ex, ey)]
        else:
            trail = drive.glide(*geo["start"], *geo["end"], speed=speed)
        cross = _first(trail, geo["inside"])
        on_window = _first(trail, window_test)
        time.sleep(DWELL * 0.5)
        opened = app.player_frame()
        time.sleep(DWELL * 0.5)
        if opened:
            ox, oy, ow, _oh = drive.to_top_left(opened)
        else:
            ox, oy, ow, _oh = frame
        outside = {"right": lambda px, py, ox=ox: px < ox - LEAVE_MARGIN,
                   "left": lambda px, py, ox=ox, ow=ow: px > ox + ow + LEAVE_MARGIN,
                   "bottom": lambda px, py, oy=oy: py < oy - LEAVE_MARGIN}[dock]
        back = drive.glide(*geo["end"], *geo["start"], speed=speed)
        left = _first(back, outside)
        time.sleep(AFTER)
        rec = rec.stop()
        before = [f for f in rec.frames if f.t < (on_window or cross or trail[0][0])]
        open_frames = [f for f in rec.frames if left and f.t < back[0][0]]
        if not before or not open_frames or cross is None or left is None:
            results.append({"trial": n, "error": "too few frames or no crossing", "frames": len(rec.frames)})
            continue
        baseline, open_final, closed_final = before[-1].pixels, open_frames[-1].pixels, rec.frames[-1].pixels
        opening = frames.motion(rec.frames, cross, baseline, open_final, until=back[0][0])
        closing = frames.motion(rec.frames, left, open_final, closed_final)
        logt = _log_times(app, trail[0][0] - 0.05, rec.frames[-1].t)
        trial = {
            "trial": n, "capture_rate": round(rec.rate(), 1), "capture_max_gap_ms": round(rec.max_gap() * 1000, 1),
            "window_to_cross_ms": _ms(cross, on_window), "open": opening, "close": closing,
            "log_area_enter_ms": _ms(logt["area_enter"], cross), "log_enter_ms": _ms(logt["enter"], cross),
            "log_scale_open_ms": _ms(logt["scale_open"], cross), "log_scale_close_ms": _ms(logt["scale_close"], left),
            "open_frame": opened,
            "paint_open": _cadence(logt["paints"], logt["scale_open"], (logt["scale_open"] or 0) + 0.4),
            "paint_close": _cadence(logt["paints"], logt["scale_close"], (logt["scale_close"] or 0) + 0.4),
            "steps_open": _cadence(logt["steps"], logt["scale_open"], (logt["scale_open"] or 0) + 0.4),
        }
        results.append(trial)
        if n == 1:
            frames.filmstrip(rec.frames, cross - 0.03, cross + 0.45, out / f"{label}-{mode}-open.png")
            frames.filmstrip(rec.frames, left - 0.03, left + 0.5, out / f"{label}-{mode}-close.png")
    return {"mode": mode, "speed": speed, "dock": dock, "trials": results, "summary": summarise(results)}


def summarise(trials: list[dict]) -> dict:
    def med(path):
        vals = []
        for t in trials:
            v = t
            for p in path:
                v = v.get(p) if isinstance(v, dict) else None
            if isinstance(v, (int, float)):
                vals.append(v)
        return {"median": round(statistics.median(vals), 1), "min": min(vals), "max": max(vals), "n": len(vals)} if vals else None

    return {
        "open_first_change_ms": med(("open", "first_change_ms")),
        "open_settled_ms": med(("open", "settled_ms")),
        "open_animation_ms": med(("open", "animation_ms")),
        "open_frames_moved": med(("open", "frames_moved")),
        "open_repeated_frames": med(("open", "repeated_frames")),
        "open_largest_step_gap_ms": med(("open", "largest_step_gap_ms")),
        "close_first_change_ms": med(("close", "first_change_ms")),
        "close_settled_ms": med(("close", "settled_ms")),
        "close_animation_ms": med(("close", "animation_ms")),
        "close_frames_moved": med(("close", "frames_moved")),
        "log_area_enter_ms": med(("log_area_enter_ms",)),
        "log_enter_ms": med(("log_enter_ms",)),
        "log_scale_open_ms": med(("log_scale_open_ms",)),
        "log_scale_close_ms": med(("log_scale_close_ms",)),
        "window_to_cross_ms": med(("window_to_cross_ms",)),
        "paint_open_median_gap_ms": med(("paint_open", "median_gap_ms")),
        "paint_open_max_gap_ms": med(("paint_open", "max_gap_ms")),
        "paint_open_count": med(("paint_open", "count")),
        "capture_rate": med(("capture_rate",)),
    }
