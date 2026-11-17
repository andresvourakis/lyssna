"""Finding the user's corrections in pasted text, and the watcher that looks for them, with a
fake Accessibility probe and a fake clock."""

import pytest

from lyssna.dictation.learn import EditWatcher, find_corrections


@pytest.mark.parametrize(
    ("inserted", "edited", "expected"),
    [
        # a name fixed inside a longer field, with text before and after
        ("I met matias at the cafe", "Yesterday: I met Mattias at the cafe. Then home.", [("matias", "Mattias")]),
        # the first and the last pasted words
        ("listna reads text aloud", "Notes - Lyssna reads text aloud", [("listna", "Lyssna")]),
        ("the model is parakeat", "the model is Parakeet and more", [("parakeat", "Parakeet")]),
        # two words for one, one for two
        ("send it to john smith today", "send it to Jon Smyth today", [("john smith", "Jon Smyth")]),
        ("we use sales force for this", "we use Salesforce for this", [("sales force", "Salesforce")]),
        # casing only is still reported (the vocabulary decides whether it matters)
        ("post it in slack please", "post it in Slack please", [("slack", "Slack")]),
        # two separate fixes in a long enough text
        ("please ask matias and pieter about the rise launch next week",
         "please ask Mattias and Pieter about the Ryse launch next week",
         [("matias", "Mattias"), ("pieter", "Pieter"), ("rise", "Ryse")]),
    ],
)
def test_corrections_are_found(inserted, edited, expected):
    assert find_corrections(inserted, edited) == expected


@pytest.mark.parametrize(
    ("inserted", "edited"),
    [
        ("Hello there, how are you?", "Hello there, how are you?"),                 # untouched
        ("Hello there, how are you?", "Hello there how are you!"),                  # punctuation only
        ("Hello there how are you", "Hello   there\nhow are you"),                  # whitespace only
        ("Hello there how are you", "Hello there my friend how are you"),           # an insertion
        ("Hello there how are you", "Hello how are you"),                           # a deletion
        ("one two three four five six", "one alpha beta gamma delta six"),           # a rewrite
        ("we should meet on friday at noon", "we should meet sometime next month maybe"),  # a rewrite
        ("send the report to the team", "Something else entirely."),                 # the paste is gone
        ("ship it", ""),
        ("", "anything"),
    ],
)
def test_no_corrections(inserted, edited):
    assert find_corrections(inserted, edited) == []


def test_replacements_longer_than_three_words_are_not_corrections():
    inserted = "the quick brown fox jumps over the lazy dog near the river bank today and tomorrow"
    edited = "the slow red old cat jumps over the lazy dog near the river bank today and tomorrow"
    assert find_corrections(inserted, edited) == []


def test_the_same_words_elsewhere_in_the_field_do_not_confuse_it():
    inserted = "call matias about the invoice"
    edited = "call matias about the invoice. Later: call Mattias about the invoice"
    # The original span is still there untouched, so there is nothing to learn.
    assert find_corrections(inserted, edited) == []


# -- EditWatcher ------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class Probe:
    """A scripted Accessibility: `values` maps a time to the field's content from then on."""

    def __init__(self, clock, values, element="field", pid=42):
        self.clock = clock
        self.values = sorted(values.items())
        self.element = element
        self.pid = pid
        self.focus_changes: dict[float, object] = {}
        self.app_changes: dict[float, int] = {}
        self.reads = 0

    def _at(self, changes, default):
        current = default
        for t, v in sorted(changes.items()):
            if self.clock.t >= t:
                current = v
        return current

    def focused(self):
        return self._at(self.focus_changes, self.element)

    def value(self, element):
        self.reads += 1
        current = None
        for t, v in self.values:
            if self.clock.t >= t:
                current = v
        return current

    def same(self, a, b):
        return a == b

    def front_pid(self):
        return self._at(self.app_changes, self.pid)


def watch(values, text="I met matias at the cafe", **probe_changes):
    clock = Clock()
    probe = Probe(clock, values)
    for name, changes in probe_changes.items():
        setattr(probe, name, changes)
    learned = []
    watcher = EditWatcher(lambda o, c: learned.append((o, c)), probe=probe, clock=clock, sleep=clock.sleep)
    watcher.watch(text, background=False)
    return learned, clock, probe


def test_a_correction_is_learned_once_after_the_field_settles():
    learned, clock, _ = watch({0: "I met matias at the cafe", 3: "I met Mattias at the cafe"})
    assert learned == [("matias", "Mattias")]  # once, though the watch goes on to the limit
    assert clock.t >= 60


def test_nothing_is_learned_without_an_edit():
    learned, clock, probe = watch({0: "I met matias at the cafe"})
    assert learned == []
    assert clock.t >= 60  # watched up to the limit
    assert probe.reads < 60 / EditWatcher.POLL + 5


def test_an_edit_still_in_progress_is_not_judged_early():
    learned, _, _ = watch({0: "I met matias at the cafe", 3: "I met M at the cafe", 3.6: "I met Mat at the cafe",
                           4.2: "I met Mattias at the cafe"})
    assert learned == [("matias", "Mattias")]  # never ("matias", "M") or ("matias", "Mat")


def test_watching_stops_when_focus_moves_and_judges_what_it_saw():
    learned, clock, _ = watch({0: "I met matias at the cafe", 3: "I met Mattias at the cafe"},
                              focus_changes={3.5: "another field"})
    assert learned == [("matias", "Mattias")]
    assert clock.t < 5


def test_watching_stops_when_the_app_changes():
    learned, clock, _ = watch({0: "I met matias at the cafe", 10: "I met Mattias at the cafe"}, app_changes={2: 99})
    assert learned == [] and clock.t < 3


def test_an_element_without_a_value_is_not_watched():
    learned, clock, probe = watch({})
    assert learned == [] and clock.t == 0 and probe.reads == 1


def test_stop_ends_the_watch(monkeypatch):
    clock = Clock()
    probe = Probe(clock, {0: "I met matias at the cafe", 5: "I met Mattias at the cafe"})
    learned = []
    watcher = EditWatcher(lambda o, c: learned.append((o, c)), probe=probe, clock=clock)

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t > 1:
            watcher.stop()  # another dictation started

    watcher.sleep = sleep
    watcher.watch("I met matias at the cafe", background=False)
    assert learned == [] and clock.t < 2


def test_a_slow_app_is_left_alone():
    clock = Clock()

    class SlowProbe(Probe):
        def value(self, element):
            clock.t += 0.5  # each read blocks for half a second
            return super().value(element)

    probe = SlowProbe(clock, {0: "I met matias at the cafe"})
    watcher = EditWatcher(lambda o, c: None, probe=probe, clock=clock, sleep=clock.sleep)
    watcher.watch("I met matias at the cafe", background=False)
    assert probe.reads == 1
