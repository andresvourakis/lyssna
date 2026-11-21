"""PyInstaller entry point for lyssna-cli.exe, the terminal command of the Windows app.

It is the same command as `lyssna` (lyssna.cli.main), with one difference: with no arguments,
or with `app`, it does not run the interface inside this console window. It starts Lyssna.exe
from its own folder as a separate process and returns at once, so closing the terminal does
not end Lyssna."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HELP_FLAGS = {"-h", "--help"}
VALUE_FLAGS = {"-p", "--provider"}  # options followed by a value, which is not a command


def positional(argv: list[str]) -> list[str]:
    """The arguments that are commands or text, the same way lyssna.cli reads them: options
    and the value after -p are left out, and a lone "-" (read standard input) counts."""
    out, skip = [], False
    for arg in argv:
        if skip:
            skip = False
        elif arg in VALUE_FLAGS:
            skip = True
        elif arg == "-" or not arg.startswith("-"):
            out.append(arg)
    return out


def starts_app(argv: list[str]) -> bool:
    """True when this command line would start the interface."""
    if HELP_FLAGS & set(argv):
        return False
    return positional(argv) in ([], ["app"])


def start_app(verbose: bool) -> int:
    exe = Path(sys.executable).with_name("Lyssna.exe")
    if not exe.exists():
        print(f"Could not find {exe}. Reinstall Lyssna.", file=sys.stderr)
        return 1
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    subprocess.Popen([str(exe), *(["-v"] if verbose else [])], cwd=str(exe.parent), creationflags=flags,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Starting Lyssna. Look for its icon by the clock.")
    return 0


def run(argv: list[str]) -> int:
    if starts_app(argv):
        return start_app(verbose="-v" in argv or "--verbose" in argv)
    from lyssna.cli import main

    return main(argv)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
