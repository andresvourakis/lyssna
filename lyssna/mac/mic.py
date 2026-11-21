"""The microphone on the Mac: its permission, and AVAudioEngine with a tap on the input node,
at whatever rate and channel count the hardware gives. lyssna.dictation.recorder turns what
this delivers into 16 kHz samples; it reaches this module through lyssna.host.

Each buffer is mixed down to mono in the tap callback and handed on at the hardware rate. The
rate conversion happens in numpy in the recorder rather than in AVAudioConverter, because the
converter's rate conversion only exists in the pull-style
`convertToBuffer:error:withInputFromBlock:` form, whose block returns a buffer through an
out-parameter: fragile across the PyObjC bridge and hard to test.

The engine object is kept between dictations (creating it and its input unit is the slow part
of start-up), but it is stopped after every recording, which releases the microphone so the
orange indicator goes off. When the input device changes mid-recording, AVAudioEngine stops
itself and posts a configuration change: we end the recording there, keep what was captured,
and build a fresh engine next time. AVFoundation loads inside the functions."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np

from ..dictation.recorder import RecorderError

log = logging.getLogger("lyssna.mac.mic")

TAP_FRAMES = 1024     # a hint; macOS often delivers bigger buffers (about 0.1 s)


def microphone_status() -> str:
    """"granted", "denied" or "undetermined" for this app's microphone access. A device
    restricted by an administrator counts as denied (the user cannot change it)."""
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

    status = int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
    if status == 3:  # AVAuthorizationStatusAuthorized
        return "granted"
    if status == 0:  # AVAuthorizationStatusNotDetermined
        return "undetermined"
    return "denied"  # 1 restricted, 2 denied


def request_microphone(callback: Callable[[bool], None]) -> None:
    """Ask for microphone access (macOS shows its prompt only while undetermined). The
    callback gets True or False, on an arbitrary thread."""
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

    AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda granted: callback(bool(granted)))



class EngineInput:
    """The default microphone through AVAudioEngine. open() starts delivering mono float32
    chunks at the hardware rate to `on_chunk` (on an audio thread); close() stops the engine
    and with it the microphone. `on_lost` fires when the device goes away mid-recording.

    Measured on an M3 Pro (built-in microphone, 48 kHz): creating the input unit takes about
    150 ms, installing the tap 20 to 50 ms, prepare() about 40 ms, start 45 to 75 ms, and the
    first buffer arrives 100 ms after that (macOS delivers 4800-frame buffers whatever size
    is asked for; the audio in it starts at engine start, so nothing is lost). So the engine,
    its input unit and the tap are built once and the engine is prepared again after every
    recording: start() then only pays for the start itself. A prepared engine does not run
    the device (CoreAudio reports it idle), so the microphone indicator stays off."""

    def __init__(self):
        self._engine = None
        self._observer = None
        self._rate = 0.0
        self._sink: Callable[[np.ndarray, float], None] | None = None
        self._on_lost: Callable[[], None] | None = None
        self._lock = threading.RLock()  # a device change can be announced from inside engine.stop()

    def _build(self) -> None:
        from AVFoundation import AVAudioEngine, AVAudioEngineConfigurationChangeNotification
        from Foundation import NSNotificationCenter

        engine = AVAudioEngine.alloc().init()
        node = engine.inputNode()
        fmt = node.outputFormatForBus_(0)
        rate, channels = float(fmt.sampleRate()), int(fmt.channelCount())
        if rate <= 0 or channels <= 0:
            raise RecorderError("No microphone found")

        def tap(buffer, when) -> None:
            sink = self._sink
            frames = int(buffer.frameLength())
            if sink is None or frames <= 0:
                return
            data = buffer.floatChannelData()
            if data is None:
                return
            n = min(channels, len(data))
            mono = np.frombuffer(data[0].as_buffer(frames), dtype=np.float32).copy()
            for c in range(1, n):
                mono += np.frombuffer(data[c].as_buffer(frames), dtype=np.float32)
            if n > 1:
                mono /= n
            sink(mono, rate)

        def changed(note) -> None:
            # The engine stops itself when the input device changes (unplugged, switched).
            # If it is still running this was some other reconfiguration: ignore it.
            if engine.isRunning():
                return
            with self._lock:
                was_open = self._sink is not None
                self._sink = None
                if self._engine is engine:
                    self._forget_engine()
                on_lost = self._on_lost
            if was_open and on_lost is not None:
                on_lost()

        node.installTapOnBus_bufferSize_format_block_(0, TAP_FRAMES, fmt, tap)
        engine.prepare()
        self._observer = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            AVAudioEngineConfigurationChangeNotification, engine, None, changed
        )
        self._engine, self._rate = engine, rate

    def _forget_engine(self) -> None:
        """Drop the engine; the next open() builds a fresh one for whatever device is current."""
        from Foundation import NSNotificationCenter

        engine, self._engine = self._engine, None
        if self._observer is not None:
            NSNotificationCenter.defaultCenter().removeObserver_(self._observer)
        self._observer = None
        if engine is not None:
            try:
                engine.inputNode().removeTapOnBus_(0)
                engine.stop()
            except Exception:
                log.debug("releasing the old engine failed", exc_info=True)

    def warm(self) -> None:
        """Build and prepare the engine ahead of time without running the microphone. Needs
        microphone access already granted: creating the input unit otherwise makes macOS ask."""
        with self._lock:
            if self._engine is None:
                self._build()

    def open(self, on_chunk: Callable[[np.ndarray, float], None], on_lost: Callable[[], None]) -> float:
        with self._lock:
            if self._engine is None:
                self._build()
            self._on_lost = on_lost
            self._sink = on_chunk
            ok, err = self._engine.startAndReturnError_(None)
            if not ok:
                self._sink = None
                self._forget_engine()
                log.warning("microphone did not start: %s", type(err).__name__ if err is not None else "unknown")
                raise RecorderError("The microphone could not start")
            return self._rate

    def close(self) -> None:
        with self._lock:
            self._sink = None
            engine = self._engine
            if engine is None:
                return
            try:
                engine.stop()     # releases the microphone; the indicator goes off
                engine.prepare()  # ready for a fast start next time, device still idle
            except Exception:
                log.debug("closing the microphone failed", exc_info=True)
                self._forget_engine()
