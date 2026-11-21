"""The microphone on Windows: its privacy switch, and Qt Multimedia's QAudioSource on the default
input, at whatever rate and channel count the device gives. The Mac twin is lyssna/mac/mic.py;
lyssna.dictation.recorder turns what either delivers into 16 kHz samples and reaches this
module through lyssna.host.

Why it is built this way. QAudioSource and the QIODevice it reads from must live on one thread
that runs a Qt event loop, while open() and close() are called from the dictation worker and the
command line has no GUI loop at all. lyssna/win/audio.py already runs such a thread for
playback, so the microphone shares it (audio.run_on_audio_thread) instead of starting a second
one. Every Qt object here is made, read and destroyed on that thread; open() and close() wait
for it, so open() returns only once capture has started or failed, and close() returns only
once the device is let go (the Windows microphone-in-use indicator goes off then).

On that thread a timer reads whatever the device has captured every READ_MS milliseconds, turns
the bytes into mono float32 and hands the chunk to the recorder at the device's own rate. A
timer rather than readyRead because the probe program proved the timer on Windows, and because
it gives chunks of an even size, so the level meter moves smoothly. The recorder's callback is
cheap (it resamples one chunk), so calling it on the audio thread does not hold up playback.

The format is the device's preferred one, kept as float or 16-bit when the device offers either;
the channels are averaged to mono here and the rate is left to the recorder's resampler, as on
the Mac. The pure parts (the byte conversion, the open, close and lost bookkeeping, the privacy
switch) take no Qt and are tested on any computer. Qt and winreg load inside the functions."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..dictation.recorder import RecorderError

log = logging.getLogger("lyssna.win.mic")

READ_MS = 30          # how often the audio thread reads the device: chunks of about 30 ms
BUFFER_MS = 250       # Qt's capture buffer; roomy, so a late read never drops audio
CALL_TIMEOUT = 5.0    # seconds open() and close() wait for the audio thread

CONSENT_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
DESKTOP_APPS_KEY = CONSENT_KEY + r"\NonPackaged"  # the "Let desktop apps access your microphone" switch

_SAMPLE_TYPES = {"UInt8": np.uint8, "Int16": np.int16, "Int32": np.int32, "Float": np.float32}


# -- the privacy switch ----------------------------------------------------------------------

def _read_consent(subkey: str) -> str | None:
    """The `Value` under HKEY_CURRENT_USER\\<subkey>, or None when the key or value is missing
    (or this is not Windows, as when the manual probe runs on a Mac)."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            value, _kind = winreg.QueryValueEx(key, "Value")
    except OSError:
        return None
    return str(value)


def microphone_status(read: Callable[[str], str | None] = _read_consent) -> str:
    """"granted" or "denied". Windows asks no per-app question of ordinary desktop apps: two
    switches in Settings > Privacy & security > Microphone allow or block all apps and all
    desktop apps, stored as "Allow" or "Deny" in the registry. Either one at "Deny" means
    denied. A missing key or value counts as granted, because that is what Windows does when
    the switches were never touched. "undetermined" never happens here. `read` is for tests."""
    for subkey in (CONSENT_KEY, DESKTOP_APPS_KEY):
        if (read(subkey) or "").strip().lower() == "deny":
            return "denied"
    return "granted"


def request_microphone(callback: Callable[[bool], None]) -> None:
    """Windows has nothing to ask: the callback gets the switches' current answer at once."""
    callback(microphone_status() == "granted")


# -- bytes to mono float32 -------------------------------------------------------------------

@dataclass(frozen=True)
class _Format:
    """What the device delivers: the rate in Hz, the channel count and Qt's sample format name
    (UInt8, Int16, Int32 or Float). `device` is its description, for the log and the probe."""

    rate: int
    channels: int
    sample: str
    device: str = ""


def _to_mono(data: bytes, sample: str, channels: int) -> np.ndarray:
    """Interleaved frames in one of Qt's sample formats to mono float32 in -1..1, the channels
    averaged. `data` must hold whole frames."""
    kind = _SAMPLE_TYPES.get(sample)
    if kind is None:
        raise ValueError(f"unsupported sample format {sample!r}")
    raw = np.frombuffer(data, dtype=kind)
    if sample == "Float":
        x = raw.astype(np.float32)
    elif sample == "UInt8":
        x = (raw.astype(np.float32) - 128.0) / 128.0
    else:
        x = raw.astype(np.float32) / float(-np.iinfo(kind).min)
    channels = max(1, int(channels))
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1, dtype=np.float32)
    return x


class _Decoder:
    """Turns the byte stream of one recording into mono chunks. A read can end part-way through
    a frame; the leftover bytes wait for the next read."""

    def __init__(self, fmt: _Format):
        self.fmt = fmt
        self._frame = np.dtype(_SAMPLE_TYPES[fmt.sample]).itemsize * max(1, fmt.channels)
        self._rest = b""

    def push(self, data: bytes) -> np.ndarray:
        data = self._rest + bytes(data)
        whole = len(data) - len(data) % self._frame
        self._rest = data[whole:]
        return _to_mono(data[:whole], self.fmt.sample, self.fmt.channels)


# -- the microphone the recorder holds -------------------------------------------------------

class EngineInput:
    """The default microphone through Qt. open() starts delivering mono float32 chunks at the
    device's rate to `on_chunk` (on the audio thread) and returns that rate; close() stops
    capture and lets the device go. `on_lost` fires once when the device goes away or fails
    mid-recording; the recorder then keeps what it has. Each open() takes whatever input is
    the default at that moment, so a new headset is picked up on the next recording.

    `backend` is the part that talks to Qt (_QtInput unless a test passes a fake); this class
    keeps the bookkeeping, so a late chunk or a second loss after close() never gets through."""

    def __init__(self, backend=None):
        self._backend = backend if backend is not None else _QtInput()
        self._lock = threading.RLock()  # close() can be called from inside on_lost, on the audio thread
        self._generation = 0
        self._sink: Callable[[np.ndarray, float], None] | None = None
        self._on_lost: Callable[[], None] | None = None
        self._decoder: _Decoder | None = None

    def warm(self) -> None:
        """Start the audio thread and load Qt Multimedia ahead of time, so the first open() is
        fast. It lists the input devices but never opens one, so it never runs the microphone."""
        self._backend.warm()

    def open(self, on_chunk: Callable[[np.ndarray, float], None], on_lost: Callable[[], None]) -> float:
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._sink, self._on_lost, self._decoder = on_chunk, on_lost, None
        try:
            fmt = self._backend.start(lambda data, fmt: self._data(gen, data, fmt), lambda: self._lost(gen))
        except Exception as e:
            with self._lock:
                if gen == self._generation:
                    self._sink = self._on_lost = None
            if isinstance(e, RecorderError):
                raise
            log.warning("microphone did not start: %s", type(e).__name__)
            raise RecorderError("The microphone could not start") from e
        return float(fmt.rate)

    def close(self) -> None:
        with self._lock:
            self._generation += 1
            self._sink = self._on_lost = None
            self._decoder = None
        try:
            self._backend.stop()  # releases the microphone; the indicator goes off
        except Exception:
            log.debug("closing the microphone failed", exc_info=True)

    def _data(self, gen: int, data: bytes, fmt: _Format) -> None:
        with self._lock:  # held while the sink runs, so nothing arrives once close() has returned
            sink = self._sink
            if gen != self._generation or sink is None:
                return
            if self._decoder is None or self._decoder.fmt != fmt:
                self._decoder = _Decoder(fmt)
            chunk = self._decoder.push(data)
            if chunk.size:
                sink(chunk, float(fmt.rate))

    def _lost(self, gen: int) -> None:
        with self._lock:
            if gen != self._generation or self._sink is None:
                return
            self._sink = None
            on_lost, self._on_lost = self._on_lost, None
        log.info("the microphone went away mid-recording")
        try:
            self._backend.stop()
        except Exception:
            log.debug("letting the lost microphone go failed", exc_info=True)
        if on_lost is not None:
            on_lost()


# -- Qt, on the audio thread -----------------------------------------------------------------

def _pick_format(device):
    """The device's preferred format, as float or 16-bit when the device supports either at its
    own rate and channel count (those two convert most simply and lose nothing that matters)."""
    from PySide6.QtMultimedia import QAudioFormat

    preferred = device.preferredFormat()
    if preferred.sampleFormat() in (QAudioFormat.SampleFormat.Float, QAudioFormat.SampleFormat.Int16):
        return preferred
    for sample in (QAudioFormat.SampleFormat.Float, QAudioFormat.SampleFormat.Int16):
        fmt = QAudioFormat(preferred)
        fmt.setSampleFormat(sample)
        if device.isFormatSupported(fmt):
            return fmt
    return preferred


class _QtInput:
    """The Qt side: one QAudioSource at a time, made and destroyed on the audio thread. start()
    and stop() may be called from any thread; they run on the audio thread and wait for it."""

    def __init__(self):
        self._source = None
        self._io = None
        self._timer = None
        self._devices = None    # a QMediaDevices, for the inputs-changed signal
        self._device_id = b""
        self._token = 0         # which start() the Qt signals belong to
        self._lost_callback: tuple[int, Callable[[], None]] | None = None

    def _run(self, fn):
        try:
            from . import audio
        except ImportError as e:
            raise RecorderError("The microphone could not start") from e
        try:
            return audio.run_on_audio_thread(fn, CALL_TIMEOUT)
        except RecorderError:
            raise
        except Exception as e:  # PySide6 missing, the thread did not start, or it did not answer
            log.warning("the audio thread failed: %s: %s", type(e).__name__, e)
            raise RecorderError("The microphone could not start") from e

    def warm(self) -> None:
        self._run(self._ensure_devices)

    def start(self, on_data: Callable[[bytes, _Format], None], on_lost: Callable[[], None]) -> _Format:
        return self._run(lambda: self._start(on_data, on_lost))

    def stop(self) -> None:
        self._run(self._stop)

    def _ensure_devices(self):
        from PySide6.QtMultimedia import QMediaDevices

        if self._devices is None:
            self._devices = QMediaDevices()
            self._devices.audioInputsChanged.connect(self._inputs_changed)
        return self._devices

    def _start(self, on_data, on_lost) -> _Format:
        from PySide6.QtCore import QTimer
        from PySide6.QtMultimedia import QAudioSource, QMediaDevices, QtAudio

        self._stop()
        self._ensure_devices()
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise RecorderError("No microphone found")
        qfmt = _pick_format(device)
        fmt = _Format(int(qfmt.sampleRate()), int(qfmt.channelCount()), qfmt.sampleFormat().name, device.description())
        if fmt.rate <= 0 or fmt.channels <= 0 or fmt.sample not in _SAMPLE_TYPES:
            raise RecorderError("No microphone found")

        self._token += 1
        token = self._token
        source = QAudioSource(device, qfmt)
        source.setBufferSize(qfmt.bytesForDuration(BUFFER_MS * 1000))

        def read() -> None:
            if token != self._token or self._io is None:
                return
            chunk = self._io.readAll()
            if chunk.size():
                on_data(bytes(chunk.data()), fmt)

        def state_changed(state) -> None:
            if token == self._token and state == QtAudio.State.StoppedState and source.error() != QtAudio.Error.NoError:
                log.warning("the microphone stopped: %s", source.error().name)
                on_lost()

        source.stateChanged.connect(state_changed)
        io = source.start()
        if io is None or source.error() != QtAudio.Error.NoError:
            log.warning("microphone did not start: %s", source.error().name)
            self._token += 1
            source.stop()
            source.deleteLater()
            raise RecorderError("The microphone could not start")
        timer = QTimer()
        timer.setInterval(READ_MS)
        timer.timeout.connect(read)
        timer.start()
        self._source, self._io, self._timer = source, io, timer
        self._device_id = bytes(device.id().data())
        self._lost_callback = (token, on_lost)
        log.info("microphone open: %s, %d Hz, %d channels, %s", fmt.device, fmt.rate, fmt.channels, fmt.sample)
        return fmt

    def _inputs_changed(self) -> None:
        """The list of inputs changed. If ours is no longer in it, the recording has lost it."""
        from PySide6.QtMultimedia import QMediaDevices

        lost = self._lost_callback
        if self._source is None or lost is None or lost[0] != self._token:
            return
        if self._device_id not in {bytes(d.id().data()) for d in QMediaDevices.audioInputs()}:
            log.warning("the microphone in use was removed")
            lost[1]()

    def _stop(self) -> None:
        self._token += 1
        self._lost_callback = None
        source, timer = self._source, self._timer
        self._source = self._io = self._timer = None
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if source is not None:
            try:
                source.stop()
            except Exception:
                log.debug("Qt audio source did not stop cleanly", exc_info=True)
            source.deleteLater()
