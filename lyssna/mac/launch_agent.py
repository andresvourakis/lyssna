"""Start at login when Lyssna runs from source on a Mac: a LaunchAgent that runs `lyssna app`.

The bundled app uses a login item instead (lyssna/mac/autostart.py), which would register the
wrong bundle from source. `lyssna autostart on|off` uses this; lyssna.host hands it out as
host.launch_agent, and it has no Windows twin (there the command line uses host.autostart)."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config

LABEL = "io.github.andresvourakis.lyssna"
PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"  # tests point this into a temp dir


def set_enabled(enable: bool) -> None:
    """Install and load the LaunchAgent, or unload and remove it. Prints what it did."""
    if enable:
        exe = shutil.which("lyssna") or str(Path(sys.argv[0]).resolve())
        plist = {
            "Label": LABEL,
            "ProgramArguments": [exe, "app"],
            "RunAtLoad": True,
            "KeepAlive": False,
            "StandardOutPath": str(config.LOG_DIR / "launchd.out.log"),
            "StandardErrorPath": str(config.LOG_DIR / "launchd.err.log"),
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        }
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_bytes(plistlib.dumps(plist))
        subprocess.run(["launchctl", "unload", str(PATH)], capture_output=True)
        subprocess.run(["launchctl", "load", str(PATH)], check=False)
        print(f"Installed {PATH} → runs `{exe} app` at login.")
        print("If hotkeys don't work when launched this way, grant Accessibility to the python binary it runs.")
    else:
        subprocess.run(["launchctl", "unload", str(PATH)], capture_output=True)
        PATH.unlink(missing_ok=True)
        print("Autostart removed.")
