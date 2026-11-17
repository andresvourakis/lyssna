"""Usage counter: only paid providers are counted, the menu line hides at zero cost, and the
file keeps at most twelve months. USAGE_PATH points into the per-test temp dir (conftest)."""

import json

from lyssna import usage

PRICES = {"kokoro": 0.0, "say": 0.0, "inworld": 25.0, "cartesia": 50.0}


def test_free_providers_are_not_recorded():
    usage.record("kokoro", 500, 0.0)
    usage.record("say", 500, 0.0)
    assert usage.this_month() == {}
    assert not usage.USAGE_PATH.exists()


def test_paid_providers_add_up():
    usage.record("inworld", 300, 25.0)
    usage.record("inworld", 200, 25.0)
    usage.record("cartesia", 1000, 50.0)
    assert usage.this_month() == {"inworld": 500, "cartesia": 1000}


def test_summary_is_empty_without_cost():
    assert usage.summary(PRICES) == ""
    usage.record("kokoro", 5000, 0.0)
    assert usage.summary(PRICES) == ""


def test_summary_ignores_old_free_counts():
    """A file written before free voices stopped being counted still shows only paid usage."""
    month = usage._month()
    usage.USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage.USAGE_PATH.write_text(json.dumps({month: {"kokoro": 90_000}}))
    assert usage.summary(PRICES) == ""
    usage.record("inworld", 40_000, 25.0)
    assert usage.summary(PRICES) == "This month: 40k characters, about $1.00"


def test_summary_small_counts():
    usage.record("cartesia", 400, 50.0)
    assert usage.summary(PRICES) == "This month: 400 characters, about $0.02"


def test_keeps_only_the_last_twelve_months():
    old = {f"2025-{m:02d}": {"inworld": m} for m in range(1, 13)}
    usage.USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage.USAGE_PATH.write_text(json.dumps(old))
    usage.record("inworld", 10, 25.0)
    months = sorted(json.loads(usage.USAGE_PATH.read_text()))
    assert len(months) == usage.KEEP_MONTHS
    assert months[0] == "2025-02" and months[-1] == usage._month()


def test_a_broken_file_reads_as_empty():
    usage.USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage.USAGE_PATH.write_text("[1, 2]")
    assert usage.this_month() == {}
    usage.record("inworld", 10, 25.0)
    assert usage.this_month() == {"inworld": 10}
