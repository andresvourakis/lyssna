"""The online dictation engines against a fake HTTP layer: request shapes (URL, headers, body),
one call per dictation (no retries), timeouts, output caps, and what counts as no usable
output. Nothing here reaches the network or a paid API."""

import base64
import io
import json
import wave

import httpx
import numpy as np
import pytest

from lyssna import usage
from lyssna.dictation.cleaners import get_cleaner, haiku, prompt
from lyssna.dictation.transcribers import TranscriberError, get_transcriber
from lyssna.dictation.transcribers.inworld import prompt_terms

SAMPLES = (0.1 * np.sin(np.arange(16000) / 5)).astype(np.float32)  # one second


@pytest.fixture
def calls(monkeypatch):
    """Records every httpx.post; answers with `calls.respond(url, kwargs)`."""

    class Calls(list):
        respond = None

    rec = Calls()

    def fake_post(url, **kwargs):
        rec.append((url, kwargs))
        result = rec.respond(url, kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", fake_post)
    return rec


def response(status=200, **kwargs):
    return httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"), **kwargs)


def read_wav(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


# -- OpenAI gpt-transcribe ----------------------------------------------------------------------------


def openai(cfg=None):
    return get_transcriber("openai", {"openai": {"api_key": "k", **(cfg or {})}, "dictation": {"language": "en"}})


def test_openai_request_shape(calls):
    calls.respond = lambda url, kw: response(json={"text": " Hello there. ", "usage": {"seconds": 1}})
    text = openai().transcribe(SAMPLES, "en", ["Lyssna", "Claude Code"])
    assert text == "Hello there."
    assert len(calls) == 1
    url, kw = calls[0]
    assert url == "https://api.openai.com/v1/audio/transcriptions"
    assert kw["headers"] == {"Authorization": "Bearer k"}
    assert kw["timeout"] == 30.0
    assert kw["data"] == {"model": "gpt-transcribe", "languages[]": ["en"], "keywords[]": ["Lyssna", "Claude Code"]}
    name, wav, mime = kw["files"]["file"]
    assert (name, mime) == ("audio.wav", "audio/wav")
    assert read_wav(wav) == (1, 2, 16000, 16000)  # 16-bit mono 16 kHz, built in memory


def test_openai_model_and_gateway_from_the_config(calls):
    calls.respond = lambda url, kw: response(json={"text": "hi"})
    t = openai({"transcribe_model": "gpt-transcribe-mini", "base_url": "https://gateway.example.com/v1/"})
    t.transcribe(SAMPLES, "sv", [])
    url, kw = calls[0]
    assert url == "https://gateway.example.com/v1/audio/transcriptions"
    assert kw["data"] == {"model": "gpt-transcribe-mini", "languages[]": ["sv"]}
    assert t.recipient() == "gateway.example.com"


def test_openai_refuses_plain_http_without_calling(calls):
    with pytest.raises(TranscriberError, match="https"):
        openai({"base_url": "http://gateway.example.com/v1"}).transcribe(SAMPLES, "en", [])
    assert calls == []


def test_openai_error_status_and_no_retry(calls):
    calls.respond = lambda url, kw: response(500, text="server trouble")
    with pytest.raises(TranscriberError) as info:
        openai().transcribe(SAMPLES, "en", [])
    assert info.value.status == 500 and len(calls) == 1


def test_openai_network_failure(calls):
    calls.respond = lambda url, kw: httpx.ConnectTimeout("slow")
    with pytest.raises(TranscriberError, match="reached"):
        openai().transcribe(SAMPLES, "en", [])
    assert len(calls) == 1


def test_openai_without_key_never_calls(calls):
    with pytest.raises(TranscriberError, match="No API key"):
        get_transcriber("openai", {}).transcribe(SAMPLES, "en", [])
    assert calls == []


def test_transcribers_take_samples_not_files(calls):
    with pytest.raises(TypeError):
        openai().transcribe("/tmp/clip.wav", "en", [])
    assert calls == []


def test_paid_transcription_is_counted(calls):
    calls.respond = lambda url, kw: response(json={"text": "hi"})
    openai().transcribe(np.zeros(16000 * 60, np.float32) + 0.1, "en", [])
    d = usage.dictation_this_month()
    assert d["audio_seconds"] == 60.0 and d["usd"] == pytest.approx(0.27 / 60)


# -- Inworld STT -----------------------------------------------------------------------------------------


def test_inworld_request_shape(calls):
    calls.respond = lambda url, kw: response(json={"transcription": {"transcript": "Hello."}, "usage": None})
    t = get_transcriber("inworld", {"inworld": {"api_key": "k"}, "dictation": {"language": "en"}})
    assert t.supports_hints
    assert t.transcribe(SAMPLES, "en", ["Lyssna", "Claude Code"]) == "Hello."
    url, kw = calls[0]
    assert url == "https://api.inworld.ai/stt/v1/transcribe"
    assert kw["headers"] == {"Authorization": "Basic k", "Content-Type": "application/json"}
    assert kw["timeout"] == 30.0
    config = kw["json"]["transcribeConfig"]
    assert config == {"modelId": "inworld/inworld-stt-1", "audioEncoding": "LINEAR16", "sampleRateHertz": 16000,
                      "numberOfChannels": 1, "language": "en", "prompts": ["Lyssna", "Claude Code"]}
    assert read_wav(base64.b64decode(kw["json"]["audioData"]["content"])) == (1, 2, 16000, 16000)


def test_inworld_prompts_keep_only_allowed_characters():
    assert prompt_terms(["Lyssna", "R&D", "Mattias’s", "a_b", "C# code", "Dr. Who (2)", "  "]) == [
        "Lyssna", "Mattias's", "Dr. Who (2)"]
    assert len(prompt_terms([f"term{i}" for i in range(150)])) == 100


def test_inworld_unexpected_response(calls):
    calls.respond = lambda url, kw: response(json={"nothing": True})
    t = get_transcriber("inworld", {"inworld": {"api_key": "k"}})
    with pytest.raises(TranscriberError, match="unexpected"):
        t.transcribe(SAMPLES, "en", [])


# -- Claude Haiku 5.5 cleanup ---------------------------------------------------------------------------


def haiku_reply(text="Send the report by Friday.", stop="end_turn", **usage_fields):
    u = {"input_tokens": 40, "output_tokens": 9, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1100}
    u.update(usage_fields)
    return response(json={"content": [{"type": "text", "text": text}], "stop_reason": stop, "usage": u})


def cleaner(key="k"):
    return get_cleaner("haiku", {"anthropic": {"api_key": key} if key else {}})


def test_haiku_request_shape(calls):
    calls.respond = lambda url, kw: haiku_reply()
    out = cleaner().clean("um send the report by friday", ["Lyssna", "Kokoro"], 4.0)
    assert out == "Send the report by Friday."
    assert len(calls) == 1
    url, kw = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert kw["headers"] == {"x-api-key": "k", "anthropic-version": "2026-10-21", "content-type": "application/json"}
    assert kw["timeout"] == 10.0
    body = kw["json"]
    assert body["model"] == "claude-haiku-5-5"
    assert body["max_tokens"] == 2 * 6 + 64
    assert body["thinking"] == {"type": "disabled"}
    assert body["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k"} & set(body)  # no sampling parameters
    assert body["system"] == [{"type": "text", "text": prompt.SYSTEM}]


def test_haiku_messages_are_the_v3_examples_then_the_transcript(calls):
    calls.respond = lambda url, kw: haiku_reply()
    cleaner().clean("send it to lyssna", ["Lyssna", "Claude Code"], 4.0)
    messages = calls[0][1]["json"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"] * 7 + ["user"]
    for (transcript, reply), user, assistant in zip(prompt.EXAMPLES, messages[0:14:2], messages[1:14:2], strict=True):
        assert user["content"][0]["text"] == prompt.user_message(transcript)
        assert "<dictionary>" not in user["content"][0]["text"]  # the fixed prefix stays cacheable
        assert assistant["content"][0]["text"] == reply
    # one cache breakpoint, on the last block of the fixed prefix
    marked = [(i, b) for i, m in enumerate(messages) for b in m["content"] if "cache_control" in b]
    assert [(i, b["cache_control"]) for i, b in marked] == [(13, {"type": "ephemeral"})]
    assert messages[-1]["content"][0]["text"] == (
        "<dictionary>Lyssna, Claude Code</dictionary>\n<transcript>send it to lyssna</transcript>\n" + prompt.REMINDER)


def test_haiku_without_terms_has_no_dictionary_line(calls):
    calls.respond = lambda url, kw: haiku_reply()
    cleaner().clean("send it", [], 4.0)
    assert calls[0][1]["json"]["messages"][-1]["content"][0]["text"].startswith("<transcript>")


def test_haiku_output_cap_has_a_ceiling(calls):
    calls.respond = lambda url, kw: haiku_reply()
    cleaner().clean("word " * 2000, [], 4.0)
    assert calls[0][1]["json"]["max_tokens"] == 1024


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_haiku_refusal_or_cut_reply_is_no_output(calls, stop):
    calls.respond = lambda url, kw: haiku_reply(stop=stop)
    assert cleaner().clean("send it", [], 4.0) is None


@pytest.mark.parametrize("failure", [lambda: response(529, text="overloaded"), lambda: response(401, text="bad key"),
                                     lambda: httpx.ReadTimeout("slow"), lambda: response(200, text="not json")])
def test_haiku_failures_are_no_output_without_retry(calls, failure):
    calls.respond = lambda url, kw: failure()
    assert cleaner().clean("send it", [], 4.0) is None
    assert len(calls) == 1


def test_haiku_without_key_never_calls(calls):
    assert cleaner(key="").clean("send it", [], 4.0) is None
    assert calls == []


def test_haiku_logs_tokens_and_counts_the_cost(calls, caplog):
    calls.respond = lambda url, kw: haiku_reply(input_tokens=40, output_tokens=10, cache_creation_input_tokens=1000,
                                                cache_read_input_tokens=0)
    with caplog.at_level("INFO"):
        cleaner().clean("send it", [], 4.0)
    line = next(r.getMessage() for r in caplog.records if "cleaned via haiku" in r.getMessage())
    assert "40 in, 10 out, 1000 cache write, 0 cache read" in line and "HTTP 200" in line
    expected = (40 * haiku.PRICE_IN + 10 * haiku.PRICE_OUT + 1000 * haiku.PRICE_CACHE_WRITE) / 1e6
    assert usage.dictation_this_month()["usd"] == pytest.approx(expected)


def test_haiku_body_is_plain_json(calls):
    calls.respond = lambda url, kw: haiku_reply()
    cleaner().clean("send it", ["Lyssna"], 4.0)
    json.dumps(calls[0][1]["json"])  # serializable as sent
