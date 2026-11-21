"""Tiny JSON-over-unix-socket control channel so the CLI can drive the running menu bar app.

On Windows ControlServer and send come from lyssna/win/ipc.py (a named pipe) instead, and the
Unix socket code below is never defined there (Windows has no Unix stream server)."""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
from collections.abc import Callable

from . import host
from .config import SOCKET_PATH

log = logging.getLogger("lyssna.server")


if host.ipc is not None:
    ControlServer = host.ipc.ControlServer
    send = host.ipc.send
else:
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
            SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
            if SOCKET_PATH.exists():
                SOCKET_PATH.unlink()
            super().__init__(str(SOCKET_PATH), _Handler)
            os.chmod(str(SOCKET_PATH), 0o600)  # other accounts on this Mac cannot send commands
            self.dispatch = dispatch

        def start(self) -> None:
            threading.Thread(target=self.serve_forever, daemon=True, name="lyssna-server").start()

        def close(self) -> None:
            self.shutdown()
            self.server_close()
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass

    def send(req: dict, timeout: float = 5.0) -> dict | None:
        """Send one request to the running app. Returns None if the app isn't running."""
        if not SOCKET_PATH.exists():
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(str(SOCKET_PATH))
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
