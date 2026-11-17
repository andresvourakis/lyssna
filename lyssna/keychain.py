"""API keys in the macOS login Keychain, one generic password per engine config section.

Why: a key in a plain text file is readable by anything running as the user and ends up in
backups and dotfile repos. The Keychain encrypts it and ties it to the app that stored it.
We call the Security framework directly instead of the `security` command line tool, which
would need the key as an argument where any other process could see it.

Every function is safe from any thread and never raises: a Keychain problem is logged and
reads as "no key here", so lookup carries on to the config file and the environment. Keys
are never logged, not even in part.

Reads are cached in memory (a miss too), because a provider is built for every reading and
the menu asks each engine whether it is set up. The cache is dropped on set and delete. A
side effect worth knowing: a key added outside the app (Keychain Access) shows up after a
restart."""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("lyssna.keychain")

SERVICE = "io.github.andresvourakis.lyssna"
_ERR_ITEM_NOT_FOUND = -25300
_ERR_DUPLICATE_ITEM = -25299


class SecurityBackend:
    """The real login Keychain. Each method returns an OSStatus (0 = success); get also
    returns the stored text. Tests swap this out for an in-memory fake."""

    def __init__(self, service: str = SERVICE):
        self.service = service

    def _query(self, account: str) -> dict:
        import Security

        return {
            Security.kSecClass: Security.kSecClassGenericPassword,
            Security.kSecAttrService: self.service,
            Security.kSecAttrAccount: account,
        }

    def get(self, account: str) -> tuple[int, str | None]:
        import Security

        query = self._query(account)
        query[Security.kSecReturnData] = True
        query[Security.kSecMatchLimit] = Security.kSecMatchLimitOne
        status, data = Security.SecItemCopyMatching(query, None)
        if status != 0 or data is None:
            return status, None
        return 0, bytes(data).decode("utf-8")

    def set(self, account: str, value: str) -> int:
        import Security
        from Foundation import NSData

        raw = value.encode("utf-8")
        data = NSData.dataWithBytes_length_(raw, len(raw))
        query = self._query(account)
        attrs = dict(query)
        attrs[Security.kSecValueData] = data
        attrs[Security.kSecAttrLabel] = f"Lyssna API key ({account})"
        status, _ = Security.SecItemAdd(attrs, None)
        if status == _ERR_DUPLICATE_ITEM:
            status = Security.SecItemUpdate(query, {Security.kSecValueData: data})
        return status

    def delete(self, account: str) -> int:
        import Security

        return Security.SecItemDelete(self._query(account))


_backend = SecurityBackend()
_lock = threading.Lock()
_cache: dict[str, str | None] = {}
_generation = 0  # bumped on every invalidation so a read that raced a write is not cached


def _invalidate(account: str) -> None:
    global _generation
    with _lock:
        _cache.pop(account, None)
        _generation += 1


def clear_cache() -> None:
    global _generation
    with _lock:
        _cache.clear()
        _generation += 1


def get(account: str) -> str | None:
    """The stored key for a config section, or None if there is none or the Keychain failed."""
    with _lock:
        if account in _cache:
            return _cache[account]
        generation = _generation
    try:
        status, value = _backend.get(account)
    except Exception as e:  # type only: an encode or decode error message quotes part of the key
        log.warning("keychain read for %s failed: %s", account, type(e).__name__)
        return None  # not cached: a crash here is worth retrying
    if status not in (0, _ERR_ITEM_NOT_FOUND):
        log.warning("keychain read for %s failed: OSStatus %s", account, status)
    value = value if status == 0 and value else None
    with _lock:
        if generation == _generation:
            _cache[account] = value
    return value


def set(account: str, value: str) -> bool:
    """Store (or replace) the key for a config section. True when the Keychain accepted it."""
    try:
        status = _backend.set(account, value)
    except Exception as e:  # type only: an encode or decode error message quotes part of the key
        log.warning("keychain write for %s failed: %s", account, type(e).__name__)
        status = None
    finally:
        _invalidate(account)
    if status != 0:
        if status is not None:
            log.warning("keychain write for %s failed: OSStatus %s", account, status)
        return False
    return True


def delete(account: str) -> bool:
    """Remove the key for a config section. True when it is gone (or was never there)."""
    try:
        status = _backend.delete(account)
    except Exception as e:  # type only: an encode or decode error message quotes part of the key
        log.warning("keychain delete for %s failed: %s", account, type(e).__name__)
        status = None
    finally:
        _invalidate(account)
    if status not in (0, _ERR_ITEM_NOT_FOUND):
        if status is not None:
            log.warning("keychain delete for %s failed: OSStatus %s", account, status)
        return False
    return True
