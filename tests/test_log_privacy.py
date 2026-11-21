"""The log never holds what the user read or a provider's error body. A failed reading logs
the provider name, the HTTP status and a length; the full message only reaches the on-screen
notice. httpx.post is faked: nothing touches the network."""

import logging
import time

import httpx
import pytest

from lyssna import config
from lyssna.controller import Controller
from lyssna.providers import get_provider

TEXT = "My bank PIN is 4931 and my secret plan is to buy the moon."
BODY = f'{{"error": "could not synthesize: {TEXT}"}}'


@pytest.fixture
def ctl(monkeypatch, fake_keychain):
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    c = Controller()
    c.notices = []
    monkeypatch.setattr(c, "notify", lambda message, ttl=3.0: c.notices.append(message))
    return c


def wait_idle(ctl, timeout=3.0):
    deadline = time.time() + timeout
    while ctl.reader.is_active() and time.time() < deadline:
        time.sleep(0.02)
    assert not ctl.reader.is_active()


def test_failed_cloud_reading_logs_no_text_and_no_body(ctl, monkeypatch, caplog):
    def fake_post(url, **kwargs):
        return httpx.Response(400, text=BODY, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    ctl.reader.provider_factory = lambda: get_provider("cartesia", {"cartesia": {"api_key": "sk-test-123"}})
    caplog.set_level(logging.DEBUG)
    assert ctl.reader.speak(TEXT)
    wait_idle(ctl)

    logged = caplog.text
    assert "chunk 0 failed via cartesia" in logged and "HTTP 400" in logged
    for secret in ("PIN", "4931", "moon", "could not synthesize", "sk-test-123"):
        assert secret not in logged
    assert any("4931" in n for n in ctl.notices)  # the fuller message is shown on screen


def test_failure_without_http_status_logs_only_the_type(ctl, caplog):
    class Broken:
        name = "fake"
        price_per_million = 0.0

        def cache_key(self):
            return "fake"

        def describe(self):
            return "Fake"

        def synthesize(self, text):
            raise RuntimeError(f"model choked on: {text}")

    ctl.reader.provider_factory = Broken
    caplog.set_level(logging.DEBUG)
    assert ctl.reader.speak(TEXT)
    wait_idle(ctl)
    assert "RuntimeError" in caplog.text
    assert "4931" not in caplog.text and "moon" not in caplog.text


def test_rejected_key_logs_no_body(ctl, monkeypatch, caplog):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(401, text=BODY, request=httpx.Request("POST", url)))
    caplog.set_level(logging.DEBUG)
    message = ctl.add_api_key("cartesia", "sk-test-456")
    assert "4931" in message  # the user sees the provider's answer
    assert "4931" not in caplog.text and "sk-test-456" not in caplog.text
    assert "HTTP 401" in caplog.text
