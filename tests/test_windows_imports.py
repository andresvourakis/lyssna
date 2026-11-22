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

    REAL_WINDOWS = sys.platform == "win32"  # on a real PC the Win32 calls answer; elsewhere they fail and fall back
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
    assert host.IS_WINDOWS and host.DEVICE == "PC", (host.IS_WINDOWS, host.DEVICE)
    for name, (_mac, windows) in host.MODULES.items():
        module = getattr(host, name)
        home = "lyssna.app" if name == "app" else "lyssna.win."  # the one app, and the Windows plumbing
        assert (module is None) if windows is None else (module.__name__ == windows and windows.startswith(home)), name
    assert host.interface() == "new", host.interface()
    import inspect
    from lyssna.mac import appearance as mac_appearance  # its AppKit loads only inside its functions

    def functions(module):
        return {n for n, f in vars(module).items() if inspect.isfunction(f) and not n.startswith("_")}

    assert host.appearance.__name__ == "lyssna.win.appearance", host.appearance.__name__
    assert functions(host.appearance) == functions(mac_appearance) == {"font_files", "tracking", "reduce_motion", "short_time"}, (
        functions(host.appearance), functions(mac_appearance))
    assert host.appearance.TEXT_FAMILY == "Inter", host.appearance.TEXT_FAMILY
    # A real PC reports its animation setting (a CI runner has animations off); elsewhere there is no Win32, so False.
    motion = host.appearance.reduce_motion()
    assert isinstance(motion, bool) if REAL_WINDOWS else motion is False, motion
    assert all(p.is_file() for p in host.appearance.font_files()), [p for p in host.appearance.font_files() if not p.is_file()]
    import inspect
    from lyssna.mac import window as mac_window

    def public_functions(module):
        return {n for n, f in vars(module).items() if inspect.isfunction(f) and f.__module__ == module.__name__ and not n.startswith("_")}
    assert host.window.__name__ == "lyssna.win.window", host.window.__name__
    assert public_functions(host.window) == public_functions(mac_window), (public_functions(host.window), public_functions(mac_window))
    assert host.window.POINTS_ARE_PHYSICAL is True and mac_window.POINTS_ARE_PHYSICAL is False, (
        host.window.POINTS_ARE_PHYSICAL, mac_window.POINTS_ARE_PHYSICAL)
    from lyssna.mac import mic as mac_mic  # its AVFoundation loads only inside its functions

    def public_classes(module):
        return {n: {m for m in vars(c) if not m.startswith("_")} for n, c in vars(module).items()
                if inspect.isclass(c) and c.__module__ == module.__name__ and not n.startswith("_")}
    assert host.mic.__name__ == "lyssna.win.mic", host.mic.__name__
    assert public_functions(host.mic) == public_functions(mac_mic) == {"microphone_status", "request_microphone"}, (
        public_functions(host.mic), public_functions(mac_mic))
    assert public_classes(host.mic) == public_classes(mac_mic) == {"EngineInput": {"warm", "open", "close"}}, (
        public_classes(host.mic), public_classes(mac_mic))
    assert "PySide6.QtMultimedia" not in sys.modules, "loaded"  # the Windows microphone loads Qt only when it opens
    # Typing and the correction watcher: the Mac twins load Quartz, so their names are compared
    # in tests/test_architecture.py; here the Windows ones must be what dictation reaches.
    assert host.insert.__name__ == "lyssna.win.insert" and host.field_probe.__name__ == "lyssna.win.fieldprobe", (
        host.insert.__name__, host.field_probe.__name__)
    assert public_functions(host.insert) == {"text_target", "put_text", "snapshot", "restore_unless_changed", "paste"}, (
        public_functions(host.insert))
    probe = host.field_probe.FieldProbe
    probe_methods = {m for m in vars(probe) if not m.startswith("_") and callable(getattr(probe, m))}
    assert probe_methods == {"focused", "value", "same", "front_pid"}, probe_methods
    # A real PC asks UI Automation about whatever has focus; elsewhere there is no Win32, so it cannot say and pastes.
    target = host.insert.text_target()
    assert target in ({"text", "maybe", "none"} if REAL_WINDOWS else {"maybe"}), target
    for name in ("globe", "launch_agent", "speechy"):
        try:
            host.require(name)
        except NotImplementedError:
            pass
        else:
            raise AssertionError(name + " should not exist on Windows yet")
    assert not host.NEEDS_ACCESSIBILITY and not host.HAS_GLOBE_KEY and not host.STATUS_TEXT_BESIDE_ICON, (
        host.NEEDS_ACCESSIBILITY, host.HAS_GLOBE_KEY, host.STATUS_TEXT_BESIDE_ICON)
    assert host.KEYBOARD_SETTINGS_URL is None and host.ACCESSIBILITY_SETTINGS_URL is None, (
        host.KEYBOARD_SETTINGS_URL, host.ACCESSIBILITY_SETTINGS_URL)
    assert host.SYSTEM_VOICE == "sapi" and host.library_file("espeak-ng") == "espeak-ng.dll", (
        host.SYSTEM_VOICE, host.library_file("espeak-ng"))
    assert str(config.CONFIG_DIR).startswith(os.environ["APPDATA"]), (str(config.CONFIG_DIR), os.environ["APPDATA"])
    assert str(config.STATE_DIR).startswith(os.environ["LOCALAPPDATA"]), (str(config.STATE_DIR), os.environ["LOCALAPPDATA"])
    assert hotkeys.pretty("ctrl+alt+s") == "Ctrl+Alt+S", hotkeys.pretty("ctrl+alt+s")
    assert type(keystore._backend).__module__ == "lyssna.win.credentials", type(keystore._backend).__module__
    reply = host.ipc.send({"cmd": "status"})
    assert reply is None, reply
    from lyssna import migrate
    migrated = migrate.run()
    assert migrated == [], migrated
    from lyssna.controller import Controller
    pieces = {name: host.require(name) for name in ("mic", "insert", "field_probe", "pushtotalk")}  # dictation's pieces
    assert all(pieces.values()), pieces
    ctl = Controller()  # builds its dictation session with them
    assert ctl.dictation.recorder is not None, "no recorder"
    assert "say" not in providers.REGISTRY and providers.local_fallback() == "kokoro", (
        sorted(providers.REGISTRY), providers.local_fallback())
    assert "sapi" in providers.REGISTRY, sorted(providers.REGISTRY)

    import tomllib
    from lyssna import menu, policy
    from lyssna.dictation import session
    assert "This PC" in menu.local_only_title(False), menu.local_only_title(False)
    assert "this PC" in providers.REGISTRY["kokoro"].label, providers.REGISTRY["kokoro"].label
    assert "Ctrl+V" in session.COPIED, session.COPIED
    assert "PC" in policy.refusal("X"), policy.refusal("X")
    assert "Credential Manager" in config.TEMPLATE, config.TEMPLATE
    assert "Library/Caches" not in config.TEMPLATE and "Keychain" not in config.TEMPLATE, config.TEMPLATE
    assert "[say]" not in config.TEMPLATE and tomllib.loads(config.TEMPLATE)["provider"] == "kokoro", config.TEMPLATE
    from lyssna.providers import kokoro
    assert kokoro.MODEL_FILE == "kokoro-v1.0.fp16.onnx" and kokoro.FILES[0] == kokoro.WINDOWS_MODEL, (
        kokoro.MODEL_FILE, kokoro.FILES[0], kokoro.WINDOWS_MODEL)
    assert kokoro.DOWNLOAD_SIZE_TEXT == "200 MB" and kokoro.KokoroProvider.setup_hint == "200 MB download", (
        kokoro.DOWNLOAD_SIZE_TEXT, kokoro.KokoroProvider.setup_hint)
    assert "about 200 MB" in config.TEMPLATE and "140 MB" not in config.TEMPLATE, config.TEMPLATE
    for text in (menu.local_only_title(True), menu.LOCAL_HEADER, *menu.KEY_NAMES.values(), providers.REGISTRY["kokoro"].label,
                 session.COPIED, session.NO_MIC, policy.refusal("X"), config.TEMPLATE):
        assert "Mac" not in text and "⌘" not in text, text
    # The dictation key: Ctrl+Win held, double tap on, named the way Windows writes keys.
    from lyssna.mac import pushtotalk as mac_pushtotalk  # pure key codes, no Quartz
    assert host.pushtotalk.__name__ == "lyssna.win.pushtotalk", host.pushtotalk.__name__
    assert public_functions(host.pushtotalk) == public_functions(mac_pushtotalk) == {"attach"}, (
        public_functions(host.pushtotalk), public_functions(mac_pushtotalk))
    from lyssna.dictation import hotkey
    assert issubclass(host.pushtotalk.PushToTalk, hotkey.PushToTalk) and issubclass(mac_pushtotalk.PushToTalk, hotkey.PushToTalk), (
        host.pushtotalk.PushToTalk.__mro__, mac_pushtotalk.PushToTalk.__mro__)
    assert host.DICTATION_KEY_DEFAULT == "ctrl+win" and host.DICTATION_DOUBLE_TAP_DEFAULT is True, (
        host.DICTATION_KEY_DEFAULT, host.DICTATION_DOUBLE_TAP_DEFAULT)
    dictation_defaults = config.DEFAULTS["dictation"]
    defaults = (dictation_defaults["hotkey"], dictation_defaults["hands_free"], dictation_defaults["double_tap"])
    assert defaults == ("ctrl+win", "space", True), defaults
    template_dictation = tomllib.loads(config.TEMPLATE)["dictation"]
    in_template = (template_dictation["hotkey"], template_dictation["hands_free"], template_dictation["double_tap"])
    assert in_template == ("ctrl+win", "space", True), in_template
    assert "right_option" not in config.TEMPLATE and "fn |" not in config.TEMPLATE, config.TEMPLATE
    assert menu.key_name("ctrl+win") == "Ctrl+Win" and menu.key_name("right_control") == "right Ctrl", (
        menu.key_name("ctrl+win"), menu.key_name("right_control"))
    assert menu.dictation_title("ctrl+win", recording=False) == "Start Dictation · hold Ctrl+Win", (
        menu.dictation_title("ctrl+win", recording=False))
    # The local cleanup model runs on the CPU here, with 2 to 4 threads.
    from lyssna.dictation.cleaners import s1mini
    assert host.LOCAL_CLEANUP == "cpu" and host.cpu.__name__ == "lyssna.win.cpu", (host.LOCAL_CLEANUP, host.cpu.__name__)
    cores = host.cpu.physical_cores()
    assert (isinstance(cores, int) and cores > 0) if REAL_WINDOWS else cores is None, cores
    settings = s1mini.llama_settings(host.LOCAL_CLEANUP, cores)
    assert settings["n_gpu_layers"] == 0 and 2 <= settings["n_threads"] == settings["n_threads_batch"] <= 4, settings
    assert "Apple silicon" not in config.TEMPLATE and "S1-mini by Superwhisper, about 460 MB)" in config.TEMPLATE, config.TEMPLATE
    blocked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
    assert not blocked, blocked
    print("imported", len(sys.argv) - 1, "modules")
    """
)


def test_engine_imports_on_pretend_windows_without_mac_frameworks():
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", SCRIPT, *MODULES], cwd=repo, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
