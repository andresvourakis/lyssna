"""One-time carry-over from the app's old name, Speechy. The config, usage figures and the
welcome marker are copied (the old Speechy.app may still be opened, so its files stay as they
are); the audio cache is moved with one rename. The socket and logs are left behind. Every
check is a stat call, so running this on each config load costs next to nothing."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from . import config, host

log = logging.getLogger("lyssna.migrate")

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


def run() -> list[str]:
    """Migrate whatever is still missing. Returns what was migrated; never raises.
    Mac only: Speechy never ran anywhere else."""
    if not host.IS_MAC:
        return []
    done: list[str] = []
    for step in (_copy_config, _copy_state, _move_cache):
        try:
            done += step()
        except Exception:
            log.warning("migration from Speechy: %s failed", step.__name__, exc_info=True)
    if done:
        log.info("migrated from Speechy: %s", "; ".join(done))
    return done


KEYCHAIN_MARKER = "keys-in-keychain"


def keys_to_keychain() -> list[str]:
    """Once per machine, at app start: move every api_key written in the Lyssna config file into
    the Keychain and blank it in the file. A key is blanked only after reading it back from the
    Keychain, so a failed write leaves the file as it was (and the move is tried again on the
    next start). If the Keychain already holds a different key for that engine, the file is left
    alone: the Keychain one is what is in use, and neither is thrown away. After a full pass the
    marker is written, so a key someone puts in the file later on purpose stays there.
    Returns the config sections moved; never raises."""
    from . import keychain
    from .providers import REGISTRY

    marker = config.STATE_DIR / KEYCHAIN_MARKER
    moved: list[str] = []
    try:
        if marker.exists():
            return []
        sections = sorted({cls.section() for cls in REGISTRY.values() if cls.needs_key})
        failed = False
        blank: dict[str, str] = {}
        for section in sections:
            key = config.file_api_key(section)
            if not key:
                continue
            existing = keychain.get(section)
            if existing == key:
                blank[f"{section}.api_key"] = ""
                moved.append(section)
            elif existing:
                log.info("%s: the Keychain already holds a different key; leaving the config file as is", section)
            elif keychain.set(section, key) and keychain.get(section) == key:
                blank[f"{section}.api_key"] = ""
                moved.append(section)
            else:
                failed = True
                log.warning("%s: could not move the API key to the Keychain; it stays in the config file", section)
        if blank:
            config.update(**blank)
        if moved:
            log.info("moved API keys to the Keychain: %s", ", ".join(moved))
        if not failed:
            marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            marker.write_text("")
    except Exception:
        log.warning("moving API keys to the Keychain failed", exc_info=True)
    return moved
