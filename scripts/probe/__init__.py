"""The Windows probe: a guided check that runs the real Windows calls of lyssna/win on a PC and
writes one report file, plus a self-test that GitHub's Windows machine runs without a person.

Why it exists: the Windows plumbing was written and unit-tested on a Mac with fakes. This is the
first time it meets real Windows, and the owner can only test on his PC a few times, so one run
has to collect everything. Entry point: scripts/probe_win.py."""

PROBE_VERSION = 1
