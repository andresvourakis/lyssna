"""The Windows control channel, run for real over a Unix socket in place of the named pipe."""

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from lyssna import config
from lyssna.win import ipc


@pytest.fixture
def channel(monkeypatch):
    """A short socket path (Unix sockets allow about 100 bytes, more than pytest's tmp_path
    often leaves) used by both ControlServer and send. On Windows a real named pipe with a
    name of its own."""
    short = Path(tempfile.mkdtemp(prefix="lyipc"))
    if sys.platform == "win32":
        address = rf"\\.\pipe\lyssna-test-{os.getpid()}-{short.name}"
    else:
        address = str(short / "s")
        monkeypatch.setattr(ipc, "FAMILY", "AF_UNIX")
    monkeypatch.setattr(ipc, "pipe_address", lambda: address)
    servers = []

    def serve(dispatch):
        server = ipc.ControlServer(dispatch)
        server.start()
        servers.append(server)
        return server

    yield serve
    for server in servers:
        server.close()
    shutil.rmtree(short, ignore_errors=True)


def test_round_trip(channel):
    channel(lambda req: {"ok": True, "echo": req["cmd"], "text": "héllo"})
    assert ipc.send({"cmd": "status"}) == {"ok": True, "echo": "status", "text": "héllo"}
    assert ipc.send({"cmd": "again"})["echo"] == "again"  # one connection per request


def test_dispatch_error_becomes_an_error_reply(channel):
    def boom(req):
        raise ValueError("no such command")

    channel(boom)
    assert ipc.send({"cmd": "x"}) == {"ok": False, "error": "no such command"}


def test_no_marker_means_not_running(channel):
    assert not config.SOCKET_PATH.exists()
    assert ipc.send({"cmd": "status"}) is None


def test_marker_without_a_server_means_not_running(channel):
    config.SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SOCKET_PATH.write_bytes(b"k" * 32)
    assert ipc.send({"cmd": "status"}) is None


def test_wrong_key_gets_no_reply(channel):
    called = []
    channel(lambda req: called.append(req) or {"ok": True})
    config.SOCKET_PATH.write_bytes(b"x" * 32)
    assert ipc.send({"cmd": "status"}) is None
    assert called == []


def test_timeout(channel):
    release = threading.Event()

    def slow(req):
        release.wait(5)
        return {"ok": True}

    channel(slow)
    started = time.monotonic()
    assert ipc.send({"cmd": "status"}, timeout=0.3) is None
    assert time.monotonic() - started < 3
    release.set()


def test_marker_holds_a_fresh_key_and_close_removes_it(channel):
    server = channel(lambda req: {"ok": True})
    key = config.SOCKET_PATH.read_bytes()
    assert len(key) == 32
    server.close()
    assert not config.SOCKET_PATH.exists()
    assert ipc.send({"cmd": "status"}) is None
    server.close()  # twice is harmless


def test_second_instance_fails_and_leaves_the_first_working(channel):
    channel(lambda req: {"ok": True, "who": "first"})
    key = config.SOCKET_PATH.read_bytes()
    with pytest.raises(OSError):
        ipc.ControlServer(lambda req: {"ok": True, "who": "second"}).start()
    assert config.SOCKET_PATH.read_bytes() == key
    assert ipc.send({"cmd": "status"}) == {"ok": True, "who": "first"}


def test_pipe_address_is_a_safe_per_user_name(monkeypatch):
    monkeypatch.setattr(ipc, "_user", lambda: r"DOMAIN\Jöran Ö")
    address = ipc.pipe_address()
    assert address.startswith("\\\\.\\pipe\\lyssna-DOMAIN_J_ran__-")
    assert "\\" not in address[len("\\\\.\\pipe\\"):]
    monkeypatch.setattr(ipc, "_user", lambda: r"DOMAIN\Järan Ä")
    assert ipc.pipe_address() != address  # same safe form, different user
