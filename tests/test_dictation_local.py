"""The engines on this Mac (Parakeet, S1-mini) and their model files, with the real models and
the network replaced by fakes: which model is picked, load once, download first, the S1-mini
prompt and piece splitting, idle unload, and the pinned files with their license details."""

import hashlib
import re
import struct
import sys
from types import SimpleNamespace
from typing import ClassVar

import httpx
import numpy as np
import pytest

from lyssna import download, host
from lyssna.dictation import models
from lyssna.dictation.cleaners import s1mini
from lyssna.dictation.models import PARAKEET_V2, PARAKEET_V3, S1_MINI, ModelFile, ModelSet
from lyssna.dictation.transcribers import TranscriberError, get_transcriber, parakeet

SAMPLES = (0.1 * np.sin(np.arange(16000) / 5)).astype(np.float32)


def put_files(model: ModelSet):
    model.directory.mkdir(parents=True, exist_ok=True)
    for f in model.files:
        model.path(f.name).write_bytes(b"x")


# -- model files ------------------------------------------------------------------------------------


def test_every_model_file_is_pinned():
    for model in models.ALL:
        assert model.files and model.total_bytes == sum(f.size for f in model.files)
        for f in model.files:
            assert f.url.startswith("https://huggingface.co/")
            assert re.search(r"/resolve/[0-9a-f]{40}/", f.url), f.url  # a commit, not a branch
            assert re.fullmatch(r"[0-9a-f]{64}", f.sha256) and f.size > 0


def test_s1_mini_carries_its_license_and_name():
    assert S1_MINI.credit() == "S1-mini by Superwhisper"
    assert {f.name for f in S1_MINI.files} == {"s1-mini-q4_k_m.gguf", "LICENSE", "NOTICE"}
    assert S1_MINI.license_url.endswith("/LICENSE") and "/blob/" in S1_MINI.license_url
    assert "Apache-2.0" in S1_MINI.license
    assert "S1-mini" in s1mini.S1MiniCleaner.label and "Superwhisper" in s1mini.S1MiniCleaner.label


def test_models_live_under_the_state_dir(isolated_paths):
    assert PARAKEET_V2.directory == isolated_paths / "state" / "dictation" / "models" / "parakeet-tdt-0.6b-v2"
    assert not PARAKEET_V2.present()
    put_files(PARAKEET_V2)
    assert PARAKEET_V2.present()


def test_download_fetches_and_verifies_each_file(monkeypatch):
    payloads = {"a.bin": b"alpha" * 100, "b.txt": b"beta"}
    model = ModelSet("tiny", "Tiny", "Test", tuple(
        ModelFile(n, f"https://example.invalid/{n}", hashlib.sha256(d).hexdigest(), len(d)) for n, d in payloads.items()),
        "MIT", "https://example.invalid/license", "https://example.invalid")

    def handler(request):
        return httpx.Response(200, content=payloads[request.url.path.lstrip("/")])

    monkeypatch.setattr(download, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    done = []
    model.download().start(on_done=done.append)
    assert model.download().wait() == "" and done == [""]
    assert model.present() and model.path("a.bin").read_bytes() == payloads["a.bin"]
    assert model.download().progress() is None  # finished
    assert models.ensure(model) == ""


def test_download_error_is_worded_for_a_model(monkeypatch):
    model = ModelSet("tiny", "Tiny", "Test", (ModelFile("a", "https://example.invalid/a", "0" * 64, 3),),
                     "MIT", "", "")
    monkeypatch.setattr(download, "make_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"bad"))))
    assert models.ensure(model) == "The downloaded model was damaged and has been deleted. Try again."


# -- Parakeet ----------------------------------------------------------------------------------------


class FakeASR:
    def __init__(self):
        self.calls = []

    def recognize(self, samples, sample_rate=16000):
        self.calls.append((len(samples), sample_rate))
        return " Hello there. "


@pytest.fixture
def fake_parakeet(monkeypatch):
    loads = []

    def make(model):
        loads.append(model.name)
        return FakeASR()

    monkeypatch.setattr(parakeet, "_make_model", make)
    return loads


def test_parakeet_picks_its_model():
    assert parakeet.model_for({}, "en") is PARAKEET_V2
    assert parakeet.model_for({"model": "v3"}, "en") is PARAKEET_V3
    assert parakeet.model_for({"model": "v2"}, "sv") is PARAKEET_V3  # v2 is English only
    assert get_transcriber("parakeet", {"dictation": {"language": "es"}}).model() is PARAKEET_V3


def test_parakeet_loads_once_and_keeps_the_model(fake_parakeet):
    put_files(PARAKEET_V2)
    t = get_transcriber("parakeet", {})
    assert t.transcribe(SAMPLES) == "Hello there."
    assert t.transcribe(SAMPLES) == "Hello there."
    assert get_transcriber("parakeet", {}).transcribe(SAMPLES) == "Hello there."
    assert fake_parakeet == ["parakeet-tdt-0.6b-v2"]


def test_parakeet_warm_up_loads_in_the_background(fake_parakeet):
    put_files(PARAKEET_V2)
    t = get_transcriber("parakeet", {})
    t.warm_up()
    assert t.ready(timeout=2)
    assert fake_parakeet == ["parakeet-tdt-0.6b-v2"]
    t.warm_up()  # already loaded: nothing to do
    assert fake_parakeet == ["parakeet-tdt-0.6b-v2"]


def test_parakeet_without_files_starts_the_download(monkeypatch, fake_parakeet):
    started = []
    monkeypatch.setattr(models.ModelDownload, "start", lambda self, on_done=None: started.append(self.model.name))
    t = get_transcriber("parakeet", {})
    assert not t.configured()
    with pytest.raises(TranscriberError, match="downloading"):
        t.transcribe(SAMPLES)
    assert started == ["parakeet-tdt-0.6b-v2"] and fake_parakeet == []
    t.prepare()
    assert started == ["parakeet-tdt-0.6b-v2"] * 2


def test_parakeet_takes_samples_only(fake_parakeet):
    put_files(PARAKEET_V2)
    with pytest.raises(TypeError):
        get_transcriber("parakeet", {}).transcribe("clip.wav")


def test_parakeet_never_sends_audio():
    t = get_transcriber("parakeet", {})
    assert not t.sends_audio_off_device and t.price_per_hour == 0 and not t.needs_key


# -- S1-mini -------------------------------------------------------------------------------------------


class FakeRunner:
    def __init__(self, outputs=None, finish="stop"):
        self.pieces, self.finish, self.closed = [], finish, False
        self.outputs = outputs

    def count_tokens(self, text):
        return len(text.split())

    def clean(self, transcript):
        self.pieces.append(transcript)
        out = self.outputs(transcript) if self.outputs else transcript.upper()
        return out, self.finish, self.count_tokens(transcript), self.count_tokens(out)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_s1(monkeypatch):
    runner = FakeRunner()
    loads = []

    def make(path):
        loads.append(path)
        return runner

    monkeypatch.setattr(s1mini, "_make_runner", make)
    monkeypatch.setattr(s1mini.S1MiniCleaner, "can_run", classmethod(lambda cls: True))
    put_files(S1_MINI)
    runner.loads = loads
    yield runner
    s1mini.unload()


def test_s1_cleans_with_the_loaded_model(fake_s1):
    c = s1mini.S1MiniCleaner({})
    assert c.clean("send it by friday", ["Lyssna"], 2.5) == "SEND IT BY FRIDAY"
    assert c.clean("again", [], 2.5) == "AGAIN"
    assert len(fake_s1.loads) == 1 and fake_s1.loads[0].endswith("s1-mini-q4_k_m.gguf")


def test_s1_cut_at_its_cap_is_no_output(fake_s1):
    fake_s1.finish = "length"
    assert s1mini.S1MiniCleaner({}).clean("send it", [], 2.5) is None


def test_s1_long_input_is_cleaned_in_pieces(fake_s1, monkeypatch):
    monkeypatch.setattr(s1mini, "MAX_PIECE_TOKENS", 6)
    text = "One two three four. Five six seven. Eight nine ten eleven twelve."
    out = s1mini.S1MiniCleaner({}).clean(text, [], 2.5)
    assert fake_s1.pieces == ["One two three four.", "Five six seven.", "Eight nine ten eleven twelve."]
    assert out == text.upper()


def test_split_pieces():
    count = lambda t: len(t.split())  # noqa: E731
    assert s1mini.split_pieces("a b. c d.", count, limit=10) == ["a b. c d."]
    assert s1mini.split_pieces("a b. c d. e f.", count, limit=4) == ["a b. c d.", "e f."]
    assert s1mini.split_pieces("a b c d e f g", count, limit=3) == ["a b c", "d e f", "g"]  # no sentence end


def test_s1_warm_up_loads_in_the_background_and_unloads(fake_s1):
    c = s1mini.S1MiniCleaner({})
    c.warm_up()
    assert c.ready(timeout=2) and len(fake_s1.loads) == 1
    assert s1mini._runner is fake_s1
    s1mini.unload()
    assert s1mini._runner is None and fake_s1.closed


def test_s1_idle_timer_is_pushed_back_on_use(fake_s1, monkeypatch):
    monkeypatch.setattr(s1mini, "IDLE_UNLOAD_S", 0.05)
    c = s1mini.S1MiniCleaner({})
    c.clean("send it", [], 2.5)
    s1mini._timer.join(1)
    assert s1mini._runner is None and fake_s1.closed


def test_s1_cannot_run_on_intel(monkeypatch):
    monkeypatch.setattr(host, "LOCAL_CLEANUP", None)
    assert not s1mini.S1MiniCleaner.can_run()
    assert s1mini.S1MiniCleaner({}).clean("send it", [], 2.5) is None


@pytest.mark.parametrize(("route", "installed", "expected"), [
    ("gpu", True, True), ("cpu", True, True), (None, True, False), ("gpu", False, False), ("cpu", False, False),
])
def test_s1_can_run_follows_the_host_capability(monkeypatch, route, installed, expected):
    monkeypatch.setattr(host, "LOCAL_CLEANUP", route)
    monkeypatch.setattr(s1mini, "packages_installed", lambda: installed)
    assert s1mini.S1MiniCleaner.can_run() is expected


@pytest.mark.parametrize(("cores", "threads"), [(1, 2), (2, 2), (4, 4), (8, 4), (16, 4)])
def test_s1_cpu_settings_use_the_physical_cores_within_two_to_four(cores, threads):
    assert s1mini.llama_settings("cpu", cores) == {"n_gpu_layers": 0, "n_threads": threads, "n_threads_batch": threads}


def test_s1_cpu_threads_fall_back_to_half_the_logical_processors(monkeypatch):
    monkeypatch.setattr(s1mini.os, "cpu_count", lambda: 12)
    assert s1mini.cpu_threads(None) == 4
    monkeypatch.setattr(s1mini.os, "cpu_count", lambda: 4)
    assert s1mini.cpu_threads(None) == 2
    monkeypatch.setattr(s1mini.os, "cpu_count", lambda: None)
    assert s1mini.cpu_threads(None) == 2


def test_s1_has_no_settings_where_it_does_not_run():
    with pytest.raises(ValueError):
        s1mini.llama_settings(None)


class FakeLlama:
    """Stands in for llama_cpp.Llama: records each call's keyword arguments; refuses flash
    attention when `refuse_flash` is set."""

    calls: ClassVar[list] = []
    refuse_flash = False

    def __init__(self, **kwargs):
        FakeLlama.calls.append(kwargs)
        if kwargs.get("flash_attn") and FakeLlama.refuse_flash:
            raise ValueError("Failed to create llama_context")


@pytest.fixture
def fake_llama(monkeypatch):
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=FakeLlama))
    monkeypatch.setattr(FakeLlama, "calls", [])
    monkeypatch.setattr(FakeLlama, "refuse_flash", False)
    monkeypatch.setattr(host, "cpu", SimpleNamespace(physical_cores=lambda: 8), raising=False)
    return FakeLlama


def test_s1_on_apple_silicon_loads_with_metal_exactly_as_before(fake_llama, monkeypatch):
    monkeypatch.setattr(host, "LOCAL_CLEANUP", "gpu")
    s1mini.load_llama("m.gguf")
    assert fake_llama.calls == [{"model_path": "m.gguf", "n_gpu_layers": -1, "n_ctx": 4096, "flash_attn": True, "verbose": False}]


def test_s1_on_apple_silicon_does_not_retry_without_flash_attention(fake_llama, monkeypatch):
    monkeypatch.setattr(host, "LOCAL_CLEANUP", "gpu")
    fake_llama.refuse_flash = True
    with pytest.raises(ValueError):
        s1mini.load_llama("m.gguf")
    assert len(fake_llama.calls) == 1


def test_s1_on_the_cpu_loads_with_explicit_threads(fake_llama, monkeypatch):
    monkeypatch.setattr(host, "LOCAL_CLEANUP", "cpu")
    s1mini.load_llama("m.gguf")
    assert fake_llama.calls == [{"model_path": "m.gguf", "n_gpu_layers": 0, "n_threads": 4, "n_threads_batch": 4,
                                 "n_ctx": 4096, "flash_attn": True, "verbose": False}]


def test_s1_on_the_cpu_retries_once_without_flash_attention(fake_llama, monkeypatch, caplog):
    monkeypatch.setattr(host, "LOCAL_CLEANUP", "cpu")
    fake_llama.refuse_flash = True
    with caplog.at_level("WARNING", logger="lyssna.dictation"):
        assert isinstance(s1mini.load_llama("m.gguf"), FakeLlama)
    assert [c["flash_attn"] for c in fake_llama.calls] == [True, False]
    assert all(c["n_gpu_layers"] == 0 and c["n_threads"] == 4 for c in fake_llama.calls)
    assert "without" in caplog.text


def test_windows_physical_cores_are_counted_from_the_processor_entries():
    from lyssna.win import cpu

    def entry(relationship, size):
        return struct.pack("<II", relationship, size) + bytes(size - 8)

    raw = entry(0, 48) + entry(0, 48) + entry(2, 80) + entry(0, 48)  # three cores and one cache entry
    assert cpu.count_cores(raw) == 3
    assert cpu.count_cores(b"") == 0


def test_s1_without_files_starts_the_download(monkeypatch):
    monkeypatch.setattr(s1mini.S1MiniCleaner, "can_run", classmethod(lambda cls: True))
    started = []
    monkeypatch.setattr(models.ModelDownload, "start", lambda self, on_done=None: started.append(self.model.name))
    assert s1mini.S1MiniCleaner({}).clean("send it", [], 2.5) is None
    assert started == ["s1-mini"]


def test_s1_prompt_follows_its_model_card():
    pytest.importorskip("jinja2")  # arrives with llama-cpp-python, which is only installed where S1-mini can run
    chat = s1mini.messages("um hello")
    assert chat[0] == {"role": "system", "content": s1mini.SYSTEM}
    assert chat[1]["content"] == "[Styling: semi-formal] [Structure: lists] [Context: general]\num hello"
    template = ("{% for m in messages %}<{{ m.role }}>{{ m.content }}{% endfor %}"
                "{% if add_generation_prompt %}<assistant>{% if not enable_thinking %}<think></think>{% endif %}{% endif %}")
    assert s1mini.render_chat(template, chat).endswith("um hello<assistant><think></think>")


def test_s1_template_runs_in_a_sandbox():
    pytest.importorskip("jinja2")
    from jinja2.exceptions import SecurityError

    with pytest.raises(SecurityError):
        s1mini.render_chat("{{ messages.__class__.__mro__ }}{{ ''.__class__.__subclasses__() }}", [])


@pytest.mark.skipif(sys.platform != "darwin", reason="reads the Mac's sysctl")
def test_mac_physical_cores_come_from_sysctl():
    from lyssna.mac import cpu

    cores = cpu.physical_cores()
    assert isinstance(cores, int) and cores > 0
