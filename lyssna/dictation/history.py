"""The dictation history: what was pasted, kept on this Mac for the History window.

One file, `STATE_DIR/dictation/history.json` (owner-only, written atomically). Each entry is
the pasted text, the speech model's transcript before any correction, and a few numbers: words,
seconds of audio, the engines used. Audio is never stored.

How long entries are kept comes from `[dictation] history_days`, read each time the store is
used, so a change in the config applies at once: N > 0 keeps the last N days, 0 keeps nothing
(new dictations are not saved and saved ones are deleted), -1 keeps everything.

Lifetime totals (words, dictations, seconds spoken, and words per week for the last few
weeks) sit next to the entries but apart from them, so trimming old entries and Clear History
leave the figures in the window as they were. They hold counts only, never text.

Nothing here logs dictated text: the log gets counts."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .. import config
from .types import DictationResult

log = logging.getLogger("lyssna.dictation.history")

DEFAULT_DAYS = 30
WEEKS_KEPT = 12  # weeks of per-week word counts kept in the totals


def default_path() -> Path:
    return config.STATE_DIR / "dictation" / "history.json"


def configured_days(cfg: dict | None = None) -> int:
    """`[dictation] history_days` from the config: N > 0 days, 0 keep none, -1 keep all.
    Anything that is not a whole number falls back to the default; any negative is "keep all"."""
    try:
        cfg = cfg if cfg is not None else config.load()
        days = cfg.get("dictation", {}).get("history_days", DEFAULT_DAYS)
    except Exception:
        return DEFAULT_DAYS
    if isinstance(days, bool) or not isinstance(days, int):
        return DEFAULT_DAYS
    return -1 if days < 0 else days


def week_key(when: datetime) -> str:
    """The ISO week of a date, e.g. "2026-W47". Zero-padded, so the keys sort by date."""
    year, week, _ = when.isocalendar()
    return f"{year}-W{week:02d}"


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse(at: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return dt.astimezone()  # a naive time is taken as local


@dataclass
class Entry:
    id: str
    at: str               # local time with its UTC offset, ISO 8601, to the second
    text: str             # what was pasted
    raw: str              # the speech model's transcript
    words: int
    audio_seconds: float
    transcriber: str
    cleaner: str
    source: str

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_json(cls, data: dict) -> Entry | None:
        try:
            entry = cls(
                id=str(data["id"]),
                at=str(data["at"]),
                text=str(data.get("text") or ""),
                raw=str(data.get("raw") or ""),
                words=int(data.get("words") or 0),
                audio_seconds=float(data.get("audio_seconds") or 0.0),
                transcriber=str(data.get("transcriber") or ""),
                cleaner=str(data.get("cleaner") or ""),
                source=str(data.get("source") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return entry if entry.id and _parse(entry.at) is not None else None

    def when(self) -> datetime:
        return _parse(self.at) or _now()


def _empty_totals() -> dict:
    return {"words": 0, "dictations": 0, "audio_seconds": 0.0, "weeks": {}}


def _clean_totals(data) -> dict:
    totals = _empty_totals()
    if not isinstance(data, dict):
        return totals
    for key in ("words", "dictations"):
        if isinstance(data.get(key), int) and not isinstance(data.get(key), bool):
            totals[key] = max(0, data[key])
    if isinstance(data.get("audio_seconds"), (int, float)) and not isinstance(data.get("audio_seconds"), bool):
        totals["audio_seconds"] = max(0.0, float(data["audio_seconds"]))
    weeks = data.get("weeks")
    if isinstance(weeks, dict):
        totals["weeks"] = {str(k): v for k, v in weeks.items() if isinstance(v, int) and not isinstance(v, bool)}
    return totals


class HistoryStore:
    """Thread-safe. Loaded on first use and again whenever the file changed underneath; every
    change is written to disk at once. `days` and `now` are for tests: by default retention is
    read from the config on every call and the clock is the local time."""

    def __init__(self, path: Path | None = None, days: Callable[[], int] | None = None,
                 now: Callable[[], datetime] | None = None):
        self._path = path
        self._days = days or configured_days
        self._now = now or _now
        self._lock = threading.RLock()
        self._entries: list[Entry] | None = None
        self._totals: dict = _empty_totals()
        self._stamp: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self._path or default_path()

    def days(self) -> int:
        return self._days()

    # -- storage ---------------------------------------------------------------------------

    def stamp(self) -> tuple[int, int] | None:
        """(mtime_ns, size) of the file, or None when there is none. Changes on every write, so
        a window can poll it to know when to redraw."""
        try:
            st = self.path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _load(self) -> list[Entry]:
        if self._entries is not None and self.stamp() != self._stamp:
            self._entries = None
        if self._entries is None:
            entries: list[Entry] = []
            totals = _empty_totals()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("not an object")
                raw = data.get("entries", [])
                raw = raw if isinstance(raw, list) else []
                entries = [e for e in (Entry.from_json(d) for d in raw if isinstance(d, dict)) if e]
                totals = _clean_totals(data.get("totals"))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                log.warning("the history file could not be read (%s); keeping it aside and starting empty",
                            type(e).__name__)
                try:
                    os.replace(self.path, self.path.with_name(self.path.name + ".broken"))
                except OSError:
                    pass
            entries.sort(key=lambda e: e.when(), reverse=True)
            self._entries = entries
            self._totals = totals
            self._stamp = self.stamp()
        return self._entries

    def _save(self) -> None:
        entries = self._entries if self._entries is not None else []
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_name(path.name + ".tmp")
        text = json.dumps({"version": 1, "totals": self._totals, "entries": [e.to_json() for e in entries]},
                          indent=1, ensure_ascii=False)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        self._stamp = self.stamp()

    def _trim(self) -> bool:
        """Drop the entries the retention setting no longer keeps. Returns whether any went."""
        entries = self._load()
        days = self.days()
        if days < 0 or not entries:
            return False
        if days == 0:
            kept: list[Entry] = []
        else:
            cutoff = self._now() - timedelta(days=days)
            kept = [e for e in entries if e.when() > cutoff]
        if len(kept) == len(entries):
            return False
        dropped = len(entries) - len(kept)
        self._entries = kept
        log.info("history: removed %d entries past the %s", dropped,
                 "setting to keep none" if days == 0 else f"{days}-day limit")
        return True

    def _trim_and_save(self) -> None:
        if self._trim():
            self._save()

    # -- writing ---------------------------------------------------------------------------

    def add(self, result: DictationResult) -> None:
        """Save a dictation and count it in the totals. Empty results are ignored. With history
        off (0 days) nothing is saved and nothing is counted."""
        if not result.text.strip():
            return
        with self._lock:
            self._load()
            if self.days() == 0:
                self._trim_and_save()
                return
            now = self._now()
            entry = Entry(
                id=uuid.uuid4().hex[:12],
                at=now.isoformat(timespec="seconds"),
                text=result.text,
                raw=result.raw,
                words=max(0, int(result.words)),
                audio_seconds=round(max(0.0, float(result.audio_seconds)), 2),
                transcriber=result.transcriber,
                cleaner=result.cleaner,
                source=result.source,
            )
            self._entries.insert(0, entry)
            self._count(entry, now)
            self._trim()
            self._save()
            kept = len(self._entries)
        log.info("history: saved a dictation (%d words); %d kept", entry.words, kept)

    def _count(self, entry: Entry, now: datetime) -> None:
        t = self._totals
        t["words"] += entry.words
        t["dictations"] += 1
        t["audio_seconds"] = round(t["audio_seconds"] + entry.audio_seconds, 2)
        weeks = t["weeks"]
        key = week_key(now)
        weeks[key] = weeks.get(key, 0) + entry.words
        oldest = week_key(now - timedelta(weeks=WEEKS_KEPT - 1))
        t["weeks"] = {k: v for k, v in weeks.items() if k >= oldest}

    def delete(self, entry_id: str) -> bool:
        """Delete one entry. The totals keep counting it."""
        with self._lock:
            entries = self._load()
            kept = [e for e in entries if e.id != entry_id]
            if len(kept) == len(entries):
                return False
            self._entries = kept
            self._save()
        log.info("history: deleted one entry")
        return True

    def clear(self) -> int:
        """Delete every entry, keeping the totals. Returns how many were deleted."""
        with self._lock:
            n = len(self._load())
            if n:
                self._entries = []
                self._save()
        log.info("history: cleared %d entries", n)
        return n

    def update_text(self, entry_id: str, new_text: str) -> bool:
        """Replace an entry's text (the user fixed a word). The raw transcript and the totals
        stay as they were."""
        new_text = new_text.strip()
        if not new_text:
            return False
        with self._lock:
            entry = next((e for e in self._load() if e.id == entry_id), None)
            if entry is None:
                return False
            if entry.text != new_text:
                entry.text = new_text
                entry.words = len(new_text.split())
                self._save()
        return True

    # -- reading ---------------------------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Every kept entry, newest first (old ones past the retention are removed first)."""
        with self._lock:
            self._trim_and_save()
            return [Entry(**e.__dict__) for e in self._load()]

    def get(self, entry_id: str) -> Entry | None:
        return next((e for e in self.entries() if e.id == entry_id), None)

    def search(self, query: str) -> list[Entry]:
        """Entries whose text contains `query`, ignoring case. An empty query gives them all."""
        q = query.strip().casefold()
        entries = self.entries()
        return [e for e in entries if q in e.text.casefold()] if q else entries

    def totals(self) -> dict:
        with self._lock:
            self._load()
            return json.loads(json.dumps(self._totals))

    def stats(self) -> dict:
        """The figures for the window: words this week, total words, dictations, minutes spoken
        and average words per minute (0 until there is any audio)."""
        t = self.totals()
        minutes = t["audio_seconds"] / 60.0
        return {
            "words_this_week": t["weeks"].get(week_key(self._now()), 0),
            "words_total": t["words"],
            "dictations": t["dictations"],
            "minutes": round(minutes, 1),
            "wpm": round(t["words"] / minutes) if minutes > 0 else 0,
        }


_shared: HistoryStore | None = None
_shared_lock = threading.Lock()


def shared() -> HistoryStore:
    """The one history the app uses (tests get a fresh one each)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = HistoryStore()
        return _shared
