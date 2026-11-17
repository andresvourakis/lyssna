"""Deterministic, model-free cleanup of a dictation transcript.

`light_clean` is the text that gets pasted whenever no cleanup model runs, or the guard
rejects a model's output, or the model misses its time budget. It only makes changes that are
safe without understanding the text: it removes filler sounds, collapses accidental word
repeats, and fixes sentence capitalization and the final period. It never paraphrases, so it
can never change a fact. The user's spellings are applied separately, by
`lyssna.dictation.vocabulary`, to whichever text is pasted.

Standard library only."""

from __future__ import annotations

import re

__all__ = ["FILLER_SOUNDS", "NUMBER_WORDS", "edit_distance", "light_clean"]

# Stand-alone filler sounds. Words such as "like", "you know" and "I mean" are deliberately
# absent: without a model it is not possible to tell when they carry meaning.
FILLER_SOUNDS: frozenset[str] = frozenset({"um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "mm"})

# Spelled-out number words, so a spoken "four four two" is never collapsed as a repeat.
NUMBER_WORDS: frozenset[str] = frozenset(
    {
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
        "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
        "thousand", "million", "billion", "half", "quarter",
    }
)

# Repeats of these words are usually intended ("we had had enough", "no no"), so they stay.
_KEEP_REPEATED: frozenset[str] = frozenset({"had", "that", "very", "no", "bye"})

# A filler sound as a whole word. Hyphens and apostrophes count as part of the word, so
# "uh-huh" and "mm-hmm" are left alone.
_FILLER_RE = re.compile(
    r"(?P<pre>,\s*)?(?<![\w'-])(?:" + "|".join(sorted(FILLER_SOUNDS)) + r")(?![\w'-])(?P<post>\s*,)?",
    re.IGNORECASE,
)
_WORD_CHAR_RE = re.compile(r"\w")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:?!])")
_REPEATED_COMMA_RE = re.compile(r",(?:\s*,)+")
_LEADING_PUNCT_RE = re.compile(r"^[\s,;:]+")
_SENTENCE_START_RE = re.compile(r"([.?!][\"')\]]*\s+)([a-z])")
_TERMINAL_RE = re.compile(r"[.?!][\"')\]]*$")
_DIGITS_RE = re.compile(r"^\d+(?:[.,:]\d+)*(?:st|nd|rd|th)?$", re.IGNORECASE)

# Punctuation that ends a phrase; a repeat is not collapsed across it.
_PHRASE_END = ".?!;:"


def light_clean(raw: str) -> str:
    """`raw` with only safe, deterministic fixes applied.

    In order: collapse whitespace, remove filler sounds (with an adjacent comma), collapse an
    immediately repeated word or two-word phrase, capitalize sentence starts, and add a final
    period to texts of three or more words. Returns "" only when the input is empty or holds
    nothing but filler sounds."""
    text = " ".join(raw.split())
    if not text:
        return ""
    without_fillers = _remove_fillers(text)
    if not _WORD_CHAR_RE.search(without_fillers):
        # Only fillers (and punctuation) were spoken: nothing to type. Input without any word
        # characters at all is returned untouched instead.
        return "" if _WORD_CHAR_RE.search(text) else text
    text = _collapse_repeats(without_fillers)
    return _fix_capitalization_and_end(text)


def _filler_replacement(match: re.Match[str]) -> str:
    """Between two commas ("Tuesday, um, because") one comma stays, because the sentence
    usually needs it. A single adjacent comma goes with the filler."""
    if match.group("pre") and match.group("post"):
        return ", "
    return " "


def _remove_fillers(text: str) -> str:
    text = _FILLER_RE.sub(_filler_replacement, text)
    text = " ".join(text.split())
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _REPEATED_COMMA_RE.sub(",", text)
    text = _LEADING_PUNCT_RE.sub("", text)
    return text.strip()


def _is_number(word: str) -> bool:
    return bool(_DIGITS_RE.match(word)) or word in NUMBER_WORDS


def _key(token: str) -> str:
    """Comparison key for repeat detection: lowercase, outer punctuation removed."""
    return token.strip("\"'()[],.;:?!").lower()


def _ends_phrase(token: str) -> bool:
    return token.rstrip("\"')]")[-1:] in _PHRASE_END


def _can_collapse(first: list[str], second: list[str]) -> bool:
    """Is `second` an accidental repeat of `first`?"""
    keys = [_key(t) for t in first]
    if not all(keys) or keys != [_key(t) for t in second]:
        return False
    if any(_ends_phrase(t) for t in first):
        return False
    if any(_is_number(k) for k in keys):
        return False
    return not (len(keys) == 1 and keys[0] in _KEEP_REPEATED)


def _collapse_repeats(text: str) -> str:
    """Collapse "the the" and "we should we should" to one copy. The first copy keeps its
    casing and takes the trailing punctuation of the second, so "the, the main" becomes
    "the main"."""
    tokens = text.split(" ")
    changed = True
    while changed:
        changed = False
        out: list[str] = []
        i = 0
        while i < len(tokens):
            collapsed = False
            for size in (2, 1):
                first = tokens[i : i + size]
                second = tokens[i + size : i + 2 * size]
                if len(second) == size and _can_collapse(first, second):
                    out.extend([*first[:-1], _with_trailing_punct(first[-1], second[-1])])
                    i += 2 * size
                    collapsed = changed = True
                    break
            if not collapsed:
                out.append(tokens[i])
                i += 1
        tokens = out
    return " ".join(tokens)


def _with_trailing_punct(word: str, source: str) -> str:
    core = word.rstrip(",.;:?!")
    return core + source[len(source.rstrip(",.;:?!")) :]


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance between two short strings."""
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def _fix_capitalization_and_end(text: str) -> str:
    """Capitalize the first letter and letters after ". ? !"; end with a period."""
    for index, char in enumerate(text):
        if char.isalpha():
            text = text[:index] + char.upper() + text[index + 1 :]
            break
    text = _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    if len(text.split()) >= 3 and not _TERMINAL_RE.search(text):
        text = text.rstrip(",;: ") + "."
    return text
