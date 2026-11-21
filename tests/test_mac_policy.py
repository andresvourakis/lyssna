"""The managed local-only setting on the Mac: read from our defaults domain through
CFPreferences (lyssna/mac/policy.py). lyssna.policy decides what the value means."""

import sys

import pytest

from lyssna.mac import policy as mac_policy


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreFoundation preferences")
def test_real_reader_reads_our_domain(monkeypatch):
    seen = []
    import CoreFoundation

    monkeypatch.setattr(CoreFoundation, "CFPreferencesCopyAppValue", lambda key, domain: seen.append((key, domain)))
    assert mac_policy.read_preference("LocalOnly") is None
    assert seen == [("LocalOnly", "io.github.andresvourakis.lyssna")]

