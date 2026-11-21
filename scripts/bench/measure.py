"""The measurements: the machine, the test audio, the speech model variants and the cleanup
model. Each returns plain dicts for the report and records an error instead of raising, so one
failing variant never stops the others.

Downloads go to the work folder (a temp folder by default), never to the app's model folder.
Where the app's own model files are already downloaded (on a developer's Mac) they are read
from there, never written. Nothing here calls a paid service."""

from __future__ import annotations

import gc
import io
import os
import platform
import subprocess
import sys
import time
import traceback
import wave
from collections.abc import Callable
from pathlib import Path

from . import report as R
from .texts import CLIPS, TRANSCRIPTS

Say = Callable[[str], None]
DOWNLOAD_TIMEOUT = 60.0  # seconds without data before a download gives up
SAMPLE_RATE = 16000


def error_text(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def error_detail(e: BaseException) -> str:
    """The error with the last lines of its traceback, for the log."""
    tail = "".join(traceback.format_exception(type(e), e, e.__traceback__)[-3:]).strip()
    return f"{error_text(e)} | {tail[-600:]}"


# ----- the machine -------------------------------------------------------------------------


def _sysctl(name: str) -> str:
    return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5).stdout.strip()


def physical_cores() -> int | None:
    try:
        if sys.platform == "win32":
            from probe import winapi

            return winapi.physical_cores()
        if sys.platform == "darwin":
            return int(_sysctl("hw.physicalcpu"))
    except Exception:
        return None
    return os.cpu_count()


def machine() -> dict:
    """As the Kokoro benchmark reports it (scripts/probe/speed.py), plus the physical cores and
    memory on a Mac, Python and llama-cpp-python."""
    from probe.speed import machine_details

    out = machine_details()
    out["physical_cores"] = out.get("physical_cores") or physical_cores()
    if sys.platform == "darwin":
        try:
            out["cpu_name"] = _sysctl("machdep.cpu.brand_string") or out.get("cpu_name")
            out["memory_gb"] = round(int(_sysctl("hw.memsize")) / 2**30, 1)
        except Exception as e:
            out["memory_gb"] = error_text(e)
    out["python"] = platform.python_version()
    out["system"] = f"{platform.system()} {platform.release()} ({platform.version()})"
    from importlib.metadata import version

    for key, dist in (("onnx_asr_version", "onnx-asr"), ("llama_cpp_python_version", "llama-cpp-python")):
        try:
            out[key] = version(dist)
        except Exception:
            out[key] = "not installed"
    return out


# ----- downloads ---------------------------------------------------------------------------


def download(url: str, dest: Path, size: int | None, say: Say) -> dict:
    """Fetch one file into the work folder over plain HTTPS and check its size, as far as the
    app's own downloader goes without a pinned hash. A file already there with the right size
    is reused (the workflow caches the work folder)."""
    import httpx

    out: dict = {"name": dest.name, "url": url}
    if dest.is_file() and (size is None or dest.stat().st_size == size):
        out.update(size_mb=round(dest.stat().st_size / 2**20, 1), cached=True)
        return out
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    say(f"    downloading {dest.name} ({(size or 0) / 2**20:.0f} MB)")
    start = time.perf_counter()
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(DOWNLOAD_TIMEOUT, connect=15.0)) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    got = tmp.stat().st_size
    if size is not None and got != size:
        tmp.unlink()
        raise OSError(f"{dest.name}: got {got} bytes, expected {size}")
    tmp.replace(dest)
    out.update(size_mb=round(got / 2**20, 1), download_seconds=round(time.perf_counter() - start, 2), cached=False)
    return out


def repo_tree(repo: str, commit: str) -> list[dict]:
    """The files of a Hugging Face repository at a commit, from its public API."""
    import httpx

    r = httpx.get(f"https://huggingface.co/api/models/{repo}/tree/{commit}", timeout=30.0, follow_redirects=True)
    r.raise_for_status()
    return [{"path": f["path"], "size": f.get("size", 0), "type": f.get("type", "file")} for f in r.json()]


# ----- test audio --------------------------------------------------------------------------


def wav_samples(data: bytes):
    """Mono float32 samples and the sample rate from 16-bit WAV bytes."""
    import numpy as np

    with wave.open(io.BytesIO(data), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit audio, got {8 * width}-bit")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def to_16k(samples, rate: int):
    """Through the app's own streaming resampler (lyssna/dictation/recorder.py)."""
    import numpy as np

    from lyssna.dictation.recorder import Resampler

    r = Resampler(rate, SAMPLE_RATE)
    return np.concatenate((r.push(samples), r.flush())).astype(np.float32)


def _kokoro(work: Path):
    from lyssna.providers import kokoro

    if not kokoro.files_present():
        kokoro.DATA_DIR = work / "kokoro"  # download into the work folder, not the app's folder
    kokoro.ensure_downloaded()
    return f"Kokoro ({kokoro.MODEL_FILE}, voice af_heart, from {kokoro.DATA_DIR})", kokoro.KokoroProvider({})


def _windows_voice():
    from lyssna.providers.windows import WindowsVoiceProvider

    provider = WindowsVoiceProvider({})
    return f"the built-in Windows voice ({provider.voice or 'default'})", provider


def make_clips(work: Path, say: Say) -> tuple[dict, dict]:
    """The three clips as ({name: samples at 16 kHz}, report section)."""
    section: dict = {"clips": []}
    makers = [("kokoro", lambda: _kokoro(work))]
    if sys.platform == "win32":
        makers.append(("windows", _windows_voice))
    for which, make in makers:
        clips: dict = {}
        rows: list = []
        try:
            say(f"  making the test audio with {which}")
            source, provider = make()
            for name, text in CLIPS:
                start = time.perf_counter()
                audio = provider.synthesize(text)
                took = time.perf_counter() - start
                samples, rate = wav_samples(audio.data)
                samples = to_16k(samples, rate)
                clips[name] = samples
                rows.append({"name": name, "text": text, "words": len(text.split()), "seconds": round(len(samples) / SAMPLE_RATE, 3),
                             "source_rate": rate, "synth_seconds": round(took, 3)})
            section.update(source=source, clips=rows)
            return clips, section
        except Exception as e:
            say(f"  {which} could not make the audio: {error_text(e)}")
            section["error" if which == "kokoro" else "fallback_error"] = error_detail(e)
    return {}, section


# ----- speech to text ----------------------------------------------------------------------


def _variant_specs(cores: int | None) -> dict:
    from lyssna.dictation.transcribers import parakeet

    t = parakeet.THREADS
    return {
        "a": {"label": f"v2 int8, {t} threads (the app today)", "model": "v2", "precision": "int8", "threads": t},
        "b": {"label": f"v2 int8, {cores or '?'} threads (physical cores)", "model": "v2", "precision": "int8", "threads": cores},
        "c": {"label": f"v2 full precision, {t} threads", "model": "v2", "precision": "fp32", "threads": t},
        "d": {"label": f"v2 half precision, {t} threads", "model": "v2", "precision": "fp16", "threads": t},
        "e": {"label": f"v3 int8, {t} threads (other languages)", "model": "v3", "precision": "int8", "threads": t},
    }


def _model_set(name: str):
    from lyssna.dictation import models

    return models.PARAKEET_V2 if name == "v2" else models.PARAKEET_V3


def _variant_files(spec: dict, work: Path, trees: dict, say: Say) -> tuple[Path, list[dict]]:
    """Where the variant's files are, downloading them into the work folder if needed."""
    ms = _model_set(spec["model"])
    if spec["precision"] == "int8":
        if ms.present():
            return ms.directory, [{"name": f.name, "size_mb": round(f.size / 2**20, 1), "source": "the app's model folder (read only)"}
                                  for f in ms.files]
        folder = work / f"parakeet-{spec['model']}-int8"
        return folder, [download(f.url, folder / f.name, f.size, say) for f in ms.files]
    repo, commit = R.parse_hf_url(ms.files[0].url)
    if repo not in trees:
        trees[repo] = repo_tree(repo, commit)
    picked = R.pick_repo_files(trees[repo], spec["precision"])
    if picked is None:
        raise LookupError(f"the repository {repo} at {commit[:12]} has no {spec['precision']} encoder and decoder")
    folder = work / f"parakeet-{spec['model']}-{spec['precision']}"
    base = f"https://huggingface.co/{repo}/resolve/{commit}"
    return folder, [download(f"{base}/{f['path']}", folder / f["path"], f.get("size") or None, say) for f in picked]


def _load_engine(model: str, folder: Path, precision: str, threads: int):
    """As lyssna/dictation/transcribers/parakeet.py _make_model does, with the variant's files,
    quantization and thread count."""
    import onnx_asr
    import onnxruntime as rt

    from lyssna.dictation.transcribers import parakeet

    options = rt.SessionOptions()
    options.intra_op_num_threads = threads
    name = parakeet._ONNX_ASR_NAMES[_model_set(model).name]
    return onnx_asr.load_model(name, str(folder), quantization=R.onnx_asr_quantization(precision), sess_options=options,
                               providers=["CPUExecutionProvider"])


def run_variant(key: str, spec: dict, clips: dict, work: Path, trees: dict, slow_cutoff: float, say: Say) -> dict:
    out: dict = {"key": key, **spec, "clips": []}
    texts = dict(CLIPS)
    if not spec.get("threads"):
        out["skipped"] = "the physical core count is unknown"
        return out
    try:
        folder, files = _variant_files(spec, work, trees, say)
    except LookupError as e:
        out["skipped"] = str(e)
        return out
    except Exception as e:
        out["error"] = f"download failed: {error_detail(e)}"
        return out
    out["files"] = files
    out["folder"] = str(folder)
    out["download_mb"] = round(sum(f.get("size_mb", 0) for f in files), 1)
    out["download_seconds"] = round(sum(f.get("download_seconds", 0) for f in files), 1)
    engine = None
    try:
        start = time.perf_counter()
        engine = _load_engine(spec["model"], folder, spec["precision"], spec["threads"])
        out["load_seconds"] = round(time.perf_counter() - start, 3)
        say(f"    loaded in {out['load_seconds']:.2f} s")
        for name, samples in clips.items():
            audio_seconds = len(samples) / SAMPLE_RATE
            clip: dict = {"clip": name, "audio_seconds": round(audio_seconds, 3), "runs": {}}
            out["clips"].append(clip)
            try:
                transcript = None
                for label in ("cold", "warm_1", "warm_2"):
                    t0 = time.perf_counter()
                    text = engine.recognize(samples, sample_rate=SAMPLE_RATE)
                    clip["runs"][label] = round(time.perf_counter() - t0, 4)
                    transcript = (text or "").strip() if transcript is None else transcript
                    if label == "cold" and clip["runs"]["cold"] > slow_cutoff:
                        clip["note"] = f"cold run over {slow_cutoff:.0f} s, warm runs skipped"
                        break
                clip["best_seconds"] = R.best_run(clip["runs"])
                clip["real_time_factor"] = R.real_time_factor(clip["best_seconds"], audio_seconds)
                clip["transcript"] = transcript
                ref, hyp = R.normalize_words(texts[name]), R.normalize_words(transcript or "")
                clip["reference_words"] = len(ref)
                clip["word_errors"] = R.word_errors(ref, hyp)
                clip["word_error_rate"] = round(clip["word_errors"] / len(ref), 4) if ref else None
                say(f"    {name}: best {clip['best_seconds']:.3f} s, RTF {clip['real_time_factor']}, WER {clip['word_error_rate']}")
            except Exception as e:
                clip["error"] = error_detail(e)
                say(f"    {name} failed: {error_text(e)}")
    except Exception as e:
        out["error"] = f"load failed: {error_detail(e)}"
        say(f"    could not load: {error_text(e)}")
    finally:
        engine = None
        gc.collect()
    return R.summarize_variant(out)


def speech(clips: dict, work: Path, variants: list[str], slow_cutoff: float, say: Say, save: Callable[[], None],
           section: dict) -> dict:
    """Every chosen variant, side by side, then the recommendation. Saves the report after each."""
    specs = _variant_specs(physical_cores())
    trees: dict = {}
    section.setdefault("variants", {})
    for key in variants:
        if key not in specs:
            section["variants"][key] = {"key": key, "label": "unknown variant", "skipped": "not one of a, b, c, d, e"}
            continue
        say(f"  variant ({key}): {specs[key]['label']}")
        section["variants"][key] = run_variant(key, specs[key], clips, work, trees, slow_cutoff, say)
        section["repository_files"] = {repo: [f for f in tree if f["type"] == "file"] for repo, tree in trees.items()}
        save()
    if "c" in variants or "d" in variants:
        try:  # list the repository even when c and d were both skipped early
            ms = _model_set("v2")
            repo, commit = R.parse_hf_url(ms.files[0].url)
            if repo not in trees:
                trees[repo] = repo_tree(repo, commit)
            section["repository_files"] = {r: [f for f in t if f["type"] == "file"] for r, t in trees.items()}
        except Exception as e:
            section["repository_files"] = {"(listing failed)": error_text(e)}
    section["recommendation"] = R.recommend(section["variants"], candidates=("a", "b", "c", "d"))
    return section


# ----- the cleanup model -------------------------------------------------------------------


def _bench_runner_class():
    from llama_cpp import Llama

    from lyssna.dictation.cleaners import s1mini

    class BenchRunner(s1mini.Runner):
        """The app's Runner (lyssna/dictation/cleaners/s1mini.py) built with CPU settings: the
        constructor repeats the app's steps with n_gpu_layers=0 and a thread count; the prompt,
        template, saved prefix and clean() are the app's own."""

        def __init__(self, model_path: str, threads: int, flash_attn: bool):
            start = time.perf_counter()
            self.llm = Llama(model_path=model_path, n_gpu_layers=0, n_ctx=s1mini.N_CTX, n_threads=threads,
                             n_threads_batch=threads, flash_attn=flash_attn, verbose=False)
            self.load_seconds = round(time.perf_counter() - start, 3)
            template = self.llm.metadata["tokenizer.chat_template"]
            prefix, self._suffix = s1mini.render_chat(template, s1mini.messages(s1mini._MARKER)).split(s1mini._MARKER)
            self._prefix_tokens = self.llm.tokenize(prefix.encode("utf-8"), add_bos=True, special=True)
            self._prefix_state = None
            start = time.perf_counter()
            self._save_prefix()
            self.prefix_seconds = round(time.perf_counter() - start, 3)

        def app_clean(self, raw: str) -> tuple[str, str, int, int, int]:
            """As S1MiniCleaner.clean does with a loaded runner: split into pieces, clean each."""
            pieces = s1mini.split_pieces(raw, self.count_tokens, s1mini.MAX_PIECE_TOKENS)
            outputs, finish, n_in, n_out = [], "", 0, 0
            for piece in pieces:
                text, finish, i, o = self.clean(piece)
                n_in, n_out = n_in + i, n_out + o
                if finish == "length":
                    break
                outputs.append(text.strip())
            return " ".join(o for o in outputs if o), finish, n_in, n_out, len(pieces)

        def first_token(self, transcript: str) -> tuple[float | None, float]:
            """The same request as clean(), streamed: (seconds to the first token, seconds in all)."""
            tail = self.llm.tokenize((transcript + self._suffix).encode("utf-8"), add_bos=False, special=True)
            n_in = self.count_tokens(transcript)
            max_tokens = min(2 * n_in + 64, s1mini.MAX_OUTPUT_TOKENS)
            start = time.perf_counter()
            self._restore_prefix()
            first = None
            for _chunk in self.llm.create_completion(prompt=self._prefix_tokens + tail, max_tokens=max_tokens, temperature=0.0,
                                                     top_k=1, top_p=1.0, min_p=0.0, repeat_penalty=1.0, stop=["<|im_end|>"],
                                                     stream=True):
                if first is None:
                    first = time.perf_counter() - start
            return (round(first, 4) if first is not None else None), time.perf_counter() - start

    return BenchRunner


def _model_path(work: Path, say: Say) -> tuple[str, str]:
    from lyssna.dictation.models import S1_MINI

    gguf = S1_MINI.files[0]
    if S1_MINI.present():
        return str(S1_MINI.path(gguf.name)), "the app's model folder (read only)"
    info = download(gguf.url, work / "s1-mini" / gguf.name, gguf.size, say)
    source = "work folder (cached)" if info.get("cached") else f"downloaded in {info.get('download_seconds')} s"
    return str(work / "s1-mini" / gguf.name), source


def rules_timing() -> list[dict]:
    from lyssna.dictation.cleaners import rules

    out = []
    for text in TRANSCRIPTS:
        n = 200
        start = time.perf_counter()
        for _ in range(n):
            rules.light_clean(text)
        out.append({"words": len(text.split()), "microseconds": round((time.perf_counter() - start) / n * 1e6, 1),
                    "output": rules.light_clean(text)})
    return out


def cleanup(work: Path, thread_counts: list[int], say: Say, save: Callable[[], None], section: dict,
            slow_cutoff: float = 60.0) -> dict:
    """The cleanup model at each thread count, then the findings. Saves after each step. When
    one length takes longer than `slow_cutoff` seconds, the longer ones are skipped at that
    thread count (they count as not fitting)."""
    from lyssna.dictation import pipeline
    from lyssna.dictation.cleaners import s1mini

    section["rules"] = rules_timing()
    try:
        import jinja2  # noqa: F401
        import llama_cpp

        section["llama_cpp_version"] = getattr(llama_cpp, "__version__", "?")
    except Exception as e:
        section["skipped"] = f"llama_cpp or jinja2 cannot be imported here: {error_text(e)}"
        return section
    try:
        path, source = _model_path(work, say)
        section["model_file"] = f"{path} ({source})"
    except Exception as e:
        section["error"] = f"the model could not be downloaded: {error_detail(e)}"
        return section
    BenchRunner = _bench_runner_class()
    cleaner = s1mini.S1MiniCleaner({})
    section.setdefault("threads", {})
    flash = True
    for threads in thread_counts:
        say(f"  cleanup model, {threads} threads")
        result: dict = {"texts": []}
        section["threads"][str(threads)] = result
        runner = None
        try:
            try:
                runner = BenchRunner(path, threads, flash)
                result["flash_attn"] = "on" if flash else "off (unsupported here)"
            except Exception as e:
                if not flash:
                    raise
                say(f"    could not load with flash attention ({error_text(e)}); trying without")
                section["flash_attn_error"] = error_text(e)
                flash = False
                runner = BenchRunner(path, threads, flash)
                result["flash_attn"] = "off (unsupported here)"
            result["load_seconds"], result["prefix_seconds"] = runner.load_seconds, runner.prefix_seconds
            say(f"    loaded in {runner.load_seconds:.2f} s, prefix in {runner.prefix_seconds:.2f} s")
            too_slow = ""
            for text in TRANSCRIPTS:
                row: dict = {"words": len(text.split()), "budget_seconds": round(pipeline.cleanup_budget(cleaner, text), 3)}
                result["texts"].append(row)
                if too_slow:
                    row.update(skipped=too_slow, fits=False)
                    continue
                try:
                    start = time.perf_counter()
                    output, finish, n_in, n_out, pieces = runner.app_clean(text)
                    total = time.perf_counter() - start
                    row.update(total_seconds=round(total, 4), output=output, finish_reason=finish, tokens_in=n_in,
                               tokens_out=n_out, pieces=pieces)
                    row["fits"] = R.fits_budget(total, row["budget_seconds"], finish)
                    if total > slow_cutoff:
                        too_slow = f"skipped: {row['words']} words took {total:.0f} s at this thread count"
                    else:
                        first, stream_total = runner.first_token(text)
                        row["first_token_seconds"] = first
                        gen = stream_total - (first or 0.0)
                        row["tokens_per_second"] = round(n_out / gen, 1) if gen > 0 and n_out else None
                    first_text = f", first token {row['first_token_seconds']:.3f} s" if row.get("first_token_seconds") else ""
                    say(f"    {row['words']} words: {total:.2f} s (budget {row['budget_seconds']:.2f} s{first_text}), "
                        f"fits: {row['fits']}")
                except Exception as e:
                    row["error"] = error_detail(e)
                    say(f"    {row['words']} words failed: {error_text(e)}")
                save()
        except Exception as e:
            result["error"] = error_detail(e)
            say(f"    failed: {error_text(e)}")
        finally:
            if runner is not None:
                try:
                    runner.close()
                except Exception as e:
                    result["close_error"] = error_text(e)
            runner = None
            gc.collect()
        save()
    section["findings"] = R.cleanup_findings(section["threads"])
    return section


def cleanup_thread_counts() -> list[int]:
    """Physical cores and logical processors, without repeats."""
    counts = [c for c in (physical_cores(), os.cpu_count()) if c]
    return list(dict.fromkeys(counts)) or [4]
