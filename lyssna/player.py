"""The reading engine: chunk text, synthesize a few chunks ahead, play them back to back.

Playback uses AVAudioPlayer so pause/resume/speed are native and instant (speed is
time-stretched with pitch preserved, so faster reading keeps the voice at its natural pitch)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import host, usage
from .cache import AudioCache
from .providers.base import Audio, Provider, ProviderError, error_summary
from .text import prepare, preview

log = logging.getLogger("lyssna.player")

PREFETCH_AHEAD = 4   # sentences synthesized beyond the one playing; keeps cost low if you stop early
FETCH_WORKERS = 2    # parallel synthesis requests
RESTART_AFTER = 2.5  # "back" restarts the current sentence if this many listening seconds have played
SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0]  # presets in the menu bar and player speed menus


@dataclass
class Session:
    chunks: list[str]
    provider: Provider
    audio: dict[int, Audio] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    cond: threading.Condition = field(default_factory=threading.Condition)
    stop: threading.Event = field(default_factory=threading.Event)
    index: int = 0
    skip: int = 0
    paused: bool = False
    player: object = None
    durations: dict[int, float] = field(default_factory=dict)  # raw seconds per chunk at 1.0x
    start_offset: float = 0.0  # raw seconds into the next chunk to start at (set by seek_to)


@dataclass
class Status:
    state: str = "idle"  # idle | loading | playing | paused
    index: int = 0
    total: int = 0
    text: str = ""
    error: str = ""
    speed: float = 1.0
    provider: str = ""
    position: float = 0.0  # listening seconds elapsed in this session (at current speed)
    duration: float = 0.0  # estimated total listening seconds at current speed
    session: int = 0       # bumps on every speak(); lets the UI tell a new reading from a skip

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class Reader:
    def __init__(
        self,
        provider_factory: Callable[[], Provider],
        speed: float = 1.0,
        cache: AudioCache | None = None,
        on_change: Callable[[Status], None] | None = None,
    ):
        self.provider_factory = provider_factory
        self.cache = cache or AudioCache()
        self.on_change = on_change
        self.speed = speed
        self._session: Session | None = None
        self._lock = threading.RLock()
        self._sessions = 0
        self.status = Status(speed=speed)

    # ----- public controls -------------------------------------------------

    def speak(self, text: str) -> bool:
        chunks = prepare(text)
        if not chunks:
            self._set(state="idle", error="Nothing to read")
            return False
        try:
            provider = self.provider_factory()
        except ProviderError as e:
            self._set(state="idle", error=str(e))
            return False
        with self._lock:
            self.stop()
            session = Session(chunks=chunks, provider=provider)
            self._session = session
            self._sessions += 1
            self.status = Status(state="loading", total=len(chunks), speed=self.speed, provider=provider.describe(),
                                 session=self._sessions)
            self._emit()  # tell the UI about the new reading now; the play thread only gets there later
            self._start(session)
        return True

    def restart_from_current(self) -> bool:
        """Re-open the running reading with a fresh provider (e.g. a new voice), starting
        over at the current sentence. Chunks, progress and the paused state carry over, and
        so does the session number, so the UI treats it as a skip rather than a new reading."""
        try:
            provider = self.provider_factory()
        except ProviderError as e:
            self._set(error=str(e))
            return False
        with self._lock:
            old = self._session
            if old is None or old.stop.is_set():
                return False
            old.stop.set()
            with old.cond:
                old.cond.notify_all()
            if old.player is not None:
                try:
                    old.player.stop()
                except Exception:
                    pass
            session = Session(chunks=old.chunks, provider=provider, index=old.index, paused=old.paused,
                              durations=dict(old.durations))
            self._session = session
            prev = self.status
            self.status = Status(state="paused" if session.paused else "loading", index=session.index + 1,
                                 total=len(session.chunks), text=prev.text, speed=self.speed,
                                 provider=provider.describe(), position=prev.position, duration=prev.duration,
                                 session=self._sessions)
            self._emit()
            self._start(session)
        return True

    def stop(self) -> None:
        with self._lock:
            s = self._session
            if s is None:
                self._set(state="idle", index=0, total=0, text="", error="")  # observers may still be showing UI
                return
            s.stop.set()
            with s.cond:
                s.cond.notify_all()
            if s.player is not None:
                try:
                    s.player.stop()
                except Exception:
                    pass
            self._session = None
        self._set(state="idle", index=0, total=0, text="", error="")

    def toggle_pause(self) -> None:
        with self._lock:
            s = self._session
            if s is None or s.stop.is_set():
                return
            s.paused = not s.paused
            if s.player is not None:
                if s.paused:
                    s.player.pause()
                else:
                    s.player.play()
            self._set(state="paused" if s.paused else ("playing" if s.player is not None else "loading"))

    def next(self) -> None:
        """Jump to the start of the next sentence."""
        self._skip(+1)

    def prev(self) -> None:
        """Restart the current sentence, or go to the previous one if we've only just started it."""
        with self._lock:
            s = self._session
            if s is None:
                return
            player = s.player
            if player is not None and player.currentTime() > RESTART_AFTER * self.speed:
                player.setCurrentTime_(0.0)
                return
        self._skip(-1)

    def seek(self, seconds: float) -> None:
        """Jump forward/back by `seconds` of listening time within the current chunk;
        past either edge it moves to the neighbouring chunk."""
        with self._lock:
            s = self._session
            if s is None or s.player is None:
                return
            player = s.player
            target = player.currentTime() + seconds * self.speed
            if target < 0:
                if player.currentTime() > 3.0 * self.speed:
                    player.setCurrentTime_(0.0)
                else:
                    self._skip(-1)
            elif target >= player.duration():
                self._skip(+1)
            else:
                player.setCurrentTime_(target)

    def seek_to(self, fraction: float) -> None:
        """Jump to `fraction` (0..1) of the whole reading, across sentences. Unplayed
        sentences are positioned by estimate, so landing inside one is approximate."""
        fraction = max(0.0, min(1.0, fraction))
        with self._lock:
            s = self._session
            if s is None:
                return
            raw = self._raw_durations(s)
            target = fraction * sum(raw)
            k = 0
            while k < len(raw) - 1 and target >= raw[k]:
                target -= raw[k]
                k += 1
            i = s.index
            if k == i and s.player is not None:
                s.player.setCurrentTime_(min(target, max(0.0, s.player.duration() - 0.05)))
                return
            s.start_offset = target
            if k != i:
                self._skip(k - i, keep_paused=True)

    def set_speed(self, speed: float) -> float:
        speed = max(0.5, min(2.0, round(speed, 2)))
        with self._lock:
            self.speed = speed
            s = self._session
            if s is not None and s.player is not None:
                s.player.setRate_(speed)
        self._set(speed=speed)
        return speed

    def is_active(self) -> bool:
        return self._session is not None

    # ----- internals ---------------------------------------------------------

    def _start(self, session: Session) -> None:
        """Start a session's two threads: one fetches audio ahead, the other plays it."""
        threading.Thread(target=self._fetch_loop, args=(session,), daemon=True, name="lyssna-fetch").start()
        threading.Thread(target=self._play_loop, args=(session,), daemon=True, name="lyssna-play").start()

    def _skip(self, delta: int, keep_paused: bool = False) -> None:
        with self._lock:
            s = self._session
            if s is None:
                return
            s.skip = delta
            if not keep_paused:
                s.paused = False
            if s.player is not None:
                s.player.stop()
            with s.cond:
                s.cond.notify_all()

    def _set(self, session: Session | None = None, **changes) -> None:
        """Apply changes to the live status and broadcast it. Pass `session` from anything
        running for one reading: a thread whose session was stopped or replaced must not
        write over the status of the reading that took its place."""
        if session is not None and (session.stop.is_set() or self._session is not session):
            return
        for k, v in changes.items():
            setattr(self.status, k, v)
        self._emit()

    def _emit(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.status)
            except Exception:
                log.exception("on_change callback failed")

    def _synthesize(self, session: Session, i: int) -> Audio:
        text = session.chunks[i]
        key = session.provider.cache_key()
        cached = self.cache.get(key, text)
        if cached is not None:
            return cached
        t = time.time()
        audio = session.provider.synthesize(text)
        log.info("synthesized chunk %d (%d chars) in %.2fs via %s", i, len(text), time.time() - t, session.provider.name)
        usage.record(session.provider.name, len(text), session.provider.price_per_million)
        self.cache.put(key, text, audio)
        return audio

    def _fetch_loop(self, session: Session) -> None:
        """Runs FETCH_WORKERS threads; each claims the next unfetched index within the
        prefetch window, so short sentences never starve playback."""
        claimed: set[int] = set()
        n = len(session.chunks)

        def unfetched(k: int) -> bool:
            return k not in claimed and k not in session.audio and k not in session.errors

        def worker() -> None:
            while not session.stop.is_set():
                with session.cond:
                    # Only ever look ahead of the sentence being played: after a scrub or a
                    # skip, the ones jumped over aren't synthesized (or paid for) unless
                    # the user actually goes back to them.
                    i = next((k for k in range(session.index, n) if unfetched(k)), None)
                    if i is None:
                        if not any(unfetched(k) for k in range(n)):
                            return
                        session.cond.wait(0.25)
                        continue
                    if i > session.index + PREFETCH_AHEAD:
                        session.cond.wait(0.25)
                        continue
                    claimed.add(i)
                try:
                    audio = self._synthesize(session, i)
                except Exception as e:
                    log.warning("chunk %d failed via %s: %s", i, session.provider.name, error_summary(e))
                    with session.cond:
                        session.errors[i] = str(e)
                        session.cond.notify_all()
                    continue
                with session.cond:
                    session.audio[i] = audio
                    session.cond.notify_all()

        threads = [threading.Thread(target=worker, daemon=True, name=f"lyssna-fetch-{k}") for k in range(FETCH_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _make_player(self, audio: Audio):
        """A player for one chunk, ready at the current speed (see lyssna/win/audio.py for the contract)."""
        return host.audio.make_player(audio, self.speed)

    def _play_loop(self, session: Session) -> None:
        i = session.index  # 0 for a new reading; the current sentence after restart_from_current()
        n = len(session.chunks)
        while i < n and not session.stop.is_set():
            with session.cond:
                session.index = i
                session.cond.notify_all()
            self._set(session=session, state="loading", index=i + 1, total=n, text=preview(session.chunks[i]))
            with session.cond:
                while not session.stop.is_set() and session.skip == 0 and i not in session.audio and i not in session.errors:
                    session.cond.wait(0.25)
            if session.stop.is_set():
                return
            if session.skip:  # scrubbed / skipped while this sentence was still loading
                i = max(0, min(n - 1, i + session.skip))
                session.skip = 0
                continue
            if i in session.errors:
                self._finish(session, error=session.errors[i])
                return
            try:
                player = self._make_player(session.audio[i])
            except ProviderError as e:
                self._finish(session, error=str(e))
                return
            with self._lock:
                if session.stop.is_set():
                    return
                session.player = player
                session.skip = 0
                if session.start_offset > 0:
                    player.setCurrentTime_(min(session.start_offset, max(0.0, player.duration() - 0.05)))
                    session.start_offset = 0.0
                if not session.paused:
                    player.play()
            session.durations[i] = float(player.duration())
            self._set(session=session, state="paused" if session.paused else "playing", index=i + 1, total=n,
                      text=preview(session.chunks[i]), **self._progress(session, i, player))
            last_tick = 0.0
            while not session.stop.is_set() and session.skip == 0 and (player.isPlaying() or session.paused):
                time.sleep(0.02)
                if time.time() - last_tick > 0.25:
                    last_tick = time.time()
                    self._set(session=session, **self._progress(session, i, player))
            player.stop()
            with self._lock:
                session.player = None
            if session.skip:
                i = max(0, min(n - 1, i + session.skip))
                session.skip = 0
                continue
            i += 1
        self._finish(session)

    def _raw_durations(self, session: Session) -> list[float]:
        """Seconds per chunk at 1.0x. Unplayed chunks are estimated from the
        characters-per-second observed on chunks we already have audio for."""
        known = {k: d for k, d in session.durations.items() if d > 0}
        if known:
            cps = sum(len(session.chunks[k]) for k in known) / sum(known.values())
        else:
            cps = 15.0
        return [known.get(k, len(c) / cps) for k, c in enumerate(session.chunks)]

    def _progress(self, session: Session, i: int, player) -> dict:
        """Elapsed/total listening seconds at the current speed."""
        raw = self._raw_durations(session)
        raw_total = sum(raw)
        raw_before = sum(raw[:i])
        raw_pos = raw_before + float(player.currentTime())
        rate = self.speed or 1.0
        return {"position": raw_pos / rate, "duration": raw_total / rate}

    def _finish(self, session: Session, error: str = "") -> None:
        """End of a reading. Only the session that is still the current one announces idle:
        a stale play thread finishing after speak() started the next reading must stay quiet,
        or it would hide a player that has just come up."""
        with self._lock:
            current = self._session is session
            if current:
                self._session = None
        if current:
            self._set(state="idle", index=0, total=0, text="", error=error)
