"""Dictation cost in the monthly usage estimate, next to the voice usage. USAGE_PATH is under
the per-test temp dir (conftest)."""

import json

import pytest

from lyssna import usage

PRICES = {"kokoro": 0.0, "inworld": 25.0}


def test_free_dictation_is_not_recorded():
    usage.record_dictation("parakeet", audio_seconds=30, usd=0.0)
    assert usage.dictation_this_month() == {} and not usage.USAGE_PATH.exists()


def test_dictation_adds_up_per_engine():
    usage.record_dictation("openai", audio_seconds=60, usd=0.0045)
    usage.record_dictation("openai", audio_seconds=30, usd=0.00225)
    usage.record_dictation("haiku", tokens_in=1200, tokens_out=40, usd=0.0001)
    d = usage.dictation_this_month()
    assert d["audio_seconds"] == 90 and d["usd"] == pytest.approx(0.00685)
    assert d["engines"]["openai"]["audio_seconds"] == 90
    assert d["engines"]["haiku"]["tokens_in"] == 1200 and d["engines"]["haiku"]["tokens_out"] == 40


def test_voice_counts_ignore_the_dictation_entry():
    usage.record("inworld", 1000, 25.0)
    usage.record_dictation("openai", audio_seconds=60, usd=0.01)
    assert usage.this_month() == {"inworld": 1000}
    month = json.loads(usage.USAGE_PATH.read_text())[usage._month()]
    assert set(month) == {"inworld", usage.DICTATION_KEY}


def test_summary_with_dictation_only():
    usage.record_dictation("openai", audio_seconds=25 * 60, usd=0.1125)
    assert usage.summary(PRICES) == "This month: 25 minutes of dictation, about $0.11"


def test_summary_with_both():
    usage.record("inworld", 40_000, 25.0)
    usage.record_dictation("inworld", audio_seconds=50, usd=0.002)
    assert usage.summary(PRICES) == "This month: 40k characters and 1 minute of dictation, about $1.00"


def test_summary_with_cloud_cleanup_of_local_speech():
    usage.record_dictation("haiku", tokens_in=50_000, tokens_out=2_000, usd=0.006)
    assert usage.summary(PRICES) == "This month: dictation cleanup, about $0.01"


def test_dictation_months_are_trimmed_with_the_rest():
    old = {f"2025-{m:02d}": {usage.DICTATION_KEY: {"usd": 1.0, "audio_seconds": 60}} for m in range(1, 13)}
    usage.USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage.USAGE_PATH.write_text(json.dumps(old))
    usage.record_dictation("openai", audio_seconds=1, usd=0.001)
    assert len(json.loads(usage.USAGE_PATH.read_text())) == usage.KEEP_MONTHS
