"""Grab the text the user has selected in whatever app is in front, on Windows.

The real version will ask UI Automation (through comtypes) for the focused element's text
selection first, then fall back to Ctrl+C sent with SendInput, with the clipboard saved and
put back afterwards, and never send Ctrl+C to a console window (it would stop a running
command). Same public functions as lyssna/capture.py. Not built yet: every call raises."""

from __future__ import annotations

_NOT_BUILT = "Windows capture is not built yet"


def selection_or_clipboard() -> tuple[str, str]:
    raise NotImplementedError(_NOT_BUILT)


def selected_text(timeout: float = 0.8, restore_clipboard: bool = True) -> str | None:
    raise NotImplementedError(_NOT_BUILT)


def ax_selected_text() -> str | None:
    raise NotImplementedError(_NOT_BUILT)


def ax_selection():
    raise NotImplementedError(_NOT_BUILT)


def clipboard_text() -> str:
    raise NotImplementedError(_NOT_BUILT)


def clipboard_change_count() -> int | None:
    raise NotImplementedError(_NOT_BUILT)


def copied_since(count: int | None) -> str:
    raise NotImplementedError(_NOT_BUILT)


def note_own_changes(before: int, after: int) -> None:
    raise NotImplementedError(_NOT_BUILT)


def own_change(count: int) -> bool:
    raise NotImplementedError(_NOT_BUILT)


def front_app_pid() -> int | None:
    raise NotImplementedError(_NOT_BUILT)


def wake_accessibility(pid: int | None) -> None:
    raise NotImplementedError(_NOT_BUILT)


def focused_element():
    raise NotImplementedError(_NOT_BUILT)


def send_key(keycode: int, flags: int = 0) -> None:
    raise NotImplementedError(_NOT_BUILT)


def _wait_for_modifiers_release(timeout: float = 1.5) -> None:
    raise NotImplementedError(_NOT_BUILT)


def snapshot_pasteboard(pb) -> list[dict[str, bytes]]:
    raise NotImplementedError(_NOT_BUILT)


def restore_pasteboard(pb, items: list[dict[str, bytes]], new_item=None) -> None:
    raise NotImplementedError(_NOT_BUILT)


def ax_attribute(element, name: str):
    raise NotImplementedError(_NOT_BUILT)


def ax_settable(element, name: str) -> bool:
    raise NotImplementedError(_NOT_BUILT)


def ax_role(element) -> str | None:
    raise NotImplementedError(_NOT_BUILT)


def ax_text_value(element) -> str | None:
    raise NotImplementedError(_NOT_BUILT)


def ax_pid(element) -> int | None:
    raise NotImplementedError(_NOT_BUILT)


def ax_set_timeout(element, seconds: float) -> None:
    raise NotImplementedError(_NOT_BUILT)
