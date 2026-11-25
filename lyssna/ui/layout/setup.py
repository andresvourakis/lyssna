"""The setup window's wording and layout without any drawing (from legacy/ui/setup_window.py): the
sample sentence, the measurements, the two tabs and the words the switch and the footer show
for the steps left. Shared so the Mac and Windows setup windows read and line up the same."""

from __future__ import annotations

SAMPLE = "Hi, this is Lyssna. Select any text, press the shortcut, and I will read it to you."

W = 540.0          # window content width
MARGIN = 28.0
ICON = 22.0
TEXT_X = MARGIN + ICON + 12.0   # where every step's text starts
TEXT_W = W - TEXT_X - MARGIN
INDENT = 20.0      # radio content sits under the radio's title
CARD_PAD = 12.0    # an option card's border to its contents
CARD_X = TEXT_X + CARD_PAD          # an option card's radio
CARD_R = TEXT_X + TEXT_W - CARD_PAD  # an option card's right edge, inside
SUB_X = CARD_X + INDENT             # everything under a card's radio title
SUB_W = CARD_R - SUB_X
MARK_H = 30.0      # the logo mark above the headline
SEG_H = 30.0       # one segment of the Listen / Dictate switch
SEG_PAD = 3.0      # the switch's track around its segments
FOOTER_H = 58.0    # the fixed footer: status and Done
PILL_H = 26.0      # every button is a pill this tall
PILL_PAD = 14.0    # title to pill edge
RADIO = 16.0       # the drawn radio circle; its image is INDENT wide, so the title lines up with the text below
FIELD_H = 26.0     # the key field's box, level with the Save pill beside it
POLL = 0.3         # seconds between live status checks while the window is open
GLOBE_POLL = 2.0   # seconds between reads of the Globe key setting (a `defaults` call)
SCREEN_GAP = 48.0  # room kept free above and below the window on a short screen
KEYS_GAP = 10.0    # Dictate's Try it: the body sentence to the block of key rows
KEY_ROW_GAP = 8.0  # between two key rows in that block
CAPS_GAP = 6.0     # a key row's label to its caps, and its caps to the words after them

# The online dictation engines the dictation step takes keys for, with the job each does.
DICTATION_KEYS = (("openai", "transcription"), ("haiku", "cleanup"))
DICTATION_KEY_ENGINES = tuple(name for name, _ in DICTATION_KEYS)

# The two tabs, in the order the switch shows them.
LISTEN, DICTATE = "listen", "dictate"


# -- the switch's wording, kept free of AppKit so it is tested on its own -----------------


def steps_left(done) -> int:
    """How many of a tab's steps (Permission and Voice or Engine) are not done yet."""
    return sum(1 for d in done if not d)


def tab_status(left: int) -> str:
    """The few words after a tab's name in the switch."""
    if not left:
        return "ready"
    return f"{left} step left" if left == 1 else f"{left} steps left"


def footer_status(listen_left: int, dictate_left: int) -> str:
    """The footer's line, e.g. "Listen ready · Dictate: 1 step left"."""
    def part(name: str, left: int) -> str:
        return f"{name} ready" if not left else f"{name}: {tab_status(left)}"
    return " · ".join((part("Listen", listen_left), part("Dictate", dictate_left)))


def opening_tab(engine: str | None, dictation: str | None, listen_left: int, dictate_left: int) -> str:
    """The tab the window opens on: the one the menu asked for (a dictation key opens Dictate,
    a voice key Listen), else the first with steps left, else Listen."""
    if dictation is not None:
        return DICTATE
    if engine is not None:
        return LISTEN
    if not listen_left and dictate_left:
        return DICTATE
    return LISTEN


def caps_column(label_widths) -> float:
    """Where the key caps start in each of Try it's key rows, from the rows' left edge: the
    widest label plus one gap, so the caps of every row line up in one column."""
    return max(label_widths) + CAPS_GAP


def _size_text(n_bytes: int) -> str:
    """A download size the way Finder writes it: "about 1.1 GB", "about 660 MB"."""
    if n_bytes >= 1_000_000_000:
        return f"about {n_bytes / 1e9:.1f} GB"
    return f"about {max(10, round(n_bytes / 1e7) * 10)} MB"
