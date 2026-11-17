"""Reader.seek_to maps a whole-reading fraction onto (sentence, offset)."""

from lyssna.player import Reader, Session


class FakePlayer:
    def __init__(self, duration=10.0):
        self._t = 0.0
        self._d = duration

    def currentTime(self):
        return self._t

    def setCurrentTime_(self, t):
        self._t = t

    def duration(self):
        return self._d

    def stop(self):
        pass


def make(chunks, index, durations):
    r = Reader(lambda: None)
    s = Session(chunks=chunks, provider=None)
    s.index = index
    s.durations = durations
    s.player = FakePlayer(durations.get(index, 10.0))
    r._session = s
    return r, s


def test_seek_within_current_sentence():
    r, s = make(["a" * 100, "b" * 100, "c" * 100], 0, {0: 10.0})
    r.seek_to(0.1)  # 30s total by estimate → 3s, inside sentence 0
    assert abs(s.player.currentTime() - 3.0) < 0.01
    assert s.skip == 0


def test_seek_into_later_sentence_sets_skip_and_offset():
    r, s = make(["a" * 100, "b" * 100, "c" * 100], 0, {0: 10.0})
    r.seek_to(0.5)  # 15s → sentence 1, 5s in
    assert s.skip == 1
    assert abs(s.start_offset - 5.0) < 0.01


def test_seek_backwards_keeps_pause_state():
    r, s = make(["a" * 100, "b" * 100, "c" * 100], 2, {0: 10.0, 1: 10.0, 2: 10.0})
    s.paused = True
    r.seek_to(0.0)
    assert s.skip == -2 and s.paused is True
    assert s.start_offset == 0.0


def test_seek_clamps_at_end_of_current_sentence():
    r, s = make(["a" * 100], 0, {0: 10.0})
    r.seek_to(1.0)
    assert s.player.currentTime() <= 10.0
