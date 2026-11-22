"""Run the whole comparison: the legacy side and the Qt side, each in its own process, then the
composer and the motion audit.

    uv run --no-sync python -m scripts.compare [--only PATTERN] [--out DIR] [--no-motion]

--only takes a glob or a substring of pair names (player.card*, setup, history.fix). --out is
the output folder (default: a new folder under the system temp folder; never the repository).
Everything the two apps would write goes to a temp folder of its own under DIR/work."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(module: str, *args: str) -> int:
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", module, *args], cwd=ROOT)
    print(f"{module}: exit {proc.returncode} after {time.monotonic() - started:.1f} s")
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.compare", description="Compare the legacy and Qt interfaces.")
    ap.add_argument("--only", help="a glob or substring of pair names")
    ap.add_argument("--out", help="output folder (default: a new temp folder)")
    ap.add_argument("--no-motion", action="store_true", help="skip the motion filmstrips")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="lyssna-compare-"))
    if ROOT in out.resolve().parents or out.resolve() == ROOT:
        print("refusing to write into the repository; pick another --out", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    only = ["--only", args.only] if args.only else []
    motion = [] if args.no_motion else ["--motion", str(out / "motion")]
    codes = [
        _run("scripts.compare.legacy_side", "--out", str(out / "raw" / "legacy"), "--work", str(out / "work" / "legacy"),
             "--menus", *only, *motion),
        _run("scripts.compare.qt_side", "--out", str(out / "raw" / "qt"), "--work", str(out / "work" / "qt"), "--menus",
             *only, *motion),
        _run("scripts.compare.compose", "--out", str(out), *only),
    ]
    if not args.no_motion:
        codes.append(_run("scripts.compare.motion", "--out", str(out), *only))
    print(f"output: {out}\nopen: {out / 'index.html'}")
    return max(codes)


if __name__ == "__main__":
    raise SystemExit(main())
