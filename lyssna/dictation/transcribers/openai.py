"""OpenAI gpt-transcribe: $0.27 per audio hour, keeps no data, takes the vocabulary as
`keywords[]`. Uses the same key and `base_url` as the OpenAI voice ([openai] in the config),
so a company gateway set there is used for dictation too (https only)."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from ... import usage
from ...providers.base import ProviderError
from ...providers.openai import DEFAULT_BASE_URL, check_base_url
from .base import Transcriber, TranscriberError, audio_seconds, check_samples, wav_bytes

MAX_KEYWORDS = 100


class OpenAITranscriber(Transcriber):
    name = "openai"
    label = "OpenAI (gpt-transcribe)"
    config_section = "openai"
    vendor = "OpenAI"
    key_url = "https://platform.openai.com/api-keys"
    price_per_hour = 0.27
    supports_hints = True
    default_model = "gpt-transcribe"

    @property
    def model(self) -> str:
        return self.cfg.get("transcribe_model") or self.default_model

    @property
    def base_url(self) -> str:
        try:
            return check_base_url(self.cfg.get("base_url") or DEFAULT_BASE_URL)
        except ProviderError as e:
            raise TranscriberError(str(e)) from e

    def recipient(self) -> str:
        """OpenAI, or the host of the custom endpoint the audio actually goes to."""
        raw = (self.cfg.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
        if raw == DEFAULT_BASE_URL:
            return self.vendor
        try:
            return urlsplit(raw).hostname or raw
        except ValueError:
            return raw

    def transcribe(self, samples, language: str = "", hints: Sequence[str] = ()) -> str:
        check_samples(samples)
        key = self._require_key()
        url = f"{self.base_url}/audio/transcriptions"
        seconds = audio_seconds(samples)
        # httpx takes repeated form fields as lists in a dict when `files` is also given.
        data: dict = {"model": self.model}
        language = (language or self.language or "").split("-")[0]
        if language:
            data["languages[]"] = [language]
        keywords = [h for h in hints if h.strip()][:MAX_KEYWORDS]
        if keywords:
            data["keywords[]"] = keywords
        r = self._post(url, seconds, headers={"Authorization": f"Bearer {key}"}, data=data,
                       files={"file": ("audio.wav", wav_bytes(samples), "audio/wav")})
        try:
            text = r.json()["text"]
        except (KeyError, ValueError, TypeError) as e:
            raise TranscriberError("OpenAI returned an unexpected response.") from e
        usage.record_dictation(self.name, audio_seconds=seconds, usd=seconds / 3600 * self.price_per_hour)
        return str(text or "").strip()
