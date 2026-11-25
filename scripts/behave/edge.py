"""The cursor pressed against the screen edge on the docked sliver during a reading: first held
still, then with small 1 to 2 point jitters along and across the edge. The owner saw the pill
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


def measure(app: LiveApp, out: Path, seed: int = 7, at: str = "middle") -> dict:
    """`at`: "middle" jitters about the middle of the sliver's length; "end" about its top end, so
    the small moves along the edge cross on and off the sliver."""
    pf = app.player_frame()
    if not pf or app.ui().get("dock") != "right":
        return {"error": f"expected the sliver docked right, got {app.ui().get('dock')!r}"}
    x, y, w, h = drive.to_top_left(pf)
    sw, _ = drive.display_size()
    cy = y + h / 2 if at == "middle" else y + 1
    edge_x = sw - 1  # the last column the cursor can reach
    region = (sw - 110, max(0, cy - 160), 110, 320)
    rng = random.Random(seed)
    drive.move(x - 200, cy)
    time.sleep(0.8)
    rec_ = frames.Recorder(region).start()
    time.sleep(0.4)
    t0 = time.time()
    closed_px = None
    drive.glide(x - 200, cy, edge_x, cy, speed=800)
    t_edge = time.time()
    time.sleep(HOLD)
    t_jit = time.time()
    while time.time() - t_jit < JITTER:
        drive.move(edge_x - rng.choice((0, 0, 1, 2)), cy + rng.choice((-2, -1, 1, 2)))
        time.sleep(EVERY)
    t_end = time.time()
    drive.glide(edge_x, cy, x - 200, cy, speed=800)
    time.sleep(1.0)
    rec = rec_.stop()
    before = [f for f in rec.frames if f.t < t0]
    closed_px = before[-1].pixels if before else rec.frames[0].pixels
    # The open picture: the frame with the most change from the closed one while on the edge.
    on_edge = [f for f in rec.frames if t_edge <= f.t <= t_end]
    opened_px = max(on_edge, key=lambda f: frames.changed(f.pixels, closed_px)).pixels if on_edge else closed_px
    frames.filmstrip(rec.frames, t_edge, t_edge + 2.0, out / f"edge-{at}-hold.png", limit=40)
    frames.filmstrip(rec.frames, t_jit, t_jit + 2.0, out / f"edge-{at}-jitter.png", limit=40)
    return {
        "at": at, "edge_x": edge_x, "cursor_y": cy, "sliver": [x, y, w, h],
        "approach": _phase_counts(app, t0, t_edge),
        "hold": {**_phase_counts(app, t_edge, t_jit), "frame_flips": _flips(rec, closed_px, opened_px, t_edge, t_jit)},
        "jitter": {**_phase_counts(app, t_jit, t_end), "frame_flips": _flips(rec, closed_px, opened_px, t_jit, t_end)},
        "capture_rate": round(rec.rate(), 1),
    }
