"""The Windows policy reader, with a fake winreg module in place of the registry."""

import sys
import types

import pytest

from lyssna.win import policy

HKLM, HKCU = "HKLM", "HKCU"
REG_SZ, REG_DWORD = 1, 4


def fake_winreg(values: dict, errors: dict | None = None) -> types.ModuleType:
    """values: {(hive, name): (value, kind)}; errors: {hive: exception raised on OpenKey}."""
    errors = errors or {}
    mod = types.ModuleType("winreg")
    mod.HKEY_LOCAL_MACHINE, mod.HKEY_CURRENT_USER = HKLM, HKCU
    mod.REG_SZ, mod.REG_DWORD = REG_SZ, REG_DWORD

    class Key:
        def __init__(self, hive):
            self.hive = hive

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(hive, path):
        assert path == r"Software\Policies\Lyssna"
        if hive in errors:
            raise errors[hive]
        if not any(h == hive for h, _ in values):
            raise FileNotFoundError(path)
        return Key(hive)

    def query_value_ex(key, name):
        if (key.hive, name) not in values:
            raise FileNotFoundError(name)
        return values[(key.hive, name)]

    mod.OpenKey = open_key
    mod.QueryValueEx = query_value_ex
    return mod


@pytest.fixture
def registry(monkeypatch):
    def install(values, errors=None):
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg(values, errors))

    return install


def test_dword_one(registry):
    registry({(HKCU, "LocalOnly"): (1, REG_DWORD)})
    assert policy.read_preference("LocalOnly") == 1


def test_absent(registry):
    registry({})
    assert policy.read_preference("LocalOnly") is None


def test_machine_wins_over_user(registry):
    registry({(HKLM, "LocalOnly"): (0, REG_DWORD), (HKCU, "LocalOnly"): (1, REG_DWORD)})
    assert policy.read_preference("LocalOnly") == 0


def test_user_value_when_machine_key_has_other_values(registry):
    registry({(HKLM, "Other"): (1, REG_DWORD), (HKCU, "LocalOnly"): (1, REG_DWORD)})
    assert policy.read_preference("LocalOnly") == 1


def test_not_a_dword_is_ignored(registry):
    registry({(HKLM, "LocalOnly"): ("1", REG_SZ)})
    assert policy.read_preference("LocalOnly") is None


def test_error_reads_as_unset(registry):
    registry({(HKLM, "LocalOnly"): (1, REG_DWORD)}, errors={HKLM: PermissionError("denied"), HKCU: OSError("broken")})
    assert policy.read_preference("LocalOnly") is None


def test_no_winreg_reads_as_unset(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)  # import raises ImportError, as on a Mac
    assert policy.read_preference("LocalOnly") is None
