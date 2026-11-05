"""Orchestrator review regressions for ledger.py (found in code review, not covered by the oracle)."""
from ledger import Ledger


def _t(cmd):
    return dict(tool_name="terminal", args={"command": cmd}, result={"output": "", "exit_code": 0})


def test_begin_turn_with_empty_or_fork_turn_id_does_not_raise():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="", user_message="hi")
    lg.begin_turn(session_id="s", turn_id="t1:side", user_message="side q")
    assert lg.user_message(session_id="s", turn_id="t1:side") == "side q"


def test_unknown_main_turn_still_sees_lookback():
    # A turn whose pre_llm_call never fired (e.g. persistence disabled) and that made no tool calls
    # must still be checked against the session's recent turns, without mutating the ledger.
    lg = Ledger(lookback_turns=3)
    for i in (1, 2):
        lg.begin_turn(session_id="s", turn_id=f"t{i}")
        lg.record(**_t(f"c{i}"), session_id="s", turn_id=f"t{i}")
    w = lg.window(session_id="s", turn_id="t9")
    assert [age for age, _ in w] == [0, -1, -2]
    assert w[0][1] == [] and w[1][1][0].command == "c2" and w[2][1][0].command == "c1"
    assert [age for age, _ in lg.window(session_id="s", turn_id="t2")] == [0, -1]  # t9 not inserted


def test_evicted_fork_drops_its_user_message():
    lg = Ledger()
    for i in range(10):
        lg.begin_turn(session_id="s", turn_id=f"t1:f{i}", user_message=f"m{i}")
        lg.record(**_t("x"), session_id="s", turn_id=f"t1:f{i}")
    assert lg.user_message(session_id="s", turn_id="t1:f0") is None
    assert lg.user_message(session_id="s", turn_id="t1:f9") == "m9"
