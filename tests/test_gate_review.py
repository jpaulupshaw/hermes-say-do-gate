"""Orchestrator review regressions for gate.py / __init__.py (not covered by the frozen oracle)."""
import json
import logging

import gate as gate_mod
from claims import Claim
from gate import Gate
from matcher import Verdict


def test_unreliable_turns_are_bounded():
    g = Gate(mode="observe", max_pending=8)
    g.ledger.record = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    for i in range(500):
        g.on_post_tool_call(tool_name="terminal", args={}, result="{}", session_id=f"s{i}", turn_id="t1")
    assert len(g._unreliable_turns) <= 8


def test_unreliable_turn_still_skips_annotation_after_eviction_of_others():
    g = Gate(mode="annotate", max_pending=8)
    g.ledger.record = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    g.on_pre_llm_call(session_id="s", turn_id="t1", user_message="run the tests")
    g.on_post_tool_call(tool_name="terminal", args={"command": "pytest"}, result="{}", session_id="s", turn_id="t1")
    assert g.on_transform(response_text="All tests pass.", session_id="s", turn_id="t1") is None


def test_last_report_is_bounded():
    g = Gate(mode="observe", max_pending=8)
    for i in range(500):
        g.on_pre_llm_call(session_id=f"s{i}", turn_id="t1", user_message="hi")
        g.on_transform(response_text="All tests pass.", session_id=f"s{i}", turn_id="t1")
    assert len(g._last_report) <= 8


def test_finding_log_names_the_claim_kind(caplog, monkeypatch):
    c = Claim(kind="tests_pass", text="All tests pass.")
    monkeypatch.setattr(gate_mod, "extract_claims", lambda text, user=None: [c])
    monkeypatch.setattr(gate_mod, "match", lambda claims, window: [
        Verdict(claim=c, severity="high", label="contradicted", evidence="`pytest` exited 1", phrase="tests pass")])
    g = Gate(mode="observe")
    with caplog.at_level(logging.INFO, logger="say_do_gate"):
        g.on_transform(response_text="All tests pass.", session_id="s", turn_id="t1")
    finding = [r.getMessage() for r in caplog.records if "finding" in r.getMessage()]
    assert finding == ["say_do_gate finding kind=tests_pass severity=high label=contradicted"]


def test_gate_ledger_uses_three_turn_lookback():
    g = Gate(mode="observe")
    for n in range(1, 6):
        g.on_pre_llm_call(session_id="s", turn_id=f"t{n}", user_message="go")
        g.ledger.record(tool_name="terminal", args={"command": f"echo {n}"},
                        result=json.dumps({"output": "", "exit_code": 0}), session_id="s", turn_id=f"t{n}")
    ages = [age for age, _ in g.ledger.window(session_id="s", turn_id="t5")]
    assert sorted(ages, reverse=True) == [0, -1, -2, -3]


def test_register_failure_logs_type_not_message(caplog):
    from conftest import load_plugin_package
    pkg = load_plugin_package()

    class BadCtx:
        def get_config(self, *a, **k):
            raise RuntimeError("secret-token-abc123 in /home/someone/private")

    with caplog.at_level(logging.WARNING, logger="say_do_gate"):
        pkg.register(BadCtx())
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in text and "secret-token" not in text and "/home/someone" not in text
