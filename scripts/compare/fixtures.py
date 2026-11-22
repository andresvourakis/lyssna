"""The fake data both sides draw from, so a legacy screen and its Qt twin show exactly the same
state: the reading, the voices, the setup conditions, the history entries with their fixed
clock, the dictionary, the What's New notes, the toasts and the tips.

Pure Python: nothing here imports AppKit or Qt."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

VF = (0.0, 25.0, 1440.0, 875.0)  # the fake screen's usable area, Mac-style (x, y up from the bottom, w, h)
VOICES = ([("heart", "Heart"), ("bella", "Bella")], "heart")
PROVIDER = "Kokoro · Heart"


def reading(state: str = "playing", index: int = 3, total: int = 9, position: float = 84.0, duration: float = 334.0,
            speed: float = 1.2, session: int = 1):
    """The reader's status (the Qt preview's _reading, field for field)."""
    from lyssna.player import Status

    return Status(state=state, index=index, total=total, position=position, duration=duration, speed=speed,
                  provider=PROVIDER, session=session)


# -- the setup window ---------------------------------------------------------------------------


class FakeModel:
    """A dictation model as the setup window reads it."""

    def __init__(self, total_bytes: int, present: bool, error: str = ""):
        self.total_bytes = total_bytes
        self._present = present
        self.error = error

    def present(self) -> bool:
        return self._present

    def download(self):
        return self


class FakeSetupController:
    """What both setup windows read, with no network and no real engine (a copy of the Qt
    preview's _FakeController, so the legacy window can take it too)."""

    def __init__(self, *, provider: str = "kokoro", ready: bool = False, progress: float | None = None,
                 keys: tuple[str, ...] = (), local_only: bool = False, mic: str = "granted", models: bool = False,
                 dict_progress: float | None = None, dict_keys: tuple[str, ...] = (), online: bool = False,
                 hotkey: str = "fn", double_tap: bool | None = None):
        dictation = {"transcriber": "openai" if online else "parakeet", "cleaner": "haiku" if online else "s1-mini",
                     "cleanup": True, "hotkey": hotkey, "hands_free": "space",
                     **({} if double_tap is None else {"double_tap": double_tap})}
        self.cfg = {"provider": provider, "hotkeys": {"speak": "ctrl+alt+s"}, "local_only": local_only, "dictation": dictation}
        self.ready = ready
        self.progress = progress
        self.keys = set(keys)
        self._local_only = local_only
        self.mic = mic
        self.models = models
        self.dict_progress = dict_progress
        self.dict_keys = set(dict_keys)

    def local_only(self) -> bool:
        return self._local_only

    def voice_download_progress(self) -> float | None:
        return self.progress

    def provider_configured(self, name: str) -> bool:
        from lyssna.providers import REGISTRY

        if name == "kokoro":
            return self.ready
        return name in self.keys or not REGISTRY[name].needs_key

    def set_provider(self, name: str) -> None:
        self.cfg["provider"] = name

    def add_api_key(self, name: str, key: str) -> str:
        return ""

    def speak_text(self, text: str) -> bool:
        return True

    def microphone_status(self) -> str:
        return self.mic

    def request_microphone(self) -> None:
        pass

    def local_dictation_models(self) -> list:
        from lyssna.dictation import models

        return [FakeModel(m.total_bytes, self.models) for m in (models.PARAKEET_V2, models.S1_MINI)]

    def dictation_download_progress(self) -> float | None:
        return self.dict_progress

    def use_local_dictation(self) -> None:
        self.cfg["dictation"].update(transcriber="parakeet", cleaner="s1-mini")

    def dictation_key_state(self, name: str) -> bool:
        return name in self.dict_keys

    def add_dictation_key(self, name: str, key: str) -> str:
        return ""

    def set_transcriber(self, name: str) -> None:
        self.cfg["dictation"]["transcriber"] = name

    def set_cleaner(self, name: str) -> None:
        self.cfg["dictation"]["cleaner"] = name

    def current_transcriber(self) -> str:
        from lyssna.dictation import transcribers

        name = self.cfg["dictation"]["transcriber"]
        return name if name in self.dict_keys or not transcribers.REGISTRY[name].needs_key else ""

    def current_cleaner(self) -> str:
        from lyssna.dictation import cleaners

        name = self.cfg["dictation"]["cleaner"]
        return name if name in self.dict_keys or not cleaners.REGISTRY[name].needs_key else ""


# One setup state: the controller's arguments and the window's conditions (the Qt preview's
# _preview arguments). Every state is on a Mac: the legacy window only exists there.
DONE = {"ready": True, "mic": "granted", "models": True}


def setup_state(ctl: dict | None = None, *, tab: str = "listen", engine: str | None = None, dictation: str | None = None,
                trusted: bool = True, taps_ok: bool = True, globe: str = "nothing", error: str = "",
                message: tuple[str, str] | None = None, saving: bool = False, key: str = "",
                dict_saving: int | None = None, dict_message: tuple[int, str, str] | None = None) -> dict:
    return {"ctl": dict(ctl or {}), "tab": tab, "engine": engine, "dictation": dictation, "trusted": trusted,
            "taps_ok": taps_ok, "globe": globe, "error": error, "message": message, "saving": saving, "key": key,
            "dict_saving": dict_saving, "dict_message": dict_message}


# -- the history window -------------------------------------------------------------------------

HISTORY_NOW = datetime(2026, 11, 17, 16, 0)  # a Tuesday, local time
HISTORY_ENTRIES = (  # (days ago, hour, minute, text, original): the Qt preview's entries
    (0, 15, 42, "Can you send the updated deck to Mattias before the call? He wants to check the numbers on slide four.",
     "can you send the updated deck to matias before the call he wants to check the numbers on slide four"),
    (0, 11, 8, "Booked the train to Uppsala for Thursday morning. Back the same evening.", ""),
    (0, 9, 30, "Notes from the standup. The build is green again after the font fix. Parakeet is now the default "
               "on Apple silicon and the cleanup model stays under two seconds on every machine we tried. Next we "
               "look at the Windows installer, the history window and the dictionary tab, then the side by side "
               "comparison with the Mac app. Kokoro needs one more pass on long sentences. Someone should also look "
               "at why the pill flickers when the screen sleeps, and we still owe the design review a short write-up.",
     ""),
    (1, 18, 5, "Thanks, that works for me. Let's keep the meeting at three.", ""),
    (1, 10, 12, "Remind me to call the dentist tomorrow.", ""),
    (4, 14, 20, "The quarterly summary is in the shared folder.", ""),
)
HISTORY_STATS = {"words_this_week": 1204, "words_total": 18930, "dictations": 412, "minutes": 0.0, "wpm": 0}
HISTORY_SIZE = (560.0, 680.0)
HISTORY_WIDE = (1040.0, 720.0)


def history_data(folder: Path, *, entries: bool = True, terms: bool = True):
    """(store, vocabulary, clock) in `folder`, filled as the Qt preview fills them."""
    from lyssna.dictation import history as history_mod
    from lyssna.dictation import vocabulary as vocabulary_mod
    from lyssna.dictation.types import DictationResult

    class Store(history_mod.HistoryStore):
        def stats(self) -> dict:
            return dict(HISTORY_STATS)

    folder.mkdir(parents=True, exist_ok=True)
    clock = {"t": HISTORY_NOW.astimezone()}
    store = Store(path=folder / "history.json", days=lambda: 30, now=lambda: clock["t"])
    vocab = vocabulary_mod.Vocabulary(path=folder / "vocabulary.json")
    if entries:
        for days_ago, hour, minute, text, raw in reversed(HISTORY_ENTRIES):
            clock["t"] = (HISTORY_NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute).astimezone()
            store.add(DictationResult(text=text, raw=raw or text.lower(), source="model", transcriber="parakeet",
                                      cleaner="s1-mini", audio_seconds=2.0, seconds={}, words=len(text.split())))
        clock["t"] = HISTORY_NOW.astimezone()
    if terms:
        vocab.add("Parakeet")
        vocab.learn("Matias", "Mattias")
        vocab.learn("Mathias", "Mattias")
        vocab.used("Mattias Mattias")
        vocab.add("Uppsala")
        vocab.learn("cocoro", "Kokoro")
    return store, vocab, (lambda: clock["t"])


# -- What's New, toasts, tips -----------------------------------------------------------------

WHATS_NEW_VERSION = "9.9.9"
WHATS_NEW_NOTES = (
    "Select text anywhere and press the shortcut: Lyssna reads it with the free voice on this Mac.",
    "Hold fn and speak. Lyssna types what you said, cleaned up, wherever your cursor is.",
    "The player tucks into the edge of the screen when you are not using it.",
)
SUPPORT_SITE = "https://example.test"

TOASTS = {
    "toast.text": ("Nothing selected", "", 640.0),
    "toast.action": ("Transcript cancelled", "Undo", 640.0),
    "toast.long": ("That key didn't work. Cartesia error 401: the API key you sent is not valid for this account, so "
                   "check it in your dashboard and paste it again.", "Open Settings", 460.0),
    "toast.download": ("Getting the speech model ready, 43%", "", 640.0),
}

TIP_ANCHOR = (100.0, 100.0, 34.0, 34.0)  # a Mac-style button rect the tip sits beside
TIPS = {
    "tip.left": ("left", "Dictate", "fn"),
    "tip.right": ("right", "Cancel", "esc"),
    "tip.bottom": ("bottom", "Finish", "fn"),
}
