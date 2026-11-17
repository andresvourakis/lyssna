"""`lyssna dictate` and `lyssna vocab`, plus the dictation settings in the config file. The
models are fakes (conftest refuses the real ones), nothing goes online, and the config,
vocabulary and model folders are under the per-test temp dir."""

import json
import tomllib
import wave

import httpx
import numpy as np
import pytest

from lyssna import cli, config
from lyssna.dictation import vocabulary
from lyssna.dictation.cleaners import s1mini
from lyssna.dictation.models import PARAKEET_V2, S1_MINI
from lyssna.dictation.transcribers import parakeet


def write_wav(path, seconds=0.5, rate=16000, channels=1):
    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm, 2)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


def put_files(model):
    model.directory.mkdir(parents=True, exist_ok=True)
    for f in model.files:
        model.path(f.name).write_bytes(b"x")


class FakeASR:
    def __init__(self):
        self.lengths = []

    def recognize(self, samples, sample_rate=16000):
        self.lengths.append(len(samples))
        return "um so i use listna every day"


class FakeRunner:
    def count_tokens(self, text):
        return len(text.split())

    def clean(self, transcript):
        return "So I " + transcript.removeprefix("um so i ") + ".", "stop", 7, 6

    def close(self):
        pass


@pytest.fixture
def local_engines(monkeypatch):
    asr = FakeASR()
    monkeypatch.setattr(cli, "_setup_logging", lambda verbose: None)
    monkeypatch.setattr(parakeet, "_make_model", lambda model: asr)
    monkeypatch.setattr(parakeet.ParakeetTranscriber, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(s1mini, "_make_runner", lambda path: FakeRunner())
    monkeypatch.setattr(s1mini.S1MiniCleaner, "can_run", classmethod(lambda cls: True))
    put_files(PARAKEET_V2)
    put_files(S1_MINI)
    yield asr
    s1mini.unload()


def test_dictate_prints_the_text(local_engines, tmp_path, capsys):
    vocabulary.shared().add("Lyssna")
    assert cli.main(["dictate", "--file", str(write_wav(tmp_path / "a.wav"))]) == 0
    assert capsys.readouterr().out == "So I use Lyssna every day.\n"
    assert local_engines.lengths == [8000]


def test_dictate_json(local_engines, tmp_path, capsys):
    assert cli.main(["-v", "dictate", "--file", str(write_wav(tmp_path / "a.wav", channels=2)), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["raw"] == "um so i use listna every day"
    assert (result["source"], result["transcriber"], result["cleaner"]) == ("model", "parakeet", "s1-mini")
    assert result["audio_seconds"] == 0.5 and set(result["seconds"]) == {"transcribe", "clean", "total"}


def test_dictate_without_cleanup(local_engines, tmp_path, capsys):
    assert cli.main(["dictate", "--file", str(write_wav(tmp_path / "a.wav")), "--no-cleanup", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["source"], result["cleaner"], result["text"]) == ("rules:none", "rules", "So i use listna every day.")


def test_dictate_resamples_other_rates(local_engines, tmp_path, capsys):
    assert cli.main(["dictate", "--file", str(write_wav(tmp_path / "a.wav", rate=48000)), "--no-cleanup"]) == 0
    assert local_engines.lengths == [8000]


def test_dictate_reports_an_unreadable_file(local_engines, tmp_path, capsys):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    assert cli.main(["dictate", "--file", str(bad)]) == 1
    assert "Could not read" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [["--transcriber", "openai"], ["--transcriber", "inworld"], ["--cleaner", "haiku"]])
def test_dictate_refuses_online_engines_in_local_only(local_engines, tmp_path, capsys, monkeypatch, fake_keychain, flags):
    fake_keychain.items.update({"openai": "k", "inworld": "k", "anthropic": "k"})
    config.ensure_dirs()
    config.CONFIG_PATH.write_text("local_only = true\n")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("an online engine was called"))
    assert cli.main(["dictate", "--file", str(write_wav(tmp_path / "a.wav")), *flags]) == 2
    assert "local-only mode is on" in capsys.readouterr().err


def test_dictate_falls_back_from_online_engines_named_in_the_config(local_engines, tmp_path, capsys, monkeypatch,
                                                                      fake_keychain):
    fake_keychain.items.update({"openai": "k", "anthropic": "k"})
    config.ensure_dirs()
    config.CONFIG_PATH.write_text('local_only = true\n[dictation]\ntranscriber = "openai"\ncleaner = "haiku"\n')
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("an online engine was called"))
    assert cli.main(["dictate", "--file", str(write_wav(tmp_path / "a.wav")), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["transcriber"], result["cleaner"]) == ("parakeet", "s1-mini")


def test_dictate_with_a_failing_engine(local_engines, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(local_engines, "recognize", lambda samples, sample_rate=16000: 1 / 0)
    assert cli.main(["dictate", "--file", str(write_wav(tmp_path / "a.wav")), "--no-cleanup"]) == 1
    assert "could not transcribe" in capsys.readouterr().err


# -- lyssna vocab ---------------------------------------------------------------------------------


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(cli, "_setup_logging", lambda verbose: None)


def test_vocab_add_list_remove(quiet, capsys):
    assert cli.main(["vocab", "list"]) == 0
    assert "empty" in capsys.readouterr().out
    assert cli.main(["vocab", "add", "Claude", "Code", "--heard-as", "cloud code"]) == 0
    assert cli.main(["vocab", "add", "Lyssna"]) == 0
    capsys.readouterr()
    assert cli.main(["vocab", "list"]) == 0
    out = capsys.readouterr().out
    assert "Claude Code  (manual, used 0x, heard as: cloud code)" in out and "Lyssna  (manual" in out
    assert cli.main(["vocab", "remove", "Lyssna"]) == 0
    assert cli.main(["vocab", "remove", "Lyssna"]) == 1
    assert vocabulary.Vocabulary().terms() == ["Claude Code"]


# -- config ---------------------------------------------------------------------------------------


def test_dictation_defaults():
    d = config.DEFAULTS
    assert d["dictation"] == {"transcriber": "parakeet", "cleaner": "s1-mini", "cleanup": True, "language": "en",
                              "hotkey": "fn", "hands_free": "space", "history_days": 30, "auto_learn": True}
    assert d["parakeet"] == {"model": "v2"}
    assert d["anthropic"] == {"api_key": "", "model": "claude-haiku-5-5"}
    assert d["openai"]["transcribe_model"] == "gpt-transcribe"


def test_template_matches_the_defaults():
    template = tomllib.loads(config.TEMPLATE)
    for section in ("dictation", "parakeet", "anthropic"):
        assert template[section] == config.DEFAULTS[section]
    assert template["openai"]["transcribe_model"] == "gpt-transcribe"


def test_dictation_settings_load_and_update():
    cfg = config.load()
    assert cfg["dictation"]["cleaner"] == "s1-mini"
    cfg = config.update(**{"dictation.cleaner": "haiku", "parakeet.model": "v3"})
    assert cfg["dictation"]["cleaner"] == "haiku" and cfg["parakeet"]["model"] == "v3"
    assert "# Speech to text" in config.CONFIG_PATH.read_text()  # comments kept
