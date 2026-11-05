"""Tests for render.py: footer and nudge text generation."""
from dataclasses import dataclass

import pytest

from render import render_footer, render_nudge


@dataclass
class FakeVerdict:
    claim: str
    severity: str | None
    label: str
    evidence: str
    phrase: str


def V(severity, phrase, evidence, claim=None, label="test"):
    return FakeVerdict(claim=claim or phrase, severity=severity, label=label,
                       evidence=evidence, phrase=phrase)


# --- render_footer ---

def test_footer_empty_verdicts():
    assert render_footer([]) is None


def test_footer_none_verdicts():
    assert render_footer(None) is None


def test_footer_low_severity_not_shown():
    vs = [V("low", "might work", "maybe")]
    assert render_footer(vs) is None


def test_footer_none_severity_not_shown():
    vs = [V(None, "backed", "yes")]
    assert render_footer(vs) is None


def test_footer_single_high():
    vs = [V("high", "tests pass", "0 test commands run")]
    assert render_footer(vs) == "[say-do] Claimed: tests pass. Ledger: 0 test commands run."


def test_footer_single_medium():
    vs = [V("medium", "deployed", "no deploy receipt")]
    assert render_footer(vs) == "[say-do] Claimed: deployed. Ledger: no deploy receipt."


def test_footer_high_before_medium():
    vs = [V("medium", "b", "ev-b"), V("high", "a", "ev-a")]
    footer = render_footer(vs)
    lines = footer.split("\n")
    assert lines == [
        "[say-do] Claimed: a. Ledger: ev-a.",
        "[say-do] Claimed: b. Ledger: ev-b.",
    ]


def test_footer_multiple_high_same_order():
    vs = [V("high", "first", "ev-1"), V("high", "second", "ev-2"),
          V("medium", "med", "ev-m")]
    footer = render_footer(vs)
    lines = footer.split("\n")
    assert len(lines) == 2
    assert lines[0] == "[say-do] Claimed: first. Ledger: ev-1."
    # Third (medium) gets the "more" suffix
    assert lines[1] == "[say-do] Claimed: second. Ledger: ev-2. (+1 more)"


def test_footer_more_than_two_appends_count():
    vs = [V("high", "a", "ev-a"), V("high", "b", "ev-b"), V("high", "c", "ev-c")]
    footer = render_footer(vs)
    lines = footer.split("\n")
    assert len(lines) == 2
    assert "(+1 more)" in lines[1]


def test_footer_four_visible():
    vs = [V("high", "a", "ev-a"), V("high", "b", "ev-b"),
          V("medium", "c", "ev-c"), V("medium", "d", "ev-d")]
    footer = render_footer(vs)
    lines = footer.split("\n")
    assert len(lines) == 2
    assert lines[0] == "[say-do] Claimed: a. Ledger: ev-a."
    assert lines[1] == "[say-do] Claimed: b. Ledger: ev-b. (+2 more)"


def test_footer_no_leading_newlines():
    vs = [V("high", "x", "y")]
    footer = render_footer(vs)
    assert not footer.startswith("\n")


# --- render_nudge ---

def test_nudge_empty_verdicts():
    assert render_nudge([]) is None


def test_nudge_none_verdicts():
    assert render_nudge(None) is None


def test_nudge_only_low():
    vs = [V("low", "might work", "maybe")]
    assert render_nudge(vs) is None


def test_nudge_none_severity():
    vs = [V(None, "ok", "backed")]
    assert render_nudge(vs) is None


def test_nudge_single_high():
    vs = [V("high", "tests pass", "0 test commands run")]
    nudge = render_nudge(vs)
    assert nudge == ("[say-do gate] Your previous reply claimed: tests pass. "
                     "Tool ledger: 0 test commands run. "
                     "Verify with a tool call and report the real result, or correct the claim.")


def test_nudge_single_medium():
    vs = [V("medium", "deployed", "no deploy receipt")]
    nudge = render_nudge(vs)
    assert nudge.startswith("[say-do gate] Your previous reply claimed: deployed.")
    assert "no deploy receipt" in nudge


def test_nudge_multiple_high_first():
    vs = [V("high", "a", "ev-a"), V("high", "b", "ev-b")]
    nudge = render_nudge(vs)
    assert nudge.startswith("[say-do gate] Your previous reply made claims the tool ledger does not support: ")
    assert "a (ledger: ev-a)" in nudge
    assert "b (ledger: ev-b)" in nudge
    assert "Verify with tool calls and report the real results, or correct the claims." in nudge


def test_nudge_three_visible():
    vs = [V("high", "a", "ev-a"), V("high", "b", "ev-b"), V("medium", "c", "ev-c")]
    nudge = render_nudge(vs)
    assert "a (ledger: ev-a)" in nudge
    assert "b (ledger: ev-b)" in nudge
    assert "c (ledger: ev-c)" in nudge


def test_nudge_more_than_three_only_lists_three():
    vs = [V("high", "a", "ev-a"), V("high", "b", "ev-b"),
          V("high", "c", "ev-c"), V("medium", "d", "ev-d")]
    nudge = render_nudge(vs)
    assert "a (ledger: ev-a)" in nudge
    assert "b (ledger: ev-b)" in nudge
    assert "c (ledger: ev-c)" in nudge
    assert "d (ledger: ev-d)" not in nudge


def test_nudge_high_before_medium_in_list():
    vs = [V("medium", "b", "ev-b"), V("high", "a", "ev-a")]
    nudge = render_nudge(vs)
    a_pos = nudge.find("a (ledger: ev-a)")
    b_pos = nudge.find("b (ledger: ev-b)")
    assert a_pos < b_pos
