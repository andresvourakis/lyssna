"""Hop onto the Qt GUI thread from any thread, the twin of ui/mainthread.py.

Qt widgets may only be touched on the thread that runs the event loop, and the reader, the
player and the hooks all call back from their own threads. on_main runs a call inline when it
is already on the GUI thread and otherwise posts it there through a queued signal on a QObject
that lives on that thread. A call posted before the event loop starts runs as soon as it spins.
later is a daemon threading.Timer whose callback hops with on_main, so the caller can cancel
it exactly as on the Mac."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, Signal, Slot

log = logging.getLogger("lyssna.ui.qt.mainthread")


class _Dispatcher(QObject):
    """Lives on the GUI thread; its queued signal carries calls there."""

    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, job) -> None:
        fn, args = job
        try:
            fn(*args)
        except Exception:
            log.exception("main-thread call %r failed", fn)


_dispatcher: _Dispatcher | None = None
_lock = threading.Lock()


def _gui_thread() -> QThread | None:
    app = QCoreApplication.instance()
    return app.thread() if app is not None else None


def is_main_thread() -> bool:
    """Is this the GUI thread? Before an application exists, the Python main thread is."""
    gui = _gui_thread()
    if gui is None:
        return threading.current_thread() is threading.main_thread()
    return QThread.currentThread() is gui or QThread.currentThread() == gui


def _get() -> _Dispatcher:
    global _dispatcher
    with _lock:
        if _dispatcher is None:
            _dispatcher = _Dispatcher()
        gui = _gui_thread()
        if gui is not None and _dispatcher.thread() != gui:
            # Made on a worker before the application existed: hand it to the GUI thread.
            # moveToThread must run on the object's own thread, which a worker-made one is not
            # once it has left; so only move it while we are on that thread.
            if QThread.currentThread() == _dispatcher.thread():
                _dispatcher.moveToThread(gui)
        return _dispatcher


def on_main(fn: Callable, *args) -> None:
    """Run fn(*args) on the GUI thread, asynchronously (or inline if already there)."""
    if is_main_thread():
        fn(*args)
        return
    _get().call.emit((fn, args))


def later(delay: float, fn: Callable, *args) -> threading.Timer:
    """Call fn(*args) on the GUI thread after `delay` seconds. Returns the timer so it can be cancelled."""
    t = threading.Timer(delay, lambda: on_main(fn, *args))
    t.daemon = True
    t.start()
    return t


# Made at import, normally on the GUI thread, so the dispatcher already lives there when the
# first worker calls in.
if threading.current_thread() is threading.main_thread():
    _get()
