"""A recorded sentence in place of the microphone, and the models in the test profile.

Inside the app's process (launch.py --mic DIR): the recorder gets its input from host.mic's
EngineInput (lyssna/dictation/recorder.py, Recorder.source), so the harness puts FileInput there.
It delivers what the harness left in DIR the way the Mac's microphone does: mono float32 chunks
of 4800 frames at 48 kHz, one every 0.1 s, in real time, from the moment the recorder opens it;
after the file, quiet room noise until the recorder closes it. The file is DIR/next.wav (16-bit
mono, any rate; made offline with `say -o` and `afconvert`), read afresh at every open, so a
scenario changes what is "said" by replacing it; no file means silence. Microphone access counts
as granted, so macOS is never asked.

DIR/fail-next makes the next transcription fail the way a model error does (TranscriberError,
which the pipeline turns into a retryable failure); the marker is used up by that one failure.

On the harness's side: link_models() clones the speech, cleanup and voice files into a profile
(an APFS clone, so nothing of the owner's folder is opened for writing and no space is used),
and make_sentence() speaks a sentence into a wav with the system voice, offline."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

log = logging.getLogger("behave.probe")  # its timed lines are read as probes (live.read_log)

RATE = 48000
CHUNK = 4800  # what macOS delivers whatever size is asked for (lyssna/mac/mic.py)
NOISE = 0.0005  # quiet room noise after the sentence, below the session's silence threshold

OWNER_DATA = Path.home() / "Library" / "Application Support" / "Lyssna"
MODEL_PARTS = ("dictation/models/parakeet-tdt-0.6b-v2", "dictation/models/s1-mini", "kokoro/kokoro-v1.0.fp16.onnx",
               "kokoro/voices-v1.0.bin")


# -- inside the app ---------------------------------------------------------------------------------


def _read_wav(path: Path):
    import numpy as np

    with wave.open(str(path)) as w:
        rate, frames = w.getframerate(), w.readframes(w.getnframes())
        width, channels = w.getsampwidth(), w.getnchannels()
    if width != 2:
        raise ValueError(f"{path.name}: 16-bit samples expected")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != RATE:  # plain linear interpolation is enough for speech made by `say`
        n = round(len(samples) * RATE / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples).astype(np.float32)
    return samples


class FileInput:
    """host.mic.EngineInput's interface (warm, open, close), fed from the harness's folder."""

    folder: Path = Path(".")

    def __init__(self):
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def warm(self) -> None:
        return None

    def open(self, on_chunk, on_lost) -> float:
        import numpy as np

        path = self.folder / "next.wav"
        speech = _read_wav(path) if path.is_file() else np.zeros(0, dtype=np.float32)
        log.debug("probe mic_open t=%.4f speech=%.2fs", time.time(), len(speech) / RATE)
        stop = self._stop = threading.Event()
        rng = np.random.default_rng(0)

        def run() -> None:
            start = time.monotonic()
            n = 0
            while not stop.is_set():
                lo = n * CHUNK
                chunk = speech[lo:lo + CHUNK]
                if len(chunk) < CHUNK:
                    chunk = np.concatenate((chunk, (rng.standard_normal(CHUNK - len(chunk)) * NOISE).astype(np.float32)))
                on_chunk(chunk.astype(np.float32), float(RATE))
                n += 1
                wait = start + n * CHUNK / RATE - time.monotonic()
                if wait > 0:
                    stop.wait(wait)

        self._thread = threading.Thread(target=run, daemon=True, name="behave-mic")
        self._thread.start()
        return float(RATE)

    def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(1.0)
        self._thread = None
        log.debug("probe mic_close t=%.4f", time.time())


def install(folder: str) -> None:
    """Put FileInput where the recorder looks for the microphone, grant access, and arm the
    failure marker. Called by launch.py before the app starts."""
    from lyssna.dictation.transcribers import base, parakeet
    from lyssna.mac import mic

    FileInput.folder = Path(folder)
    mic.EngineInput = FileInput
    mic.microphone_status = lambda: "granted"
    mic.request_microphone = lambda callback: callback(True)
    original = parakeet.ParakeetTranscriber.transcribe

    def transcribe(self, samples, language: str = "", hints=()):
        marker = FileInput.folder / "fail-next"
        if marker.exists():
            marker.unlink()
            log.debug("probe transcribe_fail t=%.4f", time.time())
            raise base.TranscriberError("The speech model could not transcribe this recording.")
        return original(self, samples, language, hints)

    parakeet.ParakeetTranscriber.transcribe = transcribe


# -- on the harness's side ----------------------------------------------------------------------------


def link_models(state_dir: Path, kokoro_dir: Path, source: Path | None = None) -> list[str]:
    """Clone the model files into a profile (APFS clones: copy on write, no space used, the
    owner's files only ever read): the dictation models under its state folder, the voice into
    `kokoro_dir`, which must be a short path (the app copies espeak's data next to the voice when
    the installed copy's path is long, and refuses a long target). Returns what was missing."""
    source = source or OWNER_DATA
    missing = []
    for part in MODEL_PARTS:
        src = source / part
        dst = kokoro_dir / Path(part).name if part.startswith("kokoro/") else state_dir / part
        if not src.exists():
            missing.append(part)
            continue
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-c", "-R", str(src), str(dst)], check=True)
    return missing


def make_sentence(text: str, path: Path, voice: str = "Samantha") -> float:
    """Speak `text` into a 48 kHz 16-bit mono wav with the system voice (offline). Seconds long."""
    path.parent.mkdir(parents=True, exist_ok=True)
    aiff = path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(aiff), str(path)], check=True)
    aiff.unlink(missing_ok=True)
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def say_next(folder: Path, wav: Path | None) -> None:
    """What the next recording hears: `wav`, or silence (None)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "next.wav"
    if wav is None:
        target.unlink(missing_ok=True)
    else:
        shutil.copyfile(wav, target)
