"""API keys in the system's key store, one entry per engine config section: the login Keychain
on the Mac (lyssna/mac/credentials.py), Credential Manager on Windows (lyssna/win/credentials.py).

Why: a key in a plain text file is readable by anything running as the user and ends up in
backups and dotfile repos. The key store encrypts it for this user. This module is the part
both systems share: the cache, the background read at launch and the rules below. The backend
that talks to the system comes from lyssna.host.

Every function is safe from any thread and never raises: a Keychain problem is logged and
reads as "no key here", so lookup carries on to the config file and the environment. Keys
are never logged, not even in part.

Reads are cached in memory (a miss too), because a provider is built for every reading and
the menu asks each engine whether it is set up. The cache is dropped on set and delete. A
side effect worth knowing: a key added outside the app (Keychain Access) shows up after a
restart.

A read can block for as long as the user takes to answer a macOS password prompt: after each
update of a self-signed app, macOS asks once before the app may read its saved key again. So
the main thread never reads here. At launch the app reads every key section once on a
background thread (load); the menu and the windows ask peek or has_key, which answer from the
cache and say "not known yet" (UNKNOWN, None) until that read has landed, then the listeners
hear about it. Real uses of a key (a reading, a dictation, saving a key) call get on their
own worker threads; a get for a section the background read is still busy with waits for it
rather than asking the Keychain (and the user) a second time."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

from . import host

log = logging.getLogger("lyssna.keystore")

SERVICE = "io.github.andresvourakis.lyssna"
# Backend statuses that are not failures. The values are the macOS ones (errSecItemNotFound,
# errSecDuplicateItem); both backends import them from here and answer with these numbers.
NOT_FOUND = -25300
DUPLICATE = -25299


class _Unknown:
    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = _Unknown()  # peek's answer while the Keychain has not been read for that section yet

_backend = host.credentials.CredentialBackend()  # tests swap this for an in-memory fake
_lock = threading.Lock()
_settled = threading.Condition(_lock)  # notified whenever a background read finishes a section
_cache: dict[str, str | None] = {}
_generation = 0  # bumped on every invalidation so a read that raced a write is not cached
_pending: set[str] = set()  # sections a background read is busy with
_unreadable: set[str] = set()  # sections whose background read crashed: peek stops asking for them
_listeners: list[Callable[[], None]] = []
_local = threading.local()  # .loader is True on the background reading thread


def _invalidate(account: str) -> None:
    global _generation
    with _lock:
        _cache.pop(account, None)
        _unreadable.discard(account)
        _generation += 1


def _forget(account: str) -> None:
    """A delete that worked: there is no key now, and we know it without asking."""
    global _generation
    with _lock:
        _cache[account] = None
        _unreadable.discard(account)
        _generation += 1


def clear_cache() -> None:
    global _generation
    with _lock:
        _cache.clear()
        _unreadable.clear()
        _generation += 1


def _read(account: str) -> tuple[str | None, bool]:
    """Ask the Keychain. Returns (key or None, whether the answer may be cached)."""
    try:
        status, value = _backend.get(account)
    except Exception as e:  # type only: an encode or decode error message quotes part of the key
        log.warning("keychain read for %s failed: %s", account, type(e).__name__)
        return None, False  # not cached: a crash here is worth retrying
    if status not in (0, NOT_FOUND):
        log.warning("keychain read for %s failed: OSStatus %s", account, status)
    return (value if status == 0 and value else None), True


def get(account: str) -> str | None:
    """The stored key for a config section, or None if there is none or the Keychain failed.
    May block on a macOS password prompt: never call it on the main thread (use peek)."""
    with _lock:
        while account in _pending and not getattr(_local, "loader", False):
            _settled.wait()  # the background read is asking already; one prompt is enough
        if account in _cache:
            return _cache[account]
        generation = _generation
    value, cacheable = _read(account)
    if cacheable:
        with _lock:
            if generation == _generation:
                _cache[account] = value
    return value


def _spawn(fn: Callable[[], None]) -> None:
    """Start the background read (tests replace this to run it inline)."""
    threading.Thread(target=fn, daemon=True, name="lyssna-keystore").start()


def subscribe(fn: Callable[[], None]) -> None:
    """Call fn() (on the background thread) each time a background read has finished."""
    with _lock:
        _listeners.append(fn)


def _notify() -> None:
    with _lock:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn()
        except Exception:
            log.exception("keychain listener failed")


def load(accounts: Iterable[str], first: Callable[[], object] | None = None) -> None:
    """Read these sections into the cache on a background thread, then tell the listeners.
    Sections already known or being read are skipped. `first` runs on that thread before the
    reads (the app moves keys out of the config file there). Returns at once."""
    with _lock:
        todo = [a for a in dict.fromkeys(accounts) if a not in _cache and a not in _pending and a not in _unreadable]
        if not todo and first is None:
            return
        _pending.update(todo)

    def run() -> None:
        _local.loader = True
        try:
            if first is not None:
                try:
                    first()
                except Exception:
                    log.warning("keychain: the step before the background read failed", exc_info=True)
            for account in todo:
                try:
                    get(account)
                finally:
                    with _lock:
                        if account not in _cache:
                            _unreadable.add(account)
                        _pending.discard(account)
                        _settled.notify_all()
        finally:
            _local.loader = False
        _notify()

    _spawn(run)


def peek(account: str) -> str | _Unknown | None:
    """get() that never blocks: the cached answer, or UNKNOWN when the Keychain has not been
    read for this section yet. Then a background read starts, and the listeners hear when it
    lands. Safe on the main thread."""
    with _lock:
        if account in _cache:
            return _cache[account]
        if account in _unreadable:
            return None
    load([account])
    with _lock:
        return _cache.get(account, UNKNOWN)


def has_key(account: str, *elsewhere: str | None) -> bool | None:
    """Is there a key for this section, in the Keychain or any of `elsewhere` (a key being
    tried, the config file's, the environment's)? None while the Keychain answer is not known
    yet. Never blocks."""
    if any(elsewhere):
        return True
    value = peek(account)
    return None if value is UNKNOWN else bool(value)


def known() -> bool:
    """True when no background read is running."""
    with _lock:
        return not _pending


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
    if status in (0, NOT_FOUND):
        _forget(account)
    else:
        _invalidate(account)
    if status not in (0, NOT_FOUND):
        if status is not None:
            log.warning("keychain delete for %s failed: OSStatus %s", account, status)
        return False
    return True
