"""The engine must import on Windows without any Mac framework, and lyssna.host must hand out
the Windows modules there. Checked here by running a fresh interpreter that pretends to be
Windows and refuses every macOS framework import."""

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
    "lyssna.keystore",
    "lyssna.policy",
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
    "lyssna.menu",
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

    from lyssna import config, host, hotkeys, keystore, providers
    assert host.IS_WINDOWS and host.DEVICE == "PC"
    for name, (_mac, windows) in host.MODULES.items():
        module = getattr(host, name)
        home = "lyssna.app" if name == "app" else "lyssna.win."  # the one app, and the Windows plumbing
        assert (module is None) if windows is None else (module.__name__ == windows and windows.startswith(home)), name
    assert host.interface() == "new"
    import inspect
    from lyssna.mac import appearance as mac_appearance  # its AppKit loads only inside its functions

    def functions(module):
        return {n for n, f in vars(module).items() if inspect.isfunction(f) and not n.startswith("_")}

    assert host.appearance.__name__ == "lyssna.win.appearance"
    assert functions(host.appearance) == functions(mac_appearance) == {"font_files", "tracking", "reduce_motion"}
    assert host.appearance.TEXT_FAMILY == "Inter" and host.appearance.reduce_motion() is False
    assert all(p.is_file() for p in host.appearance.font_files())
    import inspect
    from lyssna.mac import window as mac_window

    def public_functions(module):
        return {n for n, f in vars(module).items() if inspect.isfunction(f) and f.__module__ == module.__name__ and not n.startswith("_")}
    assert host.window.__name__ == "lyssna.win.window"
    assert public_functions(host.window) == public_functions(mac_window), (public_functions(host.window), public_functions(mac_window))
    assert host.window.POINTS_ARE_PHYSICAL is True and mac_window.POINTS_ARE_PHYSICAL is False
    from lyssna.mac import mic as mac_mic  # its AVFoundation loads only inside its functions

    def public_classes(module):
        return {n: {m for m in vars(c) if not m.startswith("_")} for n, c in vars(module).items()
                if inspect.isclass(c) and c.__module__ == module.__name__ and not n.startswith("_")}
    assert host.mic.__name__ == "lyssna.win.mic"
    assert public_functions(host.mic) == public_functions(mac_mic) == {"microphone_status", "request_microphone"}
    assert public_classes(host.mic) == public_classes(mac_mic) == {"EngineInput": {"warm", "open", "close"}}
    assert "PySide6.QtMultimedia" not in sys.modules  # the Windows microphone loads Qt only when it opens
    # Typing and the correction watcher: the Mac twins load Quartz, so their names are compared
    # in tests/test_architecture.py; here the Windows ones must be what dictation reaches.
    assert host.insert.__name__ == "lyssna.win.insert" and host.field_probe.__name__ == "lyssna.win.fieldprobe"
    assert public_functions(host.insert) == {"text_target", "put_text", "snapshot", "restore_unless_changed", "paste"}
    probe = host.field_probe.FieldProbe
    assert {m for m in vars(probe) if not m.startswith("_") and callable(getattr(probe, m))} == {"focused", "value", "same", "front_pid"}
    assert host.insert.text_target() == "maybe"  # no Win32 here: UI Automation cannot say, so paste
    for name in ("globe", "launch_agent", "speechy"):
        try:
            host.require(name)
        except NotImplementedError:
            pass
        else:
            raise AssertionError(name + " should not exist on Windows yet")
    assert not host.NEEDS_ACCESSIBILITY and not host.HAS_GLOBE_KEY and not host.STATUS_TEXT_BESIDE_ICON
    assert host.KEYBOARD_SETTINGS_URL is None and host.ACCESSIBILITY_SETTINGS_URL is None
    assert host.SYSTEM_VOICE == "sapi" and host.library_file("espeak-ng") == "espeak-ng.dll"
    assert str(config.CONFIG_DIR).startswith(os.environ["APPDATA"])
    assert str(config.STATE_DIR).startswith(os.environ["LOCALAPPDATA"])
    assert hotkeys.pretty("ctrl+alt+s") == "Ctrl+Alt+S"
    assert type(keystore._backend).__module__ == "lyssna.win.credentials"
    assert host.ipc.send({"cmd": "status"}) is None
    from lyssna import migrate
    assert migrate.run() == []
    from lyssna.controller import Controller
    ctl = Controller()  # dictation's platform pieces are missing on Windows: building must still work
    assert ctl.dictation.recorder is not None
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
    from lyssna.providers import kokoro
    assert kokoro.MODEL_FILE == "kokoro-v1.0.fp16.onnx" and kokoro.FILES[0] == kokoro.WINDOWS_MODEL
    assert kokoro.DOWNLOAD_SIZE_TEXT == "200 MB" and kokoro.KokoroProvider.setup_hint == "200 MB download"
    assert "about 200 MB" in config.TEMPLATE and "140 MB" not in config.TEMPLATE
    for text in (menu.local_only_title(True), menu.LOCAL_HEADER, *menu.KEY_NAMES.values(), providers.REGISTRY["kokoro"].label,
                 session.COPIED, session.NO_MIC, policy.refusal("X"), config.TEMPLATE):
        assert "Mac" not in text and "⌘" not in text, text
    # The dictation key: Ctrl+Win held, double tap on, named the way Windows writes keys.
    from lyssna.mac import pushtotalk as mac_pushtotalk  # pure key codes, no Quartz
    assert host.pushtotalk.__name__ == "lyssna.win.pushtotalk"
    assert public_functions(host.pushtotalk) == public_functions(mac_pushtotalk) == {"attach"}
    from lyssna.dictation import hotkey
    assert issubclass(host.pushtotalk.PushToTalk, hotkey.PushToTalk) and issubclass(mac_pushtotalk.PushToTalk, hotkey.PushToTalk)
    assert host.DICTATION_KEY_DEFAULT == "ctrl+win" and host.DICTATION_DOUBLE_TAP_DEFAULT is True
    dictation_defaults = config.DEFAULTS["dictation"]
    assert (dictation_defaults["hotkey"], dictation_defaults["hands_free"], dictation_defaults["double_tap"]) == ("ctrl+win", "space", True)
    template_dictation = tomllib.loads(config.TEMPLATE)["dictation"]
    assert (template_dictation["hotkey"], template_dictation["hands_free"], template_dictation["double_tap"]) == ("ctrl+win", "space", True)
    assert "right_option" not in config.TEMPLATE and "fn |" not in config.TEMPLATE
    assert menu.key_name("ctrl+win") == "Ctrl+Win" and menu.key_name("right_control") == "right Ctrl"
    assert menu.dictation_title("ctrl+win", recording=False) == "Start Dictation · hold Ctrl+Win"
    blocked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
    assert not blocked, blocked
    print("imported", len(sys.argv) - 1, "modules")
    """
)


def test_engine_imports_on_pretend_windows_without_mac_frameworks():
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", SCRIPT, *MODULES], cwd=repo, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
