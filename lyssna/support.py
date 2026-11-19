"""Asking for support, quietly: where the links go and when they show.

Lyssna is free and never interrupts anyone to ask for money. It asks only in places the user
opens themselves: a Support Lyssna row in the menu, a line under the totals in the History
window (once there is something to look back on), and a line at the end of the What's New
window. All three open the website's pay card in its give-only mode.

Every support link or button carries the heart (SYMBOL), in the app as on the website.

SITE_URL is empty until the website has an address. While it is empty every support link is
hidden, so a build made before then asks for nothing and links nowhere. To preview the links
before then, start the app with LYSSNA_SITE_URL set to a copy of the site, for example
`open --env LYSSNA_SITE_URL=http://localhost:8787 /Applications/Lyssna.app`. Kept free of AppKit
so the rules can be tested."""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("lyssna.support")

SITE_URL = ""  # the website, without a trailing slash, for example "https://lyssna.app"
SITE_ENV = "LYSSNA_SITE_URL"  # overrides SITE_URL, to preview the links against a local copy of the site
SYMBOL = "heart.fill"  # the SF Symbol in front of every support link or button
MENU_TITLE = "Support Lyssna"
HISTORY_LINE = "Lyssna is free and made by one person."
HISTORY_ACTION = "Support it"
WHATS_NEW_LINE = "This update was free."
HISTORY_AFTER_WORDS = 1000  # the History line waits until this many words were dictated


def url() -> str:
    """The pay card in its give-only mode, or "" while the website has no address."""
    site = (os.environ.get(SITE_ENV) or SITE_URL).strip().rstrip("/")
    return f"{site}/?support=1" if site else ""


def enabled() -> bool:
    return bool(url())


def in_history(words_total: int) -> bool:
    """Whether the History window shows its support line: only next to totals worth looking at."""
    return enabled() and words_total >= HISTORY_AFTER_WORDS


def _open(target: str) -> None:
    subprocess.run(["open", target], check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def open_support(source: str) -> None:
    """Open the support page in the browser. `source` says which of the three places asked, for
    the log only: nothing about it is sent anywhere."""
    target = url()
    if not target:
        return
    log.info("opening the support page from %s", source)
    _open(target)
