"""Where the support links go and when they show (lyssna.support)."""

import ast
from pathlib import Path

from lyssna import support


def test_no_website_address_means_no_links(monkeypatch):
    assert support.url() == ""
    assert support.enabled() is False
    assert support.in_history(1_000_000) is False
    support.open_support("menu")  # does nothing: conftest's _open would refuse


def test_links_open_the_give_only_pay_card(monkeypatch):
    opened = []
    monkeypatch.setattr(support, "SITE_URL", "https://example.test/")
    monkeypatch.setattr(support, "_open", opened.append)
    assert support.url() == "https://example.test/?support=1"
    support.open_support("menu")
    assert opened == ["https://example.test/?support=1"]


def test_history_line_waits_for_totals_worth_a_look(monkeypatch):
    monkeypatch.setattr(support, "SITE_URL", "https://example.test")
    assert support.in_history(0) is False
    assert support.in_history(support.HISTORY_AFTER_WORDS - 1) is False
    assert support.in_history(support.HISTORY_AFTER_WORDS) is True


def test_the_address_can_be_given_at_launch_to_preview_the_links(monkeypatch):
    monkeypatch.setenv(support.SITE_ENV, "http://localhost:8787")
    assert support.url() == "http://localhost:8787/?support=1"


def test_nothing_about_the_user_goes_in_the_link(monkeypatch):
    monkeypatch.setattr(support, "SITE_URL", "https://example.test")
    assert support.url().split("?")[1] == "support=1"


def test_the_shipped_website_address_is_set():
    # Releases 0.3.0 to 0.3.2 shipped with SITE_URL blank, so every support link was hidden.
    # conftest blanks SITE_URL for every test, so the value is read from the source file.
    tree = ast.parse(Path(support.__file__).read_text(encoding="utf-8"))
    values = [node.value.value for node in tree.body
              if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SITE_URL" for t in node.targets)
              and isinstance(node.value, ast.Constant)]
    assert len(values) == 1, "lyssna/support.py must set SITE_URL to one plain string"
    site = values[0]
    assert isinstance(site, str) and site.strip(), "SITE_URL is empty, so the app would hide every support link"
    assert site.startswith("https://"), f"SITE_URL must start with https://, not {site!r}"
