"""Track characters sent to paid providers per month, to show a running cost estimate.

Free voices (price 0, e.g. the ones on this Mac) are not counted at all, and only the last
KEEP_MONTHS months are kept in the file.

Dictation with online engines is counted in the same month, under DICTATION_KEY: seconds of
audio per speech engine, tokens per cleanup engine, and the estimated cost in USD (worked out
by the engine at the time, from its own list price). Engines on this Mac are not counted."""

from __future__ import annotations

import json
import threading
from datetime import date

from .config import STATE_DIR

USAGE_PATH = STATE_DIR / "usage.json"
KEEP_MONTHS = 12
DICTATION_KEY = "_dictation"  # inside a month, next to the providers' character counts
_lock = threading.Lock()


def _load() -> dict:
    try:
        data = json.loads(USAGE_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _month() -> str:
    return date.today().strftime("%Y-%m")


def _update(change) -> None:
    """Apply change(month_dict) to this month and save, keeping the last KEEP_MONTHS months."""
    month = _month()
    with _lock:
        data = _load()
        if not isinstance(data.get(month), dict):
            data[month] = {}
        change(data[month])
        data = {m: data[m] for m in sorted(data)[-KEEP_MONTHS:]}  # "YYYY-MM" sorts by date
        try:
            USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            USAGE_PATH.write_text(json.dumps(data, indent=2))
        except OSError:
            pass


def record(provider: str, chars: int, price_per_million: float) -> None:
    """Add `chars` to this month's count for a paid provider. Free providers are skipped."""
    if price_per_million <= 0 or chars <= 0:
        return

    def add(month: dict) -> None:
        month[provider] = month.get(provider, 0) + chars

    _update(add)


def record_dictation(engine: str, *, audio_seconds: float = 0.0, tokens_in: int = 0, tokens_out: int = 0,
                     usd: float = 0.0) -> None:
    """Add one paid dictation call to this month: audio seconds for a speech engine, tokens for a
    cleanup engine, and what it cost. Free engines (usd 0) are skipped."""
    if usd <= 0:
        return

    def add(month: dict) -> None:
        d = month.get(DICTATION_KEY)
        if not isinstance(d, dict):
            d = month[DICTATION_KEY] = {}
        e = d.setdefault("engines", {}).setdefault(engine, {})
        e["audio_seconds"] = round(e.get("audio_seconds", 0.0) + audio_seconds, 3)
        e["tokens_in"] = e.get("tokens_in", 0) + tokens_in
        e["tokens_out"] = e.get("tokens_out", 0) + tokens_out
        e["usd"] = e.get("usd", 0.0) + usd
        d["audio_seconds"] = round(d.get("audio_seconds", 0.0) + audio_seconds, 3)
        d["usd"] = d.get("usd", 0.0) + usd

    _update(add)


def this_month() -> dict[str, int]:
    """Characters per voice provider this month."""
    month = _load().get(_month(), {})
    return {p: n for p, n in month.items() if isinstance(n, int)} if isinstance(month, dict) else {}


def dictation_this_month() -> dict:
    """{"audio_seconds": .., "usd": .., "engines": {..}} for paid dictation this month, or {}."""
    month = _load().get(_month(), {})
    d = month.get(DICTATION_KEY) if isinstance(month, dict) else None
    return d if isinstance(d, dict) else {}


def _minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def summary(price_per_million: dict[str, float]) -> str:
    """One line for the menu, or "" when this month's estimated cost is zero (nothing to show)."""
    paid = {p: n for p, n in this_month().items() if price_per_million.get(p, 0) > 0}
    voice_cost = sum(n * price_per_million[p] / 1_000_000 for p, n in paid.items())
    dictation = dictation_this_month()
    dictation_cost = float(dictation.get("usd", 0.0) or 0.0)
    if voice_cost + dictation_cost <= 0:
        return ""
    parts = []
    if voice_cost > 0:
        chars = sum(paid.values())
        parts.append(f"{chars / 1000:.0f}k characters" if chars >= 1000 else f"{chars} characters")
    if dictation_cost > 0:
        seconds = float(dictation.get("audio_seconds", 0.0) or 0.0)
        parts.append(f"{_minutes(seconds)} of dictation" if seconds > 0 else "dictation cleanup")
    return f"This month: {' and '.join(parts)}, about ${voice_cost + dictation_cost:.2f}"
