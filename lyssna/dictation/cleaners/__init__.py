"""The cleanup engines, and which one to use. Same shape as lyssna/providers/__init__.py:
local-only mode refuses every cleaner that sends text off this Mac, and a cleaner that cannot
be used as set up falls back to the one on this Mac, then to none (rules only)."""

from __future__ import annotations

from ... import policy
from .base import Cleaner
from .haiku import HaikuCleaner
from .s1mini import S1MiniCleaner

REGISTRY: dict[str, type[Cleaner]] = {c.name: c for c in (S1MiniCleaner, HaikuCleaner)}
LOCAL = S1MiniCleaner.name
RULES = "rules"  # the name recorded when no cleaner runs


def _dictation(config: dict) -> dict:
    section = config.get("dictation")
    return section if isinstance(section, dict) else {}


def get_cleaner(name: str, config: dict) -> Cleaner:
    cls = REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown cleanup engine '{name}'. Choose one of: {', '.join(REGISTRY)}")
    return cls(config.get(cls.section(), {}))


def allowed(name: str, config: dict) -> bool:
    """False for a cleaner that sends text off this Mac while local-only mode is on."""
    cls = REGISTRY.get(name)
    return cls is not None and not (cls.sends_text_off_device and policy.local_only(config))


def _usable(c: Cleaner) -> bool:
    return c.can_run() and (c.configured() or not c.needs_key)


def cleanup_on(config: dict) -> bool:
    return bool(_dictation(config).get("cleanup", True))


def effective_cleaner(config: dict) -> str:
    """The configured cleaner if cleanup is on, local-only mode allows it and it can run as set
    up; else the one on this Mac; "" (rules only) when cleanup is off or nothing can run."""
    if not cleanup_on(config):
        return ""
    name = str(_dictation(config).get("cleaner") or LOCAL)
    if name in REGISTRY and allowed(name, config) and _usable(get_cleaner(name, config)):
        return name
    return LOCAL if S1MiniCleaner.can_run() else ""


def refusal(label: str) -> str:
    by = " (set by your organization)" if policy.managed_local_only() else ""
    return f"{label} sends your text to an online service, and local-only mode is on{by}. Use cleanup on this Mac instead."


__all__ = ["LOCAL", "REGISTRY", "RULES", "Cleaner", "allowed", "cleanup_on", "effective_cleaner", "get_cleaner", "refusal"]
