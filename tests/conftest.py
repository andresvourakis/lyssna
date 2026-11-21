"""Keep every test off the real machine state: config, cache, logs, usage, the control
socket, the LaunchAgent and the old Speechy locations all point into a per-test temp dir.
The Keychain is an in-memory fake in every test, so nothing can read or write the real login
Keychain (its launch read runs inline unless a test says otherwise), and the managed
preferences (defaults domain) read as unset unless a test fakes them. The Kokoro voice files
live under the temp dir too, and nothing may download them or load onnxruntime: the HTTP
client and the model loader refuse unless a test fakes them.
Dictation is kept off the machine the same way: its vocabulary and model files live under the
temp dir, the speech and cleanup models cannot load, and httpx.post and httpx.get (every
online engine, and the key checks) refuse unless a test fakes them. Tests that need a config file write their own on top of this.
The browser is never opened: the website has no address unless a test gives it one, and the
call that would open a page refuses unless a test fakes it."""

import sys

import httpx
import pytest

from lyssna import cache, cli, config, download, keychain, migrate, policy, server, support, usage
from lyssna.providers import kokoro

# These files need AppKit, Quartz or the Mac-only modules (lyssna.ui, app, capture, events), so they cannot run off macOS.
collect_ignore = [] if sys.platform == "darwin" else [
    "test_capture_ax_gate.py", "test_clipboard.py", "test_dictation_insert.py", "test_events.py", "test_dictation_app.py",
    "test_player.py", "test_keychain_launch.py", "test_menu.py", "test_setup_window.py", "test_history_window.py",
    "test_dictation_pill.py", "test_panel.py", "test_theme.py", "test_whats_new_window.py", "test_selection.py", "test_host.py",
]


class FakeKeychain:
    """Stands in for keychain.SecurityBackend. Set fail_get / fail_set / fail_delete to an
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
    monkeypatch.setattr(keychain, "_backend", fake)

    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the real Keychain")

    for name in ("get", "set", "delete"):  # in case a test builds a SecurityBackend itself
        monkeypatch.setattr(keychain.SecurityBackend, name, refuse)
    monkeypatch.setattr(keychain, "_spawn", lambda fn: fn())  # the launch read, inline: tests stay in order
    monkeypatch.setattr(keychain, "_listeners", [])  # no controller from an earlier test hears about keys
    monkeypatch.setattr(keychain, "_pending", set())
    keychain.clear_cache()
    yield fake
    keychain.clear_cache()


@pytest.fixture(autouse=True)
def managed_prefs(monkeypatch):
    """The defaults domain as a dict; a test sets e.g. managed_prefs["LocalOnly"] = True."""
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
    monkeypatch.setattr(migrate, "OLD_CONFIG_PATH", home / "old-config" / "config.toml")
    monkeypatch.setattr(migrate, "OLD_STATE_DIR", home / "old-state")
    monkeypatch.setattr(migrate, "OLD_CACHE_DIR", home / "old-cache")
    monkeypatch.setattr(server, "SOCKET_PATH", paths["SOCKET_PATH"])  # imported by name
    monkeypatch.setattr(usage, "USAGE_PATH", paths["STATE_DIR"] / "usage.json")
    monkeypatch.setattr(cli, "LAUNCH_AGENT", home / "LaunchAgents" / "io.github.andresvourakis.lyssna.plist")
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
    monkeypatch.setattr(kokoro, "_engine", None)
    monkeypatch.setattr(kokoro, "_make_engine", no_model)
    monkeypatch.setattr(download, "make_client", no_network)
    return isolated_paths / "state" / "kokoro"


@pytest.fixture(autouse=True)
def offline_dictation(isolated_paths, monkeypatch):
    """Dictation state starts fresh in every test; no model loads, nothing goes online."""
    from lyssna.dictation import history, models, vocabulary
    from lyssna.dictation.cleaners import s1mini
    from lyssna.dictation.transcribers import parakeet

    def no_model(*args, **kwargs):
        raise AssertionError("a test tried to load a real dictation model")

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to call an online service")

    monkeypatch.setattr(models, "DOWNLOADS", {})
    monkeypatch.setattr(vocabulary, "_shared", None)
    monkeypatch.setattr(history, "_shared", None)  # its file is under STATE_DIR, so the temp home
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
