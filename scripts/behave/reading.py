"""Reading with the default voice (Kokoro, its model files cloned into the test profile): the
first sound, sentence to sentence, the end of a reading and what the player shows after it, and
on the card: a click and a drag on the progress bar, a speed and a voice picked from their menus.
Positions on the card come from the shared layout (lyssna/ui/layout/pill.py), which both players
are built from; menu rows from Accessibility when the menu lists them, else from the menu's own
window split into equal rows."""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

from lyssna.player import SPEEDS
from lyssna.ui.layout.pill import BADGE, CLOSE, CLOSE_GAP, CLOSE_TOP, PAD, PILL_W, PLAY
from lyssna.ui.layout.wave import BAR_H

from . import ax, drive
from .live import LiveApp
from .scenarios import new_windows, shoot_rect, windows_of

SHORT = ("The first sentence is short. The second one follows it. A third sentence comes next. "
         "And this is the last one.")
LONG = ("This reading is long enough to scrub through while the harness moves the bar around. " * 12).strip()


def _status(app: LiveApp) -> dict:
    return app.status()


def _follow(app: LiveApp, t0: float, timeout: float) -> list[dict]:
    """The reading's (state, index) changes until it is idle again, in ms from t0."""
    out: list[dict] = []
    last = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _status(app)
        frame = app.player_frame()
        key = (st.get("state"), st.get("index"), (round(frame["w"]), round(frame["h"])) if frame else None)
        if key != last:
            last = key
            out.append({"ms": round((time.time() - t0) * 1000), "state": key[0], "index": key[1],
                        "form": list(key[2]) if key[2] else None})
        if st.get("state") == "idle" and len(out) > 1:
            break
        time.sleep(0.04)
    return out


def menu_rows(app: LiveApp, before: list[dict]) -> list[dict]:
    """The rows of the menu that just opened: title, centre (top-left points), checked."""
    rows: list[dict] = []

    def visit(e, d=0):
        if d > 6:
            return
        role = ax.attr(e, "AXRole")
        if role == "AXMenuItem":
            pos, size = ax.attr(e, "AXPosition"), ax.attr(e, "AXSize")
            import ApplicationServices as AXS

            ok1, p = AXS.AXValueGetValue(pos, AXS.kAXValueCGPointType, None) if pos is not None else (False, None)
            ok2, s = AXS.AXValueGetValue(size, AXS.kAXValueCGSizeType, None) if size is not None else (False, None)
            if ok1 and ok2 and s.height > 0:
                rows.append({"title": str(ax.attr(e, "AXTitle") or ""), "mark": str(ax.attr(e, "AXMenuItemMarkChar") or ""),
                             "centre": [p.x + s.width / 2, p.y + s.height / 2], "size": [round(s.width), round(s.height)]})
            return
        for c in ax.children(e):
            visit(c, d + 1)

    visit(ax.app(app.pid))
    return rows


def _menu_window(app: LiveApp, before: list[dict]) -> dict | None:
    got = [w for w in new_windows(before, windows_of(app.pid)) if w["layer"] > 0 and w["bounds"][3] > 60]
    return max(got, key=lambda w: w["bounds"][3]) if got else None


MENU_PAD = 5.0  # a Mac menu's room above its first row and below its last (both players: 10 rows in 250 points)


def pick(app: LiveApp, at: tuple[float, float], title: str, out: Path, label: str, count: int, index: int) -> dict:
    """Click `at` to open a menu, then click its row `title`: where Accessibility lists it, else
    row `index` of `count` by the menu window's geometry (the Qt menu lists no rows)."""
    before = windows_of(app.pid)
    drive.click(*at)
    menu = app.wait_for(lambda: _menu_window(app, before), 2.0, every=0.05)
    time.sleep(0.3)
    res: dict = {"menu_window": menu}
    if menu:
        x, y, w, h = menu["bounds"]
        shoot_rect((x - 20, y - 20, w + 40, h + 40), out / f"{label}-menu.png")
    rows = menu_rows(app, before)
    res["rows"] = [{k: r[k] for k in ("title", "mark", "size")} for r in rows]
    row = next((r for r in rows if r["title"] == title), None)
    if row is not None:
        target = row["centre"]
        res["picked_by"] = "accessibility"
    elif menu:
        x, y, w, h = menu["bounds"]
        res["picked_by"] = "geometry"
        step = (h - 2 * MENU_PAD) / max(1, count)
        target = [x + w / 2, y + MENU_PAD + step * (index + 0.5)]
    else:
        return {**res, "error": "no menu opened"}
    t = drive.click(*target)
    res["click"] = [round(target[0]), round(target[1])]
    res["_t"] = t
    time.sleep(0.4)
    res["menu_closed"] = _menu_window(app, before) is None
    return res


def _config(app: LiveApp) -> dict:
    try:
        cfg = tomllib.loads(app.config_path.read_text())
    except Exception as e:
        return {"error": str(e)}
    return {"speed": cfg.get("speed"), "kokoro": cfg.get("kokoro", {})}


def reading(app: LiveApp, out: Path) -> dict:
    sw, sh = drive.display_size()
    drive.move(sw / 2, sh - 120)
    res: dict = {"provider": _status(app).get("provider_name"), "voice": _status(app).get("voice")}
    # A short reading from start to end.
    t0 = time.time()
    app.send({"cmd": "speak", "text": SHORT})
    timeline = _follow(app, t0, 60.0)
    res["short"] = timeline
    res["short_first_playing_ms"] = next((e["ms"] for e in timeline if e["state"] == "playing"), None)
    res["short_end_ms"] = timeline[-1]["ms"] if timeline and timeline[-1]["state"] == "idle" else None
    time.sleep(1.0)
    res["after_end_frame"] = app.player_frame()
    res["after_end_dock"] = app.ui().get("dock")
    # A long one, on the card.
    app.send({"cmd": "speak", "text": LONG})
    if not app.wait_for(lambda: _status(app).get("state") == "playing", 30.0):
        return {**res, "error": "the long reading did not start"}
    time.sleep(0.8)
    card = app.player_frame()
    res["card"] = card
    if not card or app.ui().get("dock"):
        return {**res, "error": f"no card on screen (dock {app.ui().get('dock')!r})"}
    cx0, cy0, W, H = drive.to_top_left(card)
    shoot_rect((cx0 - 30, cy0 - 30, W + 60, H + 60), out / "reading-card.png")
    bar_y = cy0 + CLOSE_TOP + CLOSE + CLOSE_GAP + BAR_H / 2
    x_at = lambda f: cx0 + PAD + (W - 2 * PAD) * f  # noqa: E731
    # A click on the bar at 60 %.
    p0 = _status(app).get("position", 0.0)
    t = drive.click(x_at(0.6), bar_y)
    time.sleep(0.3)
    right_after = _status(app)
    app.wait_for(lambda: _status(app).get("state") == "playing", 10.0, every=0.05)
    st = _status(app)
    res["scrub_click"] = {"right_after": {k: right_after.get(k) for k in ("state", "index", "position")},
                          "playing_again_ms": round((time.time() - t) * 1000),
                          "before_position": round(p0, 2), "position": round(st.get("position", 0), 2),
                          "duration": round(st.get("duration", 0), 2), "index": st.get("index"), "total": st.get("total"),
                          "fraction": round(st.get("position", 0) / st["duration"], 3) if st.get("duration") else None,
                          "state": st.get("state")}
    # A drag from 20 % to 80 %, the times row followed on the way.
    drive.move(x_at(0.2), bar_y)
    time.sleep(0.1)
    drive.button(True, x_at(0.2), bar_y)
    time.sleep(0.15)
    drive.glide(x_at(0.2), bar_y, x_at(0.8), bar_y, speed=400)
    mid_shot = out / "reading-scrub-drag.png"
    shoot_rect((cx0 - 10, cy0 - 10, W + 20, H + 20), mid_shot)
    during = _status(app)
    time.sleep(0.15)
    t = drive.button(False, x_at(0.8), bar_y)
    time.sleep(0.3)
    right_after = _status(app)
    app.wait_for(lambda: _status(app).get("state") == "playing", 10.0, every=0.05)
    st = _status(app)
    res["scrub_drag"] = {"right_after": {k: right_after.get(k) for k in ("state", "index", "position")},
                         "playing_again_ms": round((time.time() - t) * 1000),
                         "during_state": during.get("state"), "during_position": round(during.get("position", 0), 2),
                         "position": round(st.get("position", 0), 2), "duration": round(st.get("duration", 0), 2),
                         "fraction": round(st.get("position", 0) / st["duration"], 3) if st.get("duration") else None,
                         "index": st.get("index"), "state": st.get("state")}
    # The speed menu: 1.5x.
    speed_at = (cx0 + W - PAD - PILL_W / 2, cy0 + H - PAD - PLAY / 2)
    got = pick(app, speed_at, "1.5×", out, "speed", len(SPEEDS), SPEEDS.index(1.5))
    t = got.pop("_t", time.time())
    changed = app.wait_for(lambda: abs((_status(app).get("speed") or 0) - 1.5) < 0.01, 2.0, every=0.03)
    got.update({"speed": _status(app).get("speed"), "speed_ms": round((time.time() - t) * 1000) if changed else None,
                "config": _config(app), "state": _status(app).get("state")})
    res["speed_pick"] = got
    # The voice menu: the second voice listed.
    badge_at = (cx0 + PAD + BADGE / 2, cy0 + H - PAD - PLAY / 2)
    before = windows_of(app.pid)
    drive.click(*badge_at)
    menu = app.wait_for(lambda: _menu_window(app, before), 2.0, every=0.05)
    time.sleep(0.3)
    rows = menu_rows(app, before)
    res["voice_menu"] = {"window": menu, "rows": [{k: r[k] for k in ("title", "mark", "size")} for r in rows][:40]}
    if menu:
        x, y, w, h = menu["bounds"]
        shoot_rect((x - 20, y - 20, w + 40, h + 40), out / "voice-menu.png")
    drive.click(cx0 + W / 2, cy0 + 30)  # outside the menu, on the card's own top band (no key goes out)
    time.sleep(0.4)
    if menu:
        # The second voice listed (Bella on both sides as of 0.3; the Qt menu lists no rows to
        # Accessibility, so the row count comes from the window: 24-point rows).
        count = len(rows) or round((menu["bounds"][3] - 2 * MENU_PAD) / 24)
        other = rows[1]["title"] if len(rows) > 1 else "(row 2)"
        if count > 1:
            v0 = _status(app).get("voice")
            got = pick(app, badge_at, other, out, "voice", count, 1)
            t = got.pop("_t", time.time())
            changed = app.wait_for(lambda: _status(app).get("voice") != v0, 3.0, every=0.03)
            got.update({"from": v0, "to_title": other, "voice": _status(app).get("voice"),
                        "voice_ms": round((time.time() - t) * 1000) if changed else None, "config": _config(app),
                        "state": _status(app).get("state")})
            res["voice_pick"] = got
    app.send({"cmd": "stop"})
    time.sleep(0.6)
    return res
