"""The Windows system voice, with fake SAPI objects in place of COM."""

import io
import sys
import wave

import pytest

from lyssna.providers import windows
from lyssna.providers.base import ProviderError
from lyssna.providers.windows import WindowsVoiceProvider


class Token:
    def __init__(self, description, language):
        self.description, self.language = description, language

    def GetDescription(self):
        return self.description

    def GetAttribute(self, name):
        assert name == "Language"
        return self.language


class Tokens:
    def __init__(self, items):
        self.items = items
        self.Count = len(items)

    def Item(self, i):
        return self.items[i]


VOICES = [Token("Microsoft Hedda Desktop - German", "407"), Token("Microsoft Zira Desktop - English (United States)", "409"),
          Token("Microsoft George - English (United Kingdom)", "809;9")]


class FakeSapi:
    def __init__(self, pcm=b"\x01\x00" * 2205, fail=None):
        self.pcm, self.fail = pcm, fail
        self.spoken = []
        self.voice = None

    def create(self, progid):
        sapi = self

        class SpVoice:
            Voice = None
            AudioOutputStream = None

            def GetVoices(self):
                return Tokens(VOICES)

            def Speak(self, text, flags):
                if sapi.fail:
                    raise sapi.fail
                sapi.spoken.append((text, flags))
                sapi.voice = self.Voice
                self.AudioOutputStream.data = sapi.pcm

        class SpMemoryStream:
            Format = None
            data = b""

            def GetData(self):
                assert self.Format.Type == windows.SAFT22kHz16BitMono
                return tuple(self.data)  # comtypes may hand back a tuple of ints

        class SpAudioFormat:
            Type = None

        return {"SAPI.SpVoice": SpVoice, "SAPI.SpMemoryStream": SpMemoryStream, "SAPI.SpAudioFormat": SpAudioFormat}[progid]()


@pytest.fixture
def sapi(monkeypatch):
    fake = FakeSapi()
    com = []
    monkeypatch.setattr(windows, "_create", fake.create)
    monkeypatch.setattr(windows, "_co_initialize", lambda: com.append("init") or True)
    monkeypatch.setattr(windows, "_co_uninitialize", lambda: com.append("uninit"))
    windows._voice_list.cache_clear()
    fake.com = com
    yield fake
    windows._voice_list.cache_clear()


def test_wav_is_well_formed(sapi):
    audio = WindowsVoiceProvider({}).synthesize("Hello there")
    assert audio.ext == "wav"
    with wave.open(io.BytesIO(audio.data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 22050)
        assert w.readframes(w.getnframes()) == sapi.pcm
    assert sapi.spoken == [("Hello there", windows.SVSF_IS_NOT_XML)]
    assert sapi.com.count("init") == sapi.com.count("uninit") >= 1


def test_voices_listed_and_first_english_is_default(sapi):
    assert windows.installed_voices() == [v.description for v in VOICES]
    provider = WindowsVoiceProvider({})
    assert provider.voice == "Microsoft Zira Desktop - English (United States)"
    provider.synthesize("Hi")
    assert sapi.voice is VOICES[1]


def test_configured_voice_is_used(sapi):
    WindowsVoiceProvider({"voice": "Microsoft George - English (United Kingdom)"}).synthesize("Hi")
    assert sapi.voice is VOICES[2]


def test_missing_configured_voice_falls_back_to_the_default(sapi):
    WindowsVoiceProvider({"voice": "Not Installed"}).synthesize("Hi")
    assert sapi.voice is None


def test_errors_become_provider_errors(sapi):
    sapi.fail = OSError("speech engine broke")
    with pytest.raises(ProviderError):
        WindowsVoiceProvider({}).synthesize("Hi")
    assert sapi.com.count("init") == sapi.com.count("uninit")


def test_no_audio_is_an_error(sapi):
    sapi.pcm = b""
    with pytest.raises(ProviderError):
        WindowsVoiceProvider({}).synthesize("Hi")


@pytest.mark.skipif(sys.platform == "win32", reason="checks the answer on other systems")
def test_cannot_run_off_windows():
    assert WindowsVoiceProvider.can_run() is False


def test_attributes():
    p = WindowsVoiceProvider
    assert (p.name, p.label, p.needs_key, p.sends_text_off_device, p.price_per_million) == (
        "sapi", "Windows system voice", False, False, 0.0)
    assert WindowsVoiceProvider({}).configured() is True
