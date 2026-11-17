"""The speech-to-text engines, and which one to use. Same shape as lyssna/providers/__init__.py:
local-only mode refuses every engine that sends audio off this Mac, and an engine that cannot
be used as set up falls back to the one on this Mac."""

from __future__ import annotations

from ... import policy
from .base import Transcriber, TranscriberError
from .inworld import InworldTranscriber
from .openai import OpenAITranscriber
from .parakeet import ParakeetTranscriber

REGISTRY: dict[str, type[Transcriber]] = {t.name: t for t in (ParakeetTranscriber, OpenAITranscriber, InworldTranscriber)}
LOCAL = ParakeetTranscriber.name


def dictation_cfg(config: dict) -> dict:
    section = config.get("dictation")
    return section if isinstance(section, dict) else {}


def get_transcriber(name: str, config: dict) -> Transcriber:
    cls = REGISTRY.get(name)
    if cls is None:
        raise TranscriberError(f"Unknown speech engine '{name}'. Choose one of: {', '.join(REGISTRY)}")
    return cls(config.get(cls.section(), {}), language=str(dictation_cfg(config).get("language") or "en"))


def allowed(name: str, config: dict) -> bool:
    """False for an engine that sends audio off this Mac while local-only mode is on."""
    cls = REGISTRY.get(name)
    return cls is not None and not (cls.sends_audio_off_device and policy.local_only(config))


def _usable(t: Transcriber, wait: bool = True) -> bool:
    return t.can_run() and (not t.needs_key or t.configured(wait) is not False)


def effective_transcriber(config: dict, wait: bool = True) -> str:
    """The configured engine if local-only mode allows it and it can run as set up; else the
    engine on this Mac; "" when not even that can run in this install. wait=False never waits
    for the Keychain: a key it has not answered for yet counts as there (menus, the key tap)."""
    name = str(dictation_cfg(config).get("transcriber") or LOCAL)
    if name in REGISTRY and allowed(name, config) and _usable(get_transcriber(name, config), wait):
        return name
    return LOCAL if ParakeetTranscriber.can_run() else ""


def refusal(label: str) -> str:
    """The message shown when someone asks for an online engine while local-only mode is on."""
    by = " (set by your organization)" if policy.managed_local_only() else ""
    return f"{label} sends your voice to an online service, and local-only mode is on{by}. Use the engine on this Mac instead."


__all__ = ["LOCAL", "REGISTRY", "Transcriber", "TranscriberError", "allowed", "effective_transcriber",
           "get_transcriber", "refusal"]
