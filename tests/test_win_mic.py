"""The Windows microphone (lyssna/win/mic.py) without a microphone or Qt: the byte conversion, the
privacy switches, and the open, close and lost bookkeeping on a fake Qt side, plus one run of
the real Recorder on a synthetic 48 kHz stereo tone."""

import subprocess
import sys
import time

import numpy as np
import pytest

from lyssna.dictation.recorder import Recorder, RecorderError
from lyssna.win import mic
from lyssna.win.mic import EngineInput, _Decoder, _Format, _to_mono

STEREO_48K = _Format(48000, 2, "Int16", "Fake microphone")


def stereo_int16_tone(freq=1000.0, rate=48000, seconds=1.0, amp=0.3) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    wave = np.round(amp * 32767 * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    return np.column_stack((wave, wave)).tobytes()  # interleaved left, right


class FakeQt:
    """Stands in for _QtInput: records the calls and lets a test push bytes or lose the device."""

    def __init__(self, fmt=STEREO_48K, fail: Exception | None = None):
        self.fmt, self.fail = fmt, fail
        self.warmed = 0
        self.starts = 0
        self.stops = 0
        self.on_data = self.on_lost = None

    def warm(self):
        self.warmed += 1

    def start(self, on_data, on_lost):
        if self.fail is not None:
            raise self.fail
        self.starts += 1
        self.on_data, self.on_lost = on_data, on_lost
        return self.fmt

    def stop(self):
        self.stops += 1

    def feed(self, data: bytes):
        self.on_data(data, self.fmt)

    def lose(self):
        self.on_lost()


# -- the conversion ---------------------------------------------------------------------------

def test_int16_mono_maps_to_minus_one_to_one():
    data = np.array([0, 16384, -16384, 32767, -32768], dtype=np.int16).tobytes()
    out = _to_mono(data, "Int16", 1)
    assert out.dtype == np.float32
    assert out.tolist() == pytest.approx([0.0, 0.5, -0.5, 32767 / 32768, -1.0])


def test_int16_stereo_averages_the_channels():
    data = np.array([16384, 0, -32768, 32767, 8192, 8192], dtype=np.int16).tobytes()
    out = _to_mono(data, "Int16", 2)
    assert out.dtype == np.float32 and len(out) == 3
    assert out.tolist() == pytest.approx([0.25, -0.5 / 32768, 0.25])


def test_float_mono_passes_through():
    samples = np.array([0.0, 0.25, -1.0, 1.0], dtype=np.float32)
    out = _to_mono(samples.tobytes(), "Float", 1)
    assert out.dtype == np.float32
    assert out.tolist() == samples.tolist()


def test_float_stereo_averages_the_channels():
    data = np.array([1.0, 0.0, -0.5, -0.5, 0.2, 0.4], dtype=np.float32).tobytes()
    assert _to_mono(data, "Float", 2).tolist() == pytest.approx([0.5, -0.5, 0.3])


def test_int32_and_uint8_map_to_minus_one_to_one():
    assert _to_mono(np.array([2**30, -(2**31)], dtype=np.int32).tobytes(), "Int32", 1).tolist() == pytest.approx([0.5, -1.0])
    assert _to_mono(bytes([128, 192, 0]), "UInt8", 1).tolist() == pytest.approx([0.0, 0.5, -1.0])


def test_an_unknown_sample_format_is_refused():
    with pytest.raises(ValueError):
        _to_mono(b"\0\0", "Unknown", 1)


def test_the_decoder_keeps_a_partial_frame_for_the_next_read():
    data = np.array([16384, 16384, -16384, -16384], dtype=np.int16).tobytes()  # two stereo frames, 8 bytes
    decoder = _Decoder(STEREO_48K)
    first = decoder.push(data[:5])
    assert first.tolist() == pytest.approx([0.5])
    second = decoder.push(data[5:])
    assert second.tolist() == pytest.approx([-0.5])
    assert decoder.push(b"").size == 0


# -- the privacy switches ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("everyone", "desktop", "expected"),
    [
        ("Allow", "Allow", "granted"),
        ("Deny", "Allow", "denied"),
        ("Allow", "Deny", "denied"),
        ("Deny", "Deny", "denied"),
        (None, None, "granted"),       # never touched: Windows allows
        (None, "Deny", "denied"),
        ("Deny", None, "denied"),
        ("Allow", None, "granted"),
        ("deny ", "Allow", "denied"),  # case and spaces do not matter
        ("Prompt", "Allow", "granted"),  # anything but Deny is not a block
    ],
)
def test_microphone_status_reads_both_switches(everyone, desktop, expected):
    values = {mic.CONSENT_KEY: everyone, mic.DESKTOP_APPS_KEY: desktop}
    assert mic.microphone_status(values.get) == expected


def test_microphone_status_never_says_undetermined_here():
    assert mic.microphone_status(lambda _key: None) == "granted"


def test_request_microphone_answers_at_once(monkeypatch):
    answers = []
    monkeypatch.setattr(mic, "microphone_status", lambda: "denied")
    mic.request_microphone(answers.append)
    monkeypatch.setattr(mic, "microphone_status", lambda: "granted")
    mic.request_microphone(answers.append)
    assert answers == [False, True]


# -- open, close and a lost device ------------------------------------------------------------

def test_open_returns_the_device_rate_and_delivers_mono_chunks():
    qt = FakeQt()
    source = EngineInput(qt)
    chunks = []
    assert source.open(lambda chunk, rate: chunks.append((chunk, rate)), lambda: None) == 48000.0
    qt.feed(stereo_int16_tone(seconds=0.03))
    assert len(chunks) == 1
    chunk, rate = chunks[0]
    assert rate == 48000.0 and chunk.dtype == np.float32 and len(chunk) == 1440


def test_a_start_failure_raises_a_recorder_error():
    source = EngineInput(FakeQt(fail=RuntimeError("Qt refused")))
    with pytest.raises(RecorderError, match="could not start"):
        source.open(lambda c, r: None, lambda: None)


def test_no_microphone_keeps_its_own_message():
    source = EngineInput(FakeQt(fail=RecorderError("No microphone found")))
    with pytest.raises(RecorderError, match="No microphone found"):
        source.open(lambda c, r: None, lambda: None)


def test_lost_fires_once_and_lets_the_device_go():
    qt = FakeQt()
    source = EngineInput(qt)
    lost, chunks = [], []
    source.open(lambda c, r: chunks.append(c), lambda: lost.append(1))
    qt.lose()
    qt.lose()
    assert lost == [1]
    assert qt.stops == 1
    qt.feed(stereo_int16_tone(seconds=0.03))
    assert chunks == []


def test_close_twice_is_safe_and_chunks_stop_after_close():
    qt = FakeQt()
    source = EngineInput(qt)
    chunks, lost = [], []
    source.open(lambda c, r: chunks.append(c), lambda: lost.append(1))
    qt.feed(stereo_int16_tone(seconds=0.03))
    source.close()
    source.close()
    qt.feed(stereo_int16_tone(seconds=0.03))
    qt.lose()
    assert len(chunks) == 1 and lost == []
    assert qt.stops == 2


def test_a_late_chunk_from_the_last_recording_does_not_reach_the_next():
    qt = FakeQt()
    source = EngineInput(qt)
    first, second = [], []
    source.open(lambda c, r: first.append(c), lambda: None)
    old_feed = qt.on_data
    source.close()
    source.open(lambda c, r: second.append(c), lambda: None)
    old_feed(stereo_int16_tone(seconds=0.03), qt.fmt)
    qt.feed(stereo_int16_tone(seconds=0.03))
    assert first == [] and len(second) == 1


def test_warm_only_prepares_qt():
    qt = FakeQt()
    EngineInput(qt).warm()
    assert qt.warmed == 1 and qt.starts == 0


# -- with the real Recorder -------------------------------------------------------------------

def test_the_recorder_turns_a_48khz_stereo_tone_into_16khz_samples():
    qt = FakeQt()
    levels = []
    recorder = Recorder(source=EngineInput(qt))
    recorder.start(on_level=levels.append)
    data = stereo_int16_tone(seconds=1.0)
    step = 1440 * 4  # 30 ms of 16-bit stereo, as the Qt timer reads it
    for k in range(0, len(data), step):
        qt.feed(data[k:k + step])
    deadline = time.monotonic() + 2.0
    while not levels and time.monotonic() < deadline:
        time.sleep(0.01)
    samples = recorder.stop()
    assert samples.dtype == np.float32
    assert abs(len(samples) - 16000) <= 40
    assert 0.25 < float(np.max(np.abs(samples[1000:15000]))) < 0.35
    assert levels and max(levels) > 0.8  # a 0.3 sine is about -13.5 dB, near the top of the meter
    assert qt.stops == 1


def test_a_lost_device_ends_the_recording_but_keeps_the_audio():
    qt = FakeQt()
    stopped = []
    recorder = Recorder(source=EngineInput(qt))
    recorder.start(on_stopped=lambda: stopped.append(1))
    qt.feed(stereo_int16_tone(seconds=0.5))
    qt.lose()
    assert stopped == [1] and not recorder.is_recording()
    assert abs(len(recorder.stop()) - 8000) <= 40


def test_calls_reach_the_shared_audio_thread_and_come_back():
    """The Qt side runs on lyssna/win/audio.py's thread: values and errors come back, and a call
    made on that thread runs at once instead of waiting for itself. No device is touched."""
    pytest.importorskip("PySide6.QtMultimedia")
    import threading

    from lyssna.win import audio

    assert audio.run_on_audio_thread(lambda: threading.current_thread().name, 5.0) == "lyssna-audio"
    with pytest.raises(KeyError):
        audio.run_on_audio_thread(lambda: {}["missing"], 5.0)
    assert audio.run_on_audio_thread(lambda: audio.run_on_audio_thread(lambda: 7, 5.0), 5.0) == 7


def test_the_module_imports_without_qt():
    code = "import sys, lyssna.win.mic; assert not [m for m in sys.modules if m.startswith('PySide6')]"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
