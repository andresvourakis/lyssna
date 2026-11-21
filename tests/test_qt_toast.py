"""The Windows toast and tip (lyssna/ui/qt/toast.py): the width rule, truncation, the action,
in-place updates, expiry and replacement, and where a tip goes beside each edge. Runs on the
offscreen platform with a fixed fake screen."""

import math
import os
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6.QtWidgets")

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from lyssna.controller import Notice
from lyssna.ui.shared.toast import ACTION_PAD, BOTTOM, MAX_W, TIP_GAP, TIP_H, TOAST_H, toast_width

SCREEN_H = 720.0
VF = (0.0, 0.0, 1280.0, 680.0)  # the taskbar takes the bottom 40


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("a non-GUI Qt application already exists in this process")
    return app or QApplication(["lyssna-tests"])


def pump(seconds: float = 0.0, until=None) -> None:
    end = time.monotonic() + (seconds if until is None else 2.0)
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


@pytest.fixture
def screen(qapp, monkeypatch):
    from lyssna.ui.qt import coords

    monkeypatch.setattr(coords, "primary_height", lambda: SCREEN_H)
    monkeypatch.setattr(coords, "visible_frame", lambda screen=None: VF)
    monkeypatch.setattr(coords, "screen_under_mouse", lambda: None)


@pytest.fixture
def toast(screen):
    from lyssna.ui.qt.toast import Toast

    t = Toast()
    yield t
    t._token = object()
    t.hide()


def click(widget) -> None:
    centre = QPointF(widget.width() / 2, widget.height() / 2)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(kind, centre, widget.mapToGlobal(centre), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, ev)


# -- toast -------------------------------------------------------------------------------------


def test_width_follows_the_shared_rule(toast):
    from lyssna.ui.qt.toast import text_width

    w = toast.layout("Nothing selected", "")
    assert w == toast_width(math.ceil(text_width("Nothing selected", toast.text_font)) + 4, 0)
    assert not toast.action.isVisible() and toast.text.toolTip() == ""
    w = toast.layout("Transcript cancelled", "Undo")
    action_w = math.ceil(text_width("Undo", toast.action.font_))
    assert w == toast_width(math.ceil(text_width("Transcript cancelled", toast.text_font)) + 4, action_w)
    assert toast.action.width() == round(action_w + 2 * ACTION_PAD)
    assert toast.action.geometry().right() < w


def test_long_text_is_cut_at_the_end_with_the_whole_as_tooltip(toast):
    text = "That key didn't work. " * 20
    w = toast.layout(text, "Open Settings", 460.5)
    assert w == 460
    assert toast.text.toolTip() == text
    shown = toast.text.shown_text()
    assert shown.endswith("…") and text.startswith(shown[:-1])
    assert toast.text.geometry().right() < toast.action.geometry().left()


def test_present_sits_at_the_bottom_centre_and_fades_in(toast):
    toast.present(Notice("Nothing selected"))
    x, y, w, h = toast.frame()
    assert (h, y) == (TOAST_H, VF[3] - BOTTOM - TOAST_H)
    assert abs((x + w / 2) - VF[2] / 2) <= 1
    assert toast.visible()
    pump(until=lambda: toast.alpha() == 1.0)
    assert toast.alpha() == 1.0


def test_floor_keeps_it_above_the_pill(toast):
    floor = SCREEN_H - 500.0  # Mac-style: the toast's bottom may not go below Qt y 500
    toast.present(Notice("Copied"), floor=floor)
    assert toast.frame()[1] == 500.0 - TOAST_H
    toast.present(Notice("Copied"), floor=10.0)  # below the usual spot: no change
    assert toast.frame()[1] == VF[3] - BOTTOM - TOAST_H


def test_max_width_leaves_room_on_a_narrow_screen(toast, monkeypatch):
    from lyssna.ui.qt import coords

    monkeypatch.setattr(coords, "visible_frame", lambda screen=None: (0.0, 0.0, 500.0, 680.0))
    toast.present(Notice("word " * 100))
    assert toast.frame()[2] == 460.0
    monkeypatch.setattr(coords, "visible_frame", lambda screen=None: VF)
    toast.present(Notice("word " * 300))
    assert toast.frame()[2] == MAX_W


def test_action_click_dismisses_then_runs_the_action(toast):
    seen = []
    toast.present(Notice("Transcript cancelled", "Undo", on_action=lambda: seen.append(toast._key)))
    click(toast.action)
    assert seen == [""]  # dismissed (key cleared) before the action ran
    pump(until=lambda: not toast.visible())
    assert not toast.visible()


def test_a_failing_action_is_logged_not_raised(toast):
    def boom():
        raise RuntimeError("boom")
    toast.present(Notice("Transcript cancelled", "Undo", on_action=boom))
    click(toast.action)  # does not raise


def test_same_key_updates_in_place_without_fading_again(toast):
    toast.present(Notice("Getting the speech model ready, 10%", key="model"))
    pump(until=lambda: toast.alpha() == 1.0)
    toast.present(Notice("Getting the speech model ready, 20%", key="model"))
    assert toast.alpha() == 1.0 and toast._alpha_anim is None
    assert toast.text.text() == "Getting the speech model ready, 20%"
    toast.present(Notice("Something else"))  # another notice fades in afresh
    assert toast.alpha() < 1.0


def test_expires_after_its_seconds(toast):
    toast.present(Notice("Nothing selected", seconds=0.05))
    pump(until=lambda: not toast.visible())
    assert not toast.visible() and toast._token is None


def test_a_new_notice_replaces_the_old_and_its_expiry(toast):
    toast.present(Notice("First", seconds=0.05))
    toast.present(Notice("Second", seconds=5.0))
    pump(0.3)
    assert toast.visible() and toast.text.text() == "Second"


def test_dismiss_then_present_again_keeps_the_new_one(toast):
    toast.present(Notice("First"))
    toast.dismiss()
    toast.present(Notice("Second"))
    pump(0.4)  # the first one's fade-out ends meanwhile
    assert toast.visible()


# -- tip ---------------------------------------------------------------------------------------


@pytest.fixture
def tip(screen):
    from lyssna.ui.qt.toast import Tip

    t = Tip()
    yield t
    t.hide()


def test_tip_never_takes_the_mouse(tip):
    assert tip.click_through()
    assert tip.panel.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


@pytest.mark.parametrize(("edge", "expected_x"), [
    ("left", lambda w: 100 + 40 + TIP_GAP),     # the pill on the left edge: the tip to its right
    ("right", lambda w: 100 - TIP_GAP - w),     # on the right edge: to its left
])
def test_tip_beside_a_side_edge(tip, edge, expected_x):
    anchor = (100.0, 200.0, 40.0, 40.0)  # Mac-style: y up from the primary screen's bottom
    tip.show_beside(anchor, edge, "Dictate", "fn")
    x, y, w, h = tip.frame()
    assert h == TIP_H and x == pytest.approx(expected_x(w), abs=1)  # whole pixels
    # Centred on the anchor: Mac y 200 + (40 - 30) / 2 = 205 for the tip's bottom.
    assert y == pytest.approx(SCREEN_H - 205 - TIP_H, abs=1)
    assert tip.visible()


def test_tip_above_a_bottom_edge_button(tip):
    anchor = (100.0, 20.0, 40.0, 40.0)
    tip.show_beside(anchor, "bottom", "Finish", "fn")
    x, y, w, h = tip.frame()
    assert x == pytest.approx(100 + (40 - w) / 2, abs=1)  # whole pixels
    assert y + h == pytest.approx(SCREEN_H - (20 + 40 + TIP_GAP), abs=1)  # TIP_GAP above the button's top


def test_tip_width_is_the_text_and_its_key(tip):
    from lyssna.ui.qt import theme
    from lyssna.ui.qt.toast import TEXT_SIZE, text_width
    from lyssna.ui.shared.toast import TIP_PAD

    tip.show_beside((100.0, 200.0, 40.0, 40.0), "left", "Cancel", "esc")
    expected = math.ceil(text_width("Cancel", theme.ui_font(TEXT_SIZE, "medium")) + text_width("  esc", theme.ui_font(TEXT_SIZE, "bold")))
    assert tip.frame()[2] == TIP_PAD + expected + TIP_PAD
    assert tip.text.text() == "Cancel  esc"
    tip.hide()
    assert not tip.visible()
