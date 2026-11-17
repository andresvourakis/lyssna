"""Inworld STT: $0.15 per audio hour, with the same key as the Inworld voices ([inworld] in the
config). Its `prompts` field is a soft bias toward listed terms (names, jargon), so the
vocabulary goes there as hints. Inworld refuses the whole request if a prompt holds a character
outside its allowed set, so terms with other characters are left out of the hints."""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence

from ... import usage
from .base import Transcriber, TranscriberError, audio_seconds, check_samples, wav_bytes

URL = "https://api.inworld.ai/stt/v1/transcribe"
MODEL = "inworld/inworld-stt-1"
MAX_PROMPTS = 100
# Letters and digits in any script, spaces, and . , ' ( ) : ! ? ; - (Inworld's documented set).
_ALLOWED_PROMPT = re.compile(r"^[\w .,'():!?;-]+$")


def prompt_terms(hints: Sequence[str]) -> list[str]:
    """The hints Inworld accepts as prompts: curly apostrophes made straight, terms with any
    other character left out, at most 100."""
    out = []
    for hint in hints:
        hint = hint.replace("’", "'").strip()
        if hint and "_" not in hint and _ALLOWED_PROMPT.match(hint):
            out.append(hint)
    return out[:MAX_PROMPTS]


class InworldTranscriber(Transcriber):
    name = "inworld"
    label = "Inworld STT"
    config_section = "inworld"
    vendor = "Inworld"
    key_url = "https://platform.inworld.ai/"
    price_per_hour = 0.15
    supports_hints = True

    def transcribe(self, samples, language: str = "", hints: Sequence[str] = ()) -> str:
        check_samples(samples)
        key = self._require_key()
        seconds = audio_seconds(samples)
        config: dict = {"modelId": MODEL, "audioEncoding": "LINEAR16", "sampleRateHertz": 16000, "numberOfChannels": 1}
        language = (language or self.language or "").split("-")[0]
        if language:
            config["language"] = language
        prompts = prompt_terms(hints)
        if prompts:
            config["prompts"] = prompts
        body = {"transcribeConfig": config, "audioData": {"content": base64.b64encode(wav_bytes(samples)).decode()}}
        r = self._post(URL, seconds, headers={"Authorization": f"Basic {key}", "Content-Type": "application/json"}, json=body)
        try:
            text = r.json()["transcription"]["transcript"]
        except (KeyError, ValueError, TypeError) as e:
            raise TranscriberError("Inworld returned an unexpected response.") from e
        usage.record_dictation(self.name, audio_seconds=seconds, usd=seconds / 3600 * self.price_per_hour)
        return str(text or "").strip()
