"""Model files for dictation on this Mac: the speech model (Parakeet) and the cleanup model
(S1-mini). Same approach as the Kokoro voice (lyssna.providers.kokoro): nothing is bundled;
the first time a model is needed its files are fetched with pinned URLs, sizes and SHA-256
hashes (lyssna.download.fetch, which resumes a partial file and only puts a file in place
after its hash matched), into `STATE_DIR/dictation/models/<name>/`.

Each `ModelSet` also carries what its license asks the app to show: the model's name and
maker, the license and where to read it. S1-mini's license requires that it is always called
"S1-mini" by "Superwhisper", and its LICENSE and NOTICE files are downloaded next to it."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .. import config
from ..download import DownloadError, fetch

log = logging.getLogger("lyssna.dictation.models")


def models_dir() -> Path:
    return config.STATE_DIR / "dictation" / "models"


class ModelFile(NamedTuple):
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ModelSet:
    """One model: the files it needs and what its license asks us to show."""

    name: str  # folder name under models_dir()
    label: str  # the model's own name, as its license wants it written
    maker: str  # who made it, for "<label> by <maker>"
    files: tuple[ModelFile, ...]
    license: str
    license_url: str
    source_url: str  # the model card

    @property
    def directory(self) -> Path:
        return models_dir() / self.name

    def path(self, filename: str) -> Path:
        return self.directory / filename

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def present(self) -> bool:
        """True when every file is in place. Files only reach their final name after their hash
        was checked, so existence is enough."""
        return all(self.path(f.name).is_file() for f in self.files)

    def credit(self) -> str:
        """The name to show in the UI and in notices, e.g. "S1-mini by Superwhisper"."""
        return f"{self.label} by {self.maker}"

    def download(self) -> ModelDownload:
        """The download for this set; one per set, shared, so starting it twice joins the run."""
        with _downloads_lock:
            if self.name not in DOWNLOADS:
                DOWNLOADS[self.name] = ModelDownload(self)
            return DOWNLOADS[self.name]


# -- the pinned files ------------------------------------------------------------------------

_PARAKEET_V2 = "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a3365852604aef28b538a8f066f4ccaa85"
_PARAKEET_V3 = "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"
_S1_MINI = "https://huggingface.co/superwhisper/s1-mini-GGUF/resolve/34add00a48a2e5d24e5a4ee5405a99620a3a240c"

PARAKEET_V2 = ModelSet(
    name="parakeet-tdt-0.6b-v2",
    label="Parakeet TDT 0.6B v2",
    maker="NVIDIA",
    files=(
        ModelFile("encoder-model.int8.onnx", f"{_PARAKEET_V2}/encoder-model.int8.onnx",
                  "3e0581fda6ab843888b51e56d7ee78b6d5bc3237ec113af1f732d1d5286aa155", 652_184_014),
        ModelFile("decoder_joint-model.int8.onnx", f"{_PARAKEET_V2}/decoder_joint-model.int8.onnx",
                  "a449f49acd68979d418651dd2dcb737cc0f1bf0225e009e29ee326354edbf7d3", 8_998_286),
        ModelFile("vocab.txt", f"{_PARAKEET_V2}/vocab.txt",
                  "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d", 9_384),
        ModelFile("config.json", f"{_PARAKEET_V2}/config.json",
                  "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466", 97),
    ),
    license="CC-BY-4.0",
    license_url="https://creativecommons.org/licenses/by/4.0/",
    source_url="https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2",
)

PARAKEET_V3 = ModelSet(
    name="parakeet-tdt-0.6b-v3",
    label="Parakeet TDT 0.6B v3",
    maker="NVIDIA",
    files=(
        ModelFile("encoder-model.int8.onnx", f"{_PARAKEET_V3}/encoder-model.int8.onnx",
                  "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09", 652_183_999),
        ModelFile("decoder_joint-model.int8.onnx", f"{_PARAKEET_V3}/decoder_joint-model.int8.onnx",
                  "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70", 18_202_004),
        ModelFile("vocab.txt", f"{_PARAKEET_V3}/vocab.txt",
                  "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d", 93_939),
        ModelFile("config.json", f"{_PARAKEET_V3}/config.json",
                  "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466", 97),
    ),
    license="CC-BY-4.0",
    license_url="https://creativecommons.org/licenses/by/4.0/",
    source_url="https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3",
)

# Apache-2.0 plus an additional term: the model must always be identified as "S1-mini" by
# "Superwhisper", with that exact capitalization. LICENSE and NOTICE come with the weights.
S1_MINI = ModelSet(
    name="s1-mini",
    label="S1-mini",
    maker="Superwhisper",
    files=(
        ModelFile("s1-mini-q4_k_m.gguf", f"{_S1_MINI}/s1-mini-q4_k_m.gguf",
                  "3b41ebe2502cbd03e811d5d16b022f5ab551eda58d62597d152f89535003c634", 484_219_808),
        ModelFile("LICENSE", f"{_S1_MINI}/LICENSE",
                  "f715982df6ce767ae64864d74b95644e5d658aab54717dbceeb252eb3cbcb421", 12_033),
        ModelFile("NOTICE", f"{_S1_MINI}/NOTICE",
                  "6eb1e98a0d0046dd1b71da19d3b64ebccc12622e7d62e5efcad10298b4d66292", 507),
    ),
    license="Apache-2.0 with an additional naming term",
    license_url=f"{_S1_MINI.replace('/resolve/', '/blob/')}/LICENSE",
    source_url="https://huggingface.co/superwhisper/s1-mini-GGUF",
)

ALL: tuple[ModelSet, ...] = (PARAKEET_V2, PARAKEET_V3, S1_MINI)


# -- downloads -------------------------------------------------------------------------------


class ModelDownload:
    """The one-time download of a model's files, on its own thread so the app can show progress
    and a waiting dictation can start as soon as it lands. Starting it twice joins the run in
    progress. Cancelling (on quit) keeps the partial file for next time."""

    def __init__(self, model: ModelSet):
        self.model = model
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False  # flipped under the lock, so a late start() never misses its callback
        self._cancel = threading.Event()
        self._callbacks: list[Callable[[str], None]] = []
        self.done_bytes = 0
        self.total_bytes = model.total_bytes
        self.error = ""

    def active(self) -> bool:
        return self._running

    def progress(self) -> float | None:
        """Fraction done (0..1) while downloading, else None."""
        if not self.active():
            return None
        return min(1.0, self.done_bytes / self.total_bytes) if self.total_bytes else 0.0

    def start(self, on_done: Callable[[str], None] | None = None) -> None:
        """Begin downloading whatever is missing. on_done(error) runs on the download thread when
        it ends; error is "" on success. Reports success at once if the files are already here."""
        with self._lock:
            if not self._running:
                if self.model.present():
                    if on_done:
                        on_done("")
                    return
                self._cancel.clear()
                self.error = ""
                self.done_bytes = 0
                self._callbacks = []
                self._running = True
                self._thread = threading.Thread(target=self._run, daemon=True, name=f"lyssna-model-{self.model.name}")
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
        log.info("downloading %s into %s", self.model.name, self.model.directory)
        error = ""
        finished = 0
        try:
            for f in self.model.files:
                dest = self.model.path(f.name)
                if not dest.is_file():
                    fetch(f.url, dest, f.sha256, f.size, cancel=self._cancel, what="model",
                          on_progress=lambda n, base=finished: setattr(self, "done_bytes", base + n))
                finished += f.size
                self.done_bytes = finished
        except DownloadError as e:
            error = str(e)
        except Exception as e:  # never let the download thread die without telling anyone
            log.exception("model download crashed")
            error = f"Model download failed: {type(e).__name__}"
        if error:
            log.warning("%s download did not finish: %s", self.model.name, error)
        with self._lock:
            self.error = error
            self._running = False
            callbacks, self._callbacks = self._callbacks, []
        for cb in callbacks:
            try:
                cb(error)
            except Exception:
                log.exception("download callback failed")


DOWNLOADS: dict[str, ModelDownload] = {}
_downloads_lock = threading.Lock()


def ensure(model: ModelSet, on_progress: Callable[[float], None] | None = None) -> str:
    """Download a model if needed and wait for it (for the command line). Returns the error
    message, "" when the files are here."""
    if model.present():
        return ""
    download = model.download()
    download.start()
    if on_progress is not None:
        while download.active():
            on_progress(download.progress() or 0.0)
            threading.Event().wait(0.5)
    error = download.wait()
    if not model.present():
        return error or "The model is not downloaded yet. Try again."
    return ""
