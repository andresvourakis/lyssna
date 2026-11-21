"""Smoke test of the BUILT Windows app, run by .github/workflows/windows-build.yml with no one at the
keyboard. It drives dist\\Lyssna\\Lyssna.exe and dist\\Lyssna\\lyssna-cli.exe in a fresh, empty
profile, prints one PASS, FAIL, INFO or SKIP line per check, writes smoke-report.txt and the
screenshots into the output folder, and exits 1 when a must-pass check fails.

    python scripts/smoke_win.py [--dist dist\\Lyssna] [--out smoke]
    python scripts/smoke_win.py --self-check      check the helpers (any system, no app)

The profile: APPDATA and LOCALAPPDATA of the child processes point into a temp folder (the app
finds its files through lyssna.host.paths()), and config.toml there picks the built-in Windows
voice (provider = "sapi"), so nothing is downloaded. The start at login round trip is not run:
it would write the real Run key of this account. Dictation is on in the app, but nothing here
records or sends keys: its check only asks the running app whether its pieces loaded."""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONFIG_TOML = 'provider = "sapi"\n'
READ_TEXT = "This sentence is read by the built app."
STATUS_STATES = ("idle", "loading", "playing", "paused")
DICTATION_PIECES = ("mic", "insert", "field_probe", "pushtotalk")  # what lyssna.app reports under "dictation"


# -- pure helpers (checked by --self-check) ------------------------------------------------------


@dataclass
class Check:
    number: int
    name: str
    must_pass: bool
    result: str = "SKIP"  # PASS | FAIL | INFO | SKIP
    details: list[str] = field(default_factory=list)

    def line(self) -> str:
        kind = "must pass" if self.must_pass else "report only"
        return f"{self.result:<4} {self.number}. {self.name} ({kind})"

    def failed(self) -> bool:
        return self.must_pass and self.result != "PASS"


def profile_paths(base: Path) -> dict[str, Path]:
    """The temp profile: the two environment folders and the files the app keeps in them, laid
    out as lyssna.host.paths() does on Windows."""
    roaming, local = base / "Roaming", base / "Local"
    return {
        "APPDATA": roaming,
        "LOCALAPPDATA": local,
        "config": roaming / "Lyssna" / "config.toml",
        "log": local / "Lyssna" / "Logs" / "lyssna.log",
        "marker": local / "Lyssna" / "lyssna.pipe",
    }


def status_state(line: str) -> str:
    """The state word at the start of a `lyssna status` line ("playing 1/3 speed=1.2 ..."), or ""."""
    word = line.strip().split(" ", 1)[0] if line.strip() else ""
    return word if word in STATUS_STATES else ""


def dictation_lines(ui_reply: dict | None, status_reply: dict | None) -> tuple[bool, list[str]]:
    """Whether the app's dictation pieces all loaded and its keys are attached, from its `ui`
    and `status` replies, and one line per fact for the report."""
    dictation = (ui_reply or {}).get("dictation") or {}
    pieces = dictation.get("pieces") or {}
    lines = [f"dictation keys attached: {'yes' if dictation.get('keys') else 'no'}"]
    lines += [f"{name}: {pieces.get(name, '(not reported)')}" for name in DICTATION_PIECES]
    state = ((status_reply or {}).get("status") or {}).get("dictation") or {}
    lines.append(f"dictation state: {state.get('state', '(not reported)')}")
    ok = bool(dictation.get("keys")) and all(str(pieces.get(n, "")).startswith("lyssna.win.") for n in DICTATION_PIECES)
    return ok, lines


def summary(checks: list[Check]) -> str:
    failed = [c for c in checks if c.failed()]
    if failed:
        return "SMOKE FAILED: " + ", ".join(f"{c.number}. {c.name}" for c in failed)
    return "SMOKE PASSED: every must-pass check passed"


def report_text(checks: list[Check], header: list[str], app_log: str) -> str:
    out = [*header, ""]
    for c in checks:
        out.append(c.line())
        out.extend(f"     {d}" for d in c.details)
    out += ["", summary(checks), "", "---- app log (lyssna.log from the temp profile) ----", app_log or "(no log file)"]
    return "\n".join(out) + "\n"


def self_check() -> int:
    paths = profile_paths(Path("base"))
    assert paths["config"] == Path("base/Roaming/Lyssna/config.toml")
    assert paths["marker"] == Path("base/Local/Lyssna/lyssna.pipe")
    assert paths["log"] == Path("base/Local/Lyssna/Logs/lyssna.log")
    assert status_state("playing 1/3 speed=1.2 Hello") == "playing"
    assert status_state("idle 0/0 speed=1.2 ") == "idle"
    assert status_state("Lyssna app is not running.") == ""
    assert status_state("") == ""
    import tomllib

    assert tomllib.loads(CONFIG_TOML) == {"provider": "sapi"}
    pieces = {n: f"lyssna.win.{n}" for n in DICTATION_PIECES}
    ok, lines = dictation_lines({"dictation": {"keys": True, "pieces": pieces}}, {"status": {"dictation": {"state": "idle"}}})
    assert ok and lines[0] == "dictation keys attached: yes" and lines[-1] == "dictation state: idle"
    ok, lines = dictation_lines({"dictation": {"keys": True, "pieces": {**pieces, "mic": "missing: ImportError: x"}}}, None)
    assert not ok and "mic: missing: ImportError: x" in lines
    assert dictation_lines(None, None) == (False, ["dictation keys attached: no", *(f"{n}: (not reported)" for n in DICTATION_PIECES),
                                                   "dictation state: (not reported)"])
    a = Check(1, "a", True, "PASS")
    b = Check(2, "b", False, "INFO")
    assert summary([a, b]).startswith("SMOKE PASSED")
    c = Check(3, "c", True)  # skipped counts as failed when it must pass
    assert c.failed() and summary([a, c]) == "SMOKE FAILED: 3. c"
    text = report_text([a, c], ["head"], "")
    assert "PASS 1. a (must pass)" in text and "(no log file)" in text
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pyi_entry_cli import positional, starts_app

    assert starts_app([]) and starts_app(["app"]) and starts_app(["-v"]) and starts_app(["-p", "sapi"])
    assert not starts_app(["--help"]) and not starts_app(["status"]) and not starts_app(["-"])
    assert not starts_app(["app", "--help"]) and not starts_app(["Read this."])
    assert positional(["-p", "sapi", "hi", "-"]) == ["hi", "-"]
    print("smoke_win self-check: all helpers behave")
    return 0


# -- the run --------------------------------------------------------------------------------------


class Smoke:
    def __init__(self, dist: Path, out: Path):
        self.dist = dist.resolve()
        self.out = out.resolve()
        self.app_exe = self.dist / "Lyssna.exe"
        self.cli_exe = self.dist / "lyssna-cli.exe"
        self.base = Path(tempfile.mkdtemp(prefix="lyssna-smoke-"))
        self.paths = profile_paths(self.base)
        self.env = dict(os.environ)
        self.env["APPDATA"] = str(self.paths["APPDATA"])
        self.env["LOCALAPPDATA"] = str(self.paths["LOCALAPPDATA"])
        self.checks: list[Check] = []
        self.first: subprocess.Popen | None = None
        self._qt_app = None
        self._ipc = None

    # helpers

    def say(self, text: str) -> None:
        print(text, flush=True)

    def check(self, number: int, name: str, must_pass: bool) -> Check:
        c = Check(number, name, must_pass)
        self.checks.append(c)
        self.say(f"---- check {number}: {name}")
        return c

    def done(self, c: Check, result: str, *details: str) -> None:
        c.result = result
        c.details.extend(details)
        for d in details:
            self.say(f"     {d}")
        self.say(c.line())

    def cli(self, *args: str, timeout: float = 30.0) -> tuple[int | None, str, str]:
        """Run lyssna-cli.exe in the temp profile. Returns (exit code or None on timeout, stdout, stderr)."""
        try:
            r = subprocess.run([str(self.cli_exe), *args], env=self.env, capture_output=True, timeout=timeout,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            return None, _text(e.stdout), _text(e.stderr)
        return r.returncode, _text(r.stdout), _text(r.stderr)

    def ipc(self, req: dict, timeout: float = 5.0) -> dict | None:
        """Talk to the app over its pipe directly, for the replies the CLI does not print in full
        (the `ui` command, the provider and error in the status)."""
        if self._ipc is None:
            # lyssna.config reads APPDATA and LOCALAPPDATA once, at import: set them first.
            os.environ["APPDATA"] = self.env["APPDATA"]
            os.environ["LOCALAPPDATA"] = self.env["LOCALAPPDATA"]
            sys.path.insert(0, str(ROOT))
            from lyssna.win import ipc

            self._ipc = ipc
        return self._ipc.send(req, timeout=timeout)

    def app_log(self) -> str:
        try:
            return self.paths["log"].read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def screenshot(self, name: str) -> str:
        """A full-screen picture into the output folder. Qt first (grabWindow(0) of the primary
        screen), then PowerShell's System.Drawing when Qt gives back nothing."""
        path = self.out / name
        try:
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtWidgets import QApplication

            if self._qt_app is None:
                self._qt_app = QApplication.instance() or QApplication([sys.argv[0]])
            screen = QGuiApplication.primaryScreen()
            pixmap = screen.grabWindow(0) if screen is not None else None
            if pixmap is not None and not pixmap.isNull() and pixmap.width() > 0 and pixmap.save(str(path), "PNG"):
                return f"saved {path.name} with Qt ({pixmap.width()}x{pixmap.height()})"
            qt_note = "Qt returned an empty image"
        except Exception as e:
            qt_note = f"Qt screenshot failed: {type(e).__name__}: {e}"
        script = (
            "Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
            "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
            "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
            "$g = [System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size); "
            "$bmp.Save($env:SMOKE_SHOT, [System.Drawing.Imaging.ImageFormat]::Png); "
            "Write-Output \"$($b.Width)x$($b.Height)\""
        )
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               env={**os.environ, "SMOKE_SHOT": str(path)}, capture_output=True, timeout=60)
            if r.returncode == 0 and path.exists():
                return f"{qt_note}; saved {path.name} with PowerShell ({_text(r.stdout).strip()})"
            return f"{qt_note}; PowerShell screenshot failed (exit {r.returncode}): {_text(r.stderr).strip()[-400:]}"
        except Exception as e:
            return f"{qt_note}; PowerShell screenshot failed: {type(e).__name__}: {e}"

    def wait_status(self, seconds: float) -> tuple[int | None, str, str, float]:
        """Ask `lyssna-cli status` until it answers with a status line or time runs out."""
        start = time.monotonic()
        code, out, err = None, "", ""
        while time.monotonic() - start < seconds:
            code, out, err = self.cli("status", timeout=10)
            if code == 0 and status_state(out):
                break
            if self.first is not None and self.first.poll() is not None:
                break  # the app has ended; no point waiting
            time.sleep(0.5)
        return code, out, err, time.monotonic() - start

    # the checks

    def run(self) -> int:
        self.out.mkdir(parents=True, exist_ok=True)
        header = [
            "Lyssna Windows smoke test of the built app",
            f"app:      {self.app_exe}",
            f"cli:      {self.cli_exe}",
            f"profile:  {self.base}",
            f"config:   {self.paths['config']} ({CONFIG_TOML.strip()})",
            f"started:  {time.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        for h in header:
            self.say(h)
        for exe in (self.app_exe, self.cli_exe):
            if not exe.exists():
                self.say(f"FAIL {exe} does not exist. Did the build step run?")
                return 1
        self.paths["config"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["config"].write_text(CONFIG_TOML, encoding="utf-8")
        self.paths["LOCALAPPDATA"].mkdir(parents=True, exist_ok=True)
        try:
            self._checks()
        except Exception as e:  # a bug in this script must still leave a report
            self.say(f"FAIL the smoke script itself raised {type(e).__name__}: {e}")
            c = Check(0, f"smoke script error: {type(e).__name__}: {e}", True, "FAIL")
            self.checks.append(c)
        finally:
            self._cleanup()
        log = self.app_log()
        text = report_text(self.checks, header, log)
        (self.out / "smoke-report.txt").write_text(text, encoding="utf-8")
        self.say("")
        for c in self.checks:
            self.say(c.line())
        self.say(summary(self.checks))
        self.say(f"report: {self.out / 'smoke-report.txt'}")
        with contextlib.suppress(OSError):
            shutil.rmtree(self.base)
        return 1 if any(c.failed() for c in self.checks) else 0

    def _checks(self) -> None:
        # 1. --help
        c = self.check(1, "lyssna-cli.exe --help prints usage", True)
        code, out, err = self.cli("--help")
        if code == 0 and "usage:" in out.lower():
            self.done(c, "PASS", f"exit 0, first line: {out.strip().splitlines()[0]}")
        else:
            self.done(c, "FAIL", f"exit {code}", f"stdout: {out.strip()[-800:]}", f"stderr: {err.strip()[-800:]}")

        # 2. the app starts and answers
        c = self.check(2, "Lyssna.exe starts and lyssna-cli.exe status answers within 30 s", True)
        self.first = subprocess.Popen([str(self.app_exe)], env=self.env, cwd=str(self.dist))
        self.say(f"     started Lyssna.exe, pid {self.first.pid}")
        code, out, err, took = self.wait_status(30)
        app_up = code == 0 and bool(status_state(out))
        if app_up:
            self.done(c, "PASS", f"answered after {took:.1f} s: {out.strip()}")
        else:
            ended = self.first.poll()
            self.done(c, "FAIL", f"no status line after {took:.1f} s (last exit {code})",
                      f"Lyssna.exe {'ended with code ' + str(ended) if ended is not None else 'is still running'}",
                      f"stdout: {out.strip()}", f"stderr: {err.strip()[-800:]}",
                      "app log:", *(self.app_log() or "(no log file)").splitlines()[-60:])

        # 3. the setup window on first launch
        c = self.check(3, "first launch opens the setup window (screenshot 01-first-launch.png)", False)
        if app_up:
            time.sleep(3.0)
            welcomed = (self.paths["LOCALAPPDATA"] / "Lyssna" / "welcomed").exists()
            self.done(c, "INFO", self.screenshot("01-first-launch.png"),
                      f"first-run marker written: {'yes' if welcomed else 'no'}")
        else:
            self.done(c, "SKIP", "the app is not running")

        # 4. the ui command
        c = self.check(4, "the ui command answers over the pipe", True)
        if app_up:
            reply = self.ipc({"cmd": "ui"})
            if reply and reply.get("ok"):
                player = reply.get("mini_player")
                form = f"player shown at {player}" if player else "player hidden (idle)"
                self.done(c, "PASS", f"reply: {reply}", form)
            else:
                self.done(c, "FAIL", f"reply: {reply}")
        else:
            self.done(c, "FAIL", "skipped because the app is not running")

        # 5. dictation's pieces, asked of the running app (nothing is recorded, no key is sent)
        c = self.check(5, "the built app attached the dictation keys and loaded the microphone, typing and key modules", False)
        if app_up:
            ok, lines = dictation_lines(self.ipc({"cmd": "ui"}), self.ipc({"cmd": "status"}))
            self.done(c, "INFO" if ok else "FAIL", *lines)
        else:
            self.done(c, "SKIP", "the app is not running")

        # 6. reading a sentence
        c = self.check(6, "lyssna-cli.exe reads a sentence through the app (screenshot 02-reading.png)", False)
        if app_up:
            code, out, err = self.cli(READ_TEXT)
            details = [f"speak: exit {code} {out.strip()} {err.strip()[-400:]}".rstrip()]
            seen, providers, errors, messages = [], set(), set(), set()
            shot = ""
            start = time.monotonic()
            while time.monotonic() - start < 30:
                reply = self.ipc({"cmd": "status"}) or {}
                st = reply.get("status", {})
                state = st.get("state", "?")
                if not seen or seen[-1] != state:
                    seen.append(state)
                for key, bucket in (("provider", providers), ("provider_name", providers), ("error", errors),
                                    ("message", messages)):
                    if st.get(key):
                        bucket.add(str(st[key]))
                if state == "playing" and not shot:
                    shot = self.screenshot("02-reading.png")
                if state == "idle" and len(seen) > 1:
                    break
                time.sleep(0.25)
            if not shot:
                shot = self.screenshot("02-reading.png")
            details += [f"states seen: {' -> '.join(seen)}", f"provider: {', '.join(sorted(providers)) or '(none)'}",
                        f"error: {' | '.join(sorted(errors)) or '(none)'}", f"message: {' | '.join(sorted(messages)) or '(none)'}",
                        shot]
            self.done(c, "INFO", *details)
        else:
            self.done(c, "SKIP", "the app is not running")

        # 7. pause and stop
        c = self.check(7, "lyssna-cli.exe pause and stop are answered", True)
        if app_up:
            results = []
            for cmd in ("pause", "stop"):
                code, out, err = self.cli(cmd)
                results.append((cmd, code, out.strip() or err.strip()))
            ok = all(code == 0 for _cmd, code, _text in results)
            self.done(c, "PASS" if ok else "FAIL", *(f"{cmd}: exit {code}: {text}" for cmd, code, text in results))
        else:
            self.done(c, "FAIL", "skipped because the app is not running")

        # 8. a second copy
        c = self.check(8, "a second Lyssna.exe exits by itself within 10 s with code 0", True)
        if app_up:
            second = subprocess.Popen([str(self.app_exe)], env=self.env, cwd=str(self.dist))
            try:
                code2 = second.wait(10)
            except subprocess.TimeoutExpired:
                second.kill()
                code2 = None
            code, out, err = self.cli("status")
            first_ok = code == 0 and bool(status_state(out)) and self.first.poll() is None
            ok = code2 == 0 and first_ok
            self.done(c, "PASS" if ok else "FAIL",
                      f"second copy: {'still running after 10 s, killed' if code2 is None else 'exit code ' + str(code2)}",
                      f"first copy still answers: {'yes, ' + out.strip() if first_ok else 'no (exit ' + str(code) + ') ' + err.strip()}")
        else:
            self.done(c, "FAIL", "skipped because the app is not running")

        # 9. quit
        c = self.check(9, "lyssna-cli.exe quit ends the app within 10 s, the pipe marker is gone", True)
        if app_up:
            code, out, err = self.cli("quit")
            details = [f"quit: exit {code}: {out.strip() or err.strip()}"]
            try:
                ended = self.first.wait(10)
            except subprocess.TimeoutExpired:
                ended = None
            details.append(f"Lyssna.exe: {'still running after 10 s' if ended is None else 'ended with code ' + str(ended)}")
            code, out, err = self.cli("status")
            not_running = code != 0 and "not running" in err.lower()
            details.append(f"status afterwards: exit {code}: {(out.strip() or err.strip())}")
            marker_gone = not self.paths["marker"].exists()
            details.append(f"pipe marker {self.paths['marker']}: {'gone' if marker_gone else 'STILL THERE'}")
            ok = ended is not None and not_running and marker_gone
            self.done(c, "PASS" if ok else "FAIL", *details)
        else:
            self.done(c, "FAIL", "skipped because the app is not running")

        # 10. standalone reading
        c = self.check(10, "lyssna-cli.exe reads by itself with no app running", False)
        if self.first is not None and self.first.poll() is None:
            self.done(c, "SKIP", "the first copy is still running, so the reading would go to it")
        else:
            start = time.monotonic()
            code, out, err = self.cli("Standalone.", timeout=60)
            took = time.monotonic() - start
            ended = "did not end within 60 s" if code is None else f"exit {code} after {took:.1f} s"
            self.done(c, "INFO", ended, f"stdout: {out.strip()[-400:]}", f"stderr: {err.strip()[-1200:]}")

        # 11. start at login
        c = self.check(11, "start at login round trip", False)
        self.done(c, "SKIP", "not run: it would write the real Run key of this account")

    def _cleanup(self) -> None:
        """Leave no Lyssna running: our first copy, then any Lyssna.exe left on this machine."""
        if self.first is not None and self.first.poll() is None:
            self.say("cleanup: the first copy is still running; killing it")
            self.first.kill()
            with contextlib.suppress(Exception):
                self.first.wait(5)
        if sys.platform == "win32":
            r = subprocess.run(["taskkill", "/F", "/T", "/IM", "Lyssna.exe"], capture_output=True)
            if r.returncode == 0:
                self.say(f"cleanup: killed leftover Lyssna.exe processes: {_text(r.stdout).strip()}")
        if self._qt_app is not None:
            self._qt_app = None


def _text(data) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/smoke_win.py",
                                     description="Smoke test of the built Windows app (Lyssna.exe and lyssna-cli.exe).")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist" / "Lyssna", help="the built app folder (default dist\\Lyssna)")
    parser.add_argument("--out", type=Path, default=Path("smoke"), help="where smoke-report.txt and the screenshots go (default smoke)")
    parser.add_argument("--self-check", action="store_true", help="check the helpers only (any system, starts nothing)")
    ns = parser.parse_args(argv)
    if ns.self_check:
        return self_check()
    if sys.platform != "win32":
        print("smoke_win: this runs on Windows only (use --self-check elsewhere)", file=sys.stderr)
        return 2
    return Smoke(ns.dist, ns.out).run()


if __name__ == "__main__":
    sys.exit(main())
