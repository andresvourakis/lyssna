"""Playback on the Mac: one AVAudioPlayer per chunk, so pause, resume and speed are native and
instant (speed is time-stretched with the pitch kept). The reading engine reaches this through
lyssna.host; the player contract is written down in lyssna/win/audio.py."""

from __future__ import annotations

from AVFoundation import AVAudioPlayer
from Foundation import NSData

from ..providers.base import Audio, ProviderError


def make_player(audio: Audio, speed: float):
    data = NSData.dataWithBytes_length_(audio.data, len(audio.data))
    player, err = AVAudioPlayer.alloc().initWithData_error_(data, None)
    if player is None:
        raise ProviderError(f"Could not decode audio ({audio.ext}): {err}")
    player.setEnableRate_(True)
    player.prepareToPlay()
    player.setRate_(speed)
    return player
