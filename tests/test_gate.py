"""Tests for gate.py. Uses fake claims/matcher modules until the real ones land."""
import logging
import sys
import types
from dataclasses import dataclass

import pytest

# Fake verdict for tests that need to inject verdicts
@dataclass
class FakeVerdict:
    claim: str
    severity: str | None
    label: str
    evidence: str
    phrase: str


# The real claims/matcher modules exist; never install fakes into sys.modules (they leak into every later
# test module in the run and shadow the real ones). Tests that need canned verdicts monkeypatch gate.* instead.
import gate
from gate import Gate


@pytest.fixture
def fresh_gate():
    """Fresh Gate in observe mode."""
    return Gate(mode="observe")


@pytest.fixture
def annotate_gate():
    """Fresh Gate in annotate mode."""
    return Gate(mode="annotate")


# --- on_post_tool_call ---

def test_post_tool_call_records_to_ledger(fresh_gate):
    fresh_gate.on_post_tool_call(
        tool_name="terminal",
        args={"command": "pytest -q"},
        result='{"output": "3 passed", "exit_code": 0}',
        session_id="s1",
        turn_id="t1",
    )
    receipts = fresh_gate.ledger.window(session_id="s1", turn_id="t1")
    # window returns list of (offset, receipts)
    all_receipts = []
    for offset, recs in receipts:
        all_receipts.extend(recs)
    assert len(all_receipts) == 1
    assert all_receipts[0].tool_name == "terminal"


def test_post_tool_call_returns_none(fresh_gate):
    result = fresh_gate.on_post_tool_call(
        tool_name="terminal", args={}, result="{}", session_id="s", turn_id="t"
    )
    assert result is None


# --- on_pre_llm_call ---

def test_pre_llm_call_begins_turn(fresh_gate):
    fresh_gate.on_pre_llm_call(session_id="s1", turn_id="t1", user_message="run tests",
                                conversation_history=[], is_first_turn=False, model="m", platform="cli")
    # Turn should be in the ledger
    receipts = fresh_gate.ledger.window(session_id="s1", turn_id="t1")
    # No tool calls yet, so empty
    assert all(len(recs) == 0 for _, recs in receipts)


def test_pre_llm_call_fork_does_not_begin_turn(fresh_gate):
    """Fork turns (containing ':') should not begin a main turn."""
    # First establish a main turn
    fresh_gate.on_pre_llm_call(session_id="s1", turn_id="t1", user_message="main msg",
                                conversation_history=[], is_first_turn=False, model="m", platform="cli")
    # Now a fork turn - should not appear in the main turn order
    fresh_gate.on_pre_llm_call(session_id="s1", turn_id="t1:fork", user_message="fork msg",
                                conversation_history=[], is_first_turn=False, model="m", platform="cli")
    # The fork should not have created a new main turn entry
    # Verify: window for t1 should still work normally
    window = fresh_gate.ledger.window(session_id="s1", turn_id="t1")
    # t1 should be the most recent main turn (offset 0)
    offsets = [off for off, _ in window]
    assert 0 in offsets


def test_pre_llm_call_no_nudge_returns_none(fresh_gate):
    result = fresh_gate.on_pre_llm_call(session_id="s1", turn_id="t1", user_message="hi",
                                         conversation_history=[], is_first_turn=False,
                                         model="m", platform="cli")
    assert result is None


def test_pre_llm_call_pops_nudge(annotate_gate, monkeypatch):
    """If a nudge was stored, pre_llm_call should return it and clear it."""
    # Manually store a nudge
    with annotate_gate._lock:
        annotate_gate._pending_nudges["s1"] = "test nudge text"

    result = annotate_gate.on_pre_llm_call(session_id="s1", turn_id="t2", user_message="ok",
                                            conversation_history=[], is_first_turn=False,
                                            model="m", platform="cli")
    assert result == {"context": "test nudge text"}

    # Second call should have no nudge
    result2 = annotate_gate.on_pre_llm_call(session_id="s1", turn_id="t3", user_message="ok",
                                             conversation_history=[], is_first_turn=False,
                                             model="m", platform="cli")
    assert result2 is None


# --- on_transform (annotate mode) ---

def test_transform_no_claims_returns_none(annotate_gate, monkeypatch):
    """If extract_claims finds no claims, return None (no footer)."""
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [])
    result = annotate_gate.on_transform("All good.", session_id="s1", turn_id="t1")
    assert result is None


def test_transform_backed_claim_no_footer(annotate_gate, monkeypatch):
    """If match finds no high/medium verdicts, no footer."""
    claims = [FakeVerdict(claim="tests pass", severity=None, label="tests", evidence="backed", phrase="tests pass")]
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [claims[0]])
    monkeypatch.setattr(gate, "match", lambda c, w: [claims[0]])  # severity None -> backed
    result = annotate_gate.on_transform("All tests pass.", session_id="s1", turn_id="t1")
    assert result is None


def test_transform_unbacked_high_adds_footer(annotate_gate, monkeypatch):
    """High-severity verdict gets a footer."""
    verdict = FakeVerdict(claim="tests pass", severity="high", label="tests",
                          evidence="0 test commands run", phrase="tests pass")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [verdict])
    monkeypatch.setattr(gate, "match", lambda c, w: [verdict])
    result = annotate_gate.on_transform("All tests pass.", session_id="s1", turn_id="t1")
    expected_footer = "[say-do] Claimed: tests pass. Ledger: 0 test commands run."
    assert result == f"All tests pass.\n\n{expected_footer}"


def test_transform_strips_trailing_whitespace(annotate_gate, monkeypatch):
    """Footer is appended after stripping trailing whitespace from response."""
    verdict = FakeVerdict(claim="tests pass", severity="high", label="tests",
                          evidence="0 test commands run", phrase="tests pass")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [verdict])
    monkeypatch.setattr(gate, "match", lambda c, w: [verdict])
    result = annotate_gate.on_transform("All tests pass.  \n", session_id="s1", turn_id="t1")
    assert result == "All tests pass.\n\n[say-do] Claimed: tests pass. Ledger: 0 test commands run."


def test_transform_empty_text_returns_none(annotate_gate, monkeypatch):
    """Empty or non-string response text returns None."""
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [])
    assert annotate_gate.on_transform("", session_id="s1", turn_id="t1") is None
    assert annotate_gate.on_transform(None, session_id="s1", turn_id="t1") is None
    assert annotate_gate.on_transform(42, session_id="s1", turn_id="t1") is None


def test_transform_low_severity_no_footer(annotate_gate, monkeypatch):
    """Low severity doesn't trigger a footer."""
    verdict = FakeVerdict(claim="done", severity="low", label="done", evidence="0 tool calls", phrase="done")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [verdict])
    monkeypatch.setattr(gate, "match", lambda c, w: [verdict])
    result = annotate_gate.on_transform("Done.", session_id="s1", turn_id="t1")
    assert result is None


# --- on_transform (observe mode) ---

def test_observe_transform_returns_none(annotate_gate, monkeypatch):
    """In observe mode, transform always returns None (no rewriting)."""
    observe = Gate(mode="observe")
    verdict = FakeVerdict(claim="tests pass", severity="high", label="tests",
                          evidence="0 test commands run", phrase="tests pass")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [verdict])
    monkeypatch.setattr(gate, "match", lambda c, w: [verdict])
    result = observe.on_transform("All tests pass.", session_id="s1", turn_id="t1")
    assert result is None


def test_observe_stores_nudge(annotate_gate, monkeypatch):
    """In observe mode with high/medium verdicts, store a nudge for next turn."""
    observe = Gate(mode="observe")
    verdict = FakeVerdict(claim="tests pass", severity="high", label="tests",
                          evidence="0 test commands run", phrase="tests pass")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [verdict])
    monkeypatch.setattr(gate, "match", lambda c, w: [verdict])
    observe.on_transform("All tests pass.", session_id="s1", turn_id="t1")

    # Check that a nudge was stored
    with observe._lock:
        assert "s1" in observe._pending_nudges

    # And the next pre_llm_call returns it
    result = observe.on_pre_llm_call(session_id="s1", turn_id="t2", user_message="ok",
                                      conversation_history=[], is_first_turn=False,
                                      model="m", platform="cli")
    assert result is not None
    assert "context" in result
    assert "[say-do gate]" in result["context"]


def test_observe_no_nudge_for_backed(annotate_gate, monkeypatch):
    """No nudge if all claims are backed."""
    observe = Gate(mode="observe")
    verdict = FakeVerdict(claim="tests pass", severity=None, label="tests",
                          evidence="pytest passed", phrase="tests pass")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [verdict])
    monkeypatch.setattr(gate, "match", lambda c, w: [verdict])
    observe.on_transform("All tests pass.", session_id="s1", turn_id="t1")

    result = observe.on_pre_llm_call(session_id="s1", turn_id="t2", user_message="ok",
                                      conversation_history=[], is_first_turn=False,
                                      model="m", platform="cli")
    assert result is None


# --- last_report ---

def test_last_report_empty_initially(fresh_gate):
    assert fresh_gate.last_report("s1") == []


def test_last_report_stored_after_transform(fresh_gate, monkeypatch):
    verdict = FakeVerdict(claim="x", severity="high", label="x", evidence="y", phrase="x")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [verdict])
    monkeypatch.setattr(gate, "match", lambda c, w: [verdict])
    fresh_gate.on_transform("Some text.", session_id="s1", turn_id="t1")
    report = fresh_gate.last_report("s1")
    assert len(report) == 1
    assert report[0].phrase == "x"


# --- logging privacy ---

def test_transform_logs_summary_not_text(annotate_gate, monkeypatch, caplog):
    """Transform logs a summary line but never the reply text, phrase, or evidence."""
    verdict = FakeVerdict(claim="tests pass", severity="high", label="tests",
                          evidence="0 test commands run", phrase="tests pass")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [verdict])
    monkeypatch.setattr(gate, "match", lambda c, w: [verdict])
    secret_text = "ALL TESTS PASS TOKEN-SECRET-123"
    with caplog.at_level(logging.DEBUG):
        annotate_gate.on_transform(secret_text, session_id="s1", turn_id="t1")
    assert "TOKEN-SECRET-123" not in caplog.text
    assert "say_do_gate turn evaluated" in caplog.text


def test_transform_logs_finding_lines(annotate_gate, monkeypatch, caplog):
    """Each high/medium verdict gets a finding log line with kind/severity/label only."""
    v1 = FakeVerdict(claim="a", severity="high", label="claim_a", evidence="ev-a", phrase="a")
    v2 = FakeVerdict(claim="b", severity="medium", label="claim_b", evidence="ev-b", phrase="b")
    v3 = FakeVerdict(claim="c", severity="low", label="claim_c", evidence="ev-c", phrase="c")
    monkeypatch.setattr(gate, "extract_claims", lambda text, user=None: [v1, v2, v3])
    monkeypatch.setattr(gate, "match", lambda c, w: [v1, v2, v3])
    with caplog.at_level(logging.DEBUG):
        annotate_gate.on_transform("text", session_id="s1", turn_id="t1")
    assert "kind=claim_a severity=high" in caplog.text
    assert "kind=claim_b severity=medium" in caplog.text
    # Low severity should NOT be logged as a finding
    assert "claim_c" not in caplog.text


# --- threading / max_pending ---

def test_max_pending_evicts(fresh_gate):
    """With max_pending=2, storing nudges for 3 sessions evicts the oldest."""
    g = Gate(mode="observe", max_pending=2)
    g._store_nudge("s1", "nudge1")
    g._store_nudge("s2", "nudge2")
    g._store_nudge("s3", "nudge3")
    with g._lock:
        # s1 should have been evicted
        assert "s1" not in g._pending_nudges
        assert "s2" in g._pending_nudges
        assert "s3" in g._pending_nudges
