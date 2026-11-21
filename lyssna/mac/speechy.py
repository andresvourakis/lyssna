"""The carry-over from the app's old name, Speechy, on the Mac. Each step copies or moves one
thing that is still missing and returns what it did; lyssna.migrate.run runs them through
lyssna.host (host.speechy) and logs the result."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .. import config

OLD_CONFIG_PATH = Path.home() / ".config" / "speechy" / "config.toml"
OLD_STATE_DIR = Path.home() / "Library" / "Application Support" / "Speechy"
OLD_CACHE_DIR = Path.home() / "Library" / "Caches" / "Speechy"
STATE_FILES = ("usage.json", "welcomed")


def _copy_config() -> list[str]:
    new = config.CONFIG_PATH
    if new.exists() or not OLD_CONFIG_PATH.is_file():
        return []
    data = OLD_CONFIG_PATH.read_bytes()
    new.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Created owner-only from the start: the file may hold API keys. O_EXCL never overwrites.
    fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(new, 0o600)
    return [f"config from {OLD_CONFIG_PATH}"]


def _copy_state() -> list[str]:
    copied = []
    for name in STATE_FILES:
        old, new = OLD_STATE_DIR / name, config.STATE_DIR / name
        if new.exists() or not old.is_file():
            continue
        new.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(old, new)
        copied.append(name)
    return [f"{', '.join(copied)} from {OLD_STATE_DIR}"] if copied else []


def _move_cache() -> list[str]:
    new = config.CACHE_DIR
    if new.exists() or not OLD_CACHE_DIR.is_dir():
        return []
    new.parent.mkdir(parents=True, exist_ok=True)
    os.rename(OLD_CACHE_DIR, new)  # one rename, same volume: never a file by file copy
    return [f"audio cache moved from {OLD_CACHE_DIR}"]


STEPS = (_copy_config, _copy_state, _move_cache)
