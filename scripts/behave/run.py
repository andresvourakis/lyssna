"""Run the live behaviour comparison: each scenario on the original Mac interface (legacy) and on
the shared Qt one (new), each in a fresh app on an isolated profile, then the side-by-side report.

    uv run --no-sync python -m scripts.behave [SCENARIO ...] [--out DIR] [--sides legacy,new] [--trials N]

With no scenario named, all run. The mouse moves and clicks by itself during the run, on the test
app's own panels only; keys are sent only to the test app. The installed Lyssna is quit first and
started again at the end, the sound is muted and restored, and the Mac is kept awake (machine.py).
Output (default: a new temp folder; never the repository): results.json, report.md, and per
scenario and interface the frames, filmstrips, Accessibility dumps and the app's own log."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import edge, hover, scenarios
from .live import INTERFACES, ROOT, LiveApp
from .machine import Blocked, Machine, screen_state
from .report import write_report

log = logging.getLogger("behave.run")


def _hover(app: LiveApp, out: Path, trials: int) -> dict:
    if not hover.start_reading(app, pause=True):
        return {"error": "the reading did not start"}
    glide = hover.measure(app, out, "right", trials=trials, mode="glide")
    jump = hover.measure(app, out, "right", trials=max(3, trials // 2), mode="jump")
    return {"glide": glide["summary"], "jump": jump["summary"], "trials": {"glide": glide["trials"], "jump": jump["trials"]}}


def _edge(app: LiveApp, out: Path, trials: int) -> dict:
    if not hover.start_reading(app, pause=False):
        return {"error": "the reading did not start"}
    return {"middle": edge.measure(app, out, at="middle"), "end": edge.measure(app, out, at="end")}


# name -> (LiveApp settings, function(app, out, trials))
SCENARIOS: dict[str, tuple[dict, object]] = {
    "hover": ({"dock": "right"}, _hover),
    "edge": ({"dock": "right"}, _edge),
    "rest": ({"dock": "", "rest_dock": "right"}, lambda app, out, n: scenarios.rest_menu(app, out)),
    "history_item": ({"dock": "", "rest_dock": "right"}, lambda app, out, n: scenarios.history_from_menu(app, out)),
    "pill": ({"dock": "right"}, lambda app, out, n: scenarios.pill_buttons(app, out)),
    "drag": ({"dock": "right"}, lambda app, out, n: scenarios.drag_dock(app, out)),
    "autohide": ({"dock": "", "rest_dock": "right", "auto_hide": True}, lambda app, out, n: scenarios.auto_hide(app, out)),
    "dictation": ({"dock": "", "rest_dock": "right"}, lambda app, out, n: scenarios.dictation(app, out)),
    "menubar": ({"dock": ""}, lambda app, out, n: scenarios.menubar(app, out)),
    "windows": ({"dock": ""}, lambda app, out, n: scenarios.windows(app, out)),
    "whats_new": ({"dock": "", "seen": "0.1.0"}, lambda app, out, n: scenarios.windows(app, out)),
    "selection": ({"dock": "right"}, None),  # needs the machine's cleanups; see main()
    "lifecycle": ({"dock": ""}, lambda app, out, n: scenarios.lifecycle(app, out)),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.behave", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="*", help=f"any of: {', '.join(SCENARIOS)} (default: all)")
    ap.add_argument("--out", help="output folder (default: a new temp folder)")
    ap.add_argument("--sides", default=",".join(INTERFACES), help="legacy,new (default both)")
    ap.add_argument("--trials", type=int, default=6, help="hover trials per interface")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    names = args.scenarios or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        ap.error(f"unknown scenario {', '.join(unknown)}")
    sides = [s for s in args.sides.split(",") if s]
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="lyssna-behave-"))
    if ROOT in out.resolve().parents or out.resolve() == ROOT:
        print("refusing to write into the repository; pick another --out", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.json"
    results: dict = json.loads(results_path.read_text()) if results_path.exists() else {}
    stopped = None
    try:
        with Machine(out) as machine:
            for name in names:
                settings, fn = SCENARIOS[name]
                for side in sides:
                    reason = screen_state()
                    if reason:
                        raise Blocked(reason)
                    folder = out / name / side
                    folder.mkdir(parents=True, exist_ok=True)
                    app = LiveApp(side, folder, **settings)
                    machine.cleanups.append(app.close)
                    started = time.time()
                    try:
                        app.start()
                        if name == "selection":
                            facts = scenarios.selection(app, folder, machine.cleanups.append)
                        else:
                            facts = fn(app, folder, args.trials)
                    except (Blocked, KeyboardInterrupt):
                        raise
                    except Exception as e:
                        facts = {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
                    finally:
                        app.close()
                        machine.cleanups.remove(app.close)
                    facts["_seconds"] = round(time.time() - started, 1)
                    facts["_log_errors"] = [m for _t, level, _n, m in app.log_lines(started) if level in ("ERROR", "CRITICAL")][:20]
                    results.setdefault(name, {})[side] = facts
                    results_path.write_text(json.dumps(results, indent=1, default=str))
                    print(f"{name} / {side}: {'error: ' + facts['error'] if 'error' in facts else 'done'}", flush=True)
            notes = machine.notes
    except Blocked as e:
        stopped = f"stopped: {e}"
        notes = []
    except KeyboardInterrupt:
        stopped = "stopped by an interrupt"
        notes = []
    results_path.write_text(json.dumps(results, indent=1, default=str))
    write_report(out, results, notes=notes, stopped=stopped)
    print(f"{stopped or 'finished'}\nreport: {out / 'report.md'}")
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
