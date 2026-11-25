"""Which apps cover the play button with a pop-up of their own (Edge's selection toolbar), and how
long the button waits for that pop-up there before it shows. Shared and pure, so the rules can be
tested without timers; lyssna/ui/selection_button.py does the waiting.

An app is learned the first time a pop-up is found over the button in it. From then on the button
waits there for the pop-up (up to the app's deadline) and shows once, clear of it. The deadline
starts at FIRST_WAIT and then follows the slowest arrival measured in that app, plus MARGIN, never
past MAX_WAIT. After FORGET_AFTER presentations in a row with no pop-up at all the app is
forgotten, so the button shows at once there again. Only app names (executable names) and
numbers are kept, in memory."""

from __future__ import annotations

from dataclasses import dataclass

FIRST_WAIT = 0.25   # seconds, before any arrival is measured: long enough for a toolbar that opens right after mouse-up
MARGIN = 0.05       # added to the slowest arrival measured, so a slightly slower pop-up is still waited for
MAX_WAIT = 0.4      # never longer than this: past it the wait itself would be noticed
FORGET_AFTER = 3    # presentations in a row with no pop-up before an app shows the button at once again
POLL = 0.025        # how often the button looks for the pop-up while it waits


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
