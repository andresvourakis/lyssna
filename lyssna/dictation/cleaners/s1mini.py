"""S1-mini by Superwhisper on this Mac: a 0.6B model trained only to clean up dictation
(fillers, false starts, self-corrections, punctuation, lists), run through llama.cpp with Metal.
About 0.1 to 0.3 s per dictation on an M3 Pro, and the text never leaves the Mac.

How it is prompted, as its model card requires and as measured in the bake-off: its own system
prompt verbatim, a control line `[Styling: semi-formal] [Structure: lists] [Context: general]`,
then the transcript; the chat template rendered with thinking off (the empty think block the
model was trained with); greedy decoding; output capped at twice the input tokens plus 64. The
vocabulary is not given to it (its Context field only takes the trained values); the user's
spellings are applied after it, by the vocabulary.

Everything up to the transcript is evaluated once and its llama.cpp state saved; each cleanup
restores that state and evaluates only the transcript. Inputs longer than about 1,000 tokens are
cleaned in pieces split at sentence ends.

The model loads in the background when a recording starts (warm_up) and is unloaded after ten
idle minutes. Apple silicon only: on Intel Macs can_run() is False and dictation uses the rules."""

from __future__ import annotations

import atexit
import ctypes
import importlib.util
import logging
import platform
import re
import threading
import time
from collections.abc import Callable, Sequence
from functools import lru_cache

from ...providers.base import error_summary
from ..models import S1_MINI
from .base import Cleaner

log = logging.getLogger("lyssna.dictation")

SYSTEM = (
    "You are a text normalizer for speech-to-text transcripts. The input begins with a control line "
    "specifying the styling, structure, and context settings; clean the transcript to match those settings "
    "and output only the cleaned text."
)
CONTROL = "[Styling: semi-formal] [Structure: lists] [Context: general]"
N_CTX = 4096
MAX_PIECE_TOKENS = 1000
MAX_OUTPUT_TOKENS = 2048
IDLE_UNLOAD_S = 600.0
_MARKER = "\u0000TRANSCRIPT\u0000"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@lru_cache(maxsize=1)
def packages_installed() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("llama_cpp", "jinja2"))


def apple_silicon() -> bool:
    return platform.machine() == "arm64"


def messages(transcript: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"{CONTROL}\n{transcript}"}]


def render_chat(template: str, chat: list[dict]) -> str:
    """The model's own chat template, rendered with thinking off, in a sandbox (the template
    comes from the downloaded file). Default whitespace handling, as in the bake-off."""
    from jinja2.exceptions import TemplateError
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    def raise_exception(message: str) -> None:
        raise TemplateError(message)

    env = ImmutableSandboxedEnvironment()
    env.globals["raise_exception"] = raise_exception
    return env.from_string(template).render(messages=chat, add_generation_prompt=True, enable_thinking=False,
                                            bos_token="", eos_token="")


class Runner:
    """The loaded model with its saved prompt prefix. One cleanup at a time."""

    def __init__(self, model_path: str):
        from llama_cpp import Llama

        self.llm = Llama(model_path=model_path, n_gpu_layers=-1, n_ctx=N_CTX, flash_attn=True, verbose=False)
        template = self.llm.metadata["tokenizer.chat_template"]
        prefix, self._suffix = render_chat(template, messages(_MARKER)).split(_MARKER)
        self._prefix_tokens = self.llm.tokenize(prefix.encode("utf-8"), add_bos=True, special=True)
        self._prefix_state: bytes | None = None
        self._save_prefix()

    def _save_prefix(self) -> None:
        from llama_cpp import llama_cpp as C

        self.llm.reset()
        self.llm._ctx.kv_cache_clear()
        self.llm.eval(self._prefix_tokens)
        ctx = self.llm._ctx.ctx
        size = C.llama_state_get_size(ctx)
        buf = (ctypes.c_uint8 * size)()
        n = C.llama_state_get_data(ctx, buf, size)
        self._prefix_state = bytes(buf[:n])

    def _restore_prefix(self) -> None:
        from llama_cpp import llama_cpp as C

        state = self._prefix_state
        if state is None:
            raise RuntimeError("no saved prefix")
        arr = (ctypes.c_uint8 * len(state)).from_buffer_copy(state)
        if C.llama_state_set_data(self.llm._ctx.ctx, arr, len(state)) != len(state):
            raise RuntimeError("the saved prefix could not be restored")
        n = len(self._prefix_tokens)
        self.llm.input_ids[:n] = self._prefix_tokens
        self.llm.n_tokens = n
        self.llm._requires_eval = True

    def close(self) -> None:
        """Free the model now. llama.cpp's Metal backend aborts the process at exit if a model is
        still loaded then, so this also runs at exit."""
        self._prefix_state = None
        close = getattr(self.llm, "close", None)
        if close is not None:
            close()

    def count_tokens(self, text: str) -> int:
        return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False, special=False))

    def clean(self, transcript: str) -> tuple[str, str, int, int]:
        """(output, finish reason, input tokens, output tokens) for one piece of text."""
        tail = self.llm.tokenize((transcript + self._suffix).encode("utf-8"), add_bos=False, special=True)
        tokens = self._prefix_tokens + tail
        n_in = self.count_tokens(transcript)
        max_tokens = min(2 * n_in + 64, MAX_OUTPUT_TOKENS)
        try:
            self._restore_prefix()
        except Exception as e:  # fall back to evaluating the whole prompt
            log.warning("S1-mini prefix restore failed (%s); evaluating the whole prompt", type(e).__name__)
            self.llm.reset()
            self.llm._ctx.kv_cache_clear()
        out = self.llm.create_completion(prompt=tokens, max_tokens=max_tokens, temperature=0.0, top_k=1, top_p=1.0,
                                         min_p=0.0, repeat_penalty=1.0, stop=["<|im_end|>"], stream=False)
        choice = out["choices"][0]
        n_out = int((out.get("usage") or {}).get("completion_tokens") or 0)
        return choice["text"], choice.get("finish_reason") or "", n_in, n_out


def _make_runner(model_path: str) -> Runner:
    """Load the model. Tests replace this, so nothing in the test suite loads llama.cpp."""
    return Runner(model_path)


_runner: Runner | None = None
_state_lock = threading.Lock()  # guards _runner, _loading and _timer
_run_lock = threading.Lock()  # one cleanup (or load) at a time
_loading: threading.Thread | None = None
_timer: threading.Timer | None = None


def _get_runner() -> Runner:
    """The loaded model, loading it now if needed (blocking)."""
    global _runner
    with _run_lock:
        if _runner is None:
            start = time.monotonic()
            runner = _make_runner(str(S1_MINI.path("s1-mini-q4_k_m.gguf")))
            with _state_lock:
                _runner = runner
            _register_exit_hook()
            log.info("loaded S1-mini in %.2fs", time.monotonic() - start)
        return _runner


def _schedule_unload() -> None:
    """Unload after IDLE_UNLOAD_S without use; every use pushes it back."""
    global _timer
    with _state_lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(IDLE_UNLOAD_S, unload)
        _timer.daemon = True
        _timer.start()


_exit_hook = False


def _register_exit_hook() -> None:
    global _exit_hook
    if not _exit_hook:
        atexit.register(unload)
        _exit_hook = True


def unload() -> None:
    """Free the model's memory (after ten idle minutes, and at exit). Waits for a cleanup in
    progress."""
    global _runner, _timer
    with _run_lock, _state_lock:
        runner, _runner = _runner, None
        if _timer is not None:
            _timer.cancel()
            _timer = None
    if runner is not None:
        log.info("unloading S1-mini")
        try:
            runner.close()
        except Exception as e:
            log.warning("S1-mini did not close cleanly: %s", type(e).__name__)


def split_pieces(text: str, count: Callable[[str], int], limit: int = MAX_PIECE_TOKENS) -> list[str]:
    """Split text at sentence ends into pieces of at most `limit` tokens. A single sentence
    longer than that is split between words."""
    if count(text) <= limit:
        return [text]
    units: list[str] = []
    for sentence in _SENTENCE_END.split(text.strip()):
        if count(sentence) <= limit:
            units.append(sentence)
            continue
        words, current = sentence.split(), []
        for word in words:
            if current and count(" ".join([*current, word])) > limit:
                units.append(" ".join(current))
                current = []
            current.append(word)
        if current:
            units.append(" ".join(current))
    pieces, current = [], ""
    for unit in units:
        candidate = f"{current} {unit}".strip()
        if current and count(candidate) > limit:
            pieces.append(current)
            current = unit
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


class S1MiniCleaner(Cleaner):
    name = "s1-mini"
    label = "On this Mac (S1-mini by Superwhisper)"
    needs_key = False
    setup_hint = "460 MB download"
    config_section = "s1-mini"
    vendor = "Superwhisper"
    sends_text_off_device = False
    budget_s = 2.5

    @classmethod
    def can_run(cls) -> bool:
        return apple_silicon() and packages_installed()

    def configured(self, wait: bool = True) -> bool:
        return S1_MINI.present()

    def recipient(self) -> str:
        return "this Mac"

    def prepare(self, on_error: Callable[[str], None] | None = None) -> None:
        if not S1_MINI.present() and self.can_run():
            S1_MINI.download().start(on_done=lambda err: err and on_error and on_error(err))

    def warm_up(self) -> None:
        """Start loading the model in the background, if it is downloaded and not loaded."""
        global _loading
        if not self.can_run() or not S1_MINI.present():
            return
        with _state_lock:
            if _runner is not None or (_loading is not None and _loading.is_alive()):
                return

            def load():
                try:
                    _get_runner()
                    _schedule_unload()
                except Exception as e:
                    log.warning("S1-mini could not load: %s", error_summary(e))

            _loading = threading.Thread(target=load, daemon=True, name="lyssna-s1mini-load")
            _loading.start()

    def ready(self, timeout: float | None = None) -> bool:
        t = _loading
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True

    def clean(self, raw: str, terms: Sequence[str] = (), budget_s: float = 0.0) -> str | None:
        if not raw.strip():
            return ""
        if not self.can_run():
            return None
        if not S1_MINI.present():
            self.prepare()
            log.info("S1-mini is not downloaded yet; cleaning with rules")
            return None
        start = time.monotonic()
        try:
            runner = _get_runner()
            with _run_lock:
                pieces = split_pieces(raw, runner.count_tokens, MAX_PIECE_TOKENS)
                outputs, n_in, n_out = [], 0, 0
                for piece in pieces:
                    text, finish, i, o = runner.clean(piece)
                    n_in, n_out = n_in + i, n_out + o
                    if finish == "length":
                        log.info("S1-mini hit its output cap after %.2fs; cleaning with rules", time.monotonic() - start)
                        return None
                    outputs.append(text.strip())
        except Exception as e:  # a model that fails is a cleanup that did not happen
            log.warning("S1-mini cleanup failed: %s", error_summary(e))
            return None
        finally:
            _schedule_unload()
        log.info("cleaned via s1-mini in %.2fs (%d piece(s), %d tokens in, %d out)", time.monotonic() - start,
                 len(pieces), n_in, n_out)
        return " ".join(o for o in outputs if o)
