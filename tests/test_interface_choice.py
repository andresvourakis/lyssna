"""Which interface runs (host.interface() and host.app): always the Qt one on Windows; on a Mac
the Qt one too unless LYSSNA_INTERFACE=legacy, or else interface = "legacy" under [ui] in the
config file, asks for the original one. Nothing here loads an interface: the module host would import is
recorded instead."""

import tomllib

import pytest

from lyssna import config, host


@pytest.fixture
def mac(monkeypatch):
    """A Mac, whichever system runs the tests."""
    monkeypatch.setattr(host, "IS_WINDOWS", False)
    monkeypatch.delenv(host.INTERFACE_ENV, raising=False)


@pytest.fixture
def uncached_app():
    """host.app looked up afresh, and whatever was cached before put back afterwards."""
    cached = host.__dict__.pop("app", None)
    yield
    host.__dict__.pop("app", None)
    if cached is not None:
        host.__dict__["app"] = cached


def write_config(text: str) -> None:
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(text, encoding="utf-8")


def test_windows_always_runs_the_new_interface(monkeypatch):
    monkeypatch.setattr(host, "IS_WINDOWS", True)
    monkeypatch.setenv(host.INTERFACE_ENV, "legacy")
    write_config('[ui]\ninterface = "legacy"\n')
    assert host.interface() == "new"


def test_the_mac_runs_the_new_interface_by_default(mac):
    assert host.interface() == "new"  # no config file yet: the template is written, and it picks nothing
    assert "interface" not in tomllib.loads(config.CONFIG_PATH.read_text(encoding="utf-8")).get("ui", {})
    assert host.interface() == "new"


def test_the_config_file_falls_back_to_the_legacy_interface(mac):
    write_config('[ui]\ninterface = "legacy"\n')
    assert host.interface() == "legacy"
    write_config('[ui]\ninterface = " Legacy "\n')
    assert host.interface() == "legacy"
    write_config('[ui]\ninterface = "new"\n')
    assert host.interface() == "new"
    write_config('[ui]\ninterface = "something else"\n')
    assert host.interface() == "new"
    write_config("[ui\nnot toml")
    assert host.interface() == "new"


@pytest.mark.skipif(host.IS_WINDOWS, reason="the Windows template has no legacy line to turn on")
def test_the_template_hint_turns_on_the_legacy_interface(mac):
    write_config(config.TEMPLATE.replace('# interface = "legacy"', 'interface = "legacy"'))
    assert host.interface() == "legacy"


def test_the_environment_wins_over_the_config_file(mac, monkeypatch):
    monkeypatch.setenv(host.INTERFACE_ENV, "new")
    write_config('[ui]\ninterface = "legacy"\n')
    assert host.interface() == "new"
    monkeypatch.setenv(host.INTERFACE_ENV, "legacy")
    write_config('[ui]\ninterface = "new"\n')
    assert host.interface() == "legacy"
    monkeypatch.setenv(host.INTERFACE_ENV, " New ")
    assert host.interface() == "new"


def test_the_default_is_the_new_interface():
    assert config.DEFAULTS["ui"]["interface"] == "new"
    assert ('# interface = "legacy"' in config.TEMPLATE) is not host.IS_WINDOWS  # the fallback line is for the Mac only


@pytest.mark.parametrize(("windows", "choice", "expected"), [
    (True, "new", "lyssna.app"),
    (False, "new", "lyssna.app"),
    (False, "legacy", "lyssna.legacy.app"),
])
def test_host_app_is_the_chosen_module(monkeypatch, uncached_app, windows, choice, expected):
    loaded = []
    monkeypatch.setattr(host, "IS_WINDOWS", windows)
    monkeypatch.setattr(host, "interface", lambda: choice)
    monkeypatch.setattr(host.importlib, "import_module", lambda name: loaded.append(name) or name)
    assert host.app == expected and loaded == [expected]
