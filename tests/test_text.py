from lyssna.text import chunk, clean, prepare


def test_markdown_is_stripped():
    out = clean("# Title\n\nSome **bold** and `code` with a [link](https://x.y).\n\n```py\nx=1\n```")
    assert out == "Title.\n\nSome bold and code with a link.\n\n(code block omitted)"


def test_tables_read_row_by_row():
    assert clean("| a | b |\n|---|---|\n| 1 | 2 |") == "a, b.\n\n1, 2."


def test_abbreviations_dont_split():
    assert prepare("Dr. Smith agreed. So did Mr. Jones.") == ["Dr. Smith agreed. So did Mr. Jones."]


def test_one_sentence_per_chunk():
    text = (
        "The first sentence is long enough to stand alone here. So is the second sentence of this text. "
        "And the third one is as well, naturally."
    )
    assert chunk(text) == [
        "The first sentence is long enough to stand alone here.",
        "So is the second sentence of this text.",
        "And the third one is as well, naturally.",
    ]


def test_trailing_fragment_joins_its_own_paragraph():
    assert chunk("A sentence that is long enough on its own. Short end.") == ["A sentence that is long enough on its own. Short end."]


def test_tiny_sentences_merge_forward_but_not_across_paragraphs():
    text = "Really? Yes. It absolutely does work like that in practice.\n\nOk. Fine."
    assert chunk(text) == ["Really? Yes. It absolutely does work like that in practice.", "Ok. Fine."]


def test_long_sentences_are_cut_at_commas_and_nothing_is_lost():
    text = " ".join(f"clause number {i}," for i in range(120)) + " and the end."
    parts = chunk(text, limit=200)
    assert all(len(p) <= 200 for p in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")
