"""The engine must import on Windows without any Mac framework. Checked here by running a
fresh interpreter that pretends to be Windows and refuses every macOS framework import."""

import pkgutil
import subprocess
import sys
import textwrap
from pathlib import Path

import lyssna.win

MODULES = [
    "lyssna.host",
    "lyssna.config",
    "lyssna.hotkeys",
    "lyssna.gestures",
    "lyssna.selection",
    "lyssna.keychain",
    "lyssna.policy",
    "lyssna.autostart",
    "lyssna.server",
    "lyssna.support",
    "lyssna.migrate",
    "lyssna.player",
    "lyssna.providers",
    "lyssna.providers.kokoro",
    "lyssna.controller",
    "lyssna.cli",
    "lyssna.dictation.insert",
    "lyssna.dictation.learn",
    "lyssna.dictation.session",
    "lyssna.dictation.recorder",
    "lyssna.dictation.pipeline",
    "lyssna.dictation.hotkey",
    *sorted(f"lyssna.win.{m.name}" for m in pkgutil.iter_modules(lyssna.win.__path__)),
]

SCRIPT = textwrap.dedent(
    """
    import os, sys, tempfile

    sys.platform = "win32"
    root = tempfile.mkdtemp()
    os.environ["APPDATA"] = os.path.join(root, "Roaming")
    os.environ["LOCALAPPDATA"] = os.path.join(root, "Local")

    BLOCKED = {"AppKit", "Foundation", "Quartz", "objc", "rumps", "AVFoundation", "ApplicationServices",
               "CoreFoundation", "Security", "ServiceManagement", "PyObjCTools"}

    class NoMacFrameworks:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"{name} is a macOS framework")
            return None

    sys.meta_path.insert(0, NoMacFrameworks())

    import importlib
    for name in sys.argv[1:]:
        importlib.import_module(name)

    from lyssna import config, host, hotkeys, keychain, providers, server
    assert host.IS_WINDOWS and host.DEVICE == "PC"
    assert str(config.CONFIG_DIR).startswith(os.environ["APPDATA"])
    assert str(config.STATE_DIR).startswith(os.environ["LOCALAPPDATA"])
    assert hotkeys.pretty("ctrl+alt+s") == "Ctrl+Alt+S"
    assert type(keychain._backend).__name__ == "CredentialBackend"
    assert server.send({"cmd": "status"}) is None
    assert "say" not in providers.REGISTRY and providers.local_fallback() == "kokoro"
    assert "sapi" in providers.REGISTRY

    import tomllib
    from lyssna import menu, policy
    from lyssna.dictation import session
    assert "This PC" in menu.local_only_title(False)
    assert "this PC" in providers.REGISTRY["kokoro"].label
    assert "Ctrl+V" in session.COPIED
    assert "PC" in policy.refusal("X")
    assert "Credential Manager" in config.TEMPLATE
    assert "Library/Caches" not in config.TEMPLATE and "Keychain" not in config.TEMPLATE
    assert "[say]" not in config.TEMPLATE and tomllib.loads(config.TEMPLATE)["provider"] == "kokoro"
    for text in (menu.local_only_title(True), menu.LOCAL_HEADER, *menu.KEY_NAMES.values(), providers.REGISTRY["kokoro"].label,
                 session.COPIED, session.NO_MIC, policy.refusal("X"), config.TEMPLATE):
        assert "Mac" not in text and "⌘" not in text, text
    blocked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
    assert not blocked, blocked
    print("imported", len(sys.argv) - 1, "modules")
    """
)


def test_engine_imports_on_pretend_windows_without_mac_frameworks():
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", SCRIPT, *MODULES], cwd=repo, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
