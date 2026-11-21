"""The History window's wording and layout without any drawing (from ui/history_window.py):
its measurements, the empty-state and badge strings, the tabs, and the small helpers that turn
numbers and dates into the words it shows. Shared so the Mac and Windows windows read and line
up the same."""

from __future__ import annotations

from datetime import date, timedelta

# -- measurements (8 pt grid) -------------------------------------------------------------------

# Window
W, H = 560.0, 680.0           # default content size
MIN_W, MIN_H = 480.0, 520.0
MARGIN = 32.0                 # left and right of all content
TOP = 28.0                    # above the headline
WIDE = 720.0                  # past this window width the content column stops growing...
COLUMN_MAX = 656.0            # ...at this width, centred

# Header
MARK_H = 28.0
TITLE_SIZE = 22.0
MARK_GAP = 10.0               # mark to headline
HEAD_TO_STATS = 24.0
STAT_SIZE = 28.0
STAT_LABEL_SIZE = 12.0
STATS_TO_SUPPORT = 12.0       # the totals to the support line under them
SUPPORT_SIZE = 12.0
SUPPORT_GAP = 2.0             # the sentence to its action
SYMBOL_SMALLER = 2.0          # a symbol in front of a text button is this much smaller than the text
STATS_TO_TABS = 28.0
LABEL_INSET = 2.0             # an NSTextField label draws its text this far in from its frame

# Segmented control
SEG_H = 32.0
SEG_PAD = 18.0                # label to segment edge
SEG_INSET = 3.0               # selection capsule inside the track
SEG_SIZE = 13.0
SEG_SLIDE = 0.18              # seconds for the capsule to slide
TABS_TO_FIELD = 16.0

# Fields
FIELD_H = 34.0
FIELD_RADIUS = 10.0
FIELD_PAD = 12.0              # text and icon in from the field's edge
FIELD_SIZE = 13.0
SEARCH_ICON = 13.0
SEARCH_ICON_GAP = 8.0         # magnifier to text
FOCUS_ALPHA = 0.35            # the focus hairline, in CONTENT at this alpha
BUTTON_GAP = 8.0              # field to Add, Save to Cancel
FIELD_TO_LIST = 16.0
NOTE_GAP = 8.0                # field to the note under it

# List
ROW_PAD_X = 16.0              # inside the hover background, left and right
ROW_PAD_Y = 12.0              # top and bottom
ROW_RADIUS = 10.0
ROW_GAP = 4.0                 # between rows
TEXT_SIZE = 14.0
TEXT_LINE_H = 19.0            # 14 pt at about 1.35
TEXT_LINES = 4                # a dictation shows this many lines until clicked
TIME_SIZE = 11.0
TIME_W = 56.0                 # the time column
ICON_BTN = 28.0               # the row icon buttons' circular hit area
ICON_SIZE = 14.0
ICON_SIZE_SMALL = 12.0        # the dictionary's delete
ICON_GAP = 4.0                # between the two icons
ICONS_TO_TEXT = 16.0          # text stops this far from the icons
NOTE_SIZE = 12.0              # the original transcript and "Learned:"
NOTE_TOP = 4.0
HEADER_SIZE = 12.0
HEADER_ABOVE = 24.0
HEADER_ABOVE_FIRST = 16.0
HEADER_BELOW = 8.0
LIST_TOP = 8.0                # above the first dictionary row
LIST_BOTTOM = 16.0
TERM_SIZE = 14.0
HEARD_SIZE = 12.0
HEARD_GAP = 2.0
SOURCE_SIZE = 11.0
SOURCE_TO_ICON = 4.0
PILL_ABOVE = 24.0             # Show Older
EDIT_TO_BUTTONS = 8.0
SMALL_PILL_H = 28.0           # Save, Cancel
EMPTY_SIZE = 14.0
EMPTY_W = 320.0
EMPTY_MARK_H = 28.0
EMPTY_MARK_ALPHA = 0.25
EMPTY_MARK_GAP = 12.0
EMPTY_MAX_LINES = 6
EMPTY_STEP = 4.0              # balanced lines: widen by this until the text fits its line count

# Footer
FOOTER_H = 44.0
FOOTER_SIZE = 12.0
DISABLED_ALPHA = 0.30         # a text button with nothing to do

# Behaviour
ROW_CAP = 300                 # rows built at once; Show Older adds this many more
POLL = 0.5                    # seconds between checks for changes while the window is open
COPIED_FOR = 1.2              # seconds the copy icon shows a check
FRAME = 1 / 60                # animation tick

EMPTY_DICTATIONS = "Your dictations will appear here. Hold fn and speak."
EMPTY_SEARCH = "No dictations match."
EMPTY_DICTIONARY = "Words Lyssna should know: names, products, jargon. It also learns when you correct a dictation."
SOURCE_BADGE = {"learned": "Learned", "manual": "Added"}  # any other source reads "Added"
TABS = ("dictations", "dictionary")


# -- small pure helpers (tested on their own) ----------------------------------------------------


def figure(n: int) -> str:
    """A whole number with thousands separators: 18930 -> "18,930"."""
    return f"{int(n):,}"


def retention_text(days: int) -> str:
    if days == 0:
        return "History is off"
    if days < 0:
        return "Kept on this Mac until you clear it"
    return f"Kept for {days} day{'s' if days != 1 else ''} on this Mac"


def day_label(day: date, today: date) -> str:
    """"Today", "Yesterday", "Mon 16 Nov", or "Mon 16 Nov 2025" for an earlier year."""
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    label = f"{day:%a} {day.day} {day:%b}"
    return label if day.year == today.year else f"{label} {day.year}"


def learned_text(terms: list[str]) -> str:
    return f"Learned: {', '.join(terms)}" if terms else ""


def column(width: float) -> tuple[float, float]:
    """The content column for a window this wide: (left edge, width). Margins on both sides, and
    past WIDE the column keeps COLUMN_MAX and centres."""
    if width > WIDE:
        w = COLUMN_MAX
        return round((width - w) / 2), w
    return MARGIN, width - 2 * MARGIN


def ease_out(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3
