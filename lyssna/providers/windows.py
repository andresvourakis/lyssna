"""The voices built into Windows, through SAPI 5. Free, offline, and always there on a PC,
the Windows counterpart of the Mac's `say`.

Why SAPI through comtypes: SAPI is the speech API every Windows version ships, and it can
speak into a memory stream instead of the speakers, so the result goes through our own player
(speed, pause, the floating button) like every other engine. We fix the stream's format to
22 kHz 16-bit mono and wrap the raw samples in a WAV header ourselves.

COM objects belong to the thread that made them, and readings run on worker threads, so each
call sets up COM on its own thread and builds its own SpVoice. One reading at a time, like
Kokoro: two at once only fight over the same speech engine. comtypes is imported inside the
functions, so this module imports on any system; tests replace _create and the COM setup."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import threading
import wave
from collections.abc import Iterator
from functools import lru_cache

from .. import host
from .base import Audio, Provider, ProviderError

log = logging.getLogger("lyssna.win.voice")

SAMPLE_RATE = 22050
SAMPLE_WIDTH = 2  # bytes: 16-bit samples
CHANNELS = 1
SAFT22kHz16BitMono = 22  # SpeechAudioFormatType for the format above
SVSF_IS_NOT_XML = 16  # read the text as it is; never treat it as SAPI markup
LANG_ENGLISH = 0x09  # primary language id inside a Windows LCID

_synth_lock = threading.Lock()


def _create(progid: str):
    """A new COM object on the calling thread (tests replace this with fakes)."""
    import comtypes.client

    return comtypes.client.CreateObject(progid)


def _co_initialize() -> bool:
    """Set up COM on this thread. True when the caller must undo it afterwards."""
    import comtypes

    try:
        comtypes.CoInitialize()
    except OSError:  # already set up on this thread in another mode: use that one
        return False
    return True


def _co_uninitialize() -> None:
    import comtypes

    comtypes.CoUninitialize()


@contextlib.contextmanager
def _com() -> Iterator[None]:
    owned = _co_initialize()
    try:
        yield
    finally:
        if owned:
            _co_uninitialize()


def _tokens(voice) -> list:
    items = voice.GetVoices()
    return [items.Item(i) for i in range(items.Count)]


def _is_english(token) -> bool:
    """SAPI lists a voice's languages as hex LCIDs, for example "409" or "409;9"."""
    try:
        first = str(token.GetAttribute("Language")).split(";")[0].strip()
        return (int(first, 16) & 0x3FF) == LANG_ENGLISH
    except Exception:
        return "english" in str(token.GetDescription()).casefold()


@lru_cache(maxsize=1)
def _voice_list() -> tuple[tuple[str, bool], ...]:
    """(description, is English) for every installed voice, in SAPI's order."""
    try:
        with _com():
            voice = None
            try:
                voice = _create("SAPI.SpVoice")
                return tuple((str(t.GetDescription()), _is_english(t)) for t in _tokens(voice))
            finally:
                voice = None  # released while COM is still set up on this thread
    except Exception as e:
        log.warning("could not list the Windows voices: %s", type(e).__name__)
        return ()


def installed_voices() -> list[str]:
    """The descriptions of the installed voices, for example "Microsoft Zira Desktop - English (United States)"."""
    return [name for name, _english in _voice_list()]


def _pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class WindowsVoiceProvider(Provider):
    """Free, offline, built into Windows. More natural voices can be added in Settings >
    Time & language > Speech."""

    name = "sapi"
    label = "Windows system voice"
    needs_key = False
    sends_text_off_device = False
    max_chars = 5000
    price_per_million = 0.0
    default_model = "sapi"

    @classmethod
    def can_run(cls) -> bool:
        return host.IS_WINDOWS and importlib.util.find_spec("comtypes") is not None

    @property
    def voice(self) -> str:
        if self.cfg.get("voice"):
            return self.cfg["voice"]
        have = _voice_list()
        for name, english in have:
            if english:
                return name
        return have[0][0] if have else ""

    def synthesize(self, text: str) -> Audio:
        wanted = self.voice
        with _synth_lock:
            try:
                with _com():
                    pcm = self._speak(text, wanted)
            except ProviderError:
                raise
            except Exception as e:
                raise ProviderError(f"The Windows voice could not read this: {e}") from e
        if not pcm:
            raise ProviderError("The Windows voice produced no audio")
        return Audio(_pcm_to_wav(pcm), "wav")

    def _speak(self, text: str, wanted: str) -> bytes:
        voice = stream = fmt = tokens = None
        try:
            voice = _create("SAPI.SpVoice")
            stream = _create("SAPI.SpMemoryStream")
            fmt = _create("SAPI.SpAudioFormat")
            fmt.Type = SAFT22kHz16BitMono
            stream.Format = fmt
            if wanted:
                tokens = _tokens(voice)
                chosen = next((t for t in tokens if str(t.GetDescription()) == wanted), None)
                if chosen is not None:
                    voice.Voice = chosen
                else:
                    log.info("the chosen Windows voice is not installed, so the default voice reads")
                chosen = None
            voice.AudioOutputStream = stream
            voice.Speak(text, SVSF_IS_NOT_XML)  # synchronous: returns when the stream holds all of it
            data = stream.GetData()
            return bytes(bytearray(data)) if data is not None else b""
        finally:
            # Release the COM objects now, while COM is still set up on this thread, even when an
            # error's traceback keeps this frame alive.
            voice = stream = fmt = tokens = None
