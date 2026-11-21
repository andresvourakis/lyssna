"""The probe's report: a plain dict that every step adds to, and pure functions that turn it into
the text file the owner sends back.

Why plain data: the report must survive anything (a crash in one step, the window closed half
way), so it is built up as it goes and can be written at any moment. Keeping the rendering
pure (dict in, text out) lets it be tested on a Mac. The text has one section per step with
"key: value" lines, then a JSON block holding the same data, so it is easy to read and easy to
parse.

Privacy: the only user text in the report is the first SNIPPET_CHARS characters of what the
user selected in the selection step, which the screen warns about."""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime

SNIPPET_CHARS = 60
JSON_BEGIN = "----- BEGIN JSON -----"
JSON_END = "----- END JSON -----"
REPORT_NAME = "lyssna-probe-report.txt"

STATUS_PENDING = "not reached"
STATUS_RUNNING = "stopped before the end"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"


def snippet(text: str | None, limit: int = SNIPPET_CHARS) -> str:
    """The first `limit` characters of the text, on one line."""
    one_line = " ".join((text or "").split())
    return one_line[:limit]


def error_entry(where: str, exc: BaseException) -> dict:
    """An exception as report data: type, message and the traceback lines."""
    return {
        "where": where,
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip(),
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StepRecord:
    """One step's part of the report. Safe to call from any thread."""

    def __init__(self, data: dict, lock: threading.Lock):
        self.data = data
        self._lock = lock

    def set(self, key: str, value) -> None:
        with self._lock:
            self.data["results"][key] = value

    def update(self, values: dict, prefix: str = "") -> None:
        with self._lock:
            for key, value in values.items():
                self.data["results"][f"{prefix}{key}"] = value

    def answer(self, question: str, value: str) -> None:
        with self._lock:
            self.data["answers"][question] = value

    def error(self, where: str, exc: BaseException) -> None:
        with self._lock:
            self.data["errors"].append(error_entry(where, exc))

    def status(self, status: str) -> None:
        with self._lock:
            self.data["status"] = status

    def get(self, key: str, default=None):
        with self._lock:
            return self.data["results"].get(key, default)


class Report:
    """The whole report. `data` is the plain dict the render functions take."""

    def __init__(self, mode: str, meta: dict | None = None):
        self._lock = threading.Lock()
        self.data: dict = {
            "meta": {"mode": mode, "started": _now(), **(meta or {})},
            "steps": [],
            "log": [],
        }

    def step(self, key: str, title: str) -> StepRecord:
        """The record for a step, created the first time it is asked for."""
        with self._lock:
            for s in self.data["steps"]:
                if s["key"] == key:
                    return StepRecord(s, self._lock)
            s = {"key": key, "title": title, "status": STATUS_PENDING, "results": {}, "answers": {}, "errors": []}
            self.data["steps"].append(s)
            return StepRecord(s, self._lock)

    def meta(self, key: str, value) -> None:
        with self._lock:
            self.data["meta"][key] = value

    def set_log(self, lines: list[str]) -> None:
        with self._lock:
            self.data["log"] = list(lines)

    def snapshot(self) -> dict:
        """A deep copy of the data, taken under the lock, for rendering."""
        with self._lock:
            return json.loads(json.dumps(self.data, default=str))


# ----- rendering ---------------------------------------------------------------------------


def format_value(value) -> str:
    """One value on one line: plain for text and numbers, compact JSON for the rest."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value == value else "nan"
    if isinstance(value, (int, str)):
        return str(value).replace("\n", " | ")
    return json.dumps(value, default=str, ensure_ascii=False)


def flatten(values: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Nested dicts as (dotted key, value) pairs, in insertion order. Lists stay whole."""
    out: list[tuple[str, object]] = []
    for key, value in values.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.extend(flatten(value, f"{name}."))
        else:
            out.append((name, value))
    return out


def _section(number: int, step: dict) -> list[str]:
    title = f"Step {number}. {step['title']}  [{step['status']}]"
    lines = [title, "-" * len(title)]
    for key, value in flatten(step.get("results", {})):
        lines.append(f"{key}: {format_value(value)}")
    answers = step.get("answers", {})
    if answers:
        lines.append("answers:")
        lines.extend(f"  {q} -> {a}" for q, a in answers.items())
    for err in step.get("errors", []):
        lines.append(f"ERROR in {err['where']}: {err['type']}: {err['message']}")
        lines.extend(f"    {line}" for line in err.get("traceback", "").splitlines())
    return lines


def render_text(data: dict) -> str:
    """The report file: a header, one section per step, the log, then the JSON block."""
    head = "Lyssna probe report"
    lines = [head, "=" * len(head)]
    for key, value in flatten(data.get("meta", {})):
        lines.append(f"{key}: {format_value(value)}")
    for number, step in enumerate(data.get("steps", []), start=1):
        lines.append("")
        lines.extend(_section(number, step))
    log_lines = data.get("log") or []
    if log_lines:
        lines += ["", f"Log ({len(log_lines)} lines)", "---"]
        lines.extend(log_lines)
    lines += ["", JSON_BEGIN, render_json(data), JSON_END, ""]
    return "\n".join(lines)


def render_json(data: dict) -> str:
    """The same data as JSON, without the log (it is in the text above)."""
    slim = {k: v for k, v in data.items() if k != "log"}
    return json.dumps(slim, default=str, ensure_ascii=False, indent=1, sort_keys=False)


def parse_json_block(text: str) -> dict:
    """The JSON block back out of a report file (used by tests and by whoever reads the report)."""
    start = text.index(JSON_BEGIN) + len(JSON_BEGIN)
    end = text.index(JSON_END, start)
    return json.loads(text[start:end])
