"""Adding a key from the setup window: one real synthesis proves it (faked here: httpx.post
is replaced), then it is saved to the (fake) Keychain and the engine is switched. Also the
"Get a key" links every key-needing engine offers."""

import sys
from urllib.parse import urlsplit

import httpx
import pytest

from lyssna import config
from lyssna.controller import Controller
from lyssna.providers import REGISTRY, kokoro


@pytest.fixture
def posts(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        status = 401 if kwargs["headers"].get("Authorization") == "Bearer bad" else 200
        return httpx.Response(status, content=b"MP3" if status == 200 else b"invalid key", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


@pytest.fixture
def ctl(monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE)
    return Controller()


def test_good_key_is_checked_saved_and_used(ctl, posts, fake_keychain):
    assert ctl.add_api_key("cartesia", "  good  ") == ""
    assert posts == ["https://api.cartesia.ai/tts/bytes"]  # exactly one request
    assert fake_keychain.items["cartesia"] == "good"
    assert ctl.cfg["provider"] == "cartesia"


def test_rejected_key_is_not_saved(ctl, posts, fake_keychain):
    error = ctl.add_api_key("openai", "bad")
    assert error.startswith("That key didn't work.") and "401" in error
    assert len(posts) == 1 and "openai" not in fake_keychain.items
    assert ctl.cfg["provider"] == "kokoro"


@pytest.mark.parametrize("key", ["", "   ", "two words", "line\nbreak"])
def test_malformed_key_never_reaches_the_network(ctl, posts, key):
    assert ctl.add_api_key("cartesia", key) != ""
    assert posts == []


@pytest.mark.skipif(sys.platform != "darwin", reason="asserts the Mac wording; test_windows_imports checks the Windows wording")
def test_local_only_refuses_before_any_request(ctl, posts, fake_keychain):
    ctl.set_local_only(True)
    assert "Keep Everything on This Mac" in ctl.add_api_key("cartesia", "good")
    assert posts == [] and "cartesia" not in fake_keychain.items


@pytest.mark.parametrize("name", [n for n, c in REGISTRY.items() if c.needs_key])
def test_key_pages_are_full_https_urls(name):
    parts = urlsplit(REGISTRY[name].key_url)
    assert parts.scheme == "https" and parts.hostname


def test_key_page_hosts():
    hosts = {n: urlsplit(c.key_url).hostname for n, c in REGISTRY.items() if c.needs_key}
    assert hosts == {
        "inworld": "platform.inworld.ai", "inworld-flash": "platform.inworld.ai", "cartesia": "play.cartesia.ai",
        "elevenlabs": "elevenlabs.io", "openai": "platform.openai.com",
    }
