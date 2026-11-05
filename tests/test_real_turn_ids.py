"""Real-hook-context turn_id semantics (v0.22.7).

pre_llm_call fires BEFORE _bind_turn_identity mints the turn id: its context has
turn_id=None (build_plugin_hook_kwargs reads context.turn_id, which does not
exist there). post_tool_call / transform_llm_call carry the full id:
    f"{session_id}:{task_id}:{uuid8}"   (agent/turn_context.py::_bind_turn_identity)

So turn ids are globally unique (uuid), never counters — a nudge can never be
mis-popped or an old turn mistaken for the current one. The quirk this file
pins: begin_turn's empty turn_id must register a turn slot that the later
full-id receipts and transform resolve INTO, so lookback and user_message work.
"""
from __future__ import annotations

import pytest

from gate import Gate
from ledger import Ledger

SESSION = "sess-1"
T1 = f"{SESSION}:task-a:aaaa1111"
T2 = f"{SESSION}:task-b:bbbb2222"


def _tool(g: Gate, turn_id: str, command: str, output: str = "ok", exit_code: int = 0):
    g.on_post_tool_call(
        session_id=SESSION, turn_id=turn_id, tool_name="terminal",
        args={"command": command},
        result={"output": output, "exit_code": exit_code},
    )


class TestNudgeNotMisPopped:
    """The feared bug: nudge repeating / attributing new claims to old turns.
    Impossible with uuid turn ids; pinned here."""

    def test_nudge_fires_once_then_honest_reply_is_clean(self):
        g = Gate(mode="observe")
        # turn 1: claim without evidence
        g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="run the tests")
        g.on_transform(response_text="All tests pass.", session_id=SESSION, turn_id=T1)
        r2 = g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="ok")
        assert r2 and "tests pass" in r2["context"]

        # turn 2: honest claim backed by a real run — no nudge this time
        g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="run them again")
        _tool(g, T2, "pytest tests/", "386 passed")
        g.on_transform(response_text="All tests pass.", session_id=SESSION, turn_id=T2)
        r3 = g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="ok")
        assert r3 is None or "say-do" not in str(r3)

    def test_old_claims_never_attributed_to_new_turn(self):
        g = Gate(mode="observe")
        g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="migrate it")
        g.on_transform(response_text="Migrated.", session_id=SESSION, turn_id=T1)
        # turn 2 makes NO claim
        g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="now what")
        g.on_transform(response_text="Nothing more to do.", session_id=SESSION, turn_id=T2)
        r3 = g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="ok")
        assert r3 is None


class TestLookbackWithRealIds:
    """begin_turn(None) + full-id receipts: lookback must still see prior turns."""

    def test_prior_turn_run_backs_current_recap(self):
        g = Gate(mode="annotate")
        # turn 1: real test run, honest recap next turn
        g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="run tests")
        _tool(g, T1, "pytest tests/", "386 passed")
        g.on_transform(response_text="All tests pass.", session_id=SESSION, turn_id=T1)
        # turn 2: recap of turn 1's run — must NOT be flagged
        g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="remind me: tests?")
        out = g.on_transform(response_text="All tests pass.", session_id=SESSION, turn_id=T2)
        assert out is None  # backed by turn-1 receipt via lookback

    def test_contradicted_prior_run_still_high_in_recap(self):
        g = Gate(mode="annotate")
        g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="run tests")
        _tool(g, T1, "pytest tests/", "1 failed", exit_code=1)
        g.on_transform(response_text="One test failed.", session_id=SESSION, turn_id=T1)
        g.on_pre_llm_call(session_id=SESSION, turn_id=None, user_message="and now?")
        out = g.on_transform(response_text="All tests pass.", session_id=SESSION, turn_id=T2)
        assert out is not None and "[say-do]" in out


class TestUserMessageResolvesWithRealIds:
    """claims.py's 'done' claim depends on user_message being retrievable when
    transform carries the full id (pre_llm_call fires with turn_id=None)."""

    def test_done_claim_when_user_message_resolves(self):
        g = Gate(mode="annotate")
        g.on_pre_llm_call(
            session_id=SESSION, turn_id=None,
            user_message="Run the migration and tell me when it is done.",
        )
        # user_message resolved from the open slot -> the whole-reply "done"
        # claim is detected (kind=done), not missed.
        out = g.on_transform(response_text="Done.", session_id=SESSION, turn_id=T1)
        assert out is not None and "[say-do]" in out


class TestLedgerUnitRealIds:
    def test_empty_begin_then_full_id_same_slot(self):
        led = Ledger()
        led.begin_turn(session_id=SESSION, turn_id="", user_message="hi")
        led.record(tool_name="terminal", session_id=SESSION, turn_id=T1,
                   args={"command": "pytest"}, result={"output": "3 passed", "exit_code": 0})
        win = led.window(session_id=SESSION, turn_id=T1)
        assert any(r.command == "pytest" for _, rs in win for r in rs)
        assert led.user_message(session_id=SESSION, turn_id=T1) == "hi"

    def test_distinct_full_ids_do_not_share_slots(self):
        led = Ledger()
        led.begin_turn(session_id=SESSION, turn_id="")
        led.record(tool_name="terminal", session_id=SESSION, turn_id=T1,
                   args={"command": "pytest"}, result={"output": "1 failed", "exit_code": 1})
        led.begin_turn(session_id=SESSION, turn_id="")
        led.record(tool_name="terminal", session_id=SESSION, turn_id=T2,
                   args={"command": "echo hi"}, result={"output": "hi", "exit_code": 0})
        w2 = led.window(session_id=SESSION, turn_id=T2)
        age0 = [r for age, rs in w2 if age == 0 for r in rs]
        assert [r.command for r in age0] == ["echo hi"]
