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
            "failed": [],
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
            on_failed=lambda samples, message: parts["failed"].append((samples, message)),
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


# -- a failed transcription kept for a retry -------------------------------------------------


class FlakyPipeline(FakePipeline):
    """Fails the first `failures` runs with `error`, then works."""

    def __init__(self, error, failures=1, text="Hello there."):
        super().__init__(text=text)
        self.error, self.failures = error, failures

    def run(self, samples, sample_rate=16000):
        if self.failures > 0:
            self.failures -= 1
            self.runs.append((len(samples), sample_rate))
            raise self.error
        return super().run(samples, sample_rate)


def dictate(s):
    s.start()
    s.stop()
    settle(s)


def test_a_transcription_failure_hands_over_the_samples_and_offers_retry(make):
    p = make(pipeline=FlakyPipeline(DictationError("OpenAI did not answer.", retryable=True)))
    s = p["session"]
    dictate(s)
    [(samples, message)] = p["failed"]
    assert samples is SPEECH and message == "OpenAI did not answer."
    st = s.status
    assert (st.state, st.message, st.can_retry) == ("notice", "OpenAI did not answer.", True)
    assert p["results"] == [] and p["pasted"] == []


def test_a_failure_without_a_message_says_dictation_failed(make):
    p = make(pipeline=FlakyPipeline(DictationError("", retryable=True)))
    dictate(p["session"])
    assert p["failed"][0][1] == session_mod.TRANSCRIPTION_FAILED == "Dictation failed."


def test_other_failures_are_not_kept(make):
    p = make(pipeline=FakePipeline(fail=DictationError("Dictation needs a speech engine, and none can run.")))
    dictate(p["session"])
    assert p["failed"] == [] and p["session"].status.can_retry is False
    p = make(pipeline=FakePipeline(fail=RuntimeError("boom")))
    dictate(p["session"])
    assert p["failed"] == [] and p["session"].status.message == session_mod.FAILED


def test_microphone_errors_and_silence_are_not_kept(make):
    p = make(recorder=FakeRecorder(fail=RecorderError("No microphone found.")),
             pipeline=FlakyPipeline(DictationError("x", retryable=True)))
    s = p["session"]
    s.start()
    settle(s)
    s.stop()
    settle(s)
    assert p["failed"] == [] and s.status.can_retry is False
    p = make(recorder=FakeRecorder(samples=np.zeros(16000, dtype=np.float32)),
             pipeline=FlakyPipeline(DictationError("x", retryable=True)))
    dictate(p["session"])
    assert p["failed"] == [] and p["pipeline"].runs == []


def test_no_keeper_means_no_retry_offered(make):
    p = make(pipeline=FlakyPipeline(DictationError("x", retryable=True)))
    p["session"].on_failed = None
    dictate(p["session"])
    assert p["session"].status.can_retry is False


def test_retry_pastes_through_the_working_state(make):
    p = make(pipeline=FlakyPipeline(DictationError("x", retryable=True)))
    s = p["session"]
    dictate(s)
    got, failed = [], []
    assert s.retry(SPEECH, paste=True, on_result=got.append, on_failed=failed.append) is True
    settle(s)
    assert [r.text for r in got] == ["Hello there."] and failed == []
    assert p["pasted"] == ["Hello there."] and p["watcher"].watched == ["Hello there."]
    assert p["results"] == []  # a retry is not a new dictation: the session's on_result is not called
    assert states(p)[-3:] == ["notice", "working", "idle"]


def test_retry_without_paste_only_hands_the_result_over(make):
    p = make()
    s = p["session"]
    got = []
    assert s.retry(SPEECH, paste=False, on_result=got.append) is True
    settle(s)
    assert [r.text for r in got] == ["Hello there."]
    assert p["pasted"] == [] and s.state == "idle"


def test_a_retry_that_fails_again_says_so(make):
    p = make(pipeline=FlakyPipeline(DictationError("", retryable=True), failures=2))
    s = p["session"]
    dictate(s)
    failed = []
    s.retry(SPEECH, on_result=lambda r: pytest.fail("worked"), on_failed=failed.append)
    settle(s)
    assert failed == [session_mod.RETRY_FAILED] == ["Retry failed. Try again."]
    st = s.status
    assert (st.state, st.message, st.can_retry) == ("notice", "Retry failed. Try again.", True)
    assert len(p["failed"]) == 1  # the first failure only: a retry's recording is kept already


def test_a_retry_with_a_provider_message_shows_it(make):
    p = make(pipeline=FlakyPipeline(DictationError("Inworld: rate limited.", retryable=True)))
    s = p["session"]
    failed = []
    s.retry(SPEECH, on_failed=failed.append)
    settle(s)
    assert failed == ["Inworld: rate limited."] and s.status.message == "Inworld: rate limited."


def test_a_retry_always_settles(make):
    p = make(pipeline=FakePipeline(fail=DictationError("No engine can run.")))
    s = p["session"]
    failed = []
    s.retry(SPEECH, on_failed=failed.append)
    settle(s)
    assert failed == ["No engine can run."] and s.status.can_retry is False
    p = make()
    s = p["session"]
    failed = []
    s.retry(np.zeros(100, dtype=np.float32), on_failed=failed.append)  # silent: dropped on the way
    settle(s)
    assert failed == [""] and s.state == "idle"


def test_retry_cannot_overlap_a_dictation(make):
    p = make()
    s = p["session"]
    s.start()
    assert s.retry(SPEECH) is False
    s.stop()
    assert s.state == "working" and s.retry(SPEECH) is False
    settle(s)
    assert s.retry(SPEECH, paste=False) is True
    assert s.start() is False  # and a dictation cannot start while a retry works
    settle(s)


def test_paste_text_uses_the_inserter_and_says_when_it_copied(make):
    p = make()
    s = p["session"]
    assert s.paste_text("Again.") is True
    settle(s)
    assert p["pasted"] == ["Again."] and s.state == "idle"
    p = make(outcome="copied")
    s = p["session"]
    s.paste_text("Again.")
    settle(s)
    assert s.status.message == session_mod.COPIED
    assert s.paste_text("  ") is False
    s.start()
    assert s.paste_text("Again.") is False
