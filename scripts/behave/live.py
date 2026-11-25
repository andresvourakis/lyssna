"""One run of the real app on one interface: its profile, its process, its control channel and
its log.

The profile is a folder of its own under the run's output (config, state, cache, logs and a fake
home), with a config that reads with the macOS system voice (`say`, offline and free; the sound
is muted by the run anyway) and keeps everything on this Mac, and the first-run markers in place
so no setup window or What's New opens by itself. scripts/behave/launch.py applies
scripts/compare/isolate.py inside the app's process before the app starts. The control socket
lives in a short temp folder (a Unix socket path is limited to 104 bytes)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("behave.live")

INTERFACES = ("legacy", "new")
START_WAIT = 30.0
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (\w+) ([\w.]+): (.*)$")

BASE_CONFIG = """provider = "{provider}"
speed = 1.2
cache = false
local_only = true

[ui]
selection_button = true
mini_player = true
button_timeout = 4.0
player_dock = "{dock}"
player_x = {x}
player_y = {y}
idle_pill = true
auto_hide = {auto_hide}
wave = false
dictation_dock = "{rest_dock}"
dictation_y = {rest_y}
interface = "{interface}"

[dictation]
hotkey = "{hotkey}"
"""

BUNDLE_ENV = "LYSSNA_BEHAVE_EXE"  # a frozen build's lyssna-check, built with scripts/behave/launch.py as its entry
MODELS_ENV = "LYSSNA_BEHAVE_MODELS"  # a folder laid out as the data folder, holding only model files (default: the owner's)


def version() -> str:
    from lyssna import __version__

    return __version__


class LiveApp:
    """The app on one interface. start() and quit() may be called more than once (the restart
    scenario); every start writes the profile only the first time unless `fresh`."""

    def __init__(self, interface: str, out: Path, *, dock: str = "right", along: float = 0.5, auto_hide: bool = False,
                 rest_dock: str = "right", rest_along: float = 0.5, probe: bool = True, no_nap: bool = False,
                 enter_poll: float | None = None, x: float = -1.0, seen: str | None = None, models: bool = False,
                 hotkey: str = "right_option"):
        assert interface in INTERFACES
        self.interface = interface
        self.out = out
        self.home = out / "profile"
        self.settings = dict(dock=dock, x=x, y=along, auto_hide=str(auto_hide).lower(), rest_dock=rest_dock, rest_y=rest_along,
                             interface=interface, provider="kokoro" if models else "say", hotkey=hotkey)
        self.models = models  # the real speech, cleanup and voice models (feed.link_models), and the recorded microphone
        self.seen = seen  # the What's New version marked seen (default: this version, so nothing is offered)
        self.flags = (["--probe"] if probe else []) + (["--no-nap"] if no_nap else []) + (
            ["--enter-poll", str(enter_poll)] if enter_poll else [])
        self._sockdir = Path(tempfile.mkdtemp(prefix="lyb-"))
        self.mic = out / "mic"  # what the recorder hears (feed.say_next)
        self.kokoro_dir = self._sockdir / "k"  # short: see feed.link_models
        if models:
            self.flags += ["--models", "--mic", str(self.mic), "--kokoro-dir", str(self.kokoro_dir)]
        self.socket = self._sockdir / "s.sock"
        self.proc: subprocess.Popen | None = None
        self.started_at = 0.0
        self.launches = 0
        self._stderr = None

    # -- profile ---------------------------------------------------------------------------------

    def write_profile(self) -> None:
        for sub in ("config", "state", "cache", "logs", "home"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        (self.home / "config" / "config.toml").write_text(BASE_CONFIG.format(**self.settings))
        (self.home / "state" / "welcomed").write_text("")
        (self.home / "state" / "whats_new_seen").write_text(self.seen or version())
        if self.models:
            from . import feed

            source = os.environ.get(MODELS_ENV)
            missing = feed.link_models(self.home / "state", self.kokoro_dir, Path(source) if source else None)
            if missing:
                raise RuntimeError(f"model files missing (not downloaded by the harness): {', '.join(missing)}")
            self.mic.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self.home / "logs" / "lyssna.log"

    @property
    def config_path(self) -> Path:
        return self.home / "config" / "config.toml"

    # -- process ---------------------------------------------------------------------------------

    def start(self, wait: bool = True) -> LiveApp:
        if self.launches == 0:  # a fresh profile for every scenario; a restart within one keeps it
            shutil.rmtree(self.home, ignore_errors=True)
            self.write_profile()
        env = dict(os.environ)
        env["HOME"] = str(self.home / "home")
        env["LYSSNA_INTERFACE"] = self.interface
        for key in ("LYSSNA_PROVIDER", "INWORLD_API_KEY", "CARTESIA_API_KEY", "ELEVENLABS_API_KEY", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY"):
            env.pop(key, None)
        self.launches += 1
        if self._stderr is not None:
            self._stderr.close()
        stderr = self._stderr = open(self.out / f"stderr-{self.launches}.txt", "w")
        self.started_at = time.time()
        exe = os.environ.get(BUNDLE_ENV)  # the frozen bundle's check program (run.py --bundle), else this Python
        program = [exe] if exe else [sys.executable, "-m", "scripts.behave.launch"]
        self.proc = subprocess.Popen(
            [*program, "--home", str(self.home), "--socket", str(self.socket), *self.flags],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=stderr, stderr=stderr)
        if wait:
            self.wait_ready()
        return self

    def wait_ready(self, timeout: float = START_WAIT) -> float:
        """Seconds from start until the control channel answered and the idle form had time to show."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"the {self.interface} app ended at start (exit {self.proc.returncode}); see {self.out}")
            if self.send({"cmd": "status"}, timeout=1.0):
                ready = time.time() - self.started_at
                time.sleep(1.5)  # the idle form shows 0.5 s after start; the first-run check at 1 s
                return ready
            time.sleep(0.2)
        raise RuntimeError(f"the {self.interface} app did not answer within {timeout:.0f} s")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> int:
        return self.proc.pid if self.proc else 0

    def send(self, req: dict, timeout: float = 5.0) -> dict | None:
        if not self.socket.exists():
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(str(self.socket))
                s.sendall((json.dumps(req) + "\n").encode())
                data = b""
                while not data.endswith(b"\n"):
                    part = s.recv(65536)
                    if not part:
                        break
                    data += part
            return json.loads(data.decode() or "{}")
        except (OSError, ValueError):
            return None

    def status(self) -> dict:
        return (self.send({"cmd": "status"}) or {}).get("status", {})

    def ui(self) -> dict:
        return self.send({"cmd": "ui"}) or {}

    def player_frame(self) -> dict | None:
        return self.ui().get("mini_player")

    def wait_for(self, check, timeout: float = 10.0, every: float = 0.05):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(every)
        return None

    def quit(self, timeout: float = 10.0) -> str:
        """Ask the app to quit through its control channel; terminate it if it does not."""
        if not self.alive():
            return "not running"
        self.send({"cmd": "quit"}, timeout=2.0)
        try:
            self.proc.wait(timeout=timeout)
            return "quit"
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5.0)
                return "terminated"
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5.0)
                return "killed"

    def close(self) -> None:
        try:
            self.quit()
        finally:
            shutil.rmtree(self._sockdir, ignore_errors=True)
            if self._stderr is not None:
                self._stderr.close()
                self._stderr = None

    # -- the log ---------------------------------------------------------------------------------

    def log_lines(self, since: float = 0.0) -> list[tuple[float, str, str, str]]:
        """(time, level, logger, message) for every line logged at or after `since` (time.time())."""
        return read_log(self.log_path, since)


def read_log(path: Path, since: float = 0.0) -> list[tuple[float, str, str, str]]:
    out = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        m = LOG_LINE.match(line)
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
        probe = re.search(r" t=(\d+\.\d+)", m.group(4))
        if probe and m.group(3) == "behave.probe":
            t = float(probe.group(1))
        if t >= since - 0.002:
            out.append((t, m.group(2), m.group(3), m.group(4)))
    return out
