"""Hop onto the Qt GUI thread from any thread, the twin of legacy/ui/mainthread.py.

Qt widgets may only be touched on the thread that runs the event loop, and the reader, the
player and the hooks all call back from their own threads. on_main runs a call inline when it
is already on the GUI thread and otherwise posts it there through a queued signal on a QObject
that lives on that thread. A call posted before the event loop starts runs as soon as it spins.
later is a daemon threading.Timer whose callback hops with on_main, so the caller can cancel
it exactly as on the Mac.

At shutdown: a timer can fire, or a worker can call in, after Qt has deleted the dispatcher
(when the app quits, or at interpreter exit). Such a call is dropped quietly: there is no GUI
thread left to run it on. cancel_pending() cancels every timer still waiting, for the app's
shutdown."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, Signal, Slot

log = logging.getLogger("lyssna.ui.mainthread")


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
_pending: set[threading.Timer] = set()  # later() timers that have not fired or been cancelled
_pending_lock = threading.Lock()


def _gui_thread() -> QThread | None:
    app = QCoreApplication.instance()
    return app.thread() if app is not None else None


def is_main_thread() -> bool:
    """Is this the GUI thread? Before an application exists, the Python main thread is."""
    gui = _gui_thread()
    if gui is None:
        return threading.current_thread() is threading.main_thread()
    return QThread.currentThread() is gui or QThread.currentThread() == gui


def _get() -> _Dispatcher | None:
    """The dispatcher, made on first use; None once Qt has deleted it (the app is ending)."""
    global _dispatcher
    with _lock:
        if _dispatcher is None:
            _dispatcher = _Dispatcher()
        if not shiboken6.isValid(_dispatcher):
            return None
        gui = _gui_thread()
        if gui is not None and _dispatcher.thread() != gui:
            # Made on a worker before the application existed: hand it to the GUI thread.
            # moveToThread must run on the object's own thread, which a worker-made one is not
            # once it has left; so only move it while we are on that thread.
            if QThread.currentThread() == _dispatcher.thread():
                _dispatcher.moveToThread(gui)
        return _dispatcher


def on_main(fn: Callable, *args) -> None:
    """Run fn(*args) on the GUI thread, asynchronously (or inline if already there). After Qt
    has deleted the dispatcher (the app is ending) a call from another thread is dropped."""
    if is_main_thread():
        fn(*args)
        return
    try:
        dispatcher = _get()
        if dispatcher is not None:
            dispatcher.call.emit((fn, args))
            return
    except RuntimeError:  # deleted while this call was on its way
        pass
    log.debug("dropped a main-thread call after Qt shut down: %r", fn)


class _Timer(threading.Timer):
    """A daemon timer that hops to the GUI thread and keeps itself in _pending until it fires
    or is cancelled."""

    def __init__(self, delay: float, fn: Callable, args: tuple):
        super().__init__(delay, self._fire, (fn, args))
        self.daemon = True

    def _fire(self, fn: Callable, args: tuple) -> None:
        with _pending_lock:
            _pending.discard(self)
        on_main(fn, *args)

    def cancel(self) -> None:
        with _pending_lock:
            _pending.discard(self)
        super().cancel()


def later(delay: float, fn: Callable, *args) -> threading.Timer:
    """Call fn(*args) on the GUI thread after `delay` seconds. Returns the timer so it can be cancelled."""
    t = _Timer(delay, fn, args)
    with _pending_lock:
        _pending.add(t)
    t.start()
    return t


def cancel_pending() -> int:
    """Cancel every later() timer that has not fired yet, for shutdown. Returns how many."""
    with _pending_lock:
        timers = list(_pending)
        _pending.clear()
    for t in timers:
        t.cancel()
    return len(timers)


# Made at import, normally on the GUI thread, so the dispatcher already lives there when the
# first worker calls in.
if threading.current_thread() is threading.main_thread():
    _get()
