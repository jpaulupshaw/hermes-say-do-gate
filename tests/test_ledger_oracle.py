"""Frozen acceptance oracle for ledger.Ledger (Story 2). Orchestrator-owned: do not edit."""
import json
import threading

from ledger import Ledger, Receipt


def _term(cmd, exit_code=0, output="ok", **kw):
    return dict(tool_name="terminal", args={"command": cmd}, result=json.dumps(
        {"output": output, "exit_code": exit_code, "error": None}), **kw)


def test_record_terminal_receipt_fields():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="t1", user_message="run tests")
    r = lg.record(**_term("pytest -q", 0, "17 passed"), session_id="s", turn_id="t1", duration_ms=12)
    assert isinstance(r, Receipt)
    assert (r.tool_name, r.status, r.exit_code, r.command, r.background) == ("terminal", "ok", 0, "pytest -q", False)
    assert r.output_tail == "17 passed" and r.duration_ms == 12 and r.seq >= 1


def test_nonzero_exit_overrides_ok_status():
    lg = Ledger()
    r = lg.record(**_term("pytest", 1, "3 failed"), session_id="s", turn_id="t1", status="ok")
    assert r.status == "error" and r.exit_code == 1


def test_error_result_and_explicit_status():
    lg = Ledger()
    r1 = lg.record(tool_name="write_file", args={"path": "/x/a.py"}, result={"error": "denied"},
                   session_id="s", turn_id="t1")
    r2 = lg.record(tool_name="patch", args={"path": "b.py"}, result="not json", session_id="s",
                   turn_id="t1", status="error", error_type="tool_error", error_message="E" * 900)
    assert r1.status == "error" and r1.files == ("/x/a.py",)
    assert r2.status == "error" and r2.files == ("b.py",) and len(r2.error_message) == 500


def test_files_prefers_files_modified_then_resolved_path():
    lg = Ledger()
    r1 = lg.record(tool_name="write_file", args={"path": "a.py"},
                   result={"files_modified": ["/abs/a.py"], "resolved_path": "/abs/a.py"}, session_id="s", turn_id="t")
    r2 = lg.record(tool_name="write_file", args={"path": "b.py"}, result={"resolved_path": "/abs/b.py"},
                   session_id="s", turn_id="t")
    r3 = lg.record(tool_name="read_file", args={"path": "c.py"}, result="text", session_id="s", turn_id="t")
    assert r1.files == ("/abs/a.py",) and r2.files == ("/abs/b.py",) and r3.files == ()


def test_background_flag_and_truncation():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args={"command": "x" * 5000, "background": True},
                  result={"output": "y" * 9000, "exit_code": 0}, session_id="s", turn_id="t")
    assert r.background is True and len(r.args["command"]) == 2000 and r.output_tail == "y" * 2000


def test_window_lookback_three_and_eviction():
    lg = Ledger(lookback_turns=3)
    for i in range(1, 6):
        lg.begin_turn(session_id="s", turn_id=f"t{i}", user_message=f"msg{i}")
        lg.record(**_term(f"cmd{i}"), session_id="s", turn_id=f"t{i}")
    w = lg.window(session_id="s", turn_id="t5")
    assert [age for age, _ in w] == [0, -1, -2, -3]
    assert [rs[0].command for _, rs in w] == ["cmd5", "cmd4", "cmd3", "cmd2"]
    assert lg.user_message(session_id="s", turn_id="t1") is None  # evicted
    assert lg.user_message(session_id="s", turn_id="t5") == "msg5"


def test_window_unknown_turn_and_short_history():
    lg = Ledger()
    assert lg.window(session_id="nope", turn_id="t1") == [(0, [])]
    lg.begin_turn(session_id="s", turn_id="t1")
    lg.begin_turn(session_id="s", turn_id="t2")
    lg.record(**_term("a"), session_id="s", turn_id="t1")
    w = lg.window(session_id="s", turn_id="t2")
    assert [age for age, _ in w] == [0, -1] and w[0][1] == [] and w[1][1][0].command == "a"


def test_empty_turn_id_falls_back_to_latest_main_turn():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="t1")
    lg.record(**_term("a"), session_id="s", turn_id="")
    assert lg.window(session_id="s", turn_id="t1")[0][1][0].command == "a"
    lg2 = Ledger()
    lg2.record(**_term("b"), session_id="s", turn_id="")
    assert lg2.window(session_id="s", turn_id="")[0][1][0].command == "b"


def test_fork_turns_never_merge():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="t1")
    lg.record(**_term("main"), session_id="s", turn_id="t1")
    lg.record(**_term("side"), session_id="s", turn_id="t1:side")
    main = lg.window(session_id="s", turn_id="t1")
    assert [r.command for _, rs in main for r in rs] == ["main"]
    side = lg.window(session_id="s", turn_id="t1:side")
    assert [age for age, _ in side] == [0] and [r.command for r in side[0][1]] == ["side"]
    lg.begin_turn(session_id="s", turn_id="t2")
    assert [age for age, _ in lg.window(session_id="s", turn_id="t2")] == [0, -1]


def test_sessions_isolated_and_lru_bounded():
    lg = Ledger(max_sessions=2)
    for s in ("a", "b"):
        lg.record(**_term(s), session_id=s, turn_id="t1")
    assert lg.window(session_id="a", turn_id="t1")[0][1][0].command == "a"
    lg.record(**_term("c"), session_id="c", turn_id="t1")  # evicts b (a was touched more recently)
    assert lg.window(session_id="b", turn_id="t1") == [(0, [])]
    assert lg.window(session_id="a", turn_id="t1")[0][1][0].command == "a"


def test_receipt_cap_per_turn():
    lg = Ledger(max_receipts_per_turn=5)
    for i in range(8):
        lg.record(**_term(f"c{i}"), session_id="s", turn_id="t1")
    assert [r.command for r in lg.window(session_id="s", turn_id="t1")[0][1]] == ["c3", "c4", "c5", "c6", "c7"]


def test_concurrent_sessions_get_only_their_receipts():
    lg = Ledger()

    def worker(sid):
        lg.begin_turn(session_id=sid, turn_id="t1")
        for i in range(200):
            lg.record(**_term(f"{sid}-{i}"), session_id=sid, turn_id="t1")

    threads = [threading.Thread(target=worker, args=(f"s{n}",)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    for n in range(8):
        rs = lg.window(session_id=f"s{n}", turn_id="t1")[0][1]
        assert len(rs) == 200 and all(r.command.startswith(f"s{n}-") for r in rs)
    seqs = [r.seq for n in range(8) for r in lg.window(session_id=f"s{n}", turn_id="t1")[0][1]]
    assert len(set(seqs)) == len(seqs)


def test_junk_inputs_do_not_raise():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args="not a dict", result=None, session_id="s", turn_id="t")
    assert r.args == {} and r.output_tail == "" and r.command is None
    lg.begin_turn(session_id="s", turn_id="t2", user_message={"multimodal": True})
    assert lg.user_message(session_id="s", turn_id="t2") is None
