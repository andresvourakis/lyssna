"""The cursor pressed against the screen edge on the docked sliver during a reading (on the left,
right or bottom dock): first held still, then with small 1 to 2 point jitters along and across
the edge. On the bottom dock the cursor is at the screen's last row, where an auto-hidden Dock
may slide up while it is held there (the frames show it; the counts come from the log). The owner saw the pill
"glitching, maximizing and minimizing fast" there. Counts the open and close cycles from the app's
log (each animated set_scale) and from the frames (how often the region flips between the open
and the closed picture)."""

from __future__ import annotations

import random
import time
from pathlib import Path

from . import drive, frames
from .live import LiveApp

HOLD = 2.0      # seconds held still against the edge
JITTER = 4.0    # seconds of jitter
EVERY = 0.05    # seconds between jitter moves


def _phase_counts(app: LiveApp, since: float, until: float) -> dict:
    lines = [ln for ln in app.log_lines(since) if ln[0] <= until]
    opens = [t for t, _l, _n, m in lines if m.startswith("set_scale 1.00 animate=True")]
    closes = [t for t, _l, _n, m in lines if m.startswith("set_scale") and "animate=True" in m and not m.startswith("set_scale 1.00")]
    enters = [t for t, _l, _n, m in lines if m.startswith(("mouse_entered", "mouseEntered"))]
    return {"opens": len(opens), "closes": len(closes), "enter_events": len(enters),
            "opens_per_s": round(len(opens) / max(0.001, until - since), 2)}


def _flips(rec: frames.Recording, closed, opened, start: float, end: float) -> int:
    """How many times the picture went from looking closed to looking open or back."""
    state, flips = None, 0
    for f in rec.frames:
        if not start <= f.t <= end:
            continue
        to_closed, to_open = frames.changed(f.pixels, closed), frames.changed(f.pixels, opened)
        now = "closed" if to_closed <= frames.CHANGED_PIXELS else "open" if to_open <= frames.CHANGED_PIXELS else state
        if state is not None and now != state:
            flips += 1
        state = now
    return flips


def _geometry(dock: str, sliver: tuple[float, float, float, float], at: str) -> dict:
    """For a sliver on `dock` (top-left points): the start of the approach, the spot pressed
    against the edge, a jitter move from there, and the region recorded."""
    x, y, w, h = sliver
    sw, sh = drive.display_size()
    if dock in ("left", "right"):
        cy = y + h / 2 if at == "middle" else y + 1  # "end": its top end
        edge_x = sw - 1 if dock == "right" else 0.0  # the last column the cursor can reach
        away = x - 200 if dock == "right" else x + w + 200
        inward = -1 if dock == "right" else 1

        def jitter(rng):
            return edge_x + inward * rng.choice((0, 0, 1, 2)), cy + rng.choice((-2, -1, 1, 2))
        return {"start": (away, cy), "edge": (edge_x, cy), "jitter": jitter, "cursor": cy,
                "region": (sw - 110 if dock == "right" else 0.0, max(0, cy - 160), 110, 320)}
    cx = x + w / 2 if at == "middle" else x + 1  # "end": its left end
    edge_y = sh - 1

    def jitter_flat(rng):
        return cx + rng.choice((-2, -1, 1, 2)), edge_y - rng.choice((0, 0, 1, 2))
    return {"start": (cx, y - 200), "edge": (cx, edge_y), "jitter": jitter_flat, "cursor": cx,
            "region": (max(0, cx - 160), sh - 110, 320, 110)}


def measure(app: LiveApp, out: Path, seed: int = 7, at: str = "middle", dock: str = "right") -> dict:
    """`at`: "middle" jitters about the middle of the sliver's length; "end" about its top end
    (on the bottom dock its left end), so the small moves along the edge cross on and off the
    sliver."""
    pf = app.player_frame()
    if not pf or app.ui().get("dock") != dock:
        return {"error": f"expected the sliver docked {dock}, got {app.ui().get('dock')!r}"}
    sliver = drive.to_top_left(pf)
    geo = _geometry(dock, sliver, at)
    rng = random.Random(seed)
    drive.move(*geo["start"])
    time.sleep(0.8)
    rec_ = frames.Recorder(geo["region"]).start()
    time.sleep(0.4)
    t0 = time.time()
    drive.glide(*geo["start"], *geo["edge"], speed=800)
    t_edge = time.time()
    time.sleep(HOLD)
    t_jit = time.time()
    while time.time() - t_jit < JITTER:
        drive.move(*geo["jitter"](rng))
        time.sleep(EVERY)
    t_end = time.time()
    drive.glide(*geo["edge"], *geo["start"], speed=800)
    time.sleep(1.0)
    rec = rec_.stop()
    before = [f for f in rec.frames if f.t < t0]
    closed_px = before[-1].pixels if before else rec.frames[0].pixels
    # The open picture: the frame with the most change from the closed one while on the edge.
    on_edge = [f for f in rec.frames if t_edge <= f.t <= t_end]
    opened_px = max(on_edge, key=lambda f: frames.changed(f.pixels, closed_px)).pixels if on_edge else closed_px
    frames.filmstrip(rec.frames, t_edge, t_edge + 2.0, out / f"edge-{dock}-{at}-hold.png", limit=40)
    frames.filmstrip(rec.frames, t_jit, t_jit + 2.0, out / f"edge-{dock}-{at}-jitter.png", limit=40)
    return {
        "at": at, "dock": dock, "edge": list(geo["edge"]), "cursor_along": geo["cursor"], "sliver": list(sliver),
        "approach": _phase_counts(app, t0, t_edge),
        "hold": {**_phase_counts(app, t_edge, t_jit), "frame_flips": _flips(rec, closed_px, opened_px, t_edge, t_jit)},
        "jitter": {**_phase_counts(app, t_jit, t_end), "frame_flips": _flips(rec, closed_px, opened_px, t_jit, t_end)},
        "capture_rate": round(rec.rate(), 1),
    }
