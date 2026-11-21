"""The interface's shared look-and-layout logic (lyssna.ui.shared) imports and runs without any
Mac framework, so the Windows interface can use it, and the Mac UI modules still hand out the
very same objects under their old names."""

import pkgutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import lyssna.ui.shared

MODULES = sorted(f"lyssna.ui.shared.{m.name}" for m in pkgutil.iter_modules(lyssna.ui.shared.__path__))

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

    from lyssna.ui.shared import history, mark, pill, setup, toast, wave
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


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac UI modules need AppKit")
def test_mac_ui_modules_hand_out_the_shared_objects():
    from lyssna.ui import (
        history_window,
        mark,
        mini_player,
        panel,
        selection_button,
        setup_window,
        theme,
        toast,
        wave,
        whats_new_window,
        widgets,
    )
    from lyssna.ui.shared import button, layout, tokens, whats_new
    from lyssna.ui.shared import history as s_history
    from lyssna.ui.shared import mark as s_mark
    from lyssna.ui.shared import pill as s_pill
    from lyssna.ui.shared import setup as s_setup
    from lyssna.ui.shared import toast as s_toast
    from lyssna.ui.shared import wave as s_wave

    pairs = [
        (mini_player.pick_form, s_pill.pick_form),
        (mini_player.edge_frame, s_pill.edge_frame),
        (mini_player.tuck_due, s_pill.tuck_due),
        (mini_player.DOCKS, s_pill.DOCKS),
        (mini_player.DICTATING, s_pill.DICTATING),
        (wave.wave_points, s_wave.wave_points),
        (wave.ease, s_wave.ease),
        (widgets.squiggle_points, s_wave.squiggle_points),
        (widgets.capsule_centre, s_wave.capsule_centre),
        (widgets.wave_phases, s_wave.wave_phases),
        (widgets.SQUIGGLE_FILL, tokens.SQUIGGLE_FILL),
        (mark.MARK_W, s_mark.MARK_W),
        (toast.toast_width, s_toast.toast_width),
        (toast.tip_origin, s_toast.tip_origin),
        (setup_window.steps_left, s_setup.steps_left),
        (setup_window.opening_tab, s_setup.opening_tab),
        (setup_window.SAMPLE, s_setup.SAMPLE),
        (setup_window.DICTATION_KEY_ENGINES, s_setup.DICTATION_KEY_ENGINES),
        (history_window.day_label, s_history.day_label),
        (history_window.column, s_history.column),
        (history_window.SOURCE_BADGE, s_history.SOURCE_BADGE),
        (history_window.TABS, s_history.TABS),
        (selection_button._intersects, button._intersects),
        (theme.CREAM, tokens.CREAM),
        (theme.INK_TEXT, tokens.INK_TEXT),
        (panel.BG, tokens.PANEL_BG),
        (whats_new_window.CHANGES_TITLE, whats_new.CHANGES_TITLE),
    ]
    for mac, shared in pairs:
        assert mac is shared
    assert panel.DRAG_SLOP == layout.DRAG_SLOP
