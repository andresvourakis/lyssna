"""Application core: owns the Reader and the dictation session and exposes every user action
once, so the menu bar, hotkeys, floating UI, and the CLI socket all drive the same thing.

Reading and dictation exclude each other: starting a dictation pauses a reading (it stays
paused afterwards), and while a dictation records or works the read actions only say so.

Observers get a fresh Status (or DictationStatus, or Notice) on every change, on whatever
thread produced it; UI observers must hop to the main thread themselves.

The saved API keys are read once at launch on a background thread (see keystore.py: that read
can wait on a macOS password prompt). Until it lands, every question the menu and the windows
ask here answers as if the key were there, and nothing is decided from it: the launch check
that moves an engine without a key to the voice on this Mac waits for the real answer."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import config, host, keystore, migrate, policy, usage
from .cache import AudioCache, cache_enabled
from .dictation import cleaners, transcribers
from .dictation import models as dictation_models
from .dictation.session import DictationSession
from .dictation.types import DictationResult, DictationStatus
from .host import capture  # lyssna.mac.capture on the Mac, lyssna.win.capture on Windows
from .menu import dictation_privacy
from .player import Reader, Status
from .providers import REGISTRY, allowed, effective_provider, get_provider, kokoro, local_fallback
from .providers.base import error_summary

log = logging.getLogger("lyssna.controller")

SPEED_STEP = 0.1

DICTATING = ("recording", "locked", "working")
_RECORDING = ("recording", "locked")
MIC_SETTINGS_URL = host.MIC_SETTINGS_URL
KEYBOARD_SETTINGS_URL = host.KEYBOARD_SETTINGS_URL
GLOBE_MARKER = "globe-key-hint-shown"  # in STATE_DIR, like the `welcomed` marker

ASK_MIC = "Allow microphone access to dictate"
NO_MIC = "Lyssna can't use the microphone"
OPEN_SETTINGS = "Open Settings"
MODEL_PROGRESS = "Getting the speech model ready, {pct}%"
MODEL_READY = "The speech model is ready. Dictate again."
MODEL_FAILED = "The speech model did not download. Try again."
NO_ENGINE = "Dictation can't run in this install"
GLOBE_HINT = "Set \u201cPress Globe key to\u201d to Do Nothing in Keyboard settings"
BUSY = "Finish dictating first"


@dataclass(frozen=True)
class Notice:
    """A short message for the toast at the bottom of the screen, with at most one action."""

    text: str
    action: str = ""                            # the action button's label; "" for none
    on_action: Callable[[], None] | None = None
    seconds: float = 5.0
    key: str = ""  # a notice with the same key as the one on screen replaces it in place (progress)


def key_sections() -> list[str]:
    """Every config section an engine keeps an API key under (voices and dictation)."""
    engines = [*REGISTRY.values(), *transcribers.REGISTRY.values(), *cleaners.REGISTRY.values()]
    return sorted({cls.section() for cls in engines if cls.needs_key})


def open_url(url: str) -> None:
    """Open a System Settings pane (or any URL) without touching our stdio."""
    host.open_target(url)


class Controller:
    def __init__(self):
        self.cfg = config.load()
        self.fell_back_to = ""  # the engine we switched to at launch because the chosen one could not run
        self._provider_checked = False
        self._check_lock = threading.Lock()
        self._key_observers: list[Callable[[], None]] = []
        self._observers: list[Callable[[Status], None]] = []
        cache = AudioCache.from_config(self.cfg)
        cache.trim_in_background()  # once per launch; puts trim again every so often
        self.reader = Reader(self._provider, speed=float(self.cfg.get("speed", 1.0)), cache=cache, on_change=self._changed)
        self.message: str = ""  # transient user-facing notice (errors, "nothing selected")
        self.extensions: dict[str, Callable[[dict], dict]] = {}  # extra socket commands registered by the app layer
        self.selection_button_enabled: bool = bool(self.cfg.get("ui", {}).get("selection_button", True))
        self.auto_hide_enabled: bool = bool(self.cfg.get("ui", {}).get("auto_hide", True))
        self.wave_enabled: bool = bool(self.cfg.get("ui", {}).get("wave", True))
        self._keep_dictation_local()
        self._toast_observers: list[Callable[[Notice], None]] = []
        self._dictation_lock = threading.Lock()
        self._last_dictation: tuple[str, str] = ("idle", "")  # (state, message) last seen, so a notice toasts once
        self._watching_models = False
        self._globe_checked = False
        self.push_to_talk = None  # the app's PushToTalk, kept in step with the session (see _dictation_changed)
        self.dictation = self._make_dictation()
        keystore.subscribe(self._keys_read)
        keystore.load(key_sections(), first=self._move_keys_to_keychain)
        self._check_provider()  # at once when the answer is known already (no key needed, or cached)

    # -- API keys at launch ------------------------------------------------------------

    def _move_keys_to_keychain(self) -> None:
        """On the background key thread: the one-time move of keys out of the config file."""
        if migrate.keys_to_keychain():
            self.cfg = config.load()

    def _keys_read(self) -> None:
        """A background Keychain read has landed: make the launch check, then tell the menu."""
        self._check_provider()
        for fn in list(self._key_observers):
            try:
                fn()
            except Exception:
                log.exception("key observer failed")

    def subscribe_keys(self, fn: Callable[[], None]) -> None:
        """fn() is called (on a background thread) whenever saved keys have been read."""
        self._key_observers.append(fn)

    def _check_provider(self) -> None:
        """Once per launch: if the chosen voice cannot run as set up (an online one with no
        key, say), switch to the one on this Mac. Waits for the Keychain's real answer, so a
        slow password prompt never rewrites the provider in the config file."""
        with self._check_lock:
            if self._provider_checked:
                return
            name = self.cfg.get("provider", "")
            if (name in REGISTRY and allowed(name, self.cfg)
                    and get_provider(name, self.cfg).configured(wait=False) is None):
                return  # not known yet: _keys_read calls again when it is
            self._provider_checked = True
            fallback = effective_provider(self.cfg, wait=False)  # the key's answer is known by now
            if fallback != self.cfg.get("provider"):
                log.info("provider %s cannot run as set up; falling back to %s", self.cfg.get("provider"), fallback)
                self.cfg = config.update(provider=fallback)
                self.fell_back_to = fallback

    # -- observation -------------------------------------------------------------

    def subscribe(self, fn: Callable[[Status], None]) -> None:
        self._observers.append(fn)

    def _changed(self, status: Status) -> None:
        snapshot = dataclasses.replace(status)
        if snapshot.error:
            # The message can quote a provider's response, which can echo the text: on screen only.
            log.warning("reading stopped with an error (%d chars, shown on screen, not logged)", len(snapshot.error))
            self.notify(snapshot.error)
            status.error = ""
        for fn in list(self._observers):
            try:
                fn(snapshot)
            except Exception:
                log.exception("observer failed")

    def notify(self, message: str, ttl: float = 3.0) -> None:
        self.message = message

        def clear():
            if self.message == message:
                self.message = ""
        threading.Timer(ttl, clear).start()

    @property
    def status(self) -> Status:
        return self.reader.status

    def status_dict(self) -> dict:
        d = self.reader.status.as_dict()
        d["message"] = self.message
        d["provider_name"] = self.cfg.get("provider")
        d["selection_button"] = self.selection_button_enabled
        d["voice"] = self.current_voice()
        d["voice_download"] = self.voice_download_progress()
        d["dictation"] = self.dictation_status.as_dict()
        return d

    # -- config ------------------------------------------------------------------

    def reload_config(self) -> dict:
        self.cfg = config.load()
        return self.cfg

    def _provider(self):
        self.reload_config()
        self._keep_local()  # the config file or the managed preference may have changed since
        self.reader.cache.enabled = cache_enabled(self.cfg)  # `cache = false` applies from the next reading
        return get_provider(self.cfg["provider"], self.cfg)

    def set_provider(self, name: str) -> None:
        """Switch engines and let the new one start any one-time setup (Kokoro downloads its
        voice in the background; a reading asked for meanwhile waits for it). Refuses an
        online engine while local-only mode is on."""
        if name not in REGISTRY:
            raise ValueError(f"unknown provider {name}")
        if not allowed(name, self.cfg):
            raise ValueError(policy.refusal(REGISTRY[name].label))
        self.cfg = config.update(provider=name)
        get_provider(name, self.cfg).prepare(on_error=lambda message: self.notify(message, ttl=6.0))

    def current_transcriber(self) -> str:
        """The speech engine dictation will really use ("" when none can run). Never waits
        for the Keychain: a key not read yet counts as there."""
        return transcribers.effective_transcriber(self.cfg, wait=False)

    def current_cleaner(self) -> str:
        """The cleanup engine dictation will really use; "" when cleanup is off or none can
        run. Never waits for the Keychain: a key not read yet counts as there."""
        return cleaners.effective_cleaner(self.cfg, wait=False)

    def set_transcriber(self, name: str) -> None:
        """Use this speech engine for dictation, and let it start any one-time setup (the
        model on this Mac downloads in the background). Refuses an online engine while
        local-only mode is on."""
        cls = transcribers.REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown speech engine {name}")
        if not transcribers.allowed(name, self.cfg):
            raise ValueError(transcribers.refusal(cls.label))
        self.cfg = config.update(**{"dictation.transcriber": name})
        log.info("dictation speech engine set to %s", name)
        transcribers.get_transcriber(name, self.cfg).prepare(on_error=lambda message: self.toast(Notice(message)))

    def set_cleaner(self, name: str) -> None:
        """Use this cleanup engine for dictation; "" turns cleanup off (fillers and capitals
        only). Refuses an online engine while local-only mode is on."""
        if not name:
            self.cfg = config.update(**{"dictation.cleanup": False})
            log.info("dictation cleanup off")
            return
        cls = cleaners.REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown cleanup engine {name}")
        if not cleaners.allowed(name, self.cfg):
            raise ValueError(cleaners.refusal(cls.label))
        self.cfg = config.update(**{"dictation.cleanup": True, "dictation.cleaner": name})
        log.info("dictation cleanup set to %s", name)
        cleaners.get_cleaner(name, self.cfg).prepare(on_error=lambda message: self.toast(Notice(message)))

    def transcriber_configured(self, name: str) -> bool:
        """Set up to run, without waiting for the Keychain (a key not read yet counts as there)."""
        return transcribers.get_transcriber(name, self.cfg).configured(wait=False) is not False

    def cleaner_configured(self, name: str) -> bool:
        """Set up to run, without waiting for the Keychain (a key not read yet counts as there)."""
        return cleaners.get_cleaner(name, self.cfg).configured(wait=False) is not False

    def local_only(self) -> bool:
        return policy.local_only(self.cfg)

    def local_only_managed(self) -> bool:
        """True when an administrator has forced local-only mode on: the user cannot turn it off."""
        return policy.managed_local_only()

    def set_local_only(self, enabled: bool) -> None:
        """Turn local-only mode on or off (persisted). Turning it on moves off an online engine."""
        if not enabled and self.local_only_managed():
            raise ValueError(f"\"Keep Everything on This {host.DEVICE}\" is set by your organization")
        self.cfg = config.update(local_only=bool(enabled))
        log.info("local-only mode %s", "on" if enabled else "off")
        self._keep_local()

    def _keep_local(self) -> None:
        """If local-only mode is on and the current engine is an online one, switch to the
        local fallback so not one more sentence leaves this Mac."""
        name = self.cfg.get("provider", "")
        if name in REGISTRY and not allowed(name, self.cfg):
            fallback = local_fallback()
            log.info("local-only mode: switching from %s to %s", name, fallback)
            self.set_provider(fallback)
        self._keep_dictation_local()

    def _keep_dictation_local(self) -> None:
        """The same for dictation: with local-only mode on, an online speech or cleanup engine
        in the config is replaced by the one on this Mac."""
        section = self.cfg.get("dictation", {})
        changes = {}
        name = str(section.get("transcriber") or transcribers.LOCAL)
        if name in transcribers.REGISTRY and not transcribers.allowed(name, self.cfg):
            changes["dictation.transcriber"] = transcribers.LOCAL
        name = str(section.get("cleaner") or cleaners.LOCAL)
        if name in cleaners.REGISTRY and not cleaners.allowed(name, self.cfg):
            changes["dictation.cleaner"] = cleaners.LOCAL
        if changes:
            log.info("local-only mode: dictation moves to the engines on this %s (%s)", host.DEVICE, ", ".join(changes))
            self.cfg = config.update(**changes)

    def voice_download_progress(self) -> float | None:
        """Fraction (0..1) of the Kokoro voice downloaded while that download runs, else None."""
        return kokoro.DOWNLOAD.progress()

    def dictation_download_progress(self) -> float | None:
        """Fraction (0..1) of the dictation models downloaded while any of them downloads, else None."""
        active = [d for d in list(dictation_models.DOWNLOADS.values()) if d.active()]
        if not active:
            return None
        total = sum(d.total_bytes for d in active)
        return min(1.0, sum(d.done_bytes for d in active) / total) if total else 0.0

    def privacy_note(self) -> str:
        """One line for the menu saying where the text goes with the current engine."""
        cls = REGISTRY.get(self.cfg.get("provider"))
        if cls is None or not cls.sends_text_off_device:
            return f"Text stays on this {host.DEVICE}"
        return f"Text is sent to {get_provider(cls.name, self.cfg).recipient()}"

    def dictation_privacy_note(self) -> str:
        """One line for the menu saying where speech (and the text, when an online cleaner
        tidies it) goes with the dictation engines that will really run."""
        return dictation_privacy(*self._dictation_recipients())

    def dictation_stays_local(self) -> bool:
        """Whether dictation, with the engines that will really run, sends nothing anywhere."""
        return not any(self._dictation_recipients())

    def _dictation_recipients(self) -> tuple[str, str]:
        """Who receives the speech and who the text, "" for this Mac."""
        name = transcribers.effective_transcriber(self.cfg, wait=False)
        speech_to = ""
        if name and transcribers.REGISTRY[name].sends_audio_off_device:
            speech_to = transcribers.get_transcriber(name, self.cfg).recipient()
        name = cleaners.effective_cleaner(self.cfg, wait=False)
        text_to = ""
        if name and cleaners.REGISTRY[name].sends_text_off_device:
            text_to = cleaners.get_cleaner(name, self.cfg).recipient()
        return speech_to, text_to

    def provider_label(self) -> str:
        cls = REGISTRY.get(self.cfg.get("provider"))
        return cls.label if cls else "?"

    def voices(self) -> list[tuple[str, str]]:
        """(voice id, menu label) pairs the current engine offers; empty when it has no picker."""
        cls = REGISTRY.get(self.cfg.get("provider"))
        return list(cls.voices) if cls else []

    def current_voice(self) -> str:
        cls = REGISTRY.get(self.cfg.get("provider"))
        if cls is None:
            return ""
        return self.cfg.get(cls.section(), {}).get("voice") or cls.default_voice

    def set_voice(self, name: str) -> None:
        """Persist the voice for the current engine. A reading in progress restarts at the
        current sentence with the new voice, staying paused if it was paused."""
        if name not in [v for v, _ in self.voices()]:
            raise ValueError(f"unknown voice {name}")
        if name == self.current_voice():
            return
        cls = REGISTRY[self.cfg["provider"]]
        self.cfg = config.update(**{f"{cls.section()}.voice": name})
        log.info("voice set to %s", name)
        if self.reader.is_active():
            self.reader.restart_from_current()

    def provider_configured(self, name: str) -> bool:
        """Set up to read, without waiting for the Keychain (a key not read yet counts as there)."""
        return get_provider(name, self.cfg).configured(wait=False) is not False

    def add_api_key(self, name: str, key: str) -> str:
        """Prove a key works with one real synthesis, then save it and switch to that engine.
        Returns "" on success, else a short message for the user. Blocks for as long as the
        request takes (the engine's own timeout, no retries): call it off the main thread."""
        cls = REGISTRY[name]
        if not allowed(name, self.cfg):
            return policy.refusal(cls.label)
        key = key.strip()
        if not key:
            return "Paste your API key first."
        if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in key):
            return "That doesn't look like an API key. Keys have no spaces or line breaks, so copy it again."
        section = cls.section()
        try:
            cls(self.cfg.get(section, {}), api_key=key).synthesize("Hello from Lyssna.")
        except Exception as e:
            log.warning("new %s key rejected: %s", name, error_summary(e))
            return f"That key didn't work. {e}"
        where = host.KEY_STORE if config.save_api_key(section, key) else "the config file"
        log.info("saved a new %s API key in %s", section, where)
        self.set_provider(name)
        return ""

    def _dictation_engine(self, name: str):
        """(cls, set_engine, allowed, refusal) for a speech or cleanup engine by name."""
        if name in transcribers.REGISTRY:
            return transcribers.REGISTRY[name], self.set_transcriber, transcribers.allowed, transcribers.refusal
        if name in cleaners.REGISTRY:
            return cleaners.REGISTRY[name], self.set_cleaner, cleaners.allowed, cleaners.refusal
        raise ValueError(f"unknown dictation engine {name}")

    def dictation_key_state(self, name: str) -> bool | None:
        """Does this dictation engine have its key? None while the Keychain has not answered."""
        cls = self._dictation_engine(name)[0]
        return cls(self.cfg.get(cls.section(), {})).configured(wait=False)

    def add_dictation_key(self, name: str, key: str) -> str:
        """The dictation counterpart of add_api_key: prove the key works with one request that
        costs nothing, save it where voice keys go, and use that engine for dictation (as the
        speech engine or the cleanup). The voice provider is left as it is. Returns "" on
        success, else a short message for the user. Blocks for the request: call it off the
        main thread."""
        cls, use, is_allowed, refusal = self._dictation_engine(name)
        if not is_allowed(name, self.cfg):
            return refusal(cls.label)
        key = key.strip()
        if not key:
            return "Paste your API key first."
        if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in key):
            return "That doesn't look like an API key. Keys have no spaces or line breaks, so copy it again."
        section = cls.section()
        error = cls(self.cfg.get(section, {}), api_key=key).check_key()
        if error:
            log.warning("new %s key for dictation rejected (%d chars of detail, not logged)", section, len(error))
            return error
        where = host.KEY_STORE if config.save_api_key(section, key) else "the config file"
        log.info("saved a new %s API key in %s", section, where)
        use(name)
        return ""

    def local_dictation_models(self) -> list:
        """The model files dictation on this Mac uses that can run in this install: the speech
        model for the configured language, and the cleanup model where it runs (unless cleanup
        is off)."""
        found = []
        if transcribers.REGISTRY[transcribers.LOCAL].can_run():
            found.append(transcribers.get_transcriber(transcribers.LOCAL, self.cfg).model())
        if cleaners.cleanup_on(self.cfg) and cleaners.REGISTRY[cleaners.LOCAL].can_run():
            found.append(dictation_models.S1_MINI)
        return found

    def use_local_dictation(self) -> None:
        """Dictate with the engines on this Mac; picking each starts its model's download if
        it is missing. Cleanup stays off when it was turned off."""
        self.set_transcriber(transcribers.LOCAL)
        if cleaners.cleanup_on(self.cfg) and cleaners.REGISTRY[cleaners.LOCAL].can_run():
            self.set_cleaner(cleaners.LOCAL)

    def microphone_status(self) -> str:
        """"granted", "denied" or "undetermined"."""
        return self.dictation.mic_status()

    def request_microphone(self) -> None:
        """Ask macOS for microphone access (it shows its prompt only the first time)."""
        self.dictation.request_mic(self._mic_answered)

    def has_stored_key(self, name: str) -> bool:
        """Is there a key for this engine in the Keychain or the config file (the places the
        app can remove it from; a key in the environment is out of its reach)? Never waits for
        the Keychain: False until it has been read."""
        section = REGISTRY[name].section()
        return bool(config.file_api_key(section) or keystore.has_key(section))

    def remove_api_key(self, name: str) -> None:
        """Forget the saved key for this engine (and any engine sharing its config section).
        If the current engine used it, move to the free voice on this Mac."""
        section = REGISTRY[name].section()
        keystore.delete(section)
        if config.file_api_key(section):
            self.cfg = config.update(**{f"{section}.api_key": ""})
        log.info("removed the %s API key", section)
        current = REGISTRY.get(self.cfg.get("provider"))
        if current is not None and current.needs_key and current.section() == section:
            self.set_provider(local_fallback())

    def set_selection_button(self, enabled: bool) -> None:
        """Show or hide the play button that pops up after selecting text. Persisted."""
        self.selection_button_enabled = enabled
        self.cfg = config.update(**{"ui.selection_button": enabled})
        log.info("selection play button %s", "on" if enabled else "off")
        self.notify("Play button on" if enabled else "Play button off", ttl=1.5)

    def toggle_selection_button(self) -> None:
        self.set_selection_button(not self.selection_button_enabled)

    def set_auto_hide(self, enabled: bool) -> None:
        """Keep the idle pill out of sight until the cursor comes near its spot. Persisted;
        the app applies it to the pill."""
        self.auto_hide_enabled = enabled
        self.cfg = config.update(**{"ui.auto_hide": enabled})
        log.info("idle pill auto-hide %s", "on" if enabled else "off")

    def set_wave(self, enabled: bool) -> None:
        """Let the resting wave ripple now and then. Persisted; the app applies it to the pill."""
        self.wave_enabled = enabled
        self.cfg = config.update(**{"ui.wave": enabled})
        log.info("idle pill wave %s", "on" if enabled else "off")

    def player_layout(self) -> dict:
        """Where the mini player was last left: dock ("left"/"right"/"bottom"/"") and position
        fractions (docked, y is the position along that edge)."""
        ui = self.cfg.get("ui", {})
        return {"dock": str(ui.get("player_dock", "")), "x": float(ui.get("player_x", -1)), "y": float(ui.get("player_y", -1))}

    def save_player_layout(self, dock: str, x: float, y: float) -> None:
        self.cfg = config.update(**{"ui.player_dock": dock, "ui.player_x": round(x, 4), "ui.player_y": round(y, 4)})

    def dictation_layout(self) -> dict:
        """Where the idle pill was last left while the player was not docked: edge ("left",
        "right", "bottom", or "" for the default bottom centre) and the position along it."""
        ui = self.cfg.get("ui", {})
        return {"dock": str(ui.get("dictation_dock", "")), "y": float(ui.get("dictation_y", -1))}

    def save_dictation_layout(self, dock: str, y: float) -> None:
        self.cfg = config.update(**{"ui.dictation_dock": dock, "ui.dictation_y": round(y, 4)})

    def usage_summary(self) -> str:
        return usage.summary({n: c.price_per_million for n, c in REGISTRY.items()})

    # -- actions -----------------------------------------------------------------

    def speak_text(self, text: str) -> bool:
        if self._busy_dictating():
            return False
        return self.reader.speak(text)

    def speak_selection(self) -> None:
        """Grab whatever is selected in the front app and read it. Runs async: capture can take ~0.5s."""
        if self._busy_dictating():
            return

        def run():
            text, source = capture.selection_or_clipboard()
            if not text.strip():
                self.notify("Nothing selected")
                return
            log.info("speaking %d chars from %s", len(text), source)
            self.reader.speak(text)

        threading.Thread(target=run, daemon=True, name="lyssna-capture").start()

    def speak_clipboard(self) -> None:
        if self._busy_dictating():
            return
        text = capture.clipboard_text()
        if not text.strip():
            self.notify("Clipboard is empty")
            return
        self.reader.speak(text)

    def toggle_pause(self) -> None:
        if self.dictating() and self.reader.status.state == "paused":
            self._busy_dictating()  # resuming now would read into the microphone
            return
        self.reader.toggle_pause()

    def stop(self) -> None:
        self.reader.stop()

    def next(self) -> None:
        self.reader.next()

    def prev(self) -> None:
        self.reader.prev()

    def seek_to(self, fraction: float) -> None:
        """Scrub to a fraction (0..1) of the whole reading."""
        self.reader.seek_to(fraction)

    def set_speed(self, speed: float) -> float:
        speed = self.reader.set_speed(speed)
        self.cfg = config.update(speed=speed)
        return speed

    def faster(self) -> None:
        self.set_speed(self.reader.speed + SPEED_STEP)

    def slower(self) -> None:
        self.set_speed(self.reader.speed - SPEED_STEP)

    def shutdown(self) -> None:
        self.reader.stop()
        kokoro.DOWNLOAD.cancel()  # the partial file stays; the next attempt resumes it
        self.dictation.cancel(quiet=True)
        self.recorder.cancel()  # the session cancels on its worker; this releases the microphone now
        if self.dictation.watcher is not None:
            self.dictation.watcher.stop()
        for download in list(dictation_models.DOWNLOADS.values()):
            download.cancel()
        from .dictation.cleaners import s1mini

        s1mini.unload()

    # -- dictation -------------------------------------------------------------------

    def _make_dictation(self, pipeline=None, recorder=None, inserter=None, watcher=None, **options) -> DictationSession:
        """The dictation session with the real pipeline, microphone, paste and correction
        watcher; tests pass fakes for any of them (and the session's own options)."""
        if pipeline is None:
            from .dictation.pipeline import DictationPipeline

            pipeline = DictationPipeline(self._dictation_config)
        if recorder is None:
            from .dictation.recorder import Recorder

            recorder = Recorder()
        if inserter is None:
            from .dictation.insert import Inserter

            inserter = Inserter()
        if watcher is None:
            from .dictation.learn import EditWatcher

            watcher = EditWatcher(self._learned)
        self.recorder = recorder
        session = DictationSession(
            pipeline, recorder, inserter, watcher=watcher, auto_learn=self._auto_learn,
            before_start=self._pause_reading, on_result=self._record_result, **options,
        )
        session.subscribe(self._dictation_changed)
        return session

    def _dictation_config(self) -> dict:
        """The config for one dictation, read fresh, with local-only mode applied."""
        self.reload_config()
        self._keep_dictation_local()
        return self.cfg

    def _auto_learn(self) -> bool:
        return bool(self.cfg.get("dictation", {}).get("auto_learn", True))

    @property
    def dictation_status(self) -> DictationStatus:
        return self.dictation.status

    def dictating(self) -> bool:
        """A dictation is recording or being turned into text."""
        return self.dictation.state in DICTATING

    def subscribe_dictation(self, fn: Callable[[DictationStatus], None]) -> None:
        self.dictation.subscribe(fn)

    def subscribe_toast(self, fn: Callable[[Notice], None]) -> None:
        self._toast_observers.append(fn)

    def toast(self, notice: Notice) -> None:
        for fn in list(self._toast_observers):
            try:
                fn(notice)
            except Exception:
                log.exception("toast observer failed")

    def dictation_start(self, hands_free: bool = False) -> bool:
        """Start recording; hands-free keeps recording until dictation_stop(). False when it
        cannot start now: busy, no microphone access yet, or the speech model still has to
        download (each says so in a toast). Called inline from the keyboard tap: stays quick."""
        if self.dictating() or not self._ready_to_record():
            return False
        if not self.dictation.start():
            return False
        if hands_free:
            self.dictation.lock()
            if self.push_to_talk is not None:
                self.push_to_talk.enter_locked()  # so the dictation key finishes it and Escape cancels it
        self._globe_hint_once()
        return True

    def dictation_lock(self) -> None:
        self.dictation.lock()

    def dictation_stop(self) -> None:
        self.dictation.stop()

    def dictation_cancel(self, quiet: bool = False) -> None:
        """Drop the recording; unless quiet, the toast offers Undo for a few seconds."""
        self.dictation.cancel(quiet=quiet)

    def dictation_undo(self) -> bool:
        """Bring back the recording that was just cancelled and turn it into text after all."""
        return self.dictation.undo_cancel()

    def warm_dictation(self) -> None:
        """At launch: get the microphone ready in the background, once access is granted."""
        threading.Thread(target=self.recorder.warm, daemon=True, name="lyssna-mic-warm").start()

    def _busy_dictating(self) -> bool:
        if not self.dictating():
            return False
        self.toast(Notice(BUSY, seconds=2.0))
        return True

    def _pause_reading(self) -> None:
        """The session's before_start: a reading in progress pauses and stays paused."""
        if self.reader.status.state in ("playing", "loading"):
            log.info("pausing the reading for a dictation")
            self.reader.toggle_pause()

    def _ready_to_record(self) -> bool:
        """First use: microphone access, then the speech model. Each missing piece is asked
        for (or downloaded) and explained in a toast; recording waits until all is there."""
        mic = self.dictation.mic_status()
        if mic == "undetermined":
            self.dictation.request_mic(self._mic_answered)
            self.toast(Notice(ASK_MIC))
            return False
        if mic != "granted":
            self.toast(Notice(NO_MIC, OPEN_SETTINGS, lambda: open_url(MIC_SETTINGS_URL), seconds=8.0))
            return False
        cfg = self.cfg
        name = transcribers.effective_transcriber(cfg, wait=False)  # on the key tap: never wait for the Keychain
        if not name:
            self.toast(Notice(NO_ENGINE))
            return False
        needed = []  # models the speech engine cannot run without
        engine = transcribers.get_transcriber(name, cfg)
        if not engine.sends_audio_off_device and hasattr(engine, "model") and not engine.model().present():
            needed.append(engine.model())
        extra = []  # the cleanup model: dictation works without it (rules only), so it does not block
        if cleaners.effective_cleaner(cfg, wait=False) == cleaners.LOCAL and not dictation_models.S1_MINI.present():
            extra.append(dictation_models.S1_MINI)
        if needed:
            self._get_models(needed, extra)
            return False
        for model in extra:
            model.download().start()
        return True

    def _mic_answered(self, granted: bool) -> None:
        log.info("microphone access %s", "granted" if granted else "refused")
        if granted:
            self.warm_dictation()

    def _get_models(self, needed: list, extra: list) -> None:
        """Download what dictation needs, with progress in a toast (and in the menu, which
        reads dictation_download_progress), then say when to try again."""
        for model in (*needed, *extra):
            model.download().start()
        self.toast(Notice(MODEL_PROGRESS.format(pct=int((self.dictation_download_progress() or 0.0) * 100)),
                          seconds=3.0, key="models"))
        with self._dictation_lock:
            if self._watching_models:
                return
            self._watching_models = True
        threading.Thread(target=self._watch_models, args=(needed,), daemon=True, name="lyssna-model-progress").start()

    def _watch_models(self, needed: list, poll: float = 1.0) -> None:
        try:
            while (progress := self.dictation_download_progress()) is not None:
                self.toast(Notice(MODEL_PROGRESS.format(pct=int(progress * 100)), seconds=3.0, key="models"))
                time.sleep(poll)
        finally:
            with self._dictation_lock:
                self._watching_models = False
        if all(model.present() for model in needed):
            self.toast(Notice(MODEL_READY, key="models"))
            return
        errors = [model.download().error for model in needed if model.download().error]
        self.toast(Notice(errors[0] if errors else MODEL_FAILED, key="models", seconds=8.0))

    def _globe_hint_once(self) -> None:
        """With Fn as the dictation key, macOS must not also react to the Globe key. Checked
        off the tap's thread; the hint shows once ever."""
        if not host.HAS_GLOBE_KEY or self._globe_checked or str(self.cfg.get("dictation", {}).get("hotkey", "fn")) != "fn":
            return
        self._globe_checked = True
        marker = config.STATE_DIR / GLOBE_MARKER
        if marker.exists():
            return

        def check() -> None:
            action = host.globe.globe_key_action()
            if action in ("nothing", "unknown"):
                return
            try:
                marker.write_text("")
            except OSError:
                log.warning("could not remember that the Globe key hint was shown")
            self.toast(Notice(GLOBE_HINT, OPEN_SETTINGS, lambda: open_url(KEYBOARD_SETTINGS_URL), seconds=8.0))

        threading.Thread(target=check, daemon=True, name="lyssna-globe-check").start()

    def _dictation_changed(self, st: DictationStatus) -> None:
        """Keep the push-to-talk key in step with the session, and turn its notices into toasts."""
        ptt = self.push_to_talk
        if ptt is not None and st.state not in _RECORDING and self.dictation.state not in _RECORDING:
            ptt.reset()  # ended by the pill, the time cap or an error: Escape must not be swallowed now
        with self._dictation_lock:
            seen = (st.state, st.message)
            if seen == self._last_dictation:
                return
            self._last_dictation = seen
        if st.state != "notice" or not st.message:
            return
        if st.can_undo:
            self.toast(Notice(st.message, "Undo", self.dictation_undo, seconds=self.dictation.undo_seconds))
        else:
            self.toast(Notice(st.message))

    def _record_result(self, result: DictationResult) -> None:
        """Keep the dictation in the history, when this build has one."""
        try:
            from .dictation import history
        except ImportError:
            return
        history.shared().add(result)

    def _learned(self, observed: str, corrected: str) -> None:
        """A correction the user made to pasted text. A new term is announced with Undo."""
        from .dictation.vocabulary import shared

        vocabulary = shared()
        known = {term.casefold() for term in vocabulary.terms()}
        term = vocabulary.learn(observed, corrected)
        if term and term.casefold() not in known:
            self.toast(Notice(f"Learned: {term}", "Undo", lambda: vocabulary.remove(term)))

    # -- CLI socket ----------------------------------------------------------------

    def dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd")
        simple = {
            "selection": self.speak_selection, "clipboard": self.speak_clipboard, "pause": self.toggle_pause,
            "stop": self.stop, "next": self.next, "prev": self.prev, "status": lambda: None,
        }
        if cmd == "speak":
            ok = self.speak_text(req.get("text", ""))
            return {"ok": ok, "status": self.status_dict()}
        if cmd == "dictate":
            return self._dictate_cmd(str(req.get("value", "")).lower())
        if cmd == "speed":
            self.set_speed(float(req.get("value", 1.0)))
        elif cmd == "seek":
            self.reader.seek(float(req.get("value", 10.0)))
        elif cmd == "seek_to":
            self.seek_to(float(req.get("value", 0.0)))
        elif cmd == "voice":
            try:
                self.set_voice(str(req.get("value", "")))
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        elif cmd == "button":
            value = str(req.get("value", "toggle")).lower()
            self.set_selection_button(not self.selection_button_enabled if value == "toggle" else value in ("on", "true", "1"))
        elif cmd in simple:
            simple[cmd]()
        elif cmd in self.extensions:
            return self.extensions[cmd](req)
        else:
            return {"ok": False, "error": f"unknown command {cmd!r}"}
        return {"ok": True, "status": self.status_dict()}

    def _dictate_cmd(self, action: str) -> dict:
        """`dictate start` records hands-free (no key is held), `stop` finishes, `cancel` drops it."""
        if action == "start":
            ok = self.dictation_start(hands_free=True)
        elif action == "stop":
            ok = self.dictation.state in _RECORDING
            self.dictation_stop()
        elif action == "cancel":
            ok = self.dictation.state in _RECORDING
            self.dictation_cancel()
        else:
            return {"ok": False, "error": "use: dictate start | stop | cancel"}
        return {"ok": ok, "status": self.status_dict()}
