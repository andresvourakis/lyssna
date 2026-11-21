r"""The managed local-only setting on Windows.

The real version will read the registry policy value Software\Policies\Lyssna\LocalOnly
(through winreg), which an administrator sets with Group Policy. Not built yet: nothing is
read, so no setting is ever forced."""

from __future__ import annotations


def read_preference(key: str) -> object | None:
    """The value of `key` under the Lyssna policy key, or None when it is not set."""
    return None
