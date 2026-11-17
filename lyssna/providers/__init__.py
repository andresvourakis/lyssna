from __future__ import annotations

from .. import policy
from .base import Audio, Provider, ProviderError
from .cartesia import CartesiaProvider
from .elevenlabs import ElevenLabsProvider
from .inworld import InworldFlashProvider, InworldProvider
from .kokoro import KokoroProvider
from .macos import MacOSSayProvider
from .openai import OpenAIProvider

REGISTRY: dict[str, type[Provider]] = {
    p.name: p
    for p in (
        InworldProvider,
        InworldFlashProvider,
        CartesiaProvider,
        ElevenLabsProvider,
        OpenAIProvider,
        KokoroProvider,
        MacOSSayProvider,
    )
}


def get_provider(name: str, config: dict) -> Provider:
    cls = REGISTRY.get(name)
    if cls is None:
        raise ProviderError(f"Unknown provider '{name}'. Choose one of: {', '.join(REGISTRY)}")
    return cls(config.get(cls.section(), {}))


def _usable(p: Provider) -> bool:
    """Can this engine read right now, or after a one-time setup it does by itself? Engines
    that need a key need the key; Kokoro only needs its packages (it downloads its voice)."""
    return p.can_run() and (p.configured() or not p.needs_key)


def allowed(name: str, config: dict) -> bool:
    """False for an engine that sends text off this Mac while local-only mode is on."""
    cls = REGISTRY.get(name)
    return cls is not None and not (cls.sends_text_off_device and policy.local_only(config))


def local_fallback() -> str:
    """The engine to land on when the chosen one cannot be used: the free Kokoro voice on
    this Mac, or the built-in macOS `say` when Kokoro cannot run in this install."""
    return KokoroProvider.name if KokoroProvider.can_run() else MacOSSayProvider.name


def effective_provider(config: dict) -> str:
    """The configured provider if it can run as set up and local-only mode allows it.
    Otherwise local_fallback()."""
    name = config.get("provider", "")
    if name in REGISTRY and allowed(name, config) and _usable(get_provider(name, config)):
        return name
    return local_fallback()


__all__ = ["REGISTRY", "Audio", "Provider", "ProviderError", "allowed", "effective_provider", "get_provider",
           "local_fallback"]
