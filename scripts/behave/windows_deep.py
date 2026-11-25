"""History and Setup in depth, on both interfaces.

History: the profile's own history file is seeded with three dictations and a failed one, then
the window is driven as a person would: search, a row's More menu, Show Original, Fix a Word
(the corrected word lands in the dictionary), the Dictionary tab and adding a word, Clear
History and its confirmation. Setup: every tab, and every button on each tab that stays inside
the app (no system settings, no browser, no download, no key entry).

Positions inside the History window come from its search field, which both windows expose to
Accessibility, and the shared layout (lyssna/ui/layout/history.py). Keys are typed only while the
test app is in front, into its own window."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from lyssna.ui.layout.history import ICON_BTN, ICON_SIZE, MARGIN, ROW_PAD_X

from . import ax, drive
from .live import LiveApp
from .machine import front_app
from .scenarios import _close_ax_windows, new_windows, shoot_rect, shoot_window, windows_of

ENTRIES = (  # newest first: (text, raw)
    ("Please send the quarterly report to Maria before Friday.", "please send the quarterly report to maria before friday"),
    ("Call Jon about the Kubernetes rollout.", "call jon about the cooper netties rollout"),
    ("Lunch with the team at noon.", "lunch with the team at noon"),
)
FIXED = "Please send the quarterly report to Mariah before Friday."
NEW_WORD = "Lyssnafy"
MORE_ROWS = ("Show Original", "Fix a Word…", "Delete")
ROW1_Y, TABS_Y = 345.5, 200.0  # the first row's text line and the tabs, from the window's top (title bar included)
DICTATIONS_X, DICTIONARY_X = 81.0, 181.0  # the two tabs' middles from the window's left
MENU_PAD, MENU_ROW = 5.0, 22.0  # a Mac menu's room above its first row, and its row height (NSMenu, standard size)


def seed_history(app: LiveApp) -> list[dict]:
    """Three dictations today and one that failed, written to the profile's history file."""
    now = datetime.now().astimezone()
    entries = []
    for n, (text, raw) in enumerate(ENTRIES):
        at = (now - timedelta(minutes=5 + 10 * n)).isoformat(timespec="seconds")
        entries.append({"id": uuid.uuid4().hex[:12], "at": at, "text": text, "raw": raw, "words": len(text.split()),
                        "audio_seconds": 3.0, "transcriber": "parakeet", "cleaner": "s1-mini", "source": "model", "status": "ok"})
    at = (now - timedelta(minutes=60)).isoformat(timespec="seconds")
    entries.append({"id": uuid.uuid4().hex[:12], "at": at, "text": "", "raw": "", "words": 0, "audio_seconds": 4.0,
                    "transcriber": "parakeet", "cleaner": "", "source": "", "status": "failed"})
    totals = {"words": sum(e["words"] for e in entries), "dictations": 3, "audio_seconds": 9.0, "weeks": {}}
    path = app.home / "state" / "dictation" / "history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "totals": totals, "entries": entries}, indent=1))
    return entries


def _store(app: LiveApp) -> dict:
    out = {}
    for name in ("history", "vocabulary"):
        try:
            out[name] = json.loads((app.home / "state" / "dictation" / f"{name}.json").read_text())
        except (OSError, ValueError):
            out[name] = None
    hist = out["history"] or {}
    vocab = out["vocabulary"] or {}
    terms = vocab.get("terms", vocab.get("entries", [])) if isinstance(vocab, dict) else []
    return {"texts": [e.get("text") or f"({e.get('status')})" for e in hist.get("entries", [])],
            "terms": [t.get("term", t) if isinstance(t, dict) else t for t in terms]}


def _open_from_menu(app: LiveApp, start: str) -> dict | None:
    _item, menu, pressed = ax.status_menu(app.pid)
    row = ax.find_row(menu, start) if menu is not None else None
    if row is None:
        if pressed and menu is not None:
            ax.cancel(menu)
        return None
    before = windows_of(app.pid)
    ax.press(row)
    got = app.wait_for(lambda: [w for w in new_windows(before, windows_of(app.pid)) if w["layer"] == 0], 6.0, every=0.05)
    if not got:
        return None
    time.sleep(0.8)
    win = next((w for w in windows_of(app.pid) if w["id"] == got[0]["id"]), got[0])
    if front_app()[0] != app.pid:
        # A row pressed through Accessibility is not a person's click, so macOS 26 does not let
        # either interface take the front with it (seen 2026-11-25, both sides alike); a click
        # on the window's title bar, as a person would give, does.
        x, y, w, _h = win["bounds"]
        drive.click(x + w / 2, y + 12)
        time.sleep(0.5)
    return win


def _ax_window(app: LiveApp, title: str):
    return next((w for w in ax.windows(app.pid) if str(ax.attr(w, "AXTitle") or "") == title), None)


def _texts(app: LiveApp, title: str) -> list[str]:
    win = _ax_window(app, title)
    if win is None:
        return []
    return [e["text"] for e in ax.tree(win) if e["role"] in ("AXStaticText", "AXTextField", "AXTextArea") and e["text"]]


def _ours_in_front(app: LiveApp) -> bool:
    return bool(app.wait_for(lambda: front_app()[0] == app.pid, 1.5, every=0.05))


def _menu(app: LiveApp, before: list[dict]) -> dict | None:
    got = [w for w in new_windows(before, windows_of(app.pid)) if w["layer"] > 0 and w["bounds"][3] > 40]
    return max(got, key=lambda w: w["bounds"][3]) if got else None


def _menu_row_point(menu: dict, index: int, count: int) -> tuple[float, float]:
    x, y, w, h = menu["bounds"]
    step = (h - 2 * MENU_PAD) / count
    return x + w / 2, y + MENU_PAD + step * (index + 0.5)


def history_deep(app: LiveApp, out: Path) -> dict:
    res: dict = {"seeded": [e["text"] or "(failed)" for e in seed_history(app)]}
    win = _open_from_menu(app, "History")
    if win is None:
        return {**res, "error": "the History window did not open"}
    title = win["title"]
    wx, wy, ww, _wh = win["bounds"]
    res["window"] = win
    shoot_window(win["id"], out / "history-rows.png")
    axwin = _ax_window(app, title)
    (out / "history-rows-ax.json").write_text(json.dumps(ax.tree(axwin, frames=True), indent=1))
    res["texts_rows"] = _texts(app, title)
    fields = ax.find(axwin, lambda e: ax.attr(e, "AXRole") == "AXTextField") if axwin is not None else []
    search = ax.frame(fields[0]) if fields else None
    res["search_field"] = [round(v) for v in search] if search else None
    if search is None:
        _close_ax_windows(app)
        return {**res, "error": "no search field through Accessibility"}
    sx, sy, _sw, sh_ = search
    res["front_after_open"] = front_app()[1]
    # Measured on the original window (its Accessibility frames) and the same on the new one by
    # round 1's pictures; relative to the window's top-left corner, title bar included. The rows
    # bleed ROW_PAD_X out of the content column, so the right-hand icon sits this far in.
    row_right = wx + ww - (MARGIN - ROW_PAD_X)
    more_x = row_right - ROW_PAD_X + (ICON_BTN - ICON_SIZE) / 2 - ICON_BTN / 2  # as legacy lays out its More button
    row_text_y = wy + ROW1_Y
    tabs_y = wy + TABS_Y
    # Hover the first row: its icons show.
    drive.glide(*drive.cursor(), sx + 120, row_text_y, speed=1500)
    time.sleep(0.5)
    shoot_window(win["id"], out / "history-row-hover.png")

    def more(index: int, label: str) -> dict:
        before = windows_of(app.pid)
        drive.glide(*drive.cursor(), sx + 120, row_text_y, speed=1500)
        time.sleep(0.3)
        drive.click(more_x, row_text_y)
        menu = app.wait_for(lambda: _menu(app, before), 2.0, every=0.05)
        got: dict = {"menu": menu}
        if not menu:
            return {**got, "error": "the More menu did not open"}
        mx, my, mw, mh = menu["bounds"]
        got["menu_offset_from_button"] = [round(mx - more_x), round(my - row_text_y)]
        shoot_rect((mx - 20, my - 20, mw + 40, mh + 40), out / f"history-more-{label}.png")
        drive.click(*_menu_row_point(menu, index, len(MORE_ROWS)))
        time.sleep(0.8)
        got["front_after_pick"] = front_app()[1] if front_app()[0] != app.pid else "the app"
        return got

    # Search.
    if _ours_in_front(app):
        drive.click(sx + 60, sy + sh_ / 2)
        time.sleep(0.2)
        drive.type_text("Maria")
        time.sleep(0.8)
        res["search_texts"] = _texts(app, title)
        shoot_window(win["id"], out / "history-search.png")
        drive.key("a", ("command",))
        drive.key("delete")
        time.sleep(0.8)
        res["search_cleared_texts"] = _texts(app, title)
    else:
        res["search"] = f"skipped: {front_app()} in front, the app is {app.pid}"
    # Show Original.
    res["show_original"] = more(0, "original")
    res["show_original"]["texts"] = _texts(app, title)
    shoot_window(win["id"], out / "history-original.png")
    res["hide_original"] = more(0, "hide")
    # Fix a Word.
    res["fix"] = more(1, "fix")
    time.sleep(0.3)
    shoot_window(win["id"], out / "history-fix-open.png")
    if _ours_in_front(app):
        drive.type_text(FIXED)
        time.sleep(0.3)
        drive.key("return")
        time.sleep(1.0)
        res["fix"]["store"] = _store(app)
        res["fix"]["texts"] = _texts(app, title)
        shoot_window(win["id"], out / "history-fixed.png")
    # The Dictionary tab, and a word added.
    drive.click(wx + DICTIONARY_X, tabs_y)
    time.sleep(0.8)
    shoot_window(win["id"], out / "history-dictionary.png")
    res["dictionary_texts"] = _texts(app, title)
    axwin = _ax_window(app, title)
    fields = ax.find(axwin, lambda e: ax.attr(e, "AXRole") == "AXTextField") if axwin is not None else []
    add = ax.frame(fields[0]) if fields else None
    res["add_field"] = [round(v) for v in add] if add else None
    if add and _ours_in_front(app):
        drive.click(add[0] + 60, add[1] + add[3] / 2)
        time.sleep(0.2)
        drive.type_text(NEW_WORD)
        drive.key("return")
        time.sleep(0.8)
        res["after_add"] = _store(app)
        res["after_add_texts"] = _texts(app, title)
        shoot_window(win["id"], out / "history-word-added.png")
    # Back to Dictations, then Clear History and its confirmation.
    drive.click(wx + DICTATIONS_X, tabs_y)
    time.sleep(0.6)
    axwin = _ax_window(app, title)
    clear = next(iter(ax.find(axwin, lambda e: str(ax.attr(e, "AXTitle") or "").startswith("Clear History"))), None) if axwin else None
    if clear is not None:
        cf = ax.frame(clear)
        before = windows_of(app.pid)
        drive.click(cf[0] + cf[2] / 2, cf[1] + cf[3] / 2)
        time.sleep(0.8)
        shoot_rect((wx, wy, ww, 420), out / "history-clear-confirm.png")  # the screen: the confirmation is a window of its own
        sheets = [w for w in new_windows(before, windows_of(app.pid))]
        axwin = _ax_window(app, title)
        def is_delete(e) -> bool:
            return ax.attr(e, "AXRole") == "AXButton" and str(ax.attr(e, "AXTitle") or "") == "Delete"

        buttons = ax.find(axwin, is_delete) if axwin else []
        if not buttons:  # a separate dialog window rather than a sheet
            for w in ax.windows(app.pid):
                buttons += ax.find(w, is_delete)
        res["clear"] = {"new_windows": sheets, "delete_button": [round(v) for v in ax.frame(buttons[0])] if buttons else None,
                        "texts": [t for w in ax.windows(app.pid) for t in [e["text"] for e in ax.tree(w) if e["role"] == "AXStaticText"]
                                  if "Delete all" in t or "totals" in t]}
        if buttons:
            bf = ax.frame(buttons[0])
            drive.click(bf[0] + bf[2] / 2, bf[1] + bf[3] / 2)
            time.sleep(1.0)
            res["clear"]["store"] = _store(app)
            res["clear"]["texts_after"] = _texts(app, title)
            shoot_window(win["id"], out / "history-cleared.png")
    else:
        res["clear"] = {"error": "no Clear History button"}
    _close_ax_windows(app)
    res["closed"] = not [w for w in windows_of(app.pid) if w["id"] == win["id"]]
    return res


# -- Setup ----------------------------------------------------------------------------------------

SAFE_PRESS = ("On this Mac", "Download", "Try", "Play", "Read", "Retry")  # controls that stay inside the app
NEVER_PRESS = ("Settings", "Open", "Allow", "Key", "Website", "Support", "Privacy", "Learn", "Quit", "Grant")


def _controls(win) -> list[dict]:
    out = []
    for e in ax.find(win, lambda e: ax.attr(e, "AXRole") in ("AXButton", "AXRadioButton", "AXCheckBox", "AXPopUpButton")):
        title = str(ax.attr(e, "AXTitle") or ax.attr(e, "AXDescription") or "")
        f = ax.frame(e)
        if title and f:
            out.append({"role": str(ax.attr(e, "AXRole")), "title": title, "enabled": bool(ax.attr(e, "AXEnabled")),
                        "value": ax.attr(e, "AXValue") if ax.attr(e, "AXRole") != "AXButton" else None,
                        "frame": [round(v) for v in f]})
    return out


def _config_text(app: LiveApp) -> str:
    try:
        return app.config_path.read_text()
    except OSError:
        return ""


def setup_deep(app: LiveApp, out: Path) -> dict:
    """Each tab of Setup, and on each the controls that stay inside the app, clicked as a person
    clicks; what the window says after each, and what changed in the config. Done closes it."""
    win = _open_from_menu(app, "Setup")
    if win is None:
        return {"error": "the Setup window did not open"}
    title = win["title"]
    res: dict = {"window": win, "tabs": []}
    axwin = _ax_window(app, title)
    tabs = [c for c in _controls(axwin) if c["role"] == "AXRadioButton" and c["frame"][1] < win["bounds"][1] + 200]
    res["tab_names"] = [t["title"] for t in tabs]
    for n, tab in enumerate(tabs):
        x, y, w, h = tab["frame"]
        drive.click(x + w / 2, y + h / 2)
        time.sleep(0.8)
        entry: dict = {"tab": tab["title"]}
        shoot_window(win["id"], out / f"setup-{n}.png")
        axwin = _ax_window(app, title)
        entry["texts"] = _texts(app, title)
        entry["controls"] = _controls(axwin)
        entry["presses"] = []
        for c in entry["controls"]:
            if c["role"] == "AXRadioButton" and c in tabs:
                continue
            if not c["enabled"] or not c["title"].startswith(SAFE_PRESS) or any(w_ in c["title"] for w_ in NEVER_PRESS):
                continue
            cfg0 = _config_text(app)
            before = windows_of(app.pid)
            cx, cy, cw, ch = c["frame"]
            drive.click(cx + cw / 2, cy + ch / 2)
            time.sleep(1.5)
            label = f"setup-{n}-{c['title'].split()[0].lower()}"
            shoot_window(win["id"], out / f"{label}.png")
            cfg1 = _config_text(app)
            entry["presses"].append({"control": c["title"], "texts_after": _texts(app, title),
                                     "config_changed": [line for line in cfg1.splitlines() if line not in cfg0.splitlines()],
                                     "new_windows": new_windows(before, windows_of(app.pid))})
        res["tabs"].append(entry)
    axwin = _ax_window(app, title)
    done = next((c for c in _controls(axwin) if c["title"] == "Done"), None) if axwin else None
    if done:
        x, y, w, h = done["frame"]
        drive.click(x + w / 2, y + h / 2)
        time.sleep(0.8)
    res["closed_by_done"] = not [w for w in windows_of(app.pid) if w["id"] == win["id"]]
    if not res["closed_by_done"]:
        _close_ax_windows(app)
    return res
