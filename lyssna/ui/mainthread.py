from __future__ import annotations

import threading
from collections.abc import Callable

from Foundation import NSOperationQueue, NSThread


def on_main(fn: Callable, *args) -> None:
    """Run fn(*args) on the AppKit main thread, asynchronously (or inline if already there)."""
    if NSThread.isMainThread():
        fn(*args)
        return
    NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args))


def later(delay: float, fn: Callable, *args) -> threading.Timer:
    """Call fn(*args) on the main thread after `delay` seconds. Returns the timer so it can be cancelled."""
    t = threading.Timer(delay, lambda: on_main(fn, *args))
    t.daemon = True
    t.start()
    return t
