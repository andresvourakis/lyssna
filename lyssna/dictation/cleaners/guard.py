"""Deterministic check that a cleanup model's output is still the speaker's text.

A small local model can answer a dictated question, obey a dictated command, drop a "not" or
change a number. `check` compares the text the cleaner was given with the cleaner's output
and rejects anything that looks like one of those failures; the pipeline then pastes the
rules-only text instead.

Spoken self-corrections ("fifteen, no sorry, fifty") legitimately remove words, numbers and
negations. A strong correction cue therefore opens a *correction span* (the cue plus the 8
words before it); only tokens inside a span may be dropped. Everything outside must survive.

Numbers are compared as spoken-digit strings. Each text is split into number groups (maximal
runs of number tokens, where digits, spelled numbers, ordinals and the joiners
`: , . - / $ % and` between them belong to one group), and each group is reduced to the digits
a person would say, in order: "four ninety nine", "$499" and "4.99" are all "499", "six thirty"
and "6:30" are "630", "twenty twenty six" is "2026". So writing a number differently is never a
change, while a different digit, a lost number or an invented one is.

Rejection reasons are short codes and never contain text from the dictation, so they are safe
to log. Standard library only."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .rules import FILLER_SOUNDS, edit_distance, light_clean

__all__ = ["CORRECTION_CUES", "GuardResult", "check", "final_text", "number_groups"]


@dataclass(frozen=True)
class GuardResult:
    """Verdict of `check`: `ok` and a short reason code ("ok" when accepted)."""

    ok: bool
    reason: str


_OK = GuardResult(True, "ok")

# Strong self-correction cues, as normalized word sequences. Everyday words such as a bare
# "actually", "rather" or "sorry" are deliberately not cues.
CORRECTION_CUES: tuple[tuple[str, ...], ...] = (
    ("no", "wait"),
    ("no", "sorry"),
    ("scratch", "that"),
    ("strike", "that"),
    ("i", "meant"),
    ("sorry", "i", "mean"),
    ("no", "i", "mean"),
    ("actually", "no"),
    ("no", "actually"),
    ("or", "rather"),
    ("let", "me", "rephrase"),
    ("correction",),
)
_SPAN_WORDS_BEFORE = 8
_CUE_ALTERNATIVE_WORDS_AFTER = 4
_RESTART_WINDOW = 6

_NEGATIONS: frozenset[str] = frozenset({"not", "no", "never", "none", "nothing", "nobody", "neither", "nor", "without"})

_TAGS: tuple[str, ...] = ("<transcript", "</transcript", "<think", "</think", "<dictionary")

_PREAMBLES: tuple[str, ...] = (
    "here is", "here's the", "sure", "certainly", "cleaned", "output:", "the cleaned",
    "i'm sorry", "i cannot", "i can't", "as an ai", "i'm the", "i am the", "i'm not able",
    "i don't have", "there's no transcript", "there is no transcript", "could you",
    "please provide",
)

_QUESTION_OPENERS: frozenset[str] = frozenset(
    """
    what why how when where who whom whose which can could would should shall do does
    did is are am was were will have has had may might must
    """.split()
)
# Words a speaker often puts before a question opener ("so, what do we do").
_LEAD_INS: frozenset[str] = FILLER_SOUNDS | {"so", "okay", "ok", "and", "but", "oh", "well", "hey"}
_NT_BASES = {"wo": "will", "ca": "can", "sha": "shall"}

# Function words ignored by the overlap check: a model may add or drop these when fixing
# grammar without changing what was said.
_STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if so than then that this these those there here it its
    it's i me my mine we us our you your he him his she her they them their
    is am are was were be been being do does did done have has had having will
    would shall should can could may might must to of in on at by for with from
    as into onto about over under up down out off just also too very really
    what which who whom whose when where why how all any some each every both
    not no yes oh okay ok well let's let im i'm i've i'll i'd you're we're
    they're that's there's what's
    """.split()
)

# -- spelled numbers -------------------------------------------------------------------

_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine".split())}
_TEENS = {w: i + 10 for i, w in enumerate(
    "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: (i + 2) * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_ORD_UNITS = {w: i + 1 for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth".split())}
_ORD_TEENS = {w: i + 10 for i, w in enumerate(
    "tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth".split())}
_ORD_TENS = {w: (i + 2) * 10 for i, w in enumerate(
    "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth".split())}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
# Spoken list markers ("first ... second ..."), ignored in raw when cleaned is a list.
_LIST_ORDINALS = frozenset([*_ORD_UNITS, "tenth"])
# Words that make a lone "one" a number ("one hour", "one dollar").
_MEASURE_UNITS = frozenset(
    """
    dollar dollars cent cents euro euros pound pounds percent minute minutes hour hours
    day days week weeks month months year years second seconds time times mile miles
    kilometer kilometers km kg gb mb k
    """.split()
)
# Characters that join two numbers into one group when they are all that stands between them.
_JOINER_CHARS = frozenset(":,.-/$%")
# Words that join two numbers. "and" is listed in the design; "point" and "oh" are the spoken
# forms of the decimal point and of the digit zero ("three point five", "six oh five").
_JOINER_WORDS = frozenset({"and", "point", "oh"})

# -- tokens ----------------------------------------------------------------------------

# A list marker at the start of a line: "1. ", "2) ", "- ", "* ", "• ".
_LIST_MARKER_RE = re.compile(r"(?m)^[ \t]*(?:\d+[.)]|[-*•])(?=[ \t])")
# A number ("1,200", "3.5", "42", "12th") or a word with inner apostrophes ("don't").
_TOKEN_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?(?:st|nd|rd|th)?\b|\d+|[^\W\d_]+(?:'[^\W\d_]+)*")
_DIGITS_RE = re.compile(r"\d")
_ORDINAL_SUFFIX_RE = re.compile(r"(?:st|nd|rd|th)$")


def _normalize(text: str) -> str:
    return _LIST_MARKER_RE.sub(" ", text).lower().replace("’", "'")


def _tokens(text: str) -> list[str]:
    """Normalized words: lowercase, punctuation stripped, list markers removed. Numbers stay
    whole ("1,200", "3.5", "12th"); "6:30" becomes "6" and "30"."""
    return _TOKEN_RE.findall(_normalize(text))


def _tokens_and_gaps(text: str) -> tuple[list[str], list[str]]:
    """The tokens, and for each one the text between it and the token before it."""
    text = _normalize(text)
    tokens, gaps, end = [], [], 0
    for m in _TOKEN_RE.finditer(text):
        tokens.append(m.group(0))
        gaps.append(text[end : m.start()])
        end = m.end()
    return tokens, gaps


def _is_list(text: str) -> bool:
    return bool(_LIST_MARKER_RE.search(text))


def _flat(word: str) -> str:
    """A word without apostrophes, so "what's" in one text matches "whats" in the other."""
    return word.replace("'", "")


def _is_negation(word: str) -> bool:
    return word in _NEGATIONS or word.endswith("n't")


# -- correction spans and restarts ---------------------------------------------------------


@dataclass(frozen=True)
class _Cue:
    """One correction cue in raw: the span it opens and the words after it."""

    span_start: int
    cue_end: int  # exclusive
    after_end: int  # exclusive end of the "replacement" window after the cue


def _find_cues(tokens: Sequence[str]) -> list[_Cue]:
    cues = []
    for i in range(len(tokens)):
        for cue in CORRECTION_CUES:
            if tuple(tokens[i : i + len(cue)]) == cue:
                end = i + len(cue)
                cues.append(_Cue(max(0, i - _SPAN_WORDS_BEFORE), end, min(len(tokens), end + _CUE_ALTERNATIVE_WORDS_AFTER)))
                break
    return cues


def _span_mask(n: int, cues: Sequence[_Cue]) -> list[bool]:
    """For each raw token, whether it lies inside a correction span."""
    mask = [False] * n
    for cue in cues:
        for i in range(cue.span_start, cue.cue_end):
            mask[i] = True
    return mask


def _restart_mask(tokens: Sequence[str]) -> list[bool]:
    """Raw tokens that a false start repeats shortly after ("i think the, i think the"): a
    token immediately repeated, or a 2 to 4 word phrase that recurs within the next 6 words."""
    n = len(tokens)
    mask = [False] * n
    for i in range(n):
        if i + 1 < n and tokens[i] == tokens[i + 1]:
            mask[i] = True
        for size in range(4, 1, -1):
            phrase = list(tokens[i : i + size])
            if len(phrase) < size:
                continue
            for j in range(i + 1, min(n - size, i + _RESTART_WINDOW) + 1):
                if list(tokens[j : j + size]) == phrase:
                    for k in range(i, i + size):
                        mask[k] = True
                    break
    return mask


def _negation_restart(tokens: Sequence[str], i: int) -> bool:
    """True when the negation at `i` appears again within the next six words."""
    return tokens[i] in tokens[i + 1 : i + 1 + _RESTART_WINDOW]


# -- number groups ---------------------------------------------------------------------------


def _classify(word: str) -> tuple[str, int] | None:
    """Kind and value of a spelled number word, or None."""
    for table, kind in (
        (_UNITS, "unit"), (_TEENS, "teen"), (_TENS, "tens"),
        (_ORD_UNITS, "ord_unit"), (_ORD_TEENS, "ord_teen"), (_ORD_TENS, "ord_tens"),
    ):
        if word in table:
            return kind, table[word]
    if word in _SCALES:
        return "scale", _SCALES[word]
    return None


def _is_number_word(word: str) -> bool:
    return bool(_DIGITS_RE.match(word)) or _classify(word) is not None


def _joins(gap: str) -> bool:
    """True when the text between two tokens can sit inside one number group."""
    return all(ch.isspace() or ch in _JOINER_CHARS for ch in gap)


def _starts_group(tokens: Sequence[str], i: int) -> bool:
    if _is_number_word(tokens[i]):
        return True
    return tokens[i] == "a" and i + 1 < len(tokens) and tokens[i + 1] in _SCALES


def _compose(words: Sequence[str]) -> list[int]:
    """Spelled numbers composed the usual way, split where a new number starts: "forty two"
    is [42], "four hundred and five" is [405], "four ninety nine" is [4, 99], "twenty twenty
    six" is [20, 26], "twelfth" is [12]."""
    values: list[int] = []
    total, current, last, started = 0, None, None, False

    def flush() -> None:
        nonlocal total, current, last, started
        if started:
            values.append(total + (current or 0))
        total, current, last, started = 0, None, None, False

    for word in words:
        if word == "a":
            current, last, started = 1, "unit", True
            continue
        if word == "and":
            if last not in ("hundred", "scale"):
                flush()
            continue
        found = _classify(word)
        if found is None:
            flush()
            continue
        kind, value = found
        base = kind.removeprefix("ord_")
        if base == "scale":
            if value == 100:
                if last == "hundred":
                    flush()
                current = (current or 1) * 100
                last = "hundred"
            else:
                total += (current or 1) * value
                current, last = None, "scale"
        else:
            if last in ("unit", "teen") or (last == "tens" and base != "unit"):
                flush()
            current = (current or 0) + value
            last = base
        started = True
        if kind.startswith("ord_"):
            flush()
    flush()
    return values


def _digits_of(token: str) -> str:
    """The digits a digit token stands for: "1,200" is "1200", "3.5" is "35", "12th" is "12"."""
    return re.sub(r"\D", "", _ORDINAL_SUFFIX_RE.sub("", token))


def _group_digits(words: Sequence[str]) -> str:
    """One number group as the digits a person would say, in order."""
    parts: list[str] = []
    spelled: list[str] = []

    def flush() -> None:
        parts.extend(str(v) for v in _compose(spelled))
        spelled.clear()

    for word in words:
        if _DIGITS_RE.match(word):
            flush()
            parts.append(_digits_of(word))
        elif word == "oh":
            flush()
            parts.append("0")
        elif word == "point":
            flush()
        else:
            spelled.append(word)
    flush()
    return "".join(parts)


@dataclass(frozen=True)
class _Group:
    """One number group: the index of its first token, its digits, and whether it is only a
    word that is not counted as a number in some settings (a pronoun "one", a list ordinal)."""

    index: int
    digits: str
    pronoun_one: bool = False
    list_ordinal: bool = False


def _groups(tokens: Sequence[str], gaps: Sequence[str]) -> list[_Group]:
    n = len(tokens)
    groups: list[_Group] = []
    i = 0
    while i < n:
        if not _starts_group(tokens, i):
            i += 1
            continue
        members = [i]
        j = i + 1
        while j < n and _joins(gaps[j]):
            if _is_number_word(tokens[j]):
                members.append(j)
                j += 1
            elif (tokens[j] in _JOINER_WORDS and j + 1 < n and _joins(gaps[j + 1])
                  and _is_number_word(tokens[j + 1])):
                members.append(j)
                j += 1
            else:
                break
        words = [tokens[k] for k in members]
        next_word = tokens[j] if j < n else ""
        groups.append(_Group(
            index=i,
            digits=_group_digits(words),
            pronoun_one=words == ["one"] and next_word not in _MEASURE_UNITS,
            list_ordinal=len(words) == 1 and words[0] in _LIST_ORDINALS,
        ))
        i = j
    return [g for g in groups if g.digits]


def number_groups(text: str) -> list[str]:
    """The number groups of a text as digit strings, in order (a lone pronoun "one" left out)."""
    return [g.digits for g in _groups(*_tokens_and_gaps(text)) if not g.pronoun_one]


def _numbers_ok(raw: str, cleaned: str, cues: Sequence[_Cue], span: Sequence[bool]) -> bool:
    """Compare number groups: none invented or changed, none lost outside a correction."""
    is_list = _is_list(cleaned)
    raw_groups = [g for g in _groups(*_tokens_and_gaps(raw)) if not g.pronoun_one and not (is_list and g.list_ordinal)]
    cleaned_counts = Counter(number_groups(cleaned))
    if cleaned_counts - Counter(g.digits for g in raw_groups):
        return False  # a number raw does not have: invented or changed

    # Match raw groups outside spans first, so leftovers fall inside spans if possible.
    remaining = Counter(cleaned_counts)
    missing = []
    for g in sorted(raw_groups, key=lambda g: span[g.index]):
        if remaining[g.digits] > 0:
            remaining[g.digits] -= 1
        else:
            missing.append(g)
    for g in missing:
        if span[g.index]:
            continue
        # A number right after a cue is the replacement. The guard cannot know which of the
        # two the speaker meant, so either may survive, but one must.
        alternative = any(
            cue.cue_end <= g.index < cue.after_end
            and any(w.digits in cleaned_counts for w in raw_groups if cue.span_start <= w.index < cue.cue_end)
            for cue in cues
        )
        if not alternative:
            return False
    return True


# -- other checks ----------------------------------------------------------------------------


def _negation_ok(raw_tokens: Sequence[str], cleaned_tokens: Sequence[str], span: Sequence[bool]) -> bool:
    """No negation added; drops only for restarts and corrections; never lose them all."""
    raw_neg = [i for i, w in enumerate(raw_tokens) if _is_negation(w)]
    n_raw, n_clean = len(raw_neg), sum(map(_is_negation, cleaned_tokens))
    if n_clean > n_raw:
        return False
    allowed = sum(1 for i in raw_neg if span[i] or _negation_restart(raw_tokens, i))
    if n_raw - n_clean > allowed:
        return False
    return not (n_raw > 0 and n_clean == 0 and not all(span[i] for i in raw_neg))


def _starts_with(tokens: Sequence[str], phrase: Sequence[str]) -> bool:
    return list(tokens[: len(phrase)]) == list(phrase)


def _contains(tokens: Sequence[str], phrase: Sequence[str]) -> bool:
    k = len(phrase)
    return any(list(tokens[i : i + k]) == list(phrase) for i in range(len(tokens) - k + 1))


def _has_preamble(raw_tokens: Sequence[str], cleaned_tokens: Sequence[str]) -> bool:
    """Cleaned opens with a stock assistant phrase that the speaker did not say early on."""
    head = raw_tokens[:8]
    for phrase in _PREAMBLES:
        words = _tokens(phrase)
        if _starts_with(cleaned_tokens, words) and not _contains(head, words):
            return True
    return False


def _opener(word: str) -> str:
    """Base form for the question-opener test: "what's" is "what", "won't" is "will"."""
    if word.endswith("n't"):
        base = word[:-3]
        return _NT_BASES.get(base, base)
    return word.split("'")[0]


def _raw_has_question_cue(raw: str) -> bool:
    """True when raw has a "?" or a sentence or comma clause opening with a question word."""
    if "?" in raw:
        return True
    for sentence in re.split(r"[.!;\n]+", raw):
        for clause in sentence.split(","):
            words = [w for w in _tokens(clause) if w not in _LEAD_INS]
            if words and _opener(words[0]) in _QUESTION_OPENERS:
                return True
    return False


def _overlap_ok(raw_tokens: Sequence[str], cleaned_tokens: Sequence[str], terms: Sequence[str]) -> bool:
    """At least 70% of the cleaned content words appear in raw. A vocabulary word counts as
    present when some raw word is within edit distance 2 of it, because the speech model often
    misspells names. Number words are skipped: the numbers rule compares them already."""
    content = [w for w in cleaned_tokens if w not in _STOPWORDS and not _is_number_word(w)]
    if not content:
        return True
    raw_flat = {_flat(w) for w in raw_tokens}
    term_words = {_flat(part) for term in terms for part in _tokens(term)}
    present = 0
    for word in content:
        flat = _flat(word)
        if flat in raw_flat or (flat in term_words and any(edit_distance(flat, r) <= 2 for r in raw_flat)):
            present += 1
    return present >= 0.7 * len(content)


# -- public API -------------------------------------------------------------------------------


def check(raw: str, cleaned: str, terms: Sequence[str] = ()) -> GuardResult:
    """Accept or reject a model's cleaned version of `raw` (the text the cleaner was given).

    `terms` are the user's vocabulary terms; they only relax the overlap rule for words the
    speech model is likely to have misspelled. Checks run from the most specific failure to the
    most general, and the first failing check's code is returned."""
    terms = tuple(terms)
    stripped = cleaned.strip()

    if not stripped:
        if len(light_clean(raw).split()) >= 3:
            return GuardResult(False, "empty")
        return _OK

    lowered = stripped.lower()
    if stripped.startswith("```") or any(tag in lowered for tag in _TAGS):
        return GuardResult(False, "tags")

    raw_tokens = _tokens(raw)
    cleaned_tokens = _tokens(stripped)

    if _has_preamble(raw_tokens, cleaned_tokens):
        return GuardResult(False, "preamble")

    if stripped.rstrip("\"')]*_ ").endswith("?") and not _raw_has_question_cue(raw):
        return GuardResult(False, "question_added")

    cues = _find_cues(raw_tokens)
    span = _span_mask(len(raw_tokens), cues)

    if not _numbers_ok(raw, stripped, cues, span):
        return GuardResult(False, "numbers")

    if not _negation_ok(raw_tokens, cleaned_tokens, span):
        return GuardResult(False, "negation")

    raw_count, cleaned_count = len(raw_tokens), len(cleaned_tokens)
    if cleaned_count > raw_count + max(4, 0.15 * raw_count):
        return GuardResult(False, "too_long")

    if raw_count >= 8:
        restart = _restart_mask(raw_tokens)
        counted = sum(
            1 for i, w in enumerate(raw_tokens)
            if not (span[i] or restart[i] or (_is_negation(w) and _negation_restart(raw_tokens, i)))
        )
        if cleaned_count < 0.5 * counted:
            return GuardResult(False, "too_short")

    if not _overlap_ok(raw_tokens, cleaned_tokens, terms):
        return GuardResult(False, "overlap")

    return _OK


def final_text(raw: str, cleaned_or_none: str | None, terms: Sequence[str] = ()) -> tuple[str, str]:
    """The text to paste and where it came from: ("model") when the cleaner's output passed the
    guard, "rules:<code>" when it was rejected and `light_clean(raw)` is used instead, and
    "rules:none" when no cleaner ran. Vocabulary spellings are applied by the caller."""
    if cleaned_or_none is None:
        return light_clean(raw), "rules:none"
    verdict = check(raw, cleaned_or_none, terms)
    if verdict.ok:
        return cleaned_or_none.strip(), "model"
    return light_clean(raw), f"rules:{verdict.reason}"
