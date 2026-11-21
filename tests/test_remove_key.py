"""Remove API Key: the key leaves the Keychain and the config file, and an engine that relied
on it hands over to the free voice on this Mac. Keychain and config are the conftest fakes."""

import logging

import pytest

from lyssna import cli, config
from lyssna.controller import Controller
from lyssna.providers import kokoro


@pytest.fixture
def ctl(monkeypatch, fake_keychain):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)  # no download
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    fake_keychain.items.update({"inworld": "ik", "cartesia": "ck"})
    return Controller()


def test_removing_the_current_engines_key_switches_to_kokoro(ctl, fake_keychain):
    ctl.set_provider("cartesia")
    assert ctl.has_stored_key("cartesia")
    ctl.remove_api_key("cartesia")
    assert "cartesia" not in fake_keychain.items and not ctl.has_stored_key("cartesia")
    assert ctl.cfg["provider"] == "kokoro" and config.load()["provider"] == "kokoro"


def test_removing_another_engines_key_keeps_the_current_one(ctl, fake_keychain):
    ctl.set_provider("cartesia")
    ctl.remove_api_key("inworld")
    assert "inworld" not in fake_keychain.items and fake_keychain.items["cartesia"] == "ck"
    assert ctl.cfg["provider"] == "cartesia"


def test_shared_section_counts_as_the_same_key(ctl, fake_keychain):
    ctl.set_provider("inworld-flash")  # shares the [inworld] key
    ctl.remove_api_key("inworld")
    assert ctl.cfg["provider"] == "kokoro"


def test_a_key_in_the_config_file_is_blanked_too(ctl, fake_keychain):
    config.update(**{"elevenlabs.api_key": "file-key"})
    assert ctl.has_stored_key("elevenlabs")
    ctl.remove_api_key("elevenlabs")
    assert config.file_api_key("elevenlabs") == "" and not ctl.has_stored_key("elevenlabs")
    assert "# George" in config.CONFIG_PATH.read_text(encoding="utf-8")  # comments survive


def test_no_stored_key(ctl):
    assert not ctl.has_stored_key("openai")
    ctl.remove_api_key("openai")  # nothing there: harmless
    assert ctl.cfg["provider"] == "kokoro"


def test_phonemizer_warnings_are_silenced(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))  # undo the handlers _setup_logging adds
    monkeypatch.setattr(root, "level", root.level)
    level = logging.getLogger("phonemizer").level
    try:
        cli._setup_logging(False)
        assert logging.getLogger("phonemizer").getEffectiveLevel() == logging.ERROR
    finally:
        for h in root.handlers:
            h.close()
        logging.getLogger("phonemizer").setLevel(level)
