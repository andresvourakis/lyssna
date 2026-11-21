"""The reads lyssna.dictation.learn.EditWatcher makes of the text field a dictation was pasted
into, on the Mac: through Accessibility, with a short timeout on each element so a busy app
never holds us up. lyssna.host hands this out as host.field_probe."""

from __future__ import annotations

from . import capture


class FieldProbe:
    """The Accessibility reads EditWatcher needs, in one place so tests can replace them."""

    timeout = 0.2  # seconds an AX call may block before we give up on that read

    def focused(self):
        element = capture.focused_element()
        if element is not None:
            capture.ax_set_timeout(element, self.timeout)
        return element

    def value(self, element) -> str | None:
        return capture.ax_text_value(element)

    def same(self, a, b) -> bool:
        try:
            return bool(a == b)  # CFEqual for Accessibility elements
        except Exception:
            return False

    def front_pid(self) -> int | None:
        return capture.front_app_pid()
