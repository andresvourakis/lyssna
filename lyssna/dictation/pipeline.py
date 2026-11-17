"""Recorded samples in, the text to paste out. One run, in order:

1. the speech engine transcribes the samples (with the vocabulary as hints, if it takes hints);
2. the vocabulary corrects misheard terms in the transcript;
3. the cleanup engine cleans it, on a worker thread, within its budget (2.5 s on this Mac, 4 s
   online, plus about a second per 100 words: see cleanup_budget); past the budget the pipeline
   stops waiting;
4. the guard checks the cleaned text against what the cleaner was given; if it passes it is
   used, otherwise (or with no cleaner, no output, or a timeout) the rules-only text is;
5. the vocabulary corrects the result once more (a model may change a term's casing), and the
   terms it contains are counted.

Local-only mode is enforced here as well as in the menus: an engine that would send audio or
text off this Mac is never called while it is on, whoever asked for it. The log gets one line
per dictation with durations, word counts, engine names and the guard code; never text."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .. import config as config_mod
from .. import policy
from ..providers.base import error_summary
from . import cleaners, transcribers
from .cleaners import guard, rules
from .cleaners.base import Cleaner
from .transcribers.base import SAMPLE_RATE, Transcriber, TranscriberError
from .types import DictationError, DictationResult
from .vocabulary import Vocabulary, shared

log = logging.getLogger("lyssna.dictation")

SILENCE_PEAK = 0.003  # below this peak level (about -50 dBFS) a recording is treated as silence
MIN_SECONDS = 0.1
CLEANUP_SECONDS_PER_WORD = 0.01  # extra cleanup time per word: about 1 s per 100 words


def _dictation(cfg: dict) -> dict:
    section = cfg.get("dictation")
    return section if isinstance(section, dict) else {}


def is_silent(samples) -> bool:
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32)
    return audio.size < MIN_SECONDS * SAMPLE_RATE or float(np.max(np.abs(audio))) < SILENCE_PEAK


def cleanup_budget(cleaner: Cleaner, text: str) -> float:
    """How long to wait for `cleaner` on `text`: its base budget (2.5 s on this Mac, 4 s online)
    plus a per-word allowance. A cleaner's time grows with the length of what it writes, so a
    flat budget would send every long dictation back to the rules text just for being long."""
    return cleaner.budget_s + CLEANUP_SECONDS_PER_WORD * len(text.split())


def resample(samples, sample_rate: int):
    """Mono float32 at 16 kHz (linear interpolation from another rate)."""
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if sample_rate == SAMPLE_RATE or audio.size == 0:
        return audio
    n = round(audio.size * SAMPLE_RATE / sample_rate)
    return np.interp(np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio).astype(np.float32)


class DictationPipeline:
    """See the module docstring. `cfg` is a config dict, a function returning one (read on
    every run, so menu changes apply at once), or None for the config file. `transcriber`,
    `cleaner` and `cleanup` override the config (the command line uses them)."""

    def __init__(self, cfg: dict | Callable[[], dict] | None = None, *, vocabulary: Vocabulary | None = None,
                 transcriber: str | Transcriber | None = None, cleaner: str | Cleaner | None = None,
                 cleanup: bool | None = None):
        self._cfg = cfg
        self._vocabulary = vocabulary
        self._transcriber = transcriber
        self._cleaner = cleaner
        self._cleanup = cleanup

    @property
    def vocabulary(self) -> Vocabulary:
        return self._vocabulary or shared()

    def config(self) -> dict:
        if callable(self._cfg):
            return self._cfg()
        return self._cfg if isinstance(self._cfg, dict) else config_mod.load()

    # engines

    def transcriber(self, cfg: dict) -> Transcriber:
        chosen = self._transcriber
        if isinstance(chosen, Transcriber):
            engine = chosen
        else:
            name = chosen or transcribers.effective_transcriber(cfg)
            if not name:
                raise DictationError("Dictation needs a speech engine, and none can run in this install.")
            engine = transcribers.get_transcriber(name, cfg)
        if engine.sends_audio_off_device and policy.local_only(cfg):
            raise DictationError(transcribers.refusal(engine.label))
        return engine

    def cleaner(self, cfg: dict) -> Cleaner | None:
        on = self._cleanup if self._cleanup is not None else cleaners.cleanup_on(cfg)
        if not on:
            return None
        chosen = self._cleaner
        if isinstance(chosen, Cleaner):
            engine = chosen
        else:
            name = chosen or cleaners.effective_cleaner(cfg)
            if not name:
                return None
            engine = cleaners.get_cleaner(name, cfg)
        if engine.sends_text_off_device and policy.local_only(cfg):
            raise DictationError(cleaners.refusal(engine.label))
        return engine

    def warm_up(self) -> None:
        """Called when a recording starts: let the engines start loading. Returns at once."""
        try:
            cfg = self.config()
            self.transcriber(cfg).warm_up()
            cleaner = self.cleaner(cfg)
            if cleaner is not None:
                cleaner.warm_up()
        except Exception as e:  # a warm-up problem shows up again, with a message, in run()
            log.info("dictation warm-up skipped: %s", error_summary(e))

    # one dictation

    def run(self, samples, sample_rate: int = SAMPLE_RATE) -> DictationResult:
        started = time.monotonic()
        cfg = self.config()
        transcriber = self.transcriber(cfg)
        cleaner = self.cleaner(cfg)
        cleaner_name = cleaner.name if cleaner is not None else cleaners.RULES
        audio = resample(samples, sample_rate)
        seconds_audio = audio.size / SAMPLE_RATE
        timings = {"transcribe": 0.0, "clean": 0.0, "total": 0.0}

        def result(text: str, raw: str, source: str) -> DictationResult:
            timings["total"] = round(time.monotonic() - started, 3)
            words = len(text.split())
            log.info("dictation: %.1fs of audio, transcribed by %s in %.2fs, cleaned by %s in %.2fs (%s), %d words, "
                     "%.2fs in all", seconds_audio, transcriber.name, timings["transcribe"], cleaner_name,
                     timings["clean"], source, words, timings["total"])
            return DictationResult(text=text, raw=raw, source=source, transcriber=transcriber.name,
                                   cleaner=cleaner_name, audio_seconds=round(seconds_audio, 3), seconds=timings,
                                   words=words)

        if is_silent(audio):
            return result("", "", "empty")

        vocab = self.vocabulary
        language = str(_dictation(cfg).get("language") or "en")
        hints = vocab.hints() if transcriber.supports_hints else []
        t = time.monotonic()
        try:
            raw = transcriber.transcribe(audio, language, hints)
        except TranscriberError as e:
            log.warning("dictation failed in %s: %s", transcriber.name, error_summary(e))
            raise DictationError(str(e)) from e
        except Exception as e:
            log.warning("dictation failed in %s: %s", transcriber.name, error_summary(e))
            raise DictationError("The speech engine failed. Try again.") from e
        timings["transcribe"] = round(time.monotonic() - t, 3)
        if not raw.strip():
            return result("", raw, "empty")

        text = vocab.correct(raw)
        terms = vocab.terms()
        if cleaner is None:
            final, source = rules.light_clean(text), "rules:none"
        else:
            t = time.monotonic()
            cleaned, timed_out = clean_within(cleaner, text, terms, cleanup_budget(cleaner, text))
            timings["clean"] = round(time.monotonic() - t, 3)
            if timed_out:
                final, source = rules.light_clean(text), "rules:timeout"
            elif cleaned is None:
                final, source = rules.light_clean(text), "rules:no_output"
            else:
                verdict = guard.check(text, cleaned, terms)
                if verdict.ok:
                    final, source = cleaned.strip(), "model"
                else:
                    final, source = rules.light_clean(text), f"rules:{verdict.reason}"
        final = vocab.correct(final)
        vocab.used(final)
        return result(final, raw, source)


def clean_within(cleaner: Cleaner, text: str, terms: list[str], budget_s: float) -> tuple[str | None, bool]:
    """Run the cleaner on a worker thread and wait at most `budget_s`. Returns (output, timed
    out). A cleaner still running at the budget finishes on its own and its output is dropped."""
    box: dict[str, str | None] = {}
    done = threading.Event()

    def work() -> None:
        try:
            box["out"] = cleaner.clean(text, terms, budget_s)
        except Exception as e:  # cleaners should not raise; if one does, it is a cleanup that did not happen
            log.warning("cleanup via %s failed: %s", cleaner.name, error_summary(e))
            box["out"] = None
        finally:
            done.set()

    threading.Thread(target=work, daemon=True, name=f"lyssna-cleanup-{cleaner.name}").start()
    if not done.wait(budget_s):
        log.info("cleanup via %s missed its %.1fs budget; using the rules text", cleaner.name, budget_s)
        return None, True
    return box.get("out"), False
