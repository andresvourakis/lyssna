"""Playback of synthesized audio on Windows.

The real version will use the Qt Multimedia player (PySide6) with the pitch kept when the
speed changes, or Windows' own media player (WinRT) if Qt cannot keep the pitch.

The player contract. make_player(audio, speed) takes a lyssna.providers.base.Audio and the
reading speed, and returns an object ready to play at that speed, or raises ProviderError
when the audio cannot be decoded. The reading engine (lyssna/player.py) calls exactly these
eight methods on it, with the AVAudioPlayer names the Mac player has:

  play()                    start or resume
  pause()                   pause, keeping the position
  stop()                    stop
  currentTime()             position in seconds
  setCurrentTime_(seconds)  move to a position in seconds
  duration()                length in seconds
  isPlaying()               True while it plays
  setRate_(rate)            speed, 1.0 is normal, with the pitch unchanged

Not built yet: make_player raises."""

from __future__ import annotations


def make_player(audio, speed: float):
    raise NotImplementedError("Windows playback is not built yet")
