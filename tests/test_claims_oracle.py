"""Frozen acceptance oracle for claims.extract_claims (Story 3). Orchestrator-owned: do not edit."""
import pytest

from claims import KINDS, Claim, extract_claims
from tests.fixtures.claim_cases import EXCLUDED, LOW, POSITIVE


def _real(claims):
    return [c for c in claims if not c.low]


@pytest.mark.parametrize("reply,user_message,expected", POSITIVE, ids=[p[0][:40] for p in POSITIVE])
def test_positive(reply, user_message, expected):
    got = _real(extract_claims(reply, user_message))
    assert len(got) == len(expected), f"expected {expected}, got {got}"
    unmatched = list(got)
    for exp in expected:
        hit = next((c for c in unmatched if c.kind == exp["kind"]
                    and ("count" not in exp or c.count == exp["count"])
                    and ("target" not in exp or c.target == exp["target"])), None)
        assert hit is not None, f"no claim matching {exp} in {got}"
        unmatched.remove(hit)


@pytest.mark.parametrize("reply,user_message,reason", EXCLUDED, ids=[e[2] + ":" + e[0][:30] for e in EXCLUDED])
def test_excluded(reply, user_message, reason):
    assert extract_claims(reply, user_message) == [], reason


@pytest.mark.parametrize("reply,user_message", LOW, ids=[x[0] for x in LOW])
def test_low(reply, user_message):
    got = extract_claims(reply, user_message)
    assert got, "vague claim should be reported as low"
    assert all(c.kind == "vague" and c.low for c in got)


def test_claim_shape():
    (c,) = extract_claims("All 42 tests pass now.")
    assert isinstance(c, Claim) and c.kind in KINDS and len(c.text) <= 200


def test_never_raises_on_junk():
    for junk in ["", "   ", "```", "```\nunterminated", "\x00\x01", "." * 5000, "?" * 10]:
        assert isinstance(extract_claims(junk, None), list)


def test_long_reply_is_fast():
    import time
    reply = ("I wrote `a.py` and all tests pass. " * 400) + "\n```\n" + ("x" * 50000) + "\n```"
    t0 = time.perf_counter()
    extract_claims(reply, "fix it")
    assert time.perf_counter() - t0 < 0.25
