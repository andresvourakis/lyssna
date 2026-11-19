"""The menu bar menu's wording and visibility rules, kept free of AppKit so they can be tested.

app.MenuBar builds the actual NSMenu and asks these functions what to show."""

from __future__ import annotations

from dataclasses import dataclass

LOCAL_ONLY_TITLE = "Keep Everything on This Mac"
LOCAL_ONLY_MANAGED_TITLE = f"{LOCAL_ONLY_TITLE} (Set by Your Organization)"
KEY_MENU_LABELS = {"inworld": "Inworld"}  # providers sharing a config section get one key entry
REPO_URL = "https://github.com/andresvourakis/lyssna"
CLEANUP_OFF = "Off"
# Section headers: the menu's two features, and the two kinds of engine inside each picker.
LISTEN_HEADER = "Listen"
DICTATE_HEADER = "Dictate"
LOCAL_HEADER = "On this Mac"
ONLINE_HEADER = "Online, with your API key"
_LOCAL_PREFIX = f"{LOCAL_HEADER} ("
# How each dictation key is written in menus, tooltips and the pill.
KEY_NAMES = {"fn": "fn", "right_option": "right ⌥", "right_command": "right ⌘", "right_control": "right ⌃"}


@dataclass(frozen=True)
class MenuState:
    """What the visibility rules need to know about the app right now."""

    player: str = "idle"        # idle | loading | playing | paused
    downloading: bool = False   # the local voice is downloading
    message: str = ""           # a transient notice ("Nothing selected", an error)
    has_voices: bool = True     # the current voice provider offers a voice list
    usage_line: str = ""        # usage.summary(); "" when this month's cost is zero
    local_only: bool = False
    stored_keys: bool = False   # any API key the app could remove
    dictation: str = "idle"     # the dictation session's state
    whats_new: bool = False     # an update's note is waiting to be opened
    support: bool = False       # the website has an address to send supporters to


def visibility(s: MenuState) -> dict[str, bool]:
    """Which optional items show. Everything not listed here always shows."""
    return {
        "whats_new": s.whats_new,
        "support": s.support,
        "now_playing": s.downloading or bool(s.message) or s.player != "idle",
        "transport": s.player != "idle",  # Pause / Resume, Stop, Previous and Next Sentence
        "voice": s.has_voices,
        "usage": bool(s.usage_line),
        "api_keys": not s.local_only,
        "remove_keys": s.stored_keys,
    }


def separators_shown(rows: list[bool | None]) -> list[bool]:
    """One entry per row of a menu, top to bottom: None for a separator, else whether that item
    is visible. Returns, for each separator in order, whether it shows: only between visible
    items, never first, last or next to another shown separator."""
    shown = []
    pending = False  # a visible item since the last shown separator
    for i, row in enumerate(rows):
        if row is None:
            show = pending and any(r for r in rows[i + 1:] if r is not None)
            shown.append(show)
            if show:
                pending = False
        elif row:
            pending = True
    return shown


def menubar_text_allowed(s: MenuState) -> bool:
    """While a dictation records or works the pill is the indicator: no text in the menu bar."""
    return s.dictation not in ("recording", "locked", "working")


def key_name(hotkey: str) -> str:
    """The dictation key as people read it: "fn", "right ⌥"."""
    return KEY_NAMES.get(hotkey, hotkey)


def dictation_title(hotkey: str, recording: bool) -> str:
    """The first row of the Dictation group: start (showing the key), or finish while recording."""
    return "Finish Dictation" if recording else f"Start Dictation · hold {key_name(hotkey)}"


def dictation_privacy(speech_to: str, text_to: str) -> str:
    """Where dictation sends things. Each argument is the recipient, "" for this Mac."""
    line = f"Speech is sent to {speech_to}" if speech_to else "Speech stays on this Mac"
    return f"{line}, text is sent to {text_to}" if text_to else line


def cleanup_title(label: str) -> str:
    """The Cleanup submenu's title; label is the current cleaner's, "" when cleanup is off."""
    return f"Cleanup: {label or CLEANUP_OFF}"


def download_line(what: str, fraction: float) -> str:
    """The menu's status line while something downloads."""
    if what == "voice":
        return f"Downloading voice, {int(fraction * 100)}%"
    return f"Getting the speech model ready, {int(fraction * 100)}%"


def provider_title(label: str, ready: bool, hint: str) -> str:
    """A Voice Provider entry: its name, plus what is missing when it cannot read yet."""
    return label if ready else f"{label} · {hint}"


def engine_row_label(label: str) -> str:
    """An engine's row inside its picker, under a header that already says where it runs:
    "On this Mac (Kokoro)" becomes "Kokoro"; other labels stay as they are."""
    if label.startswith(_LOCAL_PREFIX) and label.endswith(")"):
        return label[len(_LOCAL_PREFIX):-1]
    return label


def privacy_symbol(stays_local: bool) -> str:
    """The SF Symbol next to a privacy line: a lock when everything stays on this Mac, an arrow
    out when anything is sent."""
    return "lock" if stays_local else "arrow.up.right"


def local_only_title(managed: bool) -> str:
    return LOCAL_ONLY_MANAGED_TITLE if managed else LOCAL_ONLY_TITLE


def now_playing(player: str, index: int, total: int, text: str) -> str:
    verb = {"loading": "Loading", "playing": "Reading", "paused": "Paused"}.get(player, "")
    return f"{verb} {index} of {total}: {text}" if verb else ""
