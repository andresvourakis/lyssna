"""Local-only mode: keep the text being read on this computer by refusing every engine that
sends it to an online service.

Why a separate module: the setting comes from two places with different owners. The user
turns it on from the menu (`local_only` in the config file); a company can also force it on
for everyone with a managed setting `LocalOnly` (a configuration profile on the Mac, Group
Policy on Windows). A forced value wins and the user cannot switch it off from the app.

The managed setting is read by host.policy.read_preference (lyssna/mac/policy.py,
lyssna/win/policy.py). Tests swap `_reader` for a fake, so no test reads the real setting."""

from __future__ import annotations

from collections.abc import Callable

from . import host

MANAGED_KEY = "LocalOnly"

_reader: Callable[[str], object | None] = host.policy.read_preference


def managed_local_only() -> bool:
    """True when an administrator (or a `defaults write`) has turned local-only mode on.
    Only an explicit true counts: anything else leaves the choice to the user."""
    value = _reader(MANAGED_KEY)
    return isinstance(value, int) and value == 1  # bool is an int; a profile may also store 1


def local_only(cfg: dict) -> bool:
    """Is local-only mode on, from either the managed preference or the config file?"""
    return managed_local_only() or bool(cfg.get("local_only", False))


def refusal(label: str) -> str:
    """The message shown when someone asks for an online engine while local-only mode is on."""
    by = " (set by your organization)" if managed_local_only() else ""
    return (f"{label} sends text to an online service, and \"Keep Everything on This {host.DEVICE}\" is on{by}. "
            f"Use a voice on this {host.DEVICE} instead.")
