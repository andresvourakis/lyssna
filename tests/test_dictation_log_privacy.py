"""Dictation logs carry counts, durations, engine names and guard codes, never the transcript,
the vocabulary terms, an API key or a provider's error body. Every line logged during whole
dictations (success, guard rejection, provider errors, a vocabulary correction learned) is
checked for all of them."""

import httpx
import numpy as np
import pytest

from lyssna.dictation import cleaners
from lyssna.dictation.pipeline import DictationPipeline
from lyssna.dictation.transcribers import get_transcriber
from lyssna.dictation.types import DictationError
from lyssna.dictation.vocabulary import Vocabulary

SECRET_WORDS = ("aubergine", "Zanzibar", "Quillfeather")
TRANSCRIPT = "um please send the aubergine invoice to Zanzibar today it is not paid"
KEY = "sk-test-SUPERSECRETKEY123"
ERROR_BODY = '{"error": {"message": "invalid request near aubergine BODYMARKER"}}'
SAMPLES = (0.2 * np.sin(np.arange(16000) / 5)).astype(np.float32)


def responder(status_openai=200, status_haiku=200, cleaned="Please send the aubergine invoice to Zanzibar today. It is not paid."):
    def post(url, **kwargs):
        req = httpx.Request("POST", url)
        if "openai" in url:
            if status_openai != 200:
                return httpx.Response(status_openai, text=ERROR_BODY, request=req)
            return httpx.Response(200, json={"text": TRANSCRIPT}, request=req)
        if status_haiku != 200:
            return httpx.Response(status_haiku, text=ERROR_BODY, request=req)
        return httpx.Response(200, request=req, json={
            "content": [{"type": "text", "text": cleaned}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 30, "output_tokens": 12, "cache_read_input_tokens": 1100}})
    return post


def run(monkeypatch, caplog, **kwargs):
    monkeypatch.setattr(httpx, "post", responder(**kwargs))
    vocab = Vocabulary()
    vocab.add("Quillfeather")
    vocab.learn("quilfether", "Quillfeather")
    cfg = {"openai": {"api_key": KEY}, "anthropic": {"api_key": KEY}, "dictation": {"cleanup": True}}
    pipe = DictationPipeline(cfg, vocabulary=vocab, transcriber=get_transcriber("openai", cfg),
                             cleaner=cleaners.get_cleaner("haiku", cfg))
    with caplog.at_level("DEBUG"):
        try:
            return pipe.run(SAMPLES)
        except DictationError:
            return None


def assert_private(caplog):
    text = "\n".join(r.getMessage() for r in caplog.records) + "\n".join(
        str(r.exc_text or "") for r in caplog.records)
    assert caplog.records  # something was logged
    for secret in (*SECRET_WORDS, KEY, "BODYMARKER", "invalid request"):
        assert secret not in text, secret


def test_successful_dictation_logs_no_text(monkeypatch, caplog):
    result = run(monkeypatch, caplog)
    assert result is not None and result.source == "model"
    assert_private(caplog)
    assert any("dictation:" in r.getMessage() and "words" in r.getMessage() for r in caplog.records)


def test_guard_rejection_logs_only_the_code(monkeypatch, caplog):
    result = run(monkeypatch, caplog, cleaned="Please send the aubergine invoice to Zanzibar today, it is paid.")
    assert result.source == "rules:negation"
    assert_private(caplog)


@pytest.mark.parametrize("status", [400, 401, 500])
def test_transcription_error_logs_no_body(monkeypatch, caplog, status):
    assert run(monkeypatch, caplog, status_openai=status) is None
    assert_private(caplog)
    assert any(f"HTTP {status}" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("status", [400, 401, 529])
def test_cleanup_error_logs_no_body(monkeypatch, caplog, status):
    result = run(monkeypatch, caplog, status_haiku=status)
    assert result.source == "rules:no_output"
    assert_private(caplog)


def test_learning_a_term_logs_no_term(caplog):
    with caplog.at_level("DEBUG"):
        Vocabulary().learn("zanzibarr", "Zanzibar")
    assert_private(caplog)
