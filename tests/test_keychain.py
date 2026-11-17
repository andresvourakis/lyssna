"""API keys in the Keychain: the wrapper's cache and failure handling, the provider lookup
order, saving from the menu, and the one-time move out of the config file. The Keychain is
the in-memory fake from conftest; nothing here touches the real one or the network."""

import logging

import pytest

from lyssna import config, keychain, migrate
from lyssna.providers import get_provider


def _config_with_keys(**keys: str) -> None:
    """A config file from the template with api_key filled in for the given sections."""
    config.load()  # writes the template
    config.update(**{f"{section}.api_key": key for section, key in keys.items()})


# -- the wrapper -----------------------------------------------------------------------


def test_set_get_delete_round_trip(fake_keychain):
    assert keychain.get("cartesia") is None
    assert keychain.set("cartesia", "ck") is True
    assert keychain.get("cartesia") == "ck"
    assert keychain.delete("cartesia") is True
    assert keychain.get("cartesia") is None
    assert keychain.delete("cartesia") is True  # already gone counts as done


def test_reads_are_cached_misses_too(fake_keychain):
    fake_keychain.items["inworld"] = "ik"
    for _ in range(5):
        assert keychain.get("inworld") == "ik"
        assert keychain.get("openai") is None
    assert fake_keychain.calls.count(("get", "inworld")) == 1
    assert fake_keychain.calls.count(("get", "openai")) == 1


def test_set_and_delete_invalidate_the_cache(fake_keychain):
    assert keychain.get("openai") is None
    keychain.set("openai", "one")
    assert keychain.get("openai") == "one"
    keychain.set("openai", "two")
    assert keychain.get("openai") == "two"
    keychain.delete("openai")
    assert keychain.get("openai") is None


def test_failed_write_still_invalidates(fake_keychain):
    fake_keychain.items["openai"] = "old"
    assert keychain.get("openai") == "old"
    fake_keychain.fail_set = -25308
    assert keychain.set("openai", "new") is False
    fake_keychain.items["openai"] = "changed elsewhere"
    assert keychain.get("openai") == "changed elsewhere"


@pytest.mark.parametrize("failure", [-25293, RuntimeError("boom")])
def test_failures_never_raise(fake_keychain, failure):
    fake_keychain.fail_get = fake_keychain.fail_set = fake_keychain.fail_delete = failure
    assert keychain.get("inworld") is None
    assert keychain.set("inworld", "k") is False
    assert keychain.delete("inworld") is False


def test_keys_never_reach_the_log(fake_keychain, caplog):
    secret = "sk-very-secret-value"
    with caplog.at_level(logging.DEBUG):
        keychain.set("openai", secret)
        keychain.get("openai")
        fake_keychain.fail_set = UnicodeEncodeError("utf-8", secret, 0, 1, "bad")
        keychain.set("openai", secret)
        fake_keychain.fail_get = ValueError(secret)
        keychain.clear_cache()
        keychain.get("openai")
    assert caplog.records  # the failures were logged
    assert all("secret" not in r.getMessage() and not r.exc_info for r in caplog.records)


# -- lookup order --------------------------------------------------------------------


def test_keychain_beats_config_file_beats_env(fake_keychain, monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "from-env")
    cfg = {"cartesia": {"api_key": "from-file"}}
    assert get_provider("cartesia", cfg).api_key == "from-file"
    assert get_provider("cartesia", {}).api_key == "from-env"
    keychain.set("cartesia", "from-keychain")
    assert get_provider("cartesia", cfg).api_key == "from-keychain"


def test_trial_key_beats_the_saved_one(fake_keychain):
    keychain.set("openai", "saved")
    p = get_provider("openai", {})
    assert type(p)({}, api_key="trial").api_key == "trial"


def test_inworld_engines_share_one_keychain_entry(fake_keychain):
    keychain.set("inworld", "ik")
    assert get_provider("inworld", {}).configured()
    assert get_provider("inworld-flash", {}).configured()
    assert not get_provider("cartesia", {}).configured()


def test_engines_without_keys_never_ask_the_keychain(fake_keychain):
    get_provider("say", {}).configured()
    get_provider("kokoro", {}).configured()  # depends on the voice files being downloaded, not on a key
    assert fake_keychain.calls == []


def test_repeated_configured_checks_read_the_keychain_once(fake_keychain):
    for _ in range(20):
        get_provider("elevenlabs", {}).configured()
    assert fake_keychain.calls == [("get", "elevenlabs")]


def test_missing_key_message_points_at_the_menu():
    with pytest.raises(Exception, match="Settings > API Keys"):
        get_provider("openai", {}).synthesize("Hi.")


# -- saving from the menu --------------------------------------------------------------


def test_save_puts_the_key_in_the_keychain_and_blanks_the_file(fake_keychain):
    _config_with_keys(cartesia="old-file-key")
    assert config.save_api_key("cartesia", "new-key") is True
    assert fake_keychain.items["cartesia"] == "new-key"
    assert config.file_api_key("cartesia") == ""
    assert "new-key" not in config.CONFIG_PATH.read_text()
    assert get_provider("cartesia", config.load()).api_key == "new-key"


def test_save_falls_back_to_the_file_when_the_keychain_fails(fake_keychain, caplog):
    config.load()
    fake_keychain.fail_set = -25308
    with caplog.at_level(logging.WARNING, logger="lyssna.config"):
        assert config.save_api_key("openai", "file-key") is False
    assert config.file_api_key("openai") == "file-key"
    assert get_provider("openai", config.load()).api_key == "file-key"
    assert any("Keychain" in r.getMessage() for r in caplog.records)
    assert all("file-key" not in r.getMessage() for r in caplog.records)


# -- the one-time move out of the config file ---------------------------------------------


def test_migration_moves_keys_and_blanks_the_file(fake_keychain):
    _config_with_keys(inworld="ik", openai="ok")
    assert migrate.keys_to_keychain() == ["inworld", "openai"]
    assert fake_keychain.items == {"inworld": "ik", "openai": "ok"}
    assert config.file_api_key("inworld") == "" and config.file_api_key("openai") == ""
    assert '[inworld]\napi_key = ""\nvoice = "Sarah"' in config.CONFIG_PATH.read_text()  # comments kept
    assert (config.STATE_DIR / migrate.KEYCHAIN_MARKER).exists()


def test_migration_keeps_the_file_when_the_write_fails(fake_keychain):
    _config_with_keys(cartesia="ck")
    fake_keychain.fail_set = -25308
    assert migrate.keys_to_keychain() == []
    assert config.file_api_key("cartesia") == "ck"
    assert get_provider("cartesia", config.load()).api_key == "ck"
    assert not (config.STATE_DIR / migrate.KEYCHAIN_MARKER).exists()  # tried again next start
    fake_keychain.fail_set = None
    assert migrate.keys_to_keychain() == ["cartesia"]
    assert fake_keychain.items["cartesia"] == "ck" and config.file_api_key("cartesia") == ""


def test_migration_keeps_the_file_when_the_read_back_does_not_match(fake_keychain, monkeypatch):
    _config_with_keys(cartesia="ck")
    real_set = fake_keychain.set
    monkeypatch.setattr(fake_keychain, "set", lambda account, value: real_set(account, value[:-1]))
    assert migrate.keys_to_keychain() == []
    assert config.file_api_key("cartesia") == "ck"


def test_migration_leaves_a_different_keychain_key_alone(fake_keychain):
    _config_with_keys(elevenlabs="file-key")
    fake_keychain.items["elevenlabs"] = "keychain-key"
    assert migrate.keys_to_keychain() == []
    assert fake_keychain.items["elevenlabs"] == "keychain-key"
    assert config.file_api_key("elevenlabs") == "file-key"


def test_migration_runs_once(fake_keychain):
    config.load()
    assert migrate.keys_to_keychain() == []
    assert (config.STATE_DIR / migrate.KEYCHAIN_MARKER).exists()
    config.update(**{"openai.api_key": "kept-in-file-on-purpose"})
    assert migrate.keys_to_keychain() == []
    assert config.file_api_key("openai") == "kept-in-file-on-purpose"
    assert "openai" not in fake_keychain.items


def test_migration_never_touches_the_old_speechy_config(fake_keychain):
    migrate.OLD_CONFIG_PATH.parent.mkdir(parents=True)
    migrate.OLD_CONFIG_PATH.write_text('[cartesia]\napi_key = "old"\n')
    config.load()  # copies the Speechy config over
    assert migrate.keys_to_keychain() == ["cartesia"]
    assert migrate.OLD_CONFIG_PATH.read_text() == '[cartesia]\napi_key = "old"\n'


def test_app_start_moves_the_keys(fake_keychain):
    from lyssna.controller import Controller

    _config_with_keys(inworld="ik")
    config.update(provider="inworld")  # an existing Inworld user; new installs start on kokoro
    c = Controller()
    assert fake_keychain.items == {"inworld": "ik"}
    assert c.cfg["inworld"]["api_key"] == "" and c.cfg["provider"] == "inworld"
    assert c.fell_back_to == ""
