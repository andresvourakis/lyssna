import sys
import tomllib

import pytest

from lyssna import config


def test_update_keeps_comments_and_handles_sections(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(config.TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    cfg = config.update(speed=1.7, **{"ui.selection_button": False, "inworld.voice": "Olivia", "new.key": "v"})
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["speed"] == 1.7 and cfg["speed"] == 1.7
    assert data["ui"]["selection_button"] is False and data["ui"]["mini_player"] is True
    assert data["inworld"]["voice"] == "Olivia" and data["cartesia"]["voice"] != "Olivia"
    assert data["new"]["key"] == "v"
    assert "# Playback speed multiplier" in text  # comments survive
    assert text.count("speed = ") == 1


def test_idle_pill_auto_hide_is_on_by_default_and_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(config.TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    assert config.DEFAULTS["ui"]["auto_hide"] is True
    assert tomllib.loads(config.TEMPLATE)["ui"]["auto_hide"] is True
    assert config.update(**{"ui.auto_hide": False})["ui"]["auto_hide"] is False
    assert tomllib.loads(path.read_text(encoding="utf-8"))["ui"]["auto_hide"] is False
    assert config.update(**{"ui.auto_hide": True})["ui"]["auto_hide"] is True


def test_idle_pill_wave_is_on_by_default_and_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(config.TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    assert config.DEFAULTS["ui"]["wave"] is True
    assert tomllib.loads(config.TEMPLATE)["ui"]["wave"] is True
    assert config.update(**{"ui.wave": False})["ui"]["wave"] is False
    assert tomllib.loads(path.read_text(encoding="utf-8"))["ui"]["wave"] is False
    assert config.update(**{"ui.wave": True})["ui"]["wave"] is True


def test_inworld_flash_shares_inworld_key_and_voice(monkeypatch):
    from lyssna.providers import get_provider

    monkeypatch.delenv("INWORLD_API_KEY", raising=False)
    cfg = {"inworld": {"api_key": "k", "voice": "Olivia", "model": "inworld-tts-2"}}
    flash = get_provider("inworld-flash", cfg)
    assert flash.api_key == "k" and flash.voice == "Olivia"
    assert flash.model == "inworld-tts-2-flash"  # the [inworld] model setting must not leak in
    assert flash.cache_key() != get_provider("inworld", cfg).cache_key()


def test_effective_provider_falls_back_to_kokoro_without_a_key(monkeypatch):
    from lyssna.providers import effective_provider

    monkeypatch.delenv("INWORLD_API_KEY", raising=False)
    assert effective_provider({"provider": "inworld", "inworld": {"api_key": ""}}) == "kokoro"


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS say voice")
def test_effective_provider_falls_back_to_say_when_kokoro_cannot_run(monkeypatch):
    from lyssna.providers import KokoroProvider, effective_provider

    monkeypatch.setattr(KokoroProvider, "can_run", classmethod(lambda cls: False))
    assert effective_provider({"provider": "inworld", "inworld": {"api_key": ""}}) == "say"
    assert effective_provider({"provider": "kokoro"}) == "say"


def test_effective_provider_keeps_kokoro_before_its_download(monkeypatch):
    from lyssna.providers import KokoroProvider, effective_provider

    monkeypatch.setattr(KokoroProvider, "can_run", classmethod(lambda cls: True))
    assert not KokoroProvider({}).configured()  # files not downloaded yet
    assert effective_provider({"provider": "kokoro"}) == "kokoro"


def test_new_installs_default_to_kokoro():
    assert config.DEFAULTS["provider"] == "kokoro"
    assert tomllib.loads(config.TEMPLATE)["provider"] == "kokoro"


def test_effective_provider_keeps_a_provider_with_a_key(monkeypatch):
    from lyssna.providers import effective_provider

    monkeypatch.delenv("INWORLD_API_KEY", raising=False)
    assert effective_provider({"provider": "inworld-flash", "inworld": {"api_key": "k"}}) == "inworld-flash"


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS say voice")
def test_effective_provider_leaves_say_alone():
    from lyssna.providers import effective_provider

    assert effective_provider({"provider": "say", "say": {"voice": ""}}) == "say"


@pytest.mark.skipif(sys.platform != "darwin", reason="POSIX file permissions")
def test_update_writes_config_owner_only(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(config.TEMPLATE, encoding="utf-8")
    path.chmod(0o644)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    config.update(**{"inworld.api_key": "secret"})
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)


def test_toml_value_escapes_quotes_backslashes_and_newlines():
    s = "a\nb\"c\\"
    assert tomllib.loads(f"x = {config._toml_value(s)}")["x"] == s


def test_toml_value_escapes_other_control_characters():
    s = "a\x01b\x7fc\td"
    assert tomllib.loads(f"x = {config._toml_value(s)}")["x"] == s
