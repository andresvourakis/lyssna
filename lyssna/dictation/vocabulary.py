"""The user's dictionary: names and words the speech model gets wrong, and how to spell them.

One store, `STATE_DIR/dictation/vocabulary.json` (owner-only, written atomically). Each entry
is a `term` (the exact spelling to write) plus `heard_as`, the wrong forms it was seen as and
how often. Entries come from two places: typed in by the user ("manual") or learned from a
correction the user made right after a dictation was pasted ("learned"). A deleted entry stays
behind as a tombstone, so a word the user removed is not learned again from the next correction.

`correct(text)` makes one decision for every run of 1 to 3 words: replace it with a term when
the evidence that the speaker said the term outweighs the evidence that they meant the words
as written. Each piece of evidence is a named function below, so the rule stays one rule:

- for the term: the words are a recorded `heard_as` form (stronger with more sightings), or
  they sound like the term (a near miss on a rough phonetic key), or they are the term in a
  different case (unless the term is itself a plain lowercase common word);
- for the words as written: they are common words of the language (a bundled list of about
  64,000 lowercase English words, see data/common_words.LICENSE).

So a learned non-word ("Listna" for "Lyssna") is always replaced, a near miss of a rare term
("Parakeat" for "Parakeet") is replaced, and a real word that was corrected once ("Rise" for
"Ryse") is left alone at the text level; the cloud engines still get the term as a hint.

Nothing here stores dictated text: only terms and the short forms they were heard as."""

from __future__ import annotations

import itertools
import json
import logging
import math
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from .. import config
from .cleaners.rules import edit_distance

log = logging.getLogger("lyssna.dictation.vocabulary")

MAX_SPAN_WORDS = 3
MAX_TERM_CHARS = 60
# Evidence weights. A common word counts COMMON_WORD against a replacement; a recorded wrong
# form counts log2(1 + sightings), so a common word that was corrected once (1.0) stays, and
# one corrected four times (2.3) is replaced. A near miss counts its similarity (at most 1.0),
# so it can only ever replace a word that is not a common one.
COMMON_WORD = 2.0
CASE_MATCH = 10.0
NEAR_MISS = 0.8  # phonetic similarity a word needs to count as a mishearing of a term
SAME_SOUND = 0.5  # the looser bar a correction must clear to be learned at all
MIN_NEAR_MISS_KEY = 4  # shorter terms sit too close to too many words for near misses

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_SUFFIXES = ("'s", "’s", "s")  # possessive and plural endings kept on a replaced term
_TECHNICAL_CHARS = set("@/\\{}[]()<>=;$#%^&*|~`_+")


def default_path() -> Path:
    return config.STATE_DIR / "dictation" / "vocabulary.json"


# -- language data ---------------------------------------------------------------------


@lru_cache(maxsize=1)
def common_words() -> frozenset[str]:
    """The bundled list of common lowercase English words."""
    path = Path(__file__).parent / "data" / "common_words.txt"
    try:
        return frozenset(path.read_text(encoding="utf-8").split())
    except OSError:
        log.warning("the common words list is missing; vocabulary corrections will be more eager")
        return frozenset()


def _base_word(word: str) -> str:
    """A word lowercased, with a possessive ending removed and curly apostrophes made straight."""
    word = word.lower().replace("’", "'")
    return word[:-2] if word.endswith("'s") else word


def is_common(phrase: str) -> bool:
    """Is every word of `phrase` a common word? Words with digits count as common, so numbers
    are never touched."""
    words = _WORD_RE.findall(phrase)
    vocab = common_words()
    return bool(words) and all(any(c.isdigit() for c in w) or _base_word(w) in vocab for w in words)


_SOUND_RULES = (
    ("sch", "sk"), ("ph", "f"), ("ck", "k"), ("qu", "kw"), ("q", "k"), ("x", "ks"), ("z", "s"),
    ("wh", "w"), ("th", "t"), ("dg", "j"),
)


@lru_cache(maxsize=4096)
def sound_key(text: str) -> str:
    """A rough phonetic key: letters only, spellings of the same sound made equal, every run of
    vowels written as one "a", a silent final "e" and doubled consonants dropped. "Matias" and
    "Mattias" share one, as do "Rise" and "Ryse", or "Cocoro" and "Kokoro". Each word is keyed
    on its own and the keys are joined."""
    return "".join(_word_key(w) for w in text.split())


def _word_key(word: str) -> str:
    s = "".join(ch for ch in word.lower() if ch.isalpha())
    for old, new in _SOUND_RULES:
        s = s.replace(old, new)
    s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k")
    if len(s) > 2 and s.endswith("e") and s[-2] not in "aeiouy":
        s = s[:-1]
    s = re.sub(r"(?<=[^aeiouy])h", "", s)  # a silent h after a consonant
    head, rest = (s[:1], s[1:]) if s.startswith("y") else ("", s)  # a leading y is a consonant
    s = head + re.sub(r"[aeiouy]+", "a", rest)
    return re.sub(r"(.)\1+", r"\1", s)


def sound_similarity(a: str, b: str) -> float:
    """How alike two words sound, from 0 (nothing alike) to 1 (the same key)."""
    ka, kb = sound_key(a), sound_key(b)
    if not ka or not kb:
        return 0.0
    return 1.0 - edit_distance(ka, kb) / max(len(ka), len(kb))


# -- entries ---------------------------------------------------------------------------


@dataclass
class Entry:
    term: str
    heard_as: dict[str, int] = field(default_factory=dict)
    source: str = "manual"  # "manual" | "learned"; any other value in an older file is kept as is
    uses: int = 0
    added: str = ""
    deleted: bool = False

    def to_json(self) -> dict:
        return {"term": self.term, "heard_as": self.heard_as, "source": self.source, "uses": self.uses,
                "added": self.added, "deleted": self.deleted}

    @classmethod
    def from_json(cls, data: dict) -> Entry | None:
        term = data.get("term")
        if not isinstance(term, str) or not term.strip():
            return None
        heard = data.get("heard_as") or {}
        return cls(
            term=term,
            heard_as={str(k): int(v) for k, v in heard.items() if isinstance(v, int)} if isinstance(heard, dict) else {},
            source=str(data.get("source") or "manual"),
            uses=int(data.get("uses") or 0),
            added=str(data.get("added") or ""),
            deleted=bool(data.get("deleted", False)),
        )


def _phrase(text: str) -> str:
    """A term or heard form as stored: outer punctuation and extra spaces removed."""
    return " ".join(text.split()).strip(" \t\n\"'“”‘’.,;:!?")


def _form(text: str) -> str:
    """The lookup form of a heard phrase: lowercase, straight apostrophes."""
    return _phrase(text).lower().replace("’", "'")


# -- evidence ----------------------------------------------------------------------------


def heard_evidence(span: str, entry: Entry) -> float:
    """The span is a recorded wrong form of the term: log2(1 + times seen)."""
    seen = entry.heard_as.get(_form(span), 0)
    return math.log2(1 + seen) if seen else 0.0


def near_miss_evidence(span: str, entry: Entry) -> float:
    """The span sounds like the term: its phonetic similarity, when it is close enough, has as
    many words, and the term is long enough for a near miss to mean something."""
    if len(span.split()) != len(entry.term.split()) or len(sound_key(entry.term)) < MIN_NEAR_MISS_KEY:
        return 0.0
    similarity = sound_similarity(span, entry.term)
    return similarity if similarity >= NEAR_MISS else 0.0


def case_evidence(span: str, entry: Entry) -> float:
    """The span is the term in another case. That takes the term's casing, except for a term
    that is itself a plain lowercase common word ("slack"): only a term the user wrote with a
    capital ("Slack") changes the case of an ordinary word, so a lowercase one never touches,
    say, the capital at the start of a sentence."""
    if span.lower() != entry.term.lower() or span == entry.term:
        return 0.0
    return 0.0 if _is_plain_common(entry.term) else CASE_MATCH


def common_word_evidence(span: str) -> float:
    """The span is ordinary language the speaker may well have meant."""
    return COMMON_WORD if is_common(span) else 0.0


def evidence_for_term(span: str, entry: Entry) -> float:
    """The strongest single reason to think the speaker said the term. The reasons are not
    added up: a recorded wrong form already passed the sound test when it was learned."""
    return max(heard_evidence(span, entry), near_miss_evidence(span, entry), case_evidence(span, entry))


# -- learning filters -----------------------------------------------------------------------


def _word_count_ok(phrase: str) -> bool:
    return 1 <= len(phrase.split()) <= MAX_SPAN_WORDS


def _looks_technical(phrase: str) -> bool:
    """A number, a URL, a path, an email fragment or code: not vocabulary."""
    if any(ch.isdigit() for ch in phrase) or any(ch in _TECHNICAL_CHARS for ch in phrase):
        return True
    return "://" in phrase or phrase.lower().startswith("www") or bool(re.search(r"\w\.\w", phrase))


def _is_plain_common(phrase: str) -> bool:
    """All lowercase and all common words: a dictionary entry like that adds no spelling."""
    return phrase == phrase.lower() and is_common(phrase)


def _sounds_alike(observed: str, corrected: str) -> bool:
    return sound_similarity(observed, corrected) >= SAME_SOUND


# -- the store -------------------------------------------------------------------------------


class Vocabulary:
    """Thread-safe. Loaded on first use; every change is written to disk at once."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.RLock()
        self._entries: list[Entry] | None = None
        self._stamp: tuple[int, int] | None = None  # (mtime_ns, size) of the file as last read or written

    @property
    def path(self) -> Path:
        return self._path or default_path()

    # storage

    def _file_stamp(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _load(self) -> list[Entry]:
        """The entries, read again when the file changed underneath (the command line edits
        the same file the app has loaded)."""
        if self._entries is not None and self._file_stamp() != self._stamp:
            self._entries = None
        if self._entries is None:
            entries: list[Entry] = []
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                raw = data.get("entries", []) if isinstance(data, dict) else []
                entries = [e for e in (Entry.from_json(d) for d in raw if isinstance(d, dict)) if e]
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                log.warning("the vocabulary file could not be read (%s); keeping it aside and starting empty",
                            type(e).__name__)
                try:
                    os.replace(self.path, self.path.with_name(self.path.name + ".broken"))
                except OSError:
                    pass
            self._entries = entries
            self._stamp = self._file_stamp()
        return self._entries

    def _save(self) -> None:
        entries = self._entries if self._entries is not None else []
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_name(path.name + ".tmp")
        text = json.dumps({"version": 1, "entries": [e.to_json() for e in entries]}, indent=1, ensure_ascii=False)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        self._stamp = self._file_stamp()

    def _find(self, term: str) -> Entry | None:
        key = term.lower()
        return next((e for e in self._load() if e.term.lower() == key), None)

    def _active(self) -> list[Entry]:
        return [e for e in self._load() if not e.deleted]

    # reading

    def entries(self) -> list[Entry]:
        """Every entry that is not deleted, most used first."""
        with self._lock:
            return sorted((Entry(**e.__dict__) for e in self._active()), key=lambda e: (-e.uses, e.term.lower()))

    def terms(self) -> list[str]:
        return [e.term for e in self.entries()]

    def hints(self, limit: int = 100) -> list[str]:
        """The terms for engines that take a list of hints, most used first."""
        return self.terms()[: max(0, limit)]

    # editing

    def add(self, term: str, heard_as: str | None = None, source: str = "manual") -> str | None:
        """Add a term (or bring a deleted one back). Returns the term as stored, or None when it
        is empty or too long."""
        term = _phrase(term)
        if not term or len(term) > MAX_TERM_CHARS:
            return None
        with self._lock:
            entry = self._find(term)
            if entry is None:
                entry = Entry(term=term, source=source, added=date.today().isoformat())
                self._load().append(entry)
            elif entry.deleted and source != "manual":
                return None  # only the user can bring back a word they deleted
            entry.deleted = False
            entry.term = term
            if heard_as and _form(heard_as) and _phrase(heard_as) != term:
                form = _form(heard_as)
                entry.heard_as[form] = entry.heard_as.get(form, 0) + 1
            self._save()
            return term

    def remove(self, term: str) -> bool:
        """Delete a term. It stays as a tombstone so it is not learned again."""
        with self._lock:
            entry = self._find(_phrase(term))
            if entry is None or entry.deleted:
                return False
            entry.deleted = True
            entry.heard_as = {}
            self._save()
            return True

    def learn(self, observed: str, corrected: str) -> str | None:
        """Record a correction the user made to pasted text. Returns the learned term, or None
        when the pair does not look like a misheard word (see the filters above)."""
        obs, cor = _phrase(observed), _phrase(corrected)
        if not (obs and cor) or obs == cor:
            return None
        if not (_word_count_ok(obs) and _word_count_ok(cor)) or len(cor) > MAX_TERM_CHARS:
            return None
        if _looks_technical(obs) or _looks_technical(cor):
            return None
        if _is_plain_common(cor) or not _sounds_alike(obs, cor):
            return None
        with self._lock:
            entry = self._find(cor)
            if entry is not None and entry.deleted:
                return None
            if entry is None:
                entry = Entry(term=cor, source="learned", added=date.today().isoformat())
                self._load().append(entry)
            elif entry.term != cor:
                entry.term = cor  # the user's latest casing wins
            form = _form(obs)
            entry.heard_as[form] = entry.heard_as.get(form, 0) + 1
            self._save()
        log.info("learned a vocabulary term (%d words, %d sightings of that form)", len(cor.split()), entry.heard_as[form])
        return cor

    def used(self, text: str) -> None:
        """Count the terms that appear in pasted text, so the most used ones go first as hints."""
        with self._lock:
            changed = False
            for entry in self._active():
                pattern = r"(?<!\w)" + re.escape(entry.term) + r"(?:['’]s|s)?(?!\w)"
                n = len(re.findall(pattern, text))
                if n:
                    entry.uses += n
                    changed = True
            if changed:
                self._save()

    # correcting text

    def correct(self, text: str) -> str:
        """Replace every run of 1 to 3 words that the evidence says is a misheard term."""
        with self._lock:
            active = self._active()
        if not active or not text:
            return text
        tokens = list(_WORD_RE.finditer(text))
        out: list[str] = []
        pos = 0
        i = 0
        while i < len(tokens):
            for size in range(min(MAX_SPAN_WORDS, len(tokens) - i), 0, -1):
                if not _joined_by_spaces(text, tokens[i : i + size]):
                    continue
                start, end = tokens[i].start(), tokens[i + size - 1].end()
                replacement = _decide(text[start:end], active)
                if replacement is not None:
                    out.append(text[pos:start])
                    out.append(replacement)
                    pos = end
                    i += size
                    break
            else:
                i += 1
        out.append(text[pos:])
        return "".join(out)


def _joined_by_spaces(text: str, tokens: list[re.Match]) -> bool:
    """The words of a span sit on one line with only spaces between them."""
    for a, b in itertools.pairwise(tokens):
        gap = text[a.end() : b.start()]
        if not gap or gap.strip(" \t"):
            return False
    return True


def _forms(span: str) -> Iterable[tuple[str, str]]:
    """The span without a possessive or plural ending, then as written: (core, ending). The
    endings come first so that, on equal evidence, a replacement keeps the ending."""
    for suffix in _SUFFIXES:
        if span.endswith(suffix) and len(span) - len(suffix) >= 3:
            yield span[: -len(suffix)], span[-len(suffix) :]
    yield span, ""


def _decide(span: str, entries: list[Entry]) -> str | None:
    """The replacement for a span, or None to keep it. Ties keep the speaker's words."""
    if any(ch.isdigit() for ch in span):
        return None
    against = common_word_evidence(span)
    best, best_margin = None, 0.0
    for core, suffix in _forms(span):
        for entry in entries:
            if core == entry.term:
                return None  # already spelled right
            margin = evidence_for_term(core, entry) - against
            if margin > best_margin:
                best, best_margin = entry.term + suffix, margin
    return best


_shared: Vocabulary | None = None
_shared_lock = threading.Lock()


def shared() -> Vocabulary:
    """The one vocabulary the app uses (tests get a fresh one each)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Vocabulary()
        return _shared
