"""Command line entry point.

  lyssna                       launch the menu bar app
  lyssna "some text"           read text (through the app if running, else standalone)
  echo hi | lyssna -           read stdin
  lyssna notes.md              read a file
  lyssna pause|stop|next|prev|status
  lyssna speed 1.5
  lyssna seek 0.5              jump to the middle of the reading (0..1)
  lyssna button on|off|toggle  show the play button after selecting text
  lyssna doctor                check keys, permissions, and a test synthesis
  lyssna autostart on|off      launch at login
  lyssna dictate --file clip.wav [--transcriber X] [--cleaner Y] [--no-cleanup] [--json]
                               dictate from a 16 kHz mono WAV file instead of the microphone
  lyssna vocab list|add|remove [TERM]   the dictation vocabulary (names and words to spell right)
"""

from __future__ import annotations

import argparse
import logging
import plistlib
import shutil
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config, server

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "io.github.andresvourakis.lyssna.plist"


def _setup_logging(verbose: bool) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)  # the rest after the handlers, so the migration line is logged
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(config.LOG_DIR / "lyssna.log", maxBytes=1_000_000, backupCount=2)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("phonemizer").setLevel(logging.ERROR)  # "words count mismatch" on nearly every Kokoro sentence
    config.ensure_dirs()


def _read_source(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    try:
        p = Path(arg).expanduser()
        if p.is_file():
            return p.read_text(errors="replace")
    except OSError:  # e.g. "File name too long" when the argument is a paragraph of text
        pass
    return arg


def _speak_standalone(text: str) -> int:
    from .cache import AudioCache
    from .player import Reader
    from .providers import get_provider

    cfg = _local_only_guard(config.load())
    reader = Reader(lambda: get_provider(cfg["provider"], cfg), speed=float(cfg.get("speed", 1.0)),
                    cache=AudioCache.from_config(cfg))
    if not reader.speak(text):
        print(reader.status.error or "Nothing to read", file=sys.stderr)
        return 1
    last = ""
    try:
        while reader.is_active():
            st = reader.status
            line = f"[{st.state}] {st.index}/{st.total} {st.text}"
            if line != last:
                print(line, file=sys.stderr)
                last = line
            time.sleep(0.2)
    except KeyboardInterrupt:
        reader.stop()
        return 130
    if reader.status.error:
        print(reader.status.error, file=sys.stderr)
        return 1
    return 0


def _local_only_guard(cfg: dict) -> dict:
    """A config whose engine local-only mode allows (a hand-edited config file may name an
    online one while local_only is on)."""
    from .providers import REGISTRY, allowed, local_fallback

    if cfg["provider"] in REGISTRY and not allowed(cfg["provider"], cfg):
        cfg = {**cfg, "provider": local_fallback()}
    return cfg


def _print_status(resp: dict | None) -> int:
    if resp is None:
        print("Lyssna app is not running. Start it with: lyssna", file=sys.stderr)
        return 1
    if not resp.get("ok"):
        print(resp.get("error", "error"), file=sys.stderr)
        return 1
    st = resp.get("status", {})
    print(f"{st.get('state')} {st.get('index', 0)}/{st.get('total', 0)} speed={st.get('speed')} {st.get('text', '')}")
    return 0


def cmd_speak(text: str, force_standalone: bool = False) -> int:
    if not force_standalone:
        resp = server.send({"cmd": "speak", "text": text})
        if resp is not None:
            if not resp.get("ok"):
                print(resp.get("status", {}).get("error") or resp.get("error") or "failed", file=sys.stderr)
                return 1
            return 0
    return _speak_standalone(text)


def cmd_doctor() -> int:
    from .events import accessibility_trusted
    from .providers import REGISTRY, get_provider

    cfg = _local_only_guard(config.load())
    print(f"config:   {config.CONFIG_PATH}")
    print(f"provider: {cfg['provider']}  speed: {cfg['speed']}")
    print(f"accessibility permission for this process: {'yes' if accessibility_trusted() else 'NO'}")
    print(f"app running: {'yes' if server.send({'cmd': 'status'}) else 'no'}")
    print("providers:")
    for name in REGISTRY:
        p = get_provider(name, cfg)
        mark = "✓" if p.configured() else f"✗ ({p.setup_hint})"
        print(f"  {name:<14} {mark:<12} voice={p.voice} model={p.model} ~${p.price_per_million:g}/1M chars")
    try:
        p = get_provider(cfg["provider"], cfg)
        t = time.time()
        audio = p.synthesize("Lyssna is working. This is a test of the current voice.")
        print(f"test synthesis via {p.name}: {len(audio.data)} bytes of {audio.ext} in {time.time()-t:.2f}s ✓")
    except Exception as e:
        print(f"test synthesis failed: {e}")
        return 1
    return 0


def cmd_autostart(enable: bool) -> int:
    exe = shutil.which("lyssna") or str(Path(sys.argv[0]).resolve())
    if enable:
        plist = {
            "Label": "io.github.andresvourakis.lyssna",
            "ProgramArguments": [exe, "app"],
            "RunAtLoad": True,
            "KeepAlive": False,
            "StandardOutPath": str(config.LOG_DIR / "launchd.out.log"),
            "StandardErrorPath": str(config.LOG_DIR / "launchd.err.log"),
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        }
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT.write_bytes(plistlib.dumps(plist))
        subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], capture_output=True)
        subprocess.run(["launchctl", "load", str(LAUNCH_AGENT)], check=False)
        print(f"Installed {LAUNCH_AGENT} → runs `{exe} app` at login.")
        print("If hotkeys don't work when launched this way, grant Accessibility to the python binary it runs.")
    else:
        subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], capture_output=True)
        LAUNCH_AGENT.unlink(missing_ok=True)
        print("Autostart removed.")
    return 0


# -- dictation ---------------------------------------------------------------------------


def read_wav(path: Path):
    """A WAV file as mono float32 samples and its sample rate (16-bit or 32-bit PCM)."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(w.getnframes())
    if width == 2:
        audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        audio = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"{path.name}: {8 * width}-bit audio is not supported; use 16-bit PCM")
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio.astype(np.float32), rate


def _download_with_progress(model) -> str:
    from .dictation import models

    last = [-1]

    def show(fraction: float) -> None:
        pct = int(fraction * 100)
        if pct != last[0]:
            print(f"\rDownloading {model.credit()} ({model.total_bytes / 1e6:.0f} MB): {pct}%", end="", file=sys.stderr)
            last[0] = pct

    error = models.ensure(model, on_progress=show)
    if last[0] >= 0:
        print(file=sys.stderr)
    return error


def cmd_dictate(argv: list[str]) -> int:
    import json

    from . import policy
    from .dictation import cleaners, transcribers
    from .dictation.pipeline import DictationPipeline
    from .dictation.types import DictationError

    parser = argparse.ArgumentParser(prog="lyssna dictate", description="Run dictation on a 16 kHz mono WAV file.")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--transcriber", choices=list(transcribers.REGISTRY))
    parser.add_argument("--cleaner", choices=list(cleaners.REGISTRY))
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the result with timings as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    ns = parser.parse_args(argv)
    _setup_logging(ns.verbose)
    cfg = config.load()

    # Local-only mode: an online engine named on the command line is refused; one named in a
    # hand-edited config file falls back to the engine on this Mac (as for the voices).
    if ns.transcriber and not transcribers.allowed(ns.transcriber, cfg):
        print(transcribers.refusal(transcribers.REGISTRY[ns.transcriber].label), file=sys.stderr)
        return 2
    if ns.cleaner and not ns.no_cleanup and not cleaners.allowed(ns.cleaner, cfg):
        print(cleaners.refusal(cleaners.REGISTRY[ns.cleaner].label), file=sys.stderr)
        return 2
    t_name = ns.transcriber or transcribers.effective_transcriber(cfg)
    c_name = "" if ns.no_cleanup else (ns.cleaner or cleaners.effective_cleaner(cfg))
    if not t_name:
        print("No speech engine can run in this install.", file=sys.stderr)
        return 1
    transcriber = transcribers.get_transcriber(t_name, cfg)
    cleaner = cleaners.get_cleaner(c_name, cfg) if c_name else None

    import wave

    try:
        samples, rate = read_wav(ns.file)
    except (OSError, ValueError, EOFError, wave.Error) as e:
        print(f"Could not read {ns.file}: {e}", file=sys.stderr)
        return 1

    # Engines on this Mac: download the model first (with progress) and load it, so the
    # timings below are for the dictation itself.
    if not transcriber.sends_audio_off_device and hasattr(transcriber, "model"):
        error = _download_with_progress(transcriber.model())
        if error:
            print(error, file=sys.stderr)
            return 1
    if cleaner is not None and not cleaner.sends_text_off_device and cleaner.can_run():
        from .dictation.models import S1_MINI

        error = _download_with_progress(S1_MINI)
        if error:
            print(error, file=sys.stderr)
            return 1
    for engine in (transcriber, cleaner):
        if engine is not None:
            engine.warm_up()
            engine.ready(timeout=300)

    pipeline = DictationPipeline(cfg, transcriber=transcriber, cleaner=cleaner or "", cleanup=cleaner is not None)
    try:
        result = pipeline.run(samples, rate)
    except DictationError as e:
        print(str(e), file=sys.stderr)
        return 1
    if ns.json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    else:
        print(result.text)
    if policy.local_only(cfg) and ns.verbose:
        print("local-only mode is on", file=sys.stderr)
    return 0


def cmd_vocab(argv: list[str]) -> int:
    from .dictation.vocabulary import shared

    parser = argparse.ArgumentParser(prog="lyssna vocab", description="The dictation vocabulary.")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="show every term")
    add = sub.add_parser("add", help="add a term (a name or word to spell exactly so)")
    add.add_argument("term", nargs="+")
    add.add_argument("--heard-as", help="a wrong form the speech model writes for it")
    remove = sub.add_parser("remove", help="delete a term (it will not be learned again)")
    remove.add_argument("term", nargs="+")
    parser.add_argument("-v", "--verbose", action="store_true")
    ns = parser.parse_args(argv)
    _setup_logging(ns.verbose)
    vocab = shared()
    if ns.action == "list":
        entries = vocab.entries()
        if not entries:
            print("The vocabulary is empty. Add a term with: lyssna vocab add NAME")
        for e in entries:
            heard = f", heard as: {', '.join(sorted(e.heard_as))}" if e.heard_as else ""
            print(f"{e.term}  ({e.source}, used {e.uses}x{heard})")
        return 0
    if ns.action == "add":
        term = vocab.add(" ".join(ns.term), heard_as=ns.heard_as)
        if term is None:
            print(f"Not added: a term must be 1 to {60} characters.", file=sys.stderr)
            return 1
        print(f"Added {term}")
        return 0
    term = " ".join(ns.term)  # remove
    if not vocab.remove(term):
        print(f"{term} is not in the vocabulary.", file=sys.stderr)
        return 1
    print(f"Removed {term}")
    return 0


_SUBCOMMANDS = {"dictate": cmd_dictate, "vocab": cmd_vocab}


def main(argv: list[str] | None = None) -> int:
    from .providers import REGISTRY

    argv = sys.argv[1:] if argv is None else list(argv)
    head = next((i for i, a in enumerate(argv) if not a.startswith("-")), None)
    if head is not None and argv[head] in _SUBCOMMANDS:
        return _SUBCOMMANDS[argv[head]](argv[:head] + argv[head + 1 :])

    parser = argparse.ArgumentParser(prog="lyssna", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--standalone", action="store_true", help="play in this process even if the app is running")
    parser.add_argument("-p", "--provider", help=f"override the voice provider for this run ({', '.join(REGISTRY)})")
    parser.add_argument("args", nargs="*")
    ns = parser.parse_args(argv)
    _setup_logging(ns.verbose)
    if ns.provider:
        import os

        from . import policy
        from .providers import allowed

        if ns.provider in REGISTRY and not allowed(ns.provider, config.load()):
            print(policy.refusal(REGISTRY[ns.provider].label), file=sys.stderr)
            return 2
        os.environ["LYSSNA_PROVIDER"] = ns.provider
        ns.standalone = True
    args = ns.args

    if not args or args == ["app"]:
        from .app import run
        run()
        return 0

    head, rest = args[0], args[1:]
    simple = {"pause", "stop", "next", "prev", "status", "selection", "clipboard", "quit"}
    if head in simple and not rest:
        return _print_status(server.send({"cmd": head}))
    if head == "button":
        return _print_status(server.send({"cmd": "button", "value": (rest[:1] or ["toggle"])[0]}))
    if head == "speed" and rest:
        return _print_status(server.send({"cmd": "speed", "value": float(rest[0])}))
    if head == "seek" and rest:
        return _print_status(server.send({"cmd": "seek_to", "value": float(rest[0])}))
    if head == "doctor":
        return cmd_doctor()
    if head == "config":
        print(config.CONFIG_PATH)
        return 0
    if head == "autostart":
        return cmd_autostart(enable=(rest[:1] or ["on"])[0] != "off")
    if head == "speak":
        args = rest or ["-"]
    text = "\n\n".join(_read_source(a) for a in args)
    return cmd_speak(text, force_standalone=ns.standalone)


if __name__ == "__main__":
    sys.exit(main())
