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
--enter-poll overrides lyssna.mac.window.ENTER_POLL_SECS, to test whether the poll is the delay."""

from __future__ import annotations

import argparse
import functools
import logging
import os
import sys
import time

log = logging.getLogger("behave.probe")

_activity = None  # the App Nap opt-out, kept alive for the whole run


def _probe(name: str, extra: str = "") -> None:
    log.debug("probe %s t=%.4f %s", name, time.time(), extra)


def _wrap(owner, attr: str, name: str, describe=None) -> None:
    original = getattr(owner, attr)

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        try:
            _probe(name, describe(*args, **kwargs) if describe else "")
        except Exception:
            pass
        return original(*args, **kwargs)

    setattr(owner, attr, wrapper)


def install_probes() -> None:
    """The Qt side's probes. The legacy side animates on the render server with no per-frame Python,
    so its own debug lines (mouseEntered, set_scale, scale animation finished) are all there is."""
    if os.environ.get("LYSSNA_INTERFACE", "") == "legacy":
        return
    from lyssna.mac import window
    from lyssna.ui import mini_player

    _wrap(window, "_owner_event", "area", lambda owner, kind: kind)
    _wrap(window._Watch, "check", "watch_check")
    _wrap(mini_player.MiniPlayer, "mouse_entered", "mouse_entered", lambda self: f"form={self.form}")
    _wrap(mini_player.ScaleHost, "set_scale", "scale_step", lambda self, s: f"s={float(s):.3f}")
    _wrap(mini_player.ScaleHost, "paintEvent", "scale_paint", lambda self, ev: f"s={self.scale:.3f}")


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
    args = ap.parse_args(argv)

    from scripts.compare.isolate import isolate

    isolate(home=args.home, socket_path=args.socket)
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
