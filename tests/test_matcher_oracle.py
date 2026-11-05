"""Frozen acceptance oracle for matcher.match (Story 4). Orchestrator-owned: do not edit.

Uses real Ledger receipts so the ledger->matcher seam is exercised. All data invented.
"""
import json

import pytest

from claims import Claim
from ledger import Ledger
from matcher import Verdict, is_test_command, match


def T(cmd, code=0, out="", bg=False):
    return dict(tool_name="terminal", args={"command": cmd, "background": bg},
                result=json.dumps({"output": out, "exit_code": code, "error": None}))


def W(path, ok=True, tool="write_file"):
    return dict(tool_name=tool, args={"path": path},
                result={"files_modified": [path]} if ok else {"error": "permission denied"})


def win(*turns):
    """turns oldest-first, each a list of record kwargs; returns window for the last turn."""
    lg = Ledger(lookback_turns=3)
    for i, recs in enumerate(turns, 1):
        lg.begin_turn(session_id="s", turn_id=f"t{i}")
        for r in recs:
            lg.record(**r, session_id="s", turn_id=f"t{i}")
    return lg.window(session_id="s", turn_id=f"t{len(turns)}")


def one(claim, window):
    (v,) = match([claim], window)
    assert isinstance(v, Verdict) and v.claim == claim
    return v


TP = Claim(kind="tests_pass", text="All tests pass.")


@pytest.mark.parametrize("cmd,expected", [
    ("pytest -q", True), ("python -m pytest tests/", True), ("cd x && npm run test", True),
    ("go test ./...", True), ("make test", True), ("cargo test --all", True), ("uv run pytest", True),
    ("ls -la", False), ("cat pytest.ini", False), ("echo testing", False), ("grep -r test .", False), (None, False),
])
def test_is_test_command(cmd, expected):
    assert is_test_command(cmd) is expected


def test_tests_pass_unbacked_is_medium():
    v = one(TP, win([T("ls")]))
    assert (v.severity, v.label, v.evidence, v.phrase) == ("medium", "unbacked", "0 test commands run", "tests pass")


def test_tests_pass_failed_run_is_high():
    v = one(TP, win([T("pytest -q", 1, "3 failed, 14 passed")]))
    assert (v.severity, v.label) == ("high", "contradicted")
    assert v.evidence == "`pytest -q` exited 1"


def test_tests_pass_backed_current_turn():
    v = one(TP, win([T("pytest -q", 0, "17 passed in 3.1s")]))
    assert (v.severity, v.label) == (None, "backed (turn 0)")


def test_later_success_overrides_earlier_failure_in_turn():
    v = one(TP, win([T("pytest -q", 1, "1 failed"), T("pytest -q", 0, "18 passed")]))
    assert v.severity is None


def test_later_failure_overrides_earlier_success_in_turn():
    v = one(TP, win([T("pytest -q", 0, "18 passed"), T("pytest -q", 2, "1 error")]))
    assert v.severity == "high"


def test_count_mismatch_is_high():
    v = one(Claim(kind="tests_pass", text="All 42 tests pass.", count=42), win([T("pytest", 0, "40 passed in 1s")]))
    assert (v.severity, v.label, v.evidence, v.phrase) == ("high", "contradicted", "ledger shows 40 passed, not 42", "42 tests pass")


def test_count_match_is_supported_and_unparseable_is_low():
    c = Claim(kind="tests_pass", text="All 17 tests pass.", count=17)
    assert one(c, win([T("pytest", 0, "== 17 passed in 2s ==")])).severity is None
    v = one(c, win([T("pytest", 0, "OK")]))
    assert (v.severity, v.label) == ("low", "unverifiable")


def test_lookback_success_backs_claim():
    v = one(TP, win([T("pytest -q", 0, "9 passed")], [T("ls")], []))
    assert (v.severity, v.label) == (None, "backed (turn -2)")


def test_lookback_failure_is_stale_success_claim():
    v = one(TP, win([T("pytest -q", 1, "2 failed")], []))
    assert (v.severity, v.label, v.evidence) == ("high", "contradicted", "`pytest -q` exited 1 in turn -1")


def test_lookback_nearest_turn_decides():
    v = one(TP, win([T("pytest", 1)], [T("pytest", 0, "5 passed")], []))
    assert v.label == "backed (turn -1)"


def test_beyond_lookback_is_unbacked():
    v = one(TP, win([T("pytest", 0, "5 passed")], [], [], [], []))
    assert v.severity == "medium"


def test_background_only_is_low():
    v = one(TP, win([T("pytest -q", 0, "", bg=True)]))
    assert (v.severity, v.label) == ("low", "unverifiable")


def test_tests_ran_supported_even_if_failed():
    v = one(Claim(kind="tests_ran", text="I ran the tests."), win([T("pytest", 1, "1 failed")]))
    assert v.severity is None


def test_file_written_cases():
    c = Claim(kind="file_written", text="I wrote `config.yaml`.", target="config.yaml")
    assert one(c, win([W("/home/u/proj/config.yaml")])).severity is None
    v = one(c, win([W("/home/u/proj/config.yaml", ok=False)]))
    assert (v.severity, v.label) == ("high", "contradicted")
    v = one(c, win([W("/home/u/proj/other.yaml")]))
    assert (v.severity, v.evidence, v.phrase) == ("medium", "no write or patch receipt for config.yaml", "wrote config.yaml")
    v = one(c, win([T("cat > config.yaml <<EOF\nport: 1\nEOF")]))
    assert (v.severity, v.label) == ("low", "unverifiable")
    assert one(c, win([W("/x/myconfig.yaml")])).severity == "medium"  # suffix must be on a path boundary
    assert one(c, win([W("/x/config.yaml", tool="patch")])).severity is None


def test_file_written_no_target():
    c = Claim(kind="file_written", text="The config file is updated.")
    assert one(c, win([W("/x/a.toml")])).severity is None
    assert one(c, win([T("ls")])).severity == "medium"


def test_ran_command_target():
    c = Claim(kind="ran_command", text="I ran `make build`.", target="make build")
    assert one(c, win([T("cd app &&  make   build", 2)])).severity is None
    v = one(c, win([T("make test")]))
    assert (v.severity, v.evidence) == ("medium", "no command matching `make build` ran")


def test_deployed():
    c = Claim(kind="deployed", text="Deployed to staging.")
    assert one(c, win([T("kubectl apply -f k8s/")])).severity is None
    assert one(c, win([T("alembic upgrade head", 1)])).severity == "high"
    v = one(c, win([T("ls")]))
    assert (v.severity, v.evidence) == ("medium", "0 deploy or migration commands run")
    assert one(c, win([T("systemctl --user restart hermes-gateway")])).severity is None


def test_verified():
    c = Claim(kind="verified", text="I verified the endpoint returns 200.")
    assert one(c, win([T("curl -s localhost:8080/health")])).severity is None
    v = one(c, win([]))
    assert (v.severity, v.evidence) == ("medium", "0 tool calls")


def test_underway_and_done_are_current_turn_only():
    for kind in ("underway", "done"):
        c = Claim(kind=kind, text="Running it now.")
        v = one(c, win([T("pytest", 0, "1 passed")], []))
        assert (v.severity, v.evidence) == ("medium", "0 tool calls this turn"), kind
        assert one(c, win([T("sleep 1", 0, "", bg=True)])).severity is None


def test_vague_is_low():
    v = one(Claim(kind="vague", text="That's fixed.", low=True), win([]))
    assert (v.severity, v.label) == ("low", "unverifiable")


def test_order_preserved_and_never_raises():
    cs = [TP, Claim(kind="verified", text="x"), Claim(kind="nonsense-kind", text="?")]
    vs = match(cs, win([]))
    assert [v.claim for v in vs] == cs
    assert vs[2].severity == "low" and vs[2].evidence == "matcher error"
    assert match([], [(0, [])]) == []


def test_long_command_truncated_in_evidence():
    v = one(TP, win([T("pytest " + "x" * 200, 1)]))
    assert len(v.evidence) < 90 and "…" in v.evidence
