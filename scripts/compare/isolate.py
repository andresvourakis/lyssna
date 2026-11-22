"""Keep a comparison run off the real machine, the way tests/conftest.py keeps the tests off it:
config, cache, logs, usage, the control socket, the LaunchAgent and the old Speechy locations
all point into a temp folder; the key store is an in-memory fake; nothing downloads, loads a
model, goes online or opens a browser. Plain attribute assignment (no pytest), done once at
the start of each side's process, before anything builds a window.

Imports nothing from AppKit or Qt, so both sides share it."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class FakeKeychain:
    """The key store's backend, in memory (tests/conftest.py FakeKeychain)."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def get(self, account):
        if account not in self.items:
            return -25300, None
        return 0, self.items[account]

    def set(self, account, value):
        self.items[account] = value
        return 0

    def delete(self, account):
        return 0 if self.items.pop(account, None) is not None else -25300


def _refuse(what: str):
    def refuse(*args, **kwargs):
        raise RuntimeError(f"the comparison harness refused to {what}")
    return refuse


def isolate(root: str | os.PathLike | None = None) -> Path:
    """Point every on-disk location at a fresh folder under `root` (a new temp folder by
    default) and switch off the network, the models, the browser and the real Keychain.
    Returns the fake home folder."""
    import httpx

    from lyssna import cache, config, download, keystore, policy, support, usage
    from lyssna.dictation import history, models, vocabulary
    from lyssna.dictation.cleaners import s1mini
    from lyssna.dictation.transcribers import parakeet
    from lyssna.mac import credentials as mac_credentials
    from lyssna.mac import launch_agent, speechy
    from lyssna.providers import kokoro

    home = Path(tempfile.mkdtemp(prefix="lyssna-compare-", dir=root))
    paths = {
        "CONFIG_DIR": home / "config",
        "CONFIG_PATH": home / "config" / "config.toml",
        "STATE_DIR": home / "state",
        "CACHE_DIR": home / "cache",
        "LOG_DIR": home / "logs",
        "SOCKET_PATH": home / "state" / "lyssna.sock",
    }
    for name, path in paths.items():
        setattr(config, name, path)
    speechy.OLD_CONFIG_PATH = home / "old-config" / "config.toml"
    speechy.OLD_STATE_DIR = home / "old-state"
    speechy.OLD_CACHE_DIR = home / "old-cache"
    usage.USAGE_PATH = paths["STATE_DIR"] / "usage.json"
    launch_agent.PATH = home / "LaunchAgents" / "io.github.andresvourakis.lyssna.plist"
    os.environ.pop("LYSSNA_PROVIDER", None)
    for section in ("INWORLD", "CARTESIA", "ELEVENLABS", "OPENAI", "ANTHROPIC"):
        os.environ.pop(f"{section}_API_KEY", None)
    assert cache.config is config

    # The key store: in memory, never the login Keychain.
    keystore._backend = FakeKeychain()
    for name in ("get", "set", "delete"):
        setattr(mac_credentials.CredentialBackend, name, _refuse("reach the real Keychain"))
    keystore._spawn = lambda fn: fn()
    keystore._listeners = []
    keystore._pending = set()
    keystore.clear_cache()
    policy._reader = {}.get  # no managed preferences

    # The free voice and dictation: files under the temp home, no model, no network.
    kokoro.DATA_DIR = home / "state" / "kokoro"
    kokoro.DOWNLOAD = kokoro.VoiceDownload()
    kokoro._engine = None
    kokoro._make_engine = _refuse("load the Kokoro model")
    download.make_client = _refuse("download over the network")
    models.DOWNLOADS = {}
    vocabulary._shared = None
    history._shared = None
    parakeet._make_model = _refuse("load a dictation model")
    parakeet._model = None
    parakeet._warming = None
    s1mini._make_runner = _refuse("load the cleanup model")
    s1mini._runner = None
    s1mini._loading = None
    s1mini._timer = None
    httpx.post = _refuse("call an online service")
    httpx.get = _refuse("call an online service")

    # No browser, no website address unless a pair sets one for its support line.
    support.SITE_URL = ""
    os.environ.pop(support.SITE_ENV, None)
    support._open = _refuse("open a page in the browser")
    return home
