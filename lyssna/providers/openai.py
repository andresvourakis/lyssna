from __future__ import annotations

from urllib.parse import urlsplit

from .base import Audio, Provider, ProviderError

DEFAULT_BASE_URL = "https://api.openai.com/v1"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def check_base_url(url: str) -> str:
    """The base_url without a trailing slash, if it is safe to send text and a key to: https,
    or plain http only to this Mac itself. Raises ProviderError otherwise."""
    url = (url or "").strip().rstrip("/")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        parts, host = None, ""
    if parts is None or not host or parts.scheme not in ("https", "http") or (parts.scheme == "http" and host not in _LOCAL_HOSTS):
        raise ProviderError(
            f"base_url under [openai] must be an https URL (http only for localhost), not {url!r}. "
            f"The default is {DEFAULT_BASE_URL}."
        )
    return url


class OpenAIProvider(Provider):
    """OpenAI gpt-4o-mini-tts. Cheap (~$12/1M chars of audio) and steerable, but ranks
    below Inworld and Cartesia on naturalness. `base_url` points it at an OpenAI-compatible
    gateway instead, for companies that route model traffic through their own."""

    name = "openai"
    label = "OpenAI TTS"
    key_url = "https://platform.openai.com/api-keys"
    vendor = "OpenAI"
    max_chars = 4000
    price_per_million = 12.0
    default_voice = "ash"
    default_model = "gpt-4o-mini-tts"

    @property
    def base_url(self) -> str:
        return check_base_url(self.cfg.get("base_url") or DEFAULT_BASE_URL)

    def recipient(self) -> str:
        """OpenAI, or the host of the custom endpoint the text actually goes to."""
        raw = (self.cfg.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
        if raw == DEFAULT_BASE_URL:
            return self.vendor
        try:
            return urlsplit(raw).hostname or raw
        except ValueError:
            return raw

    def synthesize(self, text: str) -> Audio:
        key = self._require_key()
        url = f"{self.base_url}/audio/speech"
        body = {"model": self.model, "input": text, "voice": self.voice, "response_format": "mp3"}
        if self.cfg.get("instructions"):
            body["instructions"] = self.cfg["instructions"]
        r = self._post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=90,
        )
        return Audio(r.content, "mp3")
