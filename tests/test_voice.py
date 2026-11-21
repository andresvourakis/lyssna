"""Voice picker: per-engine voice lists, persisting the choice, and restarting a reading
mid-way with the new voice. No real synthesis: providers here are fakes."""

import sys
import tomllib

import pytest

from lyssna import config
from lyssna.controller import Controller
from lyssna.player import Reader, Session, Status
from lyssna.providers import REGISTRY
from lyssna.providers.base import Provider


class FakeProvider(Provider):
    name = "fake"
    label = "Fake"
    needs_key = False

    def synthesize(self, text):
        raise AssertionError("tests must not synthesize")


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    text = config.TEMPLATE.replace('api_key = ""\nvoice = "Sarah"', 'api_key = "k"\nvoice = "Sarah"', 1)
    path.write_text(text.replace('provider = "kokoro"', 'provider = "inworld"', 1))
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.delenv("LYSSNA_PROVIDER", raising=False)
    monkeypatch.delenv("INWORLD_API_KEY", raising=False)
    c = Controller()
    assert c.cfg["provider"] == "inworld"
    return c


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS say voice")
def test_voice_lists_per_provider():
    ids = [v for v, _ in REGISTRY["inworld"].voices]
    assert ids == ["Sarah", "Jason", "Nate", "Luna", "Sofia"]
    assert REGISTRY["inworld-flash"].voices == REGISTRY["inworld"].voices
    for name in ("cartesia", "elevenlabs", "openai", "say"):
        assert REGISTRY[name].voices == []
    kokoro_ids = [v for v, _ in REGISTRY["kokoro"].voices]
    assert kokoro_ids[0] == REGISTRY["kokoro"].default_voice == "af_heart"


def test_voice_labels_are_unique_across_engines():
    """The menu finds a voice entry by its label, so two engines must never share one."""
    labels = {}
    for cls in REGISTRY.values():
        for vid, text in cls.voices:
            assert labels.setdefault(text, vid) == vid


def test_set_voice_persists_to_config(ctl):
    assert ctl.current_voice() == "Sarah"
    ctl.set_voice("Nate")
    assert ctl.current_voice() == "Nate"
    assert tomllib.loads(config.CONFIG_PATH.read_text())["inworld"]["voice"] == "Nate"


def test_flash_engine_writes_the_shared_inworld_section(ctl):
    ctl.set_provider("inworld-flash")
    ctl.set_voice("Sofia")
    data = tomllib.loads(config.CONFIG_PATH.read_text())
    assert data["inworld"]["voice"] == "Sofia" and "inworld-flash" not in data


def test_unknown_voice_rejected(ctl):
    with pytest.raises(ValueError):
        ctl.set_voice("Ashley")
    assert ctl.current_voice() == "Sarah"


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS say voice")
def test_engine_without_voices_has_no_picker(ctl):
    ctl.set_provider("say")
    assert ctl.voices() == []
    with pytest.raises(ValueError):
        ctl.set_voice("Nate")


def test_set_voice_restarts_an_active_reading(ctl, monkeypatch):
    calls = []
    monkeypatch.setattr(ctl.reader, "restart_from_current", lambda: calls.append(1) or True)
    ctl.set_voice("Jason")  # idle: nothing to restart
    assert calls == []
    ctl.reader._session = Session(chunks=["a"], provider=None)
    ctl.set_voice("Luna")
    assert calls == [1]
    ctl.set_voice("Luna")  # already current: no-op
    assert calls == [1]


def test_restart_from_current_keeps_index_and_uses_new_provider(monkeypatch):
    providers = iter([FakeProvider({"voice": "Nate"})])
    started = []
    r = Reader(lambda: next(providers))
    monkeypatch.setattr(r, "_fetch_loop", lambda s: started.append(("fetch", s)))
    monkeypatch.setattr(r, "_play_loop", lambda s: started.append(("play", s)))
    old = Session(chunks=["one.", "two.", "three."], provider=FakeProvider({"voice": "Sarah"}), index=1, paused=True,
                  durations={0: 2.0})
    r._session = old
    r._sessions = 3
    r.status = Status(state="paused", index=2, total=3, text="two.", position=2.5, duration=6.0, session=3)

    assert r.restart_from_current() is True
    new = r._session
    assert new is not old and old.stop.is_set()
    assert new.chunks == old.chunks and new.index == 1 and new.paused is True
    assert new.provider.voice == "Nate" and new.durations == {0: 2.0}
    assert r.status.index == 2 and r.status.total == 3 and r.status.state == "paused"
    assert r.status.session == 3  # same reading, so the UI doesn't re-place the player
    assert r.status.provider == "Fake · Nate"
    assert [kind for kind, _ in started] == ["fetch", "play"] and all(s is new for _, s in started)

    # The old session's threads can no longer write into the new status.
    r._set(session=old, state="idle", index=0)
    assert r.status.state == "paused" and r.status.index == 2


def test_restart_without_a_reading_does_nothing():
    r = Reader(lambda: FakeProvider({}))
    assert r.restart_from_current() is False
    assert r._session is None


def test_socket_voice_command(ctl):
    resp = ctl.dispatch({"cmd": "voice", "value": "Nate"})
    assert resp["ok"] is True and resp["status"]["voice"] == "Nate"
    resp = ctl.dispatch({"cmd": "voice", "value": "Nobody"})
    assert resp["ok"] is False and "Nobody" in resp["error"]
    assert ctl.status_dict()["voice"] == "Nate"
