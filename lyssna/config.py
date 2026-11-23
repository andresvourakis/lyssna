"""Config lives at ~/.config/lyssna/config.toml. Missing keys fall back to DEFAULTS."""

from __future__ import annotations

import copy
import logging
import os
import tomllib
from pathlib import Path

log = logging.getLogger("lyssna.config")

CONFIG_DIR = Path.home() / ".config" / "lyssna"
CONFIG_PATH = CONFIG_DIR / "config.toml"
STATE_DIR = Path.home() / "Library" / "Application Support" / "Lyssna"
CACHE_DIR = Path.home() / "Library" / "Caches" / "Lyssna"
LOG_DIR = Path.home() / "Library" / "Logs" / "Lyssna"
SOCKET_PATH = STATE_DIR / "lyssna.sock"

DEFAULTS: dict = {
    "provider": "kokoro",
    "speed": 1.2,
    "cache": True,
    "cache_max_mb": 500,
    "local_only": False,
    "hotkeys": {
        "speak": "ctrl+alt+s",
        "pause": "ctrl+alt+space",
        "stop": "ctrl+alt+x",
        "next": "ctrl+alt+right",
        "prev": "ctrl+alt+left",
        "faster": "ctrl+alt+=",
        "slower": "ctrl+alt+-",
        "button": "ctrl+alt+b",
        "paste_last": "ctrl+alt+v",
    },
    "ui": {"selection_button": True, "mini_player": True, "button_timeout": 4.0,
           "player_dock": "", "player_x": -1.0, "player_y": -1.0,
           "idle_pill": True, "auto_hide": True, "wave": True, "dictation_dock": "", "dictation_y": -1.0},
    "inworld": {"api_key": "", "voice": "Sarah", "model": "inworld-tts-2"},
    "cartesia": {"api_key": "", "voice": "47c38ca4-5f35-497b-b1a3-415245fb35e1", "model": "sonic-3.6"},
    "elevenlabs": {"api_key": "", "voice": "JBFqnCBsd6RMkjVDRZzb", "model": "eleven_v3"},
    "openai": {"api_key": "", "voice": "ash", "model": "gpt-4o-mini-tts", "base_url": "https://api.openai.com/v1",
               "transcribe_model": "gpt-transcribe"},
    "anthropic": {"api_key": "", "model": "claude-haiku-5-5"},
    "kokoro": {"voice": "af_heart"},
    "say": {"voice": ""},
    "dictation": {"transcriber": "parakeet", "cleaner": "s1-mini", "cleanup": True, "language": "en",
                  "hotkey": "fn", "hands_free": "space", "history_days": 30, "auto_learn": True},
    "parakeet": {"model": "v2"},
}

TEMPLATE = """# Lyssna configuration. Restart the app after editing hotkeys; everything else is read on the fly.

# Which voice provider to use: kokoro | inworld | inworld-flash | cartesia | elevenlabs | openai | say
# kokoro is free and runs on this Mac: your text never leaves it. It downloads its voice once
# (about 140 MB) the first time it reads. say is the macOS system voice, also on this Mac. The
# others are online services that need an API key; add one from the menu bar (Setup… or
# Settings > API Keys). If the chosen provider has no key, Lyssna switches to kokoro automatically.
provider = "kokoro"

# Playback speed multiplier (0.5 - 2.0). Change it live from the menu bar or with ctrl+alt+= / ctrl+alt+-.
speed = 1.2

# false = never save the audio of what you read to disk (reading it again is synthesized again).
cache = true

# Size limit for the audio cache in ~/Library/Caches/Lyssna, in MB. Past it, the audio you
# played longest ago is deleted first. 0 = no limit.
cache_max_mb = 500

# true = never use an online service: only Kokoro and the macOS voice for reading, and only the
# engines on this Mac for dictation, so your text and voice stay on this Mac.
# The same as Settings > Keep Everything on This Mac in the menu.
local_only = false

[hotkeys]
speak  = "ctrl+alt+s"      # read the selected text (falls back to the clipboard)
pause  = "ctrl+alt+space"  # pause / resume
stop   = "ctrl+alt+x"
next   = "ctrl+alt+right"  # skip forward one chunk
prev   = "ctrl+alt+left"   # skip back one chunk
faster = "ctrl+alt+="
slower = "ctrl+alt+-"
button = "ctrl+alt+b"      # toggle the play button that appears after selecting text
paste_last = "ctrl+alt+v"  # paste your last dictation again (if it failed, retry it and paste)

[ui]
selection_button = true    # show a play button next to the cursor after you select text
mini_player = true         # floating transport bar while reading
button_timeout = 4.0       # seconds before the play button fades away
# Where you last left the mini player (drag it; drop it on the left, right or bottom screen
# edge to collapse it into a pill: player_dock = "left" | "right" | "bottom", and then
# player_y is the position along that edge, 0..1). Managed by the app.
player_dock = ""
player_x = -1.0
player_y = -1.0
idle_pill = true           # a slim pill on the screen edge when nothing plays; hover it to dictate,
                           # read the selection or open the history. false = nothing at rest
auto_hide = true           # the idle pill stays out of sight until the cursor comes near its spot
wave = true                # the resting wave ripples now and then; false = it stays still
# Where you last left the idle pill while the player is not docked: dictation_dock = "left" |
# "right" | "bottom" and dictation_y, the position along that edge (0..1). Empty = the bottom
# centre of the screen. Managed by the app.
dictation_dock = ""
dictation_y = -1.0

[dictation]
# Speech to text: parakeet runs on this Mac (your voice never leaves it; about 630 MB, downloaded
# once). openai (gpt-transcribe) and inworld are online services that use the API keys below.
transcriber = "parakeet"
# Cleanup of the transcript (fillers, false starts, punctuation, lists): s1-mini runs on this Mac
# (S1-mini by Superwhisper, about 460 MB, Apple silicon only). haiku (Claude Haiku 5.5) is online
# and uses the [anthropic] key. Whatever cleans it, the result is checked, and the plain
# transcript is used if the cleanup changed a number, a "not" or the meaning.
cleaner = "s1-mini"
cleanup = true             # false = only remove fillers and fix capitals, no cleanup model
language = "en"            # en, or es, sv and other European languages (parakeet then uses model v3)
hotkey = "fn"              # hold to dictate: fn | right_option | right_command | right_control
hands_free = "space"       # press while holding the hotkey to keep recording without holding it
history_days = 30          # days dictated text is kept in the history. 0 = keep none, -1 = keep all
auto_learn = true          # learn words you correct right after a dictation (names, jargon)

# API keys: add them from the menu bar (Setup… or Settings > API Keys). They are stored in your macOS
# Keychain, not in this file. You can also set them in the environment (INWORLD_API_KEY,
# CARTESIA_API_KEY, ...) or write one into api_key below. A key in the Keychain wins over this
# file, and this file wins over the environment. Keys that were already here when Lyssna first
# started using the Keychain were moved there once.

[inworld]
api_key = ""
voice = "Sarah"            # try: Ashley, Olivia, Julia, Wendy, Dennis, Mark, Theodore, Timothy, Alex, Craig
model = "inworld-tts-2"    # the inworld-flash provider shares this key and voice at $15/1M instead of $25

[cartesia]
api_key = ""
voice = "47c38ca4-5f35-497b-b1a3-415245fb35e1"   # Daniel. Browse more at play.cartesia.ai
model = "sonic-3.6"

[elevenlabs]
api_key = ""
voice = "JBFqnCBsd6RMkjVDRZzb"   # George. Any voice ID from your ElevenLabs library works.
model = "eleven_v3"              # or eleven_flash_v2_5 (half the price)

[openai]
api_key = ""
voice = "ash"                    # alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer
model = "gpt-4o-mini-tts"
# Point this at your company's OpenAI-compatible gateway if it has one (https only).
base_url = "https://api.openai.com/v1"
transcribe_model = "gpt-transcribe"   # the speech-to-text model when dictation uses openai

[anthropic]
api_key = ""
model = "claude-haiku-5-5"       # the cleanup model when dictation cleanup uses haiku

[kokoro]
voice = "af_heart"               # af_bella, af_nicole, am_michael, am_fenrir, bf_emma, bf_isabella, bm_george ...

[say]
voice = ""                       # empty = best installed system voice (Premium voices win)

[parakeet]
model = "v2"                     # v2 (English) or v3 (also Spanish, Swedish and other European languages)
"""


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs() -> None:
    """Create our dirs; the config and state dirs (API keys, control socket) are owner-only.
    Carries over the old Speechy files first, while the new dirs may still be missing."""
    from . import migrate

    migrate.run()
    for d in (CONFIG_DIR, STATE_DIR):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.chmod(0o700)  # mkdir's mode does not fix a dir that already exists
    for d in (CACHE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read (it may hold API keys)."""
    path.write_text(text)
    os.chmod(path, 0o600)


def load() -> dict:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        _write_private(CONFIG_PATH, TEMPLATE)
    try:
        user = tomllib.loads(CONFIG_PATH.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        user = {}
    cfg = _merge(DEFAULTS, user)
    if os.environ.get("LYSSNA_PROVIDER"):
        cfg["provider"] = os.environ["LYSSNA_PROVIDER"]
    return cfg


_TOML_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    out = []
    for ch in str(v):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _set_line(lines: list[str], section: str | None, key: str, value) -> None:
    """Edit `key = value` in place inside `section` (None = top level), keeping comments.
    Adds the line (and the section) if missing."""
    import re

    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)([^#\n]*?)(\s*(#.*)?)$")
    current = None
    section_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current == section and section_start is not None:
                break  # left our section without finding the key
            current = stripped[1:-1].strip()
            if current == section:
                section_start = i
            continue
        if current == section:
            m = key_re.match(line)
            if m:
                lines[i] = f"{m.group(1)}{_toml_value(value)}{m.group(3)}"
                return
    new_line = f"{key} = {_toml_value(value)}"
    if section is None:
        lines.insert(0, new_line)
    elif section_start is not None:
        lines.insert(section_start + 1, new_line)
    else:
        lines.extend(["", f"[{section}]", new_line])


def file_api_key(section: str) -> str:
    """The api_key written in the config file itself for a section, ignoring the Keychain."""
    try:
        user = tomllib.loads(CONFIG_PATH.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return ""
    value = user.get(section, {}).get("api_key", "") if isinstance(user.get(section), dict) else ""
    return value if isinstance(value, str) else ""


def save_api_key(section: str, key: str) -> bool:
    """Store a key in the Keychain and blank it in the config file. If the Keychain will not take
    it, write it to the config file instead (owner-only, as before). True = it is in the Keychain."""
    from . import keychain

    if keychain.set(section, key) and keychain.get(section) == key:
        if file_api_key(section):
            update(**{f"{section}.api_key": ""})
        return True
    log.warning("could not store the %s API key in the Keychain; saving it in %s instead", section, CONFIG_PATH)
    update(**{f"{section}.api_key": key})
    return False


def update(**changes) -> dict:
    """Persist settings without clobbering the user's comments. Keys may be dotted for
    sections: update(speed=1.2, **{"ui.selection_button": False})."""
    text = CONFIG_PATH.read_text() if CONFIG_PATH.exists() else TEMPLATE
    lines = text.split("\n")
    for dotted, value in changes.items():
        section, _, key = dotted.rpartition(".")
        _set_line(lines, section or None, key, value)
    _write_private(CONFIG_PATH, "\n".join(lines))
    return load()
