"""Which apps cover the play button with a pop-up of their own (Edge's selection toolbar), and how
long the button waits for that pop-up there before it shows. Shared and pure, so the rules can be
tested without timers; lyssna/ui/selection_button.py does the waiting.

An app is learned the first time a pop-up is found over the button in it. From then on the button
waits there for the pop-up (up to the app's deadline) and shows once, clear of it. The deadline
starts at FIRST_WAIT and then follows the slowest arrival measured in that app, plus MARGIN, never
past MAX_WAIT. After FORGET_AFTER presentations in a row with no pop-up at all the app is
forgotten, so the button shows at once there again. Only app names (executable names) and
numbers are kept. export() and load() turn them into plain data and back, so the button can keep
them in a small file between launches; load() takes anything and keeps only what fits the rules."""

from __future__ import annotations

import math
from dataclasses import dataclass

FIRST_WAIT = 0.25   # seconds, before any arrival is measured: long enough for a toolbar that opens right after mouse-up
MARGIN = 0.05       # added to the slowest arrival measured, so a slightly slower pop-up is still waited for
MAX_WAIT = 0.4      # never longer than this: past it the wait itself would be noticed
FORGET_AFTER = 3    # presentations in a row with no pop-up before an app shows the button at once again
POLL = 0.025        # how often the button looks for the pop-up while it waits
MAX_APPS = 50       # apps kept between launches; past it the ones learned longest ago go first
VERSION = 1         # the shape export() gives
MAX_NAME = 260      # an app name longer than this is not an executable name, so it is not loaded


@dataclass
class _App:
    slowest: float | None = None  # the slowest pop-up arrival measured here, seconds after the selection
    misses: int = 0               # presentations in a row with no pop-up


class PopupMemory:
    """The learned apps, by name."""

    def __init__(self) -> None:
        self._apps: dict[str, _App] = {}

    def knows(self, app: str | None) -> bool:
        """Does the button wait for a pop-up in `app`?"""
        return app is not None and app in self._apps

    def deadline(self, app: str) -> float:
        """How long the button waits for a pop-up in `app`, in seconds."""
        known = self._apps.get(app)
        if known is None or known.slowest is None:
            return FIRST_WAIT
        return min(known.slowest + MARGIN, MAX_WAIT)

    def covered(self, app: str, arrival: float | None = None) -> bool:
        """A pop-up was found over the button in `app`, `arrival` seconds after the selection
        (None when not measured). The first time, the app is learned and the arrival is not
        kept: it came from the checks after showing, which only bound it from above. Afterwards
        a slower arrival raises the deadline. Either way the run of misses starts again.
        Returns whether the app was learned just now."""
        known = self._apps.get(app)
        if known is None:
            self._apps[app] = _App()
            return True
        if arrival is not None and (known.slowest is None or arrival > known.slowest):
            known.slowest = arrival
        known.misses = 0
        return False

    def missed(self, app: str) -> bool:
        """A presentation in `app` found no pop-up. Returns whether the app was forgotten."""
        known = self._apps.get(app)
        if known is None:
            return False
        known.misses += 1
        if known.misses >= FORGET_AFTER:
            del self._apps[app]
            return True
        return False

    def export(self) -> dict:
        """The learned apps as plain data for a JSON file: per app the slowest arrival measured
        (seconds, or None) and the run of misses. At most MAX_APPS, the most recently learned."""
        apps = list(self._apps.items())[-MAX_APPS:]
        return {"version": VERSION, "apps": {
            name: {"slowest": None if a.slowest is None else round(min(a.slowest, MAX_WAIT), 3), "misses": a.misses}
            for name, a in apps}}

    def load(self, state) -> None:
        """Replace the learned apps with those in `state`, as export() gives it. Anything that
        does not fit is left out: a wrong shape means no apps, an app with a wrong entry is
        skipped, numbers out of range are brought within the limits, unknown keys are ignored
        and only the last MAX_APPS apps are kept."""
        apps: dict[str, _App] = {}
        entries = state.get("apps") if isinstance(state, dict) else None
        if isinstance(entries, dict):
            for name, entry in list(entries.items())[-MAX_APPS:]:
                if not isinstance(name, str) or not name or len(name) > MAX_NAME or not isinstance(entry, dict):
                    continue
                apps[name] = _App(slowest=_seconds(entry.get("slowest")), misses=_misses(entry.get("misses")))
        self._apps = apps


def _seconds(value) -> float | None:
    """A stored arrival, within 0 and MAX_WAIT; None when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return min(max(float(value), 0.0), MAX_WAIT)


def _misses(value) -> int:
    """A stored run of misses, within 0 and one short of forgetting the app."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(max(value, 0), FORGET_AFTER - 1)
