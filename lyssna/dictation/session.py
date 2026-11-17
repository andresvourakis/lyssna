"""The dictation state machine: record, transcribe and clean, paste.

    idle -> recording (hold) -> working -> idle
    recording -> locked (hands-free) -> working -> idle
    recording | locked -> notice "Transcript cancelled" (Undo for UNDO_SECONDS) -> idle
    anything that goes wrong -> notice with a short message -> idle

start(), stop(), lock(), cancel() and undo_cancel() are called from the keyboard tap (main
thread) and the pill, so they only change the state and return; the microphone, the speech
pipeline and the paste run in order on one worker thread. Observers get a fresh
DictationStatus on every change and at the level-meter rate while recording, on whatever
thread produced it (the same convention as Controller.subscribe): UI observers hop to the
main thread themselves.

Never logged: the transcript, the audio. The log gets durations, word counts, engine names
and outcomes."""

from __future__ import annotations

import dataclasses
import logging
import queue
import threading
from collections.abc import Callable

import numpy as np

from . import recorder as recorder_mod
from .types import DictationError, DictationResult, DictationStatus, Pipeline

log = logging.getLogger("lyssna.dictation.session")

MIN_SECONDS = 0.3      # shorter than this is a slip of the finger, not a dictation
SILENCE_PEAK = 0.002   # a recording that never gets louder than this (about -54 dBFS) is silent
UNDO_SECONDS = 5.0     # how long a cancelled recording can be brought back
NOTICE_SECONDS = 3.0   # how long other notices stay

CANCELLED = "Transcript cancelled"
COPIED = "Copied. Press ⌘V to paste"
FAILED = "Dictation failed. Try again."
NO_MIC = "Lyssna can't use the microphone. Allow it in System Settings > Privacy & Security > Microphone."
ASK_MIC = "Allow the microphone, then dictate again."

_RECORDING = ("recording", "locked")
_ACTIVE = ("recording", "locked", "working")


class DictationSession:
    def __init__(
        self,
        pipeline: Pipeline,
        recorder,
        inserter: Callable[[str], str],
        *,
        watcher=None,
        auto_learn: Callable[[], bool] = lambda: True,
        before_start: Callable[[], None] | None = None,
        on_result: Callable[[DictationResult], None] | None = None,
        mic_status: Callable[[], str] = recorder_mod.microphone_status,
        request_mic: Callable[[Callable[[bool], None]], None] = recorder_mod.request_microphone,
        timer: Callable[[float, Callable[[], None]], object] | None = None,
        undo_seconds: float = UNDO_SECONDS,
        notice_seconds: float = NOTICE_SECONDS,
    ):
        self.pipeline = pipeline
        self.recorder = recorder
        self.inserter = inserter
        self.watcher = watcher
        self.auto_learn = auto_learn
        self.before_start = before_start
        self.on_result = on_result
        self.mic_status, self.request_mic = mic_status, request_mic
        self.timer = timer or _timer
        self.undo_seconds, self.notice_seconds = undo_seconds, notice_seconds
        self.last_result: DictationResult | None = None
        self._status = DictationStatus()
        self._observers: list[Callable[[DictationStatus], None]] = []
        self._lock = threading.RLock()
        self._epoch = 0                 # bumps on every transition; stale timers and jobs check it
        self._undo: np.ndarray | None = None   # the audio of the last cancelled recording, while Undo lasts
        self._undo_token: object | None = None   # which cancel that audio belongs to
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="lyssna-dictation").start()

    # -- observation ---------------------------------------------------------------------

    def subscribe(self, fn: Callable[[DictationStatus], None]) -> None:
        self._observers.append(fn)

    @property
    def status(self) -> DictationStatus:
        with self._lock:
            return dataclasses.replace(self._status)

    @property
    def state(self) -> str:
        return self._status.state

    def _emit(self) -> None:
        """Tell the observers. Never called with the lock held, so an observer that waits on
        another thread which is calling into the session cannot deadlock."""
        snapshot = self.status
        for fn in list(self._observers):
            try:
                fn(snapshot)
            except Exception:
                log.exception("observer failed")

    def _apply(self, **changes) -> int:
        """Change the status under the lock (the caller holds it); a new state bumps the epoch
        and clears what belonged to the old one. Returns the epoch. The caller then emits."""
        if "state" in changes:
            self._epoch += 1
            defaults = {"level": 0.0, "message": "", "can_undo": False}
            if changes["state"] not in ("recording", "locked"):
                defaults["seconds"] = 0.0 if changes["state"] == "idle" else self._status.seconds
            changes = {**defaults, **changes}
        for k, v in changes.items():
            setattr(self._status, k, v)
        return self._epoch

    def _set_if(self, epoch: int | None, **changes) -> int | None:
        """Apply `changes` if nothing else happened since `epoch` (None: unconditionally), then emit."""
        with self._lock:
            if epoch is not None and epoch != self._epoch:
                return None
            new = self._apply(**changes)
        self._emit()
        return new

    def _from(self, states: tuple[str, ...], **changes) -> int | None:
        """Apply `changes` only when the current state is one of `states`, then emit."""
        with self._lock:
            if self._status.state not in states:
                return None
            new = self._apply(**changes)
        self._emit()
        return new

    # -- actions -------------------------------------------------------------------------

    def start(self) -> bool:
        """Begin recording. False when it cannot start now (busy, or no microphone access)."""
        mic = self.mic_status()
        if mic != "granted":
            if self.state in _ACTIVE:
                return False
            if mic == "undetermined":
                self.request_mic(lambda granted: log.info("microphone access %s", "granted" if granted else "refused"))
                self._notice(ASK_MIC)
            else:
                self._notice(NO_MIC)
            return False
        with self._lock:
            if self._status.state in _ACTIVE:
                return False
            self._undo, self._undo_token = None, None
            epoch = self._apply(state="recording", session=self._status.session + 1, seconds=0.0)
        self._emit()
        if self.watcher is not None:
            self.watcher.stop()
        self._jobs.put(lambda: self._begin(epoch))
        return True

    def lock(self) -> None:
        """Hands-free: keep recording after the key is let go."""
        self._from(("recording",), state="locked")

    def stop(self) -> None:
        """Finish recording and process the audio."""
        epoch = self._from(_RECORDING, state="working")
        if epoch is not None:
            self._jobs.put(lambda: self._finish(self.recorder.stop(), epoch))

    def cancel(self, quiet: bool = False) -> None:
        """Drop the recording. Quietly (a tap, a shortcut) it just ends; otherwise a notice
        offers Undo for a few seconds, and undo_cancel() then processes the audio after all."""
        if quiet:
            if self._from(_RECORDING, state="idle") is not None:
                self._jobs.put(self.recorder.cancel)
            return
        token = object()
        with self._lock:
            if self._status.state not in _RECORDING:
                return
            epoch = self._apply(state="notice", message=CANCELLED, can_undo=True)
            self._undo, self._undo_token = None, token
        self._emit()
        self._jobs.put(lambda: self._keep_for_undo(token))

        def expire() -> None:
            with self._lock:
                if epoch != self._epoch:
                    return  # Undo was pressed, or a new dictation started
                self._undo, self._undo_token = None, None
            self._set_if(epoch, state="idle")
        self.timer(self.undo_seconds, expire)

    def undo_cancel(self) -> bool:
        """Bring back the recording that was just cancelled and process it as if stopped."""
        with self._lock:
            if not (self._status.state == "notice" and self._status.can_undo):
                return False
            token = self._undo_token
            epoch = self._apply(state="working")
        self._emit()
        self._jobs.put(lambda: self._process_undo(token, epoch))
        return True

    # -- worker side ------------------------------------------------------------------------

    def _worker(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:
                log.exception("dictation step failed")

    def _begin(self, epoch: int) -> None:
        if self.before_start is not None:
            try:
                self.before_start()
            except Exception:
                log.exception("before_start hook failed")
        if self.state not in _RECORDING:
            return  # stopped or cancelled before the microphone opened; the queued step follows
        try:
            self.recorder.start(on_level=self._level, on_stopped=self.stop)
        except Exception as e:
            log.warning("microphone did not start: %s", type(e).__name__)
            if self.state in _ACTIVE:
                self._notice(str(e) if isinstance(e, recorder_mod.RecorderError) else FAILED)
            return
        try:
            self.pipeline.warm_up()
        except Exception:
            log.exception("pipeline warm-up failed")

    def _level(self, level: float) -> None:
        self._from(_RECORDING, level=level, seconds=self.recorder.seconds())

    def _keep_for_undo(self, token: object) -> None:
        samples = self.recorder.stop()
        with self._lock:
            if self._undo_token is token:  # not expired, and no new dictation since
                self._undo = samples

    def _process_undo(self, token: object, epoch: int) -> None:
        with self._lock:
            samples = self._undo if self._undo_token is token else None
            self._undo, self._undo_token = None, None
        if samples is None:
            self._set_if(epoch, state="idle")
            return
        self._finish(samples, epoch)

    def _finish(self, samples: np.ndarray, epoch: int) -> None:
        if epoch != self._epoch:
            return  # the recording already ended another way (an error notice)
        seconds = len(samples) / recorder_mod.TARGET_RATE
        if seconds < MIN_SECONDS or float(np.max(np.abs(samples))) < SILENCE_PEAK:
            log.info("dictation dropped: %.2f s, too short or silent", seconds)
            self._set_if(epoch, state="idle")
            return
        try:
            result = self.pipeline.run(samples, recorder_mod.TARGET_RATE)
        except Exception as e:
            # A DictationError's message is meant for the screen (it may quote a provider): shown, not logged.
            log.warning("dictation pipeline failed: %s", type(e).__name__)
            self._notice((str(e) if isinstance(e, DictationError) else "") or FAILED)
            return
        self.last_result = result
        log.info("dictation: %.1f s of audio, %d words, %s + %s, source %s, %.2f s",
                 result.audio_seconds, result.words, result.transcriber, result.cleaner, result.source,
                 result.seconds.get("total", 0.0))
        if self.on_result is not None:
            try:
                self.on_result(result)
            except Exception:
                log.exception("on_result callback failed")
        if not result.text.strip():
            self._set_if(epoch, state="idle")
            return
        try:
            outcome = self.inserter(result.text)
        except Exception as e:
            log.warning("paste failed: %s", type(e).__name__)
            self._notice(FAILED)
            return
        log.info("dictation %s", outcome)
        if outcome == "copied":
            self._notice(COPIED)
            return
        self._set_if(epoch, state="idle")
        if self.watcher is not None and self.auto_learn():
            self.watcher.watch(result.text)

    def _notice(self, message: str) -> None:
        epoch = self._set_if(None, state="notice", message=message)
        self.timer(self.notice_seconds, lambda: self._set_if(epoch, state="idle"))


def _timer(delay: float, fn: Callable[[], None]) -> threading.Timer:
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t
