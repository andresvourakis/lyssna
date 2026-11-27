"""The recorder without a microphone: the resampler and level meter on synthetic tones, and
the Recorder's bookkeeping (stop, cancel, the time cap, a lost device) on a fake input."""

import threading
import time

import numpy as np
import pytest

from lyssna.dictation import recorder
from lyssna.dictation.recorder import LevelMeter, Recorder, RecorderError, Resampler


def tone(freq, rate, seconds, amp=0.5):
    t = np.arange(int(rate * seconds)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def resample_in_chunks(x, rate, chunk=4800):
    r = Resampler(rate)
    parts = [r.push(x[k:k + chunk]) for k in range(0, len(x), chunk)]
    parts.append(r.flush())
    return np.concatenate(parts)


def dominant_frequency(x, rate=16000):
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / rate)[int(np.argmax(spectrum))]


@pytest.mark.parametrize("rate", [48000, 44100, 24000, 16000])
def test_resampler_keeps_speech_band_tones(rate):
    y = resample_in_chunks(tone(1000, rate, 1.0), rate)
    assert y.dtype == np.float32
    assert abs(len(y) - 16000) <= 40  # the filter's tail adds a few samples
    middle = y[1000:15000]
    assert abs(dominant_frequency(middle) - 1000) < 5
    assert 0.45 < np.max(np.abs(middle)) < 0.55


def test_resampler_removes_what_16khz_cannot_hold():
    """A 12 kHz tone would fold back to 4 kHz without the anti-alias filter."""
    y = resample_in_chunks(tone(12000, 48000, 1.0), 48000)
    assert np.max(np.abs(y[1000:15000])) < 0.01


def test_resampler_chunking_does_not_matter():
    x = tone(440, 44100, 0.5) + tone(3000, 44100, 0.5, 0.2)
    a = resample_in_chunks(x, 44100, chunk=4410)
    b = resample_in_chunks(x, 44100, chunk=1013)
    assert len(a) == len(b)
    assert np.allclose(a, b, atol=1e-5)


def test_level_meter_scale():
    assert LevelMeter.scale(np.zeros(800, dtype=np.float32)) == 0.0
    assert LevelMeter.scale(np.full(800, 1e-5, dtype=np.float32)) == 0.0  # below the floor
    quiet = LevelMeter.scale(tone(300, 16000, 0.05, 0.01))
    loud = LevelMeter.scale(tone(300, 16000, 0.05, 0.3))
    assert 0.0 < quiet < loud <= 1.0


def test_level_meter_rises_at_once_and_falls_smoothly():
    m = LevelMeter()
    m.feed(tone(300, 16000, 0.05, 0.3), 16000)
    high = m.tick()
    assert high > 0.5
    m.feed(np.zeros(800, dtype=np.float32), 16000)
    falling = m.tick()
    assert 0 < falling < high
    assert m.tick() < falling


class FakeInput:
    """Stands in for EngineInput: the test pushes chunks; nothing touches a microphone."""

    def __init__(self, rate=48000.0, fail=None):
        self.rate = rate
        self.fail = fail
        self.on_chunk = self.on_lost = None
        self.opened = self.closed = 0

    def warm(self):
        pass

    def open(self, on_chunk, on_lost):
        if self.fail:
            raise self.fail
        self.opened += 1
        self.on_chunk, self.on_lost = on_chunk, on_lost
        return self.rate

    def close(self):
        self.closed += 1

    def push(self, x):
        self.on_chunk(x, self.rate)


@pytest.fixture(autouse=True)
def no_tail_wait(monkeypatch):
    monkeypatch.setattr(recorder, "TAIL_WAIT", 0.0)


def test_stop_returns_16khz_samples_and_closes_the_microphone():
    src = FakeInput()
    rec = Recorder(source=src)
    rec.start()
    assert rec.is_recording() and src.opened == 1
    x = tone(500, 48000, 1.0)
    for k in range(0, len(x), 4800):
        src.push(x[k:k + 4800])
    samples = rec.stop()
    assert src.closed == 1 and not rec.is_recording()
    assert samples.dtype == np.float32 and abs(len(samples) - 16000) <= 40
    assert rec.start_latency is not None
    assert rec.stop().size == 0  # a second stop has nothing


def test_stop_waits_for_the_last_buffer(monkeypatch):
    monkeypatch.setattr(recorder, "TAIL_WAIT", 2.0)
    src = FakeInput(rate=16000.0)
    rec = Recorder(source=src)
    rec.start()
    src.push(np.ones(1600, dtype=np.float32) * 0.1)
    threading.Timer(0.05, lambda: src.push(np.ones(1600, dtype=np.float32) * 0.1)).start()
    t = time.monotonic()
    samples = rec.stop()
    assert len(samples) == 3200
    assert time.monotonic() - t < 1.0  # returned when the buffer came, not at the limit


def test_cancel_drops_the_audio():
    src = FakeInput()
    rec = Recorder(source=src)
    rec.start()
    src.push(tone(500, 48000, 0.5))
    rec.cancel()
    assert src.closed == 1
    assert rec.stop().size == 0
    rec.start()  # and it can record again
    assert src.opened == 2


def test_chunks_after_stop_are_ignored():
    src = FakeInput(rate=16000.0)
    rec = Recorder(source=src)
    rec.start()
    on_chunk = src.on_chunk
    src.push(np.full(1600, 0.1, dtype=np.float32))
    first = rec.stop()
    on_chunk(np.full(1600, 0.1, dtype=np.float32), 16000.0)  # a late buffer from the old recording
    rec.start()
    assert len(first) == 1600 and rec.stop().size == 0


def test_the_time_cap_ends_the_recording_by_itself():
    src = FakeInput(rate=16000.0)
    rec = Recorder(max_seconds=1.0, source=src)
    ended = threading.Event()
    rec.start(on_stopped=ended.set)
    for _ in range(12):
        src.push(np.full(1600, 0.1, dtype=np.float32))
    assert ended.wait(2)
    assert not rec.is_recording() and src.closed == 1
    samples = rec.stop()
    assert len(samples) == 16000  # capped exactly; the extra buffers were dropped


def test_a_lost_device_keeps_what_was_captured():
    src = FakeInput(rate=16000.0)
    rec = Recorder(source=src)
    ended = []
    rec.start(on_stopped=lambda: ended.append(1))
    src.push(np.full(8000, 0.1, dtype=np.float32))
    src.on_lost()
    assert ended == [1] and not rec.is_recording()
    assert len(rec.stop()) == 8000


def test_a_rate_change_mid_recording_is_resampled_from_the_new_rate(caplog):
    """A Bluetooth headset switching profile: 0.5 s at 48 kHz, then 0.5 s at 16 kHz, in one recording."""
    src = FakeInput(rate=48000.0)
    rec = Recorder(source=src)
    rec.start()
    first = tone(1000, 48000, 0.5)
    for k in range(0, len(first), 4800):
        src.push(first[k:k + 4800])
    src.rate = 16000.0
    second = tone(1000, 16000, 0.5)
    with caplog.at_level("INFO", logger="lyssna.dictation.recorder"):
        for k in range(0, len(second), 1600):
            src.push(second[k:k + 1600])
    samples = rec.stop()
    assert samples.dtype == np.float32 and abs(len(samples) - 16000) <= 40
    assert abs(dominant_frequency(samples[1000:7000]) - 1000) < 10
    assert abs(dominant_frequency(samples[9000:15000]) - 1000) < 10
    assert 0.45 < np.max(np.abs(samples[9000:15000])) < 0.55
    assert [r.getMessage() for r in caplog.records if "rate changed" in r.getMessage()] == [
        "microphone rate changed from 48000 Hz to 16000 Hz"]


def test_ended_by_says_why_the_recording_ended():
    src = FakeInput(rate=16000.0)
    rec = Recorder(source=src)
    rec.start()
    src.on_lost()
    assert rec.ended_by == "device"
    rec.stop()
    rec.start()
    assert rec.ended_by is None
    rec.stop()
    assert rec.ended_by is None  # stopped by the caller, not by itself


def test_level_callbacks_arrive_while_recording():
    src = FakeInput(rate=16000.0)
    rec = Recorder(source=src)
    levels = []
    rec.start(on_level=levels.append)
    src.push(tone(300, 16000, 0.1, 0.3))
    # The meter thread ticks every 1/LEVEL_HZ s; on a busy CI machine it can be late, so wait for
    # two ticks (bounded) rather than sleeping a fixed time.
    deadline = time.monotonic() + 3.0
    while len(levels) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    rec.stop()
    count = len(levels)
    assert count >= 2 and max(levels) > 0.3
    time.sleep(0.15)
    assert len(levels) == count  # and stop after the recording


def test_a_failed_start_leaves_the_recorder_usable():
    rec = Recorder(source=FakeInput(fail=RecorderError("No microphone found")))
    with pytest.raises(RecorderError):
        rec.start()
    assert not rec.is_recording()
    rec.source.fail = None
    rec.start()
    assert rec.is_recording()


def test_starting_twice_is_refused():
    rec = Recorder(source=FakeInput())
    rec.start()
    with pytest.raises(RecorderError):
        rec.start()


def test_warm_never_asks_for_the_microphone(monkeypatch):
    src = FakeInput()
    warmed = []
    src.warm = lambda: warmed.append(1)
    rec = Recorder(source=src)
    monkeypatch.setattr(recorder, "microphone_status", lambda: "undetermined")
    rec.warm()
    assert warmed == []
    monkeypatch.setattr(recorder, "microphone_status", lambda: "granted")
    rec.warm()
    assert warmed == [1]
