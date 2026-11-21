"""Playback of synthesized audio on Windows, through the Qt Multimedia player (PySide6).

Why it is built this way. The reading engine calls the player from its own reader threads, and
polls it every 20 ms. Qt media objects must live on one thread that runs a Qt event loop, and
the command line's standalone playback has no GUI loop at all. So this module runs one private
audio thread with its own Qt event loop, creates every Qt media object there, and posts every
call to it. The player object the engine holds is plain Python: it keeps the position, the
duration and the playing flag in a small lock-guarded state (PlaybackState) that the Qt signals
update, so the engine's polling never waits on the audio thread. Between position reports the
position is worked out from the last report, the rate and a monotonic clock, so the progress bar
moves smoothly.

Each chunk is written to a temp file and played from there, not from memory. A QBuffer made in
Python deadlocks: Qt's decoder thread reads it through PySide's wrapper, which needs the Python
lock, while the audio thread holds that lock waiting for the decoder. The file is deleted when
the player is let go (or at the next release or at exit, if Windows still has it open).

Qt needs an application object. If the app already made one (the Windows interface creates its
QApplication at startup, before anything plays), it is reused. If none exists, as in the
command line's standalone playback, the audio thread creates a QCoreApplication itself. A GUI
that wants a QApplication must therefore create it before the first playback.

The player contract. make_player(audio, speed) takes a lyssna.providers.base.Audio and the
reading speed, and returns an object ready to play at that speed, or raises ProviderError
when the audio cannot be decoded. The reading engine (lyssna/player.py) calls exactly these
eight methods on it, with the AVAudioPlayer names the Mac player has:

  play()                    start or resume
  pause()                   pause, keeping the position
  stop()                    stop
  currentTime()             position in seconds
  setCurrentTime_(seconds)  move to a position in seconds
  duration()                length in seconds
  isPlaying()               True while it plays
  setRate_(rate)            speed, 1.0 is normal, with the pitch unchanged

On this player, make_player returns only once the audio is loaded, so duration() is right at
once. isPlaying() is True as soon as play() returns, and False after pause(), after stop() and
once the audio reaches its end. A stopped or finished player has let go of its Qt objects, and
later calls to it do nothing."""

from __future__ import annotations

import atexit
import itertools
import logging
import os
import tempfile
import threading
import time
import weakref
from collections.abc import Callable

from ..providers.base import Audio, ProviderError

log = logging.getLogger("lyssna.win.audio")

LOAD_TIMEOUT = 5.0     # seconds make_player waits for Qt to load the audio
END_GRACE = 2.0        # wall seconds past the expected end before a silent player counts as finished
RESYNC_BEHIND = 0.25   # a position report this far behind the running clock is treated as jitter
SEEK_SETTLE = 1.0      # seconds after a seek during which stale position reports are ignored
SEEK_MATCH = 0.5       # a report this close to the seek target means the seek has landed


class PlaybackState:
    """The cached state of one player, with no Qt in it. Every method takes the lock, so the
    engine's threads and the audio thread can call it at the same time. Times are seconds."""

    def __init__(self, rate: float = 1.0, clock: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self.rate = float(rate) or 1.0
        self.duration = 0.0
        self.playing = False
        self.finished = False   # stopped by the user, reached the end, or failed
        self._pos = 0.0         # position at self._at
        self._at = clock()
        self._seek_target: float | None = None
        self._seek_at = 0.0

    def _now_pos(self, now: float) -> float:
        pos = self._pos + (now - self._at) * self.rate if self.playing else self._pos
        if self.duration > 0:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    def _rebase(self, now: float) -> None:
        self._pos = self._now_pos(now)
        self._at = now

    def loaded(self, duration: float) -> None:
        with self._lock:
            self.duration = max(0.0, float(duration))

    def play(self) -> bool:
        """Start or resume. Returns True when Qt should be told to play."""
        with self._lock:
            if self.finished:
                return False
            now = self._clock()
            self._rebase(now)
            if self.duration > 0 and self._pos >= self.duration:
                self._pos = 0.0
            self.playing = True
            return True

    def pause(self) -> bool:
        with self._lock:
            if self.finished:
                return False
            self._rebase(self._clock())
            self.playing = False
            return True

    def stop(self) -> bool:
        """Stop for good. Returns True the first time, when Qt should stop and let go."""
        with self._lock:
            self._rebase(self._clock())
            self.playing = False
            if self.finished:
                return False
            self.finished = True
            return True

    def end(self) -> bool:
        """The audio reached its end. Returns True the first time."""
        with self._lock:
            self.playing = False
            if self.duration > 0:
                self._pos = self.duration
            self._at = self._clock()
            if self.finished:
                return False
            self.finished = True
            return True

    def fail(self) -> bool:
        with self._lock:
            self._rebase(self._clock())
            self.playing = False
            if self.finished:
                return False
            self.finished = True
            return True

    def seek(self, seconds: float) -> bool:
        with self._lock:
            if self.finished:
                return False
            now = self._clock()
            pos = max(0.0, float(seconds))
            if self.duration > 0:
                pos = min(pos, self.duration)
            self._pos = pos
            self._at = now
            self._seek_target = pos
            self._seek_at = now
            return True

    def set_rate(self, rate: float) -> bool:
        with self._lock:
            self._rebase(self._clock())
            self.rate = float(rate) or 1.0
            return not self.finished

    def report_position(self, seconds: float) -> None:
        """A position report from Qt. Stale reports from before a seek, and small backward
        jitter while playing, are ignored so the position does not jump about."""
        with self._lock:
            if self.finished:
                return
            now = self._clock()
            seconds = max(0.0, float(seconds))
            if self._seek_target is not None:
                if abs(seconds - self._seek_target) > SEEK_MATCH and now - self._seek_at < SEEK_SETTLE:
                    return
                self._seek_target = None
            if self.playing:
                behind = self._now_pos(now) - seconds
                if 0 < behind < RESYNC_BEHIND:
                    return
            self._pos = min(seconds, self.duration) if self.duration > 0 else seconds
            self._at = now

    def current(self) -> float:
        with self._lock:
            return self._now_pos(self._clock())

    def is_playing(self) -> bool:
        """True while playing. If Qt never says the audio ended, the player still counts as
        finished once the clock is END_GRACE wall seconds past the end, so a reading never
        hangs on one sentence."""
        with self._lock:
            if not self.playing:
                return False
            if self.duration > 0:
                now = self._clock()
                overdue = self._pos + (now - self._at) * self.rate - self.duration
                if overdue > END_GRACE * self.rate:
                    return False
            return True


# ----- the private audio thread ------------------------------------------------------------

class _Loop:
    """The audio thread: a daemon Python thread running a Qt event loop. Calls reach it through
    a queued Qt signal. Qt objects for each player are kept in `players`, keyed by a number, so
    they are only ever touched and destroyed on this thread."""

    def __init__(self):
        self.players: dict[int, object] = {}
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self.closed = False
        self.dispatcher = None
        self.loop = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="lyssna-audio")
        self.thread.start()
        self._ready.wait(10)
        if self._error is not None:
            raise self._error
        if self.dispatcher is None:
            raise ProviderError("The audio thread did not start")

    def _run(self) -> None:
        try:
            from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Signal, Slot

            global _app
            if QCoreApplication.instance() is None:
                _app = QCoreApplication([])  # no app yet (command line playback); this thread owns it

            class Dispatcher(QObject):
                call = Signal(object)

                @Slot(object)
                def run(self, fn):
                    try:
                        fn()
                    except Exception:
                        log.exception("audio call failed")

            self.dispatcher = Dispatcher()
            self.dispatcher.call.connect(self.dispatcher.run)
            self.loop = QEventLoop()
            _log_pitch_once()
        except BaseException as e:
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        self.loop.exec()
        self._shutdown()

    def _shutdown(self) -> None:
        """On the audio thread, after its loop ends at exit: let every player go and, if this
        thread made the application object, destroy it here. Qt objects must be destroyed on
        the thread they belong to, or Qt aborts the process as Python exits."""
        global _app
        from PySide6.QtCore import QCoreApplication, QEvent

        self.closed = True
        for key in list(self.players):
            _release(key)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.dispatcher = None
        self.loop = None
        if _app is not None:
            _app.shutdown()
            _app = None

    def post(self, fn: Callable[[], None]) -> None:
        dispatcher = self.dispatcher
        if dispatcher is not None and not self.closed:
            dispatcher.call.emit(fn)

    def quit(self) -> None:
        loop = self.loop
        if loop is not None and not self.closed:
            self.post(loop.quit)
            self.thread.join(2.0)
        _sweep()


_loop: _Loop | None = None
_loop_lock = threading.Lock()
_app = None
_ids = itertools.count(1)
_pitch_logged = False
_leftovers: list[str] = []  # temp files of released players not deleted yet


def _get_loop() -> _Loop:
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = _Loop()
            atexit.register(_loop.quit)
        return _loop


def run_on_audio_thread(fn: Callable[[], object], timeout: float) -> object:
    """Run fn on the private audio thread and return what it returns, or raise what it raises.
    Called from the audio thread itself, it runs at once (waiting there would deadlock). Shared
    with lyssna/win/mic.py: Qt's audio input has the same need for one thread with a Qt event
    loop, and one such thread is enough. Raises TimeoutError when fn has not finished in time."""
    loop = _get_loop()
    if threading.current_thread() is loop.thread:
        return fn()
    outcome: dict = {}
    done = threading.Event()

    def call() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as e:
            outcome["error"] = e
        finally:
            done.set()

    loop.post(call)
    if not done.wait(timeout):
        raise TimeoutError(f"the audio thread did not answer within {timeout:g} seconds")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _log_pitch_once() -> None:
    global _pitch_logged
    if _pitch_logged:
        return
    _pitch_logged = True
    try:
        from PySide6.QtMultimedia import QMediaPlayer

        probe = QMediaPlayer()
        log.info("Qt pitch compensation: %s", probe.pitchCompensationAvailability().name)
        probe.deleteLater()
    except Exception as e:
        log.info("Qt pitch compensation could not be checked: %s", e)


def pitch_compensation() -> str:
    """What Qt says about keeping the pitch at other speeds: AlwaysOn, Available or Unavailable."""
    result: list[str] = []
    done = threading.Event()

    def ask():
        from PySide6.QtMultimedia import QMediaPlayer

        probe = QMediaPlayer()
        result.append(probe.pitchCompensationAvailability().name)
        probe.deleteLater()
        done.set()

    _get_loop().post(ask)
    done.wait(LOAD_TIMEOUT)
    return result[0] if result else "unknown"


def _release(key: int) -> None:
    """On the audio thread: stop one player's Qt objects, let them go and delete its file."""
    entry = _loop.players.pop(key, None) if _loop is not None else None
    if entry is None:
        return
    player, output, path = entry
    try:
        from PySide6.QtCore import QUrl

        player.stop()
        player.setSource(QUrl())
    except Exception:
        log.debug("Qt player did not stop cleanly", exc_info=True)
    player.deleteLater()
    output.deleteLater()
    _leftovers.append(path)
    _sweep()


def _sweep() -> None:
    """Delete the temp files of released players. On Windows a file the decoder still holds
    open cannot be deleted yet; it is tried again on the next release and at exit."""
    for path in list(_leftovers):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            continue
        _leftovers.remove(path)


def _write_temp(audio: Audio) -> str:
    fd, path = tempfile.mkstemp(prefix="lyssna-", suffix=f".{audio.ext}")
    with os.fdopen(fd, "wb") as f:
        f.write(audio.data)
    return path


def _create(key: int, state: PlaybackState, audio: Audio, speed: float, outcome: dict, done: threading.Event) -> None:
    """On the audio thread: build the Qt player for one chunk and report when it is loaded."""
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    player = QMediaPlayer()
    output = QAudioOutput()
    player.setAudioOutput(output)
    path = _write_temp(audio)
    _loop.players[key] = (player, output, path)

    if player.pitchCompensationAvailability() == QMediaPlayer.PitchCompensationAvailability.Available:
        player.setPitchCompensation(True)
    player.setPlaybackRate(speed)

    def finish_loading(error: str = "") -> None:
        if done.is_set():
            return
        if error:
            outcome["error"] = error
        else:
            state.loaded(player.duration() / 1000.0)
        done.set()

    def on_status(status) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            finish_loading()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            finish_loading(player.errorString() or "the format was not recognised")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if state.end():
                _release(key)

    def on_error(error, message) -> None:
        if not done.is_set():
            finish_loading(message or str(error))
            return
        log.warning("playback failed: %s", message or error)
        if state.fail():
            _release(key)

    def on_duration(ms) -> None:
        if done.is_set() and ms > 0:
            state.loaded(ms / 1000.0)

    player.mediaStatusChanged.connect(on_status)
    player.errorOccurred.connect(on_error)
    player.durationChanged.connect(on_duration)
    player.positionChanged.connect(lambda ms: state.report_position(ms / 1000.0))
    player.setSource(QUrl.fromLocalFile(path))
    if player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia:
        finish_loading()


def _qt_call(key: int, method: str, *args) -> Callable[[], None]:
    def call() -> None:
        entry = _loop.players.get(key)
        if entry is not None:
            getattr(entry[0], method)(*args)
    return call


class QtPlayer:
    """The object the reading engine holds. It answers from PlaybackState and posts the real
    work to the audio thread. Safe to call from any thread."""

    def __init__(self, key: int, state: PlaybackState, loop: _Loop):
        self._key = key
        self._state = state
        self._loop = loop
        fin = weakref.finalize(self, loop.post, lambda: _release(key))  # a player dropped without stop() still lets go
        fin.atexit = False

    def play(self) -> None:
        if self._state.play():
            self._loop.post(_qt_call(self._key, "play"))

    def pause(self) -> None:
        if self._state.pause():
            self._loop.post(_qt_call(self._key, "pause"))

    def stop(self) -> None:
        if self._state.stop():
            key = self._key
            self._loop.post(lambda: _release(key))

    def currentTime(self) -> float:
        return self._state.current()

    def setCurrentTime_(self, seconds: float) -> None:
        if self._state.seek(seconds):
            self._loop.post(_qt_call(self._key, "setPosition", round(self._state.current() * 1000)))

    def duration(self) -> float:
        return self._state.duration

    def isPlaying(self) -> bool:
        return self._state.is_playing()

    def setRate_(self, rate: float) -> None:
        if self._state.set_rate(rate):
            self._loop.post(_qt_call(self._key, "setPlaybackRate", float(rate)))


def make_player(audio: Audio, speed: float) -> QtPlayer:
    try:
        import PySide6.QtMultimedia  # noqa: F401
    except ImportError as e:
        raise ProviderError(f"Could not play audio ({audio.ext}): PySide6 (Qt Multimedia) is not installed") from e
    loop = _get_loop()
    key = next(_ids)
    state = PlaybackState(rate=speed)
    outcome: dict = {}
    done = threading.Event()
    loop.post(lambda: _create(key, state, audio, float(speed), outcome, done))
    if not done.wait(LOAD_TIMEOUT):
        loop.post(lambda: _release(key))
        raise ProviderError(f"Could not decode audio ({audio.ext}): it did not load within {LOAD_TIMEOUT:g} seconds")
    if "error" in outcome:
        loop.post(lambda: _release(key))
        raise ProviderError(f"Could not decode audio ({audio.ext}): {outcome['error']}")
    return QtPlayer(key, state, loop)
