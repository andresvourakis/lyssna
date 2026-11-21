"""Paste at the cursor, with fakes only: no key is ever posted and the user's clipboard is
never touched (a fake pasteboard, and once a private, uniquely named one). The Inserter is the
engine's (lyssna/dictation/insert.py); the clipboard writes and the text-field question are the
Mac's (lyssna/mac/insert.py)."""

import pytest
import Quartz
from AppKit import NSPasteboard

from lyssna.dictation.insert import Inserter
from lyssna.mac import capture, insert
from lyssna.mac.insert import CONCEALED_TYPE, TRANSIENT_TYPE, text_target

PLAIN = "public.utf8-plain-text"


class FakeItem:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def types(self):
        return list(self.data)

    def dataForType_(self, kind):
        return self.data.get(kind)

    def setData_forType_(self, value, kind):
        self.data[kind] = value
        return True


class FakePasteboard:
    def __init__(self, items=()):
        self.items = list(items)
        self.count = 0

    def pasteboardItems(self):
        return list(self.items)

    def clearContents(self):
        self.count += 1
        self.items = []

    def writeObjects_(self, items):
        self.count += 1
        self.items.extend(items)
        return True

    def changeCount(self):
        return self.count


def contents(pb):
    return capture.snapshot_pasteboard(pb)


@pytest.fixture
def machine():
    """An Inserter wired to fakes; the delayed clipboard restore is collected in state["later"]
    and run by the test. Items are real (detached) NSPasteboardItems on a fake pasteboard."""
    state = {"keys": [], "waited": 0, "later": [], "target": "text"}
    pb = FakePasteboard([FakeItem({PLAIN: b"what was there", "public.rtf": b"{\\rtf1 x}"})])

    def wait():
        state["waited"] += 1

    inserter = Inserter(
        pasteboard=pb,
        focused=lambda: object(),
        target=lambda element: state["target"],
        send_key=lambda key, flags: state["keys"].append((key, flags)),
        wait_for_modifiers=wait,
        later=lambda delay, fn: state["later"].append(fn),
    )
    return inserter, pb, state


def test_paste_puts_text_on_the_clipboard_presses_cmd_v_and_restores(machine):
    inserter, pb, state = machine
    assert inserter.paste("Hello there.") == "pasted"
    (item,) = contents(pb)
    assert item[PLAIN] == b"Hello there."  # exactly the text: no trailing space added
    assert TRANSIENT_TYPE in item and CONCEALED_TYPE in item
    assert state["waited"] == 1
    assert state["keys"] == [(insert.KEY_V, Quartz.kCGEventFlagMaskCommand)]
    (restore,) = state["later"]
    assert restore() is True
    assert contents(pb) == [{PLAIN: b"what was there", "public.rtf": b"{\\rtf1 x}"}]


def test_an_unknown_target_is_pasted_too(machine):
    inserter, _, state = machine
    state["target"] = "maybe"
    assert inserter.paste("hi") == "pasted"
    assert state["keys"]


def test_no_text_target_leaves_the_text_on_the_clipboard(machine):
    inserter, pb, state = machine
    state["target"] = "none"
    assert inserter.paste("Copy me") == "copied"
    assert contents(pb) == [{PLAIN: b"Copy me"}]  # a normal entry the user can paste and keep
    assert state["keys"] == [] and state["later"] == []


def test_restore_is_skipped_when_the_user_copied_something_since(machine):
    inserter, pb, state = machine
    inserter.paste("dictated")
    pb.clearContents()
    pb.writeObjects_([FakeItem({PLAIN: b"copied by the user"})])
    (restore,) = state["later"]
    assert restore() is False
    assert contents(pb) == [{PLAIN: b"copied by the user"}]


def test_an_empty_clipboard_comes_back_empty(machine):
    inserter, pb, state = machine
    pb.items = []
    inserter.paste("x")
    state["later"][0]()
    assert pb.items == []


class Element:
    def __init__(self, role=None, value=None, value_settable=False, range_settable=False):
        self.role, self.value = role, value
        self.settable = {"AXValue": value_settable, "AXSelectedTextRange": range_settable}


@pytest.fixture
def fake_ax(monkeypatch):
    monkeypatch.setattr(capture, "ax_settable", lambda el, name: el.settable.get(name, False))
    monkeypatch.setattr(capture, "ax_text_value", lambda el: el.value if isinstance(el.value, str) else None)
    monkeypatch.setattr(capture, "ax_role", lambda el: el.role)


@pytest.mark.parametrize(
    ("element", "expected"),
    [
        (None, "maybe"),                                                         # nothing exposed (Electron)
        (Element("AXTextField", "abc", value_settable=True), "text"),
        (Element("AXTextArea", "", value_settable=True), "text"),                # an empty field is still a field
        (Element("AXGroup", None, range_settable=True), "text"),                 # a web editor exposing a caret
        (Element("AXWebArea"), "maybe"),                                         # a page that hides its editor
        (Element("AXWindow"), "maybe"),
        (Element(None), "maybe"),
        (Element("AXList"), "none"),                                             # Finder icons, the desktop
        (Element("AXButton"), "none"),
        (Element("AXCheckBox", 1, value_settable=True), "none"),                 # settable, but not text
        (Element("AXStaticText", "label"), "none"),
    ],
)
def test_text_target(fake_ax, element, expected):
    assert text_target(element) == expected


def test_the_clipboard_entry_is_marked_for_clipboard_managers_on_a_private_pasteboard():
    pb = NSPasteboard.pasteboardWithUniqueName()
    try:
        insert.put_text(pb, "secret words", transient=True)
        types = {str(t) for t in pb.types()}
        assert {TRANSIENT_TYPE, CONCEALED_TYPE} <= types
        assert pb.stringForType_("public.utf8-plain-text") == "secret words"
        insert.put_text(pb, "keep me", transient=False)
        assert TRANSIENT_TYPE not in {str(t) for t in pb.types()}
    finally:
        pb.releaseGlobally()
