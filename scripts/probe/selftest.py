"""probe_win.py --self-test: everything the probe can check without a person, for GitHub's
Windows machine on every push.

Why: the guided probe needs the owner at his PC, which happens rarely. This runs the same real
Windows calls unattended, prints every result as it goes (so a failure is plain in the
workflow log), prints the full report at the end, and exits 1 only when a must-pass check
fails. Checks that depend on the machine (a sound card, a focused Notepad, the network) are
reported and never fail the run."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

from . import report as rpt
from .backend import VK_A, VK_C, RealBackend, timed

IMPORTS = [
    "lyssna.host", "lyssna.win.events", "lyssna.win.hotkeys", "lyssna.win.capture", "lyssna.win.clipboard",
    "lyssna.win.audio", "lyssna.win.credentials", "lyssna.win.policy", "lyssna.win.autostart", "lyssna.win.ipc",
    "lyssna.providers", "lyssna.providers.windows", "lyssna.providers.kokoro", "PySide6.QtMultimedia",
    "comtypes", "comtypes.client",
]
NOTEPAD_TEXT = "Lyssna self-test sentence in Notepad."


def say(line: str) -> None:
    """One line to the log, right away. Silent when there is no console (the windowed exe)."""
    out = sys.stdout
    if out is None:
        return
    try:
        out.write(line + "\n")
        out.flush()
    except Exception:
        pass


class SelfTest:
    def __init__(self, report: rpt.Report):
        self.report = report
        self.backend = RealBackend()
        self.failed: list[str] = []
        self.must: dict[str, bool] = {}

    def check(self, step_key: str, title: str, name: str, fn, must: bool) -> dict:
        say(f"... {name}")
        result = timed(fn)
        ok = bool(result.get("ok"))
        rec = self.report.step(step_key, title)
        rec.update({name: result})
        rec.status(rpt.STATUS_DONE)
        if must:
            self.must[name] = ok
            if not ok:
                self.failed.append(name)
        tag = "PASS" if ok else ("FAIL" if must else "WARN")
        detail = f"  {result.get('error')}" if result.get("error") else ""
        if not ok and not detail:
            detail = "  " + ", ".join(f"{k}={v}" for k, v in result.items() if k not in ("ok", "seconds"))[:1500]
        say(f"[{tag}] {name} ({result.get('seconds', 0):.2f} s){detail}")
        if not ok and result.get("traceback"):
            say(result["traceback"])
        return result

    # ----- the checks ---------------------------------------------------------------------

    def imports(self) -> dict:
        out: dict = {}
        for name in IMPORTS:
            try:
                importlib.import_module(name)
                out[name] = "ok"
            except Exception as e:
                out[name] = f"{type(e).__name__}: {e}"
        from lyssna import providers

        out["sapi_registered"] = "sapi" in providers.REGISTRY
        out["ok"] = all(v == "ok" for k, v in out.items() if k != "sapi_registered") and out["sapi_registered"]
        return out

    def about(self) -> dict:
        from .ui import qt_details

        return {"ok": True, "qt": qt_details(), **self.backend.about_system()}

    def hooks(self) -> dict:
        from lyssna.gestures import SelectionGestureDetector
        from lyssna.win.events import KeyboardTap, MouseTap

        kb = KeyboardTap()
        mouse = MouseTap(SelectionGestureDetector(lambda p: None, lambda p: None))
        out = {"keyboard_installed": kb.start(), "mouse_installed": mouse.start()}
        kb_thread, mouse_thread = kb._thread, mouse._thread
        kb.stop()
        mouse.stop()
        out["keyboard_removed"] = kb_thread is None or not kb_thread.is_alive()
        out["mouse_removed"] = mouse_thread is None or not mouse_thread.is_alive()
        out["ok"] = all(out.values())
        return out

    def notepad(self) -> dict:
        """Open Notepad, put a sentence in it, select it all, and see what the selection
        reading returns. Then send an injected Ctrl+C there and check the keyboard hook let it
        through without firing a hotkey. Report only: this machine may have no real desktop."""
        from lyssna.win import capture, hotkeys
        from lyssna.win.events import KeyboardTap

        from . import winapi

        out: dict = {}
        before = set(winapi.find_windows({"Notepad"}))
        proc = subprocess.Popen(["notepad.exe"])
        hwnd = winapi.wait_for_window({"Notepad"}, before, 15.0)
        out["window_found"] = bool(hwnd)
        pid = winapi.window_pid(hwnd) if hwnd else 0
        try:
            if hwnd:
                edit = winapi.notepad_edit(hwnd)
                out["edit_class"] = winapi.window_class(edit) if edit else ""
                out["text_set"] = winapi.set_window_text(edit, NOTEPAD_TEXT) if edit else False
                out["set_foreground"] = winapi.set_foreground(hwnd)
                time.sleep(0.5)
                out["foreground"] = winapi.foreground()
                capture.send_key(VK_A, hotkeys.CTRL)
                time.sleep(0.4)
                ax = self.backend.ax_selection()
                text = ax.pop("_text")
                out["ax"] = ax
                out["ax_text_matches"] = text.strip() == NOTEPAD_TEXT
                start = time.perf_counter()
                full, source = capture.selection_or_clipboard()
                out["chain"] = {"source": source, "text_length": len(full), "matches": full.strip() == NOTEPAD_TEXT,
                                "seconds": round(time.perf_counter() - start, 3)}
            # The injected Ctrl+C: the hook must see it marked as injected and fire nothing.
            fired: list[int] = []
            seen: list[tuple[int, bool, bool]] = []
            tap = KeyboardTap()
            tap.bind("ctrl+c", lambda: fired.append(1))
            original = tap.key_event

            def spy(vk, down, injected, t):
                seen.append((vk, down, injected))
                return original(vk, down, injected, t)

            tap.key_event = spy
            out["ctrl_c.hook_installed"] = tap.start()
            guarded = winapi.ignore_console_ctrl_c(True)  # in case our own console is in front
            try:
                front = winapi.foreground().get("class", "")
                out["ctrl_c.foreground"] = front
                if front in capture.CONSOLE_CLASSES:
                    # A Ctrl+C into a console would stop the programs running in it, this one's parents too.
                    out["ctrl_c.sent"] = False
                else:
                    capture.send_key(VK_C, hotkeys.CTRL)
                    out["ctrl_c.sent"] = True
                time.sleep(0.6)
            finally:
                tap.stop()
                if guarded:
                    winapi.ignore_console_ctrl_c(False)
            out["ctrl_c.events_seen_by_hook"] = len(seen)
            out["ctrl_c.all_marked_injected"] = bool(seen) and all(i for _vk, _d, i in seen)
            out["ctrl_c.hotkey_fired"] = bool(fired)
        finally:
            if pid:
                out["notepad_closed"] = winapi.kill_process(pid) == 0
            try:
                proc.kill()
            except Exception:
                pass
        out["ok"] = bool(hwnd) and not out.get("ctrl_c.hotkey_fired", False)
        return out

    def sapi(self) -> dict:
        audio, info = self.backend.sapi_synthesize()
        self._sapi_audio = audio
        info["ok"] = bool(info.get("valid")) and info.get("frames", 0) > 0 and info.get("seconds", 0) > 0
        return info

    def player(self) -> dict:
        audio = getattr(self, "_sapi_audio", None)
        if audio is None:
            return {"ok": False, "error": "no Windows voice audio to load"}
        out = self.backend.load_player(audio)
        out["ok"] = out.get("duration", 0) > 0
        return out

    def kokoro(self) -> dict:
        last = [-1]

        def progress(fraction: float) -> None:
            step = int(fraction * 10)
            if step != last[0]:
                last[0] = step
                say(f"    download {step * 10}%")

        out = self.backend.kokoro_download(progress)
        if not out.get("files_present"):
            out["ok"] = False
            return out
        _audio, info = self.backend.kokoro_synthesize()
        out.update(info)
        out["ok"] = bool(info.get("valid"))
        return out

    def floating_window(self) -> dict:
        from PySide6.QtWidgets import QApplication

        from . import winapi
        from .ui import FloatingButton

        b = FloatingButton(self.backend)
        out = {"style": b.style_info}
        out.update(b.show_at(120, 120, seconds=1.0))
        QApplication.processEvents()
        time.sleep(0.2)
        QApplication.processEvents()
        try:
            out["foreground_after_show"] = winapi.foreground()
        except Exception as e:
            out["foreground_error"] = str(e)
        out["visible"] = b.isVisible()
        b.hide()
        b.deleteLater()
        QApplication.processEvents()
        out["ok"] = bool(out["visible"]) and bool(b.style_info.get("noactivate_set", sys.platform != "win32"))
        return out

    def temp_files(self) -> dict:
        out = self.backend.temp_leftovers()
        out["ok"] = True
        return out

    # ----- running ------------------------------------------------------------------------

    def run(self) -> None:
        c = self.check
        c("imports", "Module imports", "imports", self.imports, must=True)
        c("about", "About this PC", "about", self.about, must=False)
        b = self.backend
        for name, fn, must in (("credentials", b.check_credentials, True), ("policy", b.check_policy, False),
                               ("run_key", b.check_run_key, False), ("pipe", b.check_pipe, True),
                               ("ui_automation", b.check_uia, False), ("clipboard", b.check_clipboard, True)):
            c("system", "Windows features Lyssna uses", name, fn, must=must)
        c("hooks", "Keyboard and mouse hooks", "hooks", self.hooks, must=True)
        c("notepad", "Notepad selection and injected Ctrl+C", "notepad", self.notepad, must=False)
        c("voice", "The Windows voice", "windows_voice", self.sapi, must=True)
        c("voice", "The Windows voice", "player", self.player, must=False)
        c("kokoro", "The Lyssna voice (Kokoro)", "kokoro", self.kokoro, must=False)
        c("window", "The floating button window", "floating_window", self.floating_window, must=False)
        c("cleanup", "Temp files", "temp_files", self.temp_files, must=False)


def run(app, log_lines, report_file: str | None, platform_note: str) -> int:
    from . import PROBE_VERSION

    say("Lyssna probe self-test")
    report = rpt.Report("self-test", {"probe_version": PROBE_VERSION, "qt_platform": app.platformName(),
                                      "qt_platform_choice": platform_note, "frozen": bool(getattr(sys, "frozen", False)),
                                      "executable": sys.executable, "cwd": os.getcwd()})
    test = SelfTest(report)
    try:
        test.run()
    except Exception as e:  # never leave without a report
        report.step("crash", "Self-test crashed").error("self-test", e)
        test.failed.append("self-test crashed")
    report.meta("must_pass", test.must)
    report.meta("must_pass_failed", test.failed)
    report.meta("result", "PASS" if not test.failed else "FAIL")
    report.meta("finished", time.strftime("%Y-%m-%d %H:%M:%S"))
    report.set_log(log_lines())
    text = rpt.render_text(report.snapshot())
    target = Path(report_file) if report_file else test.backend.report_dir() / "lyssna-probe-selftest.txt"
    try:
        target.write_text(text, encoding="utf-8")
        say(f"Report written to {target}")
    except Exception as e:
        say(f"Could not write the report to {target}: {type(e).__name__}: {e}")
    say("")
    say(text)
    if test.failed:
        say(f"SELF-TEST RESULT: FAIL ({', '.join(test.failed)})")
        return 1
    say("SELF-TEST RESULT: PASS")
    return 0
