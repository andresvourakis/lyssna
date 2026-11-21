"""The dictation vocabulary: what it learns from a correction, how `correct` decides, and how it
is stored. The file lives under the per-test temp dir (conftest). Sentences are written for the
tests; none come from a recording."""

import json
import os
import stat
import sys

import pytest

from lyssna.dictation import vocabulary as V
from lyssna.dictation.vocabulary import Entry, Vocabulary


@pytest.fixture
def vocab():
    v = Vocabulary()
    for term in ("Lyssna", "Parakeet", "Kokoro", "Claude Code", "Matias", "Vourakis"):
        v.add(term)
    return v


# -- the pieces of evidence, each on its own ---------------------------------------------------


def test_sound_key_makes_spellings_of_one_sound_equal():
    assert V.sound_key("Matias") == V.sound_key("Mattias")
    assert V.sound_key("Rise") == V.sound_key("Ryse")
    assert V.sound_key("Cocoro") == V.sound_key("Kokoro")
    assert V.sound_key("Cloud Code") == V.sound_key("Claude Code")
    assert V.sound_key("cat") != V.sound_key("invoice")


def test_heard_evidence_grows_with_sightings():
    entry = Entry("Ryse", heard_as={"rise": 1})
    assert V.heard_evidence("Rise", entry) == 1.0
    entry.heard_as["rise"] = 4
    assert V.heard_evidence("rise", entry) > V.COMMON_WORD
    assert V.heard_evidence("rose", entry) == 0.0


def test_near_miss_evidence_needs_a_close_sound_and_a_long_enough_term():
    assert V.near_miss_evidence("Parakeat", Entry("Parakeet")) == 1.0
    assert V.near_miss_evidence("invoice", Entry("Parakeet")) == 0.0
    assert V.near_miss_evidence("Ris", Entry("Ryse")) == 0.0  # three sounds: too short to guess
    assert V.near_miss_evidence("the lisna", Entry("Lyssna")) == 0.0  # different word count


def test_near_miss_evidence_never_beats_a_common_word():
    assert V.near_miss_evidence("Rice", Entry("Ryse")) <= 1.0 < V.common_word_evidence("rice")


def test_case_evidence():
    assert V.case_evidence("lyssna", Entry("Lyssna")) == V.CASE_MATCH
    assert V.case_evidence("Lyssna", Entry("Lyssna")) == 0.0  # already right
    assert V.case_evidence("slack", Entry("Slack")) == V.CASE_MATCH  # the user wrote it with a capital
    assert V.case_evidence("Slack", Entry("slack")) == 0.0  # a lowercase common word never changes case


def test_common_word_evidence():
    assert V.common_word_evidence("rise") == V.COMMON_WORD
    assert V.common_word_evidence("the main problem") == V.COMMON_WORD
    assert V.common_word_evidence("Listna") == 0.0
    assert V.common_word_evidence("cloud lyssna") == 0.0  # one uncommon word makes the phrase uncommon


def test_common_words_list_is_bundled_with_its_license():
    words = V.common_words()
    assert 30_000 < len(words) < 100_000
    assert {"rise", "invoice", "listener", "the"} <= words and "lyssna" not in words
    assert (V.Path(V.__file__).parent / "data" / "common_words.LICENSE").is_file()


# -- correct(): one decision rule ----------------------------------------------------------------


def test_learned_non_word_is_always_replaced(vocab):
    assert vocab.learn("Listna", "Lyssna") == "Lyssna"
    assert vocab.correct("I opened Listna today.") == "I opened Lyssna today."


def test_real_word_corrected_once_is_left_alone():
    v = Vocabulary()
    assert v.learn("Rise", "Ryse") == "Ryse"
    assert v.correct("Prices rise in the spring.") == "Prices rise in the spring."
    assert v.correct("I work at ryse now.") == "I work at Ryse now."  # its own spelling in another case


def test_real_word_seen_often_enough_is_replaced():
    v = Vocabulary()
    for _ in range(4):
        v.learn("Rise", "Ryse")
    assert v.correct("I work at rise.") == "I work at Ryse."


def test_near_miss_of_a_long_rare_term_is_replaced(vocab):
    assert vocab.correct("the parakeat model and the cocoro voice") == "the Parakeet model and the Kokoro voice"


def test_near_miss_that_is_a_common_word_is_left_alone(vocab):
    assert vocab.correct("a cloud code review") == "a cloud code review"  # "Claude Code" only when learned


def test_learned_multi_word_form(vocab):
    vocab.learn("cloud code", "Claude Code")
    for _ in range(3):
        vocab.learn("cloud code", "Claude Code")
    assert vocab.correct("pushed from cloud code today") == "pushed from Claude Code today"


def test_casing_follows_the_term(vocab):
    assert vocab.correct("i use lyssna and KOKORO with claude code") == "i use Lyssna and Kokoro with Claude Code"


def test_lowercase_common_term_does_not_touch_sentence_case():
    v = Vocabulary()
    v.add("slack")
    assert v.correct("Slack is where we talk.") == "Slack is where we talk."


def test_capitalized_term_that_is_a_common_word():
    v = Vocabulary()
    v.add("Slack")
    assert v.correct("post it in slack") == "post it in Slack"


def test_possessives_and_plurals_are_kept(vocab):
    assert vocab.correct("lyssna's settings") == "Lyssna's settings"
    assert vocab.correct("mattias’s laptop") == "Matias’s laptop"
    assert vocab.correct("two kokoros") == "two Kokoros"
    assert vocab.correct("three parakeats") == "three Parakeets"


def test_words_across_punctuation_are_not_one_span(vocab):
    vocab.learn("cloud code", "Claude Code")
    assert vocab.correct("Cloud. Code is good") == "Cloud. Code is good"


def test_numbers_are_never_touched():
    v = Vocabulary()
    v.add("Model 3")
    assert v.correct("we have 3 models") == "we have 3 models"


def test_empty_vocabulary_leaves_text_alone():
    assert Vocabulary().correct("anything at all") == "anything at all"


# -- learn(): the filters ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("observed", "corrected", "learned"),
    [
        ("Matias", "Mattias", "Mattias"),
        ("Rise", "Ryse", "Ryse"),
        ("listna", "Lyssna", "Lyssna"),
        ("slack", "Slack", "Slack"),  # case only, and the corrected form has a capital
        ("cloud code", "Claude Code", "Claude Code"),
        ("cat", "invoice", None),  # nothing alike
        ("there", "their", None),  # a plain lowercase common word adds no spelling
        ("Slack", "slack", None),
        ("same", "same", None),
        ("one two three four", "Lyssna", None),  # more than three words
        ("forty two", "42", None),  # a number
        ("example dot com", "example.com", None),  # a URL
        ("users folder", "/Users", None),  # a path
        ("matias at", "matias@", None),  # an email fragment
        ("get name", "get_name()", None),  # code
        ("", "Lyssna", None),
    ],
)
def test_learn_filters(observed, corrected, learned):
    assert Vocabulary().learn(observed, corrected) == learned


def test_learn_counts_sightings():
    v = Vocabulary()
    v.learn("listna", "Lyssna")
    v.learn("Listna.", "Lyssna")
    (entry,) = v.entries()
    assert entry.term == "Lyssna" and entry.heard_as == {"listna": 2} and entry.source == "learned"


def test_deleted_term_is_not_learned_again():
    v = Vocabulary()
    v.learn("listna", "Lyssna")
    assert v.remove("Lyssna")
    assert v.learn("listna", "Lyssna") is None
    assert v.terms() == []
    assert v.correct("I use listna") == "I use listna"
    assert v.add("Lyssna") == "Lyssna"  # only the user brings it back
    assert v.terms() == ["Lyssna"]


# -- storage, hints, use counts ---------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="POSIX file permissions")
def test_file_is_owner_only_and_round_trips(vocab):
    path = V.default_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    data = json.loads(path.read_text())
    assert data["version"] == 1 and {e["term"] for e in data["entries"]} >= {"Lyssna", "Claude Code"}
    assert set(Vocabulary().terms()) == set(vocab.terms())


def test_a_change_made_by_another_process_is_seen(vocab):
    other = Vocabulary()
    other.add("Superwhisper")
    assert "Superwhisper" in vocab.terms()


def test_broken_file_is_kept_aside(tmp_path):
    path = tmp_path / "v.json"
    path.write_text("{ not json")
    v = Vocabulary(path)
    assert v.terms() == []
    assert (tmp_path / "v.json.broken").exists()


def test_hints_are_most_used_first(vocab):
    vocab.used("Lyssna and Lyssna's settings, with Kokoro")
    assert vocab.hints(2) == ["Lyssna", "Kokoro"]
    assert dict((e.term, e.uses) for e in vocab.entries())["Lyssna"] == 2


def test_vocabulary_stores_terms_not_text(vocab):
    vocab.used("This private sentence mentions Lyssna once.")
    assert "private" not in V.default_path().read_text()
