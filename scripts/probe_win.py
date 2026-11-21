"""The Windows probe: a guided five-to-eight-minute check of the real Windows calls behind
Lyssna, ending in one report file, and a self-test for GitHub's Windows machine.

  python scripts/probe_win.py                  the guided check (Windows)
  python scripts/probe_win.py --self-test      everything that needs no person; prints the report
  python scripts/probe_win.py --preview        the screens on a Mac, with made-up results
  python scripts/probe_win.py --screenshots D  save every screen of the preview to folder D

Built into LyssnaProbe.exe by lyssna-probe.spec. It never calls a paid voice; the only
download is the free Lyssna voice, and only when the person says yes (or in the self-test).
The code lives in scripts/probe/."""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent))  # the lyssna package
    sys.path.insert(0, str(here))  # the probe package

from probe.main import main

if __name__ == "__main__":
    sys.exit(main())
