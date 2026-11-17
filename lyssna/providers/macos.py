from __future__ import annotations

import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from .base import Audio, Provider, ProviderError

PREFERRED = ["Zoe (Premium)", "Ava (Premium)", "Evan (Premium)", "Samantha"]


@lru_cache(maxsize=1)
def installed_voices() -> list[str]:
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    voices = []
    for line in out.splitlines():
        # Format: "Name (Variant)     en_US    # sample sentence"
        name = line.split("  ")[0].strip()
        if name:
            voices.append(name)
    return voices


class MacOSSayProvider(Provider):
    """Free, offline, built into macOS. Download a Premium voice in
    System Settings > Accessibility > Spoken Content > System Voice for a big quality jump."""

    name = "say"
    label = "On this Mac (System Voice)"
    needs_key = False
    sends_text_off_device = False
    max_chars = 20000
    price_per_million = 0.0
    default_model = "say"

    @property
    def voice(self) -> str:
        if self.cfg.get("voice"):
            return self.cfg["voice"]
        have = installed_voices()
        for v in PREFERRED:
            if v in have:
                return v
        return "Samantha"

    def synthesize(self, text: str) -> Audio:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.aiff"
            cmd = ["say", "-v", self.voice, "-o", str(out), "-f", "-"]
            try:
                p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=300)
            except (OSError, subprocess.SubprocessError) as e:
                raise ProviderError(f"`say` failed: {e}") from e
            if p.returncode != 0 or not out.exists():
                raise ProviderError(f"`say` failed: {p.stderr.strip()[:300]}")
            return Audio(out.read_bytes(), "aiff")
