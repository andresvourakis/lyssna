"""The Windows probe's pure parts: the report it writes, and the arithmetic behind screen
positions, microphone levels, key timings and WAV checks. The probe itself needs Windows."""

import io
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from probe import analysis, report


def _sample_report() -> dict:
    r = report.Report("guided", {"probe_version": 1})
    s = r.step("about", "About this PC")
    s.update({"windows": {"name": "Windows 11", "build": 26100}, "python": "3.11.9"})
    s.set("screens", [{"name": "A", "dpr": 1.5}])
    s.status(report.STATUS_DONE)
    sel = r.step("selection", "Reading the selection")
    sel.answer("notepad: Is this the text you selected?", "Yes")
    try:
        raise ValueError("no pattern")
    except ValueError as e:
        sel.error("selection in notepad", e)
    sel.status(report.STATUS_ERROR)
    r.step("keys", "The dictation keys")
    r.set_log(["12:00:00 INFO lyssna.win.capture: Ctrl+C copied 10 chars"])
    return r.snapshot()


def test_snippet_is_one_line_and_cut_at_sixty():
    text = "word " * 40 + "\nend"
    out = report.snippet(text)
    assert len(out) == 60 and "\n" not in out
    assert report.snippet(None) == ""


def test_format_value():
    assert report.format_value(None) == "none"
    assert report.format_value(True) == "yes"
    assert report.format_value(0.12345) == "0.123"
    assert report.format_value(2.0) == "2"
    assert report.format_value("a\nb") == "a | b"
    assert report.format_value([1, 2]) == "[1, 2]"


def test_flatten_dots_nested_keys_and_keeps_lists():
    assert report.flatten({"a": {"b": 1, "c": {"d": [1]}}, "e": {}}) == [("a.b", 1), ("a.c.d", [1]), ("e", {})]


def test_render_text_has_a_section_per_step_with_answers_errors_and_log():
    text = report.render_text(_sample_report())
    assert "Step 1. About this PC  [done]" in text
    assert "windows.build: 26100" in text
    assert "Step 2. Reading the selection  [error]" in text
    assert "notepad: Is this the text you selected? -> Yes" in text
    assert "ERROR in selection in notepad: ValueError: no pattern" in text
    assert "Traceback" in text
    assert "Step 3. The dictation keys  [not reached]" in text
    assert "Ctrl+C copied 10 chars" in text


def test_json_block_round_trips_without_the_log():
    data = _sample_report()
    parsed = report.parse_json_block(report.render_text(data))
    assert parsed["meta"]["mode"] == "guided"
    assert [s["key"] for s in parsed["steps"]] == ["about", "selection", "keys"]
    assert parsed["steps"][0]["results"]["windows"]["build"] == 26100
    assert "log" not in parsed


def test_step_record_is_reused_by_key():
    r = report.Report("guided")
    r.step("a", "A").set("x", 1)
    r.step("a", "A").set("y", 2)
    assert r.snapshot()["steps"] == [{"key": "a", "title": "A", "status": report.STATUS_PENDING,
                                      "results": {"x": 1, "y": 2}, "answers": {}, "errors": []}]


# ----- screens and the floating button -----------------------------------------------------

PRIMARY = {"name": "primary", "x": 0, "y": 0, "width": 1280, "height": 720, "dpr": 1.5}  # 1920x1080 physical
LEFT = {"name": "left", "x": -1920, "y": 0, "width": 1920, "height": 1080, "dpr": 1.0}


def test_physical_to_qt_divides_inside_the_screen_only():
    assert analysis.physical_to_qt(300, 150, [PRIMARY, LEFT]) == (200, 100, "primary")
    assert analysis.physical_to_qt(-100, 50, [PRIMARY, LEFT]) == (-100, 50, "left")


def test_screen_at_falls_back_to_the_nearest_screen():
    assert analysis.screen_at(5000, 10, [PRIMARY, LEFT])["name"] == "primary"
    assert analysis.screen_at(1, 1, []) is None


def test_button_sits_centred_above_the_selection():
    pos = analysis.button_position((600, 300, 300, 30), (0, 0), [PRIMARY])
    # anchor (750, 300) physical -> (500, 200) Qt; 34 wide, 8 above
    assert pos["anchor"] == "selection"
    assert pos["qt"] == [483.0, 158.0]
    assert pos["physical"] == [724, 237]
    assert pos["screen"] == "primary"


def test_button_uses_the_cursor_without_a_selection_and_stays_on_screen():
    pos = analysis.button_position(None, (3, 3), [PRIMARY])
    assert pos["anchor"] == "cursor"
    assert pos["qt"] == [0, 0]
    assert analysis.button_position(None, None, [PRIMARY])["anchor"] == "none"


# ----- microphone levels -------------------------------------------------------------------


def test_levels_int16():
    data = struct.pack("<4h", 0, 16384, -32768, 0)
    lv = analysis.levels(data, "Int16")
    assert lv["samples"] == 4 and lv["peak"] == 1.0
    assert abs(lv["rms"] - ((0.25 + 1.0) / 4) ** 0.5) < 1e-3


def test_levels_float_and_silence():
    assert analysis.levels(struct.pack("<2f", 0.5, -0.25), "Float")["peak"] == 0.5
    assert analysis.levels(b"", "Int16") == {"samples": 0, "peak": 0.0, "rms": 0.0}
    assert analysis.levels(bytes([128, 128]), "UInt8")["peak"] == 0.0


# ----- keys --------------------------------------------------------------------------------

CTRL, WIN, SPACE = 0xA2, 0x5B, 0x20


def test_chord_taps_and_release():
    events = [(CTRL, True, 0.0), (WIN, True, 0.1), (WIN, False, 2.1), (CTRL, False, 2.2)]
    assert analysis.chord_taps(events) == [(0.1, 2.1)]
    assert analysis.chord_released(events)
    assert not analysis.chord_released(events[:3])
    assert not analysis.chord_released([(CTRL, True, 0.0), (CTRL, False, 0.1)])


def test_double_tap_timings():
    events = [(CTRL, True, 0.0), (WIN, True, 0.02), (WIN, False, 0.12), (CTRL, False, 0.13),
              (CTRL, True, 0.30), (WIN, True, 0.31), (CTRL, False, 0.40), (WIN, False, 0.41)]
    assert analysis.double_tap(events) == {"taps_seen": 2, "first_tap_seconds": 0.1, "second_tap_seconds": 0.09,
                                           "gap_seconds": 0.19}


def test_describe_events_marks_swallowed():
    out = analysis.describe_events([(CTRL, True, 1.0), (SPACE, True, 1.25)], swallowed={1})
    assert out == ["0xA2 down +0.000s", "0x20 down +0.250s swallowed"]


def test_altgr_from_scans():
    # '@' on a Swedish layout: AltGr (Ctrl+Alt = 6) + the 2 key; '$' needs no AltGr; '€' missing.
    assert analysis.altgr_from_scans({"@": 0x0632, "$": 0x0134, "€": -1}) == {"has_altgr": True, "altgr_characters": "@"}
    assert analysis.altgr_from_scans({"@": 0x0132})["has_altgr"] is False


# ----- WAV -----------------------------------------------------------------------------------


def test_wav_info():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\0\0" * 22050)
    info = analysis.wav_info(buf.getvalue())
    assert info["valid"] and info["seconds"] == 1.0 and info["rate"] == 22050
    bad = analysis.wav_info(b"not a wav")
    assert bad["valid"] is False and "error" in bad
