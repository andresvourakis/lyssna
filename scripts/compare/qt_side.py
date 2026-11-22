"""The Qt side: build each catalogue state with the Qt interface (lyssna/ui) and write per pair
and scheme a PNG at 2x plus a JSON of its widgets and strings, the same shape legacy_side.py
writes.

    python -m scripts.compare.qt_side --out DIR --work DIR [--only PATTERN] [--menus] [--motion DIR]

On a Mac this runs on Qt's real platform (cocoa), as the app does, because the offscreen platform
draws text about 10 percent lighter than Qt draws it on the Mac (one string: 1535 ink offscreen,
1725 on cocoa, 1712 in AppKit), and with Qt's generic widget style instead of the Mac one the app
gets. Nothing reaches the screen (NoScreen below): the process is an accessory app that is never
activated (no Dock icon), every top-level window gets WA_DontShowOnScreen before Qt maps it (so
show() makes it visible to Qt's logic, which the glides and hover fades need, and never puts it
on screen), the native order-front and activation calls are switched off, and after every pair
the window server is asked whether any window of this process is on screen. The answer is
written to screen.json and the report. LYSSNA_COMPARE_QT_PLATFORM=offscreen brings back the old
platform (and is the default off the Mac).

Floating panels are drawn with their painted shadow switched off (set_shadow(False)), so the
picture is the surface alone, as the legacy side's is (the window server's shadow cannot be
captured there); the shadow is compared by its numbers in the motion and findings notes. The
sliver's sparks are cleared for the same reason. Titled windows are drawn with their window
background; panels on a transparent one."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

PLATFORM = os.environ.get("LYSSNA_COMPARE_QT_PLATFORM") or ("cocoa" if sys.platform == "darwin" else "offscreen")
os.environ["QT_QPA_PLATFORM"] = PLATFORM  # before Qt loads
os.environ["QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"] = "1"  # never a foreground app (lyssna/mac/window.py)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.compare import catalogue  # noqa: E402
from scripts.compare import fixtures as fx  # noqa: E402
from scripts.compare.isolate import isolate  # noqa: E402

SCALE = 2.0
_keep: list = []


class NoScreen:
    """Keeps every window of this process off the screen on the real platform, and checks.

    Qt sends a widget its Show event before it maps the native window (QWidgetPrivate::
    show_helper, then show_sys), and show_sys maps nothing for a widget with WA_DontShowOnScreen.
    So an application event filter sets that attribute on every top-level widget at its Show
    event: Qt then counts the widget as shown (isVisible, which the glides and the hover fades
    ask) while the window server never sees it. Native calls that order a window in or activate
    the app behind Qt's back (host.window.bring_to_top, activate_app, raise and activate on a
    window) are switched off. check() asks the window server for this process's on-screen
    windows; any found are logged as a failure of this guard."""

    def __init__(self, app) -> None:
        from PySide6.QtCore import QEvent, QObject, Qt
        from PySide6.QtWidgets import QWidget

        self.platform = app.platformName()
        self.shows: Counter = Counter()  # windows Qt was asked to show, kept off screen
        self.on_screen: list[str] = []   # windows the window server listed as on screen (must stay empty)
        self.active: list[str] = []      # moments the app was found active (must stay empty)
        self.dpr = float(app.primaryScreen().devicePixelRatio()) if app.primaryScreen() else 0.0
        guard = self

        class Filter(QObject):
            def eventFilter(self, obj, ev) -> bool:
                if ev.type() == QEvent.Type.Show and obj.isWidgetType() and obj.isWindow():
                    if not obj.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):
                        obj.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                    guard.shows[type(obj).__name__] += 1
                return False

        self.filter = Filter()
        app.installEventFilter(self.filter)
        if self.platform != "cocoa":
            return
        import AppKit

        AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        from lyssna import host

        host.window.bring_to_top = lambda widget: None
        host.window.is_on_screen = lambda widget: None  # check() asks instead, for every window
        host.window.activate_app = lambda: None
        raise_ = QWidget.raise_

        def raise_child(self) -> None:  # a child's raise only restacks it inside its window
            if not self.isWindow():
                raise_(self)

        QWidget.raise_ = raise_child
        QWidget.activateWindow = lambda self: None
        # Cocoa answers a new native window with a geometry event carrying the size it was made
        # at, delivered at the next event pass; a panel resized in between (a toast laid out for
        # its text) would snap back to its first size. Deliver it at once, while it is still true.
        win_id = QWidget.winId

        def win_id_settled(self):
            created = self.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
            wid = win_id(self)
            if not created and self.isWindow():
                app.processEvents()
            return wid

        QWidget.winId = win_id_settled

    def check(self, where: str) -> None:
        if self.platform != "cocoa":
            return
        import AppKit
        import Quartz

        pid = os.getpid()
        infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
        mine = [i for i in infos if i.get(Quartz.kCGWindowOwnerPID) == pid]
        if mine:
            self.on_screen.append(f"{where}: {len(mine)} window(s)")
            print(f"qt: WINDOW ON SCREEN at {where}", file=sys.stderr)
        if AppKit.NSRunningApplication.currentApplication().isActive():
            self.active.append(where)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps({"platform": self.platform, "device_pixel_ratio": self.dpr,
                                    "shows_kept_off_screen": dict(self.shows), "on_screen": self.on_screen,
                                    "app_active": self.active}, indent=1), encoding="utf-8")


GUARD: NoScreen | None = None
ELIDED: dict[str, str] = {}  # text a paint cut short with an ellipsis -> what it showed (cleared per picture)


def _record_elisions() -> None:
    """Note every elidedText call that cut its text, so a label shortened with an ellipsis is
    reported (the widget still holds the whole text, and the copy audit reads that)."""
    from PySide6.QtGui import QFontMetrics, QFontMetricsF

    for cls in (QFontMetrics, QFontMetricsF):
        real = cls.elidedText

        def elided(self, text, *args, real=real, **kwargs):
            shown = real(self, text, *args, **kwargs)
            if shown != text and text:
                ELIDED[text] = shown
            return shown

        cls.elidedText = elided
    # The interface cuts its labels through theme.elide (AppKit's cut on the Mac), not elidedText.
    from lyssna.ui import theme

    real_elide = theme.elide

    def elide(text, *args, **kwargs):
        shown = real_elide(text, *args, **kwargs)
        if shown != text and text:
            ELIDED[text] = shown
        return shown

    theme.elide = elide


def boot(work: Path):
    global GUARD
    isolate(work)
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["lyssna-compare"])
    GUARD = NoScreen(app)
    _record_elisions()
    from lyssna import host
    from lyssna.ui import mini_player

    mini_player._vf = lambda screen=None: fx.VF
    mini_player._screen_at = lambda x, y: None
    mini_player._screen_under_mouse = lambda: None
    mini_player._left_button_down = lambda: False
    sb = host.statusbar
    for name in ("set_icon", "show_status", "mark_headers"):
        if hasattr(sb, name):
            setattr(sb, name, lambda *a, **k: None)
    return app


# -- reading values back -----------------------------------------------------------------------


def hexcolor(c) -> str | None:
    from PySide6.QtGui import QColor

    if callable(c) and not isinstance(c, QColor):
        try:
            c = c()
        except TypeError:
            return None
    if not isinstance(c, QColor) or not c.isValid():
        return None
    c = c.toRgb()
    return f"#{c.red():02X}{c.green():02X}{c.blue():02X}{c.alpha():02X}"


QT_WEIGHTS = ((100, "thin"), (200, "ultralight"), (300, "light"), (400, "regular"), (500, "medium"), (600, "semibold"),
              (700, "bold"), (800, "heavy"), (900, "black"))


def font_info(f) -> dict | None:
    if f is None:
        return None
    w = int(f.weight())
    name = min(QT_WEIGHTS, key=lambda kv: abs(kv[0] - w))[1]
    size = f.pixelSize() if f.pixelSize() > 0 else f.pointSizeF()
    return {"family": f.family(), "size": round(float(size), 2), "weight": name, "italic": bool(f.italic()),
            "spacing": round(float(f.letterSpacing()), 3)}


def _plain(s: str) -> str:
    if "<" in s and ">" in s:
        s = re.sub(r"<br\s*/?>", "\n", s)
        s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def _text_of(w) -> str:
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QTextEdit

    value = getattr(w, "value", None)
    if callable(value) and isinstance(w, QLabel):
        return str(value())
    if isinstance(w, QLabel):
        return _plain(w.text())
    if isinstance(w, QLineEdit):
        t = w.text()
        if t and w.echoMode() != QLineEdit.EchoMode.Normal:
            return "•" * len(t)
        return t
    if isinstance(w, QTextEdit):
        return w.toPlainText()
    if isinstance(w, QComboBox):
        return w.currentText()
    if isinstance(w, QAbstractButton):
        return w.text()
    for attr in ("_text", "letter", "title", "text", "label", "_title"):
        v = getattr(type(w), attr, None)
        if callable(v) and attr in ("text",):
            try:
                r = getattr(w, attr)()
                if isinstance(r, str):
                    return r
            except TypeError:
                pass
            continue
        v = getattr(w, attr, None)
        if isinstance(v, str):
            return v
    return ""


def _painted(w, e: dict, depth: int) -> list[dict]:
    """Text a widget paints itself instead of holding text widgets: the history rows' paragraphs
    (_Text objects) and the setup window's key caps. Read for the copy audit; their frames are
    the painting widget's, so the structure table leaves them out ("painted")."""
    found = []
    labels = getattr(w, "labels", None)
    if type(w).__name__ == "KeyCaps" and isinstance(labels, list):
        for t in labels:
            found.append({"cls": "KeyCaps.label", "depth": depth + 1, "frame": e["frame"], "text": t, "painted": True})
    for name, value in vars(w).items():
        if type(value).__name__ != "_Text" or not isinstance(getattr(value, "value", None), str) or not value.value:
            continue
        if value.value == e.get("text"):
            continue  # the widget already says it
        if name == "original" and not getattr(w, "show_original", False):
            continue
        if name == "text" and getattr(w, "editing", False):
            continue
        item = {"cls": f"{type(w).__name__}.{name}", "depth": depth + 1, "frame": e["frame"], "text": value.value,
                "painted": True}
        f = getattr(value, "font", None)
        if f is not None and hasattr(f, "weight"):
            item["font"] = font_info(f)
        c = hexcolor(getattr(value, "color", None))
        if c:
            item["color"] = c
        found.append(item)
    return found


def _own_font(w):
    """The font a self-painting widget draws its text in: its font_ or text_font, the first of
    its runs of (text, font), or else its only QFont attribute. None when it has none of these."""
    from PySide6.QtGui import QFont

    for name in ("font_", "text_font"):
        f = getattr(w, name, None)
        if isinstance(f, QFont):
            return f
    runs = getattr(w, "runs", None)
    if isinstance(runs, list) and runs and isinstance(runs[0], tuple) and len(runs[0]) > 1 and isinstance(runs[0][1], QFont):
        return runs[0][1]
    fonts = [v for v in vars(w).values() if isinstance(v, QFont)]
    return fonts[0] if len(fonts) == 1 else None


def walk(root) -> list[dict]:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QTextEdit, QWidget

    out: list[dict] = []

    def visit(w, depth: int) -> None:
        if w is not root and not w.isVisibleTo(root):
            return
        p = w.mapTo(root, QPoint(0, 0)) if w is not root else QPoint(0, 0)
        e: dict = {"cls": type(w).__name__, "depth": depth, "frame": [p.x(), p.y(), w.width(), w.height()]}
        # A view that paints itself a fraction of a pixel off its whole-pixel frame, where the Mac's
        # sits on a half point (window.place): read where it draws, which is what the Mac's frame says.
        off = getattr(w, "paint_offset", None)
        if off is not None and hasattr(off, "x"):
            e["frame"] = [p.x() + off.x(), p.y() + off.y(), w.width(), w.height()]
        if hasattr(w, "paint_dy") and callable(getattr(w, "thickness", None)):
            e["frame"] = [p.x(), p.y() + w.paint_dy, w.width(), w.thickness()]
        text = _text_of(w)
        if text:
            e["text"] = text
        if isinstance(w, QLineEdit) and w.placeholderText():
            e["placeholder"] = w.placeholderText()
            e["role"] = "field"
        if isinstance(w, QTextEdit):
            e["role"] = "field"
            if w.textCursor().hasSelection():
                e["selected"] = True
        if isinstance(w, QLineEdit) and w.hasSelectedText():
            e["selected"] = True
        if isinstance(w, QComboBox):
            e["role"] = "dropdown"
            e["items"] = [w.itemText(i) for i in range(w.count())]
        icon = getattr(w, "icon", None)
        if isinstance(icon, str) and icon:
            e["icon"] = icon
        sym = getattr(w, "symbol", None)
        if isinstance(sym, str) and sym:
            e["icon"] = sym
        f = _own_font(w)
        if f is None and (text and isinstance(w, (QLabel, QAbstractButton, QLineEdit, QTextEdit, QComboBox))):
            f = w.font()
        if f is not None and hasattr(f, "weight"):
            e["font"] = font_info(f)
        for attr in ("color", "icon_color"):
            v = getattr(w, attr, None)
            if v is not None:
                c = hexcolor(v)
                if c:
                    e["color"] = c
                    break
        fill = getattr(w, "fill", None)
        if fill is not None and not isinstance(fill, (int, float, bool)):
            c = hexcolor(fill)
            if c:
                e["fill"] = c
                r = getattr(w, "radius", 0.0)
                e["radius"] = float(min(w.width(), w.height()) / 2 if r is None else r)
                shape = getattr(w, "shape_rect", None)
                if callable(shape):
                    sr = shape()
                    e["frame"] = [p.x() + sr.x(), p.y() + sr.y(), sr.width(), sr.height()]
                    e["radius"] = float(min(sr.width(), sr.height()) / 2) if r is None else e["radius"]
        border = getattr(w, "border", None)
        if border is not None and not isinstance(border, (int, float, bool)):
            c = hexcolor(border)
            if c:
                e["border"] = c
        if hasattr(w, "_on") and hasattr(w, "glow"):
            e["hover"] = bool(getattr(w, "_on", False))
        if isinstance(w, QAbstractButton) or hasattr(w, "perform_click") or (hasattr(w, "clicked") and callable(
                getattr(w, "clicked", None))):
            e.setdefault("role", "button")
            e["enabled"] = bool(w.isEnabled())
        name = e["cls"]
        roles = {"ProgressBar": "bar", "ScrubBar": "scrub", "MarkView": "mark", "WaveView": "wave", "Sliver": "sliver",
                 "Spinner": "spinner"}
        if name in roles:
            e["role"] = roles[name]
            if name == "ProgressBar":
                e["fraction"] = round(float(w.fraction), 4)
        if w.toolTip():
            e["tooltip"] = w.toolTip()
        if w.accessibleName():
            e["a11y"] = w.accessibleName()
        out.append(e)
        out.extend(_painted(w, e, depth))
        for child in w.children():
            if isinstance(child, QWidget) and not child.isWindow():
                visit(child, depth + 1)

    visit(root, 0)
    return out


# -- drawing ----------------------------------------------------------------------------------


def render(widget, path: Path, window_background: bool) -> tuple[float, float]:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QImage, QPainter, QRegion
    from PySide6.QtWidgets import QApplication, QWidget

    widget.ensurePolished()
    if widget.layout() is not None:
        widget.layout().activate()
    QApplication.processEvents()
    w, h = widget.width(), widget.height()
    img = QImage(round(w * SCALE), round(h * SCALE), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(SCALE)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    flags = QWidget.RenderFlag.DrawChildren
    if window_background:
        flags |= QWidget.RenderFlag.DrawWindowBackground
    widget.render(p, QPoint(0, 0), QRegion(), flags)
    p.end()
    img.save(str(path))
    return float(w), float(h)


# -- builders ---------------------------------------------------------------------------------


def _player():
    from lyssna.ui.mini_player import MiniPlayer

    mp = MiniPlayer()
    mp.auto_hide = False
    mp.voices_fn = lambda: fx.VOICES
    mp.key_name = "fn"
    mp.read_key = "⌃⌥S"
    mp.set_tooltips(catalogue.TOOLTIPS)
    _keep.append(mp)
    return mp


def build_player(params: dict) -> dict:
    from lyssna.ui import mini_player as M

    form = params["form"]
    notes = ["painted shadow switched off for the picture (the legacy window-server shadow cannot be captured)"]
    if form == "zone":
        z = (M.FlatDropZone if params.get("flat") else M.DropZone)()
        z.order_front()
        _keep.append(z)
        return {"owner": z, "notes": notes}
    mp = _player()
    if form == "card":
        mp._update(fx.reading(**params["status"]))
        if params.get("scrub") is not None:
            f = params["scrub"]
            mp.scrub.set_hover(True)
            mp.scrub.scrubbing = True
            mp.scrub.bar.set_fraction(f)
            mp.scrub._layout()
            mp._scrub_preview(f)
    elif form == "sliver":
        mp.layout = {"dock": params["dock"], "x": 0.0, "y": 0.5}
        st = fx.reading(**params["status"])
        mp._update(st)
        mp.sliver.preview_moment(t=catalogue.DRIFT_T, bloom=params.get("bloom") if st.state == "playing" else None)
        mp.sliver._sparks = []
        notes.append("sparks cleared for the picture (the legacy emitter cannot be drawn offscreen)")
    elif form == "pill":
        mp.layout = {"dock": params["dock"], "x": 0.0, "y": 0.5}
        mp._update(fx.reading(**params["status"]))
        mp._open_pill()
        mp._cancel_anim()
        mp._set_scale(1.0, animate=False)
    elif form in ("rest", "menu"):
        mp.dictation_layout = {"dock": params["edge"], "y": -1.0}
        mp.show_idle()
        if form == "menu":
            mp._open_menu()
            mp._cancel_anim()
            mp._set_scale(1.0, animate=False)
    elif form == "dictate":
        mp.dictation_layout = {"dock": params["edge"], "y": -1.0}
        mp._level = 0.5
        mp._set_dictation("recording")
        mp._set_dictation(params["state"])
        mp._set_hands_free(params["state"] == "locked", animate=False)
        mp.wave.stop()
        mp.wave._last = mp.wave._breath_start = 0.0
        for i in range(1, catalogue.WAVE_TICKS + 1):
            mp.wave.tick(i / 30)
    if mp._scale_anim is not None:
        mp._cancel_anim()
    return {"owner": mp, "notes": notes}


def build_toast(params: dict) -> dict:
    from lyssna.ui.toast import TOAST_H, Toast

    t = Toast()
    _keep.append(t)
    width = t.layout(params["text"], params["action"], params["max_w"])
    t.set_frame((0, 0, width, TOAST_H))
    t.set_alpha(1.0)
    return {"owner": t, "notes": ["painted shadow switched off"]}


def build_tip(params: dict) -> dict:
    from lyssna.ui.toast import Tip

    t = Tip()
    _keep.append(t)
    t.lay_out(fx.TIP_ANCHOR, params["edge"], params["text"], params["key"])
    return {"owner": t, "notes": ["painted shadow switched off"]}


def build_selection(params: dict) -> dict:
    from lyssna.ui.selection_button import SelectionButton

    b = SelectionButton()
    _keep.append(b)
    if params.get("hover"):
        b.button.set_hover(True, animate=False)
    return {"owner": b, "notes": ["painted shadow switched off"]}


def build_mark(params: dict) -> dict:
    from lyssna.ui.mark import MarkView

    m = MarkView(params["height"])
    _keep.append(m)
    return {"widget": m}


def build_wave(params: dict) -> dict:
    from lyssna.ui.wave import WaveView

    v = WaveView(horizontal=params["horizontal"])
    level = params["level"]
    v.level_fn = lambda: level
    v.set_mode(params["mode"])
    v._last = v._breath_start = 0.0
    for i in range(1, catalogue.WAVE_TICKS + 1):
        v.tick(i / 30)
    _keep.append(v)
    return {"widget": v}


def build_setup(params: dict) -> dict:
    from unittest import mock

    from lyssna import host
    from lyssna.dictation import transcribers
    from lyssna.providers import kokoro
    from lyssna.ui import setup_window as SW

    local = transcribers.REGISTRY[transcribers.LOCAL]
    ctl = fx.FakeSetupController(**params["ctl"])
    with mock.patch.object(host, "DEVICE", "Mac"), mock.patch.object(host, "MENU_PLACE", "the menu bar"), \
            mock.patch.object(host, "NEEDS_ACCESSIBILITY", True), mock.patch.object(host, "HAS_GLOBE_KEY", True), \
            mock.patch.object(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True)), \
            mock.patch.object(local, "can_run", classmethod(lambda cls: True)), \
            mock.patch.object(kokoro.DOWNLOAD, "error", params["error"]):
        w = SW.SetupWindow(ctl)
        _keep.append(w)
        w.height_limit = lambda: float("inf")
        w.key_table = SW._key_table("mac")
        w.taps_ok = lambda: params["taps_ok"]
        trusted = params["trusted"]
        w.accessibility_trusted = lambda prompt=False: trusted
        globe = params["globe"]
        w.globe_action = lambda: globe
        w._globe = globe
        engine, dictation = params["engine"], params["dictation"]
        w.prepare(engine, dictation)
        if engine is None and dictation is None:
            w._select_tab(SW.DICTATE if params["tab"] == "dictate" else SW.LISTEN)
        if params["key"]:
            w.key_field.setText(params["key"])
        if params["saving"]:
            w._saving = True
            w._key_message = ("Checking the key…", "secondary")
            w.spinner.start()
        elif params["message"] is not None:
            w._key_message = tuple(params["message"])
        if params["dict_saving"] is not None:
            row = w.key_rows[params["dict_saving"]]
            row.field.setText("sk-preview")
            row.saving = True
            row.message = ("Checking the key…", "secondary")
            row.spinner.start()
        if params["dict_message"] is not None:
            index, text, tone = params["dict_message"]
            w.key_rows[index].message = (text, tone)
        w._refresh(force=True)
    return {"widget": w, "window": True, "title": w.windowTitle()}


def build_history(params: dict, work: Path) -> dict:
    from lyssna import support
    from lyssna.ui import history_window as HW

    folder = work / f"history-{len(_keep)}"
    store, vocab, clock = fx.history_data(folder, entries=params.get("entries", True), terms=params.get("terms", True))
    w = HW.HistoryWindow()
    _keep.append(w)
    w.store, w.vocabulary, w.now = store, vocab, clock
    support.SITE_URL = fx.SUPPORT_SITE if params.get("supporter") else ""
    w.prepare()
    support.SITE_URL = ""
    size = params.get("size") or fx.HISTORY_SIZE
    w.resize(round(size[0]), round(size[1]))
    w._layout()
    rows = [i for i in w.items if isinstance(i, HW.DictationRow)]
    variant = params["variant"]
    if variant == "expanded":
        long_row = next(r for r in rows if len(r.entry.text) > 300)
        w._expanded.add(long_row.entry_id)
        w._originals.add(rows[0].entry_id)
        w._rebuild_list()
    elif variant == "learned":
        w._learned[rows[0].entry_id] = HW.learned_text(["Mattias"])
        w._rebuild_list()
    elif variant == "hover":
        rows[0].set_hover(True)
        rows[1].set_hover(True)
        rows[1].copy.set_icon("checkmark")
        rows[1].more.set_hover(True)
    elif variant == "fix":
        # The app's own path for "Fix a Word…" (the legacy side calls fixWord_ the same way):
        # the edit field opens with its whole text selected.
        first = rows[0].entry_id
        w.fix_word(first)
        w._row(first).edit_box.set_focused(True)
    elif variant == "search":
        w.search_field.setText(params["query"])
        w.search_box.set_focused(True)
    elif variant == "dictionary":
        w.select_tab("dictionary")
        if params.get("note"):
            w._add_note = params["note"]
            w._layout()
        if params.get("terms", True):
            next(i for i in w.items if isinstance(i, HW.TermRow)).set_hover(True)
    return {"widget": w, "window": True, "title": w.windowTitle()}


def build_whats_new(params: dict) -> dict:
    from lyssna import support, whatsnew
    from lyssna.ui.whats_new_window import WhatsNewWindow

    whatsnew.NOTES = {**whatsnew.NOTES, fx.WHATS_NEW_VERSION: fx.WHATS_NEW_NOTES}
    support.SITE_URL = fx.SUPPORT_SITE if params["support"] else ""
    w = WhatsNewWindow()
    _keep.append(w)
    w.version = fx.WHATS_NEW_VERSION
    w.prepare()
    support.SITE_URL = ""
    return {"widget": w, "window": True, "title": w.windowTitle()}


def build(pair, work: Path) -> dict:
    kind = pair.kind
    builders = {"player": build_player, "toast": build_toast, "tip": build_tip, "selection": build_selection,
                "mark": build_mark, "wave": build_wave, "setup": build_setup, "whats_new": build_whats_new}
    if kind == "history":
        return build_history(pair.params, work)
    return builders[kind](pair.params)


def subject_widget(subject: dict):
    owner = subject.get("owner")
    if owner is not None:
        if owner.margin > 0:
            owner.set_shadow(False)
        return owner.panel
    return subject["widget"]


# -- menus ------------------------------------------------------------------------------------


def dump_menu(menu) -> list[dict]:
    rows = []
    for a in menu.actions():
        if a.isSeparator():
            rows.append({"separator": True, "hidden": not a.isVisible()})
            continue
        title, _, keys = a.text().partition("\t")
        rows.append({"title": title, "hidden": not a.isVisible(), "enabled": a.isEnabled() and a.data() != "header",
                     "checked": a.isCheckable() and a.isChecked(), "shortcut": keys, "header": a.data() == "header",
                     "submenu": a.menu() is not None, "image": not a.icon().isNull(), "tooltip": a.toolTip()
                     if a.toolTip() != title else ""})
    return rows


def menus(out: Path, only: str | None) -> None:
    from lyssna import config
    from lyssna.controller import Controller
    from lyssna.mac import statusbar as mac_statusbar
    from lyssna.player import Status
    from lyssna.providers import kokoro
    from lyssna.ui import history_window as HW
    from lyssna.ui import tray as T

    names = catalogue.select_names(catalogue.MENUS, only)
    if not names:
        return
    kokoro.KokoroProvider.can_run = classmethod(lambda cls: True)
    kokoro.KokoroProvider.prepare = lambda self, on_error=None: None
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")

    class CompareTray(T.Tray):
        key_text = staticmethod(T._preview_keys("mac"))
        style_header = staticmethod(lambda a, menu: None)

    tray = CompareTray(Controller())
    tray.timer.stop()
    tray.item_quit.setText(f"Quit Lyssna\t{T._preview_keys('mac')(mac_statusbar.QUIT_SHORTCUT)}")
    tray._tick()
    result: dict = {}
    subs = {"menu.speed": tray.speed_menu, "menu.voice": tray.voice_menu, "menu.provider": tray.provider_menu,
            "menu.transcription": tray.transcription_menu, "menu.cleanup": tray.cleanup_menu,
            "menu.settings": tray.settings_menu, "menu.keys": tray.key_menu}
    if "menu.idle" in names:
        result["menu.idle"] = dump_menu(tray.menu)
    for name, m in subs.items():
        if name in names:
            result[name] = dump_menu(m)
    if "menu.reading" in names:
        Controller.status = property(lambda self: Status(state="playing", index=2, total=5,
                                                         text="Select any text, press the shortcut, and I will read it to you."))
        tray._tick()
        result["menu.reading"] = dump_menu(tray.menu)
    mp = _player()
    mp._update(fx.reading())
    popped: list = []
    mp._pop = lambda menu, anchor: popped.append(menu)
    for name, action in (("popup.speed", mp._speed_menu), ("popup.voice", mp._voice_menu)):
        if name in names:
            popped.clear()
            action()
            result[name] = dump_menu(popped[0]) if popped else []
    if "popup.history-more" in names:
        store, vocab, clock = fx.history_data(out / "menus-history-qt")
        w = HW.HistoryWindow()
        _keep.append(w)
        w.store, w.vocabulary, w.now = store, vocab, clock
        w.prepare()
        entry = next(i for i in w.items if isinstance(i, HW.DictationRow)).entry
        result["popup.history-more"] = dump_menu(w._more_menu(entry))
    (out / "menus.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    # A picture of the top-level menu, for the record (an NSMenu cannot be drawn offscreen).
    for scheme in catalogue.SCHEMES:
        from lyssna.ui import theme

        theme.set_scheme(scheme)
        T._preview_palette(tray.menu)
        tray.menu.ensurePolished()
        tray.menu.resize(tray.menu.sizeHint())
        render(tray.menu, out / f"menu.idle-{scheme}.png", True)
    from lyssna.ui import theme

    theme.set_scheme(None)


def ink_check(out: Path) -> None:
    """The text weight check (catalogue.INK_TEXT): the string drawn with QPainter at 2x, light
    on dark and dark on light."""
    import numpy as np
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage, QPainter

    from lyssna.ui import theme

    w, h = catalogue.INK_CANVAS
    cases = {}
    for case, (bg, fg) in catalogue.INK_CASES.items():
        # Each case in the scheme it belongs to: Qt picks its glyph rasterisation by the
        # application's scheme (theme.set_scheme), as a Mac in that appearance would.
        theme.set_scheme("light" if fg < bg else "dark")
        img = QImage(round(w * SCALE), round(h * SCALE), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(SCALE)
        img.fill(QColor(bg, bg, bg))
        p = QPainter(img)
        p.setFont(theme.ui_font(catalogue.INK_SIZE, catalogue.INK_WEIGHT))
        p.setPen(QColor(fg, fg, fg))
        p.drawText(QPointF(10, 30), catalogue.INK_TEXT)
        p.end()
        a = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(img.height(), img.bytesPerLine())
        grey = a[:, : img.width() * 4].reshape(img.height(), img.width(), 4)[..., :3].mean(axis=2)
        cases[case] = catalogue.ink_sum(grey, bg, fg)
    theme.set_scheme(None)
    (out / "ink.json").write_text(json.dumps({"platform": PLATFORM, "text": catalogue.INK_TEXT, "cases": cases}),
                                  encoding="utf-8")


# -- main -------------------------------------------------------------------------------------


def run_pairs(out: Path, work: Path, only: str | None, schemes: tuple[str, ...]) -> int:
    from PySide6.QtWidgets import QApplication

    from lyssna.ui import theme

    count = 0
    for scheme in schemes:
        theme.set_scheme(scheme)
        for pair in catalogue.select(only):
            stem = f"{pair.name}-{scheme}"
            meta: dict = {"name": pair.name, "scheme": scheme, "side": "qt", "moment": pair.moment}
            try:
                subject = build(pair, work)
                widget = subject_widget(subject)
                QApplication.processEvents()
                ELIDED.clear()
                w, h = render(widget, out / f"{stem}.png", bool(subject.get("window")))
                meta["elided"] = dict(ELIDED)
                meta.update(size=[w, h], scale=SCALE, method=f"QWidget.render ({PLATFORM})", notes=subject.get("notes", []),
                            title=subject.get("title", ""), elements=walk(widget))
                count += 1
            except Exception as exc:
                meta["error"] = f"{type(exc).__name__}: {exc}"
                meta["traceback"] = traceback.format_exc()
                print(f"qt {stem}: {meta['error']}", file=sys.stderr)
            (out / f"{stem}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
            GUARD.check(stem)
    theme.set_scheme(None)
    return count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.compare.qt_side")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--only")
    ap.add_argument("--schemes", default="light,dark")
    ap.add_argument("--menus", action="store_true")
    ap.add_argument("--motion", metavar="DIR")
    args = ap.parse_args(argv)
    sys.modules.setdefault("scripts.compare.qt_side", sys.modules[__name__])  # motion.py reaches this booted copy
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    boot(work)
    ink_check(out)
    n = run_pairs(out, work, args.only, tuple(args.schemes.split(",")))
    print(f"qt: rendered {n} pictures")
    if args.menus:
        try:
            menus(out, args.only)
        except Exception:
            traceback.print_exc()
    try:
        from scripts.compare import icons

        print(f"qt: drew {icons.qt_side(out / 'icons', args.only)} icons")
    except Exception:
        traceback.print_exc()
    if args.motion:
        from scripts.compare import motion

        motion.qt_filmstrips(Path(args.motion), args.only)
    GUARD.check("end")
    GUARD.write(out / "screen.json")
    if GUARD.on_screen:
        print(f"qt: windows reached the screen: {GUARD.on_screen}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
