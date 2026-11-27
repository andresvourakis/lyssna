"""Microphone to 16 kHz mono float32 samples, in memory only.

The microphone itself is the platform's (host.mic: lyssna/mac/mic.py on the Mac). It delivers
mono float32 chunks at whatever rate the hardware gives, and each chunk is resampled to 16 kHz
right away by a small streaming resampler in numpy (an anti-alias low-pass, then linear
interpolation). The numpy path is pure, unit tested with synthetic tones, costs well under a
millisecond per buffer, and keeps memory at 64 KB per second whatever the hardware rate, so
stop() returns at once instead of resampling a long recording while the user waits for the
text.

Audio is never written to disk and never logged; the log gets durations and counts only.

The input is opened for each recording and closed after it, which releases the microphone so
the system's indicator goes off. If the rate changes within one recording, the resampler for
the old rate is flushed into the recording and a new one starts. When the input device goes away
mid-recording the input says so: we end the recording there and keep what was captured."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable

import numpy as np

from .. import host

log = logging.getLogger("lyssna.dictation.recorder")

TARGET_RATE = 16000
MAX_SECONDS = 600.0   # hard cap: the recording ends by itself after ten minutes
LEVEL_HZ = 20         # level callbacks per second while recording
TAIL_WAIT = 0.15      # stop() waits up to this long for the buffer holding the last words


class RecorderError(Exception):
    """A short, user-facing reason the microphone could not start."""


# -- the microphone, through lyssna.host -----------------------------------------------

def microphone_status() -> str:
    """"granted", "denied" or "undetermined" for this app's microphone access (host.mic)."""
    return host.require("mic").microphone_status()


def request_microphone(callback: Callable[[bool], None]) -> None:
    """Ask for microphone access; the callback gets True or False, on an arbitrary thread."""
    host.require("mic").request_microphone(callback)


# -- pure signal pieces ----------------------------------------------------------

def _lowpass(cutoff: float, taps: int) -> np.ndarray:
    """Windowed-sinc low-pass FIR; `cutoff` as a fraction of the input rate (0..0.5)."""
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.blackman(taps)
    return (h / h.sum()).astype(np.float32)


class Resampler:
    """Streaming resampler from any rate to `target` Hz. push() takes mono chunks and returns
    the output samples ready so far; flush() returns the tail. Downsampling first removes
    everything above 90 % of the target's Nyquist frequency, so nothing folds back."""

    def __init__(self, source: float, target: int = TARGET_RATE, taps: int = 63):
        self.source = float(source)
        self.target = target
        self.step = self.source / target  # input samples per output sample
        self._h = _lowpass(0.45 * target / self.source, taps) if self.source > target else None
        self._hist = np.zeros(taps - 1 if self._h is not None else 0, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)  # filtered input not yet consumed
        self._base = 0       # global index of _buf[0]
        self._pos = 0.0      # global input position of the next output sample

    def _filter(self, chunk: np.ndarray) -> np.ndarray:
        if self._h is None:
            return chunk
        x = np.concatenate((self._hist, chunk))
        self._hist = x[len(x) - len(self._hist):]
        return np.convolve(x, self._h, mode="valid").astype(np.float32)

    def push(self, chunk: np.ndarray) -> np.ndarray:
        chunk = np.asarray(chunk, dtype=np.float32)
        if chunk.size == 0:
            return np.zeros(0, dtype=np.float32)
        if self.source == self.target:
            return chunk.copy()
        self._buf = np.concatenate((self._buf, self._filter(chunk)))
        end = self._base + len(self._buf) - 1  # last position we can interpolate up to
        if self._pos > end:
            return np.zeros(0, dtype=np.float32)
        count = math.floor((end - self._pos) / self.step) + 1
        positions = self._pos + self.step * np.arange(count)
        local = positions - self._base
        i = np.minimum(np.floor(local).astype(np.int64), len(self._buf) - 1)
        frac = (local - i).astype(np.float32)
        nxt = np.minimum(i + 1, len(self._buf) - 1)
        out = self._buf[i] * (1 - frac) + self._buf[nxt] * frac
        self._pos += self.step * count
        keep_from = min(math.floor(self._pos) - self._base, len(self._buf))
        if keep_from > 0:
            self._buf = self._buf[keep_from:]
            self._base += keep_from
        return out.astype(np.float32)

    def flush(self) -> np.ndarray:
        """The samples still held back by the filter's delay."""
        if self._h is None or self.source == self.target:
            return np.zeros(0, dtype=np.float32)
        return self.push(np.zeros(len(self._h) // 2, dtype=np.float32))


class LevelMeter:
    """Turns audio into a calm 0..1 level for the wave. Loudness is measured as RMS in
    decibels and mapped linearly from FLOOR_DB (silence) to CEIL_DB (loud speech close to the
    microphone), which matches how loud
    speech sounds better than raw amplitude does. The meter rises at once and falls back
    smoothly, the way a VU meter does, so the wave follows syllables without flickering."""

    FLOOR_DB = -55.0
    CEIL_DB = -12.0
    RELEASE = 0.6  # share of the previous level kept per tick when the input gets quieter

    def __init__(self):
        self._peak = 0.0   # loudest block since the last tick
        self.level = 0.0

    @classmethod
    def scale(cls, samples: np.ndarray) -> float:
        if samples.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        if rms <= 0:
            return 0.0
        db = 20 * math.log10(rms)
        return min(1.0, max(0.0, (db - cls.FLOOR_DB) / (cls.CEIL_DB - cls.FLOOR_DB)))

    def feed(self, samples: np.ndarray, rate: float) -> None:
        block = max(1, int(rate / LEVEL_HZ))
        for k in range(0, len(samples), block):
            self._peak = max(self._peak, self.scale(samples[k:k + block]))

    def tick(self) -> float:
        target, self._peak = self._peak, 0.0
        self.level = target if target >= self.level else self.level * self.RELEASE + target * (1 - self.RELEASE)
        return self.level


class Recorder:
    """One recording at a time: start(on_level) -> stop() samples, or cancel().

    `on_stopped` (optional) is called once when the recording ends by itself: the ten-minute
    cap or the microphone going away. The caller then calls stop() as usual to get the audio;
    `ended_by` then says why ("limit" or "device"; None when it did not end by itself).

    Stopping the engine drops the buffer it was filling, up to 0.1 s of audio, which is often
    the end of the last word. So stop() first waits (at most TAIL_WAIT) for the next buffer."""

    def __init__(self, max_seconds: float = MAX_SECONDS, source=None):
        self.max_seconds = max_seconds
        self._source = source  # the microphone input; host.mic's EngineInput unless a test passes one
        self.start_latency: float | None = None  # seconds from start() to the first buffer, last recording
        self.ended_by: str | None = None  # why the last recording ended by itself, if it did
        self._lock = threading.Lock()
        self._arrived = threading.Condition(self._lock)  # notified on every buffer
        self._buffers = 0
        self._state = "idle"  # idle | recording | ended (input closed, samples kept until stop/cancel)
        self._chunks: list[np.ndarray] = []
        self._frames = 0       # output samples so far
        self._resampler: Resampler | None = None
        self._meter = LevelMeter()
        self._on_level: Callable[[float], None] | None = None
        self._on_stopped: Callable[[], None] | None = None
        self._t_start = 0.0
        self._generation = 0

    @property
    def source(self):
        """The microphone input, built on first use so a Recorder can exist where host.mic has none."""
        if self._source is None:
            self._source = host.require("mic").EngineInput()
        return self._source

    def warm(self) -> None:
        """Get the engine ready (without opening the microphone) so the first start is fast.
        Does nothing until microphone access is granted, so it never makes macOS ask."""
        try:
            if microphone_status() == "granted":
                self.source.warm()
        except Exception:
            log.debug("microphone warm-up failed", exc_info=True)

    def is_recording(self) -> bool:
        return self._state == "recording"

    def seconds(self) -> float:
        return self._frames / TARGET_RATE

    def start(self, on_level: Callable[[float], None] | None = None, on_stopped: Callable[[], None] | None = None) -> None:
        with self._lock:
            if self._state != "idle":
                raise RecorderError("Already recording")
            self._generation += 1
            gen = self._generation
            self._chunks, self._frames = [], 0
            self._resampler = None
            self._meter = LevelMeter()
            self._on_level, self._on_stopped = on_level, on_stopped
            self.start_latency = None
            self.ended_by = None
            self._t_start = time.monotonic()
            self._state = "recording"
        try:
            self.source.open(lambda chunk, rate: self._on_chunk(gen, chunk, rate), lambda: self._end(gen, "device"))
        except Exception:
            with self._lock:
                self._state = "idle"
            raise
        if on_level is not None:
            threading.Thread(target=self._meter_loop, args=(gen,), daemon=True, name="lyssna-mic-level").start()

    def _on_chunk(self, gen: int, chunk: np.ndarray, rate: float) -> None:
        with self._lock:
            if gen != self._generation or self._state != "recording":
                return
            if self.start_latency is None:
                self.start_latency = time.monotonic() - self._t_start
                log.info("microphone started in %.0f ms at %d Hz", self.start_latency * 1000, int(rate))
            if self._resampler is None or self._resampler.source != rate:
                if self._resampler is not None:
                    log.info("microphone rate changed from %d Hz to %d Hz", int(self._resampler.source), int(rate))
                    self._keep(self._resampler.flush())
                self._resampler = Resampler(rate)
            self._meter.feed(chunk, rate)
            self._keep(self._resampler.push(chunk))
            self._buffers += 1
            self._arrived.notify_all()
            over = self._frames >= self.max_seconds * TARGET_RATE
        if over:
            threading.Thread(target=self._end, args=(gen, "limit"), daemon=True, name="lyssna-mic-end").start()

    def _keep(self, samples: np.ndarray) -> None:
        if samples.size:
            room = int(self.max_seconds * TARGET_RATE) - self._frames
            samples = samples[:max(0, room)]
            self._chunks.append(samples)
            self._frames += len(samples)

    def _end(self, gen: int, why: str) -> None:
        """The recording ended by itself (cap reached, device gone): close the input, keep the audio."""
        with self._lock:
            if gen != self._generation or self._state != "recording":
                return
            self._state = "ended"
            self.ended_by = why
            callback = self._on_stopped
        self.source.close()
        log.info("recording ended by itself (%s) after %.1f s", why, self.seconds())
        if callback is not None:
            try:
                callback()
            except Exception:
                log.exception("on_stopped callback failed")

    def _meter_loop(self, gen: int) -> None:
        period = 1.0 / LEVEL_HZ
        while True:
            time.sleep(period)
            with self._lock:
                if gen != self._generation or self._state != "recording":
                    return
                level = self._meter.tick()
                callback = self._on_level
            try:
                callback(level)
            except Exception:
                log.exception("level callback failed")

    def stop(self) -> np.ndarray:
        """End the recording and return its samples (16 kHz mono float32; empty if none)."""
        with self._lock:
            state = self._state
            if state == "idle":
                return np.zeros(0, dtype=np.float32)
            if state == "recording":
                seen = self._buffers
                self._arrived.wait_for(lambda: self._buffers > seen or self._state != "recording", TAIL_WAIT)
                state = self._state
            self._state = "idle"
            self._generation += 1
        if state == "recording":
            self.source.close()
        with self._lock:
            if self._resampler is not None:
                self._keep(self._resampler.flush())
            chunks, self._chunks = self._chunks, []
            self._resampler = None
        samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        log.info("recorded %.1f s", len(samples) / TARGET_RATE)
        return samples

    def cancel(self) -> None:
        """End the recording and drop the audio."""
        with self._lock:
            state = self._state
            self._state = "idle"
            self._generation += 1
            self._chunks, self._frames, self._resampler = [], 0, None
        if state == "recording":
            self.source.close()
