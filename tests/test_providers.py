"""HTTP providers: request shape, missing keys, error mapping and response decoding.
httpx.post is replaced with a fake: nothing here touches the network or a paid API."""

import base64

import httpx
import pytest

from lyssna.providers import get_provider
from lyssna.providers.base import ProviderError

# name -> (url prefix, timeout, auth header name, expected auth header value for key "k")
HTTP_PROVIDERS = {
    "inworld": ("https://api.inworld.ai/tts/v1/voice", 60, "Authorization", "Basic k"),
    "inworld-flash": ("https://api.inworld.ai/tts/v1/voice", 60, "Authorization", "Basic k"),
    "cartesia": ("https://api.cartesia.ai/tts/bytes", 60, "Authorization", "Bearer k"),
    "elevenlabs": ("https://api.elevenlabs.io/v1/text-to-speech/", 90, "xi-api-key", "k"),
    "openai": ("https://api.openai.com/v1/audio/speech", 90, "Authorization", "Bearer k"),
}
VENDORS = {"inworld": "Inworld", "inworld-flash": "Inworld", "cartesia": "Cartesia", "elevenlabs": "ElevenLabs", "openai": "OpenAI"}


def ok_response(name: str, url: str) -> httpx.Response:
    request = httpx.Request("POST", url)
    if name.startswith("inworld"):
        return httpx.Response(200, json={"audioContent": base64.b64encode(b"MP3DATA").decode()}, request=request)
    return httpx.Response(200, content=b"MP3DATA", request=request)


@pytest.fixture
def calls(monkeypatch):
    """Records every httpx.post; answers with `calls.respond(url, kwargs)` (default: a 200)."""

    class Calls(list):
        respond = None

    rec = Calls()

    def fake_post(url, **kwargs):
        rec.append((url, kwargs))
        return rec.respond(url, kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)
    return rec


def provider(name: str, key: str = "k"):
    section = "inworld" if name.startswith("inworld") else name
    return get_provider(name, {section: {"api_key": key}})


@pytest.mark.parametrize("name", HTTP_PROVIDERS)
def test_request_shape_and_audio(name, calls):
    url_prefix, timeout, header, value = HTTP_PROVIDERS[name]
    calls.respond = lambda url, kw: ok_response(name, url)
    p = provider(name)
    audio = p.synthesize("Hello there.")
    assert audio.data == b"MP3DATA" and audio.ext == "mp3"
    assert len(calls) == 1  # no retries
    url, kw = calls[0]
    assert url.startswith(url_prefix)
    assert kw["timeout"] == timeout
    assert kw["headers"][header] == value
    assert "Hello there." in kw["json"].values()
    assert p.model in kw["json"].values()


def test_inworld_body_carries_voice_model_and_instruction(calls):
    calls.respond = lambda url, kw: ok_response("inworld", url)
    p = get_provider("inworld", {"inworld": {"api_key": "k", "voice": "Nate", "instruction": "calm"}})
    p.synthesize("Hi.")
    body = calls[0][1]["json"]
    assert body["voiceId"] == "Nate" and body["modelId"] == "inworld-tts-2" and body["instruction"] == "calm"


def test_elevenlabs_voice_goes_in_the_url(calls):
    calls.respond = lambda url, kw: ok_response("elevenlabs", url)
    get_provider("elevenlabs", {"elevenlabs": {"api_key": "k", "voice": "V123"}}).synthesize("Hi.")
    url, kw = calls[0]
    assert url.endswith("/V123") and kw["params"] == {"output_format": "mp3_44100_128"}


@pytest.mark.parametrize("name", HTTP_PROVIDERS)
def test_missing_key_raises_before_any_request(name, calls):
    with pytest.raises(ProviderError, match="No API key"):
        provider(name, key="").synthesize("Hi.")
    assert calls == []


@pytest.mark.parametrize("name", HTTP_PROVIDERS)
def test_non_200_becomes_provider_error(name, calls):
    calls.respond = lambda url, kw: httpx.Response(401, text="bad key", request=httpx.Request("POST", url))
    with pytest.raises(ProviderError, match=rf"^{VENDORS[name]} error 401: bad key$"):
        provider(name).synthesize("Hi.")
    assert len(calls) == 1


@pytest.mark.parametrize("name", HTTP_PROVIDERS)
def test_network_failure_becomes_provider_error(name, calls):
    def boom(url, kw):
        raise httpx.ConnectTimeout("timed out")

    calls.respond = boom
    with pytest.raises(ProviderError, match=rf"^{VENDORS[name]} request failed: timed out$"):
        provider(name).synthesize("Hi.")
    assert len(calls) == 1


def test_inworld_unexpected_json_is_a_provider_error(calls):
    calls.respond = lambda url, kw: httpx.Response(200, json={"nope": 1}, request=httpx.Request("POST", url))
    with pytest.raises(ProviderError, match="unexpected response"):
        provider("inworld").synthesize("Hi.")


def test_inworld_bad_base64_is_a_provider_error(calls):
    calls.respond = lambda url, kw: httpx.Response(200, json={"audioContent": "abc"}, request=httpx.Request("POST", url))
    with pytest.raises(ProviderError, match="unexpected response"):
        provider("inworld").synthesize("Hi.")


def test_key_from_environment(calls, monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "envkey")
    calls.respond = lambda url, kw: ok_response("cartesia", url)
    get_provider("cartesia", {}).synthesize("Hi.")
    assert calls[0][1]["headers"]["Authorization"] == "Bearer envkey"


# -- OpenAI custom endpoint ----------------------------------------------------------------


@pytest.mark.parametrize(("url", "expected"), [
    ("https://api.openai.com/v1", "https://api.openai.com/v1"),
    ("https://gateway.example.com/openai/v1/", "https://gateway.example.com/openai/v1"),
    ("http://localhost:8080/v1", "http://localhost:8080/v1"),
    ("http://127.0.0.1:4000", "http://127.0.0.1:4000"),
])
def test_base_url_accepted(url, expected):
    from lyssna.providers.openai import check_base_url

    assert check_base_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://gateway.example.com/v1", "ftp://gateway.example.com", "gateway.example.com/v1", "https://", "",
    "http://localhost.evil.com/v1", "https://[bad",
])
def test_base_url_rejected(url):
    from lyssna.providers.openai import check_base_url

    with pytest.raises(ProviderError, match="must be an https URL"):
        check_base_url(url)


def test_openai_uses_the_custom_endpoint(calls):
    calls.respond = lambda url, kw: ok_response("openai", url)
    get_provider("openai", {"openai": {"api_key": "k", "base_url": "https://gateway.example.com/v1/"}}).synthesize("Hi.")
    url, kw = calls[0]
    assert url == "https://gateway.example.com/v1/audio/speech"
    assert kw["timeout"] == 90 and len(calls) == 1


def test_openai_refuses_a_plain_http_endpoint_before_any_request(calls):
    p = get_provider("openai", {"openai": {"api_key": "k", "base_url": "http://gateway.example.com/v1"}})
    with pytest.raises(ProviderError, match="https"):
        p.synthesize("Hi.")
    assert calls == []


@pytest.mark.parametrize(("base_url", "line"), [
    (None, "Text is sent to OpenAI"),
    ("https://api.openai.com/v1/", "Text is sent to OpenAI"),
    ("https://gateway.example.com/v1", "Text is sent to gateway.example.com"),
])
def test_privacy_line_names_the_endpoint_host(fake_keychain, base_url, line):
    from lyssna import config
    from lyssna.controller import Controller

    fake_keychain.items["openai"] = "k"
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE)
    c = Controller()
    c.cfg = config.update(provider="openai", **({"openai.base_url": base_url} if base_url else {}))
    assert c.privacy_note() == line
