"""Audio cache keyed by provider+voice+text, so re-reading the same paragraph is free.

`cache = false` in the config turns it off: nothing is then written to or read from disk.

The cache has a size cap (`cache_max_mb` in the config). Once it grows past the cap, the
least recently used files go first, down to about 90% of it so we don't trim again on the
very next write. A file's modification time is its "last used" time: put() writes it and
get() touches it. Walking thousands of files is too slow for every write, so trimming runs
on a background thread, once at startup and then every EVICT_EVERY writes. It never raises:
a cache problem must not stop playback."""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import threading
from pathlib import Path

from . import config
from .providers.base import Audio

log = logging.getLogger("lyssna.cache")

DEFAULT_MAX_MB = 500
EVICT_EVERY = 50     # writes between background trims
TRIM_TO = 0.9        # fraction of the cap to trim down to
_MB = 1024 * 1024


def cache_enabled(cfg: dict) -> bool:
    """The config's `cache` switch. Anything that reads as "off" turns the disk cache off."""
    return str(cfg.get("cache", True)).strip().lower() not in ("false", "0", "no", "off", "")


class AudioCache:
    def __init__(self, root: Path | None = None, max_mb: float = DEFAULT_MAX_MB, evict_every: int = EVICT_EVERY,
                 enabled: bool = True):
        self.root = root or config.CACHE_DIR
        self.enabled = enabled  # False: get() misses and put() writes nothing
        self.max_bytes = int(max_mb * _MB) if max_mb > 0 else 0  # 0 = no cap
        self.evict_every = max(1, evict_every)
        self._puts = 0
        self._lock = threading.Lock()
        self._trimming = False

    @classmethod
    def from_config(cls, cfg: dict) -> AudioCache:
        """A cache capped at the config's `cache_max_mb` (a bad value falls back to the default),
        switched on or off by its `cache` setting."""
        try:
            max_mb = float(cfg.get("cache_max_mb", DEFAULT_MAX_MB))
        except (TypeError, ValueError):
            log.warning("cache_max_mb %r is not a number; using %d", cfg.get("cache_max_mb"), DEFAULT_MAX_MB)
            max_mb = DEFAULT_MAX_MB
        return cls(max_mb=max_mb, enabled=cache_enabled(cfg))

    def _path(self, key: str, text: str, ext: str | None = None) -> Path:
        digest = hashlib.sha256(f"{key}\n{text}".encode()).hexdigest()
        d = self.root / digest[:2]
        return d / (digest if ext is None else f"{digest}.{ext}")

    def get(self, key: str, text: str) -> Audio | None:
        if not self.enabled:
            return None
        stem = self._path(key, text)
        for p in stem.parent.glob(stem.name + ".*"):
            try:
                audio = Audio(p.read_bytes(), p.suffix.lstrip("."))
            except OSError:
                return None
            try:
                os.utime(p)  # mark as recently used, so trimming keeps it
            except OSError:
                pass
            return audio
        return None

    def put(self, key: str, text: str, audio: Audio) -> None:
        if not self.enabled:
            return
        p = self._path(key, text, audio.ext)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(audio.data)
        except OSError:
            pass
        with self._lock:
            self._puts += 1
            due = self._puts % self.evict_every == 0
        if due:
            self.trim_in_background()

    def trim_in_background(self) -> None:
        """Run trim() on a daemon thread, unless one is already running, there is no cap or the
        cache is off."""
        if not self.max_bytes or not self.enabled:
            return
        with self._lock:
            if self._trimming:
                return
            self._trimming = True

        def run() -> None:
            try:
                self.trim()
            finally:
                with self._lock:
                    self._trimming = False

        threading.Thread(target=run, daemon=True, name="lyssna-cache-trim").start()

    def trim(self) -> int:
        """Delete least recently used files until the cache is under TRIM_TO of the cap.
        Returns the number of bytes freed. Never raises."""
        if not self.max_bytes:
            return 0
        try:
            files = []
            total = 0
            for p in self.root.rglob("*"):
                try:
                    st = p.stat()
                except OSError:
                    continue  # deleted under us (e.g. Clear Audio Cache)
                if stat.S_ISREG(st.st_mode):
                    files.append((st.st_mtime, st.st_size, p))
                    total += st.st_size
            if total <= self.max_bytes:
                return 0
            target = self.max_bytes * TRIM_TO
            freed = 0
            for _, size, p in sorted(files, key=lambda f: f[0]):
                if total - freed <= target:
                    break
                try:
                    p.unlink()
                except OSError:
                    continue
                freed += size
            log.info("audio cache trimmed: freed %.1f MB, %.1f MB left", freed / _MB, (total - freed) / _MB)
            return freed
        except Exception:
            log.exception("audio cache trim failed")
            return 0

    def clear(self) -> None:
        for p in self.root.rglob("*"):
            if p.is_file():
                p.unlink(missing_ok=True)
