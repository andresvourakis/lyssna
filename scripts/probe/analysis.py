"""The probe's arithmetic, kept free of Windows and Qt so it is tested on a Mac: screen
coordinates, where the floating button goes, microphone levels, key timings and WAV checks.

Coordinates. Windows reports the mouse, the cursor and UI Automation rectangles in physical
pixels. Qt on Windows (per-monitor DPI aware) places windows in its own units: each screen's
top-left corner keeps its physical position, and distances inside the screen are divided by
that screen's scale factor. A screen here is a dict with Qt's geometry (x, y, width, height)
and its scale factor (dpr)."""

from __future__ import annotations

import io
import math
import struct
import wave

VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LWIN, VK_RWIN = 0x5B, 0x5C
CTRL_KEYS = frozenset({VK_LCONTROL, VK_RCONTROL, 0x11})
WIN_KEYS = frozenset({VK_LWIN, VK_RWIN})

BUTTON_SIZE = 34  # Qt units
BUTTON_GAP = 8  # Qt units between the button and the selection


# ----- screens -----------------------------------------------------------------------------


def physical_rect(screen: dict) -> tuple[float, float, float, float]:
    """The screen's rectangle in physical pixels."""
    dpr = float(screen.get("dpr") or 1.0)
    return (screen["x"], screen["y"], screen["width"] * dpr, screen["height"] * dpr)


def screen_at(x: float, y: float, screens: list[dict]) -> dict | None:
    """The screen holding this physical point, else the nearest one, else None."""
    if not screens:
        return None
    best, best_dist = None, math.inf
    for s in screens:
        sx, sy, sw, sh = physical_rect(s)
        if sx <= x < sx + sw and sy <= y < sy + sh:
            return s
        dx = max(sx - x, 0, x - (sx + sw))
        dy = max(sy - y, 0, y - (sy + sh))
        dist = math.hypot(dx, dy)
        if dist < best_dist:
            best, best_dist = s, dist
    return best


def physical_to_qt(x: float, y: float, screens: list[dict]) -> tuple[float, float, str]:
    """A physical point in Qt units, with the name of the screen it is on."""
    s = screen_at(x, y, screens)
    if s is None:
        return x, y, ""
    dpr = float(s.get("dpr") or 1.0)
    return s["x"] + (x - s["x"]) / dpr, s["y"] + (y - s["y"]) / dpr, s.get("name", "")


def qt_to_physical(x: float, y: float, screen: dict) -> tuple[float, float]:
    dpr = float(screen.get("dpr") or 1.0)
    return screen["x"] + (x - screen["x"]) * dpr, screen["y"] + (y - screen["y"]) * dpr


def button_position(rect: tuple[float, float, float, float] | None, cursor: tuple[float, float] | None,
                    screens: list[dict], size: float = BUTTON_SIZE, gap: float = BUTTON_GAP) -> dict:
    """Where the floating button goes: centred just above the selection rectangle (physical
    pixels) when there is one, else just above the cursor, kept inside the screen. Returns the
    top-left corner in Qt units and in physical pixels, the anchor used and the screen name."""
    if rect is not None and rect[2] > 0 and rect[3] > 0:
        ax, ay, anchor = rect[0] + rect[2] / 2, rect[1], "selection"
    elif cursor is not None:
        ax, ay, anchor = cursor[0], cursor[1], "cursor"
    else:
        return {"anchor": "none", "qt": None, "physical": None, "screen": ""}
    s = screen_at(ax, ay, screens)
    qx, qy, name = physical_to_qt(ax, ay, screens)
    left, top = qx - size / 2, qy - gap - size
    if s is not None:
        left = min(max(left, s["x"]), s["x"] + s["width"] - size)
        top = min(max(top, s["y"]), s["y"] + s["height"] - size)
        px, py = qt_to_physical(left, top, s)
    else:
        px, py = left, top
    return {"anchor": anchor, "qt": [round(left, 1), round(top, 1)], "physical": [round(px), round(py)], "screen": name}


# ----- microphone --------------------------------------------------------------------------

_SAMPLE_FORMATS = {
    # Qt sample format name -> (struct code, bytes, full scale, offset)
    "UInt8": ("B", 1, 128.0, 128.0),
    "Int16": ("h", 2, 32768.0, 0.0),
    "Int32": ("i", 4, 2147483648.0, 0.0),
    "Float": ("f", 4, 1.0, 0.0),
}


def levels(data: bytes, sample_format: str) -> dict:
    """Peak and RMS of interleaved little-endian samples, as fractions of full scale (0..1)."""
    code, width, scale, offset = _SAMPLE_FORMATS.get(sample_format, _SAMPLE_FORMATS["Int16"])
    count = len(data) // width
    if count == 0:
        return {"samples": 0, "peak": 0.0, "rms": 0.0}
    values = struct.unpack(f"<{count}{code}", data[: count * width])
    peak = 0.0
    total = 0.0
    for v in values:
        x = (v - offset) / scale
        a = abs(x)
        if a > peak:
            peak = a
        total += x * x
    return {"samples": count, "peak": round(min(peak, 1.0), 4), "rms": round(math.sqrt(total / count), 4)}


# ----- keys --------------------------------------------------------------------------------


def chord_taps(events: list[tuple[int, bool, float]]) -> list[tuple[float, float]]:
    """(start, end) of every moment both a Ctrl key and a Windows key were held, from
    (virtual key, down, time) events in order."""
    held: set[int] = set()
    taps: list[tuple[float, float]] = []
    start: float | None = None
    for vk, down, t in events:
        if down:
            held.add(vk)
        else:
            held.discard(vk)
        both = bool(held & CTRL_KEYS) and bool(held & WIN_KEYS)
        if both and start is None:
            start = t
        elif not both and start is not None:
            taps.append((start, t))
            start = None
    return taps


def chord_released(events: list[tuple[int, bool, float]]) -> bool:
    """True once Ctrl and the Windows key were held together and every key has been let go."""
    if not chord_taps(events):
        return False
    held: set[int] = set()
    for vk, down, _t in events:
        if down:
            held.add(vk)
        else:
            held.discard(vk)
    return not held


def double_tap(events: list[tuple[int, bool, float]]) -> dict:
    """The first two Ctrl+Windows taps: how long each was held and the gap between them."""
    taps = chord_taps(events)
    out: dict = {"taps_seen": len(taps)}
    if taps:
        out["first_tap_seconds"] = round(taps[0][1] - taps[0][0], 3)
    if len(taps) >= 2:
        out["second_tap_seconds"] = round(taps[1][1] - taps[1][0], 3)
        out["gap_seconds"] = round(taps[1][0] - taps[0][1], 3)
    return out


def describe_events(events: list[tuple[int, bool, float]], swallowed: set[int] | None = None) -> list[str]:
    """Key events as short strings for the report: "0xA2 down +0.000s", times from the first."""
    if not events:
        return []
    t0 = events[0][2]
    swallowed = swallowed or set()
    out = []
    for i, (vk, down, t) in enumerate(events):
        mark = " swallowed" if i in swallowed else ""
        out.append(f"0x{vk:02X} {'down' if down else 'up'} +{t - t0:.3f}s{mark}")
    return out


def altgr_from_scans(scans: dict[str, int]) -> dict:
    """Does the keyboard layout have AltGr? `scans` maps characters to VkKeyScanEx results;
    a character typed with Ctrl and Alt held (bits 2 and 4 of the high byte) needs AltGr."""
    needs = sorted(ch for ch, r in scans.items() if r >= 0 and ((r >> 8) & 0x06) == 0x06)
    return {"has_altgr": bool(needs), "altgr_characters": "".join(needs)}


# ----- audio -------------------------------------------------------------------------------


def wav_info(data: bytes) -> dict:
    """Is this a WAV file with sound in it? Its rate, channels, frames and length."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return {"valid": frames > 0, "rate": rate, "channels": w.getnchannels(), "frames": frames,
                    "seconds": round(frames / rate, 3) if rate else 0.0, "bytes": len(data)}
    except Exception as e:
        return {"valid": False, "error": f"{type(e).__name__}: {e}", "bytes": len(data)}


# ----- speed of speech synthesis -----------------------------------------------------------


def real_time_factor(synthesis_seconds: float, audio_seconds: float) -> float | None:
    """Seconds of work per second of speech: below 1 is faster than real time."""
    if not audio_seconds or audio_seconds <= 0:
        return None
    return round(synthesis_seconds / audio_seconds, 3)


def timing_row(synthesis_seconds: float, audio: dict) -> dict:
    """One timed synthesis for the report: the time, the audio length and the real-time factor.
    `audio` is a wav_info() result."""
    seconds = float(audio.get("seconds") or 0.0)
    return {"synthesis_seconds": round(synthesis_seconds, 3), "audio_seconds": seconds,
            "real_time_factor": real_time_factor(synthesis_seconds, seconds)}


def fastest(rows: dict[str, dict]) -> str | None:
    """The name of the row with the lowest real-time factor, ignoring rows without one."""
    scored = [(r["real_time_factor"], name) for name, r in rows.items() if r.get("real_time_factor") is not None]
    return min(scored)[1] if scored else None


# ----- the processor -----------------------------------------------------------------------

LOGICAL_PROCESSOR_ENTRY = 32  # sizeof(SYSTEM_LOGICAL_PROCESSOR_INFORMATION) on 64-bit Windows
RELATION_PROCESSOR_CORE = 0


def count_cores(raw: bytes, entry_size: int = LOGICAL_PROCESSOR_ENTRY) -> int:
    """Physical cores in a GetLogicalProcessorInformation buffer: the entries whose
    Relationship (a 32-bit value after the 8-byte processor mask) is RelationProcessorCore."""
    count = 0
    for offset in range(0, len(raw) - entry_size + 1, entry_size):
        (relationship,) = struct.unpack_from("<I", raw, offset + 8)
        if relationship == RELATION_PROCESSOR_CORE:
            count += 1
    return count


def avx_features(features: dict) -> list[str]:
    """The AVX-family features numpy says this processor has, from its __cpu_features__ map."""
    return sorted(name for name, present in features.items() if present and name.upper().startswith("AVX"))
