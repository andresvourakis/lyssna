"""One-time carry-overs at start-up.

From the app's old name, Speechy (Mac only, the steps are in lyssna/mac/speechy.py): the
config, usage figures and the welcome marker are copied (the old Speechy.app may still be
opened, so its files stay as they are); the audio cache is moved with one rename. The socket
and logs are left behind. Every check is a stat call, so running this on each config load
costs next to nothing.

From the config file to the key store: API keys written in the config file move into the
system's key store once per machine (keys_to_keychain)."""

from __future__ import annotations

import logging

from . import config, host

log = logging.getLogger("lyssna.migrate")


def run() -> list[str]:
    """Carry over whatever is still missing from Speechy. Returns what was migrated; never
    raises. Only on a Mac (host.speechy): Speechy never ran anywhere else."""
    speechy = host.speechy
    if speechy is None:
        return []
    done: list[str] = []
    for step in speechy.STEPS:
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
    from . import keystore
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
            existing = keystore.get(section)
            if existing == key:
                blank[f"{section}.api_key"] = ""
                moved.append(section)
            elif existing:
                log.info("%s: the Keychain already holds a different key; leaving the config file as is", section)
            elif keystore.set(section, key) and keystore.get(section) == key:
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
