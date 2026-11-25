"""Keep every test off the real machine state: config, cache, logs, usage, the control
socket, the LaunchAgent and the old Speechy locations all point into a per-test temp dir.
The key store's backend is an in-memory fake in every test, so nothing can read or write the
real login Keychain or Credential Manager (the launch read runs inline unless a test says
otherwise), and the managed preferences read as unset unless a test fakes them. The Kokoro voice files
live under the temp dir too, and nothing may download them or load onnxruntime: the HTTP
client and the model loader refuse unless a test fakes them.
Dictation is kept off the machine the same way: its vocabulary and model files live under the
temp dir, the speech and cleanup models cannot load, and httpx.post and httpx.get (every
online engine, and the key checks) refuse unless a test fakes them. Tests that need a config file write their own on top of this.
The browser is never opened: the website has no address unless a test gives it one, and the
call that would open a page refuses unless a test fakes it.
The Qt interface's tests draw on Qt's offscreen platform, so no window ever reaches the screen,
and share one QApplication (the qapp fixture)."""

import gc
import os
import sys
import threading
from pathlib import Path

import httpx
import pytest

from lyssna import cache, config, download, keystore, policy, support, usage
from lyssna.mac import credentials as mac_credentials
from lyssna.mac import launch_agent, speechy
from lyssna.providers import kokoro

# These need AppKit, Quartz or the Mac-only modules (the legacy interface in lyssna.legacy, lyssna.mac.capture and
# events), so they cannot run off macOS.
collect_ignore = [] if sys.platform == "darwin" else [
    "legacy", "test_mac_capture_ax_gate.py", "test_mac_clipboard.py", "test_dictation_insert.py", "test_mac_events.py",
    "test_player.py", "test_selection.py", "test_host.py",
]

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # before any test module imports Qt

# On the Mac the legacy AppKit interface's tests run in a pytest process of their own
# (`uv run pytest tests/legacy`; make test, CI and CONTRIBUTING.md run both). The app runs one
# interface per process, never AppKit's NSApplication next to a QApplication, and a run with
# both in one process could die in native code with no Python frame to show for it.
LEGACY_DIR = Path(__file__).resolve().parent / "legacy"
LEGACY_COMMAND = "uv run pytest tests/legacy"


def _legacy_asked_for(config) -> bool:
    """Did the command line name tests/legacy or something inside it?"""
    for arg in config.args:
        path = (config.invocation_params.dir / arg.split("::")[0]).resolve()
        if path == LEGACY_DIR or LEGACY_DIR in path.parents:
            return True
    return False


def pytest_ignore_collect(collection_path, config):
    if sys.platform == "darwin" and collection_path == LEGACY_DIR and not _legacy_asked_for(config):
        return True
    return None


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    alive = config.stash.get(_LEFTOVER_THREADS, [])
    terminalreporter.write_line(f"Lyssna threads still alive at the end: {len(alive)}")
    if len(alive) > MAX_LEFTOVER_THREADS:
        terminalreporter.write_line(f"More than {MAX_LEFTOVER_THREADS} were left behind, so the run fails: {alive}", red=True)
    if sys.platform == "darwin" and not _legacy_asked_for(config):
        terminalreporter.write_line(f"The legacy AppKit interface's tests were not run here; they run on their own: {LEGACY_COMMAND}")


class FakeKeychain:
    """Stands in for the key store's backend (host.credentials.CredentialBackend). Set fail_get / fail_set / fail_delete to an
    OSStatus (or an exception) to make that call fail; `calls` counts backend calls."""

    def __init__(self):
        self.items: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_get = self.fail_set = self.fail_delete = None

    def _fail(self, failure):
        if isinstance(failure, BaseException):
            raise failure
        return failure

    def get(self, account):
        self.calls.append(("get", account))
        if self.fail_get is not None:
            return self._fail(self.fail_get), None
        if account not in self.items:
            return -25300, None
        return 0, self.items[account]

    def set(self, account, value):
        self.calls.append(("set", account))
        if self.fail_set is not None:
            return self._fail(self.fail_set)
        self.items[account] = value
        return 0

    def delete(self, account):
        self.calls.append(("delete", account))
        if self.fail_delete is not None:
            return self._fail(self.fail_delete)
        return 0 if self.items.pop(account, None) is not None else -25300


@pytest.fixture(autouse=True)
def fake_keychain(monkeypatch):
    fake = FakeKeychain()
    monkeypatch.setattr(keystore, "_backend", fake)

    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the real Keychain")

    for name in ("get", "set", "delete"):  # in case a test builds the Mac backend itself
        monkeypatch.setattr(mac_credentials.CredentialBackend, name, refuse)
    monkeypatch.setattr(keystore, "_spawn", lambda fn: fn())  # the launch read, inline: tests stay in order
    monkeypatch.setattr(keystore, "_listeners", [])  # no controller from an earlier test hears about keys
    monkeypatch.setattr(keystore, "_pending", set())
    keystore.clear_cache()
    yield fake
    keystore.clear_cache()


@pytest.fixture(autouse=True)
def managed_prefs(monkeypatch):
    """The managed preferences as a dict; a test sets e.g. managed_prefs["LocalOnly"] = True."""
    prefs: dict = {}
    monkeypatch.setattr(policy, "_reader", prefs.get)
    return prefs


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    paths = {
        "CONFIG_DIR": home / "config",
        "CONFIG_PATH": home / "config" / "config.toml",
        "STATE_DIR": home / "state",
        "CACHE_DIR": home / "cache",
        "LOG_DIR": home / "logs",
        "SOCKET_PATH": home / "state" / "lyssna.sock",
    }
    for name, path in paths.items():
        monkeypatch.setattr(config, name, path)
    # The old Speechy locations the migration reads from, so no test reaches the real ones.
    monkeypatch.setattr(speechy, "OLD_CONFIG_PATH", home / "old-config" / "config.toml")
    monkeypatch.setattr(speechy, "OLD_STATE_DIR", home / "old-state")
    monkeypatch.setattr(speechy, "OLD_CACHE_DIR", home / "old-cache")
    monkeypatch.setattr(usage, "USAGE_PATH", paths["STATE_DIR"] / "usage.json")
    monkeypatch.setattr(launch_agent, "PATH", home / "LaunchAgents" / "io.github.andresvourakis.lyssna.plist")
    monkeypatch.delenv("LYSSNA_PROVIDER", raising=False)
    for section in ("INWORLD", "CARTESIA", "ELEVENLABS", "OPENAI", "ANTHROPIC"):
        monkeypatch.delenv(f"{section}_API_KEY", raising=False)
    assert cache.config is config  # AudioCache reads config.CACHE_DIR at construction time
    return home


@pytest.fixture(autouse=True)
def offline_kokoro(isolated_paths, monkeypatch):
    """Kokoro's files go under the temp home, each test gets a fresh download state, and
    neither the network nor the real model can be reached."""

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to download over the network")

    def no_model(*args, **kwargs):
        raise AssertionError("a test tried to load the real Kokoro model")

    monkeypatch.setattr(kokoro, "DATA_DIR", isolated_paths / "state" / "kokoro")
    monkeypatch.setattr(kokoro, "DOWNLOAD", kokoro.VoiceDownload())
    monkeypatch.setattr(kokoro, "UPGRADE", kokoro.VoiceDownload())
    monkeypatch.setattr(kokoro, "_engine", None)
    monkeypatch.setattr(kokoro, "_engine_model", None)
    monkeypatch.setattr(kokoro, "_make_engine", no_model)
    monkeypatch.setattr(download, "make_client", no_network)
    return isolated_paths / "state" / "kokoro"


@pytest.fixture(autouse=True)
def offline_dictation(isolated_paths, monkeypatch):
    """Dictation state starts fresh in every test; no model loads, nothing goes online."""
    from lyssna.dictation import failed, history, models, vocabulary
    from lyssna.dictation.cleaners import s1mini
    from lyssna.dictation.transcribers import parakeet

    def no_model(*args, **kwargs):
        raise AssertionError("a test tried to load a real dictation model")

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to call an online service")

    monkeypatch.setattr(models, "DOWNLOADS", {})
    monkeypatch.setattr(vocabulary, "_shared", None)
    monkeypatch.setattr(history, "_shared", None)  # its file is under STATE_DIR, so the temp home
    monkeypatch.setattr(failed, "_shared", None)  # the failed recordings too
    monkeypatch.setattr(parakeet, "_make_model", no_model)
    monkeypatch.setattr(parakeet, "_model", None)
    monkeypatch.setattr(parakeet, "_warming", None)
    monkeypatch.setattr(s1mini, "_make_runner", no_model)
    monkeypatch.setattr(s1mini, "_runner", None)
    monkeypatch.setattr(s1mini, "_loading", None)
    monkeypatch.setattr(s1mini, "_timer", None)
    monkeypatch.setattr(httpx, "post", no_network)
    monkeypatch.setattr(httpx, "get", no_network)
    return isolated_paths / "state" / "dictation"


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Support links are off (no website address) and nothing can open a page in the browser."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a page in the browser")

    monkeypatch.setattr(support, "SITE_URL", "")
    monkeypatch.delenv(support.SITE_ENV, raising=False)
    monkeypatch.setattr(support, "_open", refuse)


@pytest.fixture(autouse=True)
def no_leftovers(monkeypatch):
    """Each test ends what it started, so nothing from it runs during a later test against that
    test's state, or touches a window or widget after the test let go of it: every Controller is
    shut down and every dictation session's worker ends; threading.Timers started during the
    test (the Qt and AppKit later() timers, the controller's message timers) are cancelled; calls
    queued for the Qt main thread and not run are dropped; and the test's garbage is collected
    here, on the main thread. Left to chance, Python's cycle collector frees those objects on
    whichever thread happens to allocate next, and an AppKit view or a Qt widget must never be
    freed off the main thread."""
    from lyssna.controller import Controller
    from lyssna.dictation.session import DictationSession

    before = set(threading.enumerate())
    controllers: list = []
    sessions: list = []
    controller_init, session_init = Controller.__init__, DictationSession.__init__

    def track_controller(self, *args, **kwargs):
        controllers.append(self)
        controller_init(self, *args, **kwargs)

    def track_session(self, *args, **kwargs):
        sessions.append(self)
        session_init(self, *args, **kwargs)

    monkeypatch.setattr(Controller, "__init__", track_controller)
    monkeypatch.setattr(DictationSession, "__init__", track_session)
    yield
    for ctl in controllers:
        if hasattr(ctl, "dictation"):  # built all the way
            ctl.shutdown()
    for session in sessions:
        if hasattr(session, "thread"):
            session.close()
    for t in set(threading.enumerate()) - before:
        if isinstance(t, threading.Timer):
            t.cancel()
    for t in set(threading.enumerate()) - before:
        if isinstance(t, threading.Timer) or t.name == "lyssna-dictation":
            t.join(2)
    mainthread = sys.modules.get("lyssna.ui.mainthread")
    if mainthread is not None:
        mainthread.cancel_pending()
        # Taken from the loaded module, not imported: some tests make importing PySide6 fail.
        qt_core, shiboken, dispatcher = sys.modules["PySide6.QtCore"], sys.modules["shiboken6"], mainthread._dispatcher
        if qt_core.QCoreApplication.instance() is not None and dispatcher is not None and shiboken.isValid(dispatcher):
            qt_core.QCoreApplication.removePostedEvents(dispatcher)
    # The Windows player's private audio thread (lyssna.win.audio) is left running: it is one
    # per process by design, ended at exit, and stopping it per test made the next test's fresh
    # one crash in Qt Multimedia when that thread had made the application object.
    gc.collect(1)  # the test's young garbage; the module_garbage fixture does a full pass


@pytest.fixture(scope="module", autouse=True)
def module_garbage():
    """A full garbage collection on the main thread after each test module, for whatever of its
    garbage got old enough to escape no_leftovers' young-generation pass."""
    yield
    gc.collect()


@pytest.fixture(scope="session", autouse=True)
def collect_on_the_main_thread_only():
    """Python's cycle collector runs on whichever thread allocates past its threshold, and frees
    every unreachable object there, AppKit views and Qt widgets from earlier tests included.
    Freeing those off the main thread can crash the process. So during the tests it never runs
    by itself: no_leftovers and module_garbage collect on the main thread instead."""
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


# A few long-lived ones are expected: the Windows UI Automation threads tests build, and the
# Windows player's one audio thread.
MAX_LEFTOVER_THREADS = 8


def pytest_sessionfinish(session, exitstatus):
    """Fail the run when tests leave Lyssna's threads behind (no_leftovers ends what it knows of;
    this catches a new kind). Each one alive keeps its test's objects alive and can call into
    them during a later test."""
    alive = sorted(t.name for t in threading.enumerate() if t.name.startswith("lyssna-"))
    session.config.stash[_LEFTOVER_THREADS] = alive
    if len(alive) > MAX_LEFTOVER_THREADS and exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


_LEFTOVER_THREADS = pytest.StashKey[list]()


@pytest.fixture(scope="session")
def qapp():
    """The one QApplication the Qt interface's tests share, on the offscreen platform."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("a non-GUI Qt application already exists in this process")
    return app or QApplication(["lyssna-tests"])
