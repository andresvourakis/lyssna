"""The managed settings on the Mac: a company forces local-only mode on for everyone with a
configuration profile that sets `LocalOnly` in our defaults domain. lyssna.policy decides what
the value means; this module only reads it.

The preference is read through CFPreferences, which sees profile (managed) values as well as
`defaults write` ones. CoreFoundation loads inside the function, so this module imports on any
system."""

from __future__ import annotations

import logging

log = logging.getLogger("lyssna.mac.policy")

DOMAIN = "io.github.andresvourakis.lyssna"


def read_preference(key: str) -> object | None:
    """The value of `key` in our defaults domain, or None when it is not set."""
    try:
        from CoreFoundation import CFPreferencesCopyAppValue

        return CFPreferencesCopyAppValue(key, DOMAIN)
    except Exception as e:  # a broken preference must never stop the app from starting
        log.warning("could not read the %s preference: %s", key, e)
        return None
