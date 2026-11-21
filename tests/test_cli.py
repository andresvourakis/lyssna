"""CLI: reading sources and routing arguments. The socket, standalone playback and logging are
stubbed, so nothing talks to a running app or plays audio."""

import io
import os
import sys

import pytest

from lyssna import cli, config


@pytest.fixture
def sent(monkeypatch):
    """Every request the CLI would send to the app; the fake app answers ok."""
    requests = []

    def send(req, timeout=5.0):
        requests.append(req)
        return {"ok": True, "status": {"state": "idle"}}

    monkeypatch.setattr(cli.host.ipc, "send", send)
    monkeypatch.setattr(cli, "_setup_logging", lambda verbose: None)
    monkeypatch.setattr(cli, "_speak_standalone", lambda text: pytest.fail("must not play standalone"))
    return requests


def test_read_source_plain_text():
    assert cli._read_source("Hello world") == "Hello world"


def test_read_source_file(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Notes\n\nSome text.")
    assert cli._read_source(str(f)) == "# Notes\n\nSome text."


def test_read_source_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("piped text"))
    assert cli._read_source("-") == "piped text"


def test_read_source_paragraph_too_long_for_a_path():
    text = "word " * 400  # longer than any file name: stat fails, the text is spoken as is
    assert cli._read_source(text) == text


@pytest.mark.parametrize("cmd", ["pause", "stop", "next", "prev", "status", "selection", "clipboard", "quit"])
def test_simple_commands_go_to_the_app(sent, cmd):
    assert cli.main([cmd]) == 0
    assert sent == [{"cmd": cmd}]


@pytest.mark.parametrize(("argv", "req"), [
    (["speed", "1.5"], {"cmd": "speed", "value": 1.5}),
    (["seek", "0.5"], {"cmd": "seek_to", "value": 0.5}),
    (["button"], {"cmd": "button", "value": "toggle"}),
    (["button", "off"], {"cmd": "button", "value": "off"}),
])
def test_commands_with_values(sent, argv, req):
    assert cli.main(argv) == 0
    assert sent == [req]


def test_text_is_spoken_through_the_app(sent):
    assert cli.main(["Read", "this"]) == 0
    assert sent == [{"cmd": "speak", "text": "Read\n\nthis"}]


def test_speak_reads_files(sent, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("From a file.")
    cli.main(["speak", str(f)])
    assert sent == [{"cmd": "speak", "text": "From a file."}]


def test_app_not_running_falls_back_to_standalone(monkeypatch):
    played = []
    monkeypatch.setattr(cli.host.ipc, "send", lambda req, timeout=5.0: None)
    monkeypatch.setattr(cli, "_setup_logging", lambda verbose: None)
    monkeypatch.setattr(cli, "_speak_standalone", lambda text: played.append(text) or 0)
    assert cli.main(["hello"]) == 0
    assert played == ["hello"]
    assert cli.main(["status"]) == 1  # control commands need the app


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS say voice")
def test_provider_flag_forces_standalone_with_that_engine(monkeypatch):
    played = []
    monkeypatch.setattr(cli.host.ipc, "send", lambda req, timeout=5.0: pytest.fail("must not use the app"))
    monkeypatch.setattr(cli, "_setup_logging", lambda verbose: None)
    monkeypatch.setattr(cli, "_speak_standalone", lambda text: played.append((text, os.environ["LYSSNA_PROVIDER"])) or 0)
    monkeypatch.setenv("LYSSNA_PROVIDER", "unset")  # so monkeypatch restores the environment afterwards
    assert cli.main(["-p", "say", "hi"]) == 0
    assert played == [("hi", "say")]


def test_failed_speak_reports_the_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.host.ipc, "send", lambda req, timeout=5.0: {"ok": False, "status": {"error": "Nothing to read"}})
    monkeypatch.setattr(cli, "_setup_logging", lambda verbose: None)
    assert cli.main(["   "]) == 1
    assert "Nothing to read" in capsys.readouterr().err


def test_config_prints_the_path(sent, capsys):
    assert cli.main(["config"]) == 0
    assert capsys.readouterr().out.strip() == str(config.CONFIG_PATH)
    assert sent == []


def test_doctor_and_autostart_route(sent, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "cmd_doctor", lambda: seen.append("doctor") or 0)
    monkeypatch.setattr(cli, "cmd_autostart", lambda enable: seen.append(("autostart", enable)) or 0)
    cli.main(["doctor"])
    cli.main(["autostart"])
    cli.main(["autostart", "off"])
    assert seen == ["doctor", ("autostart", True), ("autostart", False)]
    assert sent == []


def test_provider_help_lists_every_engine(capsys):
    from lyssna.providers import REGISTRY

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert ", ".join(REGISTRY) in help_text
