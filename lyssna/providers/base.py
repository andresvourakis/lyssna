from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar

import httpx

from .. import host, keychain


@dataclass
class Audio:
    data: bytes
    ext: str  # "mp3", "wav", "aiff"


class ProviderError(RuntimeError):
    """A failed synthesis. The message is for the on-screen notice and may quote the
    provider's response; `status` is the HTTP status when there was one."""

    def __init__(self, message: str = "", status: int | None = None):
        super().__init__(message)
        self.status = status


def error_summary(e: BaseException) -> str:
    """What the log may say about a failure: the exception type, the HTTP status if any, and
    the message length. Never the message itself, which can echo the text being read."""
    status = getattr(e, "status", None)
    http = f", HTTP {status}" if status else ""
    return f"{type(e).__name__}{http}, {len(str(e))} chars of detail not logged"


class Provider:
    """One TTS backend. Subclasses set the class attributes and implement synthesize()."""

    name = "base"
    label = "Base"
    needs_key = True
    setup_hint = "no API key"  # shown in the menu when configured() is False
    max_chars = 2000
    price_per_million = 0.0  # USD list price per 1M characters, used for the usage estimate only
    default_voice = ""
    default_model = ""
    config_section = ""  # config table + env var prefix to read; defaults to `name`
    key_url = ""  # https page where the user gets an API key; "Get a key" in the setup window opens it
    vendor = ""  # company name used in request error messages, e.g. "Cartesia"
    sends_text_off_device = True  # False for engines that never send the text anywhere (shown in the menu)
    voices: ClassVar[list[tuple[str, str]]] = []  # (voice id, menu label) offered in the voice picker; empty = no picker

    @classmethod
    def section(cls) -> str:
        return cls.config_section or cls.name

    def __init__(self, cfg: dict, api_key: str = ""):
        self.cfg = cfg or {}
        self._key_override = api_key  # a key being tried out, ahead of any saved one

    @property
    def voice(self) -> str:
        return self.cfg.get("voice") or self.default_voice

    @property
    def model(self) -> str:
        return self.cfg.get("model") or self.default_model

    @property
    def api_key(self) -> str:
        """Keychain first (where the menu saves keys), then the config file, then the env var.
        The Keychain read is cached, so asking often is cheap."""
        if not self.needs_key:
            return ""
        return (
            self._key_override
            or keychain.get(self.section())
            or self.cfg.get("api_key")
            or os.environ.get(f"{self.section().upper()}_API_KEY", "")
        )

    @classmethod
    def can_run(cls) -> bool:
        """Whether this engine can work at all in this install (its packages are present).
        Must be cheap: the menu asks often."""
        return True

    def configured(self, wait: bool = True) -> bool | None:
        """Set up to read. With wait=False it never waits for the Keychain (the main thread
        asks this way: menus, windows) and answers None while the Keychain is not read yet."""
        if not self.needs_key:
            return True
        if not wait:
            return keychain.has_key(self.section(), self._key_override, self.cfg.get("api_key"),
                                    os.environ.get(f"{self.section().upper()}_API_KEY", ""))
        return bool(self.api_key)

    def prepare(self, on_error=None) -> None:
        """Called when the user picks this engine: start any one-time setup in the background.
        on_error(message) is called if that setup fails. Nothing to do for most engines."""

    def recipient(self) -> str:
        """Who receives the text when this engine reads it, for the privacy line in the menu."""
        return self.vendor or self.label

    def cache_key(self) -> str:
        return f"{self.name}|{self.model}|{self.voice}"

    def describe(self) -> str:
        return f"{self.label} · {self.voice}"

    def synthesize(self, text: str) -> Audio:
        raise NotImplementedError

    def _require_key(self) -> str:
        key = self.api_key
        if not key:
            raise ProviderError(
                f"No API key for {self.label}. Add one from {host.MENU_PLACE} (Settings > API Keys), "
                f"or set {self.section().upper()}_API_KEY. An api_key under [{self.section()}] in the "
                f"config file also works."
            )
        return key

    def _post(self, url: str, timeout: float, **kwargs) -> httpx.Response:
        """POST to the engine's API and return the 200 response. Network failures and any
        other status become a ProviderError. Deliberately no retries: a retry re-runs (and
        re-bills) a whole synthesis."""
        try:
            r = httpx.post(url, timeout=timeout, **kwargs)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.vendor} request failed: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"{self.vendor} error {r.status_code}: {r.text[:300]}", status=r.status_code)
        return r
