"""Listen to the Windows player (lyssna/win/audio.py) at three speeds, to hear that the pitch
stays the same when the speed changes. A manual check for a person at the speakers; it also
runs on the Mac, where Qt Multimedia is installed for development.

  uv run python scripts/play_probe.py                  a generated tone sweep
  uv run python scripts/play_probe.py speech.wav       a file (wav, mp3 or aiff)
  uv run python scripts/play_probe.py --kokoro "Hi."   a sentence from Kokoro, on this computer

It plays the audio at 1.0x, then 1.5x, then 0.8x, printing the duration, what Qt says about
keeping the pitch, and the position once a second. Kokoro runs offline and costs nothing; this
script never calls an online voice."""

from __future__ import annotations

import argparse
import io
import logging
import math
import struct
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lyssna.providers.base import Audio, ProviderError
from lyssna.win import audio as win_audio

SPEEDS = (1.0, 1.5, 0.8)


def tone_sweep(seconds: float = 6.0, rate: int = 22050) -> bytes:
    """A quiet sweep from 220 Hz to 660 Hz and back, about as long as a spoken sentence. A pitch
    change at other speeds is easy to hear on it."""
    out = io.BytesIO()
    frames = bytearray()
    phase = 0.0
    n = int(seconds * rate)
    for i in range(n):
        f = 220 + 440 * (1 - abs(2 * i / n - 1))
        phase += 2 * math.pi * f / rate
        frames += struct.pack("<h", int(0.08 * 32767 * math.sin(phase)))
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return out.getvalue()


def kokoro_audio(text: str) -> Audio:
    from lyssna.providers import kokoro

    if not kokoro.files_present():
        sys.exit("Kokoro is not downloaded on this computer, so --kokoro cannot be used.")
    print("Synthesizing with Kokoro...")
    return kokoro.KokoroProvider({}).synthesize(text)


def play(audio: Audio, speed: float) -> None:
    player = win_audio.make_player(audio, speed)
    print(f"\n{speed:g}x  duration {player.duration():.2f} s (listening time {player.duration() / speed:.2f} s)")
    start = time.monotonic()
    player.play()
    next_print = 0.0
    while player.isPlaying():
        elapsed = time.monotonic() - start
        if elapsed >= next_print:
            print(f"  {elapsed:4.1f} s  position {player.currentTime():.2f} s")
            next_print += 1.0
        time.sleep(0.02)
    print(f"  finished after {time.monotonic() - start:.2f} s at position {player.currentTime():.2f} s")
    player.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Play audio through the Windows player at 1.0x, 1.5x and 0.8x.")
    parser.add_argument("file", nargs="?", help="an audio file to play (wav, mp3 or aiff)")
    parser.add_argument("--kokoro", metavar="TEXT", help="synthesize this sentence with Kokoro and play it")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    if args.kokoro:
        audio = kokoro_audio(args.kokoro)
        source = "Kokoro"
    elif args.file:
        path = Path(args.file)
        audio = Audio(path.read_bytes(), path.suffix.lstrip(".").lower())
        source = str(path)
    else:
        audio = Audio(tone_sweep(), "wav")
        source = "a generated tone sweep"

    print(f"Playing {source}. Keep the volume low.")
    print(f"Qt pitch compensation: {win_audio.pitch_compensation()}")
    try:
        for speed in SPEEDS:
            play(audio, speed)
    except ProviderError as e:
        print(f"Playback failed: {e}", file=sys.stderr)
        return 1
    print("\nDone. The pitch should sound the same at all three speeds; only the pace should change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
