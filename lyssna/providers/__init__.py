from __future__ import annotations

from .. import host, policy
from .base import Audio, Provider, ProviderError
from .cartesia import CartesiaProvider
from .elevenlabs import ElevenLabsProvider
from .inworld import InworldFlashProvider, InworldProvider
from .kokoro import KokoroProvider
from .openai import OpenAIProvider

_PROVIDERS: list[type[Provider]] = [
    InworldProvider,
    InworldFlashProvider,
    CartesiaProvider,
    ElevenLabsProvider,
    OpenAIProvider,
    KokoroProvider,
]
if host.IS_MAC:  # the built-in `say` voice exists only on the Mac
    from .macos import MacOSSayProvider

    _PROVIDERS.append(MacOSSayProvider)
elif host.IS_WINDOWS:  # the built-in SAPI voices exist only on Windows
    from .windows import WindowsVoiceProvider

    _PROVIDERS.append(WindowsVoiceProvider)

REGISTRY: dict[str, type[Provider]] = {p.name: p for p in _PROVIDERS}


def get_provider(name: str, config: dict) -> Provider:
    cls = REGISTRY.get(name)
    if cls is None:
        raise ProviderError(f"Unknown provider '{name}'. Choose one of: {', '.join(REGISTRY)}")
    return cls(config.get(cls.section(), {}))


def _usable(p: Provider, wait: bool = True) -> bool:
    """Can this engine read right now, or after a one-time setup it does by itself? Engines
    that need a key need the key; Kokoro only needs its packages (it downloads its voice).
    With wait=False a key the Keychain has not answered for yet counts as there."""
    return p.can_run() and (not p.needs_key or p.configured(wait) is not False)


def allowed(name: str, config: dict) -> bool:
    """False for an engine that sends text off this Mac while local-only mode is on."""
    cls = REGISTRY.get(name)
    return cls is not None and not (cls.sends_text_off_device and policy.local_only(config))


def local_fallback() -> str:
    """The engine to land on when the chosen one cannot be used: the free Kokoro voice on
    this computer. When Kokoro cannot run in this install, the voice built into the system:
    `say` on the Mac, SAPI on Windows; elsewhere Kokoro even then (there is no system voice)."""
    if KokoroProvider.can_run():
        return KokoroProvider.name
    for name in ("say", "sapi"):
        if name in REGISTRY:
            return name
    return KokoroProvider.name


def effective_provider(config: dict, wait: bool = True) -> str:
    """The configured provider if it can run as set up and local-only mode allows it.
    Otherwise local_fallback(). wait=False never waits for the Keychain (for display only:
    an unknown key counts as there, so never act on that answer)."""
    name = config.get("provider", "")
    if name in REGISTRY and allowed(name, config) and _usable(get_provider(name, config), wait):
        return name
    return local_fallback()


__all__ = ["REGISTRY", "Audio", "Provider", "ProviderError", "allowed", "effective_provider", "get_provider",
           "local_fallback"]
