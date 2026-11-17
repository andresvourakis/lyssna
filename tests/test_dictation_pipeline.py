"""The dictation pipeline with fake engines: the order of the steps, the cleanup budget and its
timeout fallback, empty audio, local-only refusal, and hints only for engines that take them.
No model loads and nothing goes online (conftest)."""

import threading
import time

import numpy as np
import pytest

from lyssna.dictation import cleaners, transcribers
from lyssna.dictation.cleaners import Cleaner
from lyssna.dictation.pipeline import DictationPipeline, clean_within, cleanup_budget, resample
from lyssna.dictation.transcribers import Transcriber, TranscriberError
from lyssna.dictation.types import DictationError
from lyssna.dictation.vocabulary import Vocabulary


def speech(seconds=1.0, rate=16000):
    t = np.arange(int(seconds * rate)) / rate
    return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


class FakeTranscriber(Transcriber):
    name = "fake-stt"
    label = "Fake speech"
    needs_key = False
    sends_audio_off_device = False

    def __init__(self, text="", supports_hints=False, error=None, off_device=False):
        super().__init__({})
        self.text, self.error, self.calls = text, error, []
        self.supports_hints = supports_hints
        self.sends_audio_off_device = off_device

    def transcribe(self, samples, language="en", hints=()):
        self.calls.append({"n": len(samples), "language": language, "hints": list(hints)})
        if self.error:
            raise self.error
        return self.text


class FakeCleaner(Cleaner):
    name = "fake-clean"
    label = "Fake cleanup"
    needs_key = False
    sends_text_off_device = False
    budget_s = 1.0

    def __init__(self, output=None, delay=0.0, off_device=False, raises=None):
        super().__init__({})
        self.output, self.delay, self.calls, self.raises = output, delay, [], raises
        self.sends_text_off_device = off_device

    def clean(self, raw, terms=(), budget_s=0.0):
        self.calls.append({"raw": raw, "terms": list(terms), "budget": budget_s})
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.output(raw) if callable(self.output) else self.output


@pytest.fixture
def vocab():
    v = Vocabulary()
    v.add("Lyssna")
    v.learn("listna", "Lyssna")
    v.add("Kokoro")
    return v


def pipeline(t, c=None, cfg=None, vocab=None, cleanup=None):
    cfg = cfg if cfg is not None else {"dictation": {"language": "en", "cleanup": True}}
    return DictationPipeline(cfg, vocabulary=vocab or Vocabulary(), transcriber=t, cleaner=c if c is not None else "",
                             cleanup=cleanup if cleanup is not None else c is not None)


def test_steps_run_in_order(vocab):
    t = FakeTranscriber("um so i use listna every day")
    c = FakeCleaner(lambda raw: "So I use lyssna every day.")
    result = pipeline(t, c, vocab=vocab).run(speech())
    # the cleaner got the transcript with the vocabulary already applied, and the terms
    assert c.calls[0]["raw"] == "um so i use Lyssna every day"
    assert set(c.calls[0]["terms"]) == {"Lyssna", "Kokoro"}
    assert c.calls[0]["budget"] == pytest.approx(c.budget_s + 7 * 0.01)  # base plus the per-word allowance
    # the model's output passed the guard and the vocabulary fixed its casing again
    assert result.text == "So I use Lyssna every day."
    assert result.raw == "um so i use listna every day"
    assert result.source == "model"
    assert (result.transcriber, result.cleaner, result.words) == ("fake-stt", "fake-clean", 6)
    assert result.audio_seconds == 1.0 and set(result.seconds) == {"transcribe", "clean", "total"}
    assert {e.term: e.uses for e in vocab.entries()}["Lyssna"] == 1  # counted as used


def test_guard_rejection_falls_back_to_rules():
    t = FakeTranscriber("i have not tested it yet")
    c = FakeCleaner("I have tested it yet.")
    result = pipeline(t, c).run(speech())
    assert result.text == "I have not tested it yet." and result.source == "rules:negation"


def test_cleaner_past_its_budget_falls_back_to_rules():
    t = FakeTranscriber("um send the report by friday")
    c = FakeCleaner("Send the report by Friday.", delay=0.5)
    c.budget_s = 0.05
    start = time.monotonic()
    result = pipeline(t, c).run(speech())
    assert time.monotonic() - start < 0.4  # it stopped waiting at the budget
    assert result.text == "Send the report by friday." and result.source == "rules:timeout"


def test_cleaner_without_output_or_crashing_falls_back():
    t = FakeTranscriber("send the report by friday")
    assert pipeline(t, FakeCleaner(None)).run(speech()).source == "rules:no_output"
    assert pipeline(t, FakeCleaner(raises=RuntimeError("boom"))).run(speech()).source == "rules:no_output"


def test_cleanup_off_uses_rules_and_skips_the_cleaner():
    t = FakeTranscriber("um send it")
    c = FakeCleaner("Send it.")
    result = pipeline(t, c, cleanup=False).run(speech())
    assert result.source == "rules:none" and result.cleaner == "rules" and c.calls == []
    assert result.text == "Send it"


@pytest.mark.parametrize("samples", [np.zeros(0, np.float32), np.zeros(16000, np.float32),
                                     np.full(16000, 0.001, np.float32), speech(0.05)])
def test_empty_or_silent_audio_gives_empty_text(samples):
    t = FakeTranscriber("should not be called")
    result = pipeline(t, FakeCleaner("x")).run(samples)
    assert (result.text, result.source, result.words) == ("", "empty", 0)
    assert t.calls == []


def test_empty_transcript_gives_empty_text():
    c = FakeCleaner("x")
    result = pipeline(FakeTranscriber("   "), c).run(speech())
    assert (result.text, result.source) == ("", "empty") and c.calls == []


def test_transcriber_failure_raises_dictation_error():
    t = FakeTranscriber(error=TranscriberError("Inworld error 401: bad key", status=401))
    with pytest.raises(DictationError, match="401"):
        pipeline(t).run(speech())


def test_hints_only_for_engines_that_take_them(vocab):
    with_hints = FakeTranscriber("hello there", supports_hints=True)
    without = FakeTranscriber("hello there", supports_hints=False)
    pipeline(with_hints, vocab=vocab).run(speech())
    pipeline(without, vocab=vocab).run(speech())
    assert set(with_hints.calls[0]["hints"]) == {"Lyssna", "Kokoro"}
    assert without.calls[0]["hints"] == []


def test_language_comes_from_the_config():
    t = FakeTranscriber("hola")
    pipeline(t, cfg={"dictation": {"language": "es"}}).run(speech())
    assert t.calls[0]["language"] == "es"


def test_other_sample_rates_are_resampled():
    t = FakeTranscriber("hi there")
    result = pipeline(t).run(speech(1.0, 48000), 48000)
    assert t.calls[0]["n"] == 16000 and result.audio_seconds == 1.0
    assert resample(np.ones(480, np.float32), 48000).shape == (160,)


# -- local-only mode ------------------------------------------------------------------------------

LOCAL_ONLY = {"local_only": True, "dictation": {"cleanup": True}}


def test_local_only_refuses_an_online_transcriber_before_calling_it():
    t = FakeTranscriber("secret", off_device=True)
    with pytest.raises(DictationError, match="local-only"):
        pipeline(t, cfg=LOCAL_ONLY).run(speech())
    assert t.calls == []


def test_local_only_refuses_an_online_cleaner_before_calling_it():
    c = FakeCleaner("x", off_device=True)
    with pytest.raises(DictationError, match="local-only"):
        pipeline(FakeTranscriber("hi"), c, cfg=LOCAL_ONLY).run(speech())
    assert c.calls == []


def test_managed_local_only_refuses_too(managed_prefs):
    managed_prefs["LocalOnly"] = True
    t = FakeTranscriber("secret", off_device=True)
    with pytest.raises(DictationError, match="organization"):
        pipeline(t, cfg={"dictation": {}}).run(speech())


def test_online_engines_named_in_the_config_fall_back_in_local_only(monkeypatch, fake_keychain):
    fake_keychain.items.update({"openai": "k", "anthropic": "k"})
    monkeypatch.setattr(transcribers.ParakeetTranscriber, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(cleaners.S1MiniCleaner, "can_run", classmethod(lambda cls: True))
    cfg = {"dictation": {"transcriber": "openai", "cleaner": "haiku", "cleanup": True}}
    assert transcribers.effective_transcriber(cfg) == "openai"
    assert cleaners.effective_cleaner(cfg) == "haiku"
    cfg["local_only"] = True
    assert transcribers.effective_transcriber(cfg) == "parakeet"
    assert cleaners.effective_cleaner(cfg) == "s1-mini"
    assert not transcribers.allowed("openai", cfg) and not transcribers.allowed("inworld", cfg)
    assert transcribers.allowed("parakeet", cfg)
    assert not cleaners.allowed("haiku", cfg) and cleaners.allowed("s1-mini", cfg)


def test_online_engines_without_a_key_fall_back(monkeypatch):
    monkeypatch.setattr(transcribers.ParakeetTranscriber, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(cleaners.S1MiniCleaner, "can_run", classmethod(lambda cls: False))
    cfg = {"dictation": {"transcriber": "inworld", "cleaner": "haiku", "cleanup": True}}
    assert transcribers.effective_transcriber(cfg) == "parakeet"
    assert cleaners.effective_cleaner(cfg) == ""  # no S1-mini here (an Intel Mac): rules only


def test_cleanup_off_means_no_cleaner():
    assert cleaners.effective_cleaner({"dictation": {"cleanup": False, "cleaner": "s1-mini"}}) == ""


def test_every_online_engine_is_marked():
    assert {n for n, t in transcribers.REGISTRY.items() if t.sends_audio_off_device} == {"openai", "inworld"}
    assert {n for n, c in cleaners.REGISTRY.items() if c.sends_text_off_device} == {"haiku"}


# -- the budget helper and warm-up -----------------------------------------------------------------


def test_cleanup_budget_grows_with_the_length_of_the_text():
    local, cloud = FakeCleaner(), FakeCleaner(off_device=True)
    local.budget_s, cloud.budget_s = 2.5, 4.0
    assert cleanup_budget(local, "") == 2.5
    assert cleanup_budget(cloud, "") == 4.0
    hundred = " ".join(["word"] * 100)
    assert cleanup_budget(local, hundred) == pytest.approx(3.5)
    assert cleanup_budget(cloud, " ".join([hundred] * 3)) == pytest.approx(7.0)
    assert cleanup_budget(local, "  a   few\nwords  ") == pytest.approx(2.53)  # words, not characters


def test_clean_within_returns_early_and_leaves_the_worker_running():
    finished = threading.Event()

    class Slow(FakeCleaner):
        def clean(self, raw, terms=(), budget_s=0.0):
            time.sleep(0.2)
            finished.set()
            return "late"

    assert clean_within(Slow(), "text", [], 0.02) == (None, True)
    assert finished.wait(1.0)
    assert clean_within(FakeCleaner("ok"), "text", [], 1.0) == ("ok", False)


def test_warm_up_starts_both_engines_and_never_raises():
    calls = []

    class T(FakeTranscriber):
        def warm_up(self):
            calls.append("t")

    class C(FakeCleaner):
        def warm_up(self):
            calls.append("c")

    pipeline(T("x"), C("x")).warm_up()
    assert calls == ["t", "c"]
    pipeline(FakeTranscriber(off_device=True), cfg=LOCAL_ONLY).warm_up()  # refused, but quietly
