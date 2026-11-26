"""What's New: a short note per version, offered in the menu after an update.

Nothing pops up. After an update the menu gains a "What's New in 0.3.0…" row, which stays until
it is opened once. A first install never shows it: everything is new then, and the setup window
is already open.

NOTES holds the highlights for each version, a few short lines each, written for people and
not copied from the changelog. Every version needs an entry (a test checks the current one);
an empty one means there is nothing worth announcing and no row is offered.

The version whose note was last dealt with is kept in STATE_DIR/whats_new_seen. Kept free of
AppKit so the rules can be tested."""

from __future__ import annotations

import logging

from . import config

log = logging.getLogger("lyssna.whatsnew")

NOTES: dict[str, tuple[str, ...]] = {
    "0.4.0": (
        "The free Kokoro voice for read-aloud sounds cleaner. Lyssna downloads the new voice file once, in the background.",
        "The menu has a Support Lyssna row. It opens the support page on the website.",
        "Lyssna is now available for Windows as well, for read-aloud and dictation.",
    ),
    "0.3.2": (
        "Retry a dictation that failed, from the message or from History. No need to speak again.",
        "Paste Last Dictation (⌃⌥V) pastes your latest dictation again, in case it landed in the wrong place.",
        "Messages now appear next to the wave when it sits on a side of the screen.",
    ),
    "0.3.1": (
        "The full player moves freely when you drag it. Use its arrow button to tuck it into the edge.",
    ),
    "0.3.0": (
        "Dictation. Hold fn and speak, and Lyssna types what you said, cleaned up, wherever your cursor is.",
        "A dictionary that learns. Correct a name once and Lyssna spells it right next time.",
        "A History window with what you dictated and how much.",
        "A free voice that runs on your Mac is now the default. No account, no key.",
    ),
}


def notes(version: str) -> tuple[str, ...]:
    return NOTES.get(version, ())


def menu_title(version: str) -> str:
    return f"What's New in {version}…"


def window_title(version: str) -> str:
    return f"What's new in Lyssna {version}"


def pending(current: str, seen: str | None, existing_install: bool) -> bool:
    """Whether to offer the note for `current`. `seen` is the version last dealt with, None when
    nothing was recorded: a first install, or an install from before notes existed (which is an
    update, and `existing_install` tells the two apart)."""
    if not notes(current) or seen == current:
        return False
    return existing_install if seen is None else True


def _path():
    return config.STATE_DIR / "whats_new_seen"


def read_seen() -> str | None:
    try:
        return _path().read_text().strip() or None
    except OSError:
        return None


def mark_seen(version: str) -> None:
    try:
        _path().write_text(version)
    except OSError:
        log.warning("could not record the What's New note as seen", exc_info=True)


def check(current: str, existing_install: bool) -> bool:
    """Run once at launch: is there a note to offer? When there is none, the version is
    recorded right away, so a later update is recognised as one."""
    seen = read_seen()
    offer = pending(current, seen, existing_install)
    if not offer and seen != current:
        mark_seen(current)
    return offer
