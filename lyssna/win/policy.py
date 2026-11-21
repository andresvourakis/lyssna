r"""The managed local-only setting on Windows.

Why: a company forces local-only mode on for everyone with Group Policy, which writes the
value Software\Policies\Lyssna\LocalOnly (a DWORD) to the registry. The machine-wide value
(HKEY_LOCAL_MACHINE) wins over the per-user one (HKEY_CURRENT_USER), the usual order for
policies. lyssna.policy decides what the value means; this module only reads it.

winreg is imported inside the function, so this module imports on any system and tests put a
fake winreg in sys.modules."""

from __future__ import annotations

import logging

log = logging.getLogger("lyssna.win.policy")

POLICY_KEY = r"Software\Policies\Lyssna"


def read_preference(key: str) -> object | None:
    """The DWORD value named `key` under the Lyssna policy key, machine-wide first and then
    for this user. None when it is not set, is not a DWORD, or cannot be read."""
    try:
        import winreg
    except ImportError as e:
        log.debug("cannot read the %s policy: %s", key, e)
        return None
    for hive, hive_name in ((winreg.HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE"), (winreg.HKEY_CURRENT_USER, "HKEY_CURRENT_USER")):
        try:
            with winreg.OpenKey(hive, POLICY_KEY) as handle:
                value, kind = winreg.QueryValueEx(handle, key)
        except FileNotFoundError:
            continue
        except Exception as e:  # a broken policy must never stop the app from starting
            log.debug("could not read the %s policy in %s: %s", key, hive_name, e)
            continue
        if kind == winreg.REG_DWORD and isinstance(value, int):
            return value
        log.debug("the %s policy in %s is not a DWORD, so it is ignored", key, hive_name)
    return None
