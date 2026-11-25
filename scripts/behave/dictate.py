"""Dictation end to end, on a profile with the real speech and cleanup models and a recorded
sentence in place of the microphone (feed.py): the dictation key held (right Option in the test
profile, see below), hands-free with Space, Escape and the toast's Undo, a recording of silence, a
tap, a failed transcription and the toast's Retry, Paste Last Dictation, and the pill's own
buttons. What lands in the harness's TextEdit document and in the profile's History is read
after every step; the pill's form and the toasts are recorded on the way.

The test profile's dictation key is right Option, not fn: a synthetic fn press could reach the
system's own Globe key action (emoji picker, macOS dictation). The key's rules are shared code
(lyssna/mac/pushtotalk.py), the same for every trigger.

Safety: the text is only ever pasted into the harness's own document. Before every step that
pastes, the document must be in front; when it is not, the recording is cancelled quietly and
the step is marked skipped."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from lyssna.ui.layout.pill import BUTTON_HIT, CANCEL_D, CANCEL_TOP, FINISH_BOTTOM, FINISH_D
from lyssna.ui.layout.toast import ACTION_RIGHT, TOAST_H

from . import ax, drive, feed
from .live import LiveApp
from .machine import front_app
from .scenarios import _descendants, _textedit_pid, _tl, new_windows, shoot_rect, windows_of

SENTENCE = "Please send the quarterly report to Maria before Friday."
SECOND = "The meeting moved to Thursday at three."
DOC_TEXT = "Dictation test document.\n"
RIGHT_OPTION = 0x3D
OPTION_DOWN = 0x00080000 | 0x00000040  # the option flag and the right-hand device bit
DONE_WAIT = 40.0  # the first dictation loads both models


# -- the document ---------------------------------------------------------------------------------


class Document:
    """A TextEdit document of the harness's own (TextEdit must not be running already)."""

    def __init__(self, out: Path, register_cleanup: Callable[[Callable[[], None]], None] | None,
                 content: str = DOC_TEXT, name: str = "dictation-test.txt"):
        self.path = out / name
        self.content = content
        self.pid = 0
        self.area = None
        self._register = register_cleanup

    def open(self, app: LiveApp) -> str:
        """"" when open and in front, else why not."""
        if _textedit_pid():
            return "TextEdit was already running; the harness does not touch it"
        self.path.write_text(self.content)
        subprocess.run(["open", "-a", "TextEdit", str(self.path)], check=False)
        if self._register:
            self._register(self.close)
        self.pid = app.wait_for(_textedit_pid, 10.0) or 0
        win = app.wait_for(lambda: [w for w in ax.windows(self.pid) if self.path.name in str(ax.attr(w, "AXTitle") or "")], 10.0)
        if not win:
            return "the test document did not open in TextEdit"
        time.sleep(1.0)
        areas = [e for e in _descendants(win[0]) if ax.attr(e, "AXRole") == "AXTextArea"]
        if not areas:
            return "no text area in the test document"
        self.area = areas[0]
        if not self.in_front():
            from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

            running = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
            if running is not None:
                running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(0.6)
        if not self.in_front():
            pos = ax.attr(win[0], "AXPosition")
            if pos is not None:  # click the document's own title bar
                import ApplicationServices as AXS

                ok, p = AXS.AXValueGetValue(pos, AXS.kAXValueCGPointType, None)
                if ok:
                    drive.click(p.x + 200, p.y + 12)
                    time.sleep(0.6)
        if not self.in_front():
            return f"the test document is not in front ({front_app()[1]})"
        self.caret_to_end()
        return ""

    def in_front(self) -> bool:
        return front_app()[1] == "TextEdit"

    def caret_to_end(self) -> None:
        if self.in_front():
            drive.key("down", ("command",))
            time.sleep(0.1)

    def text(self) -> str:
        return str(ax.attr(self.area, "AXValue") or "") if self.area is not None else ""

    def close(self) -> None:
        """Without AppleScript (see scenarios.selection); the document is the harness's own, so
        its unsaved text may go."""
        from AppKit import NSRunningApplication

        for running in NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.TextEdit"):
            running.forceTerminate()
        deadline = time.monotonic() + 5
        while _textedit_pid() and time.monotonic() < deadline:
            time.sleep(0.2)


# -- the key and what the app shows -------------------------------------------------------------------


def option(down: bool) -> float:
    import Quartz

    ev = Quartz.CGEventCreateKeyboardEvent(drive._source(), RIGHT_OPTION, down)
    Quartz.CGEventSetFlags(ev, OPTION_DOWN if down else 0)
    t = time.time()
    Quartz.CGEventPost(drive.HID, ev)
    (drive._held_keys.add if down else drive._held_keys.discard)(RIGHT_OPTION)
    return t


def dstate(app: LiveApp) -> dict:
    return app.status().get("dictation", {}) or {}


class Watch:
    """Samples the dictation state, the pill's frame and the app's small windows (toasts, tips)
    about every 40 ms; keeps the changes, with ms from `t0`."""

    def __init__(self, app: LiveApp, t0: float):
        self.app, self.t0 = app, t0
        self.timeline: list[dict] = []
        self.toasts: list[dict] = []
        self._last = None
        self._base = [w["id"] for w in windows_of(app.pid)]

    def sample(self) -> dict:
        st = dstate(self.app)
        frame = self.app.player_frame()
        now = round((time.time() - self.t0) * 1000)
        key = (st.get("state"), st.get("message"), bool(st.get("can_undo")), bool(st.get("can_retry")),
               (round(frame["w"]), round(frame["h"])) if frame else None)
        if key != self._last:
            self._last = key
            self.timeline.append({"ms": now, "state": key[0], "message": key[1], "undo": key[2], "retry": key[3],
                                  "form": list(key[4]) if key[4] else None})
        for w in windows_of(self.app.pid):
            if w["id"] in self._base or w["layer"] < 1 or w["bounds"][3] > 120 or w["bounds"][2] < 60:
                continue
            if not [t for t in self.toasts if t["id"] == w["id"]]:
                self.toasts.append({**w, "first_ms": now})
            for t in self.toasts:
                if t["id"] == w["id"]:
                    t["last_ms"] = now
        return st

    def until(self, test: Callable[[dict], bool], timeout: float) -> int | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if test(self.sample()):
                return round((time.time() - self.t0) * 1000)
            time.sleep(0.04)
        return None

    def run_for(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.sample()
            time.sleep(0.04)

    def first(self, state: str) -> int | None:
        return next((e["ms"] for e in self.timeline if e["state"] == state), None)


def history(app: LiveApp) -> list[dict]:
    path = app.home / "state" / "dictation" / "history.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [{k: e.get(k) for k in ("text", "raw", "status", "words", "cleaner", "transcriber")} for e in data.get("entries", [])]


def toast_action_point(toast: dict) -> tuple[float, float]:
    """The middle of a toast's action pill (Undo, Retry), from the shared toast layout; the
    window may carry a shadow margin around the drawn toast, the same on every side."""
    x, y, w, h = toast["bounds"]
    margin = max(0.0, (h - TOAST_H) / 2)
    return x + w - margin - ACTION_RIGHT - 24, y + h / 2


def _appended(doc: Document, before: str) -> str:
    now = doc.text()
    return now[len(before):] if now.startswith(before) else now


# -- the steps --------------------------------------------------------------------------------------


def _hold(app: LiveApp, doc: Document, wav: Path | None, seconds: float, shoot: Path | None = None) -> dict:
    """Hold the dictation key for `seconds` while `wav` (or silence) is heard, let go, and follow
    the dictation until it is over."""
    feed.say_next(app.mic, wav)
    before = doc.text()
    hist0 = len(history(app))
    t0 = option(True)
    w = Watch(app, t0)
    shot_at = t0 + min(1.0, seconds / 2)
    while time.time() < t0 + seconds:
        w.sample()
        if shoot and time.time() >= shot_at:
            frame = app.player_frame()
            if frame:
                x, y, fw, fh = _tl(frame)
                shoot_rect((x - 140, y - 60, fw + 180, fh + 120), shoot)
            shoot = None
        time.sleep(0.04)
    if not doc.in_front():
        app.send({"cmd": "dictate", "value": "cancel"})
        option(False)
        return {"skipped": f"{front_app()[1]} came in front; cancelled"}
    t_up = option(False)
    up_ms = round((t_up - t0) * 1000)
    done = w.until(lambda s: s.get("state") in ("idle", "notice"), DONE_WAIT)
    landed = app.wait_for(lambda: _appended(doc, before).strip() or None, 2.0, every=0.05)
    landed_ms = round((time.time() - t0) * 1000) - up_ms if landed else None
    w.run_for(0.6)
    entries = history(app)
    return {"released_ms": up_ms, "recording_ms": w.first("recording"), "working_after_release_ms": _after(w.first("working"), up_ms),
            "over_after_release_ms": _after(done, up_ms), "text_after_release_ms": landed_ms,
            "typed": _appended(doc, before), "history_added": entries[:max(0, len(entries) - hist0)],
            "timeline": w.timeline, "toasts": w.toasts}


def _after(ms: int | None, base: int) -> int | None:
    return None if ms is None else ms - base


def hold_to_talk(app: LiveApp, doc: Document, wav: Path, seconds: float, out: Path) -> dict:
    return _hold(app, doc, wav, seconds, shoot=out / "dictate-holding.png")


def hands_free(app: LiveApp, doc: Document, wav: Path, seconds: float, out: Path) -> dict:
    """Hold the key, press Space (locked), let go of the key; the sentence plays; press the key
    again to finish."""
    feed.say_next(app.mic, wav)
    before = doc.text()
    t0 = option(True)
    w = Watch(app, t0)
    w.run_for(0.4)
    drive.key("space")
    w.run_for(0.2)
    option(False)
    locked = w.until(lambda s: s.get("state") == "locked", 1.0)
    w.run_for(0.5)
    frame = app.player_frame()
    res: dict = {"locked_ms": locked, "locked_form": frame, "space_typed": " " in _appended(doc, before)}
    if frame:
        x, y, fw, fh = _tl(frame)
        shoot_rect((x - 140, y - 60, fw + 180, fh + 120), out / "dictate-hands-free.png")
    w.run_for(max(0.0, seconds - 1.1))
    still = dstate(app).get("state")
    res["still_locked_after_release"] = still == "locked"
    if not doc.in_front():
        app.send({"cmd": "dictate", "value": "cancel"})
        return {**res, "skipped": f"{front_app()[1]} came in front; cancelled"}
    t1 = option(True)
    time.sleep(0.05)
    option(False)
    base = round((t1 - t0) * 1000)
    done = w.until(lambda s: s.get("state") in ("idle", "notice"), DONE_WAIT)
    landed = app.wait_for(lambda: _appended(doc, before).strip() or None, 2.0, every=0.05)
    w.run_for(0.4)
    res.update({"over_after_key_ms": _after(done, base), "typed": _appended(doc, before), "landed": bool(landed),
                "timeline": w.timeline, "toasts": w.toasts})
    return res


def escape_and_undo(app: LiveApp, doc: Document, wav: Path, seconds: float, out: Path, undo: bool) -> dict:
    """Hands-free, then Escape: the toast "Transcript cancelled" with Undo. With `undo`, its
    Undo is clicked and the text lands after all; without, it is left to time out."""
    feed.say_next(app.mic, wav)
    before = doc.text()
    t0 = option(True)
    w = Watch(app, t0)
    w.run_for(0.3)
    drive.key("space")
    option(False)
    w.run_for(seconds)
    t_esc = drive.key("escape")
    base = round((t_esc - t0) * 1000)
    toast = app.wait_for(lambda: (w.sample() and None) or (w.toasts[-1] if w.toasts else None), 3.0, every=0.03)
    res: dict = {"notice": dstate(app), "toast": toast, "toast_after_escape_ms": _after(toast["first_ms"], base) if toast else None,
                 "escape_typed": "\x1b" in _appended(doc, before)}
    if toast:
        bx, by, bw, bh = toast["bounds"]
        shoot_rect((bx - 30, by - 30, bw + 60, bh + 60), out / f"toast-cancelled{'-undo' if undo else ''}.png")
    if undo and toast and doc.in_front():
        t_undo = drive.click(*toast_action_point(toast))
        ubase = round((t_undo - t0) * 1000)
        done = w.until(lambda s: s.get("state") == "idle", DONE_WAIT)
        landed = app.wait_for(lambda: _appended(doc, before).strip() or None, 2.0, every=0.05)
        res.update({"undo_over_after_click_ms": _after(done, ubase), "typed": _appended(doc, before), "landed": bool(landed)})
        doc.caret_to_end()
    else:
        gone = w.until(lambda s: s.get("state") == "idle", 10.0)
        w.run_for(0.8)
        res.update({"idle_after_escape_ms": _after(gone, base), "typed": _appended(doc, before)})
    w.run_for(0.3)
    res.update({"timeline": w.timeline, "toasts": w.toasts})
    return res


def failed_and_retry(app: LiveApp, doc: Document, wav: Path, seconds: float, out: Path) -> dict:
    """The next transcription fails (feed.py's marker): the toast says so and offers Retry, History
    keeps a failed row; the toast's Retry runs it again and pastes."""
    (app.mic / "fail-next").write_text("")
    res = _hold(app, doc, wav, seconds)
    toast = (res.get("toasts") or [None])[-1]
    res["history_after_failure"] = history(app)[:2]
    if toast:
        bx, by, bw, bh = toast["bounds"]
        live = [x for x in windows_of(app.pid) if x["id"] == toast["id"]]
        res["toast_still_up"] = bool(live)
        shoot_rect((bx - 30, by - 30, bw + 60, bh + 60), out / "toast-failed.png")
        if live and doc.in_front():
            before = doc.text()
            t = drive.click(*toast_action_point(toast))
            w = Watch(app, t)
            done = w.until(lambda s: s.get("state") in ("idle", "notice") and w.first("working") is not None, DONE_WAIT)
            landed = app.wait_for(lambda: _appended(doc, before).strip() or None, 3.0, every=0.05)
            w.run_for(0.5)
            res["retry"] = {"over_after_click_ms": done, "typed": _appended(doc, before), "landed": bool(landed),
                            "timeline": w.timeline, "history": history(app)[:2]}
            doc.caret_to_end()
    return res


def paste_last(app: LiveApp, doc: Document) -> dict:
    """Control-Option-V pastes the last dictation again."""
    if not doc.in_front():
        return {"skipped": f"{front_app()[1]} was in front"}
    before = doc.text()
    t = drive.chord("v", ("control", "option"))
    landed = app.wait_for(lambda: _appended(doc, before).strip() or None, 4.0, every=0.03)
    return {"ms": round((time.time() - t) * 1000) if landed else None, "typed": _appended(doc, before)}


def pill_buttons(app: LiveApp, doc: Document, wav: Path, seconds: float, out: Path) -> dict:
    """Hands-free from the key, then the pill's own finish button (and, in a second round, its
    cancel button). The clicks land on the app's panel, which must not take the focus from the
    document."""
    res: dict = {}
    for which in ("finish", "cancel"):
        res[which] = _pill_button(app, doc, wav, seconds, out, which)
        doc.caret_to_end()
    return res


def _pill_button(app: LiveApp, doc: Document, wav: Path, seconds: float, out: Path, which: str) -> dict:
    feed.say_next(app.mic, wav)
    before = doc.text()
    t0 = option(True)
    w = Watch(app, t0)
    w.run_for(0.3)
    drive.key("space")
    option(False)
    w.until(lambda s: s.get("state") == "locked", 1.0)
    w.run_for(seconds)
    frame = app.player_frame()
    if not frame:
        app.send({"cmd": "dictate", "value": "cancel"})
        return {"error": "no pill on screen while hands-free"}
    x, y, fw, fh = _tl(frame)
    upright = fh > fw
    if which == "finish":
        along = FINISH_BOTTOM + FINISH_D / 2  # from the far end: the bottom when upright, the right when flat
        px, py = (x + fw / 2, y + fh - along) if upright else (x + fw - along, y + fh / 2)
    else:
        along = CANCEL_TOP + CANCEL_D / 2
        px, py = (x + fw / 2, y + along) if upright else (x + along, y + fh / 2)
    drive.glide(*drive.cursor(), px, py, speed=1500)
    time.sleep(0.5)  # the tip shows after TIP_DELAY
    tips = [t for t in new_windows([], windows_of(app.pid)) if t["layer"] > 0 and t["bounds"][3] < 80 and t["bounds"][2] > 60]
    shoot_rect((x - 220, y - 40, fw + 260, fh + 80), out / f"dictate-{which}-hover.png")
    if not doc.in_front():
        app.send({"cmd": "dictate", "value": "cancel"})
        return {"skipped": f"{front_app()[1]} was in front"}
    t = drive.click(px, py)
    base = round((t - t0) * 1000)
    done = w.until(lambda s: s.get("state") in ("idle", "notice") and (which == "cancel" or w.first("working") is not None),
                   DONE_WAIT)
    landed = app.wait_for(lambda: _appended(doc, before).strip() or None, 2.0, every=0.05) if which == "finish" else None
    w.run_for(0.6)
    res = {"frame": frame, "point": [round(px), round(py)], "hit": BUTTON_HIT, "over_after_click_ms": _after(done, base),
           "typed": _appended(doc, before), "landed": bool(landed), "front_after_click": front_app()[1],
           "tips": tips, "timeline": w.timeline, "toasts": w.toasts}
    drive.move(x - 300, y + fh / 2)
    if which == "cancel":  # its toast offers Undo for five seconds; let it go
        w.until(lambda s: s.get("state") == "idle", 8.0)
    return res


def dictation_live(app: LiveApp, out: Path, register_cleanup: Callable[[Callable[[], None]], None] | None = None) -> dict:
    """The whole sequence on one interface. The cursor stays away from the pill except where a
    step clicks it."""
    wav = out / "sentence.wav"
    seconds = feed.make_sentence(SENTENCE, wav)
    wav2 = out / "second.wav"
    seconds2 = feed.make_sentence(SECOND, wav2)
    doc = Document(out, register_cleanup)
    try:
        why = doc.open(app)
        if why:
            return {"error": why}
        sw, sh = drive.display_size()
        drive.move(sw / 2, sh / 2 + 200)  # off the pill and the document's text
        app.ui()  # the new side's first `ui` request loads the cleanup library (seconds, holding the GIL): not in a step
        for f in (app.home / "state").rglob("*"):  # the model files read once, so neither side pays the disk for them
            if f.is_file() and f.stat().st_size > 1_000_000:
                with open(f, "rb") as fh:
                    while fh.read(1 << 24):
                        pass
        res: dict = {"sentence_s": round(seconds, 2), "idle_frame": app.player_frame()}
        steps = (
            ("hold", lambda: hold_to_talk(app, doc, wav, seconds + 0.6, out)),
            ("hold_again", lambda: _hold(app, doc, wav2, seconds2 + 0.6)),
            ("hands_free", lambda: hands_free(app, doc, wav, seconds + 0.6, out)),
            ("escape_undo", lambda: escape_and_undo(app, doc, wav2, seconds2 + 0.3, out, undo=True)),
            ("escape_expire", lambda: escape_and_undo(app, doc, wav2, 1.0, out, undo=False)),
            ("silence", lambda: _hold(app, doc, None, 1.5)),
            ("tap", lambda: _hold(app, doc, wav, 0.12)),
            ("failed_retry", lambda: failed_and_retry(app, doc, wav2, seconds2 + 0.6, out)),
            ("paste_last", lambda: paste_last(app, doc)),
            ("pill_buttons", lambda: pill_buttons(app, doc, wav, seconds + 0.3, out)),
        )
        for name, step in steps:
            if not doc.in_front():
                res[name] = {"skipped": f"{front_app()[1]} was in front"}
                continue
            idle = app.wait_for(lambda: dstate(app).get("state") == "idle", 10.0)
            if not idle:
                res[name] = {"skipped": f"the dictation was not idle ({dstate(app)})"}
                continue
            time.sleep(0.4)
            res[name] = step()
            doc.caret_to_end()
        res["document"] = doc.text()
        res["history"] = history(app)
        (out / "dictation.json").write_text(json.dumps(res, indent=1, default=str))
        return res
    finally:
        doc.close()
