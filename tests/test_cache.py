"""AudioCache: round trips, and the size cap evicting least recently used files."""

import os
import threading

from lyssna import config
from lyssna.cache import AudioCache
from lyssna.providers.base import Audio

MB = 1024 * 1024


def fill(cache: AudioCache, n: int, size: int) -> list[str]:
    """Put n entries of `size` bytes, each one second older than the next."""
    texts = [f"sentence {i}" for i in range(n)]
    for i, text in enumerate(texts):
        cache.put("k", text, Audio(b"x" * size, "mp3"))
        path = next(cache._path("k", text).parent.glob(cache._path("k", text).name + ".*"))
        os.utime(path, (1000 + i, 1000 + i))
    return texts


def test_put_then_get(tmp_path):
    cache = AudioCache(tmp_path)
    assert cache.get("k", "hello") is None
    cache.put("k", "hello", Audio(b"data", "mp3"))
    assert cache.get("k", "hello") == Audio(b"data", "mp3")
    assert cache.get("other-voice", "hello") is None


def test_default_root_is_the_configured_cache_dir():
    assert AudioCache().root == config.CACHE_DIR


def test_get_marks_the_file_recently_used(tmp_path):
    cache = AudioCache(tmp_path)
    (text,) = fill(cache, 1, 10)
    cache.get("k", text)
    path = next(tmp_path.rglob("*.mp3"))
    assert path.stat().st_mtime > 2000


def test_trim_does_nothing_under_the_cap(tmp_path):
    cache = AudioCache(tmp_path, max_mb=1)
    fill(cache, 3, 1000)
    assert cache.trim() == 0
    assert len(list(tmp_path.rglob("*.mp3"))) == 3


def test_trim_evicts_least_recently_used_down_to_90_percent(tmp_path):
    cache = AudioCache(tmp_path, max_mb=1, evict_every=1000)
    texts = fill(cache, 10, MB // 8)  # 1.25 MB against a 1 MB cap
    cache.get("k", texts[0])  # the oldest becomes the newest
    freed = cache.trim()
    left = sum(p.stat().st_size for p in tmp_path.rglob("*.mp3"))
    assert left <= 0.9 * MB and freed == 10 * (MB // 8) - left
    assert cache.get("k", texts[0]) is not None  # recently used survives
    assert cache.get("k", texts[1]) is None and cache.get("k", texts[2]) is None  # oldest go first
    assert cache.get("k", texts[9]) is not None


def test_zero_cap_means_no_limit(tmp_path):
    cache = AudioCache(tmp_path, max_mb=0)
    fill(cache, 3, 1000)
    assert cache.trim() == 0
    assert len(list(tmp_path.rglob("*.mp3"))) == 3


def test_trim_never_raises(tmp_path):
    assert AudioCache(tmp_path / "missing", max_mb=1).trim() == 0


def test_put_trims_in_the_background_every_n_writes(tmp_path, monkeypatch):
    cache = AudioCache(tmp_path, max_mb=1, evict_every=3)
    triggered = []
    monkeypatch.setattr(cache, "trim_in_background", lambda: triggered.append(1))
    for i in range(7):
        cache.put("k", f"t{i}", Audio(b"x", "mp3"))
    assert len(triggered) == 2


def test_trim_in_background_runs_trim_once_at_a_time(tmp_path, monkeypatch):
    cache = AudioCache(tmp_path, max_mb=1)
    started, release = threading.Event(), threading.Event()
    runs = []

    def slow_trim():
        runs.append(1)
        started.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(cache, "trim", slow_trim)
    cache.trim_in_background()
    assert started.wait(2)
    cache.trim_in_background()  # one is already running: skipped
    release.set()
    assert runs == [1]


def test_from_config(tmp_path):
    assert AudioCache.from_config({"cache_max_mb": 20}).max_bytes == 20 * MB
    assert AudioCache.from_config({"cache_max_mb": "lots"}).max_bytes == 500 * MB
    assert AudioCache.from_config({}).max_bytes == 500 * MB


def test_config_default_and_template_agree():
    import tomllib

    assert config.DEFAULTS["cache_max_mb"] == tomllib.loads(config.TEMPLATE)["cache_max_mb"] == 500


def test_switched_off_writes_and_reads_nothing(tmp_path):
    on = AudioCache(tmp_path)
    on.put("k", "kept", Audio(b"data", "mp3"))
    off = AudioCache(tmp_path, enabled=False)
    off.put("k", "hello", Audio(b"data", "mp3"))
    assert list(tmp_path.rglob("*.mp3")) == [next(tmp_path.rglob("*.mp3"))]  # only the one written while on
    assert off.get("k", "kept") is None and off.get("k", "hello") is None


def test_switched_off_never_trims(tmp_path, monkeypatch):
    cache = AudioCache(tmp_path, max_mb=1, enabled=False)
    monkeypatch.setattr(cache, "trim", lambda: (_ for _ in ()).throw(AssertionError("trimmed")))
    cache.trim_in_background()


def test_clear_still_works_when_off(tmp_path):
    AudioCache(tmp_path).put("k", "hello", Audio(b"data", "mp3"))
    AudioCache(tmp_path, enabled=False).clear()
    assert list(tmp_path.rglob("*.mp3")) == []


def test_switch_from_config():
    assert AudioCache.from_config({}).enabled is True
    assert AudioCache.from_config({"cache": True}).enabled is True
    for off in (False, 0, "false", "off"):
        assert AudioCache.from_config({"cache": off}).enabled is False
    assert AudioCache.from_config({"cache": False, "cache_max_mb": 0}).max_bytes == 0  # 0 is still "no limit"


def test_cache_switch_default_and_template_agree():
    import tomllib

    assert config.DEFAULTS["cache"] is tomllib.loads(config.TEMPLATE)["cache"] is True
