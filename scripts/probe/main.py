"""Starts the probe in one of its modes: guided (the default, on Windows), --self-test (no
person, no window), --preview (the screens on a Mac with made-up results) and
--screenshots DIR (every screen saved as a picture, then exit).

The QApplication is created before anything else, because lyssna/win/audio.py reuses the
application object it finds and would otherwise make its own on its audio thread."""

from __future__ import annotations

import argparse
import collections
import faulthandler
import logging
import os
import sys
from pathlib import Path

from . import PROBE_VERSION


class LogCollector(logging.Handler):
    """Keeps the last few hundred log lines for the report. Lyssna's logs never hold the
    user's text, only lengths, sources and timings."""

    def __init__(self, limit: int = 400):
        super().__init__(logging.INFO)
        self.records: collections.deque[str] = collections.deque(maxlen=limit)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            pass

    def lines(self) -> list[str]:
        return list(self.records)


def _setup_logging(to_console: bool) -> LogCollector:
    collector = LogCollector()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(collector)
    if to_console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("    log %(levelname)s %(name)s: %(message)s"))
        root.addHandler(stream)
    return collector


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _choose_platform() -> str:
    """Offscreen Qt only when this session has no desktop that can show a window."""
    if os.environ.get("QT_QPA_PLATFORM"):
        return f"QT_QPA_PLATFORM was already set to {os.environ['QT_QPA_PLATFORM']}"
    if sys.platform != "win32":
        return "default (not Windows)"
    try:
        from . import winapi

        if winapi.interactive_desktop():
            return "real windows (an input desktop is available)"
    except Exception as e:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        return f"offscreen (the desktop check failed: {type(e).__name__}: {e})"
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    return "offscreen (no input desktop in this session)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check how Lyssna works on this Windows PC.")
    parser.add_argument("--self-test", action="store_true", help="run every check that needs no person, print the report, exit")
    parser.add_argument("--report-file", help="with --self-test: also write the report to this file")
    parser.add_argument("--preview", action="store_true", help="show the screens with made-up results (for review on a Mac)")
    parser.add_argument("--screenshots", metavar="DIR", help="save every screen of the preview as a PNG in DIR, then exit")
    args = parser.parse_args(argv)
    _utf8_console()

    if args.self_test:
        collector = _setup_logging(to_console=True)
        faulthandler.enable()
        note = _choose_platform()
        from PySide6.QtWidgets import QApplication

        app = QApplication(sys.argv[:1])
        from . import selftest

        code = selftest.run(app, collector.lines, args.report_file, note)
        sys.stdout and sys.stdout.flush()
        os._exit(code)  # the audio thread and COM threads are daemons; leave without waiting on Qt teardown

    shots = Path(args.screenshots).resolve() if args.screenshots else None
    preview = args.preview or shots is not None
    if not preview and sys.platform != "win32":
        print("The guided probe runs on Windows. On this computer, use --preview or --screenshots DIR.", file=sys.stderr)
        return 2
    if shots is not None:
        shots.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    collector = _setup_logging(to_console=preview)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])

    from .backend import FakeBackend, RealBackend, report_dir
    from .flow import Flow
    from .report import Report
    from .steps import STEPS, preview_fakes
    from .ui import ProbeWindow

    crash_file = None
    if not preview:
        try:
            crash_file = open(report_dir() / "lyssna-probe-crash.txt", "w", encoding="utf-8")
            faulthandler.enable(crash_file)
        except Exception:
            crash_file = None
    backend = FakeBackend(fast=shots is not None) if preview else RealBackend()
    report = Report("preview" if preview else "guided",
                    {"probe_version": PROBE_VERSION, "qt_platform": app.platformName()})
    window = ProbeWindow(preview=preview)
    flow = Flow(window, STEPS, report, backend, collector.lines, auto=shots is not None, shots=shots)
    flow.fakes = preview_fakes()
    window.on_close = flow.abort
    window.show()
    QTimer.singleShot(0, flow.start)
    try:
        code = app.exec()
    finally:
        try:
            backend.remove_hooks()
        except Exception:
            logging.getLogger("probe").exception("could not remove the hooks")
        if crash_file is not None:
            faulthandler.disable()
            crash_file.close()
            try:
                path = Path(crash_file.name)
                if path.stat().st_size == 0:
                    path.unlink()
            except OSError:
                pass
    if shots is not None:
        print(f"Saved {len(list(shots.glob('*.png')))} screenshots to {shots}")
    return code
