"""Claude Haiku 5.5 as the cleanup step, over plain HTTPS to the Anthropic Messages API (no
vendor SDK, one network path to audit). Prompt v3 (see prompt.py): the system prompt and seven
example turns are a fixed prefix with a cache breakpoint on its last block, so after the first
call each cleanup pays the cached rate for it; then the real transcript with the vocabulary as
a <dictionary> line.

Cost rules: thinking disabled, effort low, a hard `max_tokens` cap sized to the input, no
sampling parameters (API version 2026-10-21 refuses them), no retries, a 10 s timeout. A
refusal, a reply cut at the cap, any status other than 200 and any timeout all mean "no usable
output": the pipeline pastes the rules text. One log line per call with seconds, token counts
(including cache reads and writes) and the status; never the text, the key or an error body."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

import httpx

from ... import usage
from ...providers.base import error_summary
from . import prompt
from .base import Cleaner, max_output_tokens

log = logging.getLogger("lyssna.dictation")

URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
TIMEOUT = 10.0
MAX_TOKENS_CEILING = 1024
# USD per million tokens, Claude Haiku 5.5 (cache write is the 5-minute rate).
PRICE_IN, PRICE_OUT, PRICE_CACHE_WRITE, PRICE_CACHE_READ = 0.10, 0.50, 0.125, 0.01


def request_body(model: str, raw: str, terms: Sequence[str]) -> dict:
    """The Messages API body for one cleanup."""
    turns: list[dict] = []
    for transcript, reply in prompt.EXAMPLES:
        turns.append({"role": "user", "content": [{"type": "text", "text": prompt.user_message(transcript)}]})
        turns.append({"role": "assistant", "content": [{"type": "text", "text": reply}]})
    turns[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}  # end of the fixed prefix
    turns.append({"role": "user", "content": [{"type": "text", "text": prompt.user_message(raw, terms)}]})
    return {
        "model": model,
        "max_tokens": max_output_tokens(raw, MAX_TOKENS_CEILING),
        "system": [{"type": "text", "text": prompt.SYSTEM}],
        "messages": turns,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low"},
    }


class HaikuCleaner(Cleaner):
    name = "haiku"
    label = "Claude Haiku 5.5"
    config_section = "anthropic"
    vendor = "Anthropic"
    key_url = "https://platform.claude.com/settings/keys"
    price_in_per_million = PRICE_IN
    price_out_per_million = PRICE_OUT
    budget_s = 4.0
    default_model = "claude-haiku-5-5"

    @property
    def model(self) -> str:
        return self.cfg.get("model") or self.default_model

    def clean(self, raw: str, terms: Sequence[str] = (), budget_s: float = 0.0) -> str | None:
        if not raw.strip():
            return ""
        key = self.api_key
        if not key:
            log.info("cleanup via haiku skipped: no API key")
            return None
        body = request_body(self.model, raw, terms)
        headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        start = time.monotonic()
        try:
            r = httpx.post(URL, headers=headers, json=body, timeout=TIMEOUT)
        except httpx.HTTPError as e:
            log.warning("cleanup via haiku failed after %.2fs: %s", time.monotonic() - start, error_summary(e))
            return None
        elapsed = time.monotonic() - start
        if r.status_code != 200:
            log.warning("cleanup via haiku failed after %.2fs: HTTP %s, %d bytes of detail not logged",
                        elapsed, r.status_code, len(r.content))
            return None
        try:
            data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            stop = str(data.get("stop_reason") or "")
            u = data.get("usage") or {}
        except (ValueError, AttributeError, TypeError) as e:
            log.warning("cleanup via haiku returned an unreadable response after %.2fs: %s", elapsed, type(e).__name__)
            return None
        n_in, n_out = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
        cache_write, cache_read = int(u.get("cache_creation_input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0)
        log.info("cleaned via haiku in %.2fs (HTTP 200, stop %s, tokens: %d in, %d out, %d cache write, %d cache read, "
                 "cap %d)", elapsed, stop or "?", n_in, n_out, cache_write, cache_read, body["max_tokens"])
        usd = (n_in * PRICE_IN + n_out * PRICE_OUT + cache_write * PRICE_CACHE_WRITE + cache_read * PRICE_CACHE_READ) / 1e6
        usage.record_dictation(self.name, tokens_in=n_in + cache_write + cache_read, tokens_out=n_out, usd=usd)
        if stop in ("refusal", "max_tokens"):
            return None
        return text
