"""The interface's look-and-layout rules (lyssna.ui.layout) import and run without any Mac
framework and without Qt, on a pretend Windows. (That the Mac's legacy interface hands out the
very same objects is checked in legacy/test_layout_sharing.py.)"""

import pkgutil
import subprocess
import sys
import textwrap
from pathlib import Path

import lyssna.ui.layout

MODULES = sorted(f"lyssna.ui.layout.{m.name}" for m in pkgutil.iter_modules(lyssna.ui.layout.__path__))

SCRIPT = textwrap.dedent(
    """
    import sys

    sys.platform = "win32"

    BLOCKED = {"AppKit", "Foundation", "Quartz", "objc", "rumps", "AVFoundation", "ApplicationServices",
               "CoreFoundation", "Security", "ServiceManagement", "PyObjCTools", "PySide6"}

    class NoMacFrameworks:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"{name} is blocked here")
            return None

    sys.meta_path.insert(0, NoMacFrameworks())

    import importlib
    for name in sys.argv[1:]:
        importlib.import_module(name)

    from datetime import date

    from lyssna.ui.layout import history, mark, pill, setup, toast, wave
    assert pill.pick_form("idle", "recording", False, True) == "dictate"
    assert pill.pick_form("playing", "idle", True, True) == "sliver"
    assert pill.pick_form("idle", "idle", False, False) == ""
    assert pill.edge_frame((0.0, 0.0, 1000.0, 800.0), "bottom", -1.0, 60.0, 34.0) == ((470.0, 10.0), (60.0, 34.0))
    points = wave.wave_points(1.0, 0.0)
    assert len(points) == wave.STEPS + 1 and points[0] == (wave.WAVE_W / 2, wave.STROKE / 2)
    assert toast.toast_width(100.0, 0.0) == 136.0
    assert setup.steps_left([True, False, False]) == 2 and setup.tab_status(1) == "1 step left"
    assert history.day_label(date(2026, 11, 20), date(2026, 11, 21)) == "Yesterday"
    stem, line, width = mark.mark_geometry(((0.0, 0.0), (292.0, 240.0)))
    assert stem == (0.0, 0.0, 48.0, 240.0, 24.0) and width == 44.0 and len(line) == mark.WAVE_STEPS + 1

    blocked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
    assert not blocked, blocked
    print("imported", len(sys.argv) - 1, "modules")
    """
)


def test_shared_ui_imports_on_pretend_windows_without_mac_frameworks():
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", SCRIPT, *MODULES], cwd=repo, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
