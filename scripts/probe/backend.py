r"""What the probe does to the PC, in one object, and a fake twin for the preview on a Mac.

Why two: the guided screens and the self-test call the same methods. RealBackend calls the
real lyssna/win modules and Win32; FakeBackend returns made-up results, every one marked as
fake, so the screens can be reviewed on a Mac without a single Windows call.

Threading: every method except the hook installers may block for a moment and is called from a
worker thread, never the Qt main thread. The methods return plain dicts for the report and
raise when something fails; the caller records the exception and moves on."""

from __future__ import annotations

import glob
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from .analysis import wav_info
from .report import snippet

SENTENCE = "Lyssna reads the text you select, out loud, in a clear and natural voice."
CRED_ACCOUNT = "lyssna-probe"
CLIPBOARD_TEST = "Lyssna probe clipboard test."
PASTE_TEXT = "Lyssna typed this sentence."
VK_V, VK_C, VK_A = 0x56, 0x43, 0x41
VK_UNASSIGNED = 0xE8
VK_RMENU = 0xA5
FAKE = "PREVIEW FAKE"


def timed(fn, *args, **kwargs) -> dict:
    """Run one check: its result dict plus "seconds", or ok False with the error and traceback."""
    from .report import error_entry

    start = time.perf_counter()
    try:
        out = fn(*args, **kwargs)
        out = dict(out) if isinstance(out, dict) else {"value": out}
    except Exception as e:
        err = error_entry(fn.__name__, e)
        out = {"ok": False, "error": f"{err['type']}: {err['message']}", "traceback": err["traceback"]}
    out["seconds"] = round(time.perf_counter() - start, 3)
    return out


def report_dir() -> Path:
    """The Desktop, else the home folder."""
    home = Path.home()
    for candidate in (Path(os.environ.get("USERPROFILE") or home) / "Desktop", home / "OneDrive" / "Desktop", home / "Desktop"):
        if candidate.is_dir():
            return candidate
    return home


class RealBackend:
    preview = False

    def __init__(self):
        self.keyboard = None
        self.mouse = None
        self._kokoro_engine_loaded = False

    # ----- step 1: about this PC ----------------------------------------------------------

    def about_system(self) -> dict:
        from lyssna.providers import kokoro
        from lyssna.win import audio as win_audio

        from . import winapi

        out: dict = {}
        for name, fn in (("windows", winapi.windows_version), ("keyboard", winapi.keyboard_layout)):
            try:
                out[name] = fn()
            except Exception as e:
                out[name] = {"error": f"{type(e).__name__}: {e}"}
        out["dpi_awareness"] = winapi.dpi_awareness()
        out["python"] = sys.version.split()[0]
        out.update(winapi.frozen_info())
        out["pitch_compensation"] = win_audio.pitch_compensation()
        try:
            import comtypes

            out["comtypes_version"] = comtypes.__version__
        except Exception as e:
            out["comtypes_version"] = f"not importable: {type(e).__name__}: {e}"
        try:
            from lyssna.providers import windows as win_voice

            out["sapi_voices"] = win_voice.installed_voices()
        except Exception as e:
            out["sapi_voices"] = f"{type(e).__name__}: {e}"
        try:
            lib, data = kokoro._bundled_espeak()
            out["espeak_library"] = lib
            out["espeak_library_exists"] = os.path.isfile(lib)
            out["espeak_data_exists"] = os.path.isdir(data)
        except Exception as e:
            out["espeak"] = f"{type(e).__name__}: {e}"
        try:
            from .speed import machine_details

            out["machine"] = machine_details()
        except Exception as e:
            out["machine"] = {"error": f"{type(e).__name__}: {e}"}
        out["kokoro_packages"] = kokoro.packages_installed()
        out["kokoro_files_present"] = kokoro.files_present()
        out["kokoro_folder"] = str(kokoro.DATA_DIR)
        return out

    # ----- step 2: system pieces ----------------------------------------------------------

    def check_credentials(self) -> dict:
        from lyssna import keystore
        from lyssna.win.credentials import CredentialBackend

        b = CredentialBackend()
        value = f"probe-{secrets.token_hex(6)}"
        set_code = b.set(CRED_ACCOUNT, value)
        get_code, got = b.get(CRED_ACCOUNT)
        delete_code = b.delete(CRED_ACCOUNT)
        after_code, _after = b.get(CRED_ACCOUNT)
        ok = set_code == 0 and get_code == 0 and got == value and delete_code == 0 and after_code == keystore.NOT_FOUND
        return {"ok": ok, "set_code": set_code, "get_code": get_code, "value_matches": got == value,
                "delete_code": delete_code, "get_after_delete_code": after_code,
                "get_after_delete_is_not_found": after_code == keystore.NOT_FOUND}

    def check_policy(self) -> dict:
        from lyssna.win import policy

        value = policy.read_preference("LocalOnly")
        return {"ok": value is None, "local_only_policy": value, "expected": "none"}

    def check_run_key(self) -> dict:
        from lyssna.win import autostart

        from . import winapi

        out = winapi.run_key_round_trip()
        out["ok"] = bool(out.get("read_back_matches") and out.get("gone_after_remove"))
        out["lyssna_start_at_login_enabled"] = autostart.is_enabled()
        return out

    def check_pipe(self) -> dict:
        from lyssna import config
        from lyssna.win import ipc

        marker = Path(tempfile.mkdtemp(prefix="lyssna-probe-")) / "probe.pipe"
        address = rf"\\.\pipe\lyssna-probe-{secrets.token_hex(4)}"
        old_path, old_address = config.SOCKET_PATH, ipc.pipe_address
        config.SOCKET_PATH = marker
        ipc.pipe_address = lambda: address
        out: dict = {"address": address}
        server = None
        try:
            server = ipc.ControlServer(lambda req: {"ok": True, "echo": req.get("n")}, address=address)
            server.start()
            out["server_started"] = True
            out["marker_written"] = marker.is_file()
            start = time.perf_counter()
            reply = ipc.send({"cmd": "probe", "n": 7}, timeout=5.0)
            out["round_trip_seconds"] = round(time.perf_counter() - start, 3)
            out["reply"] = reply
        finally:
            if server is not None:
                server.close()
            config.SOCKET_PATH = old_path
            ipc.pipe_address = old_address
        out["marker_removed"] = not marker.exists()
        shutil.rmtree(marker.parent, ignore_errors=True)
        out["ok"] = out.get("reply") == {"ok": True, "echo": 7}
        return out

    def check_uia(self) -> dict:
        """Build the UI Automation object the way lyssna/win/capture.py does, on a thread of its
        own, and say which class worked and whether the two timeouts took. Then ask capture's
        own COM thread whether it started."""
        out: dict = {}
        done = threading.Event()

        def work():
            try:
                import comtypes
                import comtypes.client

                try:
                    comtypes.CoInitializeEx()
                except OSError:
                    pass
                comtypes.client.GetModule("UIAutomationCore.dll")
                import comtypes.gen
                from comtypes.gen import UIAutomationClient as UIA

                out["comtypes_gen_dir"] = str(comtypes.client.gen_dir)
                out["comtypes_gen_path"] = [str(p) for p in comtypes.gen.__path__]
                out["uia_module_file"] = str(getattr(UIA, "__file__", ""))
                try:
                    automation = comtypes.client.CreateObject(UIA.CUIAutomation8, interface=UIA.IUIAutomation)
                    out["object"] = "CUIAutomation8"
                except Exception as e:
                    out["cuiautomation8_error"] = f"{type(e).__name__}: {e}"
                    automation = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
                    out["object"] = "CUIAutomation"
                try:
                    a2 = automation.QueryInterface(UIA.IUIAutomation2)
                    a2.ConnectionTimeout = 1000
                    a2.TransactionTimeout = 1000
                    out["timeouts_set"] = True
                    out["connection_timeout_ms"] = int(a2.ConnectionTimeout)
                    out["transaction_timeout_ms"] = int(a2.TransactionTimeout)
                    a2 = None
                except Exception as e:
                    out["timeouts_set"] = False
                    out["timeouts_error"] = f"{type(e).__name__}: {e}"
                root = automation.GetRootElement()
                out["root_element"] = bool(root)
                root = automation = None
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
                import traceback

                out["traceback"] = traceback.format_exc()
            finally:
                done.set()

        threading.Thread(target=work, daemon=True, name="probe-uia").start()
        if not done.wait(60):
            out["error"] = "UI Automation setup did not finish within 60 seconds"
        from lyssna.win import capture

        try:
            out["lyssna_com_thread_ready"] = bool(capture._com.call(lambda: "automation" in capture._uia))
        except Exception as e:
            out["lyssna_com_thread_ready"] = False
            out["lyssna_com_thread_error"] = f"{type(e).__name__}: {e}"
        out["ok"] = "error" not in out and bool(out.get("object"))
        return out

    def check_clipboard(self) -> dict:
        from lyssna.win import clipboard

        out: dict = {"seq_start": clipboard.sequence()}
        original = clipboard.get_text()
        saved = clipboard.snapshot()
        out["original_text_length"] = len(original)
        out["formats_saved"] = None if saved is None else len(saved)
        out["set_ok"] = clipboard.set_text(CLIPBOARD_TEST)
        out["seq_after_set"] = clipboard.sequence()
        out["read_back_matches"] = clipboard.get_text() == CLIPBOARD_TEST
        out["restore_ok"] = clipboard.restore(saved)
        out["seq_after_restore"] = clipboard.sequence()
        out["text_after_restore_matches"] = clipboard.get_text() == original
        out["ok"] = bool(saved is not None and out["set_ok"] and out["read_back_matches"] and out["restore_ok"]
                         and out["text_after_restore_matches"])
        return out

    def system_checks(self) -> dict:
        return {
            "credentials": timed(self.check_credentials),
            "policy": timed(self.check_policy),
            "run_key": timed(self.check_run_key),
            "pipe": timed(self.check_pipe),
            "ui_automation": timed(self.check_uia),
            "clipboard": timed(self.check_clipboard),
        }

    # ----- hooks --------------------------------------------------------------------------

    def install_keyboard(self, on_hotkey) -> bool:
        from lyssna.win.events import KeyboardTap

        if self.keyboard is None:
            self.keyboard = KeyboardTap()
            self.keyboard.bind("ctrl+alt+s", on_hotkey)
        return self.keyboard.start()

    def set_raw_listener(self, fn) -> None:
        if self.keyboard is not None:
            self.keyboard.raw_listener = fn

    def install_mouse(self, detector) -> bool:
        from lyssna.win.events import MouseTap

        if self.mouse is None:
            self.mouse = MouseTap(detector)
        else:
            self.mouse.detector = detector
        return self.mouse.start()

    def stop_mouse(self) -> bool:
        """Remove the mouse hook once the mouse step is over; True when its thread ended."""
        tap, self.mouse = self.mouse, None
        if tap is None:
            return True
        thread = tap._thread
        tap.stop()
        return thread is None or not thread.is_alive()

    def remove_hooks(self) -> dict:
        out = {}
        for name in ("keyboard", "mouse"):
            tap = getattr(self, name)
            if tap is None:
                continue
            thread = tap._thread
            tap.stop()
            out[f"{name}_removed"] = thread is None or not thread.is_alive()
            setattr(self, name, None)
        return out

    def key_down(self, vk: int) -> bool:
        from . import winapi

        return winapi.key_down(vk)

    def inject_unassigned_key(self) -> None:
        from lyssna.win import capture

        capture._send_inputs([(VK_UNASSIGNED, True), (VK_UNASSIGNED, False)])

    # ----- reading the selection ----------------------------------------------------------

    def foreground(self) -> dict:
        from . import winapi

        return winapi.foreground()

    def cursor_pos(self) -> tuple[int, int]:
        from . import winapi

        return winapi.cursor_pos()

    def window_rect(self, hwnd: int):
        from . import winapi

        return winapi.window_rect(hwnd)

    def ax_selection(self) -> dict:
        from lyssna.win import capture

        start = time.perf_counter()
        ax = capture.ax_selection()
        return {"supported": ax.supported, "text_length": len(ax.text), "range_type": type(ax.range).__name__,
                "range": repr(ax.range)[:80], "bounds": list(ax.bounds) if ax.bounds else None,
                "seconds": round(time.perf_counter() - start, 3), "_text": ax.text}

    def read_selection(self) -> dict:
        """What the reading hotkey would read, right now: UI Automation first, then the whole
        fallback chain. Runs on the hotkey's own thread."""
        from lyssna.win import capture

        out: dict = {"foreground": self.foreground()}
        try:
            out["cursor"] = list(self.cursor_pos())
        except Exception as e:
            out["cursor_error"] = str(e)
        ax = self.ax_selection()
        text = ax.pop("_text")
        out["ax"] = ax
        out["console_in_front"] = capture._console_in_front()
        start = time.perf_counter()
        full, source = capture.selection_or_clipboard()
        out["chain"] = {"source": source, "text_length": len(full), "seconds": round(time.perf_counter() - start, 3)}
        if out["console_in_front"]:
            out["chain"]["copy_skipped_by_console_guard"] = source != "selection (copy)"
        out["_snippet"] = snippet(text or full)
        return out

    def add_no_activate(self, hwnd: int) -> dict:
        from . import winapi

        return winapi.add_no_activate(hwnd)

    def office_installed(self) -> dict:
        from . import winapi

        return winapi.office_installed()

    # ----- the voice ----------------------------------------------------------------------

    def sapi_synthesize(self, text: str = SENTENCE):
        from lyssna.providers.windows import WindowsVoiceProvider

        provider = WindowsVoiceProvider({})
        start = time.perf_counter()
        audio = provider.synthesize(text)
        info = {"voice": provider.voice, "synthesis_seconds": round(time.perf_counter() - start, 3), **wav_info(audio.data)}
        return audio, info

    def play(self, audio, speed: float) -> dict:
        from lyssna.win import audio as win_audio

        start = time.perf_counter()
        player = win_audio.make_player(audio, speed)
        out = {"speed": speed, "load_seconds": round(time.perf_counter() - start, 3), "duration": round(player.duration(), 3)}
        start = time.perf_counter()
        player.play()
        limit = player.duration() / speed + 8.0
        while player.isPlaying() and time.perf_counter() - start < limit:
            time.sleep(0.05)
        out["played_seconds"] = round(time.perf_counter() - start, 3)
        out["expected_seconds"] = round(player.duration() / speed, 3)
        out["position_at_end"] = round(player.currentTime(), 3)
        player.stop()
        return out

    def load_player(self, audio) -> dict:
        """Load the audio into the player without playing it, for the self-test."""
        from lyssna.win import audio as win_audio

        start = time.perf_counter()
        player = win_audio.make_player(audio, 1.0)
        out = {"load_seconds": round(time.perf_counter() - start, 3), "duration": round(player.duration(), 3)}
        player.stop()
        return out

    def kokoro_download(self, progress) -> dict:
        from lyssna.providers import kokoro

        start = time.perf_counter()
        out: dict = {"already_present": kokoro.files_present(), "folder": str(kokoro.DATA_DIR)}
        if not out["already_present"]:
            kokoro.DOWNLOAD.start()
            while kokoro.DOWNLOAD.active():
                fraction = kokoro.DOWNLOAD.progress()
                if fraction is not None:
                    progress(fraction)
                time.sleep(0.25)
            out["download_error"] = kokoro.DOWNLOAD.error
        out["files_present"] = kokoro.files_present()
        out["download_seconds"] = round(time.perf_counter() - start, 3)
        return out

    def kokoro_benchmark(self, text: str = SENTENCE):
        """The app's Kokoro setup, timed cold and warm (see speed.py). Returns (first audio, timings)."""
        from .speed import kokoro_benchmark

        return kokoro_benchmark(text)

    def temp_leftovers(self) -> dict:
        time.sleep(1.0)  # players let go of their files a moment after they stop
        files = glob.glob(os.path.join(tempfile.gettempdir(), "lyssna-*"))
        files = [f for f in files if not os.path.basename(f).startswith("lyssna-probe-")]
        return {"temp_folder": tempfile.gettempdir(), "leftover_lyssna_files": len(files)}

    # ----- typing -------------------------------------------------------------------------

    def paste_test(self) -> dict:
        from lyssna.win import capture, clipboard, hotkeys

        out: dict = {"foreground": self.foreground(), "seq_start": clipboard.sequence()}
        saved = clipboard.snapshot()
        out["formats_saved"] = None if saved is None else len(saved)
        out["seq_after_snapshot"] = clipboard.sequence()
        out["set_ok"] = clipboard.set_text(PASTE_TEXT, transient=True)
        out["seq_after_set"] = clipboard.sequence()
        capture.send_key(VK_V, hotkeys.CTRL)
        out["seq_after_ctrl_v"] = clipboard.sequence()
        time.sleep(0.5)
        out["seq_after_wait"] = clipboard.sequence()
        out["restore_ok"] = clipboard.restore(saved)
        out["seq_after_restore"] = clipboard.sequence()
        return out

    # ----- other --------------------------------------------------------------------------

    def open_mic_settings(self) -> None:
        from lyssna import host

        host.open_target("ms-settings:privacy-microphone")

    def open_folder(self, path: Path) -> None:
        from . import winapi

        winapi.open_folder(str(path))

    def report_dir(self) -> Path:
        return report_dir()


class FakeBackend:
    """Made-up results for the preview on a Mac. Every value says it is fake."""

    preview = True

    def __init__(self, fast: bool = False):
        self.fast = fast
        self.raw_listener = None

    def _pause(self, seconds: float) -> None:
        time.sleep(0.15 if self.fast else seconds)  # long enough for a screenshot of the busy screen

    def about_system(self) -> dict:
        self._pause(0.5)
        return {"windows": {"name": "Windows 11", "build": 26100, "note": FAKE},
                "machine": {"cpu_name": FAKE, "physical_cores": 4, "logical_processors": 8, "memory_gb": 16.0},
                "keyboard": {"layout_id": "0000041D", "language": "sv-SE", "has_altgr": True, "altgr_characters": "@$[]{}"},
                "dpi_awareness": FAKE, "python": sys.version.split()[0], "frozen": False, "pitch_compensation": FAKE}

    def system_checks(self) -> dict:
        self._pause(0.8)
        names = ("credentials", "policy", "run_key", "pipe", "ui_automation", "clipboard")
        return {n: {"ok": n != "run_key", "seconds": 0.01, "note": FAKE} for n in names}

    def install_keyboard(self, on_hotkey) -> bool:
        return True

    def set_raw_listener(self, fn) -> None:
        self.raw_listener = fn

    def install_mouse(self, detector) -> bool:
        return True

    def stop_mouse(self) -> bool:
        return True

    def remove_hooks(self) -> dict:
        return {"keyboard_removed": True, "mouse_removed": True, "note": FAKE}

    def key_down(self, vk: int) -> bool:
        return False

    def inject_unassigned_key(self) -> None:
        pass

    def foreground(self) -> dict:
        return {"hwnd": 1, "class": "Chrome_WidgetWin_1", "process": "chrome.exe", "note": FAKE}

    def cursor_pos(self) -> tuple[int, int]:
        return (900, 500)

    def window_rect(self, hwnd: int):
        return None

    def ax_selection(self) -> dict:
        return {"supported": True, "text_length": 42, "range_type": "tuple", "bounds": [700, 420, 400, 22], "note": FAKE}

    def read_selection(self) -> dict:
        self._pause(0.3)
        return {"foreground": self.foreground(), "cursor": [900, 500], "ax": self.ax_selection(),
                "chain": {"source": "selection (accessibility)", "text_length": 42, "seconds": 0.05},
                "_snippet": "This is made-up text for the preview on a Mac"}

    def add_no_activate(self, hwnd: int) -> dict:
        return {"note": FAKE}

    def office_installed(self) -> dict:
        return {"winword": True, "outlook": False}

    def sapi_synthesize(self, text: str = SENTENCE):
        self._pause(0.5)
        return None, {"voice": FAKE, "synthesis_seconds": 0.4, "seconds": 4.2}

    def play(self, audio, speed: float) -> dict:
        self._pause(1.0)
        return {"speed": speed, "duration": 4.2, "note": FAKE}

    def load_player(self, audio) -> dict:
        return {"duration": 4.2, "note": FAKE}

    def kokoro_download(self, progress) -> dict:
        for i in range(0, 101, 20):
            progress(i / 100)
            self._pause(0.3)
        return {"files_present": True, "note": FAKE}

    def kokoro_benchmark(self, text: str = SENTENCE):
        self._pause(0.5)
        row = {"synthesis_seconds": 0.9, "audio_seconds": 4.0, "real_time_factor": 0.225}
        return None, {"cold": row, "warm_1": row, "warm_2": row, "seconds": 4.0, "note": FAKE}

    def temp_leftovers(self) -> dict:
        return {"leftover_lyssna_files": 0, "note": FAKE}

    def paste_test(self) -> dict:
        self._pause(0.6)
        return {"seq_start": 10, "seq_after_set": 14, "seq_after_restore": 18, "note": FAKE}

    def open_mic_settings(self) -> None:
        pass

    def open_folder(self, path: Path) -> None:
        from lyssna import host

        host.open_target(str(Path(path).parent))

    def report_dir(self) -> Path:
        return Path(tempfile.gettempdir())
