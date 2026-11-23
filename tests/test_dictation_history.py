"""The dictation history store: what it saves, how long it keeps it, the lifetime totals, and
how it stores the file. The file lives under the per-test temp dir (conftest). Sentences are
written for the tests; none come from a recording."""

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from lyssna import config
from lyssna.dictation import failed as failed_mod
from lyssna.dictation import history as H
from lyssna.dictation.history import HistoryStore
from lyssna.dictation.types import DictationResult

T0 = datetime(2026, 11, 17, 10, 30, tzinfo=timezone(timedelta(hours=1)))  # a Tuesday


def result(text="Send the draft to Mattias today.", raw="send the draft to matias today", words=None,
           audio_seconds=3.0, source="model"):
    return DictationResult(text=text, raw=raw, source=source, transcriber="parakeet", cleaner="s1-mini",
                           audio_seconds=audio_seconds, seconds={"total": 0.4},
                           words=len(text.split()) if words is None else words)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def days():
    box = {"days": 30}
    return box


@pytest.fixture
def store(clock, days):
    return HistoryStore(days=lambda: days["days"], now=clock)


# -- adding ------------------------------------------------------------------------------------


def test_add_saves_the_fields_and_nothing_else(store):
    store.add(result())
    [e] = store.entries()
    assert e.text == "Send the draft to Mattias today."
    assert e.raw == "send the draft to matias today"
    assert e.words == 6 and e.audio_seconds == 3.0
    assert (e.transcriber, e.cleaner, e.source) == ("parakeet", "s1-mini", "model")
    assert e.at == "2026-11-17T10:30:00+01:00"
    data = json.loads(H.default_path().read_text())
    assert set(data["entries"][0]) == {"id", "at", "text", "raw", "words", "audio_seconds", "transcriber",
                                       "cleaner", "source", "status"}
    assert data["entries"][0]["status"] == "ok"


def test_empty_results_are_ignored(store):
    store.add(result(text="", raw="", words=0))
    store.add(result(text="   ", raw="", words=0))
    assert store.entries() == []
    assert store.stats()["dictations"] == 0
    assert not H.default_path().exists()


def test_entries_are_newest_first(store, clock):
    for i in range(3):
        clock.t = T0 + timedelta(minutes=i)
        store.add(result(text=f"note number {i}"))
    assert [e.text for e in store.entries()] == ["note number 2", "note number 1", "note number 0"]


def test_ids_are_unique(store):
    for _ in range(20):
        store.add(result())
    assert len({e.id for e in store.entries()}) == 20


# -- retention -----------------------------------------------------------------------------------


def test_entries_older_than_the_limit_are_trimmed_on_add(store, clock):
    store.add(result(text="old one"))
    clock.t = T0 + timedelta(days=31)
    store.add(result(text="new one"))
    assert [e.text for e in store.entries()] == ["new one"]


def test_entries_within_the_limit_stay(store, clock):
    store.add(result(text="recent"))
    clock.t = T0 + timedelta(days=29, hours=23)
    store.add(result(text="newer"))
    assert len(store.entries()) == 2


def test_zero_days_stores_nothing_and_deletes_what_was_kept(store, days):
    store.add(result(text="kept for now"))
    days["days"] = 0
    store.add(result(text="not kept"))
    assert store.entries() == []
    assert "kept for now" not in H.default_path().read_text()
    assert store.stats()["dictations"] == 1  # the totals from before stay


def test_zero_days_applies_on_read_too(store, days):
    store.add(result(text="kept for now"))
    days["days"] = 0
    assert store.entries() == []
    assert "kept for now" not in H.default_path().read_text()


def test_minus_one_keeps_everything(store, clock, days):
    days["days"] = -1
    store.add(result(text="from long ago"))
    clock.t = T0 + timedelta(days=4000)
    store.add(result(text="today"))
    assert len(store.entries()) == 2


def test_retention_is_read_from_the_config_each_time(clock):
    store = HistoryStore(now=clock)
    store.add(result(text="first"))
    assert len(store.entries()) == 1
    config.CONFIG_PATH.write_text("[dictation]\nhistory_days = 0\n")
    assert store.entries() == []
    store.add(result(text="second"))
    assert store.entries() == []


@pytest.mark.parametrize("value, expected", [
    ("30", 30), ("7", 7), ("0", 0), ("-1", -1), ("-5", -1), ('"ten"', 30), ("true", 30), ("2.5", 30),
])
def test_configured_days(value, expected):
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(f"[dictation]\nhistory_days = {value}\n")
    assert H.configured_days() == expected


def test_configured_days_default():
    assert H.configured_days() == 30


# -- editing -----------------------------------------------------------------------------------


def test_delete_one(store, clock):
    store.add(result(text="first"))
    clock.t = T0 + timedelta(minutes=1)
    store.add(result(text="second"))
    first = store.entries()[1]
    assert store.delete(first.id)
    assert [e.text for e in store.entries()] == ["second"]
    assert not store.delete(first.id)


def test_clear_removes_entries_but_keeps_totals(store):
    store.add(result(text="one two three", audio_seconds=2.0))
    store.add(result(text="four five", audio_seconds=1.0))
    assert store.clear() == 2
    assert store.entries() == []
    s = store.stats()
    assert s["words_total"] == 5 and s["dictations"] == 2 and s["words_this_week"] == 5


def test_trimming_keeps_totals(store, clock):
    store.add(result(text="one two three"))
    clock.t = T0 + timedelta(days=40)
    store.add(result(text="four"))
    assert len(store.entries()) == 1
    assert store.stats()["words_total"] == 4
    assert store.stats()["dictations"] == 2


def test_update_text(store):
    store.add(result(text="Send it to Matias."))
    e = store.entries()[0]
    assert store.update_text(e.id, "Send it to Mattias please.")
    fixed = store.get(e.id)
    assert fixed.text == "Send it to Mattias please." and fixed.words == 5
    assert fixed.raw == e.raw
    assert store.stats()["words_total"] == 4  # lifetime totals are not rewritten
    assert not store.update_text(e.id, "   ")
    assert not store.update_text("missing", "text")


def test_search_is_case_insensitive_substring(store, clock):
    store.add(result(text="Lunch with Mattias on Friday"))
    clock.t = T0 + timedelta(minutes=1)
    store.add(result(text="Ship the Parakeet build"))
    assert [e.text for e in store.search("mattias")] == ["Lunch with Mattias on Friday"]
    assert [e.text for e in store.search("PARAKEET b")] == ["Ship the Parakeet build"]
    assert store.search("nothing here") == []
    assert len(store.search("  ")) == 2


# -- totals ------------------------------------------------------------------------------------


def test_stats(store):
    store.add(result(text="a b c d e f", audio_seconds=30.0))
    store.add(result(text="g h i j k l", audio_seconds=30.0))
    assert store.stats() == {"words_this_week": 12, "words_total": 12, "dictations": 2, "minutes": 1.0,
                             "wpm": 12}


def test_stats_when_empty(store):
    assert store.stats() == {"words_this_week": 0, "words_total": 0, "dictations": 0, "minutes": 0.0, "wpm": 0}


def test_words_this_week_starts_again_each_week(store, clock):
    store.add(result(text="one two three"))
    clock.t = T0 + timedelta(days=7)
    assert store.stats()["words_this_week"] == 0
    store.add(result(text="four"))
    assert store.stats()["words_this_week"] == 1
    assert store.stats()["words_total"] == 4


def test_only_the_last_twelve_weeks_are_kept(store, clock, days):
    days["days"] = -1
    for week in range(20):
        clock.t = T0 + timedelta(weeks=week)
        store.add(result(text="word"))
    weeks = store.totals()["weeks"]
    assert len(weeks) == H.WEEKS_KEPT
    assert max(weeks) == H.week_key(clock.t)
    assert store.stats()["words_total"] == 20


def test_week_keys_sort_by_date():
    assert H.week_key(datetime(2026, 1, 1)) == "2026-W01"
    assert H.week_key(datetime(2025, 12, 29)) == "2026-W01"  # ISO weeks belong to the year of their Thursday
    assert H.week_key(datetime(2026, 3, 2)) < H.week_key(datetime(2026, 11, 17))


# -- the file ----------------------------------------------------------------------------------


def test_file_is_owner_only_and_written_atomically(store):
    store.add(result())
    path = H.default_path()
    assert path == config.STATE_DIR / "dictation" / "history.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not path.with_name(path.name + ".tmp").exists()


def test_survives_a_reload(store, clock, days):
    store.add(result(text="kept across restarts"))
    again = HistoryStore(days=lambda: days["days"], now=clock)
    assert [e.text for e in again.entries()] == ["kept across restarts"]
    assert again.stats()["dictations"] == 1


def test_a_change_on_disk_is_picked_up(store, clock, days):
    store.add(result(text="first"))
    other = HistoryStore(days=lambda: days["days"], now=clock)
    other.add(result(text="second"))
    assert len(store.entries()) == 2


def test_corrupt_file_is_kept_aside_and_the_store_starts_empty(store):
    path = H.default_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert store.entries() == []
    assert path.with_name("history.json.broken").read_text() == "{not json"
    store.add(result(text="fresh start"))
    assert [e.text for e in store.entries()] == ["fresh start"]


def test_bad_rows_are_skipped(store):
    path = H.default_path()
    path.parent.mkdir(parents=True)
    good = {"id": "a1", "at": "2026-11-17T09:00:00+01:00", "text": "fine", "raw": "fine", "words": 1,
            "audio_seconds": 1.0, "transcriber": "parakeet", "cleaner": "none", "source": "rules:none"}
    path.write_text(json.dumps({"version": 1, "totals": {"words": "x"},
                                "entries": [good, {"id": "b2"}, "junk", {"id": "c3", "at": "never"}]}))
    assert [e.id for e in store.entries()] == ["a1"]
    assert store.stats()["words_total"] == 0


def test_shared_is_one_store():
    assert H.shared() is H.shared()
    H.shared().add(result())
    assert len(H.shared().entries()) == 1


def test_stamp_changes_on_write(store):
    assert store.stamp() is None
    store.add(result())
    first = store.stamp()
    store.add(result(text="a longer second dictation"))
    assert store.stamp() != first


def test_log_has_counts_not_text(store, caplog, days):
    secret = "Mattias's door code is in the blue folder"
    with caplog.at_level("DEBUG"):
        store.add(result(text=secret, raw=secret.lower()))
        e = store.entries()[0]
        store.update_text(e.id, secret + " again")
        store.search("door code")
        store.delete(e.id)
        store.add(result(text=secret))
        store.clear()
        days["days"] = 0
        store.add(result(text=secret))
    logged = "\n".join(r.getMessage() for r in caplog.records).lower()
    assert logged  # it does log, just not the words
    for word in ("mattias", "door", "folder", "blue"):
        assert word not in logged


# -- failed dictations ---------------------------------------------------------------------------

TONE = (0.25 * np.sin(np.arange(32000) / 7)).astype(np.float32)


def recording(entry_id):
    return config.STATE_DIR / "dictation" / "failed" / f"{entry_id}.wav"


def test_a_failed_dictation_is_saved_with_its_recording(store):
    e = store.add_failed(TONE, 2.0, "openai")
    assert e is not None and e.failed and e.status == "failed"
    assert (e.text, e.words, e.audio_seconds, e.transcriber) == ("", 0, 2.0, "openai")
    assert recording(e.id).is_file()
    assert store.recordings.load(e.id) is not None
    [kept] = store.entries()
    assert kept.id == e.id and kept.failed
    data = json.loads(H.default_path().read_text())
    assert data["entries"][0]["status"] == "failed"


def test_failed_dictations_are_not_counted_until_a_retry_works(store):
    store.add(result())
    e = store.add_failed(TONE, 2.0)
    assert store.stats()["dictations"] == 1 and store.stats()["words_total"] == 6
    assert store.complete(e.id, result(text="Second try worked.", audio_seconds=2.0)) is True
    [first, _] = store.entries()
    assert first.id == e.id and not first.failed and first.text == "Second try worked."
    assert first.raw == "send the draft to matias today" and first.words == 3
    stats = store.stats()
    assert stats["dictations"] == 2 and stats["words_total"] == 9 and stats["minutes"] == round(5.0 / 60, 1)
    assert not recording(e.id).exists()
    assert store.complete(e.id, result()) is False  # once only


def test_a_retry_that_hears_nothing_removes_the_entry(store):
    e = store.add_failed(TONE, 2.0)
    assert store.complete(e.id, result(text="", raw="", words=0)) is True
    assert store.entries() == [] and not recording(e.id).exists()
    assert store.stats()["dictations"] == 0


def test_old_files_without_a_status_read_as_ok(store):
    path = H.default_path()
    path.parent.mkdir(parents=True)
    old = {"id": "a1", "at": "2026-11-17T09:00:00+01:00", "text": "fine", "raw": "fine", "words": 1,
           "audio_seconds": 1.0, "transcriber": "parakeet", "cleaner": "none", "source": "rules:none"}
    odd = {**old, "id": "b2", "status": "something else"}
    path.write_text(json.dumps({"version": 1, "totals": {}, "entries": [old, odd]}))
    assert [(e.id, e.status) for e in store.entries()] == [("a1", "ok"), ("b2", "ok")]


def test_search_skips_failed_entries(store):
    store.add(result(text="Draft for Mattias."))
    store.add_failed(TONE, 2.0)
    assert len(store.search("")) == 2  # the list shows them
    assert [e.text for e in store.search("a")] == ["Draft for Mattias."]


def test_a_failed_entry_cannot_be_fixed(store):
    e = store.add_failed(TONE, 2.0)
    assert store.update_text(e.id, "typed in") is False


def test_delete_and_clear_remove_the_recordings(store):
    a = store.add_failed(TONE, 2.0)
    b = store.add_failed(TONE, 2.0)
    store.add(result())
    assert store.delete(a.id) is True
    assert not recording(a.id).exists() and recording(b.id).exists()
    assert store.clear() == 2
    assert not recording(b.id).exists()
    assert list((config.STATE_DIR / "dictation" / "failed").iterdir()) == []


def test_failed_entries_expire_after_24_hours(store, clock, days):
    days["days"] = -1  # keep everything else for ever: the 24 hours still apply
    store.add(result(text="kept"))
    e = store.add_failed(TONE, 2.0)
    clock.t = T0 + timedelta(hours=23)
    assert len(store.entries()) == 2
    clock.t = T0 + timedelta(hours=24, minutes=1)
    assert [x.text for x in store.entries()] == ["kept"]
    assert not recording(e.id).exists()


def test_at_most_five_failed_entries_the_oldest_going_first(store, clock):
    ids = []
    for i in range(H.MAX_FAILED + 1):
        clock.t = T0 + timedelta(minutes=i)
        ids.append(store.add_failed(TONE, 2.0).id)
    kept = [e.id for e in store.entries()]
    assert kept == list(reversed(ids[1:]))
    assert not recording(ids[0]).exists() and all(recording(i).exists() for i in ids[1:])


def test_with_history_off_nothing_failed_is_saved(store, days):
    days["days"] = 0
    assert store.add_failed(TONE, 2.0) is None
    assert store.entries() == [] and not (config.STATE_DIR / "dictation" / "failed").exists()


def test_turning_history_off_deletes_failed_recordings(store, days):
    e = store.add_failed(TONE, 2.0)
    days["days"] = 0
    assert store.entries() == [] and not recording(e.id).exists()


def test_when_the_recording_cannot_be_written_no_entry_is_saved(store, monkeypatch):
    monkeypatch.setattr(failed_mod.FailedRecordings, "save", lambda self, entry_id, samples: False)
    assert store.add_failed(TONE, 2.0) is None
    assert store.entries() == []


def test_failed_entries_log_counts_only(store, caplog):
    with caplog.at_level("DEBUG"):
        e = store.add_failed(TONE, 2.0)
        store.complete(e.id, result(text="Mattias's door code"))
    logged = "\n".join(r.getMessage() for r in caplog.records).lower()
    assert "door" not in logged and "mattias" not in logged and e.id not in logged
