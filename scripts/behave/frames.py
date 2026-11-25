"""Frames of a screen region with their times, and what changed between them.

Capture: repeated CGWindowListCreateImage of the region (everything on screen there, without the
cursor), from two threads so one call's 12 to 13 ms is overlapped by the next. Measured on this
Mac (M3 Pro, macOS 26, 120 Hz display): about 80 frames a second, never more than about 15 ms
apart. CGDisplayStream would give every display refresh but crashes under pyobjc here, and
ScreenCaptureKit has no pyobjc wrapper installed. A frame's time is the middle of its call, so
any time read from frames is good to about +-7 ms.

The analysis functions below take plain numpy arrays and are tested without a screen."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import numpy as np

PIXEL_DELTA = 24      # a channel must move by more than this (of 255) for its pixel to count as changed
CHANGED_PIXELS = 12   # a frame differs from another when more pixels than this changed


@dataclass
class Frame:
    t: float            # time.time() at the middle of the capture call
    pixels: np.ndarray  # height x width x 4 (BGRA as the window server gives it), uint8


@dataclass
class Recording:
    rect: tuple[float, float, float, float]  # top-left global points
    frames: list[Frame] = field(default_factory=list)

    def rate(self) -> float:
        if len(self.frames) < 2:
            return 0.0
        return (len(self.frames) - 1) / (self.frames[-1].t - self.frames[0].t)

    def max_gap(self) -> float:
        ts = [f.t for f in self.frames]
        return max((b - a for a, b in pairwise(ts)), default=0.0)


def grab(rect: tuple[float, float, float, float]) -> np.ndarray | None:
    import Quartz

    x, y, w, h = rect
    img = Quartz.CGWindowListCreateImage(((x, y), (w, h)), Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID,
                                         Quartz.kCGWindowImageNominalResolution)
    if img is None:
        return None
    width, height = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
    row = Quartz.CGImageGetBytesPerRow(img)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    arr = np.frombuffer(bytes(data), dtype=np.uint8).reshape(height, row)[:, : width * 4]
    return arr.reshape(height, width, 4).copy()


class Recorder:
    """Records `rect` from start() until stop(), on background threads."""

    def __init__(self, rect: tuple[float, float, float, float], threads: int = 2):
        self.rec = Recording(rect)
        self._threads = threads
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._lock = threading.Lock()

    def start(self) -> Recorder:
        for i in range(self._threads):
            w = threading.Thread(target=self._run, daemon=True, name=f"behave-capture-{i}")
            self._workers.append(w)
            w.start()
            time.sleep(0.006)  # staggered, so the calls overlap rather than coincide
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            px = grab(self.rec.rect)
            t1 = time.time()
            if px is not None:
                with self._lock:
                    self.rec.frames.append(Frame((t0 + t1) / 2, px))

    def stop(self) -> Recording:
        self._stop.set()
        for w in self._workers:
            w.join(timeout=2.0)
        self.rec.frames.sort(key=lambda f: f.t)
        return self.rec


# -- analysis ----------------------------------------------------------------------------------


def changed(a: np.ndarray, b: np.ndarray) -> int:
    """How many pixels differ by more than PIXEL_DELTA in some channel."""
    if a.shape != b.shape:
        return a.shape[0] * a.shape[1]
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return int((d > PIXEL_DELTA).sum())


def first_change(frames: list[Frame], after: float, baseline: np.ndarray, threshold: int = CHANGED_PIXELS) -> Frame | None:
    """The first frame captured after `after` that differs from `baseline`."""
    for f in frames:
        if f.t >= after and changed(f.pixels, baseline) > threshold:
            return f
    return None


def settled(frames: list[Frame], after: float, final: np.ndarray, threshold: int = CHANGED_PIXELS,
            until: float | None = None) -> Frame | None:
    """The first frame after `after` from which every later frame (up to `until`) looks like `final`."""
    candidate = None
    for f in frames:
        if f.t < after:
            continue
        if until is not None and f.t > until:
            break
        if changed(f.pixels, final) <= threshold:
            if candidate is None:
                candidate = f
        else:
            candidate = None
    return candidate


def steps(frames: list[Frame], start: float, end: float, threshold: int = CHANGED_PIXELS) -> list[float]:
    """The times in [start, end] at which the picture moved on from the frame before: what the
    eye sees as the animation's frames."""
    out = []
    window = [f for f in frames if start - 0.03 <= f.t <= end]
    for prev, f in pairwise(window):
        if f.t >= start and changed(f.pixels, prev.pixels) > threshold:
            out.append(f.t)
    return out


def motion(frames: list[Frame], cause: float, baseline: np.ndarray, final: np.ndarray, until: float | None = None) -> dict:
    """What followed `cause` (the moment the cursor crossed in or out): when the picture first
    changed, when it settled on `final` (looking no further than `until`), and how smoothly it
    got there."""
    first = first_change(frames, cause, baseline)
    if first is None or (until is not None and first.t > until):
        return {"first_change_ms": None}
    done = settled(frames, first.t, final, until=until)
    end = done.t if done else frames[-1].t
    moved = steps(frames, first.t, end)
    gaps = [b - a for a, b in pairwise(moved)]
    sampled = [f for f in frames if first.t <= f.t <= end]
    return {
        "first_change_ms": round((first.t - cause) * 1000, 1),
        "settled_ms": round((done.t - cause) * 1000, 1) if done else None,
        "animation_ms": round((end - first.t) * 1000, 1) if done else None,
        "frames_sampled": len(sampled),
        "frames_moved": len(moved),  # the first change is a move too
        "largest_step_gap_ms": round(max(gaps) * 1000, 1) if gaps else None,
        "repeated_frames": max(0, len(sampled) - len(moved)),
    }


def save_png(pixels: np.ndarray, path: Path) -> None:
    """BGRA pixels to a PNG file, through AppKit (no PIL here)."""
    import AppKit
    import Quartz
    from Foundation import NSData

    h, w = pixels.shape[:2]
    raw = np.ascontiguousarray(pixels).tobytes()
    provider = Quartz.CGDataProviderCreateWithCFData(NSData.dataWithBytes_length_(raw, len(raw)))
    info = Quartz.kCGImageAlphaPremultipliedFirst | Quartz.kCGBitmapByteOrder32Little  # BGRA in memory
    img = Quartz.CGImageCreate(w, h, 8, 32, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(), info, provider, None, False,
                               Quartz.kCGRenderingIntentDefault)
    rep = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(img)
    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    data.writeToFile_atomically_(str(path), True)


def filmstrip(frames: list[Frame], start: float, end: float, path: Path, limit: int = 40) -> int:
    """The frames between start and end side by side in one PNG, at most `limit` of them."""
    picked = [f for f in frames if start <= f.t <= end]
    if not picked:
        return 0
    if len(picked) > limit:
        idx = np.linspace(0, len(picked) - 1, limit).round().astype(int)
        picked = [picked[i] for i in idx]
    h = max(f.pixels.shape[0] for f in picked)
    gap = np.full((h, 2, 4), (0, 0, 255, 255), dtype=np.uint8)
    parts = []
    for f in picked:
        p = f.pixels
        if p.shape[0] < h:
            p = np.vstack([p, np.zeros((h - p.shape[0], p.shape[1], 4), dtype=np.uint8)])
        parts += [p, gap]
    save_png(np.hstack(parts[:-1]), path)
    return len(picked)
