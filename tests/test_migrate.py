import logging
import stat
import sys

import pytest

from lyssna import config, migrate
from lyssna.mac import speechy

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the Speechy migration runs on the Mac only")

OLD_CONFIG = 'provider = "cartesia"\n\n[cartesia]\napi_key = "secret"\n'


def _old_install():
    """An old Speechy setup: config, usage, welcome marker and a cached chunk."""
    speechy.OLD_CONFIG_PATH.parent.mkdir(parents=True)
    speechy.OLD_CONFIG_PATH.write_text(OLD_CONFIG)
    speechy.OLD_CONFIG_PATH.chmod(0o600)
    speechy.OLD_STATE_DIR.mkdir(parents=True)
    (speechy.OLD_STATE_DIR / "usage.json").write_text('{"2026-11": {"cartesia": 120}}')
    (speechy.OLD_STATE_DIR / "welcomed").write_text("")
    (speechy.OLD_STATE_DIR / "speechy.sock").write_text("")
    (speechy.OLD_CACHE_DIR / "ab").mkdir(parents=True)
    (speechy.OLD_CACHE_DIR / "ab" / "chunk.mp3").write_bytes(b"audio")


def test_migrates_everything_and_keeps_old_files(caplog):
    _old_install()
    with caplog.at_level(logging.INFO, logger="lyssna.migrate"):
        cfg = config.load()
    assert cfg["provider"] == "cartesia"
    assert config.CONFIG_PATH.read_text() == OLD_CONFIG
    assert stat.S_IMODE(config.CONFIG_PATH.stat().st_mode) == 0o600
    assert (config.STATE_DIR / "usage.json").read_text() == '{"2026-11": {"cartesia": 120}}'
    assert (config.STATE_DIR / "welcomed").exists()
    assert not (config.STATE_DIR / "speechy.sock").exists()  # the socket is never migrated
    assert (config.CACHE_DIR / "ab" / "chunk.mp3").read_bytes() == b"audio"
    assert not speechy.OLD_CACHE_DIR.exists()  # moved, not copied
    # The old app may still be opened: its config and state stay untouched.
    assert speechy.OLD_CONFIG_PATH.read_text() == OLD_CONFIG
    assert (speechy.OLD_STATE_DIR / "usage.json").exists()
    assert (speechy.OLD_STATE_DIR / "welcomed").exists()
    lines = [r for r in caplog.records if r.name == "lyssna.migrate"]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO


def test_second_run_is_a_noop():
    _old_install()
    assert migrate.run()
    config.ensure_dirs()
    assert migrate.run() == []
    config.update(speed=2.0)
    config.ensure_dirs()
    assert "speed = 2.0" in config.CONFIG_PATH.read_text()  # not overwritten by the old config


def test_existing_new_config_is_not_overwritten():
    _old_install()
    config.CONFIG_DIR.mkdir(parents=True)
    config.CONFIG_PATH.write_text('provider = "openai"\n')
    config.load()
    assert config.CONFIG_PATH.read_text() == 'provider = "openai"\n'


def test_existing_new_cache_is_left_alone():
    _old_install()
    config.CACHE_DIR.mkdir(parents=True)
    migrate.run()
    assert speechy.OLD_CACHE_DIR.exists()
    assert not (config.CACHE_DIR / "ab").exists()


def test_nothing_to_migrate():
    assert migrate.run() == []
    config.load()
    assert config.CONFIG_PATH.read_text() == config.TEMPLATE


def test_never_raises(monkeypatch):
    _old_install()

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(speechy.os, "rename", boom)
    done = migrate.run()
    assert any("config" in d for d in done)  # the other steps still ran
    assert speechy.OLD_CACHE_DIR.exists()
