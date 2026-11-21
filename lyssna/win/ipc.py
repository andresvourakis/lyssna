r"""The control channel the CLI uses to drive the running app, on Windows.

Why a named pipe: Windows has no Unix socket server in the standard library, and the standard
library's multiprocessing.connection already speaks named pipes. The requests and replies are
the same JSON objects as the Unix socket in lyssna/server.py carries, one per connection.

Named pipes are visible to every account on the PC, so the pipe alone does not keep other
users out. The running app makes a random 32-byte key at start() and writes it to the marker
file config.SOCKET_PATH (%LOCALAPPDATA%\Lyssna\lyssna.pipe, inside the user's own profile,
which other accounts cannot read). Both sides prove they know the key before any request is
sent (the multiprocessing challenge, in both directions), so only this user's processes can
talk to the app, and a pipe made by someone else never receives a request. close() deletes
the marker; send() treats a missing marker as "the app is not running" and does not connect.

A second instance: start() on a pipe that already exists raises OSError (multiprocessing
creates the pipe with FILE_FLAG_FIRST_PIPE_INSTANCE), like the Mac server's bind failure. The
marker is written only after the pipe was created, so the first instance keeps working.

The address and family come from pipe_address() and FAMILY, read at call time, so the tests
run the real server and client over AF_UNIX on a Mac."""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    from multiprocessing import connection

log = logging.getLogger("lyssna.win.ipc")

FAMILY = "AF_PIPE"
KEY_BYTES = 32
MAX_REQUEST = 1 << 20  # bytes; a request is a short JSON object
REQUEST_WAIT = 30.0  # seconds a connected client gets to send its request


def _connection():
    """multiprocessing.connection, imported on first use: importing multiprocessing picks its
    Windows or Unix parts from sys.platform, which the pretend-Windows import test changes."""
    from multiprocessing import connection

    return connection


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "user"


def pipe_address() -> str:
    r"""\\.\pipe\lyssna-<user>. The user name is reduced to safe characters, plus a short hash
    of the exact name so two accounts whose names differ only in other characters never share
    a pipe (the pipe namespace is shared by every session on the PC)."""
    name = _user()
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:64] or "user"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return rf"\\.\pipe\lyssna-{safe}-{digest}"


def _write_marker(key: bytes) -> None:
    path = config.SOCKET_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    os.replace(tmp, path)  # a reader never sees half a key


class ControlServer:
    def __init__(self, dispatch: Callable[[dict], dict], address: str | None = None, family: str | None = None):
        self.dispatch = dispatch
        self._address = address
        self._family = family
        self._listener: connection.Listener | None = None
        self._thread: threading.Thread | None = None
        self._key = b""
        self._closed = False

    def start(self) -> None:
        """Open the pipe and start answering. Raises OSError when another instance owns it."""
        self._address = self._address or pipe_address()
        self._family = self._family or FAMILY
        self._key = secrets.token_bytes(KEY_BYTES)
        # No authkey on the listener: the key check runs on each connection's own thread
        # (_handle), so a client that never answers cannot stall the accept loop.
        self._listener = _connection().Listener(self._address, family=self._family)
        try:
            _write_marker(self._key)
        except Exception:
            self._listener.close()
            self._listener = None
            raise
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="lyssna-server")
        self._thread.start()

    def _accept_loop(self) -> None:
        listener = self._listener
        while not self._closed and listener is not None:
            try:
                conn = listener.accept()
            except Exception as e:
                if self._closed:
                    break
                log.debug("control channel accept failed: %s", type(e).__name__)
                time.sleep(0.1)
                continue
            if self._closed:
                conn.close()
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True, name="lyssna-server-conn").start()

    def _handle(self, conn: connection.Connection) -> None:
        connection = _connection()
        with conn:
            try:
                connection.deliver_challenge(conn, self._key)
                connection.answer_challenge(conn, self._key)
            except Exception as e:
                log.warning("a control channel client failed the key check: %s", type(e).__name__)
                return
            try:
                if not conn.poll(REQUEST_WAIT):
                    return
                raw = conn.recv_bytes(MAX_REQUEST)
                req = json.loads(raw.decode("utf-8") or "{}")
                resp = self.dispatch(req)
            except Exception as e:
                resp = {"ok": False, "error": str(e)}
            try:
                conn.send_bytes(json.dumps(resp).encode("utf-8"))
            except Exception as e:
                log.debug("control channel reply failed: %s", type(e).__name__)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._listener is not None:
            # The accept call does not wake up when the listener closes; a bare connection does.
            with contextlib.suppress(Exception):
                _connection().Client(self._address, family=self._family).close()
            if self._thread is not None:
                self._thread.join(1.0)
            with contextlib.suppress(Exception):
                self._listener.close()
            self._listener = None
        try:
            if config.SOCKET_PATH.read_bytes() == self._key:  # only our own marker
                config.SOCKET_PATH.unlink()
        except OSError:
            pass


def send(req: dict, timeout: float = 5.0) -> dict | None:
    """Send one request to the running app. Returns None if the app isn't running or anything
    goes wrong, including no reply within `timeout` seconds."""
    try:
        key = config.SOCKET_PATH.read_bytes()
    except OSError:
        return None  # no marker: no app is running
    try:
        with _connection().Client(pipe_address(), family=FAMILY, authkey=key) as conn:
            conn.send_bytes(json.dumps(req).encode("utf-8"))
            if not conn.poll(timeout):
                return None
            return json.loads(conn.recv_bytes(MAX_REQUEST).decode("utf-8") or "{}")
    except Exception:
        return None
