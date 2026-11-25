"""The real Lyssna app in normal mode, started by the harness as its own process on the profile the
harness prepared: scripts/compare/isolate.py points every file at that profile and switches off
the network, the models, the browser and the real Keychain, then `lyssna -v` runs as usual.

    python -m scripts.behave.launch --home DIR --socket PATH [--probe] [--no-nap] [--enter-poll SECS]

The interface comes from LYSSNA_INTERFACE in the environment (legacy or new), as for the real app.

--probe adds timestamped debug lines to the app's log at the points the hover measurement needs
(the tracking area's enter and exit, the arrival callback, every scale step and every paint of
the scaled pill on the Qt side). Nothing in lyssna/ changes: the probes wrap functions from here.
--no-nap holds an NSProcessInfo activity (user initiated, latency critical) for the whole run,
which turns App Nap and timer coalescing off for this process, to test whether they slow it.
--enter-poll overrides lyssna.mac.window.ENTER_POLL_SECS, to test whether the poll is the delay.
--models lets the speech, cleanup and voice models load (isolate.py refuses that); the harness
put copies of their files in the profile first. --mic DIR puts scripts/behave/feed.py's recorded
input in place of the microphone. --kokoro-dir puts the voice's folder at a short path (see
feed.link_models)."""

from __future__ import annotations

import argparse
import functools
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger("behave.probe")

_activity = None  # the App Nap opt-out, kept alive for the whole run


def _probe(name: str, extra: str = "") -> None:
    log.debug("probe %s t=%.4f %s", name, time.time(), extra)


def _set(owner, attr: str, wrapper, pyobjc: bool) -> None:
    if pyobjc:  # a Python-only method on an Objective-C class must stay one
        import objc

        wrapper = objc.python_method(wrapper)
    setattr(owner, attr, wrapper)


def _wrap(owner, attr: str, name: str, describe=None, pyobjc: bool = False) -> None:
    original = getattr(owner, attr)

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        try:
            _probe(name, describe(*args, **kwargs) if describe else "")
        except Exception:
            pass
        return original(*args, **kwargs)

    _set(owner, attr, wrapper, pyobjc)


def _wrap_timed(owner, attr: str, name: str, pyobjc: bool = False) -> None:
    """A probe at the start and one at the end of the call, with its length."""
    original = getattr(owner, attr)

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        t0 = time.time()
        _probe(f"{name}_start")
        try:
            return original(*args, **kwargs)
        finally:
            _probe(f"{name}_end", f"took={(time.time() - t0) * 1000:.1f}ms")

    _set(owner, attr, wrapper, pyobjc)


def install_selection_probes() -> None:
    """Both sides: the path from the mouse-up of a selection to the play button on screen (the
    gesture, the Accessibility reads, the decision, the button's show)."""
    from lyssna import gestures, selection
    from lyssna.mac import capture

    _wrap(gestures.SelectionGestureDetector, "mouse_down", "sel_down")
    _wrap(gestures.SelectionGestureDetector, "mouse_up", "sel_up")
    _wrap(selection.SelectionWatcher, "gesture", "sel_gesture")
    _wrap_timed(capture, "ax_selection", "sel_ax")
    if os.environ.get("LYSSNA_INTERFACE", "") == "legacy":
        from lyssna.legacy.ui import panel as legacy_panel
        from lyssna.legacy.ui import selection_button as legacy_button

        _wrap(legacy_button.SelectionButton, "_present", "button_present", pyobjc=True)
        _wrap_timed(legacy_panel.FloatingPanel, "show_at", "button_show", pyobjc=True)
        return
    from lyssna.ui import panel, selection_button

    _wrap(selection_button.SelectionButton, "_present", "button_present")
    _wrap_timed(selection_button.SelectionButton, "_show", "button_show")
    _wrap_timed(panel.FloatingPanel, "order_front", "panel_order_front")


def install_probes() -> None:
    """The Qt side's probes. The legacy side animates on the render server with no per-frame Python,
    so its own debug lines (mouseEntered, set_scale, scale animation finished) are all there is."""
    try:
        install_selection_probes()
    except Exception:
        log.exception("could not install the selection probes")
    if os.environ.get("LYSSNA_INTERFACE", "") == "legacy":
        return
    from lyssna.mac import window
    from lyssna.ui import mini_player

    _wrap(window, "_owner_event", "area", lambda owner, kind: kind)
    _wrap(window._Watch, "check", "watch_check")
    _wrap(mini_player.MiniPlayer, "mouse_entered", "mouse_entered", lambda self: f"form={self.form}")
    _wrap(mini_player.ScaleHost, "set_scale", "scale_step", lambda self, s: f"s={float(s):.3f}")
    _wrap(mini_player.ScaleHost, "paintEvent", "scale_paint", lambda self, ev: f"s={self.scale:.3f}")
    from lyssna.ui import sliver

    _wrap(sliver.Sliver, "set_slide", "slide_step", lambda self, points: f"p={float(points):.2f}")


def _model_loaders() -> list:
    """The three model loaders isolate.py refuses, to put back after it: the speech, cleanup and
    voice models then load from the profile's own copies. Downloads and the network stay refused."""
    from lyssna.dictation.cleaners import s1mini
    from lyssna.dictation.transcribers import parakeet
    from lyssna.providers import kokoro

    return [(parakeet, "_make_model", parakeet._make_model), (s1mini, "_make_runner", s1mini._make_runner),
            (kokoro, "_make_engine", kokoro._make_engine)]


def hold_activity() -> None:
    global _activity
    from Foundation import NSProcessInfo

    user_initiated, latency_critical = 0x00FFFFFF, 0xFF00000000
    _activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
        user_initiated | latency_critical, "behaviour harness: App Nap off")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.behave.launch")
    ap.add_argument("--home", required=True)
    ap.add_argument("--socket", required=True)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--no-nap", action="store_true")
    ap.add_argument("--enter-poll", type=float)
    ap.add_argument("--models", action="store_true")
    ap.add_argument("--mic")
    ap.add_argument("--kokoro-dir")
    args = ap.parse_args(argv)

    from scripts.compare.isolate import isolate

    loaders = _model_loaders() if args.models else []
    isolate(home=args.home, socket_path=args.socket)
    for owner, attr, original in loaders:  # the files are in the profile (feed.link_models): let them load
        setattr(owner, attr, original)
    if args.kokoro_dir:
        from lyssna.providers import kokoro

        kokoro.DATA_DIR = Path(args.kokoro_dir)
    if args.mic:
        from . import feed

        feed.install(args.mic)
    if args.probe:
        install_probes()
    if args.no_nap:
        hold_activity()
    if args.enter_poll:
        from lyssna.mac import window

        window.ENTER_POLL_SECS = args.enter_poll
    from lyssna import cli

    code = cli.main(["-v"])
    log.info("behave launch: the app returned %s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
