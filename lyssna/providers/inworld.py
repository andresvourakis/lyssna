from __future__ import annotations

import base64
from typing import ClassVar

from .base import Audio, Provider, ProviderError


class InworldProvider(Provider):
    """Inworld TTS-2: #2 on the Artificial Analysis arena at $25 per 1M characters."""

    name = "inworld"
    label = "Inworld TTS-2"
    key_url = "https://platform.inworld.ai/"
    vendor = "Inworld"
    max_chars = 2000
    price_per_million = 25.0
    default_voice = "Sarah"
    default_model = "inworld-tts-2"
    voices: ClassVar[list[tuple[str, str]]] = [
        ("Sarah", "Sarah"), ("Jason", "Jason"), ("Nate", "Nate"), ("Luna", "Luna"), ("Sofia", "Sofía · Español"),
    ]

    def synthesize(self, text: str) -> Audio:
        key = self._require_key()
        body = {
            "text": text,
            "voiceId": self.voice,
            "modelId": self.model,
            "audioConfig": {"audioEncoding": "MP3", "sampleRateHertz": 44100, "bitRate": 128000},
        }
        if self.cfg.get("instruction"):
            body["instruction"] = self.cfg["instruction"]
        r = self._post(
            "https://api.inworld.ai/tts/v1/voice",
            headers={"Authorization": f"Basic {key}", "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        try:
            return Audio(base64.b64decode(r.json()["audioContent"]), "mp3")
        except (KeyError, ValueError) as e:
            raise ProviderError(f"Inworld returned an unexpected response: {r.text[:300]}") from e


class InworldFlashProvider(InworldProvider):
    """Inworld TTS-2 Flash: the low-latency sibling at $15 per 1M characters. Same key and
    voice as [inworld]; the model is fixed so the [inworld] model setting can't override it."""

    name = "inworld-flash"
    label = "Inworld TTS-2 Flash"
    price_per_million = 15.0
    default_model = "inworld-tts-2-flash"
    config_section = "inworld"

    @property
    def model(self) -> str:
        return self.default_model
