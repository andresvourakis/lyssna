"""The player scenarios on every dock (left, right, bottom), and two selections in quick
succession.

  * hover on the reading's sliver and on the resting squiggle (hover.measure, which already
    takes the dock);
  * the open pill's buttons on each dock: play/pause, next, previous, expand, and stop. On the
    bottom edge the pill lies flat and its buttons read left to right in the order they read
    top to bottom upright (lay(..., flip=True) in lyssna/ui/layout/pill.py);
  * auto-hide on each dock: the cursor coming near slides the squiggle in, leaving slides it out;
  * a dictation on each dock (real models, recorded sentence): the dictation form's place, the
    toast beside it, Escape's toast with Undo;
  * two selections in quick succession: the play button moves to the second word, and reads it."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from lyssna.ui.layout.pill import MENU_DISC

from . import dictate, drive, feed, frames, hover
from .live import LiveApp
from .scenarios import OPEN_PILL, _state, _tl, _wait_state, _word_rect, shoot_rect, windows_of

NEAR = 60.0  # points from the edge the auto-hide approach ends at


def _approach(frame, dock: str, far: float = hover.APPROACH):
    """(start, end) of an approach onto the middle of `frame` from inside the screen."""
    x, y, w, h = frame
    if dock == "right":
        return (x - far, y + h / 2), (x + w / 2, y + h / 2)
    if dock == "left":
        return (x + w + far, y + h / 2), (x + w / 2, y + h / 2)
    return (x + w / 2, y - far), (x + w / 2, y + h / 2)


def hover_dock(dock: str):
    def run(app: LiveApp, out: Path, trials: int) -> dict:
        if not hover.start_reading(app, pause=True):
            return {"error": "the reading did not start"}
        glide = hover.measure(app, out, dock, trials=trials, mode="glide")
        return {"glide": glide["summary"], "trials": glide["trials"], "sliver": app.player_frame()}
    return run


def rest_dock(dock: str):
    def run(app: LiveApp, out: Path, trials: int) -> dict:
        rest = app.player_frame()
        if not rest:
            return {"error": "no resting squiggle on screen"}
        res: dict = {"rest_frame": rest, "hover": hover.measure(app, out, dock, trials=4, label="rest")["summary"]}
        start, end = _approach(_tl(rest), dock)
        drive.glide(*start, *end)
        time.sleep(0.5)
        menu = app.player_frame()
        res["menu_frame"] = menu
        if menu:
            mx, my, mw, mh = _tl(menu)
            shoot_rect((mx - 60, my - 60, mw + 120, mh + 120), out / "menu-open.png")
            # The tip of the History disc (the menu's first item): when it shows, and where.
            flat = mw > mh
            disc = (mx + MENU_DISC / 2, my + mh / 2) if flat else (mx + mw / 2, my + MENU_DISC / 2)
            before = windows_of(app.pid)
            t = drive.glide(*drive.cursor(), *disc, speed=300)[-1][0]
            tip = app.wait_for(lambda: [w for w in windows_of(app.pid) if w["id"] not in {b["id"] for b in before}
                                        and w["layer"] > 0 and w["bounds"][3] < 80], 2.0, every=0.01)
            res["tip_ms"] = round((time.time() - t) * 1000) if tip else None
            res["tip"] = tip[0] if tip else None
            end = disc
        drive.glide(*end, *start)
        res["menu_closed_after_leaving"] = bool(app.wait_for(lambda: (app.player_frame() or {}).get("h", 0) < 80
                                                             and (app.player_frame() or {}).get("w", 0) < 80, 3.0))
        return res
    return run


def pill_dock(dock: str):
    """The open pill's buttons, on `dock`."""
    def run(app: LiveApp, out: Path, trials: int) -> dict:
        if not hover.start_reading(app, pause=False):
            return {"error": "the reading did not start"}
        sliver = _tl(app.player_frame())
        start, end = _approach(sliver, dock)
        res: dict = {"sliver": app.player_frame()}

        def open_pill():
            drive.glide(*start, *end)
            time.sleep(0.45)
            return _tl(app.player_frame())

        px, py, pw, ph = open_pill()
        res["pill"] = [px, py, pw, ph]
        shoot_rect((px - 30, py - 30, pw + 60, ph + 60), out / "pill-open.png")
        flat = pw > ph

        def tap(name):
            if flat:
                return drive.click(px + OPEN_PILL[name], py + ph / 2)
            return drive.click(px + pw / 2, py + OPEN_PILL[name])

        tap("toggle")
        res["pause"], res["pause_ms"] = _wait_state(app, lambda s: s["state"] == "paused")
        tap("toggle")
        res["resume"], res["resume_ms"] = _wait_state(app, lambda s: s["state"] in ("playing", "loading"))
        i0 = _state(app)["index"]
        tap("forward")
        res["next"], res["next_ms"] = _wait_state(app, lambda s: (s["index"] or 0) > (i0 or 0))
        i1 = _state(app)["index"]
        tap("back")
        res["prev"], res["prev_ms"] = _wait_state(app, lambda s: (s["index"] or 99) < (i1 or 0))
        res["pill_still_open"] = max((app.player_frame() or {}).get("h", 0), (app.player_frame() or {}).get("w", 0)) > 100
        tap("expand")
        res["expand"], res["expand_ms"] = _wait_state(app, lambda s: s["dock"] == "")
        res["card"] = app.player_frame()
        time.sleep(0.5)
        app.send({"cmd": "stop"})
        time.sleep(0.5)
        drive.move(*start)
        return res
    return run


def autohide_dock(dock: str):
    """Idle with auto-hide on, the squiggle on `dock`."""
    def run(app: LiveApp, out: Path, trials: int) -> dict:
        sw, sh = drive.display_size()
        if dock == "right":
            far, near, region = (sw - 600, sh / 2), (sw - NEAR, sh / 2), (sw - 110, sh / 2 - 160, 110, 320)
            reached = lambda px, py: px >= sw - 26 - 100  # noqa: E731
        elif dock == "left":
            far, near, region = (600, sh / 2), (NEAR, sh / 2), (0, sh / 2 - 160, 110, 320)
            reached = lambda px, py: px <= 26 + 100  # noqa: E731
        else:
            far, near, region = (sw / 2, sh - 600), (sw / 2, sh - NEAR), (sw / 2 - 160, sh - hover.BOTTOM_BAND, 320, hover.BOTTOM_BAND)
            reached = lambda px, py: py >= sh - 26 - 100  # noqa: E731
        drive.move(*far)
        time.sleep(3.0)
        res: dict = {"hidden_frame": app.player_frame()}
        rec = frames.Recorder(region).start()
        time.sleep(0.4)
        trail = drive.glide(*far, *near, speed=600)
        reach = next((t for t, px, py in trail if reached(px, py)), None)
        time.sleep(1.2)
        res["revealed_frame"] = app.player_frame()
        back = drive.glide(*near, *far, speed=600)
        left = next((t for t, px, py in back if not reached(px, py)), None)
        time.sleep(1.5)
        r = rec.stop()
        before = [f for f in r.frames if f.t < trail[0][0]]
        shown = [f for f in r.frames if f.t < back[0][0]]
        if before and shown and reach and left:
            res["reveal"] = frames.motion(r.frames, reach, before[-1].pixels, shown[-1].pixels, until=back[0][0])
            res["hide"] = frames.motion(r.frames, left, shown[-1].pixels, r.frames[-1].pixels)
            frames.filmstrip(r.frames, reach, reach + 0.5, out / "autohide-reveal.png")
            frames.filmstrip(r.frames, left, left + 1.0, out / "autohide-hide.png")
        res["capture_rate"] = round(r.rate(), 1)
        return res
    return run


def dictation_dock(app: LiveApp, out: Path, register_cleanup: Callable | None = None) -> dict:
    """A dictation with the idle pill on this run's dock: hold to talk (the form's place while
    recording and working, the text landing), then hands-free and Escape (the toast beside the
    pill, with Undo, left to time out)."""
    wav = out / "sentence.wav"
    seconds = feed.make_sentence(dictate.SENTENCE, wav)
    doc = dictate.Document(out, register_cleanup)
    try:
        why = doc.open(app)
        if why:
            return {"error": why}
        sw, sh = drive.display_size()
        drive.move(sw / 2, sh / 2)
        app.ui()
        res: dict = {"idle_frame": app.player_frame(), "dock": app.ui().get("dock")}
        res["hold"] = dictate.hold_to_talk(app, doc, wav, seconds + 0.6, out)
        doc.caret_to_end()
        app.wait_for(lambda: dictate.dstate(app).get("state") == "idle", 10.0)
        time.sleep(0.4)
        res["escape"] = dictate.escape_and_undo(app, doc, wav, 1.0, out, undo=False)
        res["document"] = doc.text()
        return res
    finally:
        doc.close()


TWICE_TEXT = "First word here and then another one over there.\n"


def selection_twice(app: LiveApp, out: Path, register_cleanup: Callable | None = None) -> dict:
    """Double-click one word, then within half a second another: the play button follows to the
    second; a click on it reads the second word."""
    doc = dictate.Document(out, register_cleanup, content=TWICE_TEXT, name="selection-twice.txt")
    try:
        why = doc.open(app)
        if why:
            return {"error": why}
        first = _word_rect(doc.area, TWICE_TEXT.index("First"), 5)
        second = _word_rect(doc.area, TWICE_TEXT.index("another"), 7)
        if not first or not second:
            return {"error": "no word rects"}
        app.ui()
        res: dict = {}
        drive.click(first[0] + first[2] / 2, first[1] + first[3] / 2, clicks=2)
        b1 = app.wait_for(lambda: app.ui().get("selection_button"), 3.0, every=0.03)
        res["first_button"] = b1
        time.sleep(0.3)
        t = drive.click(second[0] + second[2] / 2, second[1] + second[3] / 2, clicks=2)
        moved = app.wait_for(lambda: (lambda b: b if b and b1 and abs(b["x"] - b1["x"]) > 20 else None)(app.ui().get("selection_button")),
                             3.0, every=0.03)
        res["second_button"] = moved or app.ui().get("selection_button")
        res["moved_ms"] = round((time.time() - t) * 1000) if moved else None
        res["buttons_on_screen"] = [w for w in windows_of(app.pid) if w["layer"] > 0 and w["bounds"][2] <= 80 and w["bounds"][3] <= 80]
        shoot_rect((first[0] - 60, first[1] - 80, second[0] - first[0] + 260, 200), out / "selection-twice.png")
        got = res["second_button"]
        if got:
            bx, by, bw, bh = drive.to_top_left(got)
            drive.click(bx + bw / 2, by + bh / 2)
            st = app.wait_for(lambda: (lambda s: s if s.get("state") in ("playing", "loading") else None)(app.status()), 8.0)
            res["reads"] = (st or {}).get("text", "")[:60] if st else None
            app.send({"cmd": "stop"})
            time.sleep(0.5)
        return res
    finally:
        doc.close()
