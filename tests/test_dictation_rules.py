"""The rules-only cleanup (lyssna.dictation.cleaners.rules.light_clean), ported from the
dictation bake-off. The vocabulary spellings it used to apply now live in the vocabulary."""

import pytest

from lyssna.dictation.cleaners.rules import edit_distance, light_clean


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # whitespace
        ("  hello   there  ", "Hello there"),
        ("", ""),
        ("   ", ""),
        # filler sounds, with the comma that hugs them
        ("um so i think we should go", "So i think we should go."),
        ("Um, so I was thinking that, uh, we could go.", "So I was thinking that, we could go."),
        ("Tuesday, um, because it is.", "Tuesday, because it is."),
        ("It is late. Um, so we go.", "It is late. So we go."),
        ("I think, um we go", "I think we go."),
        ("UMM we Hmm agree", "We agree"),
        ("er, erm, uhm, uhh mm the plan", "The plan"),
        ("I said uh-huh and mm-hmm", "I said uh-huh and mm-hmm."),
        ("the summer was nice", "The summer was nice."),
        # only fillers
        ("um uh", ""),
        ("Um, uh, hmm.", ""),
        # repeated words and two-word phrases
        ("what's the the capital of france again", "What's the capital of france again."),
        ("we should we should move it", "We should move it."),
        ("I, I think so", "I think so."),
        ("the the the end is near", "The end is near."),
        # repeats that stay
        ("we had had enough", "We had had enough."),
        ("he said that that was fine", "He said that that was fine."),
        ("it was very very good", "It was very very good."),
        ("no no that is wrong", "No no that is wrong."),
        ("bye bye now", "Bye bye now."),
        ("call four four two", "Call four four two."),
        ("room 12 12 please", "Room 12 12 please."),
        ("Go. Go now", "Go. Go now."),
        # capitalization after . ? ! and the final period
        ("hello. how are you? fine! ok", "Hello. How are you? Fine! Ok."),
        ("one two", "One two"),
        ("one two three", "One two three."),
        ("is it ready?", "Is it ready?"),
        ("send it to me,", "Send it to me."),
        ("a list:", "A list:"),
        # no dictionary any more: names are left as written
        ("i use lyssna daily", "I use lyssna daily."),
    ],
)
def test_light_clean(raw: str, expected: str) -> None:
    assert light_clean(raw) == expected


def test_never_empty_unless_only_fillers() -> None:
    assert light_clean("...") == "..."
    assert light_clean("ok") == "Ok"


def test_edit_distance() -> None:
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3
    assert edit_distance("same", "same") == 0
