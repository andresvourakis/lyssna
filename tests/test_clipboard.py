"""The Cmd+C fallback puts the whole previous clipboard back: every item, every type, and an
empty clipboard stays empty. Mostly a fake pasteboard; one round trip uses a private, uniquely
named pasteboard, never the user's clipboard."""

from AppKit import NSPasteboard

from lyssna import capture


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
        self.cleared = 0
        self.count = 0

    def pasteboardItems(self):
        return list(self.items)

    def changeCount(self):
        return self.count

    def clearContents(self):
        self.cleared += 1
        self.count += 1
        self.items = []

    def writeObjects_(self, items):
        self.items.extend(items)
        return True


RICH = {
    "public.utf8-plain-text": b"hello",
    "public.rtf": b"{\\rtf1 hello}",
    "public.html": b"<b>hello</b>",
}
IMAGE = {"public.png": b"\x89PNG..."}
FILE = {"public.file-url": b"file:///Users/me/a.pdf"}


def test_snapshot_keeps_every_item_and_type():
    pb = FakePasteboard([FakeItem(RICH), FakeItem(IMAGE), FakeItem(FILE)])
    assert capture.snapshot_pasteboard(pb) == [RICH, IMAGE, FILE]


def test_restore_puts_everything_back_after_a_copy():
    pb = FakePasteboard([FakeItem(RICH), FakeItem(IMAGE)])
    saved = capture.snapshot_pasteboard(pb)
    pb.clearContents()
    pb.writeObjects_([FakeItem({"public.utf8-plain-text": b"the selection"})])  # what Cmd+C did
    capture.restore_pasteboard(pb, saved, new_item=FakeItem)
    assert [item.data for item in pb.items] == [RICH, IMAGE]


def test_an_empty_clipboard_is_restored_empty():
    pb = FakePasteboard()
    saved = capture.snapshot_pasteboard(pb)
    assert saved == []
    pb.writeObjects_([FakeItem({"public.utf8-plain-text": b"the selection"})])
    capture.restore_pasteboard(pb, saved, new_item=FakeItem)
    assert pb.items == [] and pb.cleared == 1


def test_items_without_data_are_skipped():
    pb = FakePasteboard()
    capture.restore_pasteboard(pb, [{}, IMAGE], new_item=FakeItem)
    assert [item.data for item in pb.items] == [IMAGE]


def test_round_trip_on_a_private_pasteboard():
    pb = NSPasteboard.pasteboardWithUniqueName()
    try:
        pb.clearContents()
        item = capture._new_item()
        for kind, value in RICH.items():
            item.setData_forType_(value, kind)
        pb.writeObjects_([item])
        saved = capture.snapshot_pasteboard(pb)
        assert len(saved) == 1 and RICH.items() <= saved[0].items()  # macOS may add derived types
        pb.clearContents()
        pb.setString_forType_("the selection", "public.utf8-plain-text")
        capture.restore_pasteboard(pb, saved)
        (restored,) = capture.snapshot_pasteboard(pb)
        assert RICH.items() <= restored.items()
        capture.restore_pasteboard(pb, [])
        assert capture.snapshot_pasteboard(pb) == []
    finally:
        pb.releaseGlobally()
