"""The engine's paste flow (lyssna/dictation/insert.py) against a fake system twin, so it runs on
any computer: the order of the five operations, the "copied" outcome, the delayed restore,
and that the twin is looked up only when a paste happens."""

import pytest

from lyssna import host
from lyssna.dictation import insert
from lyssna.dictation.insert import Inserter


class FakeTwin:
    """Records every operation in order; put_text hands out increasing tokens."""

    def __init__(self, target="text"):
        self.target = target
        self.calls = []
        self.token = 0

    def text_target(self):
        self.calls.append("text_target")
        return self.target

    def put_text(self, text, transient):
        self.token += 1
        self.calls.append(("put_text", text, transient))
        return self.token

    def snapshot(self):
        self.calls.append("snapshot")
        return "saved"

    def restore_unless_changed(self, saved, token):
        self.calls.append(("restore", saved, token))
        return True

    def paste(self):
        self.calls.append("paste")


@pytest.fixture
def twin():
    return FakeTwin()


def make(twin):
    later = []
    return Inserter(system=twin, later=lambda delay, fn: later.append((delay, fn))), later


@pytest.mark.parametrize("target", ["text", "maybe"])
def test_a_text_field_or_an_unknown_one_is_pasted_into(twin, target):
    twin.target = target
    inserter, later = make(twin)
    assert inserter("Hello there.") == "pasted"
    assert twin.calls == ["text_target", "snapshot", ("put_text", "Hello there.", True), "paste"]
    [(delay, restore)] = later
    assert delay == insert.RESTORE_DELAY
    assert restore() is True
    assert twin.calls[-1] == ("restore", "saved", 1)  # the saved clipboard, against our write's token


def test_no_text_field_leaves_a_normal_clipboard_entry_and_does_not_paste(twin):
    twin.target = "none"
    inserter, later = make(twin)
    assert inserter.paste("Copy me") == "copied"
    assert twin.calls == ["text_target", ("put_text", "Copy me", False)]
    assert later == []


def test_a_failed_clipboard_write_does_not_paste(twin):
    def fail(text, transient):
        raise OSError("clipboard busy")

    twin.put_text = fail
    inserter, later = make(twin)
    with pytest.raises(OSError):
        inserter.paste("x")
    assert "paste" not in twin.calls and later == []


def test_the_system_twin_is_looked_up_at_paste_time(monkeypatch, twin):
    inserter, _ = make(None)
    inserter.later = lambda delay, fn: None
    monkeypatch.setattr(host, "insert", twin, raising=False)
    assert inserter.paste("hi") == "pasted"
    assert "paste" in twin.calls
