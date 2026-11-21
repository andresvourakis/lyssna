"""The benchmark's arithmetic and its report, kept free of models, Windows and the network so it
is tested with made-up numbers: word error rate, real-time factor, budget checks, which model
files a repository offers, which speech variant to recommend, and the report text."""

from __future__ import annotations

import json
import re

BASELINE = "a"  # the app as it is today
WER_TOLERANCE = 0.01  # a variant may be this much worse than the baseline (one percentage point)
JSON_MARKER = "----- JSON -----"

_PUNCT_RE = re.compile(r"[^\w\s]")


# ----- word error rate ---------------------------------------------------------------------


def normalize_words(text: str) -> list[str]:
    """Lowercase words without punctuation. Hyphens and slashes split words ("real-time" is two
    words); apostrophes are dropped inside the word ("don't" becomes "dont")."""
    text = (text or "").lower().replace("-", " ").replace("/", " ").replace("_", " ")
    text = text.replace("'", "").replace("’", "")
    return _PUNCT_RE.sub(" ", text).split()


def word_errors(reference: list[str], hypothesis: list[str]) -> int:
    """Word-level edit distance: substitutions, insertions and deletions."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, 1):
        current = [i]
        for j, hyp in enumerate(hypothesis, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref != hyp)))
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Errors per reference word (0.0 is perfect; can exceed 1.0 with many insertions)."""
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return word_errors(ref, hyp) / len(ref)


# ----- speed -------------------------------------------------------------------------------


def real_time_factor(seconds: float | None, audio_seconds: float | None) -> float | None:
    """Seconds of work per second of audio (below 1 is faster than real time)."""
    if seconds is None or not audio_seconds:
        return None
    return round(seconds / audio_seconds, 4)


def best_run(runs: dict) -> float | None:
    """The fastest warm run, or the cold run when no warm run happened."""
    warm = [v for k, v in runs.items() if k.startswith("warm") and isinstance(v, (int, float))]
    if warm:
        return min(warm)
    cold = runs.get("cold")
    return cold if isinstance(cold, (int, float)) else None


def summarize_variant(variant: dict) -> dict:
    """Fill in a variant's overall numbers from its clips: real-time factor (all best runs over
    all audio) and word error rate (all errors over all reference words). Changes and returns
    the dict."""
    clips = [c for c in variant.get("clips", []) if "best_seconds" in c and c.get("best_seconds") is not None]
    seconds = sum(c["best_seconds"] for c in clips)
    audio = sum(c["audio_seconds"] for c in clips)
    errors = sum(c.get("word_errors", 0) for c in clips)
    words = sum(c.get("reference_words", 0) for c in clips)
    variant["real_time_factor"] = real_time_factor(seconds, audio) if clips else None
    variant["word_error_rate"] = round(errors / words, 4) if words else None
    return variant


def recommend(variants: dict, baseline: str = BASELINE, tolerance: float = WER_TOLERANCE,
              candidates: tuple[str, ...] | None = None) -> dict:
    """The fastest variant whose word error rate is no worse than the baseline's by more than
    `tolerance`. `candidates` limits the choice (the English variants); None means all.
    Returns {"key", "reason", "speedup"} or {"key": None, "reason"}."""
    base = variants.get(baseline) or {}
    base_wer, base_rtf = base.get("word_error_rate"), base.get("real_time_factor")
    if base_wer is None or base_rtf is None:
        return {"key": None, "reason": f"variant ({baseline}), the baseline, has no result"}
    usable = {}
    for key, v in variants.items():
        if candidates is not None and key not in candidates:
            continue
        wer, rtf = v.get("word_error_rate"), v.get("real_time_factor")
        if wer is None or rtf is None:
            continue
        if wer <= base_wer + tolerance + 1e-9:
            usable[key] = rtf
    if not usable:
        return {"key": None, "reason": "no candidate variant has a result"}
    key = min(usable, key=lambda k: (usable[k], k != baseline, k))
    speedup = round(base_rtf / usable[key], 2) if usable[key] else None
    points = f"{tolerance * 100:g} percentage point{'' if tolerance == 0.01 else 's'}"
    if key == baseline:
        reason = f"no other variant was faster than ({baseline}) with a word error rate within {points} of it"
    else:
        reason = (f"the fastest variant with a word error rate within {points} of ({baseline}), "
                  f"{speedup}x the speed of ({baseline})")
    return {"key": key, "reason": reason, "speedup": speedup}


# ----- the cleanup budget ------------------------------------------------------------------


def fits_budget(total_seconds: float | None, budget_seconds: float, finish_reason: str = "") -> bool:
    """Whether the app would use the model's output: it finished inside the budget and did not
    stop at its output cap (the app falls back to the rules then too)."""
    return total_seconds is not None and total_seconds <= budget_seconds and finish_reason != "length"


def cleanup_findings(by_threads: dict) -> dict:
    """From {threads: {"texts": [{"words", "fits", "total_seconds" or "skipped"}, ...]}}: the best thread
    count (most lengths fit, then the fewest skipped, then the least total time), how many lengths fit there, the share
    and the largest length that fits."""
    scored = []
    for threads, result in by_threads.items():
        # a length skipped because a shorter one was already too slow counts as not fitting
        texts = [t for t in result.get("texts", []) if t.get("total_seconds") is not None or t.get("skipped")]
        if not any(t.get("total_seconds") is not None for t in texts):
            continue
        fits = [t for t in texts if t.get("fits")]
        skipped = sum(1 for t in texts if t.get("skipped"))
        scored.append((-len(fits), skipped, sum(t.get("total_seconds") or 0.0 for t in texts), str(threads), texts, fits))
    if not scored:
        return {"best_threads": None, "fits": 0, "lengths": 0, "share": None, "largest_fit_words": None,
                "fits_all_up_to_words": None}
    scored.sort(key=lambda s: s[:3])
    _neg, _skipped, _total, threads, texts, fits = scored[0]
    up_to = None
    for t in sorted(texts, key=lambda t: t["words"]):  # the longest length below which everything fits
        if not t.get("fits"):
            break
        up_to = t["words"]
    return {"best_threads": threads, "fits": len(fits), "lengths": len(texts), "share": round(len(fits) / len(texts), 2),
            "largest_fit_words": max((t["words"] for t in fits), default=None), "fits_all_up_to_words": up_to}


# ----- repository files --------------------------------------------------------------------


def parse_hf_url(url: str) -> tuple[str, str]:
    """("owner/repo", commit) from https://huggingface.co/<owner>/<repo>/resolve/<commit>/<file>."""
    m = re.match(r"https://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/", url)
    if not m:
        raise ValueError(f"not a Hugging Face file URL: {url}")
    return m.group(1), m.group(2)


def pick_repo_files(tree: list[dict], precision: str) -> list[dict] | None:
    """The encoder and decoder files of one precision ("int8", "fp16", or "fp32" for the plain
    files), with their external data files and the vocabulary and config, from a Hugging Face
    tree listing ([{"path", "size"}]). None when the encoder or the decoder is missing."""
    by_path = {f["path"]: f for f in tree if f.get("type", "file") == "file"}
    infix = "" if precision == "fp32" else f".{precision}"
    picked = []
    for stem in ("encoder-model", "decoder_joint-model"):
        name = f"{stem}{infix}.onnx"
        if name not in by_path:
            return None
        picked.append(by_path[name])
        if f"{name}.data" in by_path:
            picked.append(by_path[f"{name}.data"])
    picked += [by_path[n] for n in ("vocab.txt", "config.json") if n in by_path]
    return picked


def onnx_asr_quantization(precision: str) -> str | None:
    """The value onnx_asr.load_model takes for these files: it looks for `encoder-model?<q>.onnx`,
    or `encoder-model.onnx` when quantization is None."""
    return None if precision == "fp32" else precision


# ----- the report --------------------------------------------------------------------------


def _fmt(value, digits: int = 3, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()  # noqa: E731
    return [line(header), line(["-" * w for w in widths]), *(line(r) for r in rows)]


def summary_lines(report: dict) -> list[str]:
    """The findings in plain English, for the top of the report and the job summary."""
    out = ["SUMMARY (findings on this machine, not decisions)"]
    m = report.get("machine") or {}
    if m:
        avx = m.get("numpy_avx_features")
        if isinstance(avx, dict):
            avx = [k for k, v in avx.items() if v]
        avx_text = ", ".join(avx) if isinstance(avx, list) else str(avx or "")
        out.append(f"- Machine: {m.get('cpu_name') or m.get('machine')}, {m.get('physical_cores', '-')} physical cores, "
                   f"{m.get('logical_processors', '-')} logical processors, {m.get('memory_gb', '-')} GB memory, "
                   f"AVX: {avx_text or 'none'}.")
    speech = report.get("speech") or {}
    variants = speech.get("variants") or {}
    if speech.get("skipped"):
        out.append(f"- Speech model: not measured ({speech['skipped']}).")
    elif speech.get("error") and not variants:
        out.append(f"- Speech model: the measurement failed: {speech['error']}")
    elif variants:
        base = variants.get(BASELINE) or {}
        if base.get("real_time_factor") is not None:
            out.append(f"- Speech model, the app today, ({BASELINE}) {base.get('label')}: real-time factor "
                       f"{_fmt(base['real_time_factor'])} (seconds of work per second of audio), word error rate "
                       f"{_pct(base.get('word_error_rate'))}.")
        elif BASELINE in variants:
            out.append(f"- Speech model, the app today ({BASELINE}): no result ({base.get('error') or base.get('skipped')}).")
        rec = speech.get("recommendation") or {}
        if rec.get("key"):
            v = variants[rec["key"]]
            long_clip = next((c for c in reversed(v.get("clips", [])) if c.get("best_seconds") is not None), None)
            clip_text = (f"; the {long_clip['clip']} clip ({long_clip['audio_seconds']:.1f} s of audio) took "
                         f"{long_clip['best_seconds']:.2f} s") if long_clip else ""
            out.append(f"- Recommended speech variant for this machine: ({rec['key']}) {v.get('label')}: real-time factor "
                       f"{_fmt(v.get('real_time_factor'))}, word error rate {_pct(v.get('word_error_rate'))}{clip_text}. "
                       f"Why: {rec.get('reason')}.")
        elif rec:
            out.append(f"- Recommended speech variant: none ({rec.get('reason')}).")
        other = variants.get("e") or {}
        if other.get("real_time_factor") is not None:
            out.append(f"- Other languages, (e) {other.get('label')}: real-time factor {_fmt(other['real_time_factor'])}, "
                       f"word error rate {_pct(other.get('word_error_rate'))} on the English clips.")
    cleanup = report.get("cleanup") or {}
    f = cleanup.get("findings") or {}
    if cleanup.get("skipped"):
        out.append(f"- Cleanup model (S1-mini by Superwhisper): not measured ({cleanup['skipped']}).")
    elif f.get("best_threads") is not None:
        if f["fits"] == 0:
            verdict = "no, it does not fit the budget at any measured length"
        elif f["fits"] == f["lengths"]:
            verdict = f"yes, it fits the budget at every measured length, up to {f['largest_fit_words']} words"
        else:
            upto = f["fits_all_up_to_words"]
            verdict = (f"partly: {f['fits']} of {f['lengths']} lengths fit; "
                       + (f"every length up to {upto} words fits" if upto else "the shortest length does not fit")
                       + f", the largest that fits is {f['largest_fit_words']} words")
        out.append(f"- Cleanup model (S1-mini by Superwhisper) on the CPU, {f['best_threads']} threads: {verdict}.")
    elif cleanup.get("error"):
        out.append(f"- Cleanup model (S1-mini by Superwhisper): the measurement failed: {cleanup['error']}")
    rules = cleanup.get("rules") or []
    if rules:
        worst = max(r["microseconds"] for r in rules)
        out.append(f"- Rules-only cleanup: at most {worst:.0f} microseconds per text, up to {rules[-1]['words']} words.")
    return out


def _speech_lines(speech: dict) -> list[str]:
    out = ["SPEECH TO TEXT (Parakeet TDT 0.6B through onnx-asr, CPU)"]
    if speech.get("skipped"):
        return [*out, f"Skipped: {speech['skipped']}"]
    if speech.get("error"):
        out.append(f"Error: {speech['error']}")
    variants = speech.get("variants") or {}
    rows = []
    for key, v in variants.items():
        status = v.get("error") or v.get("skipped") or ""
        rows.append([key, v.get("label", ""), _fmt(v.get("download_mb"), 0), _fmt(v.get("load_seconds"), 2),
                     _fmt(v.get("real_time_factor")), _pct(v.get("word_error_rate")), status[:110]])
    if rows:
        out += _table(["", "variant", "MB", "load s", "RTF", "WER", "note"], rows)
    rec = speech.get("recommendation") or {}
    if rec:
        out.append(f"Recommended: {rec.get('key') or 'none'} ({rec.get('reason')})")
    for key, v in variants.items():
        clips = v.get("clips") or []
        if not clips:
            continue
        out.append("")
        out.append(f"({key}) {v.get('label')}: files {', '.join(f['name'] for f in v.get('files', []))}")
        crow = []
        for c in clips:
            runs = c.get("runs") or {}
            crow.append([c["clip"], _fmt(c.get("audio_seconds"), 2), _fmt(runs.get("cold"), 3), _fmt(runs.get("warm_1"), 3),
                         _fmt(runs.get("warm_2"), 3), _fmt(c.get("real_time_factor")), _pct(c.get("word_error_rate")),
                         (c.get("error") or c.get("note") or "")[:60]])
        out += ["  " + line for line in _table(["clip", "audio s", "cold s", "warm 1 s", "warm 2 s", "RTF", "WER", "note"], crow)]
        for c in clips:
            if c.get("transcript") is not None:
                out.append(f"  {c['clip']} transcript: {c['transcript']}")
    files = speech.get("repository_files") or {}
    for repo, listing in files.items():
        out.append("")
        out.append(f"Files in {repo}:")
        if isinstance(listing, list):
            out += [f"  {f['path']}  {f.get('size', 0) / 2**20:.1f} MB" for f in listing]
        else:
            out.append(f"  {listing}")
    return out


def _cleanup_lines(cleanup: dict) -> list[str]:
    out = ["CLEANUP MODEL (S1-mini by Superwhisper through llama.cpp, CPU)"]
    for key in ("skipped", "install_note", "llama_cpp_version", "model_file", "error"):
        if cleanup.get(key):
            out.append(f"{key.replace('_', ' ').capitalize()}: {cleanup[key]}")
    for threads, r in (cleanup.get("threads") or {}).items():
        out.append("")
        out.append(f"{threads} threads: load {_fmt(r.get('load_seconds'), 2)} s, prompt prefix warm-up "
                   f"{_fmt(r.get('prefix_seconds'), 2)} s, flash attention: {r.get('flash_attn', '-')}"
                   + (f", error: {r['error']}" if r.get("error") else ""))
        rows = []
        for t in r.get("texts") or []:
            rows.append([t["words"], _fmt(t.get("first_token_seconds"), 3), _fmt(t.get("total_seconds"), 3),
                         _fmt(t.get("tokens_per_second"), 1), _fmt(t.get("budget_seconds"), 2),
                         "yes" if t.get("fits") else "no", t.get("finish_reason") or "",
                         (t.get("error") or t.get("skipped") or "")[:60]])
        if rows:
            out += ["  " + line for line in _table(["words", "first token s", "total s", "tokens/s", "budget s", "fits",
                                                    "finish", "note"], rows)]
        for t in r.get("texts") or []:
            if t.get("output") is not None:
                out.append(f"  {t['words']} words, output: " + t["output"].replace("\n", "\n      "))
    f = cleanup.get("findings") or {}
    if f.get("best_threads") is not None:
        out.append("")
        out.append(f"Best thread count: {f['best_threads']}; {f['fits']} of {f['lengths']} lengths fit "
                   f"(share {f['share']:.0%}); largest length that fits: {f['largest_fit_words'] or 'none'} words.")
    rules = cleanup.get("rules") or []
    if rules:
        out.append("")
        out.append("Rules-only cleanup (rules.light_clean), for reference:")
        out += ["  " + line for line in _table(["words", "microseconds"], [[r["words"], f"{r['microseconds']:.1f}"] for r in rules])]
    return out


def _audio_lines(audio: dict) -> list[str]:
    out = ["TEST AUDIO (synthesized, 16 kHz mono)"]
    for key in ("skipped", "source", "error", "fallback_error"):
        if audio.get(key):
            out.append(f"{key.replace('_', ' ').capitalize()}: {audio[key]}")
    for c in audio.get("clips") or []:
        out.append(f"  {c['name']}: {c['seconds']:.2f} s, {c['words']} words, synthesized in {c.get('synth_seconds', 0):.2f} s: "
                   f"{c['text']}")
    return out


def render(report: dict) -> str:
    """The whole report: the summary first, then each section, then the raw numbers as JSON."""
    lines = summary_lines(report)
    lines += ["", f"Lyssna dictation benchmark, version {report.get('bench_version')}, started {report.get('started')}, "
                  f"finished {report.get('finished') or '(still running or interrupted)'}"]
    m = report.get("machine") or {}
    if m:
        lines += ["", "MACHINE"] + [f"  {k}: {v}" for k, v in m.items()]
    if report.get("audio"):
        lines += ["", *_audio_lines(report["audio"])]
    if report.get("speech"):
        lines += ["", *_speech_lines(report["speech"])]
    if report.get("cleanup"):
        lines += ["", *_cleanup_lines(report["cleanup"])]
    lines += ["", JSON_MARKER, json.dumps(report, indent=2, default=str)]
    return "\n".join(lines) + "\n"
