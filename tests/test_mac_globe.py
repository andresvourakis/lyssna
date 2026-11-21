"""The macOS "Press Globe key to" setting, read through a fake `defaults read`
(lyssna/mac/globe.py): nothing here reads the real setting."""

import pytest

from lyssna.mac import globe


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", "nothing"), ("1", "input_source"), ("2", "emoji"), ("3", "dictation"), ("", "default"), (None, "unknown"),
     ("7", "unknown"), ("garbage", "unknown")],
)
def test_globe_key_action(raw, expected):
    seen = []

    def read(domain, key):
        seen.append((domain, key))
        return raw

    assert globe.globe_key_action(read) == expected
    assert seen == [("com.apple.HIToolbox", "AppleFnUsageType")]
