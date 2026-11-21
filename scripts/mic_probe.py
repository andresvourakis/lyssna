"""Manual check of the Windows microphone (lyssna/win/mic.py): record three seconds through its
EngineInput and the real Recorder, and print the device, the format Qt chose, the chunk sizes,
the level meter's values and the final sample count at 16 kHz. Nothing is saved.

Run from the repo on a PC: `uv run --no-sync python scripts/mic_probe.py [seconds]`. Watch the
microphone-in-use icon by the clock: it should show while recording and go off within a moment
of "closed". It also runs on a Mac, where the first run makes macOS ask for microphone access
for the terminal app."""

from __future__ import annotations

import sys
import time

import numpy as np

from lyssna.dictation.recorder import TARGET_RATE, Recorder, RecorderError
from lyssna.win import mic


class SpyQt(mic._QtInput):
    """The real Qt side, noting the format each start() chose."""

    def __init__(self):
        super().__init__()
        self.formats: list[mic._Format] = []

    def start(self, on_data, on_lost):
        fmt = super().start(on_data, on_lost)
        self.formats.append(fmt)
        return fmt


class SpyInput(mic.EngineInput):
    """The real EngineInput, noting each chunk's size and rate and whether the device was lost."""

    def __init__(self, backend):
        super().__init__(backend)
        self.chunks: list[tuple[int, float, float]] = []  # frames, rate, seconds since open
        self.lost = 0

    def open(self, on_chunk, on_lost):
        opened = time.monotonic()

        def chunk(samples, rate):
            self.chunks.append((len(samples), rate, time.monotonic() - opened))
            on_chunk(samples, rate)

        def lost():
            self.lost += 1
            on_lost()

        return super().open(chunk, lost)


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    print(f"microphone access (privacy switches): {mic.microphone_status()}")
    qt = SpyQt()
    source = SpyInput(qt)
    t = time.monotonic()
    try:
        source.warm()
    except RecorderError as e:
        print(f"warm-up failed: {e}")
        return 1
    print(f"warm-up took {(time.monotonic() - t) * 1000:.0f} ms")

    levels: list[float] = []
    recorder = Recorder(source=source)
    t = time.monotonic()
    try:
        recorder.start(on_level=levels.append, on_stopped=lambda: print("the recording ended by itself (device lost?)"))
    except RecorderError as e:
        print(f"the microphone did not start: {e}")
        return 1
    print(f"open() returned after {(time.monotonic() - t) * 1000:.0f} ms")
    fmt = qt.formats[-1]
    print(f"device: {fmt.device}")
    print(f"format: {fmt.rate} Hz, {fmt.channels} channel(s), {fmt.sample}")
    print(f"recording {seconds:g} s: speak now")
    time.sleep(seconds)
    samples = recorder.stop()
    print("closed: the microphone-in-use icon should go off now")

    sizes = [n for n, _rate, _at in source.chunks]
    print(f"chunks: {len(sizes)}")
    if sizes:
        rate = source.chunks[0][1]
        print(f"chunk frames: min {min(sizes)}, max {max(sizes)}, median {int(np.median(sizes))}"
              f" (about {np.median(sizes) / rate * 1000:.0f} ms each at {rate:g} Hz)")
        print(f"first chunk after {source.chunks[0][2] * 1000:.0f} ms")
    print(f"start latency (recorder): {recorder.start_latency * 1000:.0f} ms" if recorder.start_latency is not None
          else "start latency (recorder): no audio arrived")
    print("levels: " + " ".join(f"{v:.2f}" for v in levels))
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    print(f"samples at {TARGET_RATE} Hz: {len(samples)} ({len(samples) / TARGET_RATE:.2f} s), peak {peak:.3f}")
    print(f"device lost during the recording: {source.lost} time(s)")
    time.sleep(2.0)  # time to look at the indicator before the process exits
    return 0


if __name__ == "__main__":
    sys.exit(main())
