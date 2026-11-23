"""The recordings of failed dictations, kept on this Mac so they can be retried.

Only a dictation that failed inside transcription (the speech engine or provider errored or
timed out) is saved, as `STATE_DIR/dictation/failed/<history entry id>.wav`: 16 kHz mono
16-bit WAV, the folder and the files owner-only, each file written atomically. A recording is
deleted when its retry works, when its history entry is deleted or History is cleared, or after
24 hours. At most MAX_KEPT are kept; past that the oldest goes first. Successful dictations are
never written.

Nothing here logs audio or text: the log gets counts."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .. import config

log = logging.getLogger("lyssna.dictation.failed")

SAMPLE_RATE = 16000
KEEP_SECONDS = 24 * 60 * 60   # a failed recording is kept this long
MAX_KEPT = 5                  # and at most this many of them
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # entry ids become file names: nothing that can leave the folder


def default_dir() -> Path:
    return config.STATE_DIR / "dictation" / "failed"


class FailedRecordings:
    """Thread-safe. `directory` and `now` (seconds since the epoch) are for tests."""

    def __init__(self, directory: Path | None = None, now: Callable[[], float] | None = None):
        self._dir = directory
        self._now = now or time.time
        self._lock = threading.RLock()

    @property
    def directory(self) -> Path:
        return self._dir or default_dir()

    def _path(self, entry_id: str) -> Path | None:
        return self.directory / f"{entry_id}.wav" if _ID.match(entry_id or "") else None

    def _files(self) -> list[Path]:
        try:
            return [p for p in self.directory.iterdir() if p.suffix == ".wav" and p.is_file()]
        except OSError:
            return []

    def save(self, entry_id: str, samples) -> bool:
        """Write the recording (float samples at 16 kHz, -1..1) for this entry. False when it
        could not be written; nothing is left behind then."""
        path = self._path(entry_id)
        if path is None:
            return False
        audio = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
        pcm = (audio * 32767.0).astype("<i2").tobytes()
        with self._lock:
            tmp = path.with_name(path.name + ".tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.parent.chmod(0o700)  # mkdir's mode does not fix a folder that already exists
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    with wave.open(f, "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(SAMPLE_RATE)
                        w.writeframes(pcm)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            except OSError as e:
                log.warning("a failed recording could not be saved (%s)", type(e).__name__)
                tmp.unlink(missing_ok=True)
                return False
            self.prune(keep=entry_id)
        log.info("saved a failed recording (%.1f s) for a retry", audio.size / SAMPLE_RATE)
        return True

    def load(self, entry_id: str) -> np.ndarray | None:
        """The recording as float32 samples at 16 kHz, or None when it is gone or expired."""
        path = self._path(entry_id)
        if path is None:
            return None
        with self._lock:
            self.prune()
            try:
                with wave.open(str(path), "rb") as w:
                    if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
                        raise ValueError("not 16 kHz mono 16-bit")
                    pcm = w.readframes(w.getnframes())
            except FileNotFoundError:
                return None
            except (OSError, EOFError, ValueError, wave.Error) as e:
                log.warning("a failed recording could not be read (%s); deleting it", type(e).__name__)
                path.unlink(missing_ok=True)
                return None
        return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32767.0

    def exists(self, entry_id: str) -> bool:
        path = self._path(entry_id)
        return path is not None and path.is_file()

    def delete(self, entry_id: str) -> bool:
        path = self._path(entry_id)
        if path is None:
            return False
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            except OSError as e:
                log.warning("a failed recording could not be deleted (%s)", type(e).__name__)
                return False
        log.info("deleted a failed recording")
        return True

    def clear(self) -> int:
        """Delete every failed recording. Returns how many went."""
        n = 0
        with self._lock:
            for path in self._files():
                try:
                    path.unlink()
                    n += 1
                except OSError:
                    pass
        if n:
            log.info("deleted %d failed recordings", n)
        return n

    def prune(self, keep: str = "") -> int:
        """Delete recordings older than KEEP_SECONDS, then the oldest past MAX_KEPT (never
        `keep`, the one just saved). Returns how many went."""
        with self._lock:
            files = []
            for path in self._files():
                try:
                    files.append((path.stat().st_mtime, path))
                except OSError:
                    pass
            files.sort(reverse=True)  # newest first
            cutoff = self._now() - KEEP_SECONDS
            gone = [p for t, p in files if t <= cutoff]
            kept = [p for t, p in files if t > cutoff]
            if len(kept) > MAX_KEPT:
                extra = [p for p in kept if p.stem != keep]
                gone += extra[MAX_KEPT - (len(kept) - len(extra)):]
            for path in gone:
                try:
                    path.unlink()
                except OSError:
                    pass
        if gone:
            log.info("expired %d failed recordings", len(gone))
        return len(gone)


_shared: FailedRecordings | None = None
_shared_lock = threading.Lock()


def shared() -> FailedRecordings:
    """The one store the app uses (tests get a fresh one each)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = FailedRecordings()
        return _shared
