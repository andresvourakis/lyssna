"""Small types shared by the dictation engines (speech models, cleanup) and the io side
(microphone, hotkey, paste, session), so neither half imports the other's modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True)
class DictationResult:
    text: str            # what gets pasted
    raw: str             # the speech model's transcript, before any correction
    source: str          # "model", "rules:<guard code>", "rules:timeout", "rules:none", "empty"
    transcriber: str     # engine names, for history and the log
    cleaner: str
    audio_seconds: float
    seconds: dict[str, float]   # {"transcribe": .., "clean": .., "total": ..}
    words: int


class DictationError(RuntimeError):
    """A dictation that produced no text: the speech engine failed, or the chosen engine is not
    allowed. The message is for the on-screen notice (it may quote a provider's response), so
    it is never logged. `retryable` marks a failure inside transcription (an engine or provider
    error, a timeout): the same recording may work when run again, so it is kept for a retry."""

    def __init__(self, message: str = "", retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class Pipeline(Protocol):
    def run(self, samples: np.ndarray, sample_rate: int = 16000) -> DictationResult: ...
    def warm_up(self) -> None: ...    # called when recording starts; must return fast (start threads)


@dataclass
class DictationStatus:
    """What the pill shows. Observers get a fresh copy on every change."""

    state: str = "idle"    # idle | recording | locked | working | notice
    level: float = 0.0     # microphone level for the meter, 0..1 (recording and locked only)
    seconds: float = 0.0   # seconds recorded so far
    message: str = ""      # the notice text (state "notice"), else ""
    can_undo: bool = False  # a cancelled recording can still be brought back
    can_retry: bool = False  # the notice is a failed transcription whose recording was kept for a retry
    session: int = 0       # bumps on every new recording, so the UI can tell them apart

    def as_dict(self) -> dict:
        return dict(self.__dict__)
