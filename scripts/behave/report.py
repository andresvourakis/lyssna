"""The side-by-side report: every scenario's facts for the old and the new interface, flattened to
one line per fact, with the ones that differ marked. Timings count as the same within TIME_SLACK
of each other; everything else must be equal. A scenario with an error on either side is "could
not be exercised". This is the mechanical part; the reading of the differences is a person's.

Pure Python, tested in tests/test_behave_report.py."""

from __future__ import annotations

from pathlib import Path

TIME_SLACK_MS = 15.0     # two timings this close count as the same
TIME_SLACK_RATIO = 0.15  # or this close relative to the larger one
SKIP_KEYS = ("id", "trials", "traceback", "_seconds", "log", "ax", "texts", "_log_errors")
# Only the new side has probes (see launch.py), and a summary's spread is in the evidence: the
# comparison keeps each summary's median.
SKIP_PARTS = ("paint_", "log_area_enter", "steps_", "capture_")
SKIP_ENDINGS = (".min", ".max", ".n")


def flatten(value, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            if k in SKIP_KEYS:
                continue
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        for i, v in enumerate(value):
            out.update(flatten(v, f"{prefix}[{i}]"))
    elif not any(part in prefix for part in SKIP_PARTS) and not prefix.endswith(SKIP_ENDINGS):
        out[prefix] = value
    return out


def same(key: str, a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if key.endswith("_ms") or "_ms." in key:
            return abs(a - b) <= max(TIME_SLACK_MS, TIME_SLACK_RATIO * max(abs(a), abs(b)))
        if isinstance(a, int) and isinstance(b, int):
            return a == b  # counts must match
        return abs(a - b) <= 0.05 * max(abs(a), abs(b))
    return a == b


def compare(old: dict | None, new: dict | None) -> tuple[str, list[tuple[str, object, object]]]:
    """(verdict, rows that differ). The verdict is "same", "differs" or "could not be exercised"."""
    if not old or not new or "error" in old or "error" in new:
        return "could not be exercised", []
    a, b = flatten(old), flatten(new)
    rows = [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if not same(k, a.get(k), b.get(k))]
    return ("differs" if rows else "same"), rows


def _fmt(v) -> str:
    text = "-" if v is None else str(v)
    return text if len(text) <= 70 else text[:67] + "..."


def write_report(out: Path, results: dict, notes: list[str] | None = None, stopped: str | None = None) -> Path:
    lines = ["# Lyssna live behaviour: old (legacy) vs new (Qt)", ""]
    if stopped:
        lines += [f"**The run {stopped}.**", ""]
    for n in notes or []:
        lines.append(f"- {n}")
    lines += ["", "| scenario | verdict | facts that differ |", "|---|---|---|"]
    detail = []
    for name, sides in results.items():
        verdict, rows = compare(sides.get("legacy"), sides.get("new"))
        lines.append(f"| {name} | {verdict} | {len(rows)} |")
        detail += [f"## {name}: {verdict}", ""]
        for side in ("legacy", "new"):
            err = (sides.get(side) or {}).get("error")
            if err:
                detail.append(f"- {side}: {err}")
        if rows:
            detail += ["| fact | old | new |", "|---|---|---|"]
            detail += [f"| {k} | {_fmt(a)} | {_fmt(b)} |" for k, a, b in rows]
        detail += ["", f"Evidence: {out / name}", ""]
    path = out / "report.md"
    path.write_text("\n".join([*lines, "", *detail]) + "\n")
    return path
