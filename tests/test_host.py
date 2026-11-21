import sys
from pathlib import Path

import pytest

import lyssna.capture
from lyssna import config, controller, host, hotkeys
from lyssna.win import hotkeys as win_hotkeys

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="checks the Mac choices")


@mac_only
def test_mac_paths_are_the_ones_lyssna_always_used():
    home = Path.home()
    p = host.paths()
    assert p.config_dir == home / ".config" / "lyssna"
    assert p.state_dir == home / "Library" / "Application Support" / "Lyssna"
    assert p.cache_dir == home / "Library" / "Caches" / "Lyssna"
    assert p.log_dir == home / "Library" / "Logs" / "Lyssna"
    assert p.socket_path == home / "Library" / "Application Support" / "Lyssna" / "lyssna.sock"


@mac_only
def test_mac_gets_the_mac_modules():
    assert host.IS_MAC and not host.IS_WINDOWS and host.DEVICE == "Mac"
    assert host.capture is lyssna.capture
    assert controller.capture is lyssna.capture
    assert host.ipc is None and host.autostart_impl is None and host.credentials is None and host.policy_impl is None
    assert controller.MIC_SETTINGS_URL == "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"


@mac_only
def test_mac_hotkeys_are_unchanged():
    import Quartz

    flags = Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskAlternate
    assert hotkeys.parse("ctrl+alt+s") == (flags, 1)
    assert hotkeys.parse("ctrl+alt+right") == (flags, 124)
    assert hotkeys.pretty("ctrl+alt+s") == "⌃⌥S"
    assert hotkeys.pretty("ctrl+alt+space") == "⌃⌥Space"


def test_unknown_host_attribute_raises():
    with pytest.raises(AttributeError):
        host.no_such_thing  # noqa: B018


def test_windows_table_parses_every_default_hotkey():
    for spec in config.DEFAULTS["hotkeys"].values():
        flags, key = hotkeys.parse_with(spec, win_hotkeys.MODIFIERS, win_hotkeys.KEYCODES)
        assert flags == win_hotkeys.CTRL | win_hotkeys.ALT
        assert 0 < key < 0x100
    assert hotkeys.parse_with("ctrl+alt+s", win_hotkeys.MODIFIERS, win_hotkeys.KEYCODES) == (3, 0x53)
    assert hotkeys.parse_with("cmd+shift+f5", win_hotkeys.MODIFIERS, win_hotkeys.KEYCODES) == (12, 0x74)


def test_windows_table_knows_every_mac_key_name():
    from lyssna import hotkeys_mac

    assert set(win_hotkeys.KEYCODES) == set(hotkeys_mac.KEYCODES)
    assert set(hotkeys_mac.MODIFIERS) <= set(win_hotkeys.MODIFIERS)


def test_windows_pretty_names():
    assert win_hotkeys.pretty(hotkeys.tokens("ctrl+alt+s")) == "Ctrl+Alt+S"
    assert win_hotkeys.pretty(hotkeys.tokens("cmd+shift+space")) == "Win+Shift+Space"
