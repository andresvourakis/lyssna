"""The Mac's processor: how many physical cores it has, for the thread count of the local
cleanup model when it runs on the CPU (lyssna/dictation/cleaners/s1mini.py). Apple silicon runs
that model with Metal and never asks; this twin exists so the engine asks the same question on
both systems.

The count comes from the hw.physicalcpu sysctl through libc, so nothing is started and no
framework loads."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger("lyssna.mac.cpu")


def physical_cores() -> int | None:
    """The number of physical cores, or None when the system cannot say."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if libc.sysctlbyname(b"hw.physicalcpu", ctypes.byref(value), ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            raise OSError(ctypes.get_errno(), "sysctlbyname failed")
        return value.value if value.value > 0 else None
    except Exception as e:  # the caller has a fallback, so a failure here is only logged
        log.debug("could not read the physical core count: %s", e)
        return None
