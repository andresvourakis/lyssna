"""The PC's processor: how many physical cores it has, for the thread count of the local cleanup
model on the CPU (lyssna/dictation/cleaners/s1mini.py).

Why physical cores and not os.cpu_count(): os.cpu_count() counts logical processors, which on
most PCs is twice the cores, and running llama.cpp with a thread on every logical processor is
slower, not faster. GetLogicalProcessorInformationEx with RelationProcessorCore returns one
entry per physical core across every processor group, so counting the entries is the answer.

ctypes loads kernel32 inside the function, so this module imports on any system; elsewhere the
call fails and physical_cores() returns None."""

from __future__ import annotations

import logging
import struct

log = logging.getLogger("lyssna.win.cpu")

RELATION_PROCESSOR_CORE = 0
ERROR_INSUFFICIENT_BUFFER = 122


def count_cores(raw: bytes) -> int:
    """The physical cores in a GetLogicalProcessorInformationEx buffer. Each entry starts with
    its Relationship and its Size (two 32-bit values), and Size is where the next one begins."""
    count, offset = 0, 0
    while offset + 8 <= len(raw):
        relationship, size = struct.unpack_from("<II", raw, offset)
        if size == 0:
            break
        if relationship == RELATION_PROCESSOR_CORE:
            count += 1
        offset += size
    return count


def physical_cores() -> int | None:
    """The number of physical cores, or None when the system cannot say."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # Windows only
        fn = kernel32.GetLogicalProcessorInformationEx
        fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        fn.restype = wintypes.BOOL
        size = wintypes.DWORD(0)
        fn(RELATION_PROCESSOR_CORE, None, ctypes.byref(size))  # fails on purpose and reports the size it needs
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        if error != ERROR_INSUFFICIENT_BUFFER or not size.value:
            raise OSError(f"GetLogicalProcessorInformationEx did not report a size (error {error})")
        buf = ctypes.create_string_buffer(size.value)
        if not fn(RELATION_PROCESSOR_CORE, buf, ctypes.byref(size)):
            raise OSError(f"GetLogicalProcessorInformationEx failed with error {ctypes.get_last_error()}")  # type: ignore[attr-defined]
        cores = count_cores(buf.raw[: size.value])
        return cores if cores > 0 else None
    except Exception as e:  # the caller has a fallback, so a failure here is only logged
        log.debug("could not read the physical core count: %s", e)
        return None
