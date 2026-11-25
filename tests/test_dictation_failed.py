"""The failed-recording store: WAV files under the per-test temp dir (conftest), owner-only,
written atomically, kept for 24 hours and at most five. The audio is a test tone."""

import logging
import os
import stat
import time

import numpy as np
import pytest

from lyssna import config
from lyssna.dictation import failed as F
from lyssna.dictation.failed import FailedRecordings

# Windows has no POSIX modes (chmod only sets read-only); there the folder sits in the user's own
# %LOCALAPPDATA%, which Windows already limits to that user, so the mode checks run on POSIX only.
POSIX_MODES = os.name == "posix"

TONE = (0.25 * np.sin(np.arange(16000) / 7)).astype(np.float32)


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return FailedRecordings(now=clock)


def age(store, entry_id, seconds):
    path = store.directory / f"{entry_id}.wav"
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_saves_16_khz_mono_wav_and_reads_it_back(store):
    assert store.save("abc123", TONE) is True
    path = config.STATE_DIR / "dictation" / "failed" / "abc123.wav"
    assert path.is_file() and store.exists("abc123")
    import wave

    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 16000)
    back = store.load("abc123")
    assert back.dtype == np.float32 and back.shape == TONE.shape
    assert np.max(np.abs(back - TONE)) < 1e-3


def test_folder_and_files_are_owner_only_and_written_atomically(store):
    store.save("abc123", TONE)
    folder = store.directory
    if POSIX_MODES:
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        assert stat.S_IMODE((folder / "abc123.wav").stat().st_mode) == 0o600
    assert [p.name for p in folder.iterdir()] == ["abc123.wav"]  # no temp file left behind


def test_an_existing_folder_is_made_owner_only(store):
    store.directory.mkdir(parents=True, mode=0o755)
    store.directory.chmod(0o755)
    assert store.save("abc123", TONE) is True
    if POSIX_MODES:
        assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700


def test_missing_and_bad_ids(store):
    assert store.load("nothing") is None
    assert store.delete("nothing") is False
    for bad in ("../escape", "a/b", "", "x" * 100):
        assert store.save(bad, TONE) is False
        assert store.load(bad) is None
    assert not store.directory.exists()


def test_delete_and_clear(store):
    for i in range(3):
        store.save(f"id{i}", TONE)
    assert store.delete("id0") is True and store.load("id0") is None
    assert store.clear() == 2
    assert list(store.directory.iterdir()) == []


def test_expires_after_24_hours(store):
    store.save("old", TONE)
    store.save("new", TONE)
    age(store, "old", F.KEEP_SECONDS + 60)
    age(store, "new", F.KEEP_SECONDS - 60)
    assert store.load("old") is None
    assert store.load("new") is not None
    assert not (store.directory / "old.wav").exists()


def test_keeps_at_most_five_the_oldest_going_first(store):
    for i in range(F.MAX_KEPT):
        store.save(f"id{i}", TONE)
        age(store, f"id{i}", 600 - i * 60)  # id0 oldest
    store.save("newest", TONE)
    names = sorted(p.stem for p in store.directory.iterdir())
    assert names == sorted(["id1", "id2", "id3", "id4", "newest"])


def test_the_one_just_saved_is_never_the_one_pruned(store):
    for i in range(F.MAX_KEPT):
        store.save(f"id{i}", TONE)
    age(store, "id0", 10)
    # a clock skew makes the new file look oldest: it still stays
    store.save("fresh", TONE)
    age(store, "fresh", 3600)
    store.prune(keep="fresh")
    assert (store.directory / "fresh.wav").exists() and len(list(store.directory.iterdir())) == F.MAX_KEPT


def test_a_damaged_file_is_dropped(store):
    store.directory.mkdir(parents=True)
    (store.directory / "broken.wav").write_bytes(b"not a wav")
    assert store.load("broken") is None
    assert not (store.directory / "broken.wav").exists()


def test_shared_is_one_store():
    assert F.shared() is F.shared()


def test_log_has_no_audio(store, caplog):
    caplog.set_level(logging.DEBUG, logger="lyssna")
    store.save("abc123", TONE)
    store.load("abc123")
    store.delete("abc123")
    logged = [r.getMessage() for r in caplog.records]
    assert logged == ["saved a failed recording (1.0 s) for a retry", "deleted a failed recording"]
