"""The ten guided steps: what each screen says, what it waits for, and what goes in the report.

Each step is a generator run by lyssna probe's Flow (flow.py): it sets the screen's text, then
yields a request and gets the answer back. The wording is for someone who is not technical:
plain literal sentences, one instruction at a time, no jargon."""

from __future__ import annotations

import threading
import time

from . import analysis
from .backend import SENTENCE
from .flow import Ask, Countdown, Later, Press, Skipped, Step, WaitFor, Work
from .report import snippet
from .ui import FloatingButton, MicRecording, checkbox, line_edit, qt_details, qt_screens

APPS = [
    # key, how the instruction names it, how the questions name it
    ("browser", "your web browser", "your web browser"),
    ("notepad", "Notepad", "Notepad"),
    ("office", "Word or Outlook", "Word or Outlook"),
    ("terminal", "the terminal (Windows Terminal or Command Prompt)", "the terminal"),
]

CHECK_LABELS = {
    "credentials": "Saving passwords in Windows",
    "policy": "Company settings",
    "run_key": "Starting with Windows",
    "pipe": "Lyssna talking to itself",
    "ui_automation": "Reading text in other apps",
    "clipboard": "The clipboard",
}

MIC_BLOCKED = ("Windows blocked the microphone. Open Settings > Privacy & security > Microphone and turn on "
               "'Let desktop apps access your microphone'.")


def _clean(d: dict) -> dict:
    """A result without the private fields (names starting with _), for the report."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ----- shared helpers ----------------------------------------------------------------------


def show_button(ctx, bounds, cursor) -> dict:
    """Show the floating button above the selection (or the cursor) for four seconds. Returns
    where it went, in physical pixels and Qt units, for the report."""
    bubble = ctx.state.get("bubble")
    if bubble is None:
        bubble = ctx.state["bubble"] = FloatingButton(ctx.backend)
    screens = qt_screens()
    rect = tuple(bounds) if bounds else None
    pos = analysis.button_position(rect, tuple(cursor) if cursor else None, screens)
    out = {"selection_rect": list(rect) if rect else None, "cursor": list(cursor) if cursor else None, **pos}
    if pos["qt"] is not None:
        out.update(bubble.show_at(*pos["qt"]))
        if ctx.flow.shots is not None:
            bubble.grab().save(str(ctx.flow.shots / "floating-button.png"))
    out["window_style"] = bubble.style_info
    return out


def check_focus_later(ctx, prefix: str) -> None:
    """Half a second after the button shows, note which window is in front (it must not be ours)."""
    from PySide6.QtCore import QTimer

    rec = ctx.rec

    def look():
        try:
            rec.set(f"{prefix}foreground_after_button", ctx.backend.foreground())
        except Exception as e:
            rec.error(f"{prefix}foreground check", e)

    QTimer.singleShot(500, look)


# ----- 1. about this PC --------------------------------------------------------------------


def step_about(ctx):
    ui, rec = ctx.ui, ctx.rec
    ui.set_body("This program checks how Lyssna will work on this PC. It takes about five to eight minutes and "
                "changes no settings. At the end it saves a report file for you to send back.")
    yield Press("Start", skip=False)
    ui.set_body("")
    rec.update(qt_details(), prefix="qt.")
    info = yield Work(ctx.backend.about_system, busy="Reading the details of this PC.")
    rec.update(info)
    windows = info.get("windows") or {}
    keyboard = info.get("keyboard") or {}
    ctx.state["altgr"] = bool(keyboard.get("has_altgr"))
    screens = rec.get("qt.screens") or []
    lines = [
        f"Windows: {windows.get('name', 'unknown')}, build {windows.get('build', 'unknown')}.",
        f"Screens: {len(screens)}.",
        f"Keyboard layout: {keyboard.get('language') or keyboard.get('layout_id') or 'unknown'}.",
    ]
    ui.set_body("Done. Lyssna found these details.\n\n" + "\n".join(lines))
    yield Press("Next", skip=False)


# ----- 2. system pieces --------------------------------------------------------------------


def step_system(ctx):
    ui, rec = ctx.ui, ctx.rec
    ui.set_body("Lyssna now tries the parts of Windows it needs, such as saving passwords, the clipboard and "
                "reading text in other apps. It changes nothing and cleans up after itself.")
    results = yield Work(ctx.backend.system_checks, busy="Checking. This takes a few seconds.")
    rec.update(results)
    lines = []
    for key, label in CHECK_LABELS.items():
        r = results.get(key) or {}
        if key == "policy":
            state = "none set" if r.get("ok") else "set by your company, or could not be read"
        else:
            state = "works" if r.get("ok") else "did not work"
        lines.append(f"{label}: {state}")
    ui.set_body("Done.\n\n" + "\n".join(lines))
    yield Press("Next", skip=False)


# ----- 3. the reading hotkey ---------------------------------------------------------------


def step_hotkey(ctx):
    ui, rec, flow = ctx.ui, ctx.rec, ctx.flow
    ok = ctx.backend.install_keyboard(flow.on_hotkey)
    rec.set("keyboard_hook_installed", ok)
    ctx.state["hotkey"] = ok
    if not ok:
        ui.set_body("Lyssna could not watch the keyboard on this PC. The steps that need Ctrl+Alt+S will be skipped.")
        yield Press("Next", skip=False)
        return
    ui.set_body("Click on any other window, then press Ctrl+Alt+S.")
    flow.hotkey_work = None
    got = yield WaitFor("hotkey", 30, waiting="Waiting for Ctrl+Alt+S.")
    rec.set("hotkey_seen", got is not None)
    if got is not None:
        rec.update(_clean(got), prefix="hotkey.")
        ui.set_body("Lyssna saw Ctrl+Alt+S.")
    if not ctx.state.get("altgr"):
        rec.set("altgr_test", "not shown: this keyboard layout has no AltGr key")
        if got is not None:
            yield Press("Next", skip=False)
        return
    ui.set_body("Open Notepad and type the @ sign. You can also type it in the box below.")
    box = line_edit("Type the @ sign here")
    ui.show_extra([box])
    before = flow.hotkey_count
    log_before = len(flow.hotkey_log)
    answer = yield Ask("Did the @ sign appear?")
    fired = flow.hotkey_log[log_before:]
    rec.set("altgr_test.hotkey_fired_by_mistake", flow.hotkey_count - before > 0)
    rec.set("altgr_test.fires_with_right_alt_down", sum(1 for f in fired if f.get("right_alt_down")))
    rec.set("altgr_test.box_has_at_sign", "@" in box.text())
    rec.set("altgr_test.answer", answer)


# ----- 4. reading the selection ------------------------------------------------------------


def step_selection(ctx):
    ui, rec, flow, be = ctx.ui, ctx.rec, ctx.flow, ctx.backend
    if not ctx.state.get("hotkey", True):
        rec.set("reason", "the keyboard hook could not be installed")
        raise Skipped()
    try:
        office = be.office_installed()
    except Exception as e:
        rec.error("office lookup", e)
        office = {}
    rec.set("office_installed", office)
    has_office = any(office.values()) if office else True
    boxes = []
    for key, name, _short in APPS:
        label = name[0].upper() + name[1:]
        if key == "office" and not has_office:
            label += " (not found on this PC)"
        boxes.append((key, checkbox(label, key != "office" or has_office)))
    ui.set_body("Next you select a sentence in a few apps and press Ctrl+Alt+S in each. The report includes the "
                "first few words of what you select, so pick something harmless, like a news headline.\n\n"
                "Keep this window to one side, so you can see it next to the other app. "
                "Untick any app you do not have.")
    ui.show_extra([b for _k, b in boxes])
    yield Press("Start")
    chosen = [k for k, b in boxes if b.isChecked()]
    rec.set("apps_chosen", chosen)
    ui.hide_extra()
    for key, name, short in APPS:
        if key not in chosen:
            continue
        prefix = f"{key}."
        try:
            ui.set_body(f"Open {name}, select a sentence with the mouse, then press Ctrl+Alt+S.")
            flow.hotkey_work = be.read_selection
            try:
                got = yield WaitFor("hotkey", 90, waiting="Waiting for Ctrl+Alt+S.", skip_label="Skip this app")
            finally:
                flow.hotkey_work = None
            if got is None:
                rec.set(prefix + "result", "skipped, or no Ctrl+Alt+S within 90 seconds")
                continue
            text = got.pop("_snippet", "")
            rec.update(_clean(got), prefix)
            rec.set(prefix + "first_60_characters", text)
            ax = got.get("ax") or {}
            rec.update(show_button(ctx, ax.get("bounds"), got.get("cursor")), prefix + "button.")
            check_focus_later(ctx, prefix)
            if text:
                ui.set_body(f'Lyssna read this text.\n\n"{text}"')
            else:
                ui.set_body("Lyssna did not find any selected text.")
            yield Ask(f"Is your text still highlighted in {short}?", key=f"{key}: Is your text still highlighted?")
            yield Ask("Did a round button appear just above your selection?", key=f"{key}: Did a round button appear?")
            if text:
                yield Ask("Is this the text you selected?", key=f"{key}: Is this the text you selected?")
        except Exception as e:
            rec.error(f"selection in {key}", e)


# ----- 5. selecting with the mouse ---------------------------------------------------------


class GestureWatch:
    """Feeds the selection detector's callbacks. They run on the mouse hook's worker thread,
    so the UI Automation reads before and after the gesture happen there."""

    def __init__(self, ctx, probe_hwnd: int):
        self.ctx = ctx
        self.probe_hwnd = probe_hwnd
        self.before: dict | None = None
        self.down = None
        self.clicks = 0
        self.lock = threading.Lock()

    def ignore(self, p) -> bool:
        try:
            r = self.ctx.backend.window_rect(self.probe_hwnd) if self.probe_hwnd else None
        except Exception:
            return False
        return bool(r) and r[0] <= p.x < r[0] + r[2] and r[1] <= p.y < r[1] + r[3]

    def on_press(self, p, click_state: int) -> None:
        with self.lock:
            self.down, self.clicks = [p.x, p.y], click_state
        if click_state == 1:
            try:
                self.before = self.ctx.backend.ax_selection()
            except Exception as e:
                self.before = {"error": f"{type(e).__name__}: {e}"}

    def on_select(self, p) -> None:
        start = time.perf_counter()
        try:
            after = self.ctx.backend.ax_selection()
        except Exception as e:
            after = {"error": f"{type(e).__name__}: {e}"}
        before = dict(self.before or {})
        before_text, after_text = before.pop("_text", None), after.pop("_text", None)
        payload = {"gesture_seen": True, "click_state": self.clicks, "down": self.down, "up": [p.x, p.y],
                   "ax_before": before, "ax_after": after, "selection_changed": before_text != after_text,
                   "after_read_seconds": round(time.perf_counter() - start, 3)}
        self.ctx.flow.post_trigger("gesture", payload)


def step_mouse(ctx):
    from lyssna.gestures import SelectionGestureDetector

    ui, rec = ctx.ui, ctx.rec
    watch = GestureWatch(ctx, int(ui.winId()))
    detector = SelectionGestureDetector(on_select=watch.on_select, on_dismiss=lambda p: None,
                                        ignore_region=watch.ignore, on_press=watch.on_press)
    ok = ctx.backend.install_mouse(detector)
    rec.set("mouse_hook_installed", ok)
    if not ok:
        ui.set_body("Lyssna could not watch the mouse on this PC.")
        yield Press("Next", skip=False)
        return
    for key, instruction in (
        ("drag", "Select a sentence in the web browser by dragging the mouse. Do not press any keys."),
        ("double_click", "Double-click a word in the web browser."),
    ):
        prefix = f"{key}."
        try:
            ui.set_body(instruction)
            got = yield WaitFor("gesture", 60, waiting="Waiting for you to select.")
            if got is None:
                rec.set(prefix + "gesture_seen", False)
                continue
            rec.update(got, prefix)
            bounds = (got.get("ax_after") or {}).get("bounds")
            rec.update(show_button(ctx, bounds, got.get("up")), prefix + "button.")
            check_focus_later(ctx, prefix)
            ui.set_body("Lyssna saw that.")
            yield Ask("Did the round button appear?", key=f"{key}: Did the round button appear?")
        except Exception as e:
            rec.error(f"gesture {key}", e)
    rec.set("mouse_hook_removed", ctx.backend.stop_mouse())


# ----- 6. the voice ------------------------------------------------------------------------


def _play_twice(ctx, audio, prefix: str):
    for speed, busy in ((1.0, "Playing at normal speed."), (1.5, "Playing faster.")):
        result = yield Work(lambda s=speed: ctx.backend.play(audio, s), busy=busy)
        ctx.rec.update(result, f"{prefix}play_{speed:g}x.")


def step_voice(ctx):
    ui, rec, be = ctx.ui, ctx.rec, ctx.backend
    rec.set("sentence", SENTENCE)
    ui.set_body("Lyssna now reads one sentence twice with the voice built into Windows, first at normal speed "
                "and then faster. Turn the sound on.")
    yield Press("Play")
    try:
        audio, info = yield Work(be.sapi_synthesize, busy="Preparing the voice.")
        rec.update(info, "windows_voice.")
        yield from _play_twice(ctx, audio, "windows_voice.")
        ui.set_body("")
        yield Ask("Did you hear the sentence twice?", key="Windows voice: Did you hear the sentence twice?")
        yield Ask("Did the faster one sound like the same voice, only quicker?",
                  key="Windows voice: Did the faster one sound like the same voice?")
    except Exception as e:
        rec.error("windows voice", e)
    ui.set_body("Lyssna also has its own voice, which sounds more natural. Testing it needs a one-time download "
                "of 140 MB from the internet.")
    choice = yield Ask("Download the Lyssna voice (140 MB) and test it?", choices=("Yes", "Skip"),
                       key="Download the Lyssna voice?")
    if choice == "Yes":
        try:
            ui.set_body("This can take a few minutes, depending on your internet connection.")
            result = yield Work(lambda say: be.kokoro_download(
                lambda f: say(f"Downloading the Lyssna voice. {round(f * 100)}% done.")),
                busy="Downloading the Lyssna voice.", with_status=True)
            rec.update(result, "lyssna_voice.")
            if not result.get("files_present"):
                ui.set_body("The download did not finish. The report says why.")
                yield Press("Next", skip=False)
            else:
                audio, info = yield Work(be.kokoro_benchmark,
                                         busy="Preparing the Lyssna voice. This can take a minute.")
                rec.update(info, "lyssna_voice.")
                yield from _play_twice(ctx, audio, "lyssna_voice.")
                ui.set_body("")
                yield Ask("Did you hear the sentence twice?", key="Lyssna voice: Did you hear the sentence twice?")
                yield Ask("Did the faster one sound like the same voice, only quicker?",
                          key="Lyssna voice: Did the faster one sound like the same voice?")
        except Exception as e:
            rec.error("lyssna voice", e)
    try:
        rec.update((yield Work(be.temp_leftovers, busy="Cleaning up.")))
    except Exception as e:
        rec.error("temp files", e)


# ----- 7. the dictation keys ---------------------------------------------------------------


class KeyWatch:
    """The keyboard hook's raw listener for one part of the dictation key test. Runs inside
    the hook callback, so it only notes the event and decides; anything slow is spawned."""

    def __init__(self, ctx, mode: str):
        self.ctx = ctx
        self.mode = mode  # "swallow_win", "inject", "swallow_space", "watch"
        self.events: list[tuple[int, bool, float]] = []
        self.swallowed: set[int] = set()
        self.held: set[int] = set()
        self._win_swallowed: set[int] = set()
        self._space_swallowed = False
        self.injected_at: float | None = None
        self.done = False

    def __call__(self, vk: int, down: bool, t: float) -> bool:
        self.events.append((vk, down, t))
        if down:
            self.held.add(vk)
        else:
            self.held.discard(vk)
        both = bool(self.held & analysis.CTRL_KEYS) and bool(self.held & analysis.WIN_KEYS)
        swallow = False
        if self.mode == "swallow_win" and vk in analysis.WIN_KEYS:
            # Swallow Windows key presses in matched pairs, so Windows never thinks it is stuck down.
            if down:
                self._win_swallowed.add(vk)
                swallow = True
            elif vk in self._win_swallowed:
                self._win_swallowed.discard(vk)
                swallow = True
        elif self.mode == "inject" and both and self.injected_at is None:
            self.injected_at = t
            threading.Thread(target=self.ctx.backend.inject_unassigned_key, daemon=True).start()
        elif self.mode == "swallow_space" and vk == 0x20:
            if down and both:
                self._space_swallowed = True
                swallow = True
            elif not down and self._space_swallowed:
                self._space_swallowed = False
                swallow = True
        if swallow:
            self.swallowed.add(len(self.events) - 1)
        if not self.done:
            finished = (len(analysis.chord_taps(self.events)) >= 2 if self.mode == "watch"
                        else analysis.chord_released(self.events))
            if finished:
                self.done = True
                self.ctx.flow.post_trigger("keys", self.mode)
        return swallow

    def summary(self) -> dict:
        out = {"mode": self.mode, "events": analysis.describe_events(self.events, self.swallowed),
               "events_seen": len(self.events), "events_swallowed": len(self.swallowed)}
        taps = analysis.chord_taps(self.events)
        if taps:
            out["first_hold_seconds"] = round(taps[0][1] - taps[0][0], 3)
        if self.injected_at is not None and self.events:
            out["injected_0xE8_at"] = round(self.injected_at - self.events[0][2], 3)
        return out


KEY_PARTS = [
    ("a_hold_windows_key_swallowed", "swallow_win", "Hold Ctrl and the Windows key together for two seconds, then let go.",
     "Did the Start menu open?"),
    ("b_hold_with_dummy_key", "inject", "Once more. Hold Ctrl and the Windows key together for two seconds, then let go.",
     "Did the Start menu open?"),
    ("c_space", "swallow_space", "Hold Ctrl and the Windows key, tap Space, then let go of all three.",
     "Did anything on screen change, such as the keyboard language?"),
    ("d_double_tap", "watch", "Tap Ctrl and the Windows key together twice, quickly.", None),
]


def step_keys(ctx):
    ui, rec, be = ctx.ui, ctx.rec, ctx.backend
    if not ctx.state.get("hotkey", True):
        rec.set("reason", "the keyboard hook could not be installed")
        raise Skipped()
    ui.set_body("Lyssna will start dictation when you hold Ctrl and the Windows key. These four short tests check "
                "that Windows lets Lyssna use those keys.")
    yield Press("Start")
    for key, mode, instruction, question in KEY_PARTS:
        watch = KeyWatch(ctx, mode)
        try:
            ui.set_body(instruction)
            be.set_raw_listener(watch)
            got = yield WaitFor("keys", 30, waiting="Waiting for the keys.")
            be.set_raw_listener(None)
            rec.set(f"{key}.seen", got is not None)
            rec.update(watch.summary(), f"{key}.")
            if mode == "watch":
                rec.update(analysis.double_tap(watch.events), f"{key}.")
            if got is None:
                continue
            if question:
                ui.set_body("")
                yield Ask(question, key=f"{key}: {question}")
        except Exception as e:
            rec.error(f"keys {key}", e)
        finally:
            be.set_raw_listener(None)
    ui.set_body("Done.")
    yield Press("Next", skip=False)


# ----- 8. the microphone -------------------------------------------------------------------


def _record(ctx, seconds: float):
    def start(done):
        if ctx.backend.preview:
            attempt = ctx.state["mic_attempt"]
            from PySide6.QtCore import QTimer

            fake = {"opened": attempt > 1, "note": "PREVIEW FAKE", "peak": 0.41, "rms": 0.07, "samples": 48000,
                    "recorded_seconds": 3.0, "format": {"rate": 16000, "channels": 1, "sample": "Int16"}}
            ctx.ui.set_big("Speak now")
            QTimer.singleShot(300 if ctx.flow.auto else 3000, lambda: done(fake))
            return
        rec_obj = MicRecording(seconds, done, tick=lambda n: ctx.ui.set_big(f"Speak now. {n}"))
        ctx.state["mic"] = rec_obj  # keep it alive while it records
        try:
            rec_obj.start()
        except Exception as e:
            done(e)

    return Later(start)


def step_mic(ctx):
    ui, rec, be = ctx.ui, ctx.rec, ctx.backend
    ui.set_body("Lyssna records three seconds from your microphone. The recording is not saved or sent anywhere. "
                "The report only says how loud it was.")
    yield Press("Start")
    attempt = 0
    while True:
        attempt += 1
        ctx.state["mic_attempt"] = attempt
        ui.set_body("Get ready to say a sentence out loud.")
        yield Countdown(3, "{n}")
        ui.set_body("Say a sentence out loud.")
        result = yield _record(ctx, 3.0)
        ui.set_big("")
        rec.update(result, f"attempt_{attempt}.")
        if result.get("opened") and not result.get("all_silent"):
            ui.set_body(f"Recorded {result.get('recorded_seconds', 0)} seconds. "
                        f"The loudest moment reached {round(float(result.get('peak', 0)) * 100)}% of full volume.")
            yield Press("Next", skip=False)
            return
        ui.set_body(MIC_BLOCKED)
        choice = yield Ask("", choices=("Retry", "Skip"), extra=(("Open the settings", be.open_mic_settings),), record=False)
        if choice == "Skip":
            rec.set("result", "skipped after the microphone did not record")
            raise Skipped()


# ----- 9. typing text ----------------------------------------------------------------------


def step_paste(ctx):
    ui, rec = ctx.ui, ctx.rec
    ui.set_body("Open Notepad and click in the page. Come back and press Start. "
                "You have five seconds to click back into Notepad.")
    yield Press("Start")
    ui.set_body("Click into Notepad now.")
    yield Countdown(5, "{n}")
    result = yield Work(ctx.backend.paste_test, busy="Typing.")
    rec.update(result)
    ui.set_body("")
    yield Ask("Did the sentence 'Lyssna typed this sentence.' appear in Notepad?",
              key="Did the sentence appear in Notepad?")
    yield Ask("Press the Windows key and V. Is that sentence in the list?",
              key="Is the sentence in the clipboard history (expected: No)?")


# ----- 10. done ----------------------------------------------------------------------------


def step_done(ctx):
    from PySide6.QtGui import QGuiApplication

    ui, rec, flow = ctx.ui, ctx.rec, ctx.flow
    try:
        rec.update(ctx.backend.remove_hooks())
    except Exception as e:
        rec.error("remove hooks", e)
    rec.status("done")
    path, _text = flow.write_report()
    where = str(path) if path else "The report could not be saved. Use Copy the report instead."
    ui.set_body(f"Thank you. The report is saved here.\n\n{where}\n\n"
                "Please send the file lyssna-probe-report.txt back to Claude.")

    def open_folder():
        if flow.report_path is not None:
            ctx.backend.open_folder(flow.report_path)

    def copy():
        QGuiApplication.clipboard().setText(flow.write_report()[1])
        ui.set_status("The report is copied. You can paste it into a message.")

    yield Ask("", choices=("Close",), extra=(("Open the folder", open_folder), ("Copy the report", copy)), record=False)
    flow.finish()
    ui.close()


STEPS = [
    Step("about", "About this PC", step_about, 1),
    Step("system", "Windows features Lyssna uses", step_system, 2),
    Step("hotkey", "The reading shortcut", step_hotkey, 3),
    Step("selection", "Reading the selection", step_selection, 4),
    Step("mouse", "Selecting with the mouse", step_mouse, 5),
    Step("voice", "The voice", step_voice, 6),
    Step("keys", "The dictation keys", step_keys, 7),
    Step("microphone", "The microphone", step_mic, 8),
    Step("typing", "Typing text", step_paste, 9),
    Step("done", "Done", step_done, None),
]


def preview_fakes() -> dict:
    """What "pretend it happened" delivers in the preview, per event."""
    return {
        "hotkey": lambda: {"at": "12:00:00", "right_alt_down": False, "note": "PREVIEW FAKE",
                           "ax": {"supported": True, "text_length": 42, "bounds": [700, 420, 400, 22]},
                           "cursor": [900, 500], "chain": {"source": "selection (accessibility)", "text_length": 42},
                           "_snippet": snippet("This is made-up text for the preview on a Mac")},
        "gesture": lambda: {"gesture_seen": True, "click_state": 1, "down": [700, 430], "up": [1100, 432],
                            "ax_after": {"supported": True, "bounds": [700, 420, 400, 22]}, "note": "PREVIEW FAKE"},
        "keys": lambda: "PREVIEW FAKE",
    }
