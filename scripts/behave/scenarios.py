"""The scenarios, each run on one interface against a live app. A scenario returns plain facts
(what happened, when, where) and writes its evidence (frames, filmstrips, Accessibility dumps)
under `out`; the report sets the two interfaces' facts side by side.

Positions inside the player come from the shared layout (lyssna/ui/layout/pill.py), which both
interfaces draw from, and the frames the app reports through its control channel (`ui`).
Clicks only ever land on the test app's own floating panels and windows; keys are only sent
while one of the test app's windows or the harness's own test document is in front."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from lyssna.ui.layout.pill import CLOSE, CLOSE_TOP, MENU_DISC, MENU_H, MENU_MIC_H, PAD, PILL_W, PLAY, SKIP

from . import ax, drive, frames, hover
from .live import LiveApp
from .machine import front_app

OPEN_PILL = {"stop": 15.0, "expand": 51.0, "back": 95.0, "toggle": 129.0, "forward": 163.0}  # from the pill's top, upright


def windows_of(pid: int) -> list[dict]:
    """The app's windows on screen now: layer, bounds (top-left points), title, alpha."""
    import Quartz

    out = []
    for info in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []:
        if info.get(Quartz.kCGWindowOwnerPID) != pid:
            continue
        b = info.get(Quartz.kCGWindowBounds) or {}
        out.append({"id": int(info.get(Quartz.kCGWindowNumber, 0)), "layer": int(info.get(Quartz.kCGWindowLayer, 0)),
                    "bounds": [round(b.get("X", 0)), round(b.get("Y", 0)), round(b.get("Width", 0)), round(b.get("Height", 0))],
                    "title": str(info.get(Quartz.kCGWindowName) or ""), "alpha": round(float(info.get(Quartz.kCGWindowAlpha, 1)), 2)})
    return out


def new_windows(before: list[dict], after: list[dict]) -> list[dict]:
    ids = {w["id"] for w in before}
    return [w for w in after if w["id"] not in ids and w["bounds"][2] > 2 and w["layer"] < 1000]


def shoot_window(window_id: int, path: Path) -> bool:
    import Quartz

    img = Quartz.CGWindowListCreateImage(Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, window_id,
                                         Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution)
    if img is None:
        return False
    import AppKit

    rep = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(img)
    path.parent.mkdir(parents=True, exist_ok=True)
    rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None).writeToFile_atomically_(str(path), True)
    return True


def shoot_rect(rect, path: Path) -> None:
    px = frames.grab(rect)
    if px is not None:
        frames.save_png(px, path)


def _tl(frame: dict | None):
    return drive.to_top_left(frame) if frame else None


def _state(app: LiveApp) -> dict:
    st = app.status()
    return {"state": st.get("state"), "index": st.get("index"), "dock": app.ui().get("dock")}


def _wait_state(app: LiveApp, test: Callable[[dict], bool], timeout: float = 3.0) -> tuple[dict, float | None]:
    t0 = time.time()
    got = app.wait_for(lambda: (lambda s: s if test(s) else None)(_state(app)), timeout, every=0.03)
    return (got or _state(app)), (round((time.time() - t0) * 1000) if got else None)


# -- the resting squiggle and its menu ---------------------------------------------------------------


def rest_menu(app: LiveApp, out: Path) -> dict:
    """Idle on the right edge: hover opens the menu (timed as the sliver is), each item's tip, a
    click on History opens the history window."""
    res: dict = {"rest_frame": app.player_frame()}
    if not res["rest_frame"]:
        return {"error": "no resting squiggle on screen"}
    shoot_rect(_rest_region(res["rest_frame"]), out / "rest.png")
    res["hover"] = hover.measure(app, out, "right", trials=4, label="rest")["summary"]
    x, y, w, h = _tl(res["rest_frame"])
    cy = y + h / 2
    drive.glide(x - 200, cy, x + w / 2, cy)
    time.sleep(0.5)
    menu = app.player_frame()
    res["menu_frame"] = menu
    shoot_rect(_rest_region(res["rest_frame"], wide=True), out / "menu-open.png")
    if not menu:
        return res
    mx, my, mw, mh = _tl(menu)
    centre = mx + mw / 2
    spots = {"history": my + MENU_DISC / 2, "read": my + MENU_DISC + 6 + MENU_DISC / 2, "dictate": my + mh - MENU_MIC_H / 2}
    res["menu_len_expected"] = MENU_H
    tips = {}
    for name, sy in spots.items():
        before = windows_of(app.pid)
        drive.glide(*drive.cursor(), centre, sy, speed=300)
        time.sleep(0.8)
        appeared = new_windows(before, windows_of(app.pid))
        tips[name] = appeared
        shoot_rect((drive.display_size()[0] - 330, sy - 60, 330, 120), out / f"tip-{name}.png")
    res["tips"] = tips
    # Leave: the menu closes.
    t = drive.glide(*drive.cursor(), mx - 220, spots["read"])
    closed = app.wait_for(lambda: (app.player_frame() or {}).get("h", 0) < 80, 3.0, every=0.03)
    res["menu_closed_after_leaving"] = bool(closed)
    res["menu_close_ms"] = round((time.time() - t[-1][0]) * 1000) if closed else None
    return res


def _rest_region(frame: dict, wide: bool = False):
    _x, y, _w, h = drive.to_top_left(frame)
    sw, _ = drive.display_size()
    across = 330 if wide else 110
    return (sw - across, max(0, y + h / 2 - 140), across, 280)


def history_from_menu(app: LiveApp, out: Path) -> dict:
    """Open the idle menu and click its History item: the history window opens."""
    rest = app.player_frame()
    if not rest:
        return {"error": "no resting squiggle"}
    x, y, w, h = _tl(rest)
    cy = y + h / 2
    drive.glide(x - 200, cy, x + w / 2, cy)
    time.sleep(0.5)
    menu = app.player_frame()
    mx, my, mw, _mh = _tl(menu)
    before = windows_of(app.pid)
    t = drive.click(mx + mw / 2, my + MENU_DISC / 2)
    got = app.wait_for(lambda: [w_ for w_ in new_windows(before, windows_of(app.pid)) if w_["layer"] == 0], 5.0, every=0.05)
    res = {"window": got[0] if got else None, "open_ms": round((time.time() - t) * 1000) if got else None}
    if got:
        time.sleep(0.6)
        shoot_window(got[0]["id"], out / "history.png")
        res["ax"] = _window_ax(app, got[0]["title"])
        (out / "history-ax.json").write_text(json.dumps(res["ax"], indent=1))
        _close_ax_windows(app)
    drive.move(x - 220, cy)
    return res


# -- the docked reading pill -----------------------------------------------------------------------


def pill_buttons(app: LiveApp, out: Path) -> dict:
    """During a reading docked right: open the pill, then play/pause, next, previous, expand to the
    card, the card's own buttons, tuck back, and stop from the pill."""
    res: dict = {}
    if not hover.start_reading(app, pause=False):
        return {"error": "the reading did not start"}
    sliver = app.player_frame()
    res["sliver"] = sliver
    x, y, w, h = _tl(sliver)
    cy = y + h / 2

    def open_pill():
        drive.glide(x - 200, cy, x + w / 2, cy)
        time.sleep(0.45)
        return _tl(app.player_frame())

    px, py, pw, ph = open_pill()
    res["pill"] = [px, py, pw, ph]
    shoot_rect((px - 20, py - 10, pw + 60, ph + 20), out / "pill-open.png")
    cx = px + pw / 2

    def tap(name):
        return drive.click(cx, py + OPEN_PILL[name])

    s0 = _state(app)
    tap("toggle")
    res["pause"], res["pause_ms"] = _wait_state(app, lambda s: s["state"] == "paused")
    tap("toggle")
    res["resume"], res["resume_ms"] = _wait_state(app, lambda s: s["state"] in ("playing", "loading"))
    i0 = _state(app)["index"] or s0["index"]
    tap("forward")
    res["next"], res["next_ms"] = _wait_state(app, lambda s: (s["index"] or 0) > i0)
    i1 = _state(app)["index"]
    tap("back")
    res["prev"], res["prev_ms"] = _wait_state(app, lambda s: (s["index"] or 99) < (i1 or 0))
    res["pill_still_open"] = (app.player_frame() or {}).get("h", 0) > 100
    tap("expand")
    res["expand"], res["expand_ms"] = _wait_state(app, lambda s: s["dock"] == "")
    card = app.player_frame()
    res["card"] = card
    if card:
        res.update(card_buttons(app, out, card))
    if app.ui().get("dock") == "right":
        px, py, pw, ph = open_pill()
        cx = px + pw / 2
        tap("stop")
        res["stop"], res["stop_ms"] = _wait_state(app, lambda s: s["state"] in ("idle", "stopped", "done", None))
        time.sleep(0.8)
        res["after_stop_frame"] = app.player_frame()
    drive.move(x - 220, cy)
    return res


def card_buttons(app: LiveApp, out: Path, card: dict) -> dict:
    """On the card: pause, speed pill opens its menu, tuck docks it to the nearest edge."""
    res: dict = {}
    cx0, cy0, W, H = drive.to_top_left(card)
    shoot_rect((cx0 - 30, cy0 - 30, W + 60, H + 60), out / "card.png")
    play = (cx0 + W / 2, cy0 + H - PAD - PLAY / 2)
    drive.click(*play)
    res["card_pause"], res["card_pause_ms"] = _wait_state(app, lambda s: s["state"] == "paused")
    drive.click(*play)
    res["card_resume"], _ = _wait_state(app, lambda s: s["state"] in ("playing", "loading"))
    i0 = _state(app)["index"]
    drive.click(cx0 + W / 2 + PLAY / 2 + 22 + SKIP / 2, cy0 + H - PAD - PLAY / 2)
    res["card_next"], _ = _wait_state(app, lambda s: (s["index"] or 0) > (i0 or 0))
    before = windows_of(app.pid)
    drive.click(cx0 + W - PAD - PILL_W / 2, cy0 + H - PAD - PLAY / 2)
    time.sleep(0.6)
    menus = new_windows(before, windows_of(app.pid))
    res["speed_menu_windows"] = menus
    if menus:
        shoot_rect((cx0 - 60, cy0 - 260, W + 120, H + 520), out / "speed-menu.png")
        drive.click(cx0 + W / 2, cy0 + 30)  # outside the menu, on the card's own top band
    time.sleep(0.3)
    res["speed_after_menu"] = app.status().get("speed")
    tuck = (cx0 + W - PAD - CLOSE - 8 - CLOSE / 2, cy0 + CLOSE_TOP + CLOSE / 2)
    drive.click(*tuck)
    res["tuck"], res["tuck_ms"] = _wait_state(app, lambda s: s["dock"] != "")
    time.sleep(0.5)
    res["tucked_frame"] = app.player_frame()
    return res


# -- docking by dragging -------------------------------------------------------------------------


def drag_dock(app: LiveApp, out: Path) -> dict:
    """A docked reading: drag the sliver to the left edge (the dock hint shows while dragging), drop
    there, then drag it off the edge into the middle (it becomes the card), and drag the card."""
    res: dict = {}
    if not hover.start_reading(app, pause=True):
        return {"error": "the reading did not start"}
    sliver = _tl(app.player_frame())
    x, y, w, h = sliver
    sw, sh = drive.display_size()
    start = (x + w / 2, y + h / 2)
    drive.move(start[0] - 200, start[1])
    time.sleep(0.4)
    drive.glide(start[0] - 200, start[1], *start)
    time.sleep(0.5)
    opened = _tl(app.player_frame())
    if opened and opened[3] > 100:  # the hover opened the pill: grab its capsule's top padding, where no button is
        start = (opened[0] + opened[2] / 2, opened[1] + OPEN_PILL["expand"] + 25)
        drive.move(*start)
        time.sleep(0.2)
    res["grabbed"] = "pill" if opened and opened[3] > 100 else "sliver"
    before = windows_of(app.pid)
    drive.move(*start)
    drive.button(True, *start)
    time.sleep(0.15)
    drive.glide(*start, sw / 2, start[1], speed=900)
    mid = windows_of(app.pid)
    drive.glide(sw / 2, start[1], 12, start[1], speed=900)
    time.sleep(0.3)
    hint = new_windows(before, windows_of(app.pid))
    res["while_dragging_mid_windows"] = new_windows(before, mid)
    res["hint_windows_near_left"] = hint
    shoot_rect((0, start[1] - 180, 200, 360), out / "drag-left-hint.png")
    drive.button(False, 12, start[1])
    res["drop_left"], res["drop_left_ms"] = _wait_state(app, lambda s: s["dock"] == "left")
    time.sleep(0.5)
    res["left_frame"] = app.player_frame()
    res["config_after_left"] = _config_ui(app)
    lf = _tl(res["left_frame"]) if res["left_frame"] else None
    if lf:
        lx, ly, lw, lh = lf
        res["left_form_after_drop"] = "pill" if lh > 100 else "sliver"
        # Grab the closed sliver in its middle, or the open pill on its capsule's top padding (no button there).
        s2 = (lx + lw / 2, ly + (OPEN_PILL["expand"] + 25 if lh > 100 else lh / 2))
        drive.glide(s2[0] + 200, s2[1], *s2)
        time.sleep(0.4)
        drive.button(True, *s2)
        time.sleep(0.15)
        drive.glide(*s2, sw / 2, sh / 2, speed=900)
        time.sleep(0.2)
        drive.button(False, sw / 2, sh / 2)
        res["drop_middle"], res["drop_middle_ms"] = _wait_state(app, lambda s: s["dock"] == "")
        time.sleep(0.5)
        card = app.player_frame()
        res["card_after_undock"] = card
        if card:
            cx0, cy0, W, _H = drive.to_top_left(card)
            grip = (cx0 + W / 2, cy0 + 30)  # the card's top band, between its text and its corner buttons
            drive.drag([grip, (grip[0] - 300, grip[1] - 150)])
            time.sleep(0.4)
            res["card_after_free_drag"] = app.player_frame()
            drive.drag([(grip[0] - 300, grip[1] - 150), (8, grip[1] - 150)])
            time.sleep(0.5)
            res["card_dragged_to_left_edge"] = {"frame": app.player_frame(), "dock": app.ui().get("dock")}
    res["config_end"] = _config_ui(app)
    app.send({"cmd": "stop"})
    return res


def _config_ui(app: LiveApp) -> dict:
    import tomllib

    try:
        ui = tomllib.loads(app.config_path.read_text()).get("ui", {})
    except Exception as e:
        return {"error": str(e)}
    return {k: ui.get(k) for k in ("player_dock", "player_x", "player_y", "dictation_dock", "dictation_y")}


# -- auto-hide -----------------------------------------------------------------------------------


def auto_hide(app: LiveApp, out: Path) -> dict:
    """Idle with auto-hide on: the squiggle is out of sight; the cursor coming within reach slides it
    in, leaving slides it out again after the delay."""
    sw, sh = drive.display_size()
    cy = sh / 2
    region = (sw - 110, cy - 160, 110, 320)
    drive.move(sw - 600, cy)
    time.sleep(3.0)  # past the linger after start
    res: dict = {"hidden_frame": app.player_frame()}
    rec = frames.Recorder(region).start()
    time.sleep(0.4)
    trail = drive.glide(sw - 600, cy, sw - 60, cy, speed=600)
    reach = next((t for t, px, py in trail if px >= sw - 26 - 100), None)
    time.sleep(1.2)
    res["revealed_frame"] = app.player_frame()
    out_trail = drive.glide(sw - 60, cy, sw - 600, cy, speed=600)
    left = next((t for t, px, py in out_trail if px < sw - 26 - 100), None)
    time.sleep(1.5)
    r = rec.stop()
    before = [f for f in r.frames if f.t < trail[0][0]]
    shown = [f for f in r.frames if f.t < out_trail[0][0]]
    if before and shown and reach and left:
        res["reveal"] = frames.motion(r.frames, reach, before[-1].pixels, shown[-1].pixels, until=out_trail[0][0])
        res["hide"] = frames.motion(r.frames, left, shown[-1].pixels, r.frames[-1].pixels)
        frames.filmstrip(r.frames, reach, reach + 0.5, out / "autohide-reveal.png")
        frames.filmstrip(r.frames, left, left + 1.0, out / "autohide-hide.png")
    res["capture_rate"] = round(r.rate(), 1)
    return res


# -- toast and dictation -------------------------------------------------------------------------


def dictation(app: LiveApp, out: Path) -> dict:
    """Start a dictation from the control channel (the menu's Dictate does the same call): with no
    speech model in the profile the app asks for the download, which the isolation refuses. What
    the user sees (state, toast, its place) is recorded; cancel ends it."""
    res: dict = {}
    before = windows_of(app.pid)
    t = time.time()
    res["start_reply"] = app.send({"cmd": "dictate", "value": "start"})
    time.sleep(1.5)
    res["windows_after_start"] = new_windows(before, windows_of(app.pid))
    res["dictation_state"] = app.status().get("dictation", {})
    shoot_rect((drive.display_size()[0] - 520, 200, 520, 600), out / "dictation-start.png")
    toast_seen = []
    end = time.time() + 12
    while time.time() < end:
        now = new_windows(before, windows_of(app.pid))
        toast_seen.append((round(time.time() - t, 2), len(now)))
        if not now and time.time() - t > 2:
            break
        time.sleep(0.25)
    res["toast_visible_until_s"] = max((s for s, n in toast_seen if n), default=None)
    res["cancel_reply"] = app.send({"cmd": "dictate", "value": "cancel"})
    lines = [m for _t, _l, name, m in app.log_lines(t) if "dictat" in name or "toast" in m.lower()]
    res["log"] = lines[:30]
    return res


# -- the menu bar menu and the windows --------------------------------------------------------------


def menubar(app: LiveApp, out: Path) -> dict:
    _item, menu, pressed = ax.status_menu(app.pid)
    if menu is None:
        return {"error": "no status item menu found through Accessibility", "status_items": len(ax.status_items(app.pid))}
    rows = ax.menu_rows(menu)
    shot = [w for w in windows_of(app.pid) if w["layer"] >= 101]
    if pressed and shot:
        shoot_window(max(shot, key=lambda w: w["bounds"][3])["id"], out / "menubar-menu.png")
    if pressed:
        ax.cancel(menu)
        time.sleep(0.3)
    (out / "menubar.json").write_text(json.dumps(rows, indent=1))
    return {"rows": rows, "opened_by_press": pressed}


def _window_ax(app: LiveApp, title: str) -> list[dict]:
    for w in ax.windows(app.pid):
        if not title or str(ax.attr(w, "AXTitle") or "") == title:
            return ax.tree(w)
    return []


def _close_ax_windows(app: LiveApp) -> None:
    for w in ax.windows(app.pid):
        btn = ax.attr(w, "AXCloseButton")
        if btn is not None:
            ax.press(btn)
    time.sleep(0.4)


def windows(app: LiveApp, out: Path) -> dict:
    """Each window the menu opens: Setup (and each of its tabs), History, What's New."""
    res: dict = {}
    for label, start in (("setup", "Setup"), ("history", "History"), ("whats_new", "What")):
        _item, menu, pressed = ax.status_menu(app.pid)
        row = ax.find_row(menu, start) if menu is not None else None
        if row is None:
            for sub in (menu and ax.children(menu)) or []:
                for c in ax.children(sub):
                    row = row or ax.find_row(c, start)
        if row is None:
            res[label] = {"error": f"no menu row starting {start!r}"}
            if pressed and menu is not None:
                ax.cancel(menu)
            continue
        before = windows_of(app.pid)
        t = time.time()
        ax.press(row)
        got = app.wait_for(lambda before=before: [w for w in new_windows(before, windows_of(app.pid)) if w["layer"] == 0], 6.0,
                           every=0.05)
        entry: dict = {"open_ms": round((time.time() - t) * 1000) if got else None, "window": got[0] if got else None}
        if got:
            time.sleep(0.8)
            shoot_window(got[0]["id"], out / f"{label}.png")
            entry["ax"] = _window_ax(app, got[0]["title"])
            (out / f"{label}-ax.json").write_text(json.dumps(entry["ax"], indent=1))
            entry["front_after_open"] = front_app()[1]
            if label == "setup":
                entry["tabs"] = setup_tabs(app, got[0], out)
            _close_ax_windows(app)
            entry["closed"] = not [w for w in windows_of(app.pid) if w["id"] == got[0]["id"]]
        res[label] = entry
    return res


def setup_tabs(app: LiveApp, win: dict, out: Path) -> list[dict]:
    """Press each tab-like button at the top of the setup window and shoot it."""
    tabs = []
    for w in ax.windows(app.pid):
        if str(ax.attr(w, "AXTitle") or "") != win["title"]:
            continue
        candidates = []

        def visit(e, found, d=0):
            if d > 8:
                return
            if ax.attr(e, "AXRole") in ("AXRadioButton", "AXTab"):
                found.append(e)
            for c in ax.children(e):
                visit(c, found, d + 1)
        visit(w, candidates)
        for n, tab in enumerate(candidates[:8]):
            name = str(ax.attr(tab, "AXTitle") or ax.attr(tab, "AXDescription") or n)
            ax.press(tab)
            time.sleep(0.6)
            shoot_window(win["id"], out / f"setup-tab-{n}.png")
            tabs.append({"tab": name, "texts": [x["text"] for x in ax.tree(w) if x["role"] == "AXStaticText"][:40]})
        break
    return tabs


# -- start, quit, a second copy --------------------------------------------------------------------


def lifecycle(app: LiveApp, out: Path) -> dict:
    """Quit from the menu's Quit row, start again, then start a second copy while one runs."""
    res: dict = {}
    _item, menu, _pressed = ax.status_menu(app.pid)
    row = ax.find_row(menu, "Quit") if menu is not None else None
    t = time.time()
    if row is not None:
        ax.press(row)
        try:
            app.proc.wait(timeout=8)
            res["quit_from_menu_s"] = round(time.time() - t, 2)
        except Exception:
            res["quit_from_menu_s"] = None
    else:
        res["quit_row"] = "not found"
        app.quit()
    res["exit_code"] = app.proc.returncode if app.proc else None
    res["socket_left"] = app.socket.exists()
    app.start(wait=False)
    res["restart_ready_s"] = round(app.wait_ready(), 2)
    first = app.proc
    second = LiveApp(app.interface, out / "second")
    second.home, second.socket, second.flags, second.launches = app.home, app.socket, app.flags, 1
    (out / "second").mkdir(parents=True, exist_ok=True)
    t = time.time()
    second.start(wait=False)
    try:
        second.proc.wait(timeout=12)
        res["second_copy"] = {"exited_after_s": round(time.time() - t, 2), "exit_code": second.proc.returncode}
    except Exception:
        res["second_copy"] = {"still_running_after_s": 12}
    time.sleep(1.0)
    res["first_still_answers"] = app.send({"cmd": "status"}) is not None
    res["first_alive"] = first.poll() is None
    res["windows_after_second"] = [w for w in windows_of(first.pid) if w["layer"] == 0]
    if second.alive():  # never through the socket: that may be the first copy's
        second.proc.terminate()
        second.proc.wait(timeout=5)
    if res["windows_after_second"]:
        _close_ax_windows(app)
    shutil.rmtree(second._sockdir, ignore_errors=True)
    if not app.send({"cmd": "status"}):
        res["first_after_cleanup"] = "no answer"
    return res


# -- text selection and the shortcuts, in a TextEdit document of the harness's own ------------------

SELECTION_TEXT = "Harness text for the play button.\n\nThe second paragraph is here so the shortcut has something else to read.\n"


def _textedit_pid() -> int:
    import subprocess

    out = subprocess.run(["pgrep", "-x", "TextEdit"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


def _word_rect(area, start: int, length: int):
    """The screen rect (top-left points) of characters [start, start+length) in an AX text area."""
    import ApplicationServices as AXS

    rng = AXS.AXValueCreate(AXS.kAXValueCFRangeType, (start, length))
    err, value = AXS.AXUIElementCopyParameterizedAttributeValue(area, "AXBoundsForRange", rng, None)
    if err != 0 or value is None:
        return None
    ok, rect = AXS.AXValueGetValue(value, AXS.kAXValueCGRectType, None)
    return (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height) if ok else None


def selection(app: LiveApp, out: Path, register_cleanup: Callable[[Callable[[], None]], None] | None = None) -> dict:
    """Open a temporary document in TextEdit (only when TextEdit is not running already, so none of
    the owner's documents can be touched), double-click a word: the play button appears beside it;
    click it: the word is read. Then the read-selection shortcut, the paste-last shortcut (nothing
    dictated yet: a toast says so), and the button's own timeout. Keys go out only while TextEdit,
    with this document, is in front."""
    import subprocess

    if _textedit_pid():
        return {"error": "TextEdit was already running; the harness does not touch it"}
    doc = out / "selection-test.txt"
    doc.write_text(SELECTION_TEXT)
    subprocess.run(["open", "-a", "TextEdit", str(doc)], check=False)

    def close_textedit():
        """Quit the TextEdit this scenario started, without AppleScript: an Apple event to TextEdit
        makes macOS ask the owner whether this terminal may control it (seen on 2026-11-25)."""
        from AppKit import NSRunningApplication

        for running in NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.TextEdit"):
            running.terminate()
        deadline = time.monotonic() + 5
        while _textedit_pid() and time.monotonic() < deadline:
            time.sleep(0.2)

    if register_cleanup:
        register_cleanup(close_textedit)
    res: dict = {}
    try:
        pid = app.wait_for(_textedit_pid, 10.0)
        win = app.wait_for(lambda: [w for w in ax.windows(pid) if doc.name in str(ax.attr(w, "AXTitle") or "")], 10.0)
        if not win:
            return {"error": "the test document did not open in TextEdit"}
        time.sleep(1.0)
        areas = [e for e in _descendants(win[0]) if ax.attr(e, "AXRole") == "AXTextArea"]
        if not areas:
            return {"error": "no text area in the test document"}
        word = _word_rect(areas[0], SELECTION_TEXT.index("play"), 4)
        res["word_rect"] = word

        def ours_in_front() -> bool:  # by name: a TextEdit from an earlier run may still be closing under another pid
            return front_app()[1] == "TextEdit"

        if word and not ours_in_front():  # macOS may refuse `open` the focus: ask TextEdit, then click its own text
            from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

            running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if running is not None:
                running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(0.6)
            if not ours_in_front():
                drive.click(word[0] + word[2] / 2, word[1] + word[3] / 2)
                time.sleep(0.6)
        res["front_before_selecting"] = front_app()[1]
        if not word or not ours_in_front():
            return {**res, "error": f"the test document is not in front ({front_app()[1]})"}
        wx, wy = word[0] + word[2] / 2, word[1] + word[3] / 2
        t = drive.click(wx, wy, clicks=2)
        got = app.wait_for(lambda: app.ui().get("selection_button"), 4.0, every=0.03)
        res["button_ms"] = round((time.time() - t) * 1000) if got else None
        res["button_frame"] = got
        res["button_offset_from_word"] = ([round(got["x"] - word[0]), round(drive.to_top_left(got)[1] - word[1])] if got else None)
        shoot_rect((word[0] - 80, word[1] - 80, 360, 200), out / "selection-button.png")
        if got:
            bx, by, bw, bh = drive.to_top_left(got)
            t = drive.click(bx + bw / 2, by + bh / 2)
            st = app.wait_for(lambda: (lambda s: s if s.get("state") in ("playing", "loading") else None)(app.status()), 8.0)
            res["button_click_reads"] = (st or {}).get("text", "")[:60] if st else None
            res["button_click_ms"] = round((time.time() - t) * 1000) if st else None
            res["button_gone_after_click"] = app.wait_for(lambda: app.ui().get("selection_button") is None, 2.0) is not None
            res["front_after_click"] = front_app()[1]
            app.send({"cmd": "stop"})
            time.sleep(0.8)
        # The button's own timeout.
        t = drive.click(wx, wy, clicks=2)
        if app.wait_for(lambda: app.ui().get("selection_button"), 4.0, every=0.03):
            gone = app.wait_for(lambda: app.ui().get("selection_button") is None, 10.0, every=0.1)
            res["button_timeout_s"] = round(time.time() - t, 1) if gone else None
        # The read-selection shortcut on the selected word.
        if ours_in_front():
            t = drive.chord("s", ("control", "option"))
            st = app.wait_for(lambda: (lambda s: s if s.get("state") in ("playing", "loading") else None)(app.status()), 8.0)
            res["shortcut_reads"] = (st or {}).get("text", "")[:60] if st else None
            res["shortcut_ms"] = round((time.time() - t) * 1000) if st else None
            app.send({"cmd": "stop"})
            time.sleep(0.8)
        # Paste the last dictation: there is none.
        if ours_in_front():
            before = windows_of(app.pid)
            drive.chord("v", ("control", "option"))
            toast = app.wait_for(lambda: new_windows(before, windows_of(app.pid)), 3.0)
            res["paste_last_toast"] = toast
            if toast:
                shoot_rect((toast[0]["bounds"][0] - 20, toast[0]["bounds"][1] - 20, toast[0]["bounds"][2] + 40,
                            toast[0]["bounds"][3] + 40), out / "paste-last-toast.png")
            time.sleep(0.5)
            res["document_unchanged"] = str(ax.attr(areas[0], "AXValue") or "") == SELECTION_TEXT.rstrip("\n") or (
                str(ax.attr(areas[0], "AXValue") or "") == SELECTION_TEXT)
        else:
            res["keys_skipped"] = f"{front_app()[1]} was in front"
    finally:
        close_textedit()
    return res


def _descendants(el, depth: int = 0) -> list:
    if depth > 8:
        return []
    out = [el]
    for c in ax.children(el):
        out += _descendants(c, depth + 1)
    return out
