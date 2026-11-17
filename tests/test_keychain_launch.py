"""Launch never waits for the Keychain. After an update macOS may ask for the password before
the app can read its saved key again, so the launch read runs on a background thread: here a
fake Keychain that blocks until the test releases it stands in for that prompt. The controller
and the menu build at once, the config file's provider stays as it was, and once the read
lands the menu shows the real state."""

import threading
import time

import pytest
from Foundation import NSDate, NSRunLoop

from lyssna import config, keychain
from lyssna.providers import kokoro


class BlockingKeychain:
    """The fake Keychain's items, but every read waits until `release` is set (a password
    prompt the user has not answered yet)."""

    def __init__(self, items):
        self.items = dict(items)
        self.release = threading.Event()
        self.reads: list[str] = []

    def get(self, account):
        self.release.wait(10)
        self.reads.append(account)
        return (0, self.items[account]) if account in self.items else (-25300, None)

    def set(self, account, value):
        self.items[account] = value
        return 0

    def delete(self, account):
        return 0 if self.items.pop(account, None) is not None else -25300


def spawn(fn):
    threading.Thread(target=fn, daemon=True, name="lyssna-keychain").start()


@pytest.fixture
def launch(monkeypatch):
    """Returns build(provider, items) -> (backend, menubar), with the real background read."""
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)
    monkeypatch.setattr(keychain, "_spawn", spawn)
    backends = []

    def build(provider, items):
        backend = BlockingKeychain(items)
        backends.append(backend)
        monkeypatch.setattr(keychain, "_backend", backend)
        config.ensure_dirs()
        config.CONFIG_PATH.write_text(config.TEMPLATE)
        config.update(provider=provider)
        from lyssna.app import MenuBar
        from lyssna.controller import Controller

        start = time.monotonic()
        menubar = MenuBar(Controller())
        assert time.monotonic() - start < 2.0  # built while the "prompt" is still up
        return backend, menubar

    yield build
    for backend in backends:
        backend.release.set()  # let any reader thread finish


def settle(backend, menubar, seconds=3.0):
    """Release the Keychain, wait for the read to land, and run the main-thread refresh."""
    backend.release.set()
    deadline = time.monotonic() + seconds
    while not keychain.known() and time.monotonic() < deadline:
        time.sleep(0.01)
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))


def test_launch_does_not_wait_and_keeps_the_provider_while_the_key_is_unknown(launch):
    backend, menubar = launch("cartesia", {})  # no key, but the Keychain has not said so yet
    assert backend.reads == []
    assert config.load()["provider"] == "cartesia"  # not rewritten from an unknown answer
    assert menubar.ctl.fell_back_to == ""
    assert menubar.provider_items["cartesia"].title == "Cartesia Sonic"  # no "no API key" flash
    assert menubar.provider_menu.title == "Voice Provider: Cartesia Sonic"

    settle(backend, menubar)
    assert config.load()["provider"] == "kokoro"  # the real answer: no key, so the voice on this Mac
    assert menubar.ctl.fell_back_to == "kokoro"
    assert menubar.provider_menu.title == "Voice Provider: On this Mac (Kokoro)"
    assert menubar.provider_items["cartesia"].title == "Cartesia Sonic · no API key"


def test_a_saved_key_keeps_its_provider(launch):
    backend, menubar = launch("cartesia", {"cartesia": "ck", "anthropic": "ak"})
    settle(backend, menubar)
    assert config.load()["provider"] == "cartesia" and menubar.ctl.fell_back_to == ""
    assert menubar.provider_items["cartesia"].title == "Cartesia Sonic"
    assert not menubar.remove_items["cartesia"].hidden  # the menu caught up: there is a key to remove
    assert menubar.cleanup_items["haiku"].title == "Claude Haiku 5.5"


def test_each_section_is_read_once_even_when_a_reading_asks_meanwhile(launch):
    backend, menubar = launch("cartesia", {"cartesia": "ck"})
    got = []
    reader = threading.Thread(target=lambda: got.append(keychain.get("cartesia")))
    reader.start()  # a reading on its own thread waits for the launch read, not a second prompt
    settle(backend, menubar)
    reader.join(2)
    assert got == ["ck"]
    assert backend.reads.count("cartesia") == 1
