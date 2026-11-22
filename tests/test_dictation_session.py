"""The dictation state machine with a fake pipeline, recorder, inserter and watcher: no
microphone, no models, no keystrokes."""

import logging
import threading

import numpy as np
import pytest

from lyssna.dictation import session as session_mod
from lyssna.dictation.recorder import RecorderError
from lyssna.dictation.session import DictationSession
from lyssna.dictation.types import DictationError, DictationResult

SPEECH = (0.2 * np.sin(np.arange(16000) / 5)).astype(np.float32)  # one second of "speech"


class FakeRecorder:
    def __init__(self, samples=SPEECH, fail=None):
        self.samples = samples
        self.fail = fail
        self.calls = []
        self.on_level = self.on_stopped = None

    def start(self, on_level=None, on_stopped=None):
        self.calls.append("start")
        if self.fail:
            raise self.fail
        self.on_level, self.on_stopped = on_level, on_stopped

    def stop(self):
        self.calls.append("stop")
        return self.samples

    def cancel(self):
        self.calls.append("cancel")

    def seconds(self):
        return 1.5


class FakePipeline:
    def __init__(self, text="Hello there.", fail=None):
        self.text = text
        self.fail = fail
        self.runs = []
        self.warmed = 0

    def warm_up(self):
        self.warmed += 1

    def run(self, samples, sample_rate=16000):
        self.runs.append((len(samples), sample_rate))
        if self.fail:
            raise self.fail
        return DictationResult(text=self.text, raw=self.text.lower(), source="model", transcriber="parakeet",
                               cleaner="s1mini", audio_seconds=len(samples) / 16000,
                               seconds={"transcribe": 0.1, "clean": 0.1, "total": 0.2}, words=len(self.text.split()))


class FakeWatcher:
    def __init__(self):
        self.watched = []
        self.stopped = 0

    def watch(self, text):
        self.watched.append(text)

    def stop(self):
        self.stopped += 1


class Timers:
    def __init__(self):
        self.pending = []

    def __call__(self, delay, fn):
        self.pending.append((delay, fn))

    def fire(self):
        pending, self.pending = self.pending, []
        for _, fn in pending:
            fn()


@pytest.fixture
def make():
    def build(recorder=None, pipeline=None, outcome="pasted", mic="granted", auto_learn=True):
        parts = {
            "recorder": recorder or FakeRecorder(),
            "pipeline": pipeline or FakePipeline(),
            "pasted": [],
            "watcher": FakeWatcher(),
            "timers": Timers(),
            "states": [],
            "results": [],
            "hooks": [],
            "asked": [],
        }

        def inserter(text):
            parts["pasted"].append(text)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        s = DictationSession(
            parts["pipeline"], parts["recorder"], inserter,
            watcher=parts["watcher"], auto_learn=lambda: auto_learn,
            before_start=lambda: parts["hooks"].append("before_start"),
            on_result=parts["results"].append,
            mic_status=lambda: mic, request_mic=lambda cb: parts["asked"].append(cb),
            timer=parts["timers"],
        )
        s.subscribe(lambda st: parts["states"].append(st))
        parts["session"] = s
        return parts
    return build


def settle(s):
    """Wait until the session's worker has run everything queued so far."""
    done = threading.Event()
    s._jobs.put(done.set)
    assert done.wait(2)


def states(parts):
    out = []
    for st in parts["states"]:
        if not out or out[-1] != st.state:
            out.append(st.state)
    return out


def test_hold_to_talk_pastes_and_watches_for_corrections(make):
    p = make()
    s = p["session"]
    assert s.start() is True
    settle(s)
    assert p["hooks"] == ["before_start"] and p["pipeline"].warmed == 1
    s.stop()
    settle(s)
    assert p["pasted"] == ["Hello there."]
    assert states(p) == ["recording", "working", "idle"]
    assert p["results"][0].text == "Hello there." and s.last_result is p["results"][0]
    assert p["watcher"].watched == ["Hello there."]
    assert p["pipeline"].runs == [(16000, 16000)]


def test_level_updates_reach_observers_while_recording(make):
    p = make()
    s = p["session"]
    s.start()
    settle(s)
    p["recorder"].on_level(0.7)
    last = p["states"][-1]
    assert last.state == "recording" and last.level == 0.7 and last.seconds == 1.5
    p["states"][-1].level = 0.0  # observers get copies: changing one changes nothing
    assert s.status.level == 0.7


def test_hands_free_lock_then_stop(make):
    p = make()
    s = p["session"]
    s.start()
    s.lock()
    assert s.state == "locked"
    s.stop()
    settle(s)
    assert states(p) == ["recording", "locked", "working", "idle"]
    assert p["pasted"] == ["Hello there."]


def test_cancel_offers_undo_which_processes_the_audio(make):
    p = make()
    s = p["session"]
    s.start()
    s.lock()
    s.cancel()
    st = s.status
    assert st.state == "notice" and st.can_undo and st.message == session_mod.CANCELLED
    settle(s)
    assert p["pasted"] == []
    assert s.undo_cancel() is True
    settle(s)
    assert p["pasted"] == ["Hello there."] and s.state == "idle"
    p["timers"].fire()  # the expired undo timer changes nothing now
    assert s.state == "idle"


def test_undo_expires(make):
    p = make()
    s = p["session"]
    s.start()
    s.cancel()
    settle(s)
    p["timers"].fire()
    assert s.state == "idle" and s._undo is None
    assert s.undo_cancel() is False
    settle(s)
    assert p["pasted"] == []


def test_undo_pressed_before_the_audio_was_kept_still_works(make):
    p = make()
    s = p["session"]
    s.start()
    s.cancel()
    s.undo_cancel()  # straight away, maybe before the worker stored the audio
    settle(s)
    assert p["pasted"] == ["Hello there."]


def test_quiet_cancel_shows_nothing(make):
    p = make()
    s = p["session"]
    s.start()
    s.cancel(quiet=True)
    settle(s)
    assert states(p) == ["recording", "idle"]
    assert "cancel" in p["recorder"].calls and p["pasted"] == []
    assert s.undo_cancel() is False


@pytest.mark.parametrize(
    "samples",
    [np.zeros(16000, dtype=np.float32), SPEECH[:4000], np.zeros(0, dtype=np.float32)],  # silent, 0.25 s, empty
)
def test_short_or_silent_recordings_end_quietly(make, samples):
    p = make(recorder=FakeRecorder(samples=samples))
    s = p["session"]
    s.start()
    s.stop()
    settle(s)
    assert states(p) == ["recording", "working", "idle"]
    assert p["pipeline"].runs == [] and p["pasted"] == []


def test_empty_transcript_pastes_nothing(make):
    p = make(pipeline=FakePipeline(text="  "))
    s = p["session"]
    s.start()
    s.stop()
    settle(s)
    assert s.state == "idle" and p["pasted"] == [] and len(p["results"]) == 1


def test_no_text_field_leaves_a_copied_notice(make):
    p = make(outcome="copied")
    s = p["session"]
    s.start()
    s.stop()
    settle(s)
    assert s.status.message == session_mod.COPIED and s.state == "notice"
    assert p["watcher"].watched == []
    p["timers"].fire()
    assert s.state == "idle"


def test_auto_learn_off_does_not_watch(make):
    p = make(auto_learn=False)
    s = p["session"]
    s.start()
    s.stop()
    settle(s)
    assert p["pasted"] and p["watcher"].watched == []


def test_pipeline_failure_becomes_a_notice_without_the_details(make, caplog):
    p = make(pipeline=FakePipeline(fail=RuntimeError("secret transcript words")))
    s = p["session"]
    caplog.set_level(logging.INFO)
    s.start()
    s.stop()
    settle(s)
    assert s.status.message == session_mod.FAILED
    assert "secret transcript" not in caplog.text
    p["timers"].fire()
    assert s.state == "idle"


def test_the_transcript_is_never_logged(make, caplog):
    p = make(pipeline=FakePipeline(text="Meet Mattias at noon"))
    caplog.set_level(logging.DEBUG)
    s = p["session"]
    s.start()
    s.stop()
    settle(s)
    assert p["pasted"] == ["Meet Mattias at noon"]
    assert "Mattias" not in caplog.text and "dictation pasted" in caplog.text


def test_a_pipeline_error_message_is_shown_but_not_logged(make, caplog):
    p = make(pipeline=FakePipeline(fail=DictationError("OpenAI said: quota exceeded")))
    caplog.set_level(logging.DEBUG)
    s = p["session"]
    s.start()
    s.stop()
    settle(s)
    assert s.status.message == "OpenAI said: quota exceeded"
    assert "quota" not in caplog.text


def test_microphone_failure_becomes_a_notice(make):
    p = make(recorder=FakeRecorder(fail=RecorderError("No microphone found")))
    s = p["session"]
    s.start()
    settle(s)
    assert s.status.message == "No microphone found" and s.state == "notice"
    s.stop()  # letting go of the key afterwards changes nothing
    settle(s)
    assert s.state == "notice" and p["pasted"] == []


def test_without_microphone_access(make):
    p = make(mic="denied")
    s = p["session"]
    assert s.start() is False
    assert s.status.message == session_mod.NO_MIC
    p2 = make(mic="undetermined")
    assert p2["session"].start() is False
    assert len(p2["asked"]) == 1 and p2["session"].status.message == session_mod.ASK_MIC


def test_busy_session_refuses_a_new_start(make):
    gate = threading.Event()

    class SlowPipeline(FakePipeline):
        def run(self, samples, sample_rate=16000):
            gate.wait(2)
            return super().run(samples, sample_rate)

    p = make(pipeline=SlowPipeline())
    s = p["session"]
    s.start()
    s.stop()
    assert s.start() is False  # still working on the last one
    gate.set()
    settle(s)
    assert s.start() is True


def test_a_new_dictation_stops_the_watcher_and_dismisses_a_notice(make):
    p = make(outcome="copied")
    s = p["session"]
    s.start()
    s.stop()
    settle(s)
    assert s.state == "notice"
    assert s.start() is True
    assert p["watcher"].stopped == 2 and s.status.session == 2
    p["timers"].fire()  # the old notice's timer must not end the new recording
    assert s.state == "recording"


def test_the_recorder_ending_by_itself_finishes_normally(make):
    p = make()
    s = p["session"]
    s.start()
    s.lock()
    settle(s)
    p["recorder"].on_stopped()  # the ten-minute cap
    settle(s)
    assert p["pasted"] == ["Hello there."] and s.state == "idle"


def test_a_failing_observer_does_not_break_the_session(make):
    p = make()
    s = p["session"]
    s.subscribe(lambda st: 1 / 0)
    s.start()
    s.stop()
    settle(s)
    assert p["pasted"] == ["Hello there."]


def test_close_ends_the_worker_after_what_is_queued(make):
    p = make()
    s = p["session"]
    s.start()
    s.stop()  # queued before close, so it still runs
    s.close()
    s.thread.join(2)
    assert not s.thread.is_alive()
    assert p["pasted"] == ["Hello there."]
