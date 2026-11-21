"""The Kokoro engine: the one-time voice download (hash check, damage, interruption, resume),
the espeak path guard, synthesis through a fake model, and how picking or falling back to
Kokoro behaves. The HTTP server is an httpx MockTransport and the model is a fake: nothing
here touches the network or loads onnxruntime."""

import hashlib
import io
import shutil
import sys
import tempfile
import threading
import wave

import httpx
import numpy as np
import pytest

from lyssna import config, download
from lyssna.controller import Controller
from lyssna.providers import REGISTRY, get_provider, kokoro
from lyssna.providers.base import ProviderError

MODEL_BYTES = b"model-bytes-" * 500
VOICES_BYTES = b"voice-bytes-" * 300


def model_file(name: str, data: bytes) -> kokoro.ModelFile:
    return kokoro.ModelFile(name, f"https://example.test/{name}", hashlib.sha256(data).hexdigest(), len(data))


@pytest.fixture
def small_files(monkeypatch):
    """Swap the real 140 MB files for tiny ones with the same names."""
    files = [model_file(kokoro.MODEL_FILE, MODEL_BYTES), model_file(kokoro.VOICES_FILE, VOICES_BYTES)]
    monkeypatch.setattr(kokoro, "FILES", files)
    monkeypatch.setattr(kokoro, "DOWNLOAD", kokoro.VoiceDownload())
    return {f.url: data for f, data in zip(files, (MODEL_BYTES, VOICES_BYTES), strict=True)}


class Broken(httpx.SyncByteStream):
    """A body that drops the connection after `cut` bytes."""

    def __init__(self, data: bytes, cut: int):
        self.data, self.cut = data, cut

    def __iter__(self):
        yield self.data[: self.cut]
        raise httpx.ReadError("connection reset")


@pytest.fixture
def server(monkeypatch):
    """A fake file server. `server.bodies` maps URL -> bytes; `server.cut` makes the next
    response for a URL break after that many bytes; `server.requests` records Range headers."""

    class Server:
        def __init__(self):
            self.bodies: dict[str, bytes] = {}
            self.cut: dict[str, int] = {}
            self.requests: list[tuple[str, str]] = []

        def handle(self, request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            rng = request.headers.get("range", "")
            self.requests.append((url, rng))
            data = self.bodies.get(url)
            if data is None:
                return httpx.Response(404)
            start = int(rng.removeprefix("bytes=").rstrip("-")) if rng else 0
            body = data[start:]
            headers = {"content-range": f"bytes {start}-{len(data) - 1}/{len(data)}"} if rng else {}
            status = 206 if rng else 200
            if url in self.cut:
                return httpx.Response(status, headers=headers, stream=Broken(body, self.cut.pop(url)))
            return httpx.Response(status, headers=headers, content=body)

    srv = Server()
    monkeypatch.setattr(download, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(srv.handle)))
    return srv


class FakeKokoro:
    """Stands in for kokoro_onnx.Kokoro: a short 440 Hz tone at 24 kHz."""

    def __init__(self, *args):
        self.args = args
        self.calls = []

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, lang))
        t = np.arange(2400) / 24000
        return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 24000


# -- download ------------------------------------------------------------------------------


def test_download_happy_path_verifies_and_reports_configured(small_files, server):
    server.bodies.update(small_files)
    p = get_provider("kokoro", {})
    assert not p.configured()
    done = []
    kokoro.DOWNLOAD.start(on_done=done.append)
    assert kokoro.DOWNLOAD.wait() == ""
    assert done == [""]
    assert p.configured()
    assert (kokoro.DATA_DIR / kokoro.MODEL_FILE).read_bytes() == MODEL_BYTES
    assert (kokoro.DATA_DIR / kokoro.VOICES_FILE).read_bytes() == VOICES_BYTES
    assert not list(kokoro.DATA_DIR.glob("*.part"))
    assert kokoro.DOWNLOAD.done_bytes == len(MODEL_BYTES) + len(VOICES_BYTES)


def test_hash_mismatch_keeps_nothing(small_files, server, tmp_path):
    f = kokoro.FILES[0]
    server.bodies[f.url] = MODEL_BYTES[:-1] + b"X"  # same size, different content
    dest = tmp_path / "out" / f.name
    with pytest.raises(download.DownloadError, match="damaged"):
        download.fetch(f.url, dest, f.sha256, f.size)
    assert not dest.exists() and not download.part_path(dest).exists()


def test_messages_name_what_is_downloaded(small_files, server, tmp_path):
    f = kokoro.FILES[0]
    server.bodies[f.url] = MODEL_BYTES[:-1] + b"X"
    with pytest.raises(download.DownloadError, match=r"^The downloaded voice was damaged"):
        download.fetch(f.url, tmp_path / "a" / f.name, f.sha256, f.size)
    with pytest.raises(download.DownloadError, match=r"^The downloaded speech model was damaged"):
        download.fetch(f.url, tmp_path / "b" / f.name, f.sha256, f.size, what="speech model")
    server.cut[f.url] = 10
    with pytest.raises(download.DownloadError, match=r"^Model download failed\. Check your internet"):
        download.fetch(f.url, tmp_path / "c" / f.name, f.sha256, f.size, what="model")


def test_interrupted_download_leaves_no_final_file_then_resumes(small_files, server, tmp_path):
    f = kokoro.FILES[0]
    server.bodies[f.url] = MODEL_BYTES
    server.cut[f.url] = 1000
    dest = tmp_path / f.name
    with pytest.raises(download.DownloadError, match="internet connection"):
        download.fetch(f.url, dest, f.sha256, f.size)
    assert not dest.exists()
    assert download.part_path(dest).stat().st_size == 1000

    seen = []
    download.fetch(f.url, dest, f.sha256, f.size, on_progress=seen.append)
    assert dest.read_bytes() == MODEL_BYTES
    assert server.requests[-1] == (f.url, "bytes=1000-")  # asked only for the rest
    assert seen[0] == 1000 and seen[-1] == f.size


def test_server_ignoring_the_range_starts_over(small_files, server, tmp_path, monkeypatch):
    f = kokoro.FILES[0]
    dest = tmp_path / f.name
    download.part_path(dest).write_bytes(b"stale-start")

    def full_body(request):
        return httpx.Response(200, content=MODEL_BYTES)

    monkeypatch.setattr(download, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(full_body)))
    download.fetch(f.url, dest, f.sha256, f.size)
    assert dest.read_bytes() == MODEL_BYTES


def test_cancel_stops_and_keeps_the_partial_file(small_files, server, tmp_path):
    f = kokoro.FILES[0]
    server.bodies[f.url] = MODEL_BYTES
    cancel = threading.Event()
    cancel.set()
    dest = tmp_path / f.name
    with pytest.raises(download.DownloadCancelled):
        download.fetch(f.url, dest, f.sha256, f.size, cancel=cancel)
    assert not dest.exists()


def test_offline_gives_a_plain_error(small_files, monkeypatch):
    def offline(request):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(download, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(offline)))
    kokoro.DOWNLOAD.start()
    assert "internet connection" in kokoro.DOWNLOAD.wait()
    assert not get_provider("kokoro", {}).configured()


def test_http_error_status_is_reported(small_files, server):
    kokoro.DOWNLOAD.start()  # server has no bodies: 404
    assert "404" in kokoro.DOWNLOAD.wait()


def test_progress_only_while_downloading(small_files, server):
    assert kokoro.DOWNLOAD.progress() is None
    gate = threading.Event()
    real = server.handle

    def slow(request):
        gate.wait(5)
        return real(request)

    server.handle = slow
    server.bodies.update(small_files)
    kokoro.DOWNLOAD.start()
    assert kokoro.DOWNLOAD.progress() == 0.0
    gate.set()
    kokoro.DOWNLOAD.wait()
    assert kokoro.DOWNLOAD.progress() is None


def test_start_with_files_present_does_not_download(small_files):
    kokoro.DATA_DIR.mkdir(parents=True)
    for f, data in zip(kokoro.FILES, (MODEL_BYTES, VOICES_BYTES), strict=True):
        (kokoro.DATA_DIR / f.name).write_bytes(data)
    done = []
    kokoro.DOWNLOAD.start(on_done=done.append)  # make_client would raise if it tried
    assert done == [""] and not kokoro.DOWNLOAD.active()


# -- synthesis -----------------------------------------------------------------------------


def test_first_reading_downloads_then_speaks(small_files, server, monkeypatch):
    server.bodies.update(small_files)
    made = []
    monkeypatch.setattr(kokoro, "_make_engine", lambda *a: made.append(FakeKokoro(*a)) or made[-1])
    monkeypatch.setattr(kokoro, "espeak_paths", lambda: ("/lib/libespeak-ng.dylib", "/data/espeak-ng-data"))
    p = get_provider("kokoro", {"kokoro": {"voice": "bm_george"}})
    audio = p.synthesize("Hello there.")
    assert audio.ext == "wav"
    with wave.open(io.BytesIO(audio.data)) as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1 and w.getnframes() == 2400
    assert made[0].calls == [("Hello there.", "bm_george", "en-gb")]
    p.synthesize("Again.")
    assert len(made) == 1  # the model is loaded once and reused
    model_path, voices_path, lib, data = made[0].args
    assert model_path.endswith(kokoro.MODEL_FILE) and voices_path.endswith(kokoro.VOICES_FILE)
    assert (lib, data) == ("/lib/libespeak-ng.dylib", "/data/espeak-ng-data")


def test_reading_fails_cleanly_when_the_download_fails(small_files, server):
    with pytest.raises(ProviderError, match="404"):
        get_provider("kokoro", {}).synthesize("Hello.")


def test_model_errors_become_provider_errors(small_files, server, monkeypatch):
    server.bodies.update(small_files)

    class Bad(FakeKokoro):
        def create(self, *a, **k):
            raise ValueError("Voice zz not found")

    monkeypatch.setattr(kokoro, "_make_engine", Bad)
    monkeypatch.setattr(kokoro, "espeak_paths", lambda: ("/lib/libespeak-ng.dylib", "/data/espeak-ng-data"))
    with pytest.raises(ProviderError, match="Voice zz not found"):
        get_provider("kokoro", {"kokoro": {"voice": "zz"}}).synthesize("Hi.")


def test_old_mlx_model_setting_is_ignored():
    p = get_provider("kokoro", {"kokoro": {"voice": "af_heart", "model": "mlx-community/Kokoro-82M-bf16"}})
    assert p.model == "kokoro-v1.0-int8"


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac model file")
def test_mac_keeps_the_int8_model():
    assert kokoro.FILES[0] == kokoro.ModelFile(
        "kokoro-v1.0.int8.onnx",
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.int8.onnx",
        "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c", 114_119_327)
    assert kokoro.MODEL_FILE == "kokoro-v1.0.int8.onnx"
    assert kokoro.DOWNLOAD_SIZE_TEXT == "140 MB" and kokoro.KokoroProvider.setup_hint == "140 MB download"
    assert "(about 140 MB)" in config.TEMPLATE


# -- espeak path guard ---------------------------------------------------------------------


def test_short_espeak_path_is_used_as_is(tmp_path):
    assert kokoro.espeak_paths(lambda: ("/lib/x.dylib", "/short/espeak-ng-data")) == ("/lib/x.dylib", "/short/espeak-ng-data")


@pytest.fixture
def short_data_dir(monkeypatch):
    """pytest's own temp dirs are too long for espeak, which is the point of the guard; this
    one is short (under /tmp) so the copy has somewhere valid to go."""
    d = tempfile.mkdtemp(prefix="ly", dir="/tmp")
    monkeypatch.setattr(kokoro, "DATA_DIR", kokoro.Path(d) / "kokoro")
    yield kokoro.DATA_DIR
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="a short temp dir under /tmp")
def test_long_espeak_path_is_copied_somewhere_short(tmp_path, short_data_dir):
    deep = tmp_path / ("d" * 160) / "espeak-ng-data"
    deep.mkdir(parents=True)
    (deep / "phontab").write_bytes(b"data")
    _, data = kokoro.espeak_paths(lambda: ("/lib/x.dylib", str(deep)))
    assert len(data.encode()) <= kokoro.MAX_ESPEAK_PATH
    assert data == str(kokoro.DATA_DIR / "espeak-ng-data")
    assert (kokoro.DATA_DIR / "espeak-ng-data" / "phontab").read_bytes() == b"data"
    # Second time: reuses the copy.
    (deep / "phontab").write_bytes(b"changed")
    assert kokoro.espeak_paths(lambda: ("/lib/x.dylib", str(deep)))[1] == data
    assert (kokoro.DATA_DIR / "espeak-ng-data" / "phontab").read_bytes() == b"data"


@pytest.mark.skipif(sys.platform != "darwin", reason="paths past the Windows MAX_PATH limit")
def test_espeak_path_still_too_long_raises_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(kokoro, "DATA_DIR", tmp_path / ("h" * 200))
    deep = tmp_path / ("d" * 160)
    deep.mkdir()
    with pytest.raises(ProviderError, match="too long"):
        kokoro.espeak_paths(lambda: ("/lib/x.dylib", str(deep)))
    assert not kokoro.DATA_DIR.exists()  # refused before copying anything


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac app bundle's layout")
def test_frozen_app_uses_the_bundled_espeak(monkeypatch):
    monkeypatch.setattr(kokoro.sys, "_MEIPASS", "/Applications/Lyssna.app/Contents/Frameworks", raising=False)
    lib, data = kokoro._bundled_espeak()
    assert lib == "/Applications/Lyssna.app/Contents/Frameworks/espeakng_loader/libespeak-ng.dylib"
    assert data == "/Applications/Lyssna.app/Contents/Frameworks/espeak-ng-data"


# -- privacy and engine choice -------------------------------------------------------------


@pytest.mark.parametrize(("name", "off_device"), [
    ("kokoro", False),
    pytest.param("say", False, marks=pytest.mark.skipif(sys.platform != "darwin", reason="the macOS say voice")),
    ("inworld", True), ("inworld-flash", True),
    ("cartesia", True), ("elevenlabs", True), ("openai", True),
])
def test_privacy_flag_per_engine(name, off_device):
    assert REGISTRY[name].sends_text_off_device is off_device


@pytest.fixture
def ctl(monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE, encoding="utf-8")
    return Controller()


@pytest.mark.skipif(sys.platform != "darwin", reason="asserts the Mac wording; test_windows_imports checks the Windows wording")
def test_privacy_note(ctl, fake_keychain):
    assert ctl.cfg["provider"] == "kokoro"
    assert ctl.privacy_note() == "Text stays on this Mac"
    fake_keychain.items["inworld"] = "k"
    ctl.cfg = config.update(provider="inworld")
    assert ctl.privacy_note() == "Text is sent to Inworld"


def test_new_install_starts_on_kokoro_without_downloading(ctl):
    assert ctl.cfg["provider"] == "kokoro" and ctl.fell_back_to == ""
    assert not kokoro.DOWNLOAD.active()


def test_no_key_falls_back_to_kokoro_at_launch(monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE.replace('provider = "kokoro"', 'provider = "cartesia"'), encoding="utf-8")
    c = Controller()
    assert c.cfg["provider"] == "kokoro" and c.fell_back_to == "kokoro"


def test_existing_key_user_is_unchanged(fake_keychain, monkeypatch):
    fake_keychain.items["cartesia"] = "ck"
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE.replace('provider = "kokoro"', 'provider = "cartesia"'), encoding="utf-8")
    c = Controller()
    assert c.cfg["provider"] == "cartesia" and c.fell_back_to == ""


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS say voice")
def test_picking_kokoro_starts_the_download(ctl, small_files, server):
    server.bodies.update(small_files)
    ctl.set_provider("say")
    assert not kokoro.DOWNLOAD.active() and not kokoro.files_present()
    ctl.set_provider("kokoro")
    assert kokoro.DOWNLOAD.wait() == ""
    assert kokoro.files_present()
    assert ctl.status_dict()["voice_download"] is None


def test_failed_download_after_picking_kokoro_is_shown(ctl, small_files, server, monkeypatch):
    notices = []
    monkeypatch.setattr(ctl, "notify", lambda message, ttl=3.0: notices.append(message))
    ctl.set_provider("kokoro")  # server has no files: 404
    kokoro.DOWNLOAD.wait()
    assert len(notices) == 1 and "404" in notices[0]
