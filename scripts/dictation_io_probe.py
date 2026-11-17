"""Manual check of the dictation recorder: record a few seconds from the default microphone
and print the sample count, the peak level and how long the microphone took to start.
Nothing is saved. Run from the repo: `uv run python scripts/dictation_io_probe.py [seconds]`.
The first run may make macOS ask for microphone access for the terminal app."""

from __future__ import annotations

import sys
import threading
import time

import numpy as np

from lyssna.dictation import recorder


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    status = recorder.microphone_status()
    print(f"microphone access: {status}")
    if status == "undetermined":
        answered = threading.Event()
        result = {}

        def got(granted: bool) -> None:
            result["granted"] = granted
            answered.set()

        recorder.request_microphone(got)
        print("asked for access; answer the macOS prompt (waiting up to 60 s)")
        if not answered.wait(60):
            print("no answer to the prompt")
            return 1
        status = "granted" if result["granted"] else "denied"
        print(f"microphone access: {status}")
    if status != "granted":
        return 1

    rec = recorder.Recorder()
    t = time.monotonic()
    rec.warm()
    print(f"engine prepared in {(time.monotonic() - t) * 1000:.0f} ms")
    levels: list[float] = []
    for attempt in (1, 2):
        levels.clear()
        rec.start(on_level=levels.append)
        time.sleep(seconds)
        samples = rec.stop()
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        latency = rec.start_latency
        started = f"{latency * 1000:.0f} ms" if latency is not None else "no buffer arrived"
        print(
            f"run {attempt}: {samples.size} samples ({samples.size / recorder.TARGET_RATE:.2f} s at 16 kHz), "
            f"dtype {samples.dtype}, peak {peak:.3f}, start latency {started}"
        )
        print(f"run {attempt}: {len(levels)} level callbacks, max level {max(levels, default=0):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
