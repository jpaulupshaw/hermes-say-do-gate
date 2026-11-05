"""Focused unit tests for ledger.Ledger edge cases not covered by the oracle."""
import json
import threading
import time

from ledger import Ledger, Receipt


def _term(cmd, exit_code=0, output="ok", **kw):
    return dict(tool_name="terminal", args={"command": cmd}, result=json.dumps(
        {"output": output, "exit_code": exit_code, "error": None}), **kw)


def test_seq_is_global_and_monotonic():
    lg = Ledger()
    r1 = lg.record(**_term("a"), session_id="s1", turn_id="t1")
    r2 = lg.record(**_term("b"), session_id="s2", turn_id="t2")
    r3 = lg.record(**_term("c"), session_id="s1", turn_id="t1")
    assert r1.seq < r2.seq < r3.seq


def test_result_json_string_parsed():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args={"command": "x"}, result='{"output":"hello","exit_code":0}',
                  session_id="s", turn_id="t")
    assert r.output_tail == "hello" and r.exit_code == 0


def test_result_unparseable_string_becomes_output_tail():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args={"command": "x"}, result="plain text result",
                  session_id="s", turn_id="t")
    assert r.output_tail == "plain text result"
    assert r.exit_code is None


def test_non_dict_args_become_empty_dict():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args="junk", result=None, session_id="s", turn_id="t")
    assert r.args == {}


def test_args_str_values_truncated_to_2000():
    lg = Ledger()
    long_val = "x" * 3000
    r = lg.record(tool_name="write_file", args={"path": long_val}, result=None,
                  session_id="s", turn_id="t")
    assert len(r.args["path"]) == 2000


def test_non_str_args_values_preserved():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args={"command": "ls", "timeout": 30}, result=None,
                  session_id="s", turn_id="t")
    assert r.args["timeout"] == 30


def test_output_tail_truncated_to_2000_chars():
    lg = Ledger()
    big = "z" * 5000
    r = lg.record(tool_name="terminal", args={"command": "x"}, result={"output": big, "exit_code": 0},
                  session_id="s", turn_id="t")
    assert len(r.output_tail) == 2000
    assert r.output_tail == "z" * 2000


def test_error_message_truncated_to_500():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args={"command": "x"}, result=None,
                  session_id="s", turn_id="t", status="error", error_message="e" * 800)
    assert len(r.error_message) == 500


def test_files_from_files_modified_list():
    lg = Ledger()
    r = lg.record(tool_name="write_file", args={"path": "a.py"},
                  result={"files_modified": ["/abs/a.py", "/abs/b.py"]},
                  session_id="s", turn_id="t")
    assert r.files == ("/abs/a.py", "/abs/b.py")


def test_files_from_resolved_path():
    lg = Ledger()
    r = lg.record(tool_name="read_file", args={"path": "c.py"}, result={"resolved_path": "/abs/c.py"},
                  session_id="s", turn_id="t")
    assert r.files == ("/abs/c.py",)


def test_files_for_write_file_and_patch():
    lg = Ledger()
    r1 = lg.record(tool_name="write_file", args={"path": "a.py"}, result=None,
                   session_id="s", turn_id="t")
    r2 = lg.record(tool_name="patch", args={"path": "b.py"}, result=None,
                   session_id="s", turn_id="t")
    assert r1.files == ("a.py",) and r2.files == ("b.py",)


def test_files_empty_for_other_tools():
    lg = Ledger()
    r = lg.record(tool_name="web_search", args={"query": "q"}, result=None,
                  session_id="s", turn_id="t")
    assert r.files == ()


def test_status_derived_from_error_dict_key():
    lg = Ledger()
    r = lg.record(tool_name="write_file", args={"path": "a.py"}, result={"error": "denied"},
                  session_id="s", turn_id="t")
    assert r.status == "error"


def test_status_ok_when_no_error_key():
    lg = Ledger()
    r = lg.record(tool_name="write_file", args={"path": "a.py"}, result={"error": None},
                  session_id="s", turn_id="t")
    assert r.status == "ok"


def test_status_explicit_ok():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args={"command": "x"}, result=None,
                  session_id="s", turn_id="t", status="ok")
    assert r.status == "ok"


def test_status_explicit_error():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args={"command": "x"}, result=None,
                  session_id="s", turn_id="t", status="error")
    assert r.status == "error"


def test_status_explicit_invalid_ignored():
    lg = Ledger()
    r = lg.record(tool_name="terminal", args={"command": "x"}, result={"error": "bad"},
                  session_id="s", turn_id="t", status="bogus")
    assert r.status == "error"  # derived from result


def test_fork_turn_kept_at_most_8():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="t1")
    for i in range(1, 12):
        lg.record(**_term(f"fork{i}"), session_id="s", turn_id=f"t1:f{i}")
    # Only last 8 fork keys should be kept: f4..f11
    for i in range(1, 4):
        assert lg.window(session_id="s", turn_id=f"t1:f{i}") == [(0, [])]
    for i in range(4, 12):
        w = lg.window(session_id="s", turn_id=f"t1:f{i}")
        assert w[0][1][0].command == f"fork{i}"


def test_fork_turn_not_in_main_window():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="t1")
    lg.record(**_term("main"), session_id="s", turn_id="t1")
    lg.record(**_term("side"), session_id="s", turn_id="t1:side")
    main_w = lg.window(session_id="s", turn_id="t1")
    all_main = [r.command for _, rs in main_w for r in rs]
    assert "side" not in all_main


def test_empty_turn_id_no_main_turns_uses_noturn():
    lg = Ledger()
    lg.record(**_term("orphan"), session_id="s", turn_id="")
    w = lg.window(session_id="s", turn_id="")
    assert len(w) == 1 and w[0][0] == 0
    assert w[0][1][0].command == "orphan"


def test_window_returns_new_lists_each_call():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="t1")
    lg.record(**_term("a"), session_id="s", turn_id="t1")
    w1 = lg.window(session_id="s", turn_id="t1")
    w2 = lg.window(session_id="s", turn_id="t1")
    assert w1 is not w2
    assert w1[0][1] is not w2[0][1]


def test_user_message_non_str_stored_as_none():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="t1", user_message=12345)
    assert lg.user_message(session_id="s", turn_id="t1") is None


def test_user_message_unknown_turn():
    lg = Ledger()
    assert lg.user_message(session_id="s", turn_id="unknown") is None


def test_user_message_unknown_session():
    lg = Ledger()
    assert lg.user_message(session_id="nope", turn_id="t1") is None


def test_max_sessions_lru_eviction():
    lg = Ledger(max_sessions=2)
    lg.record(**_term("a"), session_id="a", turn_id="t1")
    lg.record(**_term("b"), session_id="b", turn_id="t1")
    # Touch session a again
    lg.window(session_id="a", turn_id="t1")
    # Now add session c — should evict b
    lg.record(**_term("c"), session_id="c", turn_id="t1")
    assert lg.window(session_id="a", turn_id="t1")[0][1][0].command == "a"
    assert lg.window(session_id="b", turn_id="t1") == [(0, [])]
    assert lg.window(session_id="c", turn_id="t1")[0][1][0].command == "c"


def test_record_without_explicit_session_id_uses_empty_string():
    lg = Ledger()
    r = lg.record(**_term("x"))
    assert r.session_id == ""


def test_record_without_explicit_turn_id_uses_empty_string():
    lg = Ledger()
    r = lg.record(**_term("x"), session_id="s")
    assert r.session_id == "s"
    assert r.turn_key == "__noturn__"


def test_window_for_fork_with_unknown_session():
    lg = Ledger()
    w = lg.window(session_id="nope", turn_id="t1:side")
    assert w == [(0, [])]


def test_max_receipts_per_turn_drops_oldest():
    lg = Ledger(max_receipts_per_turn=3)
    for i in range(5):
        lg.record(**_term(f"c{i}"), session_id="s", turn_id="t1")
    w = lg.window(session_id="s", turn_id="t1")
    assert [r.command for r in w[0][1]] == ["c2", "c3", "c4"]


def test_begin_turn_does_not_duplicate_in_turn_order():
    lg = Ledger(lookback_turns=2)
    lg.begin_turn(session_id="s", turn_id="t1")
    lg.begin_turn(session_id="s", turn_id="t1")  # duplicate
    lg.begin_turn(session_id="s", turn_id="t2")
    w = lg.window(session_id="s", turn_id="t2")
    assert [age for age, _ in w] == [0, -1]


def test_lookback_zero():
    lg = Ledger(lookback_turns=0)
    lg.begin_turn(session_id="s", turn_id="t1")
    lg.record(**_term("a"), session_id="s", turn_id="t1")
    lg.begin_turn(session_id="s", turn_id="t2")
    lg.record(**_term("b"), session_id="s", turn_id="t2")
    w = lg.window(session_id="s", turn_id="t2")
    assert [age for age, _ in w] == [0]
    assert w[0][1][0].command == "b"


def test_thread_safe_concurrent_record_same_turn():
    lg = Ledger()
    lg.begin_turn(session_id="s", turn_id="t1")

    def worker(n):
        for i in range(50):
            lg.record(**_term(f"{n}-{i}"), session_id="s", turn_id="t1")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    w = lg.window(session_id="s", turn_id="t1")
    assert len(w[0][1]) == 200


def test_receipt_is_frozen():
    lg = Ledger()
    r = lg.record(**_term("x"), session_id="s", turn_id="t")
    assert isinstance(r, Receipt)
    try:
        r.seq = 999
        assert False, "Receipt should be frozen"
    except Exception:
        pass
