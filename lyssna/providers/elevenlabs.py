from __future__ import annotations

from .base import Audio, Provider


class ElevenLabsProvider(Provider):
    """ElevenLabs. eleven_v3 is the expressive model ($100/1M chars);
    eleven_flash_v2_5 is half the price and noticeably flatter."""

    name = "elevenlabs"
    label = "ElevenLabs"
    key_url = "https://elevenlabs.io/app/settings/api-keys"
    vendor = "ElevenLabs"
    max_chars = 4500  # v3 caps at 5000
    price_per_million = 100.0
    default_voice = "JBFqnCBsd6RMkjVDRZzb"  # "George", warm British narrator from the default library
    default_model = "eleven_v3"

    def synthesize(self, text: str) -> Audio:
        key = self._require_key()
        body = {"text": text, "model_id": self.model}
        r = self._post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )
        return Audio(r.content, "mp3")
