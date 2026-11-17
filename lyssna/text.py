"""Turn whatever the user copied (markdown, code, LinkedIn posts, terminal output)
into clean, speakable chunks."""

from __future__ import annotations

import re

MAX_CHUNK_CHARS = 320   # one sentence per chunk; very long sentences are split at commas
MIN_CHUNK_CHARS = 30    # sentences shorter than this are merged with the next one

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_URL = re.compile(r"https?://\S+|www\.\S+")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$", re.MULTILINE)
_TABLE_ROW = re.compile(r"^[ \t]*\|(.+)\|[ \t]*$", re.MULTILINE)
_HTML_TAG = re.compile(r"<[^>\n]{1,80}>")
_HASHTAG = re.compile(r"(?<!\w)#(\w+)")
_BOX_DRAWING = re.compile(r"[─-╿▀-▟]+")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_MULTI_SPACE = re.compile(r"[ \t]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
_ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "inc", "ltd", "co", "fig", "no", "approx",
    "dept", "est", "al",
}


def _heading_to_sentence(m: re.Match) -> str:
    title = m.group(1).strip()
    return title if title[-1:] in ".!?:" else title + "."


def _table_row(m: re.Match) -> str:
    cells = [c.strip() for c in m.group(1).split("|")]
    cells = [c for c in cells if c]
    return ", ".join(cells) + "." if cells else ""


def clean(text: str) -> str:
    """Strip markup that sounds terrible when read aloud."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI.sub("", text)
    text = _CODE_FENCE.sub("\n(code block omitted)\n", text)
    text = _MD_IMAGE.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _HTML_TAG.sub("", text)
    text = _TABLE_RULE.sub("", text)
    text = _HEADING.sub(_heading_to_sentence, text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _BULLET.sub("", text)
    text = _TABLE_ROW.sub(_table_row, text)
    text = _HASHTAG.sub(r"\1", text)
    text = _BOX_DRAWING.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text)
    # A line break inside a paragraph is just wrapping; a blank line is a real pause.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text)]
    paragraphs = [" ".join(line.strip() for line in p.split("\n") if line.strip()) for p in paragraphs if p]
    text = "\n\n".join(paragraphs)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def _split_sentences(paragraph: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(paragraph) if p and p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and merged[-1].rstrip(".").rsplit(" ", 1)[-1].lower() in _ABBREVIATIONS:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def _split_long(sentence: str, limit: int) -> list[str]:
    """Last resort for a single sentence longer than the limit: cut at commas or spaces."""
    out: list[str] = []
    while len(sentence) > limit:
        cut = max(sentence.rfind(", ", 0, limit), sentence.rfind(" ", 0, limit))
        if cut <= 0:
            cut = limit
        out.append(sentence[:cut].strip())
        sentence = sentence[cut:].strip()
    if sentence:
        out.append(sentence)
    return out


def chunk(text: str, limit: int = MAX_CHUNK_CHARS, min_chars: int = MIN_CHUNK_CHARS) -> list[str]:
    """Split clean text into one sentence per chunk.

    Tiny sentences ("Really?", "Yes.") are merged with the following one so a chunk is
    worth a request; a sentence longer than `limit` is cut at commas. Merges never cross
    a paragraph boundary, so paragraph pauses stay where the author put them.
    """
    chunks: list[str] = []
    for paragraph in text.split("\n\n"):
        first_in_paragraph = len(chunks)
        pending = ""
        for sentence in _split_sentences(paragraph):
            for piece in _split_long(sentence, limit):
                pending = f"{pending} {piece}".strip()
                if len(pending) >= min_chars:
                    chunks.append(pending)
                    pending = ""
        if pending:
            same_paragraph = len(chunks) > first_in_paragraph
            if same_paragraph and len(chunks[-1]) + 1 + len(pending) <= limit:
                chunks[-1] = f"{chunks[-1]} {pending}"  # trailing fragment joins its paragraph's last sentence
            else:
                chunks.append(pending)
    return chunks


def prepare(text: str) -> list[str]:
    return chunk(clean(text))


def preview(text: str, width: int = 48) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"
