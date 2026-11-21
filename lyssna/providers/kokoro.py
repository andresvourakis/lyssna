"""Kokoro-82M running on this Mac through onnxruntime (CPU). Free, offline after a one-time
download, and the default engine for new installs.

The model files are not bundled: the first time the voice is needed they are fetched into
~/Library/Application Support/Lyssna/kokoro/ with pinned URLs and pinned SHA-256 hashes
(see lyssna.download). Text is turned into phonemes by espeak-ng, which ships inside the
espeakng-loader wheel, so no Homebrew install is needed.

Nothing here imports onnxruntime or kokoro_onnx until a reading actually needs them, so
listing engines, building menus and checking `configured()` stay instant."""

from __future__ import annotations

import importlib.util
import io
import logging
import shutil
import sys
import threading
import wave
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, NamedTuple

from .. import config, host
from ..download import DownloadError, fetch
from .base import Audio, Provider, ProviderError

log = logging.getLogger("lyssna.kokoro")


class ModelFile(NamedTuple):
    name: str
    url: str
    sha256: str
    size: int


_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
FILES: list[ModelFile] = [
    ModelFile("kokoro-v1.0.int8.onnx", f"{_RELEASE}/kokoro-v1.0.int8.onnx",
              "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c", 114_119_327),
    ModelFile("voices-v1.0.bin", f"{_RELEASE}/voices-v1.0.bin",
              "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d", 28_214_398),
]
MODEL_FILE, VOICES_FILE = (f.name for f in FILES)

DATA_DIR = config.STATE_DIR / "kokoro"  # tests point this into a temp dir

# espeak-ng copies its data path into a fixed-size buffer and calls exit(1), taking the whole
# app down, when the path is longer than about 155 bytes. Stay well under that.
MAX_ESPEAK_PATH = 140


def files_present() -> bool:
    """True when every model file is in place. Files only reach their final name after their
    hash was checked, so existence is enough."""
    return all((DATA_DIR / f.name).is_file() for f in FILES)


@lru_cache(maxsize=1)
def packages_installed() -> bool:
    """Whether the Python packages Kokoro needs are present, without importing them."""
    return all(importlib.util.find_spec(m) is not None for m in ("kokoro_onnx", "onnxruntime", "espeakng_loader"))


# -- espeak-ng location ------------------------------------------------------------------


_ESPEAK_LIBRARY = "espeak-ng.dll" if host.IS_WINDOWS else "libespeak-ng.dylib"  # its file name in the frozen app


def _bundled_espeak() -> tuple[str, str]:
    """(library, data dir) as shipped: inside the app bundle when frozen by PyInstaller
    (lyssna.spec puts the data at <_MEIPASS>/espeak-ng-data), else from the installed wheel."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return str(Path(base) / "espeakng_loader" / _ESPEAK_LIBRARY), str(Path(base) / "espeak-ng-data")
    import espeakng_loader

    return espeakng_loader.get_library_path(), espeakng_loader.get_data_path()


def _short_copy(data: Path) -> Path:
    """Copy the espeak data to a short path under our state dir, once. Copied to a temporary
    name first and renamed, so a half-finished copy is never mistaken for a complete one."""
    target = DATA_DIR / "espeak-ng-data"
    if len(str(target).encode()) > MAX_ESPEAK_PATH:
        raise ProviderError("Kokoro cannot start because the path to your home folder is too long.")
    if target.is_dir():
        return target
    tmp = DATA_DIR / "espeak-ng-data.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(data, tmp)
    tmp.rename(target)
    log.info("copied espeak data to %s (the original path was too long for espeak)", target)
    return target


def espeak_paths(source: Callable[[], tuple[str, str]] = _bundled_espeak) -> tuple[str, str]:
    """(library, data dir) for espeak-ng, with the data dir guaranteed short enough that
    espeak will not kill the process."""
    lib, data = source()
    if len(data.encode()) > MAX_ESPEAK_PATH:
        data = str(_short_copy(Path(data)))
    return lib, data


# -- download ----------------------------------------------------------------------------


class VoiceDownload:
    """The one-time model download, run on its own thread so the menu bar can show progress
    and a waiting reading can start as soon as it lands. Starting it twice joins the run in
    progress. Cancelling (on quit) keeps the partial file for next time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False  # flipped under the lock, so a late start() never misses its callback
        self._cancel = threading.Event()
        self._callbacks: list[Callable[[str], None]] = []
        self.done_bytes = 0
        self.total_bytes = sum(f.size for f in FILES)
        self.error = ""

    def active(self) -> bool:
        return self._running

    def progress(self) -> float | None:
        """Fraction done (0..1) while downloading, else None."""
        if not self.active():
            return None
        return min(1.0, self.done_bytes / self.total_bytes) if self.total_bytes else 0.0

    def start(self, on_done: Callable[[str], None] | None = None) -> None:
        """Begin downloading whatever is missing. on_done(error) runs on the download thread
        when it ends; error is "" on success. Does nothing (and reports success) if the files
        are already here."""
        with self._lock:
            if not self._running:
                if files_present():
                    if on_done:
                        on_done("")
                    return
                self._cancel.clear()
                self.error = ""
                self.done_bytes = 0
                self._callbacks = []
                self._running = True
                self._thread = threading.Thread(target=self._run, daemon=True, name="lyssna-voice-download")
                self._thread.start()
            if on_done:
                self._callbacks.append(on_done)

    def wait(self) -> str:
        """Block until the current download ends; returns its error ("" on success)."""
        t = self._thread
        if t is not None:
            t.join()
        return self.error

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        log.info("downloading the Kokoro voice into %s", DATA_DIR)
        error = ""
        finished = 0
        try:
            for f in FILES:
                dest = DATA_DIR / f.name
                if not dest.is_file():
                    fetch(f.url, dest, f.sha256, f.size, cancel=self._cancel,
                          on_progress=lambda n, base=finished: setattr(self, "done_bytes", base + n))
                finished += f.size
                self.done_bytes = finished
        except DownloadError as e:
            error = str(e)
        except Exception as e:  # never let the download thread die without telling anyone
            log.exception("voice download crashed")
            error = f"Voice download failed: {e}"
        if error:
            log.warning("voice download did not finish: %s", error)
        with self._lock:
            self.error = error
            self._running = False
            callbacks, self._callbacks = self._callbacks, []
        for cb in callbacks:
            try:
                cb(error)
            except Exception:
                log.exception("download callback failed")


DOWNLOAD = VoiceDownload()


def ensure_downloaded() -> None:
    """Make sure the model files are here, downloading them if needed (blocking)."""
    if files_present():
        return
    DOWNLOAD.start()
    error = DOWNLOAD.wait()
    if not files_present():
        raise ProviderError(error or "The Kokoro voice is not downloaded yet. Try again.")


# -- synthesis ---------------------------------------------------------------------------


def _make_engine(model_path: str, voices_path: str, lib_path: str, data_path: str):
    """Load the model. Tests replace this, so nothing in the test suite loads onnxruntime."""
    from kokoro_onnx import EspeakConfig, Kokoro

    return Kokoro(model_path, voices_path, espeak_config=EspeakConfig(lib_path=lib_path, data_path=data_path))


_engine = None
_load_lock = threading.Lock()
# One reading at a time: the player runs two fetch workers, and two simultaneous inferences
# only fight over the same CPU cores (espeak itself is also process-global).
_synth_lock = threading.Lock()


def _get_engine():
    global _engine
    ensure_downloaded()
    with _load_lock:
        if _engine is None:
            lib, data = espeak_paths()
            try:
                _engine = _make_engine(str(DATA_DIR / MODEL_FILE), str(DATA_DIR / VOICES_FILE), lib, data)
            except ProviderError:
                raise
            except Exception as e:
                raise ProviderError(f"Kokoro could not start: {e}") from e
        return _engine


def _wav(pcm, sample_rate: int) -> bytes:
    import numpy as np

    pcm16 = np.clip(np.asarray(pcm, dtype=np.float32) * 32767, -32768, 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm16)
    return buf.getvalue()


class KokoroProvider(Provider):
    """Kokoro-82M on this Mac. About one second of work per twelve seconds of speech on an
    M-series CPU; the model is loaded once and shared."""

    name = "kokoro"
    label = "On this Mac (Kokoro)"
    needs_key = False
    sends_text_off_device = False
    setup_hint = "140 MB download"
    max_chars = 1500
    price_per_million = 0.0
    default_voice = "af_heart"
    default_model = "kokoro-v1.0-int8"
    voices: ClassVar[list[tuple[str, str]]] = [
        ("af_heart", "Heart · US"), ("af_bella", "Bella · US"), ("af_nicole", "Nicole · US"),
        ("am_michael", "Michael · US"), ("am_fenrir", "Fenrir · US"),
        ("bf_emma", "Emma · UK"), ("bf_isabella", "Isabella · UK"), ("bm_george", "George · UK"),
    ]

    @classmethod
    def can_run(cls) -> bool:
        return packages_installed()

    @property
    def model(self) -> str:
        return self.default_model  # older configs name an MLX model here; there is only one now

    def configured(self, wait: bool = True) -> bool:
        return files_present()

    def prepare(self, on_error: Callable[[str], None] | None = None) -> None:
        if not files_present() and self.can_run():
            DOWNLOAD.start(on_done=lambda err: err and on_error and on_error(err))

    def synthesize(self, text: str) -> Audio:
        engine = _get_engine()
        lang = "en-gb" if self.voice.startswith("b") else "en-us"
        with _synth_lock:
            try:
                pcm, sample_rate = engine.create(text, voice=self.voice, speed=1.0, lang=lang)
            except Exception as e:  # anything from the model becomes a readable error
                raise ProviderError(f"Kokoro could not read this: {e}") from e
        if pcm is None or len(pcm) == 0:
            raise ProviderError("Kokoro produced no audio")
        return Audio(_wav(pcm, sample_rate), "wav")

