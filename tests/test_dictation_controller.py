"""The controller's dictation side: reading and dictation exclude each other, first use (the
microphone, the speech model, the Globe key), local-only mode, the toasts, the socket command
and shutdown. The session runs with a fake pipeline, microphone and paste; the reader is a
fake; nothing records, plays, pastes, downloads or opens System Settings."""

import sys
import threading
import time

import numpy as np
import pytest

from lyssna import config, controller, host, keystore
from lyssna.controller import ASK_MIC, BUSY, GLOBE_HINT, MODEL_READY, NO_MIC, Controller, Notice
from lyssna.dictation import models
from lyssna.dictation.types import DictationResult
from lyssna.player import Status

SPEECH = (0.2 * np.sin(np.arange(16000) / 5)).astype(np.float32)


class FakeReader:
    def __init__(self, state="idle"):
        self.calls = []
        self.speed = 1.0
        self.status = Status(state=state)

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))

    def toggle_pause(self):
        self.calls.append(("toggle_pause",))
        self.status = Status(state="paused" if self.status.state != "paused" else "playing")

    def speak(self, text):
        self.calls.append(("speak", text))
        return True

    def is_active(self):
        return self.status.state != "idle"


class FakeRecorder:
    def __init__(self):
        self.calls = []

    def start(self, on_level=None, on_stopped=None):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")
        return SPEECH

    def cancel(self):
        self.calls.append("cancel")

    def seconds(self):
        return 1.0

    def warm(self):
        self.calls.append("warm")


class FakePipeline:
    def warm_up(self):
        pass

    def run(self, samples, sample_rate=16000):
        return DictationResult(text="Hello.", raw="hello", source="model", transcriber="parakeet", cleaner="s1-mini",
                               audio_seconds=1.0, seconds={"transcribe": 0.1, "clean": 0.1, "total": 0.2}, words=1)


class FakeWatcher:
    def __init__(self):
        self.stopped = 0

    def watch(self, text):
        pass

    def stop(self):
        self.stopped += 1


class FakePTT:
    def __init__(self):
        self.state = "idle"
        self.calls = []

    def enter_locked(self):
        self.calls.append("enter_locked")
        self.state = "locked"

    def reset(self):
        self.calls.append("reset")
        self.state = "idle"


class Timers:
    def __init__(self):
        self.pending = []

    def __call__(self, delay, fn):
        self.pending.append(fn)

    def fire(self):
        pending, self.pending = self.pending, []
        for fn in pending:
            fn()


@pytest.fixture
def models_here(monkeypatch):
    monkeypatch.setattr(models.ModelSet, "present", lambda self: True)


@pytest.fixture
def ctl(monkeypatch, models_here):
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(controller, "open_url", lambda url: opened.append(url))
    opened: list[str] = []
    c = Controller()
    c.reader = FakeReader()
    c.toasts = []
    c.opened = opened
    c.subscribe_toast(c.toasts.append)
    c.mic = "granted"
    c.asked = []
    c.timers = Timers()
    c.pasted = []
    c.dictation = c._make_dictation(
        pipeline=FakePipeline(), recorder=FakeRecorder(), inserter=lambda text: (c.pasted.append(text), "pasted")[1],
        watcher=FakeWatcher(), mic_status=lambda: c.mic, request_mic=c.asked.append, timer=c.timers,
    )
    c.push_to_talk = FakePTT()
    monkeypatch.setattr("lyssna.mac.globe.globe_key_action", lambda: "nothing")
    return c


def settle(c):
    done = threading.Event()
    c.dictation._jobs.put(done.set)
    assert done.wait(2)


def texts(c):
    return [t.text for t in c.toasts]


# -- starting, stopping, and reading versus dictation ----------------------------------------


def test_hold_to_talk_records_then_pastes(ctl):
    assert ctl.dictation_start() is True
    assert ctl.dictating() and ctl.dictation.state == "recording"
    ctl.dictation_stop()
    settle(ctl)
    assert ctl.pasted == ["Hello."] and ctl.dictation.state == "idle"


def test_starting_a_dictation_pauses_a_reading_and_it_stays_paused(ctl):
    ctl.reader.status = Status(state="playing")
    ctl.dictation_start()
    settle(ctl)
    assert ctl.reader.calls == [("toggle_pause",)] and ctl.reader.status.state == "paused"
    ctl.dictation_stop()
    settle(ctl)
    assert ctl.reader.status.state == "paused" and ctl.reader.calls == [("toggle_pause",)]


def test_a_paused_reading_is_left_alone(ctl):
    ctl.reader.status = Status(state="paused")
    ctl.dictation_start()
    settle(ctl)
    assert ctl.reader.calls == []


def test_read_actions_only_say_so_while_dictating(ctl, monkeypatch):
    monkeypatch.setattr(controller.capture, "selection_or_clipboard", lambda: pytest.fail("captured while dictating"))
    monkeypatch.setattr(controller.capture, "clipboard_text", lambda: pytest.fail("read the clipboard while dictating"))
    ctl.dictation_start()
    assert ctl.speak_text("Hello.") is False
    ctl.speak_selection()
    ctl.speak_clipboard()
    ctl.reader.status = Status(state="paused")
    ctl.toggle_pause()  # resuming would read into the microphone
    assert ctl.reader.calls == []
    assert texts(ctl) == [BUSY] * 4
    assert ctl.dispatch({"cmd": "speak", "text": "Hi"})["ok"] is False
    ctl.dictation_stop()
    settle(ctl)
    assert ctl.speak_text("Hello.") is True  # reading works again afterwards


def test_working_also_blocks_reading(ctl):
    gate = threading.Event()
    slow = FakePipeline()
    run = slow.run
    slow.run = lambda samples, rate=16000: (gate.wait(2), run(samples))[1]
    ctl.dictation.pipeline = slow
    ctl.dictation_start()
    ctl.dictation_stop()
    assert ctl.dictation.state == "working" and ctl.speak_text("Hi") is False
    gate.set()
    settle(ctl)


def test_hands_free_locks_and_tells_the_key(ctl):
    assert ctl.dictation_start(hands_free=True) is True
    assert ctl.dictation.state == "locked"
    assert ctl.push_to_talk.calls == ["enter_locked"]


def test_busy_session_refuses_a_second_start(ctl):
    ctl.dictation_start()
    assert ctl.dictation_start() is False


def test_ending_by_another_route_resets_the_key(ctl):
    ctl.dictation_start(hands_free=True)
    ctl.dictation_stop()  # the pill's finish button
    settle(ctl)
    assert ctl.push_to_talk.state == "idle" and "reset" in ctl.push_to_talk.calls


def test_the_key_is_not_reset_while_recording(ctl):
    ctl.push_to_talk.state = "holding"
    ctl.dictation_start()
    settle(ctl)
    assert ctl.push_to_talk.state == "holding" and "reset" not in ctl.push_to_talk.calls


def test_cancel_offers_undo_in_a_toast(ctl):
    ctl.dictation_start(hands_free=True)
    ctl.dictation_cancel()
    settle(ctl)
    toast = ctl.toasts[-1]
    assert (toast.text, toast.action) == ("Transcript cancelled", "Undo")
    assert toast.seconds == ctl.dictation.undo_seconds
    assert ctl.push_to_talk.state == "idle"
    toast.on_action()  # Undo
    settle(ctl)
    assert ctl.pasted == ["Hello."]
    assert texts(ctl).count("Transcript cancelled") == 1  # one toast per notice, not per status update


@pytest.mark.skipif(sys.platform != "darwin", reason="asserts the Mac wording; test_windows_imports checks the Windows wording")
def test_copied_notice_becomes_a_toast(ctl):
    ctl.dictation.inserter = lambda text: "copied"
    ctl.dictation_start()
    ctl.dictation_stop()
    settle(ctl)
    assert ctl.toasts[-1] == Notice("Copied. Press ⌘V to paste")


def test_result_goes_to_the_history(ctl, monkeypatch):
    from lyssna.dictation import history

    added = []
    monkeypatch.setattr(history, "shared", lambda: type("H", (), {"add": lambda self, r: added.append(r)})())
    ctl.dictation_start()
    ctl.dictation_stop()
    settle(ctl)
    assert [r.text for r in added] == ["Hello."]


def test_no_history_module_is_fine(ctl, monkeypatch):
    import builtins

    real = builtins.__import__

    def no_history(name, globals=None, locals=None, fromlist=(), level=0):
        if fromlist and "history" in fromlist and name.endswith("dictation"):
            raise ImportError("no history in this build")
        return real(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_history)
    ctl._record_result(FakePipeline().run(SPEECH))  # does not raise


# -- first use -------------------------------------------------------------------------------


def test_undetermined_microphone_asks_and_explains(ctl):
    ctl.mic = "undetermined"
    assert ctl.dictation_start() is False
    assert len(ctl.asked) == 1 and texts(ctl) == [ASK_MIC]
    assert ctl.dictation.state == "idle"
    ctl.asked[0](True)  # granted: the microphone gets ready
    deadline = time.monotonic() + 2
    while "warm" not in ctl.recorder.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "warm" in ctl.recorder.calls


def test_denied_microphone_offers_settings(ctl):
    ctl.mic = "denied"
    assert ctl.dictation_start() is False
    toast = ctl.toasts[-1]
    assert (toast.text, toast.action) == (NO_MIC, "Open Settings")
    toast.on_action()
    assert ctl.opened == [controller.MIC_SETTINGS_URL]
    assert ctl.recorder.calls == []


class FakeDownload:
    def __init__(self, model, done=0):
        self.model = model
        self.total_bytes = 1000
        self.done_bytes = done
        self.running = False
        self.error = ""
        self.starts = 0

    def start(self, on_done=None):
        self.starts += 1
        self.running = True

    def active(self):
        return self.running

    def cancel(self):
        self.running = False


def test_missing_speech_model_downloads_first(ctl, monkeypatch):
    here = {models.S1_MINI.name}
    monkeypatch.setattr(models.ModelSet, "present", lambda self: self.name in here)
    stt = models.DOWNLOADS[models.PARAKEET_V2.name] = FakeDownload(models.PARAKEET_V2, done=430)
    monkeypatch.setattr(controller.Controller, "_watch_models", lambda self, needed, poll=1.0: None)
    assert ctl.dictation_start() is False
    assert stt.starts == 1 and ctl.dictation.state == "idle"
    assert ctl.toasts[-1] == Notice("Getting the speech model ready, 43%", seconds=3.0, key="models")
    assert ctl.dictation_download_progress() == pytest.approx(0.43)
    assert ctl.status_dict()["dictation"]["state"] == "idle"


def test_model_watch_reports_progress_then_ready(ctl, monkeypatch):
    here: set[str] = set()
    monkeypatch.setattr(models.ModelSet, "present", lambda self: self.name in here)
    stt = models.DOWNLOADS[models.PARAKEET_V2.name] = FakeDownload(models.PARAKEET_V2, done=500)
    stt.running = True
    sleeps = []

    def tick(seconds):
        sleeps.append(seconds)
        stt.running = False
        here.add(models.PARAKEET_V2.name)

    monkeypatch.setattr(controller.time, "sleep", tick)
    ctl._watch_models([models.PARAKEET_V2])
    assert texts(ctl) == ["Getting the speech model ready, 50%", MODEL_READY]
    assert all(t.key == "models" for t in ctl.toasts)
    assert ctl.dictation_download_progress() is None


def test_model_watch_reports_a_failed_download(ctl, monkeypatch):
    monkeypatch.setattr(models.ModelSet, "present", lambda self: False)
    stt = models.DOWNLOADS[models.PARAKEET_V2.name] = FakeDownload(models.PARAKEET_V2)
    stt.error = "Model download failed. Check your internet connection and try again."
    ctl._watch_models([models.PARAKEET_V2])
    assert texts(ctl) == [stt.error]


def test_missing_cleanup_model_does_not_block(ctl, monkeypatch):
    monkeypatch.setattr(models.ModelSet, "present", lambda self: self.name != models.S1_MINI.name)
    s1 = models.DOWNLOADS[models.S1_MINI.name] = FakeDownload(models.S1_MINI)
    monkeypatch.setattr(controller.cleaners, "effective_cleaner", lambda cfg, wait=True: "s1-mini")
    assert ctl.dictation_start() is True
    assert s1.starts == 1  # fetched in the background; this dictation uses the rules


@pytest.mark.skipif(not host.HAS_GLOBE_KEY, reason="the Globe key and its hint exist on the Mac only")
def test_globe_hint_shows_once(ctl, monkeypatch):
    monkeypatch.setattr("lyssna.mac.globe.globe_key_action", lambda: "emoji")
    ctl.dictation_start()
    deadline = time.monotonic() + 2
    while GLOBE_HINT not in texts(ctl) and time.monotonic() < deadline:
        time.sleep(0.01)
    toast = next(t for t in ctl.toasts if t.text == GLOBE_HINT)
    assert toast.action == "Open Settings"
    toast.on_action()
    assert ctl.opened == [controller.KEYBOARD_SETTINGS_URL]
    assert (config.STATE_DIR / controller.GLOBE_MARKER).exists()
    ctl._globe_checked = False  # as after a relaunch
    ctl.toasts.clear()
    ctl._globe_hint_once()
    time.sleep(0.05)
    assert ctl.toasts == []


def test_no_globe_hint_when_set_right_or_not_fn(ctl, monkeypatch):
    ctl.dictation_start()
    time.sleep(0.05)
    assert GLOBE_HINT not in texts(ctl)  # "Do Nothing" already
    assert not (config.STATE_DIR / controller.GLOBE_MARKER).exists()
    monkeypatch.setattr("lyssna.mac.globe.globe_key_action", lambda: "emoji")
    ctl._globe_checked = False
    ctl.cfg["dictation"]["hotkey"] = "right_option"
    ctl._globe_hint_once()
    time.sleep(0.05)
    assert GLOBE_HINT not in texts(ctl)


# -- local-only mode and engine choice -------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="asserts the Mac wording; test_windows_imports checks the Windows wording")
def test_local_only_moves_dictation_to_this_mac(ctl, fake_keychain):
    fake_keychain.items.update({"openai": "k", "anthropic": "k"})
    keystore.clear_cache()  # the launch read found none
    ctl.set_transcriber("openai")
    ctl.set_cleaner("haiku")
    assert ctl.dictation_privacy_note() == "Speech is sent to OpenAI, text is sent to Anthropic"
    ctl.set_local_only(True)
    saved = config.load()["dictation"]
    assert (saved["transcriber"], saved["cleaner"]) == ("parakeet", "s1-mini")
    assert ctl.dictation_privacy_note() == "Speech stays on this Mac"
    with pytest.raises(ValueError, match="local-only"):
        ctl.set_transcriber("openai")
    with pytest.raises(ValueError, match="local-only"):
        ctl.set_cleaner("haiku")


def test_hand_edited_cloud_engine_is_caught_before_a_dictation(ctl, fake_keychain):
    fake_keychain.items["openai"] = "k"
    config.update(**{"dictation.transcriber": "openai", "local_only": True})  # behind the app's back
    cfg = ctl._dictation_config()
    assert cfg["dictation"]["transcriber"] == "parakeet" and config.load()["dictation"]["transcriber"] == "parakeet"


def test_cleanup_off_and_on(ctl):
    ctl.set_cleaner("")
    assert config.load()["dictation"]["cleanup"] is False and ctl.current_cleaner() == ""
    ctl.set_cleaner("s1-mini")
    assert config.load()["dictation"]["cleanup"] is True


def test_unknown_engines_are_refused(ctl):
    with pytest.raises(ValueError):
        ctl.set_transcriber("nope")
    with pytest.raises(ValueError):
        ctl.set_cleaner("nope")


def test_dictation_layout_round_trip(ctl):
    assert ctl.dictation_layout() == {"dock": "", "y": -1.0}
    ctl.save_dictation_layout("left", 0.25)
    assert ctl.dictation_layout() == {"dock": "left", "y": 0.25}


# -- learning --------------------------------------------------------------------------------


def test_a_newly_learned_word_can_be_undone(ctl):
    from lyssna.dictation.vocabulary import shared

    ctl._learned("Matias", "Mattias")
    toast = ctl.toasts[-1]
    assert (toast.text, toast.action) == ("Learned: Mattias", "Undo")
    toast.on_action()
    assert "Mattias" not in shared().terms()


def test_a_known_word_is_learned_quietly(ctl):
    from lyssna.dictation.vocabulary import shared

    shared().add("Mattias")
    ctl._learned("Matias", "Mattias")
    assert ctl.toasts == [] and "Mattias" in shared().terms()


# -- the socket and shutdown -----------------------------------------------------------------


def test_socket_dictate_commands(ctl):
    resp = ctl.dispatch({"cmd": "dictate", "value": "start"})
    assert resp["ok"] is True and resp["status"]["dictation"]["state"] == "locked"
    resp = ctl.dispatch({"cmd": "dictate", "value": "cancel"})
    assert resp["ok"] is True and resp["status"]["dictation"]["state"] == "notice"
    settle(ctl)
    ctl.timers.fire()
    assert ctl.dispatch({"cmd": "dictate", "value": "start"})["ok"] is True
    resp = ctl.dispatch({"cmd": "dictate", "value": "stop"})
    assert resp["ok"] is True
    settle(ctl)
    assert ctl.pasted == ["Hello."]
    assert ctl.dispatch({"cmd": "dictate", "value": "stop"})["ok"] is False  # nothing recording
    assert ctl.dispatch({"cmd": "dictate", "value": "dance"})["ok"] is False


def test_shutdown_cancels_and_unloads(ctl, monkeypatch):
    from lyssna.dictation.cleaners import s1mini

    unloaded = []
    monkeypatch.setattr(s1mini, "unload", lambda: unloaded.append(1))
    download = models.DOWNLOADS[models.S1_MINI.name] = FakeDownload(models.S1_MINI)
    download.running = True
    ctl.dictation_start()
    ctl.shutdown()
    assert ctl.dictation.state == "idle"
    assert "cancel" in ctl.recorder.calls and ctl.dictation.watcher.stopped >= 1
    assert unloaded == [1] and not download.active()
    ctl.dictation.thread.join(2)
    assert not ctl.dictation.thread.is_alive()  # the session's worker ends with the controller


# -- retry a failed dictation, and Paste Last Dictation ----------------------------------------


class FlakyPipeline(FakePipeline):
    """Fails the first `failures` runs inside transcription, then works."""

    def __init__(self, failures=1, message="OpenAI did not answer."):
        self.failures, self.message = failures, message
        self.runs = 0

    def run(self, samples, sample_rate=16000):
        from lyssna.dictation.types import DictationError

        self.runs += 1
        if self.failures > 0:
            self.failures -= 1
            raise DictationError(self.message, retryable=True)
        return super().run(samples, sample_rate)


def fail_once(ctl, failures=1, message="OpenAI did not answer."):
    ctl.dictation.pipeline = FlakyPipeline(failures, message)
    ctl.dictation_start()
    ctl.dictation_stop()
    settle(ctl)


def history():
    from lyssna.dictation import history as history_mod

    return history_mod.shared()


def history_off():
    config.update(**{"dictation.history_days": 0})


def test_a_failed_dictation_is_kept_and_the_toast_offers_retry(ctl):
    fail_once(ctl)
    [entry] = history().entries()
    assert entry.failed and history().recordings.exists(entry.id)
    toast = ctl.toasts[-1]
    assert (toast.text, toast.action, toast.seconds) == ("OpenAI did not answer.", "Retry", controller.RETRY_SECONDS)
    assert controller.RETRY_SECONDS == 8.0
    assert texts(ctl).count("OpenAI did not answer.") == 1


def test_the_toast_retry_pastes_and_fills_the_entry(ctl):
    fail_once(ctl)
    ctl.toasts[-1].on_action()
    settle(ctl)
    assert ctl.pasted == ["Hello."]
    [entry] = history().entries()
    assert not entry.failed and entry.text == "Hello." and not history().recordings.exists(entry.id)
    assert history().stats()["dictations"] == 1


def test_a_retry_that_fails_again_toasts_with_retry(ctl):
    fail_once(ctl, failures=2, message="")
    assert ctl.toasts[-1].text == "Dictation failed."
    ctl.toasts[-1].on_action()
    settle(ctl)
    toast = ctl.toasts[-1]
    assert (toast.text, toast.action) == ("Retry failed. Try again.", "Retry")
    assert history().entries()[0].failed
    toast.on_action()
    settle(ctl)
    assert ctl.pasted == ["Hello."] and not history().entries()[0].failed


def test_history_retry_does_not_paste_and_reports_back(ctl):
    fail_once(ctl)
    entry_id = history().entries()[0].id
    done = []
    assert ctl.retry_dictation(entry_id, paste=False, on_done=done.append) is True
    settle(ctl)
    assert done == [True] and ctl.pasted == []
    assert history().entries()[0].text == "Hello."


def test_history_retry_failing_reports_back_and_its_toast_retries_without_paste(ctl):
    fail_once(ctl, failures=2)
    entry_id = history().entries()[0].id
    done = []
    ctl.retry_dictation(entry_id, paste=False, on_done=done.append)
    settle(ctl)
    assert done == [False] and history().entries()[0].failed
    ctl.toasts[-1].on_action()
    settle(ctl)
    assert ctl.pasted == [] and history().entries()[0].text == "Hello."


def test_no_retry_while_dictating(ctl):
    fail_once(ctl)
    entry_id = history().entries()[0].id
    ctl.dictation_start()
    assert ctl.retry_dictation(entry_id) is False
    assert ctl.toasts[-1].text == "Can't retry while dictating."
    ctl.dictation_cancel(quiet=True)
    settle(ctl)


def test_a_recording_that_is_gone_says_so(ctl):
    fail_once(ctl)
    entry = history().entries()[0]
    history().recordings.delete(entry.id)
    assert ctl.retry_dictation(entry.id) is False
    assert ctl.toasts[-1].text == "This recording is no longer available."
    assert history().entries() == []  # a row that can never be retried goes
    assert ctl.retry_dictation("nonexistent") is False


def test_mic_errors_offer_no_retry(ctl):
    ctl.mic = "denied"
    ctl.dictation_start()
    assert all(t.action != "Retry" for t in ctl.toasts)
    assert history().entries() == []


def test_with_history_off_the_recording_stays_in_memory_for_the_toast_only(ctl, monkeypatch):
    history_off()
    fail_once(ctl)
    assert history().entries() == []
    assert not (config.STATE_DIR / "dictation" / "failed").exists()
    toast = ctl.toasts[-1]
    assert toast.action == "Retry"
    toast.on_action()
    settle(ctl)
    assert ctl.pasted == ["Hello."] and ctl._held is None
    # past the toast's seconds the audio is gone
    fail_once(ctl)
    entry_id = ctl._held[0]
    ctl._forget_held(entry_id)
    assert ctl.retry_dictation(entry_id) is False
    assert ctl.toasts[-1].text == "This recording is no longer available."


def test_paste_last_pastes_the_latest_dictation(ctl):
    ctl.dictation_start()
    ctl.dictation_stop()
    settle(ctl)
    ctl.pasted.clear()
    ctl._paste_last()
    settle(ctl)
    assert ctl.pasted == ["Hello."]


def test_paste_last_uses_a_fixed_text(ctl):
    ctl.dictation_start()
    ctl.dictation_stop()
    settle(ctl)
    entry = history().entries()[0]
    history().update_text(entry.id, "Hello, fixed.")
    ctl._paste_last()
    settle(ctl)
    assert ctl.pasted[-1] == "Hello, fixed."


def test_paste_last_retries_a_failed_one_then_pastes(ctl):
    fail_once(ctl)
    ctl._paste_last()
    settle(ctl)
    assert ctl.pasted == ["Hello."] and history().entries()[0].text == "Hello."


def test_paste_last_with_nothing_yet(ctl):
    ctl._paste_last()
    assert ctl.toasts[-1].text == "Nothing to paste yet."
    assert ctl.pasted == []


def test_paste_last_with_history_off_uses_memory(ctl):
    history_off()
    ctl._paste_last()
    assert ctl.toasts[-1].text == "Nothing to paste yet."
    ctl.dictation_start()
    ctl.dictation_stop()
    settle(ctl)
    ctl.pasted.clear()
    ctl._paste_last()
    settle(ctl)
    assert ctl.pasted == ["Hello."] and history().entries() == []
    fail_once(ctl)
    ctl._paste_last()  # the latest failed: retried from memory, then pasted
    settle(ctl)
    assert ctl.pasted == ["Hello.", "Hello."]


def test_paste_last_says_copied_when_no_field_is_in_front(ctl):
    ctl.dictation_start()
    ctl.dictation_stop()
    settle(ctl)
    ctl.dictation.inserter = lambda text: "copied"
    ctl._paste_last()
    settle(ctl)
    assert ctl.toasts[-1] == Notice(f"Copied. Press {host.PASTE_SHORTCUT} to paste")


def test_paste_last_while_dictating(ctl):
    ctl.dictation_start()
    ctl.dictation_stop()
    settle(ctl)
    ctl.dictation_start()
    ctl._paste_last()
    assert ctl.toasts[-1].text == BUSY
    ctl.dictation_cancel(quiet=True)
    settle(ctl)


def test_paste_last_runs_off_the_callers_thread(ctl, monkeypatch):
    seen = []
    monkeypatch.setattr(ctl, "_paste_last", lambda: seen.append(threading.current_thread().name))
    ctl.paste_last()
    for _ in range(100):
        if seen:
            break
        time.sleep(0.01)
    assert seen == ["lyssna-paste-last"]


def test_paste_last_hotkey_default():
    assert config.DEFAULTS["hotkeys"]["paste_last"] == "ctrl+alt+v"
    assert 'paste_last = "ctrl+alt+v"' in config.TEMPLATE


def test_an_old_config_gets_the_paste_last_default():
    config.ensure_dirs()
    config.CONFIG_PATH.write_text('[hotkeys]\nspeak = "ctrl+alt+s"\n')
    assert config.load()["hotkeys"]["paste_last"] == "ctrl+alt+v"
