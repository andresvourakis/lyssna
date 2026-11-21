"""How fast the Lyssna voice (Kokoro) is on this machine, and what kind of machine it is.

Why: on GitHub's Windows machine Kokoro took about three seconds of work per second of speech,
where an Apple silicon Mac is several times faster than real time. These measurements tell a
cold first run apart from a weak machine and from a slow model setting:

- machine_details(): the processor, its cores and features, the memory, and onnxruntime.
- kokoro_benchmark(): exactly what the app does (lyssna/providers/kokoro.py), timed three
  times on the same sentence (cold, then warm twice), plus one short and one long sentence.
- engine_comparison(): self-test only. Other model files and session settings, built here
  directly so the app's code stays untouched, each timed warm on the same sentence. The
  extra models are free downloads from the kokoro-onnx GitHub releases into the temp folder,
  never into the app's folder.

Every function returns plain dicts for the report. Nothing here calls a paid service."""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
from pathlib import Path

from .analysis import avx_features, fastest, timing_row, wav_info

SENTENCE = "Lyssna reads the text you select, out loud, in a clear and natural voice."
SHORT_SENTENCE = "Press the button to listen."
LONG_SENTENCE = (
    "Lyssna reads the text you select out loud, so you can rest your eyes and keep working. "
    "Select a paragraph in your browser, an email or a document, then press the shortcut or the "
    "round button that appears next to it. The voice runs on this computer, it costs nothing, "
    "and the text you select never leaves your machine while it is being read to you."
)

V10_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
EXTRA_MODELS = (
    # (variant name, file name)
    ("fp32_default", "kokoro-v1.0.onnx"),
    ("fp16_default", "kokoro-v1.0.fp16.onnx"),
)
VOICE = "af_heart"  # KokoroProvider.default_voice
DOWNLOAD_TIMEOUT = 60.0  # seconds without data before a download gives up


# ----- the machine -------------------------------------------------------------------------


def _numpy_features() -> dict:
    try:
        from numpy._core._multiarray_umath import __cpu_features__ as features
    except ImportError:
        from numpy.core._multiarray_umath import __cpu_features__ as features
    return dict(features)


def machine_details() -> dict:
    out: dict = {"machine": platform.machine(), "logical_processors": os.cpu_count()}
    if sys.platform == "win32":
        from . import winapi

        for key, fn in (("cpu_name", winapi.cpu_name), ("physical_cores", winapi.physical_cores),
                        ("processor_features", winapi.processor_features)):
            try:
                out[key] = fn()
            except Exception as e:
                out[key] = f"{type(e).__name__}: {e}"
        try:
            out["memory_gb"] = round(winapi.total_memory_bytes() / 2**30, 1)
        except Exception as e:
            out["memory_gb"] = f"{type(e).__name__}: {e}"
    else:
        out["cpu_name"] = platform.processor()
    try:
        out["numpy_avx_features"] = avx_features(_numpy_features())
    except Exception as e:
        out["numpy_avx_features"] = f"{type(e).__name__}: {e}"
    try:
        import onnxruntime

        out["onnxruntime_version"] = onnxruntime.__version__
        out["onnxruntime_providers"] = onnxruntime.get_available_providers()
    except Exception as e:
        out["onnxruntime_version"] = f"not importable: {type(e).__name__}: {e}"
    return out


def physical_cores() -> int | None:
    if sys.platform == "win32":
        try:
            from . import winapi

            return winapi.physical_cores()
        except Exception:
            return None
    return None


# ----- the app's own setup -----------------------------------------------------------------


def _timed_synthesis(provider, text: str):
    start = time.perf_counter()
    audio = provider.synthesize(text)
    return audio, timing_row(time.perf_counter() - start, wav_info(audio.data))


def kokoro_benchmark(text: str = SENTENCE):
    """The app's engine (lyssna.providers.kokoro), timed: the model load, then the same
    sentence three times (cold, warm_1, warm_2), then a short and a long sentence once each.
    Returns the first audio (for playing) and the timings."""
    from lyssna.providers import kokoro

    info: dict = {"model": kokoro.MODEL_FILE, "sentence_words": len(text.split())}
    start = time.perf_counter()
    kokoro._get_engine()
    info["model_load_seconds"] = round(time.perf_counter() - start, 3)
    provider = kokoro.KokoroProvider({})
    first = None
    for label in ("cold", "warm_1", "warm_2"):
        audio, row = _timed_synthesis(provider, text)
        info[label] = row
        first = first or audio
    for label, sentence in (("short", SHORT_SENTENCE), ("long", LONG_SENTENCE)):
        _audio, row = _timed_synthesis(provider, sentence)
        info[label] = {"words": len(sentence.split()), **row}
    info.update(wav_info(first.data))  # the played audio, as before (seconds, frames, rate)
    return first, info


# ----- other settings (self-test only) -----------------------------------------------------


def _download(url: str, dest: Path) -> dict:
    """Fetch one file into `dest` (in the temp folder). Plain HTTPS, no retries."""
    import httpx

    out: dict = {"url": url}
    if dest.is_file():
        out.update(already_there=True, size_mb=round(dest.stat().st_size / 2**20, 1))
        return out
    head = httpx.head(url, follow_redirects=True, timeout=httpx.Timeout(DOWNLOAD_TIMEOUT, connect=15.0))
    out["head_status"] = head.status_code
    if head.status_code != 200:
        out["available"] = False
        return out
    out["available"] = True
    tmp = dest.with_name(dest.name + ".part")
    start = time.perf_counter()
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(DOWNLOAD_TIMEOUT, connect=15.0)) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.replace(dest)
    out["download_seconds"] = round(time.perf_counter() - start, 2)
    out["size_mb"] = round(dest.stat().st_size / 2**20, 1)
    return out


def _engine(model_path: str, threads: int | None):
    """A Kokoro engine the way kokoro_onnx builds one, with optional session settings."""
    import onnxruntime as rt
    from kokoro_onnx import EspeakConfig, Kokoro
    from kokoro_onnx.session import resolve_providers

    from lyssna.providers import kokoro

    lib, data = kokoro.espeak_paths()
    options = None
    if threads:
        options = rt.SessionOptions()
        options.intra_op_num_threads = threads
    session = rt.InferenceSession(model_path, sess_options=options, providers=resolve_providers())
    return Kokoro.from_session(session, str(kokoro.DATA_DIR / kokoro.VOICES_FILE),
                               espeak_config=EspeakConfig(lib_path=lib, data_path=data))


def _time_variant(model_path: str, threads: int | None, text: str) -> dict:
    """Build the session, synthesize once to warm it up, then time two warm runs."""
    import numpy as np

    start = time.perf_counter()
    engine = _engine(model_path, threads)
    out: dict = {"model": os.path.basename(model_path), "intra_op_num_threads": threads or "default",
                 "load_seconds": round(time.perf_counter() - start, 3)}
    try:
        runs = []
        for label in ("cold", "warm_1", "warm_2"):
            start = time.perf_counter()
            pcm, rate = engine.create(text, voice=VOICE, speed=1.0, lang="en-us")
            took = time.perf_counter() - start
            seconds = len(np.asarray(pcm)) / float(rate) if rate else 0.0
            row = timing_row(took, {"seconds": round(seconds, 3)})
            out[label] = row
            if label != "cold":
                runs.append(row)
        best = min(runs, key=lambda r: r["synthesis_seconds"])
        out["warm_best"] = best
        out["real_time_factor"] = best["real_time_factor"]
    finally:
        engine = None
    return out


def engine_comparison(text: str = SENTENCE, download: bool = True, say=print) -> dict:
    """Self-test only: the same sentence through several model files and session settings,
    side by side. Never raises for one variant; its error goes in the report."""
    from lyssna.providers import kokoro

    out: dict = {"sentence_words": len(text.split())}
    cores = physical_cores()
    variants: list[tuple[str, str | None, int | None]] = [
        ("int8_default", str(kokoro.DATA_DIR / kokoro.MODEL_FILE), None),
        (f"int8_threads_{cores}", str(kokoro.DATA_DIR / kokoro.MODEL_FILE), cores) if cores else
        ("int8_threads_physical", None, None),
    ]
    folder = Path(tempfile.gettempdir()) / "lyssna-probe-models"
    downloads: dict = {}
    for name, file_name in EXTRA_MODELS:
        if not download:
            downloads[file_name] = {"skipped": "downloads turned off"}
            variants.append((name, None, None))
            continue
        folder.mkdir(parents=True, exist_ok=True)
        say(f"    downloading {file_name}")
        try:
            downloads[file_name] = _download(f"{V10_RELEASE}/{file_name}", folder / file_name)
        except Exception as e:
            downloads[file_name] = {"error": f"{type(e).__name__}: {e}"}
        ok = (folder / file_name).is_file()
        variants.append((name, str(folder / file_name) if ok else None, None))
    out["downloads"] = downloads
    results: dict = {}
    for name, path, threads in variants:
        if path is None:
            results[name] = {"skipped": "no model file (see downloads), or the core count is unknown"}
            continue
        say(f"    timing {name}")
        try:
            results[name] = _time_variant(path, threads, text)
        except Exception as e:
            results[name] = {"error": f"{type(e).__name__}: {e}"}
    out["variants"] = results
    out["side_by_side"] = {n: r.get("real_time_factor") for n, r in results.items()}
    out["fastest"] = fastest({n: r for n, r in results.items() if "real_time_factor" in r})
    return out
