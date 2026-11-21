"""The Windows player (lyssna/win/audio.py). The state logic is checked with a fake clock and no
Qt; one test plays a quiet tone through Qt when PySide6 and a sound device are there."""

import io
import math
import os
import struct
import time
import wave

import pytest

from lyssna.providers.base import Audio, ProviderError
from lyssna.win import audio
from lyssna.win.audio import PlaybackState


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += seconds


def loaded(rate=1.0, duration=10.0):
    clock = Clock()
    state = PlaybackState(rate=rate, clock=clock)
    state.loaded(duration)
    return state, clock


def test_play_sets_playing_at_once():
    state, _ = loaded()
    assert not state.is_playing()
    assert state.play()
    assert state.is_playing()


def test_pause_keeps_the_position():
    state, clock = loaded()
    state.play()
    clock.tick(2.0)
    assert state.pause()
    assert not state.is_playing()
    clock.tick(5.0)
    assert state.current() == pytest.approx(2.0)
    state.play()
    clock.tick(1.0)
    assert state.current() == pytest.approx(3.0)


def test_stop_ends_the_player_for_good():
    state, clock = loaded()
    state.play()
    clock.tick(1.0)
    assert state.stop()
    assert not state.is_playing()
    assert state.current() == pytest.approx(1.0)
    assert not state.stop()  # only the first stop releases
    assert not state.play()
    assert not state.is_playing()
    assert not state.seek(3.0)


def test_end_of_media_clears_playing_and_sits_at_the_end():
    state, clock = loaded(duration=4.0)
    state.play()
    clock.tick(3.9)
    assert state.end()
    assert not state.is_playing()
    assert state.current() == pytest.approx(4.0)
    assert not state.end()
    assert not state.stop()  # already let go at the end


def test_failure_clears_playing():
    state, _ = loaded()
    state.play()
    assert state.fail()
    assert not state.is_playing()


def test_interpolation_follows_the_rate_and_clamps_to_the_duration():
    state, clock = loaded(rate=1.5, duration=3.0)
    state.play()
    clock.tick(1.0)
    assert state.current() == pytest.approx(1.5)
    clock.tick(5.0)
    assert state.current() == pytest.approx(3.0)


def test_position_reports_rebase_the_clock():
    state, clock = loaded()
    state.play()
    clock.tick(1.0)
    state.report_position(1.5)  # Qt is ahead of the clock
    assert state.current() == pytest.approx(1.5)
    clock.tick(0.5)
    assert state.current() == pytest.approx(2.0)


def test_small_backward_reports_are_ignored_while_playing():
    state, clock = loaded()
    state.play()
    clock.tick(2.0)
    state.report_position(1.9)
    assert state.current() == pytest.approx(2.0)
    state.report_position(1.0)  # far behind: resync
    assert state.current() == pytest.approx(1.0)


def test_set_current_time_while_paused():
    state, clock = loaded()
    state.play()
    clock.tick(1.0)
    state.pause()
    assert state.seek(6.0)
    clock.tick(3.0)
    assert state.current() == pytest.approx(6.0)
    assert not state.is_playing()
    state.play()
    clock.tick(1.0)
    assert state.current() == pytest.approx(7.0)


def test_set_current_time_while_playing_ignores_stale_reports():
    state, clock = loaded()
    state.play()
    clock.tick(1.0)
    state.seek(5.0)
    state.report_position(1.1)  # sent by Qt before it heard about the seek
    assert state.current() == pytest.approx(5.0)
    clock.tick(0.2)
    state.report_position(5.2)
    assert state.current() == pytest.approx(5.2)


def test_seek_clamps_to_the_audio():
    state, _ = loaded(duration=4.0)
    state.seek(-1.0)
    assert state.current() == 0.0
    state.seek(9.0)
    assert state.current() == pytest.approx(4.0)


def test_set_rate_changes_the_interpolation_speed():
    state, clock = loaded()
    state.play()
    clock.tick(1.0)
    state.set_rate(2.0)
    assert state.current() == pytest.approx(1.0)
    clock.tick(1.0)
    assert state.current() == pytest.approx(3.0)


def test_play_at_the_end_starts_over():
    state, _ = loaded(duration=2.0)
    state.seek(2.0)
    state.play()
    assert state.current() == 0.0


def test_a_player_qt_never_ends_still_finishes_after_a_grace():
    state, clock = loaded(rate=2.0, duration=2.0)
    state.play()
    clock.tick(1.0 + audio.END_GRACE - 0.1)
    assert state.is_playing()
    clock.tick(0.2)
    assert not state.is_playing()


def test_make_player_without_pyside_raises_provider_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pyside(name, *args, **kwargs):
        if name.startswith("PySide6"):
            raise ImportError("no PySide6")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyside)
    with pytest.raises(ProviderError, match="PySide6"):
        audio.make_player(Audio(b"", "wav"), 1.0)


# ----- real playback through Qt ------------------------------------------------------------

def quiet_tone(seconds=1.0, rate=22050) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        amp = 0.05 * 32767
        w.writeframes(b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(seconds * rate))))
    return out.getvalue()


def play_to_end(player, timeout=5.0) -> float:
    start = time.monotonic()
    player.play()
    assert player.isPlaying()
    while player.isPlaying():
        assert time.monotonic() - start < timeout, "playback did not finish"
        time.sleep(0.02)
    return time.monotonic() - start


def test_real_qt_playback_of_a_quiet_tone():
    pytest.importorskip("PySide6.QtMultimedia")
    if os.environ.get("CI"):
        pytest.skip("no sound device on CI")
    from PySide6.QtMultimedia import QMediaDevices

    if not QMediaDevices.audioOutputs():
        pytest.skip("no audio output device")
    data = quiet_tone()

    player = audio.make_player(Audio(data, "wav"), 1.0)
    assert player.duration() == pytest.approx(1.0, abs=0.05)
    took = play_to_end(player)
    assert not player.isPlaying()
    assert player.currentTime() == pytest.approx(player.duration(), abs=0.05)
    assert 0.8 < took < 2.0
    player.stop()

    fast = audio.make_player(Audio(data, "wav"), 2.0)
    took_fast = play_to_end(fast)
    fast.stop()
    assert 0.35 < took_fast < 1.0
    assert took_fast < took * 0.75


def test_real_qt_rejects_bad_audio():
    pytest.importorskip("PySide6.QtMultimedia")
    if os.environ.get("CI"):
        pytest.skip("no sound device on CI")
    with pytest.raises(ProviderError, match=r"Could not decode audio \(mp3\)"):
        audio.make_player(Audio(b"not audio at all" * 50, "mp3"), 1.0)
