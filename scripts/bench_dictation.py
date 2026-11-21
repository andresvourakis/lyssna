"""How fast the two local dictation models are on this machine, and how well the speech model
hears, so the Windows build can choose its files by numbers. Works on Windows and macOS.

    uv run python scripts/bench_dictation.py --out bench-report.txt [--skip-cleanup] [--skip-speech]
                                             [--variants a,b,c,d,e] [--work-dir DIR]

It measures the machine; makes three test clips (about 3, 10 and 30 s) with the app's own
voice; times the Parakeet speech model in up to five variants (a: the app today, v2 int8 with 4
threads; b: int8 with one thread per physical core; c: full precision; d: half precision if the
repository has it; e: v3 int8) and scores each transcript; and times the cleanup model (S1-mini
by Superwhisper) on the CPU against the app's cleanup budget, next to the rules-only cleaner.

The report starts with a summary of findings and ends with every number as JSON. A variant that
fails is recorded and the run goes on; the report is rewritten after every step, so an
interrupted run still leaves what it measured. The cleanup model runs in a child process, so a
crash inside llama.cpp is reported instead of ending the run. Everything is free and local:
model files come from Hugging Face and GitHub into the work folder, never the app's folder."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import measure
from bench import report as R

BENCH_VERSION = 1
ALL_VARIANTS = "a,b,c,d,e"


def say(text: str) -> None:
    print(text, flush=True)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark the local dictation models on this machine.")
    p.add_argument("--out", default="bench-report.txt", help="the report file")
    p.add_argument("--summary", help="also write the summary block to this file (for the GitHub job summary)")
    p.add_argument("--skip-speech", action="store_true", help="do not measure the speech model")
    p.add_argument("--skip-cleanup", action="store_true", help="do not measure the cleanup model")
    p.add_argument("--variants", default=ALL_VARIANTS, help=f"speech variants to measure (default {ALL_VARIANTS})")
    p.add_argument("--work-dir", default=str(Path(tempfile.gettempdir()) / "lyssna-bench"),
                   help="where downloads go (default: a folder in the temp folder)")
    p.add_argument("--install-note-file", help="a file saying how llama-cpp-python was installed, or why it was not")
    p.add_argument("--slow-cutoff", type=float, default=60.0,
                   help="seconds after which a slow run stops early: a speech clip skips its warm runs, the "
                   "cleanup model skips the longer texts at that thread count")
    p.add_argument("--cleanup-threads", help="thread counts for the cleanup model, comma separated "
                   "(default: the physical cores and the logical processors)")
    p.add_argument("--cleanup-timeout", type=float, default=1800.0, help="seconds before the cleanup measurement is stopped")
    p.add_argument("--cleanup-worker", help=argparse.SUPPRESS)  # internal: run only the cleanup part, write JSON here
    return p.parse_args(argv)


def _cleanup_worker(args: argparse.Namespace) -> int:
    out = Path(args.cleanup_worker)
    section: dict = {}

    def save() -> None:
        out.write_text(json.dumps(section, default=str), encoding="utf-8")

    try:
        threads = ([int(t) for t in args.cleanup_threads.split(",") if t.strip()] if args.cleanup_threads
                   else measure.cleanup_thread_counts())
        measure.cleanup(Path(args.work_dir), threads, say, save, section, args.slow_cutoff)
    except Exception as e:
        section["error"] = measure.error_detail(e)
    save()
    return 0


def _install_note(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError as e:
        return f"(the install note {path} could not be read: {type(e).__name__})"


def _run_cleanup(args: argparse.Namespace, section: dict, save) -> None:
    result = Path(args.work_dir) / "cleanup-result.json"
    result.unlink(missing_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--cleanup-worker", str(result), "--work-dir", args.work_dir,
           "--slow-cutoff", str(args.slow_cutoff)]
    if args.cleanup_threads:
        cmd += ["--cleanup-threads", args.cleanup_threads]

    def merge(final: bool) -> None:
        if not result.is_file():
            return
        try:
            section.update(json.loads(result.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:  # caught mid-write while polling; the final read is whole
            if final:
                section["error"] = f"the cleanup result could not be read: {e}"

    code: int | str
    worker = subprocess.Popen(cmd)
    deadline = time.monotonic() + args.cleanup_timeout
    while True:
        try:
            code = worker.wait(timeout=10.0)
            break
        except subprocess.TimeoutExpired:
            merge(final=False)
            save()  # an interrupted run still keeps what was measured so far
            if time.monotonic() > deadline:
                worker.kill()
                worker.wait()
                code = f"stopped after {args.cleanup_timeout:.0f} s"
                break
    merge(final=True)
    if code != 0:
        section["worker_exit"] = code
        note = f"the cleanup measurement process ended early (exit {code}); a crash inside llama.cpp would look like this"
        section["error"] = f"{section['error']}; {note}" if section.get("error") else note
        say(f"  {note}")
    if section.get("threads") and not section.get("findings"):
        section["findings"] = R.cleanup_findings(section["threads"])
    save()


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.cleanup_worker:
        return _cleanup_worker(args)
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    report: dict = {"bench_version": BENCH_VERSION, "started": _now(), "finished": None, "platform": sys.platform,
                    "work_dir": str(work), "arguments": sys.argv[1:] if argv is None else argv}

    def save() -> None:
        out.write_text(R.render(report), encoding="utf-8")
        if args.summary:
            Path(args.summary).write_text("## Lyssna dictation benchmark\n\n" + "\n".join(R.summary_lines(report)[1:]) + "\n\n"
                                          "_Findings on this machine, not decisions. The full report is in the artifact._\n",
                                          encoding="utf-8")

    say("Lyssna dictation benchmark")
    say("The machine")
    try:
        report["machine"] = measure.machine()
    except Exception as e:
        report["machine"] = {"error": measure.error_detail(e)}
    save()

    if args.skip_speech:
        report["speech"] = {"skipped": "--skip-speech was given"}
    else:
        say("The test audio")
        clips: dict = {}
        try:
            clips, report["audio"] = measure.make_clips(work, say)
        except Exception as e:
            report["audio"] = {"error": measure.error_detail(e)}
        save()
        report["speech"] = {}
        if not clips:
            report["speech"]["error"] = "no test audio, so the speech model was not measured (see TEST AUDIO)"
        else:
            say("The speech model")
            variants = [v.strip() for v in args.variants.split(",") if v.strip()]
            try:
                measure.speech(clips, work, variants, args.slow_cutoff, say, save, report["speech"])
            except Exception as e:
                report["speech"]["error"] = measure.error_detail(e)
        save()

    say("The cleanup model")
    note = _install_note(args.install_note_file)
    report["cleanup"] = {"install_note": note} if note else {}
    if args.skip_cleanup:
        try:
            report["cleanup"]["rules"] = measure.rules_timing()
        except Exception as e:
            report["cleanup"]["error"] = measure.error_detail(e)
        report["cleanup"]["skipped"] = (f"the cleanup model could not be installed ({note})" if note.lower().startswith("failed")
                                        else "--skip-cleanup was given")
    else:
        _run_cleanup(args, report["cleanup"], save)

    report["finished"] = _now()
    save()
    say("")
    say("\n".join(R.summary_lines(report)))
    say(f"\nReport written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
