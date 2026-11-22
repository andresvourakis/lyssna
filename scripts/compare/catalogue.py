"""The matched pairs: one entry per screen state. Each names the screen family, the builder both
sides use (`kind`, with the same `params` on both), and the moment any animation is frozen at.
legacy_side.py and qt_side.py each turn (kind, params) into a view of their toolkit; neither
decides the state on its own, so a legacy screen and its Qt twin always get the same data.

Pure Python: nothing here imports AppKit or Qt."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from . import fixtures as fx

SCHEMES = ("light", "dark")


@dataclass(frozen=True)
class Pair:
    name: str
    family: str
    kind: str
    params: dict = field(default_factory=dict)
    moment: str = "still: nothing animates in this state"


# The moment every frozen animation is shown at, on both sides.
DRIFT_T = 3.4    # seconds into the sliver's drifts (the Qt preview_moment default)
BLOOM_T = 0.3    # seconds into a sentence bloom
WAVE_TICKS = 12  # the dictation wave, 12 ticks of 1/30 s from a calm line (0.4 s), mic level 0.5
SLIVER_MOMENT = (f"sliver drifts at t={DRIFT_T} s (hue, split, breathe), bloom {BLOOM_T} s old; "
                 "no sparks on either side (the legacy emitter cannot be drawn offscreen)")
PAUSED_MOMENT = f"sliver drifts frozen at t={DRIFT_T} s; paused, so no bloom and no sparks"
WAVE_MOMENT = f"wave {WAVE_TICKS} ticks of 1/30 s in at level 0.5 (amplitude and phase stepped identically)"
WORK_MOMENT = WAVE_MOMENT + "; working: the breathing opacity 0.4 s into its loop"


def _player(form: str, moment: str = "still: nothing animates in this state", **params) -> tuple[str, dict, str]:
    return "player", {"form": form, **params}, moment


PLAYER = {
    "player.card": _player("card", status={}),
    "player.card-paused": _player("card", status={"state": "paused"}),
    "player.card-loading": _player("card", status={"state": "loading", "index": 0, "total": 0, "position": 0.0,
                                                   "duration": 0.0, "speed": 1.0}),
    "player.card-long": _player("card", status={"index": 128, "total": 240, "position": 1834.0, "duration": 3460.0,
                                                "speed": 1.75}),
    "player.card-scrubbing": _player("card", "scrub bar hovered and held at 62 percent (knob shown, bar thickened)",
                                     status={}, scrub=0.62),
    "player.sliver": _player("sliver", SLIVER_MOMENT, dock="right", status={"position": 0.4 * 334.0}, bloom=BLOOM_T),
    "player.sliver-paused": _player("sliver", PAUSED_MOMENT, dock="right", status={"state": "paused", "position": 0.4 * 334.0}),
    "player.sliver-bottom": _player("sliver", SLIVER_MOMENT, dock="bottom", status={"position": 0.65 * 334.0}, bloom=BLOOM_T),
    "player.sliver-left": _player("sliver", SLIVER_MOMENT, dock="left", status={"position": 0.4 * 334.0}, bloom=BLOOM_T),
    "player.pill": _player("pill", "hover growth landed at scale 1", dock="right", status={}),
    "player.pill-flat": _player("pill", "hover growth landed at scale 1", dock="bottom", status={}),
    "player.idle": _player("rest", "resting squiggle, no ripple, slid fully in", edge="bottom"),
    "player.idle-side": _player("rest", "resting squiggle, no ripple, slid fully in", edge="right"),
    "player.idle-menu": _player("menu", "menu growth landed at scale 1", edge="bottom"),
    "player.idle-menu-side": _player("menu", "menu growth landed at scale 1", edge="right"),
    "player.drop-zone": _player("zone", flat=False),
    "player.drop-zone-flat": _player("zone", flat=True),
    "player.dictate": _player("dictate", WAVE_MOMENT, edge="right", state="recording"),
    "player.dictate-flat": _player("dictate", WAVE_MOMENT, edge="bottom", state="recording"),
    "player.dictate-hands-free": _player("dictate", WAVE_MOMENT + "; growth landed", edge="right", state="locked"),
    "player.dictate-hands-free-flat": _player("dictate", WAVE_MOMENT + "; growth landed", edge="bottom", state="locked"),
    "player.dictate-working": _player("dictate", WORK_MOMENT, edge="right", state="working"),
    "player.dictate-working-flat": _player("dictate", WORK_MOMENT, edge="bottom", state="working"),
}

S = fx.setup_state
SETUP = {
    # Listen: the permission step
    "setup.listen.not-allowed": S(trusted=False),
    "setup.listen.relaunch": S({"ready": True}, taps_ok=False),
    "setup.listen.ready": S(fx.DONE),
    # Listen: the free voice
    "setup.voice.not-downloaded": S(),
    "setup.voice.downloading": S({"progress": 0.42}),
    "setup.voice.ready": S({"ready": True}),
    "setup.voice.error": S(error="The download stopped. Check your connection."),
    # Listen: the online card
    "setup.online.empty": S({"ready": True}, engine="openai"),
    "setup.online.checking": S({"ready": True}, engine="openai", saving=True, key="sk-preview"),
    "setup.online.saved": S({"ready": True, "provider": "openai", "keys": ("openai",)}, engine="openai",
                            message=("Saved. Lyssna now reads with OpenAI TTS.", "good")),
    "setup.local-only": S({"ready": True, "local_only": True}),
    # Dictate: the permission step and the Globe key
    "setup.dictate.mic-undetermined": S({"ready": True, "mic": "undetermined"}, tab="dictate"),
    "setup.dictate.mic-denied": S({"ready": True, "mic": "denied"}, tab="dictate"),
    "setup.dictate.mic-granted": S({"ready": True, "mic": "granted"}, tab="dictate"),
    "setup.dictate.globe": S({"ready": True}, tab="dictate", globe="emoji"),
    # Dictate: the models
    "setup.dictate.models-download": S({"ready": True}, tab="dictate"),
    "setup.dictate.models-downloading": S({"ready": True, "dict_progress": 0.42}, tab="dictate"),
    "setup.dictate.ready": S(fx.DONE, tab="dictate"),
    # Dictate: the online keys
    "setup.dictate.online-checking": S({"ready": True, "models": True, "online": True, "dict_keys": ("openai",)},
                                       dictation="haiku", dict_saving=1),
    "setup.dictate.online-saved": S({"ready": True, "models": True, "online": True, "dict_keys": ("openai", "haiku")},
                                    dictation="openai",
                                    dict_message=(0, "Saved. Dictation now uses OpenAI for transcription.", "good")),
    "setup.dictate.local-only": S({"ready": True, "local_only": True}, tab="dictate"),
}

HISTORY = {
    "history.dictations": {"variant": "plain", "supporter": True},
    "history.expanded": {"variant": "expanded"},
    "history.learned": {"variant": "learned"},
    "history.hover": {"variant": "hover"},
    "history.fix": {"variant": "fix"},
    "history.search": {"variant": "search", "query": "mattias"},
    "history.search-none": {"variant": "search", "query": "nothing like it"},
    "history.empty": {"variant": "plain", "entries": False},
    "history.dictionary": {"variant": "dictionary"},
    "history.dictionary-empty": {"variant": "dictionary", "terms": False},
    "history.dictionary-too-long": {"variant": "dictionary", "note": "That is too long for the dictionary."},
    "history.wide": {"variant": "plain", "supporter": True, "size": fx.HISTORY_WIDE},
}


def _build() -> list[Pair]:
    pairs: list[Pair] = []
    for name, (kind, params, moment) in PLAYER.items():
        pairs.append(Pair(name, "player", kind, params, moment))
    for name, (text, action, max_w) in fx.TOASTS.items():
        pairs.append(Pair(name, "toast", "toast", {"text": text, "action": action, "max_w": max_w}, "faded in, alpha 1"))
    for name, (edge, text, key) in fx.TIPS.items():
        pairs.append(Pair(name, "tip", "tip", {"edge": edge, "text": text, "key": key}))
    pairs.append(Pair("selection-button", "selection", "selection", {"hover": False}))
    pairs.append(Pair("selection-button.hover", "selection", "selection", {"hover": True},
                      "hover glow landed (legacy: the button has no hover state)"))
    for h in (28.0, 30.0):
        pairs.append(Pair(f"mark.{int(h)}", "mark", "mark", {"height": h}))
    for mode in ("recording", "working"):
        for flat in (False, True):
            name = f"wave.{mode}" + ("-flat" if flat else "")
            pairs.append(Pair(name, "wave", "wave", {"mode": mode, "horizontal": flat, "level": 0.8},
                              (WORK_MOMENT if mode == "working" else WAVE_MOMENT).replace("level 0.5", "level 0.8")))
    for name, state in SETUP.items():
        pairs.append(Pair(name, "setup", "setup", state, "spinner, if any, at its first frame"))
    for name, params in HISTORY.items():
        pairs.append(Pair(name, "history", "history", params))
    pairs.append(Pair("whats-new", "whats-new", "whats_new", {"support": False}))
    pairs.append(Pair("whats-new.support", "whats-new", "whats_new", {"support": True}))
    return pairs


PAIRS: list[Pair] = _build()

# The menus, compared as structure (titles, order, enabled, checks, shortcuts, headers).
MENUS = ("menu.idle", "menu.reading", "menu.speed", "menu.voice", "menu.provider", "menu.transcription", "menu.cleanup",
         "menu.settings", "menu.keys", "popup.speed", "popup.voice", "popup.history-more")

TOOLTIPS = {  # what the app wiring sets on the player's buttons (the config's default hotkeys)
    "toggle": "Pause / resume  ⌃⌥Space", "stop": "Stop  ⌃⌥X", "back": "Previous sentence  ⌃⌥←",
    "forward": "Next sentence  ⌃⌥→", "speed": "Speed  ⌃⌥- / ⌃⌥=",
}


def select(only: str | None) -> list[Pair]:
    """The pairs whose name matches `only` (a glob, or a plain substring), or all of them."""
    if not only:
        return list(PAIRS)
    pattern = only if any(c in only for c in "*?[") else f"*{only}*"
    return [p for p in PAIRS if fnmatch.fnmatch(p.name, pattern)]


def select_names(names, only: str | None) -> list[str]:
    if not only:
        return list(names)
    pattern = only if any(c in only for c in "*?[") else f"*{only}*"
    return [n for n in names if fnmatch.fnmatch(n, pattern)]
