"""The legacy side: build each catalogue state with today's AppKit interface (lyssna/legacy/ui),
offscreen, and write per pair and scheme a PNG at 2x plus a JSON of its elements and strings.

    python -m scripts.compare.legacy_side --out DIR [--only PATTERN] [--menus] [--motion]

Nothing reaches the screen: every panel's order_front is replaced, windows are built but never
ordered in, and the app stays an accessory (no Dock icon, never activated). The pictures come
from the view tree itself:

  * layer:  the view's backing layer through CALayer.renderInContext_, which draws Core
            Animation content (the sliver's gradient, the wave's shape layer, the knob, the hover
            glows). With a frozen moment, the presentation layer is drawn instead, after every
            animation the pair names is re-timed to its age under a stopped root clock.
  * cache:  NSView.cacheDisplayInRect_toBitmapImageRep_, for the titled windows and plain
            drawRect_ views (it draws what drawRect_ draws, and the layer-backed subviews).

What cannot be drawn offscreen and is left out (and said so in the JSON notes): the window
server's shadow under every panel, the sliver's spark emitter, and a spinning
NSProgressIndicator's motion."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.compare import catalogue  # noqa: E402
from scripts.compare import fixtures as fx  # noqa: E402
from scripts.compare.isolate import isolate  # noqa: E402

SCALE = 2.0
T0 = 1000.0  # the stopped root clock frozen animations are evaluated against

AppKit = Quartz = objc = None  # imported in boot(), after the isolation


# -- boot -------------------------------------------------------------------------------------


def boot(work: Path) -> None:
    global AppKit, Quartz, objc
    isolate(work)
    import AppKit as _AppKit
    import objc as _objc
    import Quartz as _Quartz

    AppKit, Quartz, objc = _AppKit, _Quartz, _objc
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    _patch_legacy()


class _Timer:
    def cancel(self) -> None:
        pass


def _patch_legacy() -> None:
    """Every screen lookup on the fake screen, no timers, no window on screen."""
    import importlib
    import pkgutil

    import lyssna.legacy.ui as ui_pkg
    from lyssna.legacy.ui import panel as panel_mod

    vf = AppKit.NSMakeRect(*fx.VF)
    real_vf = panel_mod.visible_frame
    for info in pkgutil.iter_modules(ui_pkg.__path__, "lyssna.legacy.ui."):
        mod = importlib.import_module(info.name)
        if getattr(mod, "visible_frame", None) is real_vf:
            mod.visible_frame = lambda screen=None: vf
        if hasattr(mod, "later"):
            mod.later = lambda delay, fn, *args: _Timer()
        if hasattr(mod, "copy_to_clipboard"):
            mod.copy_to_clipboard = lambda text: None

    def order_front(self):
        self._cmp_on = True

    def hide(self):
        self._cmp_on = False

    def visible(self):
        return bool(getattr(self, "_cmp_on", False))

    panel_mod.FloatingPanel.order_front = order_front
    panel_mod.FloatingPanel.hide = hide
    panel_mod.FloatingPanel.visible = visible


def appearance(scheme: str):
    name = AppKit.NSAppearanceNameDarkAqua if scheme == "dark" else AppKit.NSAppearanceNameAqua
    return AppKit.NSAppearance.appearanceNamed_(name)


# -- reading values back ------------------------------------------------------------------------


def hexcolor(color, app) -> str | None:
    """An NSColor (or CGColor) as #RRGGBBAA in sRGB, resolved in appearance `app`."""
    if color is None:
        return None
    out: list = []

    def resolve():
        c = color
        if not isinstance(c, AppKit.NSColor):
            c = AppKit.NSColor.colorWithCGColor_(c)
        if c is None:
            return
        s = c.colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
        if s is not None:
            out.append(s.getRed_green_blue_alpha_(None, None, None, None))

    app.performAsCurrentDrawingAppearance_(resolve)
    if not out:
        return None
    r, g, b, a = (max(0.0, min(1.0, float(v))) for v in out[0])
    return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}{round(a * 255):02X}"


WEIGHTS = ((-0.8, "ultralight"), (-0.6, "thin"), (-0.4, "light"), (0.0, "regular"), (0.23, "medium"),
           (0.3, "semibold"), (0.4, "bold"), (0.56, "heavy"), (0.62, "black"))


def font_info(font) -> dict | None:
    if font is None:
        return None
    traits = font.fontDescriptor().objectForKey_(AppKit.NSFontTraitsAttribute) or {}
    w = float(traits.get(AppKit.NSFontWeightTrait, 0.0) or 0.0)
    name = min(WEIGHTS, key=lambda kv: abs(kv[0] - w))[1]
    italic = bool(AppKit.NSFontManager.sharedFontManager().traitsOfFont_(font) & AppKit.NSItalicFontMask)
    return {"family": str(font.familyName()), "size": round(float(font.pointSize()), 2), "weight": name,
            "italic": italic}


def symbol_of(image) -> str:
    if image is None:
        return ""
    d = image.accessibilityDescription()
    return str(d) if d else ""


def _frame_in(view, root) -> list[float]:
    r = view.convertRect_toView_(view.bounds(), root)
    h = root.bounds().size.height
    y = r.origin.y if root.isFlipped() else h - r.origin.y - r.size.height
    return [round(float(r.origin.x), 2), round(float(y), 2), round(float(r.size.width), 2), round(float(r.size.height), 2)]


def _text(view) -> str:
    if isinstance(view, AppKit.NSSecureTextField):
        return "•" * len(str(view.stringValue() or ""))
    if isinstance(view, AppKit.NSTextField):
        return str(view.stringValue() or "")
    if isinstance(view, AppKit.NSPopUpButton):
        return str(view.titleOfSelectedItem() or "")
    if isinstance(view, AppKit.NSButton):
        title = str(view.title() or "")
        if title == "Button" and view.image() is not None:
            return ""  # an image-only button keeps AppKit's default title, which never shows
        return title
    return ""


def walk(root, app) -> list[dict]:
    """Every visible view under `root`, in tree order, with what the comparison reads of it."""
    out: list[dict] = []

    def visit(view, depth: int) -> None:
        if view.isHidden():
            return
        e: dict = {"cls": type(view).__name__, "depth": depth, "frame": _frame_in(view, root)}
        text = _text(view)
        if text:
            e["text"] = text
        if isinstance(view, AppKit.NSTextField):
            ph = view.placeholderString()
            if not ph and view.placeholderAttributedString() is not None:
                ph = view.placeholderAttributedString().string()
            if ph:
                e["placeholder"] = str(ph)
            e["font"] = font_info(view.font())
            e["color"] = hexcolor(view.textColor(), app)
            if view.isEditable():
                e["role"] = "field"
        elif isinstance(view, AppKit.NSButton):
            sym = symbol_of(view.image())
            if sym:
                e["icon"] = sym
            if text:
                e["font"] = font_info(view.font())
            e["color"] = hexcolor(view.contentTintColor(), app)
            e["enabled"] = bool(view.isEnabled())
            e["role"] = "button"
            if isinstance(view, AppKit.NSPopUpButton):
                e["role"] = "dropdown"
                e["items"] = [str(t) for t in view.itemTitles()]
        elif isinstance(view, AppKit.NSImageView):
            sym = symbol_of(view.image())
            if sym:
                e["icon"] = sym
            e["color"] = hexcolor(view.contentTintColor(), app)
            e["role"] = "image"
        if hasattr(view, "color_fn") and callable(getattr(view, "color_fn", None)):
            e["fill"] = hexcolor(view.color_fn(), app)
            e["radius"] = float(getattr(view, "radius", 0.0) or 0.0)
            border = getattr(view, "border_fn", None)
            if callable(border):
                e["border"] = hexcolor(border(), app)
            glow = getattr(view, "glow", None)
            if glow is not None and hasattr(glow, "opacity"):
                e["hover"] = bool(glow.opacity() > 0.5)
        name = e["cls"]
        if name in ("ProgressBar",):
            e["role"] = "bar"
            e["fraction"] = round(float(view.fraction), 4)
        elif name == "ScrubBar":
            e["role"] = "scrub"
        elif name in ("MarkView", "HistoryEmptyMark"):
            e["role"] = "mark"
        elif name == "WaveView":
            e["role"] = "wave"
        elif name == "Sliver":
            e["role"] = "sliver"
        elif name == "NSProgressIndicator":
            e["role"] = "spinner"
        tip = view.toolTip()
        if tip:
            e["tooltip"] = str(tip)
        try:
            label = view.accessibilityLabel()
        except Exception:
            label = None
        if label:
            e["a11y"] = str(label)
        out.append(e)
        if isinstance(view, (AppKit.NSButton, AppKit.NSTextField, AppKit.NSPopUpButton)):
            return  # a control's own subviews (its cell's editor) are not interface
        for sub in view.subviews():
            visit(sub, depth + 1)

    visit(root, 0)
    return out


# -- drawing ------------------------------------------------------------------------------------


def _retime(layer, ages: dict[str, float]) -> list[str]:
    """Re-time the animations `ages` names to that age under the root clock T0; remove every
    other animation (implicit ones still in flight), so the model is what shows elsewhere."""
    dropped: list[str] = []
    layer.setSpeed_(1.0)
    layer.setTimeOffset_(0.0)
    layer.setBeginTime_(0.0)
    for key in list(layer.animationKeys() or []):
        anim = layer.animationForKey_(key)
        if key in ages:
            copy = anim.copy()
            copy.setBeginTime_(T0 - ages[key])
            layer.addAnimation_forKey_(copy, key)
        else:
            dropped.append(str(key))
            layer.removeAnimationForKey_(key)
    for sub in layer.sublayers() or []:
        dropped += _retime(sub, ages)
    return dropped


def _png(cgimage, path: Path) -> None:
    rep = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(cgimage)
    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
    data.writeToFile_atomically_(str(path), True)


def _text_doubles(view) -> list:
    """The private label views AppKit puts inside each NSTextField (NSTextFieldSimpleLabel).
    Both capture paths draw a label's text twice, once through the field's own cell and once
    through this subview: cacheDisplayInRect draws both views, and renderInContext draws the
    subview's layer contents and, for the field's layer, which has no contents, calls the
    field's drawing. So a dim 0.60 label came out at 0.84 (a real screenshot of today's app,
    docs/images/setup.png, measures 0.60) and full-colour text came out heavier at its edges.
    Hidden, view and layer, during the capture (after the layout and display passes, which
    show them again), the cell draws the text once."""
    found = []
    for sub in view.subviews():
        if isinstance(view, AppKit.NSTextField):
            if "SimpleLabel" in type(sub).__name__ and not sub.isHidden():
                found.append(sub)
        else:
            found += _text_doubles(sub)
    return found


def _once(view, app, draw) -> None:
    """Run `draw` in appearance `app` with every label's text drawn once (_text_doubles)."""
    doubles = _text_doubles(view)
    for v in doubles:
        v.setHidden_(True)
        if v.layer() is not None:
            v.layer().setHidden_(True)
    try:
        app.performAsCurrentDrawingAppearance_(draw)
    finally:
        for v in doubles:
            v.setHidden_(False)
            if v.layer() is not None:
                v.layer().setHidden_(False)


def render(view, app, path: Path, method: str = "layer", ages: dict[str, float] | None = None) -> tuple[float, float, str]:
    """Draw `view` at SCALE into `path`. Returns its logical size and the method used."""
    window = view.window()
    if window is not None:
        window.setAppearance_(app)
        window.contentView().layoutSubtreeIfNeeded()
        window.displayIfNeeded()
    view.layoutSubtreeIfNeeded()
    view.setNeedsDisplay_(True)
    view.displayIfNeeded()
    Quartz.CATransaction.flush()
    w, h = float(view.bounds().size.width), float(view.bounds().size.height)
    pw, ph = max(1, math.ceil(w * SCALE)), max(1, math.ceil(h * SCALE))
    layer = view.layer()
    if method == "layer" and layer is not None:
        target = layer
        if ages is not None:
            _retime(layer, ages)
            layer.setSpeed_(0.0)
            layer.setTimeOffset_(T0)
            Quartz.CATransaction.flush()
            target = layer.presentationLayer() or layer
        cs = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
        ctx = Quartz.CGBitmapContextCreate(None, pw, ph, 8, 0, cs, Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextScaleCTM(ctx, SCALE, SCALE)
        _once(view, app, lambda: target.renderInContext_(ctx))
        _png(Quartz.CGBitmapContextCreateImage(ctx), path)
        return w, h, "layer" + (" (presentation, frozen)" if ages is not None else "")
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, pw, ph, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 32)
    rep.setSize_((w, h))
    _once(view, app, lambda: view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep))
    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
    data.writeToFile_atomically_(str(path), True)
    return w, h, "cache"


def host_window(view):
    """A borderless window that is never shown, holding a standalone view (the mark, the wave)."""
    f = view.frame()
    win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(0, 0, f.size.width, f.size.height), AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False)
    win.setOpaque_(False)
    win.setBackgroundColor_(AppKit.NSColor.clearColor())
    win.setReleasedWhenClosed_(False)
    holder = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, f.size.width, f.size.height))
    holder.setWantsLayer_(True)
    win.setContentView_(holder)
    view.setFrameOrigin_((0, 0))
    holder.addSubview_(view)
    _keep.append(win)
    return holder


_keep: list = []  # windows and owners the pictures are taken from, kept alive for the run


# -- builders -----------------------------------------------------------------------------------
#
# Each returns a dict: view (what is drawn), method, ages (a frozen moment, or None), notes,
# title (a window title, if any), strings (extra (kind, text) pairs the copy audit reads).


def _player_owner():
    from lyssna.legacy.ui import mini_player as mpm

    p = mpm.MiniPlayer.alloc().init()
    p.auto_hide = False
    p.voices_fn = lambda: fx.VOICES
    p.key_name = "fn"
    p.read_key = "⌃⌥S"
    p.set_tooltips(catalogue.TOOLTIPS)
    _keep.append(p)
    return p


def build_player(params: dict, app) -> dict:
    form = params["form"]
    notes = ["no window shadow: the window server draws it, and it cannot be captured offscreen"]
    if form == "zone":
        from lyssna.legacy.ui import mini_player as mpm

        z = (mpm.FlatDropZone if params.get("flat") else mpm.DropZone).alloc().init()
        _keep.append(z)
        z.panel.setAppearance_(app)
        return {"view": z.panel.contentView(), "method": "layer", "ages": None, "notes": notes}
    p = _player_owner()
    p.panel.setAppearance_(app)
    ages = None
    if form == "card":
        p._update(fx.reading(**params["status"]))
        if params.get("scrub") is not None:
            f = params["scrub"]
            p.scrub.hovering = True
            p.scrub.scrubbing = True
            p.scrub.bar.set_fraction(f)
            p.scrub.setNeedsDisplay_(True)
            p._scrub_preview(f)
    elif form == "sliver":
        p.layout = {"dock": params["dock"], "x": 0.0, "y": 0.5}
        p._update(fx.reading(**params["status"]))
        ages = {"hue": catalogue.DRIFT_T, "split": catalogue.DRIFT_T, "breathe": catalogue.DRIFT_T}
        if params.get("bloom") is not None:
            p.sliver.pulse()
            ages["bloom"] = params["bloom"]
        notes.append("the legacy 'swing' and 'swing2' drifts (startPoint, endPoint) are left out: their values are "
                     "Python tuples bridged to NSArray, which Core Animation cannot evaluate (presentationLayer raises)")
        notes.append("no sparks: the CAEmitterLayer's particles live in the render server")
    elif form == "pill":
        p.layout = {"dock": params["dock"], "x": 0.0, "y": 0.5}
        p._update(fx.reading(**params["status"]))
        p._open_pill()
        p._cancel_anim()
        p._set_scale(1.0, animate=False)
    elif form in ("rest", "menu"):
        p.dictation_layout = {"dock": params["edge"], "y": -1.0}
        p.show_idle()
        if form == "menu":
            p._open_menu()
            p._cancel_anim()
            p._set_scale(1.0, animate=False)
    elif form == "dictate":
        p.dictation_layout = {"dock": params["edge"], "y": -1.0}
        p._level = 0.5
        p._set_dictation("recording")
        p._set_dictation(params["state"])
        p._set_hands_free(params["state"] == "locked", animate=False)
        p.wave.stop()
        p.wave._last = 0.0
        for i in range(1, catalogue.WAVE_TICKS + 1):
            p.wave.tick(i / 30)
        ages = {"breathe": catalogue.WAVE_TICKS / 30} if params["state"] == "working" else {}
    else:
        raise ValueError(f"unknown player form {form}")
    view = p.panel.contentView()
    return {"view": view, "method": "layer", "ages": ages, "notes": notes, "owner": p}


def build_toast(params: dict, app) -> dict:
    from lyssna.legacy.ui.toast import TOAST_H, Toast

    t = Toast.alloc().init()
    _keep.append(t)
    t.panel.setAppearance_(app)
    width = t.layout(params["text"], params["action"], params["max_w"])
    t.panel.setFrame_display_(((0, 0), (width, TOAST_H)), False)
    return {"view": t.panel.contentView(), "method": "layer", "ages": None,
            "notes": ["no window shadow (window server)", "window alpha (the fade) is not part of the picture"]}


def build_tip(params: dict, app) -> dict:
    from lyssna.legacy.ui.toast import Tip

    t = Tip.alloc().init()
    _keep.append(t)
    t.panel.setAppearance_(app)
    t.show_beside(fx.TIP_ANCHOR, params["edge"], params["text"], params["key"])
    return {"view": t.panel.contentView(), "method": "layer", "ages": None, "notes": ["no window shadow (window server)"]}


def build_selection(params: dict, app) -> dict:
    from lyssna.legacy.ui.selection_button import SelectionButton

    b = SelectionButton.alloc().init()
    _keep.append(b)
    b.panel.setAppearance_(app)
    notes = ["no window shadow (window server)"]
    if params.get("hover"):
        notes.append("the legacy button has no hover state: this is the same picture as the resting one")
    return {"view": b.panel.contentView(), "method": "layer", "ages": None, "notes": notes}


def build_mark(params: dict, app) -> dict:
    from lyssna.legacy.ui.mark import MarkView

    m = MarkView.make(params["height"])
    holder = host_window(m)
    holder.window().setAppearance_(app)
    return {"view": holder, "method": "cache", "ages": None, "notes": []}


def build_wave(params: dict, app) -> dict:
    from lyssna.legacy.ui.wave import WAVE_H, WAVE_W, WaveView

    flat = params["horizontal"]
    w, h = (WAVE_H, WAVE_W) if flat else (WAVE_W, WAVE_H)
    v = WaveView.alloc().initWithFrame_(((0, 0), (w, h)))
    v.horizontal = flat
    level = params["level"]
    v.level_fn = lambda: level
    holder = host_window(v)
    holder.window().setAppearance_(app)
    v.set_mode(params["mode"])
    v._last = 0.0
    for i in range(1, catalogue.WAVE_TICKS + 1):
        v.tick(i / 30)
    ages = {"breathe": catalogue.WAVE_TICKS / 30} if params["mode"] == "working" else {}
    return {"view": holder, "method": "layer", "ages": ages, "notes": []}


def _no_screen_window_class():
    """The setup window's class, but on no screen: its height is then never cut to fit one."""
    from lyssna.legacy.ui import setup_window

    name = "CompareNoScreenEditingWindow"
    try:
        return objc.lookUpClass(name)
    except objc.nosuchclass_error:
        pass

    class CompareNoScreenEditingWindow(setup_window.EditingWindow):
        def screen(self):
            return None

    return CompareNoScreenEditingWindow


def build_setup(params: dict, app) -> dict:
    from lyssna.dictation import transcribers
    from lyssna.legacy.ui import setup_window
    from lyssna.providers import kokoro

    setup_window.EditingWindow = _no_screen_window_class()
    setup_window.screen_under_mouse = lambda: None
    setup_window.accessibility_trusted = lambda prompt=False: params["trusted"]
    kokoro.KokoroProvider.can_run = classmethod(lambda cls: True)
    local = transcribers.REGISTRY[transcribers.LOCAL]
    local.can_run = classmethod(lambda cls: True)
    kokoro.DOWNLOAD.error = params["error"]
    ctl = fx.FakeSetupController(**params["ctl"])
    w = setup_window.SetupWindow.alloc().init()
    _keep.append(w)
    w.ctl = ctl
    w.taps_ok = lambda: params["taps_ok"]
    globe = params["globe"]
    w.globe_action = lambda: globe
    w._build()
    w.window.setAppearance_(app)
    w._globe = globe
    w._globe_busy = True  # no reading thread: the answer is set above
    engine, dictation = params["engine"], params["dictation"]
    w._prepare(engine, dictation, opening=False)
    if engine is None and dictation is None:
        w._select_tab(setup_window.DICTATE if params["tab"] == "dictate" else setup_window.LISTEN)
    if params["key"]:
        w.key_field.setStringValue_(params["key"])
    if params["saving"]:
        w._saving = True
        w._key_message = ("Checking the key…", "secondary")
        w.spinner.startAnimation_(None)
    elif params["message"] is not None:
        w._key_message = tuple(params["message"])
    if params["dict_saving"] is not None:
        row = w.key_rows[params["dict_saving"]]
        row.field.setStringValue_("sk-preview")
        row.saving = True
        row.message = ("Checking the key…", "secondary")
        row.spinner.startAnimation_(None)
    if params["dict_message"] is not None:
        index, text, tone = params["dict_message"]
        w.key_rows[index].message = (text, tone)
    w._refresh(force=True)
    kokoro.DOWNLOAD.error = ""
    return {"view": w.window.contentView(), "method": "cache", "ages": None, "title": str(w.window.title()),
            "notes": ["content view only: the title bar (traffic lights, title) is the system's on both sides"]}


def build_history(params: dict, app, work: Path) -> dict:
    from lyssna import support
    from lyssna.legacy.ui import history_window as HW

    folder = work / f"history-{len(_keep)}"
    store, vocab, clock = fx.history_data(folder, entries=params.get("entries", True), terms=params.get("terms", True))
    w = HW.HistoryWindow.alloc().init()
    _keep.append(w)
    w.store, w.vocabulary, w.now = store, vocab, clock
    support.SITE_URL = fx.SUPPORT_SITE if params.get("supporter") else ""
    w.prepare()
    support.SITE_URL = ""
    w.window.setAppearance_(app)
    size = params.get("size") or fx.HISTORY_SIZE
    w.window.setContentSize_(size)
    w._layout()
    rows = [i for i in w.items if isinstance(i, HW._DictationRow)]
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
        rows[0].view.set_hover(True)
        rows[1].view.set_hover(True)
        rows[1].copy.set_symbol("checkmark")
        rows[1].more.set_hover(True)
    elif variant == "fix":
        first = rows[0].entry_id
        w._editing = first
        w._edit_text = None
        w._rebuild_list()
        row = w._row(first)
        row.edit_field.setStringValue_(row.entry.text.replace("Mattias", "Matias"))
        w._layout_list()
        row.edit_box.set_focused(True)
    elif variant == "search":
        w.search_field.setStringValue_(params["query"])
        w._query = params["query"]
        w._rebuild_list()
        w.search_box.set_focused(True)
    elif variant == "dictionary":
        w.select_tab("dictionary")
        if params.get("note"):
            w._add_note = params["note"]
            w._layout()
        if params.get("terms", True):
            next(i for i in w.items if isinstance(i, HW._TermRow)).view.set_hover(True)
    return {"view": w.window.contentView(), "method": "cache", "ages": None, "title": str(w.window.title()),
            "notes": ["content view only: the title bar is the system's on both sides"], "owner": w}


def build_whats_new(params: dict, app) -> dict:
    from lyssna import support, whatsnew
    from lyssna.legacy.ui import whats_new_window as WN

    whatsnew.NOTES = {**whatsnew.NOTES, fx.WHATS_NEW_VERSION: fx.WHATS_NEW_NOTES}
    support.SITE_URL = fx.SUPPORT_SITE if params["support"] else ""
    w = WN.WhatsNewWindow.alloc().init()
    _keep.append(w)
    w.version = fx.WHATS_NEW_VERSION
    w.prepare()
    support.SITE_URL = ""
    w.window.setAppearance_(app)
    return {"view": w.window.contentView(), "method": "cache", "ages": None, "title": str(w.window.title()),
            "notes": ["content view only: the title bar is the system's on both sides"]}


def build(pair, scheme: str, work: Path) -> dict:
    app = appearance(scheme)
    AppKit.NSApp.setAppearance_(app)
    kind = pair.kind
    if kind == "player":
        return build_player(pair.params, app)
    if kind == "toast":
        return build_toast(pair.params, app)
    if kind == "tip":
        return build_tip(pair.params, app)
    if kind == "selection":
        return build_selection(pair.params, app)
    if kind == "mark":
        return build_mark(pair.params, app)
    if kind == "wave":
        return build_wave(pair.params, app)
    if kind == "setup":
        return build_setup(pair.params, app)
    if kind == "history":
        return build_history(pair.params, app, work)
    if kind == "whats_new":
        return build_whats_new(pair.params, app)
    raise ValueError(f"unknown kind {kind}")


# -- menus --------------------------------------------------------------------------------------


def dump_menu(nsmenu) -> list[dict]:
    rows = []
    for item in nsmenu.itemArray():
        if item.isSeparatorItem():
            rows.append({"separator": True, "hidden": bool(item.isHidden())})
            continue
        mods = int(item.keyEquivalentModifierMask())
        key = str(item.keyEquivalent() or "")
        glyphs = ""
        if key:
            for mask, glyph in ((AppKit.NSEventModifierFlagControl, "⌃"), (AppKit.NSEventModifierFlagOption, "⌥"),
                                (AppKit.NSEventModifierFlagShift, "⇧"), (AppKit.NSEventModifierFlagCommand, "⌘")):
                if mods & mask:
                    glyphs += glyph
            glyphs += key.upper()
        header = bool(item.isSectionHeader()) if item.respondsToSelector_("isSectionHeader") else False
        rows.append({"title": str(item.title()), "hidden": bool(item.isHidden()),
                     "enabled": bool(item.isEnabled()) and (item.action() is not None or item.hasSubmenu()),
                     "checked": int(item.state()) == 1, "shortcut": glyphs, "header": header,
                     "submenu": bool(item.hasSubmenu()), "image": bool(item.image() is not None),
                     "tooltip": str(item.toolTip() or "")})
    return rows


def menus(out: Path, only: str | None) -> None:
    """The menu bar menu and its submenus from the real MenuBar on a real Controller (temp
    config), the player's speed and voice popups, and the history row's More menu."""
    from lyssna import config
    from lyssna.controller import Controller
    from lyssna.legacy import app as legacy_app
    from lyssna.legacy.ui import history_window as HW
    from lyssna.legacy.ui import mini_player as mpm
    from lyssna.player import Status
    from lyssna.providers import kokoro

    names = catalogue.select_names(catalogue.MENUS, only)
    if not names:
        return
    kokoro.KokoroProvider.can_run = classmethod(lambda cls: True)
    kokoro.KokoroProvider.prepare = lambda self, on_error=None: None
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    result: dict = {}
    ctl = Controller()
    bar = legacy_app.MenuBar(ctl)
    bar._tick(None)
    subs = {"menu.speed": bar.speed_menu, "menu.voice": bar.voice_menu, "menu.provider": bar.provider_menu,
            "menu.transcription": bar.transcription_menu, "menu.cleanup": bar.cleanup_menu,
            "menu.settings": bar.settings_menu, "menu.keys": bar.key_menu}
    if "menu.idle" in names:
        result["menu.idle"] = dump_menu(bar._menu._menu)
    for name, item in subs.items():
        if name in names:
            result[name] = dump_menu(item._menu)
    if "menu.reading" in names:
        Controller.status = property(lambda self: Status(state="playing", index=2, total=5,
                                                         text="Select any text, press the shortcut, and I will read it to you."))
        bar._tick(None)
        result["menu.reading"] = dump_menu(bar._menu._menu)

    popped: list = []

    class CompareMenu(AppKit.NSMenu):
        def popUpMenuPositioningItem_atLocation_inView_(self, item, location, view):
            popped.append(self)
            return False

    mpm.NSMenu = CompareMenu
    p = _player_owner()
    p._update(fx.reading())
    for name, action, sender in (("popup.speed", p.speedMenu_, p.speed_btn), ("popup.voice", p.voiceMenu_, p.badge_btn)):
        if name in names:
            popped.clear()
            action(sender)
            result[name] = dump_menu(popped[0]) if popped else []
    if "popup.history-more" in names:
        store, vocab, clock = fx.history_data(out / "menus-history")
        w = HW.HistoryWindow.alloc().init()
        _keep.append(w)
        w.store, w.vocabulary, w.now = store, vocab, clock
        w.prepare()
        entry = next(i for i in w.items if isinstance(i, HW._DictationRow)).entry
        result["popup.history-more"] = dump_menu(w._more_menu(entry))
    (out / "menus.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")


# -- main ---------------------------------------------------------------------------------------


def run_pairs(out: Path, work: Path, only: str | None, schemes: tuple[str, ...]) -> int:
    count = 0
    for pair in catalogue.select(only):
        for scheme in schemes:
            stem = f"{pair.name}-{scheme}"
            meta: dict = {"name": pair.name, "scheme": scheme, "side": "legacy", "moment": pair.moment}
            try:
                subject = build(pair, scheme, work)
                app = appearance(scheme)
                view = subject["view"]
                w, h, method = render(view, app, out / f"{stem}.png", subject["method"], subject.get("ages"))
                meta.update(size=[w, h], scale=SCALE, method=method, notes=subject.get("notes", []),
                            title=subject.get("title", ""), elements=walk(view, app))
                window = view.window()
                if subject.get("title") and window is not None:  # a titled window paints its background outside the content view
                    meta["background"] = hexcolor(window.backgroundColor(), app)
                count += 1
            except Exception as exc:
                meta["error"] = f"{type(exc).__name__}: {exc}"
                meta["traceback"] = traceback.format_exc()
                print(f"legacy {stem}: {meta['error']}", file=sys.stderr)
            (out / f"{stem}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.compare.legacy_side")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True, help="a temp folder for the isolated config and stores")
    ap.add_argument("--only")
    ap.add_argument("--schemes", default="light,dark")
    ap.add_argument("--menus", action="store_true")
    ap.add_argument("--motion", metavar="DIR")
    args = ap.parse_args(argv)
    sys.modules.setdefault("scripts.compare.legacy_side", sys.modules[__name__])  # motion.py reaches this booted copy
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LYSSNA_COMPARE", "1")
    boot(work)
    n = run_pairs(out, work, args.only, tuple(args.schemes.split(",")))
    print(f"legacy: rendered {n} pictures")
    if args.menus:
        try:
            menus(out, args.only)
        except Exception:
            traceback.print_exc()
    if args.motion:
        from scripts.compare import motion

        motion.legacy_filmstrips(Path(args.motion), args.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
