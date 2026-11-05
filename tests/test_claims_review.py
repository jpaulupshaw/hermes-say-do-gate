"""Orchestrator review regressions for claims.py (found in code review, not covered by the oracle)."""
import pytest

from claims import extract_claims


def kinds(reply, user=None):
    return [c.kind for c in extract_claims(reply, user)]


@pytest.mark.parametrize("reply,expected", [
    ("I’ve executed the backfill script.", ["ran_command"]),          # curly apostrophe
    ("I have confirmed the endpoint returns 200.", ["verified"]),
    ("I’ve written `notes.md`.", ["file_written"]),
])
def test_contractions_and_curly_quotes(reply, expected):
    assert kinds(reply) == expected


def test_reply_starting_with_done_is_not_a_bare_done():
    got = extract_claims("Done — I restarted the service and all tests pass.", "restart the service")
    assert "done" not in [c.kind for c in got]
    assert {"deployed", "tests_pass"} <= {c.kind for c in got}
    assert kinds("Completed the rollout plan draft for review.", "write the plan") != ["done"]


@pytest.mark.parametrize("reply", [
    "The deploy script lives in ops/.",
    "Deploying requires the staging token.",
    "Ready to push to prod after review.",
])
def test_deploy_nouns_and_bare_verbs_are_not_claims(reply):
    assert "deployed" not in kinds(reply)


@pytest.mark.parametrize("reply", [
    "The service is running now.",
    "It's working now.",
    "Everything is loading now.",
])
def test_state_descriptions_are_not_underway(reply):
    assert "underway" not in kinds(reply)


def test_first_person_progressive_is_underway():
    assert kinds("I'm running it now.") == ["underway"]


def test_absolute_path_target_keeps_leading_slash():
    (c,) = extract_claims("I wrote the summary to /var/tmp/out/summary.json.")
    assert c.kind == "file_written" and c.target == "/var/tmp/out/summary.json"


def test_tests_word_boundary_does_not_swallow_ran_command():
    assert kinds("I ran the attestation export script.") == ["ran_command"]
