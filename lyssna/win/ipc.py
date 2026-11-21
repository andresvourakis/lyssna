"""The control channel the CLI uses to drive the running app, on Windows.

The real version will use a named pipe through the standard library's
multiprocessing.connection, carrying the same one-line JSON requests and replies as the Unix
socket in lyssna/server.py. Not built yet: the server cannot start and send() always says
the app is not running."""

from __future__ import annotations

from collections.abc import Callable


class ControlServer:
    def __init__(self, dispatch: Callable[[dict], dict]):
        self.dispatch = dispatch

    def start(self) -> None:
        raise NotImplementedError("The Windows control channel is not built yet")

    def close(self) -> None:
        pass


def send(req: dict, timeout: float = 5.0) -> dict | None:
    """Send one request to the running app. Returns None if the app isn't running."""
    return None
