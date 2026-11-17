"""The cleanup engine interface: takes the transcript (already corrected with the user's
vocabulary) and returns it cleaned, or None when it has no usable output. A cleaner never
raises for ordinary failures (no key, network, refusal, timeout, a model that cannot load): it
logs a one-line summary without any text and returns None, and the pipeline pastes the
rules-only text instead. Every output still passes the guard before it is pasted."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence

from ... import keychain


class Cleaner:
    name = "base"
    label = "Base"
    needs_key = True
    setup_hint = "no API key"
    config_section = ""
    vendor = ""
    key_url = ""
    sends_text_off_device = True
    price_in_per_million = 0.0  # USD per million input tokens, for the usage estimate only
    price_out_per_million = 0.0
    budget_s = 4.0  # how long the pipeline waits for this cleaner before using the rules text

    @classmethod
    def section(cls) -> str:
        return cls.config_section or cls.name

    def __init__(self, cfg: dict | None = None, api_key: str = ""):
        self.cfg = cfg or {}
        self._key_override = api_key

    @property
    def api_key(self) -> str:
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
        return True

    def configured(self, wait: bool = True) -> bool | None:
        """Set up to clean. With wait=False it never waits for the Keychain and answers None
        while the Keychain is not read yet (as for the voices)."""
        if not self.needs_key:
            return True
        if not wait:
            return keychain.has_key(self.section(), self._key_override, self.cfg.get("api_key"),
                                    os.environ.get(f"{self.section().upper()}_API_KEY", ""))
        return bool(self.api_key)

    def prepare(self, on_error: Callable[[str], None] | None = None) -> None:
        """Called when the user picks this cleaner: start any one-time setup in the background."""

    def warm_up(self) -> None:
        """Called when a recording starts. Must return at once; may start a background load."""

    def ready(self, timeout: float | None = None) -> bool:
        """Wait (up to `timeout` seconds) until a warm-up started earlier has finished."""
        return True

    def recipient(self) -> str:
        return self.vendor or self.label

    def clean(self, raw: str, terms: Sequence[str] = (), budget_s: float = 0.0) -> str | None:
        raise NotImplementedError


def max_output_tokens(text: str, ceiling: int) -> int:
    """The hard output cap for one cleanup: twice the input words plus 64, at most `ceiling`."""
    return min(2 * len(text.split()) + 64, ceiling)
