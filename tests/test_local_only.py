"""Local-only mode: online engines are refused from every entry point (controller, CLI,
launch fallback) and a managed preference can force it on. The defaults domain is the
in-memory fake from conftest; nothing reads the real one."""

import sys

import pytest

from lyssna import cli, config, policy
from lyssna.controller import Controller
from lyssna.providers import REGISTRY, allowed, effective_provider, kokoro

ONLINE = [n for n, c in REGISTRY.items() if c.sends_text_off_device]
LOCAL = [n for n, c in REGISTRY.items() if not c.sends_text_off_device]


@pytest.fixture
def ctl(monkeypatch, fake_keychain):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)  # no download
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    fake_keychain.items.update({"inworld": "k", "cartesia": "k"})
    return Controller()


def test_off_by_default(ctl):
    assert ctl.local_only() is False and ctl.local_only_managed() is False
    assert all(allowed(n, ctl.cfg) for n in REGISTRY)


def test_filtering(ctl):
    ctl.set_local_only(True)
    assert config.load()["local_only"] is True
    assert [n for n in REGISTRY if allowed(n, ctl.cfg)] == LOCAL
    assert set(ONLINE) == {"inworld", "inworld-flash", "cartesia", "elevenlabs", "openai"}


@pytest.mark.skipif(sys.platform != "darwin", reason="asserts the Mac wording; test_windows_imports checks the Windows wording")
def test_set_provider_refuses_online_engines(ctl):
    ctl.set_local_only(True)
    with pytest.raises(ValueError, match="Keep Everything on This Mac"):
        ctl.set_provider("cartesia")
    assert config.load()["provider"] == "kokoro"
    ctl.set_provider("say")  # local engines are still fine
    assert ctl.cfg["provider"] == "say"


@pytest.mark.skipif(sys.platform != "darwin", reason="asserts the Mac wording; test_windows_imports checks the Windows wording")
def test_turning_it_on_leaves_an_online_engine(ctl):
    ctl.set_provider("inworld")
    ctl.set_local_only(True)
    assert ctl.cfg["provider"] == "kokoro" and config.load()["provider"] == "kokoro"
    assert ctl.privacy_note() == "Text stays on this Mac"


def test_hand_edited_config_is_caught_before_a_reading(ctl):
    config.update(provider="cartesia", local_only=True)  # written behind the app's back
    assert ctl._provider().name == "kokoro"
    assert config.load()["provider"] == "kokoro"


def test_launch_falls_back_from_an_online_engine(fake_keychain, monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    fake_keychain.items["cartesia"] = "k"
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE.replace('provider = "kokoro"', 'provider = "cartesia"')
                                  .replace("local_only = false", "local_only = true"), encoding="utf-8")
    c = Controller()
    assert c.cfg["provider"] == "kokoro" and c.fell_back_to == "kokoro"


def test_effective_provider_respects_local_only(fake_keychain):
    fake_keychain.items["cartesia"] = "k"
    assert effective_provider({"provider": "cartesia"}) == "cartesia"
    assert effective_provider({"provider": "cartesia", "local_only": True}) in LOCAL


# -- managed preference -------------------------------------------------------------------


@pytest.mark.parametrize(("value", "forced"), [(True, True), (1, True), (False, False), (0, False), ("yes", False), (None, False)])
def test_managed_preference_values(managed_prefs, value, forced):
    managed_prefs["LocalOnly"] = value
    assert policy.managed_local_only() is forced
    assert policy.local_only({"local_only": False}) is forced


def test_managed_preference_overrides_the_config(ctl, managed_prefs):
    ctl.set_provider("cartesia")
    managed_prefs["LocalOnly"] = True
    assert config.load()["local_only"] is False
    assert ctl.local_only() and ctl.local_only_managed()
    with pytest.raises(ValueError, match="set by your organization"):
        ctl.set_provider("inworld")
    with pytest.raises(ValueError, match="set by your organization"):
        ctl.set_local_only(False)
    assert ctl._provider().name == "kokoro"  # the next reading is already local


def test_managed_false_leaves_the_choice_to_the_user(ctl, managed_prefs):
    managed_prefs["LocalOnly"] = False
    ctl.set_local_only(True)
    assert ctl.local_only()
    ctl.set_local_only(False)
    assert not ctl.local_only()


# -- CLI ----------------------------------------------------------------------------------


@pytest.fixture
def no_app(monkeypatch):
    monkeypatch.setattr(cli, "_setup_logging", lambda verbose: None)
    monkeypatch.setattr(cli.host.ipc, "send", lambda req, timeout=5.0: pytest.fail("must not reach the app"))
    played = []
    monkeypatch.setattr(cli, "_speak_standalone", lambda text: played.append(text) or 0)
    return played


@pytest.mark.skipif(sys.platform != "darwin", reason="asserts the Mac wording; test_windows_imports checks the Windows wording")
def test_cli_provider_flag_refuses_online_engines(no_app, capsys, monkeypatch):
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE.replace("local_only = false", "local_only = true"), encoding="utf-8")
    assert cli.main(["-p", "openai", "hello"]) == 2
    assert "Keep Everything on This Mac" in capsys.readouterr().err
    assert no_app == []
    assert cli.main(["-p", "say", "hello"]) == 0
    assert no_app == ["hello"]


def test_cli_provider_flag_refused_by_managed_preference(no_app, capsys, managed_prefs):
    managed_prefs["LocalOnly"] = True
    assert cli.main(["-p", "cartesia", "hello"]) == 2
    assert "set by your organization" in capsys.readouterr().err


def test_standalone_guard_swaps_an_online_engine():
    assert cli._local_only_guard({"provider": "cartesia", "local_only": True})["provider"] in LOCAL
    assert cli._local_only_guard({"provider": "cartesia", "local_only": False})["provider"] == "cartesia"
