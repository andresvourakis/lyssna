"""The dictation benchmark's pure parts, with made-up numbers: word error rate, real-time
factor, the cleanup budget, which repository files a variant uses, the recommended variant and
the report text. The benchmark itself loads models and runs on GitHub's Windows machine."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bench import report as R
from bench import texts


def test_normalize_words_drops_case_and_punctuation():
    assert R.normalize_words("Hello, World! It's real-time.") == ["hello", "world", "its", "real", "time"]
    assert R.normalize_words("") == []


def test_word_errors_counts_substitutions_insertions_and_deletions():
    assert R.word_errors(["a", "b", "c"], ["a", "b", "c"]) == 0
    assert R.word_errors(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert R.word_errors(["a", "b", "c"], ["a", "c"]) == 1
    assert R.word_errors(["a", "b"], ["a", "b", "c", "d"]) == 2
    assert R.word_errors([], ["a"]) == 1


def test_word_error_rate():
    assert R.word_error_rate("Please send the report.", "please send the report") == 0.0
    assert R.word_error_rate("one two three four", "one too three") == 0.5
    assert R.word_error_rate("", "") == 0.0
    assert R.word_error_rate("", "noise") == 1.0


def test_real_time_factor():
    assert R.real_time_factor(1.0, 10.0) == 0.1
    assert R.real_time_factor(None, 10.0) is None
    assert R.real_time_factor(1.0, 0.0) is None


def test_best_run_prefers_warm_runs():
    assert R.best_run({"cold": 3.0, "warm_1": 1.2, "warm_2": 1.1}) == 1.1
    assert R.best_run({"cold": 3.0}) == 3.0
    assert R.best_run({}) is None


def _variant(rtf_seconds: float, errors: int) -> dict:
    clips = [{"clip": "3s", "audio_seconds": 3.0, "best_seconds": rtf_seconds * 3, "word_errors": errors, "reference_words": 50},
             {"clip": "10s", "audio_seconds": 10.0, "best_seconds": rtf_seconds * 10, "word_errors": 0, "reference_words": 50}]
    return R.summarize_variant({"label": "x", "clips": clips})


def test_summarize_variant_pools_clips():
    v = _variant(0.2, 3)
    assert v["real_time_factor"] == 0.2
    assert v["word_error_rate"] == 0.03
    empty = R.summarize_variant({"clips": [{"clip": "3s", "audio_seconds": 3.0, "error": "boom"}]})
    assert empty["real_time_factor"] is None and empty["word_error_rate"] is None


def test_recommend_picks_the_fastest_within_one_point():
    variants = {"a": _variant(0.5, 2), "b": _variant(0.4, 2), "c": _variant(0.1, 3), "d": _variant(0.05, 10)}
    # a: 2%, c: 3% (exactly one point worse, allowed), d: 10% (too many errors)
    rec = R.recommend(variants)
    assert rec["key"] == "c"
    assert rec["speedup"] == 5.0


def test_recommend_keeps_the_baseline_when_nothing_beats_it():
    variants = {"a": _variant(0.1, 0), "b": _variant(0.2, 0), "c": {"error": "load failed"}}
    assert R.recommend(variants)["key"] == "a"


def test_recommend_respects_candidates_and_a_missing_baseline():
    variants = {"a": _variant(0.5, 0), "e": _variant(0.1, 0)}
    assert R.recommend(variants, candidates=("a", "b"))["key"] == "a"
    assert R.recommend({"b": _variant(0.1, 0)})["key"] is None


def test_fits_budget():
    assert R.fits_budget(2.0, 2.58)
    assert not R.fits_budget(3.0, 2.58)
    assert not R.fits_budget(None, 2.58)
    assert not R.fits_budget(1.0, 2.58, finish_reason="length")


def test_cleanup_findings_picks_the_thread_count_that_fits_most():
    def texts_for(times):
        return [{"words": w, "total_seconds": t, "fits": t <= 2.5 + 0.01 * w} for w, t in zip((8, 20, 45, 90, 180), times, strict=True)]

    by_threads = {"2": {"texts": texts_for([1.0, 2.0, 2.9, 4.0, 6.0])}, "4": {"texts": texts_for([0.8, 1.5, 2.5, 3.0, 5.0])}}
    f = R.cleanup_findings(by_threads)
    assert f["best_threads"] == "4"
    assert f["fits"] == 4 and f["lengths"] == 5 and f["share"] == 0.8
    assert f["largest_fit_words"] == 90
    assert f["fits_all_up_to_words"] == 90
    assert R.cleanup_findings({})["best_threads"] is None


def test_cleanup_findings_ties_go_to_the_faster_count():
    rows = lambda t: [{"words": 8, "total_seconds": t, "fits": True}]  # noqa: E731
    assert R.cleanup_findings({"2": {"texts": rows(1.0)}, "4": {"texts": rows(0.5)}})["best_threads"] == "4"


TREE = [
    {"path": "config.json", "size": 97, "type": "file"},
    {"path": "decoder_joint-model.int8.onnx", "size": 8_998_286, "type": "file"},
    {"path": "decoder_joint-model.onnx", "size": 35_792_059, "type": "file"},
    {"path": "encoder-model.int8.onnx", "size": 652_184_014, "type": "file"},
    {"path": "encoder-model.onnx", "size": 41_770_866, "type": "file"},
    {"path": "encoder-model.onnx.data", "size": 2_435_420_160, "type": "file"},
    {"path": "vocab.txt", "size": 9_384, "type": "file"},
]


def test_pick_repo_files():
    fp32 = [f["path"] for f in R.pick_repo_files(TREE, "fp32")]
    assert fp32 == ["encoder-model.onnx", "encoder-model.onnx.data", "decoder_joint-model.onnx", "vocab.txt", "config.json"]
    int8 = [f["path"] for f in R.pick_repo_files(TREE, "int8")]
    assert int8 == ["encoder-model.int8.onnx", "decoder_joint-model.int8.onnx", "vocab.txt", "config.json"]
    assert R.pick_repo_files(TREE, "fp16") is None
    assert R.onnx_asr_quantization("fp32") is None
    assert R.onnx_asr_quantization("int8") == "int8"


def test_parse_hf_url():
    url = "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a/encoder-model.int8.onnx"
    assert R.parse_hf_url(url) == ("istupakov/parakeet-tdt-0.6b-v2-onnx", "0bbb45a")


def test_the_cleanup_texts_have_the_planned_lengths():
    assert tuple(len(t.split()) for t in texts.TRANSCRIPTS) == texts.TRANSCRIPT_WORDS == (8, 20, 45, 90, 180)
    assert [name for name, _ in texts.CLIPS] == ["3s", "10s", "30s"]


def _report() -> dict:
    variants = {"a": {**_variant(0.5, 2), "label": "v2 int8, 4 threads (the app today)", "key": "a"},
                "c": {**_variant(0.1, 2), "label": "v2 full precision, 4 threads", "key": "c"},
                "d": {"key": "d", "label": "v2 half precision", "skipped": "no fp16 files"},
                "e": {**_variant(0.6, 4), "label": "v3 int8, 4 threads (other languages)", "key": "e"}}
    rows = [{"words": w, "total_seconds": t, "budget_seconds": 2.5 + 0.01 * w, "fits": t <= 2.5 + 0.01 * w,
             "first_token_seconds": 0.2, "tokens_per_second": 30.0, "finish_reason": "stop", "output": "Cleaned."}
            for w, t in zip((8, 20, 45, 90, 180), (0.9, 1.4, 2.4, 4.0, 7.5), strict=True)]
    cleanup = {"threads": {"4": {"load_seconds": 0.5, "prefix_seconds": 0.3, "flash_attn": "on", "texts": rows}},
               "rules": [{"words": 8, "microseconds": 12.0}, {"words": 180, "microseconds": 80.0}]}
    cleanup["findings"] = R.cleanup_findings(cleanup["threads"])
    return {"bench_version": 1, "started": "2026-11-21 10:00:00", "finished": "2026-11-21 10:20:00",
            "machine": {"cpu_name": "Test CPU", "physical_cores": 2, "logical_processors": 4, "memory_gb": 16.0,
                        "numpy_avx_features": ["AVX", "AVX2"]},
            "speech": {"variants": variants, "recommendation": R.recommend(variants, candidates=("a", "b", "c", "d"))},
            "cleanup": cleanup}


def test_summary_states_the_findings():
    text = "\n".join(R.summary_lines(_report()))
    assert "AVX: AVX, AVX2." in text
    assert "Recommended speech variant for this machine: (c) v2 full precision" in text
    assert "5.0x the speed of (a)" in text
    assert "Other languages, (e) v3 int8" in text
    assert "partly: 3 of 5 lengths fit; every length up to 45 words fits" in text
    assert "Rules-only cleanup: at most 80 microseconds" in text
    assert "—" not in text


def test_summary_for_skipped_parts():
    text = "\n".join(R.summary_lines({"speech": {"skipped": "--skip-speech was given"},
                                       "cleanup": {"skipped": "the cleanup model could not be installed (failed: no wheel)"}}))
    assert "Speech model: not measured (--skip-speech was given)" in text
    assert "not measured (the cleanup model could not be installed (failed: no wheel))" in text


def test_summary_when_the_cleanup_model_never_fits():
    rep = _report()
    for row in rep["cleanup"]["threads"]["4"]["texts"]:
        row["fits"] = False
    rep["cleanup"]["findings"] = R.cleanup_findings(rep["cleanup"]["threads"])
    assert "no, it does not fit the budget at any measured length" in "\n".join(R.summary_lines(rep))


def test_render_puts_the_summary_first_and_the_json_last():
    rep = _report()
    text = R.render(rep)
    assert text.startswith("SUMMARY")
    assert "SPEECH TO TEXT" in text and "CLEANUP MODEL" in text
    assert json.loads(text.split(R.JSON_MARKER, 1)[1]) == json.loads(json.dumps(rep))


def test_cleanup_findings_counts_skipped_lengths_as_not_fitting():
    slow = [{"words": 8, "total_seconds": 30.0, "fits": False}, {"words": 20, "skipped": "too slow", "fits": False}]
    fast = [{"words": 8, "total_seconds": 1.0, "fits": True}, {"words": 20, "total_seconds": 3.5, "fits": False}]
    f = R.cleanup_findings({"11": {"texts": slow}, "4": {"texts": fast}})
    assert f["best_threads"] == "4" and f["lengths"] == 2 and f["fits"] == 1
    only_slow = R.cleanup_findings({"11": {"texts": slow}})
    assert only_slow["lengths"] == 2 and only_slow["fits"] == 0 and only_slow["largest_fit_words"] is None
