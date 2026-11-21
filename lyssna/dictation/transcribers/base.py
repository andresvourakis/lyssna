"""The speech-to-text engine interface, shaped like the voice engines in
lyssna/providers/base.py so the menu and the setup window can treat both alike.

Audio arrives as a numpy array of mono float32 samples at 16 kHz and stays in memory: an
engine on this Mac reads the array, an online engine encodes it as a WAV in memory and sends
it over HTTPS. Nothing here writes audio to disk or logs it, nor the transcript."""

from __future__ import annotations

import io
import logging
import os
import time
import wave
from collections.abc import Callable, Sequence

import httpx

from ... import host, keystore
from ...providers.base import error_summary

log = logging.getLogger("lyssna.dictation")

SAMPLE_RATE = 16000
TIMEOUT = 30.0  # seconds for one transcription request; no retries


class TranscriberError(RuntimeError):
    """A failed transcription. The message is for the on-screen notice and may quote the
    provider's response, so it is never logged; `status` is the HTTP status when there was one."""

    def __init__(self, message: str = "", status: int | None = None):
        super().__init__(message)
        self.status = status


class Transcriber:
    """One speech-to-text engine. Subclasses set the class attributes and implement transcribe()."""

    name = "base"
    label = "Base"
    needs_key = True
    setup_hint = "no API key"  # shown in the menu when configured() is False
    config_section = ""  # config table + env var prefix to read; defaults to `name`
    vendor = ""  # company name used in error messages
    key_url = ""  # https page where the user gets an API key
    sends_audio_off_device = True
    price_per_hour = 0.0  # USD list price per hour of audio, for the usage estimate only
    supports_hints = False  # takes a list of vocabulary terms to listen for
    languages: tuple[str, ...] = ()  # language codes it handles; empty = many, detected by the service
    budget_s = TIMEOUT

    @classmethod
    def section(cls) -> str:
        return cls.config_section or cls.name

    def __init__(self, cfg: dict | None = None, api_key: str = "", language: str = "en"):
        self.cfg = cfg or {}
        self._key_override = api_key
        self.language = language or "en"  # the [dictation] language, for engines that pick a model by it

    @property
    def api_key(self) -> str:
        """Keychain first, then the config file, then the env var (as for the voices)."""
        if not self.needs_key:
            return ""
        return (
            self._key_override
            or keystore.get(self.section())
            or self.cfg.get("api_key")
            or os.environ.get(f"{self.section().upper()}_API_KEY", "")
        )

    @classmethod
    def can_run(cls) -> bool:
        """Whether this engine can work at all in this install. Must be cheap."""
        return True

    def configured(self, wait: bool = True) -> bool | None:
        """Set up to transcribe. With wait=False it never waits for the Keychain and answers
        None while the Keychain is not read yet (as for the voices)."""
        if not self.needs_key:
            return True
        if not wait:
            return keystore.has_key(self.section(), self._key_override, self.cfg.get("api_key"),
                                    os.environ.get(f"{self.section().upper()}_API_KEY", ""))
        return bool(self.api_key)

    def prepare(self, on_error: Callable[[str], None] | None = None) -> None:
        """Called when the user picks this engine: start any one-time setup in the background."""

    def warm_up(self) -> None:
        """Called when a recording starts. Must return at once; may start a background load."""

    def ready(self, timeout: float | None = None) -> bool:
        """Wait (up to `timeout` seconds) until a warm-up started earlier has finished."""
        return True

    def recipient(self) -> str:
        """Who receives the audio when this engine transcribes it, for the privacy line."""
        return self.vendor or self.label

    def check_key(self) -> str:
        """Prove the key works with one request that costs nothing. "" when it works, else a
        short message for the user. Engines without such a request say so."""
        return f"Lyssna can't check a {self.vendor or self.label} key here yet."

    def transcribe(self, samples, language: str = "en", hints: Sequence[str] = ()) -> str:
        raise NotImplementedError

    # helpers for online engines

    def _require_key(self) -> str:
        key = self.api_key
        if not key:
            raise TranscriberError(
                f"No API key for {self.label}. Add one from {host.MENU_PLACE} (Settings > API Keys), "
                f"or set {self.section().upper()}_API_KEY."
            )
        return key

    def _post(self, url: str, audio_seconds: float, **kwargs) -> httpx.Response:
        """POST to the engine's API and return the 200 response; anything else becomes a
        TranscriberError. Deliberately no retries: a retry re-sends and re-bills the audio.
        Logs one line per call with the service, seconds, audio seconds and status only."""
        start = time.monotonic()
        try:
            r = httpx.post(url, timeout=TIMEOUT, **kwargs)
        except httpx.HTTPError as e:
            log.warning("transcription via %s failed after %.2fs (%.1fs of audio): %s",
                        self.name, time.monotonic() - start, audio_seconds, error_summary(e))
            raise TranscriberError(f"{self.vendor} could not be reached. Check your internet connection.") from e
        elapsed = time.monotonic() - start
        if r.status_code != 200:
            log.warning("transcription via %s failed after %.2fs (%.1fs of audio): HTTP %s, %d bytes of detail not logged",
                        self.name, elapsed, audio_seconds, r.status_code, len(r.content))
            raise TranscriberError(f"{self.vendor} error {r.status_code}: {r.text[:300]}", status=r.status_code)
        log.info("transcribed %.1fs of audio via %s in %.2fs (HTTP 200)", audio_seconds, self.name, elapsed)
        return r


KEY_CHECK_TIMEOUT = 15.0  # seconds for the free request that proves a key works; no retries


def check_key_request(name: str, vendor: str, url: str, headers: dict) -> str:
    """One authenticated GET that costs nothing (a list of models) to prove a key works.
    "" on HTTP 200, else a short message for the user. No retries, a timeout, and one log line
    with the status only: never the key or the response body."""
    start = time.monotonic()
    try:
        r = httpx.get(url, headers=headers, timeout=KEY_CHECK_TIMEOUT)
    except httpx.HTTPError as e:
        log.warning("key check for %s failed after %.2fs: %s", name, time.monotonic() - start, error_summary(e))
        return f"{vendor} could not be reached. Check your internet connection."
    log.info("key check for %s: HTTP %s in %.2fs", name, r.status_code, time.monotonic() - start)
    if r.status_code == 200:
        return ""
    if r.status_code in (401, 403):
        return f"{vendor} did not accept that key. Copy it again."
    return f"{vendor} answered with error {r.status_code}. Try again in a moment."


def audio_seconds(samples, sample_rate: int = SAMPLE_RATE) -> float:
    return len(samples) / float(sample_rate) if sample_rate else 0.0


def check_samples(samples) -> None:
    """Engines take audio as an in-memory array only, never a path to a file."""
    if isinstance(samples, (str, bytes, os.PathLike)):
        raise TypeError("transcribe() takes the audio samples themselves, not a file")


def wav_bytes(samples, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Mono float32 samples as a 16-bit PCM WAV, built in memory."""
    import numpy as np

    pcm16 = np.clip(np.asarray(samples, dtype=np.float32) * 32767, -32768, 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm16)
    return buf.getvalue()
