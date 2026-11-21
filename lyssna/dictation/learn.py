"""Notice the corrections the user makes to dictated text, so the vocabulary can learn them.

find_corrections() is pure: given what we pasted and what the field holds later, it finds the
pasted words inside the field (which usually has other text around them), aligns the two word
by word with difflib, and returns the small replacements: 1 to 3 words swapped for 1 to 3
words. Insertions, deletions, spacing and punctuation are not corrections of what was heard.
When too much of the pasted text changed, the user rewrote it rather than fixed a word, and
nothing is returned.

EditWatcher runs after a paste. It reads only the one element we pasted into, a few times a
second, for at most a minute, and gives up as soon as focus moves to another element, another
app comes to the front, or another dictation starts. Once the field has changed and then
stayed still for a couple of seconds, the corrections found go to `learn(observed, corrected)`,
each pair once. Values stay in memory; the log gets counts only. Reads go through a short
timeout and the watcher stops if the app answers slowly, so typing is never held up.

The reads themselves are the system's: host.field_probe.FieldProbe (lyssna/mac/fieldprobe.py
or lyssna/win/fieldprobe.py), built on first use rather than when the watcher is made, so
building a watcher never loads a platform framework."""

from __future__ import annotations

import difflib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .. import host

log = logging.getLogger("lyssna.dictation.learn")

MAX_PHRASE = 3          # words on either side of a correction
REWRITE_SHARE = 1 / 3   # more than this share of the pasted words changed: a rewrite, not a fix

_WORD = re.compile(r"\S+")
_EDGE_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")


@dataclass(frozen=True)
class _Token:
    key: str   # the word without surrounding punctuation, case kept
    start: int
    end: int


def _tokens(text: str) -> list[_Token]:
    out = []
    for m in _WORD.finditer(text):
        key = _EDGE_PUNCT.sub("", m.group())
        if key:  # punctuation on its own is not a word
            out.append(_Token(key, m.start(), m.end()))
    return out


def _locate(ins: list[str], field: list[str]) -> tuple[int, int] | None:
    """The token range in `field` where the pasted words most likely are, with room on both
    sides for words the user replaced at the edges. None when the paste is not there."""
    if not ins or not field:
        return None
    matcher = difflib.SequenceMatcher(None, ins, field, autojunk=False)
    a, b, size = matcher.find_longest_match(0, len(ins), 0, len(field))
    if size == 0:
        return None
    slack = int(len(ins) * REWRITE_SHARE) + MAX_PHRASE
    lo = max(0, b - a - slack)
    hi = min(len(field), b + (len(ins) - a) + slack)
    window = field[lo:hi]
    blocks = [m for m in difflib.SequenceMatcher(None, ins, window, autojunk=False).get_matching_blocks() if m.size]
    matched = sum(m.size for m in blocks)
    if matched < len(ins) - max(1, round(len(ins) * REWRITE_SHARE)):
        return None
    first, last = blocks[0], blocks[-1]
    # Extend to cover pasted words before the first match and after the last one.
    start = max(0, first.b - first.a)
    end = min(len(window), last.b + last.size + (len(ins) - last.a - last.size))
    return lo + start, lo + end


def find_corrections(inserted: str, edited: str) -> list[tuple[str, str]]:
    """Word-level replacements the user made inside the pasted span: [(observed, corrected)]."""
    ins = [t.key for t in _tokens(inserted)]
    field_tokens = _tokens(edited)
    field = [t.key for t in field_tokens]
    span = _locate(ins, field)
    if span is None:
        return []
    lo, hi = span
    span_tokens = field[lo:hi]
    if span_tokens == ins:
        return []
    changed = 0
    pairs: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ins, span_tokens, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        changed += max(i2 - i1, j2 - j1)
        if op != "replace":
            continue
        if 1 <= i2 - i1 <= MAX_PHRASE and 1 <= j2 - j1 <= MAX_PHRASE:
            observed = " ".join(ins[i1:i2])
            corrected = " ".join(span_tokens[j1:j2])
            if observed != corrected:
                pairs.append((observed, corrected))
    if changed > max(1, round(len(ins) * REWRITE_SHARE)):
        return []  # a rewrite, not a correction
    return pairs


# -- watching the field ----------------------------------------------------------------------

class EditWatcher:
    POLL = 0.3          # seconds between reads
    SETTLE = 2.0        # the field must be still this long before we compare
    LIMIT = 60.0        # stop watching after this long
    MAX_CHARS = 200_000  # a field this big is a document; reading it repeatedly is not worth it
    SLOW_READ = 0.1     # a read slower than this means the app is busy: stop

    def __init__(
        self,
        learn: Callable[[str, str], object],
        probe=None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.learn = learn
        self._probe = probe  # host.field_probe's FieldProbe unless a test passes one
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def probe(self):
        """The reads of the focused field: focused(), value(element), same(a, b), front_pid()."""
        if self._probe is None:
            self._probe = host.field_probe.FieldProbe()
        return self._probe

    def watch(self, text: str, background: bool = True) -> None:
        """Start watching the focused element for edits to `text`, which was just pasted
        there. Replaces any watch in progress."""
        with self._lock:
            self._generation += 1
            gen = self._generation
        if background:
            threading.Thread(target=self._run, args=(gen, text), daemon=True, name="lyssna-learn").start()
        else:
            self._run(gen, text)

    def stop(self) -> None:
        """Another dictation started (or the app is quitting): stop watching."""
        with self._lock:
            self._generation += 1

    def _current(self, gen: int) -> bool:
        return gen == self._generation

    def _read(self, element) -> str | None:
        t = self.clock()
        value = self.probe.value(element)
        if self.clock() - t > self.SLOW_READ or (value is not None and len(value) > self.MAX_CHARS):
            return None
        return value

    def _run(self, gen: int, text: str) -> None:
        try:
            self._watch(gen, text)
        except Exception:
            log.exception("watching for corrections failed")

    def _watch(self, gen: int, text: str) -> None:
        element = self.probe.focused()
        if element is None:
            return
        pid = self.probe.front_pid()
        if self._read(element) is None:
            return  # the element shows us no value: nothing to watch
        began = self.clock()
        last: str | None = None
        changed_at = began
        edited = False
        reported: set[tuple[str, str]] = set()
        while self._current(gen) and self.clock() - began < self.LIMIT:
            self.sleep(self.POLL)
            if not self._current(gen):
                return
            focused = self.probe.focused()
            if focused is None or not self.probe.same(focused, element) or self.probe.front_pid() != pid:
                break
            value = self._read(element)
            if value is None:
                break
            now = self.clock()
            if last is None:
                last = value  # the first read after the paste is the baseline
                changed_at = now
                continue
            if value != last:
                last, changed_at, edited = value, now, True
                continue
            if edited and now - changed_at >= self.SETTLE:
                edited = False
                for pair in find_corrections(text, value):
                    if pair not in reported:
                        reported.add(pair)
                        self._learn(*pair)
        if edited and last is not None and self._current(gen):
            for pair in find_corrections(text, last):
                if pair not in reported:
                    reported.add(pair)
                    self._learn(*pair)
        log.info("stopped watching a dictation for corrections (%d found)", len(reported))

    def _learn(self, observed: str, corrected: str) -> None:
        try:
            self.learn(observed, corrected)
        except Exception:
            log.exception("learning a correction failed")
