"""The control channel the CLI uses to drive the running app, on the Mac: one JSON request and
one JSON reply per connection over a Unix socket at config.SOCKET_PATH. Windows has the same
two names over a named pipe in lyssna/win/ipc.py.

The socket path is read at call time, so the tests point it into a temp dir through config."""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
from collections.abc import Callable

from .. import config

log = logging.getLogger("lyssna.mac.ipc")


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline()
            req = json.loads(raw.decode() or "{}")
            resp = self.server.dispatch(req)  # type: ignore[attr-defined]
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        self.wfile.write((json.dumps(resp) + "\n").encode())


class ControlServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, dispatch: Callable[[dict], dict]):
        path = config.SOCKET_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        super().__init__(str(path), _Handler)
        os.chmod(str(path), 0o600)  # other accounts on this Mac cannot send commands
        self.path = path
        self.dispatch = dispatch

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, daemon=True, name="lyssna-server").start()

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


def send(req: dict, timeout: float = 5.0) -> dict | None:
    """Send one request to the running app. Returns None if the app isn't running."""
    path = config.SOCKET_PATH
    if not path.exists():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall((json.dumps(req) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                part = s.recv(65536)
                if not part:
                    break
                data += part
        return json.loads(data.decode() or "{}")
    except (OSError, ValueError):
        return None
