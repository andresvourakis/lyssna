"""Start at login on Windows, with a fake winreg module in place of the registry."""

import sys
import types

import pytest

from lyssna.win import autostart

HKCU = "HKCU"
REG_SZ = 1
KEY_SET_VALUE = 2
EXE = r"C:\Program Files\Lyssna\Lyssna.exe"


class FakeRegistry:
    def __init__(self):
        self.values: dict[str, tuple[str, int]] = {}
        self.fail: Exception | None = None

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("winreg")
        mod.HKEY_CURRENT_USER, mod.REG_SZ, mod.KEY_SET_VALUE = HKCU, REG_SZ, KEY_SET_VALUE
        registry = self

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def open_key(hive, path, reserved=0, access=0):
            assert (hive, path) == (HKCU, autostart.RUN_KEY)
            if registry.fail:
                raise registry.fail
            return Key()

        def create_key_ex(hive, path, reserved=0, access=0):
            return open_key(hive, path)

        def query_value_ex(key, name):
            if name not in registry.values:
                raise FileNotFoundError(name)
            return registry.values[name]

        def set_value_ex(key, name, reserved, kind, value):
            registry.values[name] = (value, kind)

        def delete_value(key, name):
            if name not in registry.values:
                raise FileNotFoundError(name)
            del registry.values[name]

        mod.OpenKey, mod.CreateKeyEx, mod.QueryValueEx = open_key, create_key_ex, query_value_ex
        mod.SetValueEx, mod.DeleteValue = set_value_ex, delete_value
        return mod


@pytest.fixture
def registry(monkeypatch):
    reg = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg.module())
    monkeypatch.setattr(sys, "executable", EXE)
    return reg


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_frozen_turn_on_and_off(registry, frozen):
    assert not autostart.is_enabled()
    assert autostart.set_enabled(True)
    assert registry.values["Lyssna"] == (f'"{EXE}"', REG_SZ)
    assert autostart.is_enabled()
    assert autostart.set_enabled(False)
    assert "Lyssna" not in registry.values
    assert not autostart.is_enabled()
    assert autostart.set_enabled(False)  # already off is fine


def test_not_frozen_cannot_turn_on(registry, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert not autostart.set_enabled(True)
    assert registry.values == {}


def test_is_enabled_ignores_case_and_quotes(registry):
    registry.values["Lyssna"] = (r'"c:\program files\LYSSNA\lyssna.exe"', REG_SZ)
    assert autostart.is_enabled()
    registry.values["Lyssna"] = (EXE, REG_SZ)
    assert autostart.is_enabled()


def test_is_enabled_false_for_another_path(registry):
    registry.values["Lyssna"] = (r'"D:\Old\Lyssna.exe"', REG_SZ)
    assert not autostart.is_enabled()


def test_registry_errors_never_raise(registry, frozen):
    registry.fail = PermissionError("denied")
    assert not autostart.is_enabled()
    assert not autostart.set_enabled(True)
    assert not autostart.set_enabled(False)


def test_no_winreg_reads_as_off(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)
    assert not autostart.is_enabled()
    assert not autostart.set_enabled(False)
