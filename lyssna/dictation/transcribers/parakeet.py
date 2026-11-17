"""Parakeet TDT 0.6B on this Mac, through onnx-asr on the onnxruntime the app already ships for
the Kokoro voice. CPU only, 4 threads: about 0.2 s for a 10 s clip on an M3 Pro, and the audio
never leaves the Mac. v2 is English only; v3 adds Spanish, Swedish and other European languages
and is used when `[parakeet] model = "v3"` or the dictation language is not English.

The model is loaded once and kept loaded (about 1 GB of memory); the first transcription after
launch may wait for that load unless a recording start warmed it up. Nothing here imports
onnxruntime or onnx_asr until a model is actually loaded, so menus stay instant."""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from collections.abc import Callable, Sequence
from functools import lru_cache

from ..models import PARAKEET_V2, PARAKEET_V3, ModelSet
from .base import SAMPLE_RATE, Transcriber, TranscriberError, audio_seconds, check_samples

log = logging.getLogger("lyssna.dictation")

THREADS = 4  # 4 to 5 threads get nearly all the gain; more spill onto the efficiency cores
_ONNX_ASR_NAMES = {PARAKEET_V2.name: "nemo-parakeet-tdt-0.6b-v2", PARAKEET_V3.name: "nemo-parakeet-tdt-0.6b-v3"}
# Languages v3 transcribes (its model card); v2 is English only.
V3_LANGUAGES = ("bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it", "lv", "lt", "mt",
                "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk")


@lru_cache(maxsize=1)
def packages_installed() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("onnx_asr", "onnxruntime", "numpy"))


def model_for(cfg: dict, language: str = "en") -> ModelSet:
    """v3 when asked for, or for a language other than English; else v2."""
    if str(cfg.get("model", "v2")).lower() == "v3" or (language or "en").split("-")[0].lower() != "en":
        return PARAKEET_V3
    return PARAKEET_V2


def _make_model(model: ModelSet):
    """Load a model. Tests replace this, so nothing in the test suite loads onnxruntime."""
    import onnx_asr
    import onnxruntime as rt

    options = rt.SessionOptions()
    options.intra_op_num_threads = THREADS
    return onnx_asr.load_model(_ONNX_ASR_NAMES[model.name], str(model.directory), quantization="int8",
                               sess_options=options, providers=["CPUExecutionProvider"])


_model = None  # (ModelSet name, loaded model)
_load_lock = threading.Lock()
_run_lock = threading.Lock()  # one transcription at a time; two would only fight over the cores
_warming: threading.Thread | None = None


def _get_model(model: ModelSet):
    global _model
    with _load_lock:
        if _model is None or _model[0] != model.name:
            _model = None  # let the old one go before loading the other version
            start = time.monotonic()
            try:
                loaded = _make_model(model)
            except Exception as e:
                log.warning("could not load %s: %s", model.name, type(e).__name__)
                raise TranscriberError(f"The speech model could not start: {type(e).__name__}") from e
            _model = (model.name, loaded)
            log.info("loaded %s in %.2fs", model.name, time.monotonic() - start)
        return _model[1]


class ParakeetTranscriber(Transcriber):
    name = "parakeet"
    label = "On this Mac (Parakeet)"
    needs_key = False
    sends_audio_off_device = False
    setup_hint = "630 MB download"
    price_per_hour = 0.0
    supports_hints = False
    languages = V3_LANGUAGES
    vendor = "NVIDIA"

    @classmethod
    def can_run(cls) -> bool:
        return packages_installed()

    def model(self, language: str = "") -> ModelSet:
        return model_for(self.cfg, language or self.language)

    def configured(self, wait: bool = True) -> bool:
        return self.model().present()

    def recipient(self) -> str:
        return "this Mac"

    def prepare(self, on_error: Callable[[str], None] | None = None) -> None:
        model = self.model()
        if not model.present() and self.can_run():
            model.download().start(on_done=lambda err: err and on_error and on_error(err))

    def warm_up(self) -> None:
        """Load the model in the background if it is downloaded and not loaded yet."""
        global _warming
        model = self.model()
        if not self.can_run() or not model.present() or (_model is not None and _model[0] == model.name):
            return
        if _warming is not None and _warming.is_alive():
            return

        def load():
            try:
                _get_model(model)
            except TranscriberError:
                pass  # reported again by the transcription that needs it

        _warming = threading.Thread(target=load, daemon=True, name="lyssna-parakeet-load")
        _warming.start()

    def ready(self, timeout: float | None = None) -> bool:
        t = _warming
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True

    def transcribe(self, samples, language: str = "", hints: Sequence[str] = ()) -> str:
        check_samples(samples)
        model = self.model(language)
        if not model.present():
            download = model.download()
            download.start()
            done = download.progress()
            progress = f" ({done:.0%} done)" if done is not None else ""
            raise TranscriberError(f"The speech model is still downloading{progress}. Try again when it is done.")
        engine = _get_model(model)
        import numpy as np

        audio = np.asarray(samples, dtype=np.float32)
        start = time.monotonic()
        with _run_lock:
            try:
                text = engine.recognize(audio, sample_rate=SAMPLE_RATE)
            except Exception as e:  # anything from the model becomes a readable error
                log.warning("transcription via parakeet failed: %s", type(e).__name__)
                raise TranscriberError("The speech model could not transcribe this recording.") from e
        log.info("transcribed %.1fs of audio via parakeet (%s) in %.2fs", audio_seconds(audio), model.name,
                 time.monotonic() - start)
        return (text or "").strip()
