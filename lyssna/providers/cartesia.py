from __future__ import annotations

from .base import Audio, Provider


class CartesiaProvider(Provider):
    """Cartesia Sonic 3.6: #1 on the Artificial Analysis arena, ~$49 per 1M characters."""

    name = "cartesia"
    label = "Cartesia Sonic"
    key_url = "https://play.cartesia.ai/keys"
    vendor = "Cartesia"
    max_chars = 5000
    price_per_million = 50.0
    default_voice = "47c38ca4-5f35-497b-b1a3-415245fb35e1"  # "Daniel", US English male
    default_model = "sonic-3.6"

    def synthesize(self, text: str) -> Audio:
        key = self._require_key()
        body = {
            "model_id": self.model,
            "transcript": text,
            "voice": {"mode": "id", "id": self.voice},
            "language": self.cfg.get("language", "en"),
            "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000},
        }
        r = self._post(
            "https://api.cartesia.ai/tts/bytes",
            headers={"Authorization": f"Bearer {key}", "Cartesia-Version": "2026-08-14"},
            json=body,
            timeout=60,
        )
        return Audio(r.content, "mp3")
