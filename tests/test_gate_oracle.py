"""Frozen acceptance oracle for the wired plugin (Stories 5, 6, 7). Orchestrator-owned: do not edit.

Drives the REAL registered callbacks, with the plugin imported as a package exactly as Hermes does.
"""
import json
import logging

import pytest

from tests.conftest import FakeCtx, load_plugin_package

S = "sess-1"


def boot(mode):
    pkg = load_plugin_package()
    ctx = FakeCtx({"mode": mode} if mode else {})
    pkg.register(ctx)
    assert set(ctx.hooks) == {"post_tool_call", "pre_llm_call", "transform_llm_output"}
    return pkg, ctx


def turn(ctx, turn_id, user="run the tests", session=S):
    return ctx.hooks["pre_llm_call"](session_id=session, turn_id=turn_id, user_message=user,
                                     conversation_history=[], is_first_turn=False, model="m", platform="cli")


def tool(ctx, turn_id, cmd, code=0, out="", session=S, name="terminal", args=None, result=None):
    if result is None:
        result = json.dumps({"output": out, "exit_code": code, "error": None})
    return ctx.hooks["post_tool_call"](tool_name=name, args=args or {"command": cmd}, result=result,
                                       session_id=session, turn_id=turn_id, tool_call_id="c", task_id="",
                                       api_request_id="", duration_ms=5, status=None, error_type=None,
                                       error_message=None, middleware_trace=[])


def reply(ctx, turn_id, text, session=S):
    return ctx.hooks["transform_llm_output"](response_text=text, session_id=session, model="m",
                                             platform="cli", turn_id=turn_id)


# ---- annotate mode (Story 5)

def test_annotate_holds_stream_observe_does_not():
    assert boot("annotate")[1].held["transform_llm_output"] is True
    assert boot("observe")[1].held["transform_llm_output"] is False
    assert boot(None)[1].held["transform_llm_output"] is False


def test_annotate_unbacked_tests_footer_exact():
    _, ctx = boot("annotate")
    assert turn(ctx, "t1") is None
    out = reply(ctx, "t1", "All tests pass.")
    assert out == "All tests pass.\n\n[say-do] Claimed: tests pass. Ledger: 0 test commands run."


def test_annotate_contradiction_footer_exact():
    _, ctx = boot("annotate")
    turn(ctx, "t1")
    tool(ctx, "t1", "pytest -q", 1, "3 failed, 14 passed")
    out = reply(ctx, "t1", "Fixed it. All tests pass.  \n")
    assert out == "Fixed it. All tests pass.\n\n[say-do] Claimed: tests pass. Ledger: `pytest -q` exited 1."


def test_annotate_backed_reply_unchanged():
    _, ctx = boot("annotate")
    turn(ctx, "t1")
    tool(ctx, "t1", "pytest -q", 0, "17 passed in 3.11s")
    assert reply(ctx, "t1", "All 17 tests pass.") is None


def test_annotate_footer_max_two_lines_high_first():
    _, ctx = boot("annotate")
    turn(ctx, "t1", "fix the build")
    tool(ctx, "t1", "alembic upgrade head", 1)
    out = reply(ctx, "t1", "I wrote `a.py`. All tests pass. Deployed to staging.")
    footer = out.split("\n\n", 1)[1].split("\n")
    assert footer == [
        "[say-do] Claimed: deployed. Ledger: `alembic upgrade head` exited 1.",
        "[say-do] Claimed: wrote a.py. Ledger: no write or patch receipt for a.py. (+1 more)",
    ]


def test_low_and_excluded_never_annotate():
    _, ctx = boot("annotate")
    turn(ctx, "t1")
    assert reply(ctx, "t1", "That's fixed.") is None
    assert reply(ctx, "t1", "If the tests pass, merge it.\n```\nAll tests pass\n```") is None
    assert reply(ctx, "t1", "") is None


def test_bare_done_after_action_request():
    _, ctx = boot("annotate")
    turn(ctx, "t1", "Run the migration on staging")
    assert reply(ctx, "t1", "Done.") == "Done.\n\n[say-do] Claimed: done. Ledger: 0 tool calls this turn."
    turn(ctx, "t2", "thanks, that's all")
    assert reply(ctx, "t2", "Done.") is None


def test_lookback_backs_recap_and_flags_stale_success():
    _, ctx = boot("annotate")
    turn(ctx, "t1")
    tool(ctx, "t1", "pytest -q", 0, "9 passed")
    turn(ctx, "t2", "summarize")
    assert reply(ctx, "t2", "Earlier today: all tests pass.") is None
    turn(ctx, "t3")
    tool(ctx, "t3", "pytest -q", 1, "1 failed")
    turn(ctx, "t4", "status?")
    out = reply(ctx, "t4", "All tests pass.")
    assert out.endswith("[say-do] Claimed: tests pass. Ledger: `pytest -q` exited 1 in turn -1.")


def test_sessions_do_not_back_each_other():
    _, ctx = boot("annotate")
    turn(ctx, "t1", session="A")
    tool(ctx, "t1", "pytest", 0, "3 passed", session="A")
    turn(ctx, "t1", session="B")
    assert reply(ctx, "t1", "All tests pass.", session="B").endswith("Ledger: 0 test commands run.")


# ---- observe mode + nudge (Story 6)

def test_observe_never_rewrites_and_nudges_once():
    _, ctx = boot("observe")
    turn(ctx, "t1")
    assert reply(ctx, "t1", "All tests pass.") is None
    nudge = turn(ctx, "t2", "ok")
    assert nudge == {"context": "[say-do gate] Your previous reply claimed: tests pass. Tool ledger: 0 test commands "
                                "run. Verify with a tool call and report the real result, or correct the claim."}
    assert turn(ctx, "t3", "ok") is None


def test_observe_multi_claim_nudge_and_low_no_nudge():
    _, ctx = boot("observe")
    turn(ctx, "t1")
    reply(ctx, "t1", "I wrote `a.py`. All tests pass.")
    ctx_text = turn(ctx, "t2")["context"]
    assert ctx_text.startswith("[say-do gate] Your previous reply made claims the tool ledger does not support: ")
    assert "wrote a.py (ledger: no write or patch receipt for a.py)" in ctx_text
    assert "tests pass (ledger: 0 test commands run)" in ctx_text
    reply(ctx, "t2", "That's fixed.")
    assert turn(ctx, "t3") is None


def test_observe_nudge_is_per_session():
    _, ctx = boot("observe")
    turn(ctx, "t1", session="A")
    reply(ctx, "t1", "All tests pass.", session="A")
    assert turn(ctx, "t2", session="B") is None
    assert turn(ctx, "t2", session="A") is not None


# ---- fail-open + privacy (Story 7)

@pytest.mark.parametrize("target", ["Ledger.record", "Ledger.window", "Ledger.begin_turn", "extract_claims", "match",
                                    "render_footer", "render_nudge"])
def test_injected_failures_fail_open(target, monkeypatch, caplog):
    pkg, ctx = boot("annotate")
    gate_mod = pkg.gate

    def boom(*a, **k):
        raise RuntimeError("SECRET-REPLY-TEXT should never be logged")

    if target.startswith("Ledger."):
        monkeypatch.setattr(gate_mod.Ledger, target.split(".")[1], boom)
    else:
        monkeypatch.setattr(gate_mod, target, boom)
    with caplog.at_level(logging.DEBUG):
        assert turn(ctx, "t1") is None
        assert tool(ctx, "t1", "pytest -q", 0, "1 passed") is None
        assert reply(ctx, "t1", "All tests pass. SECRET-REPLY-TEXT") is None
        assert turn(ctx, "t2") is None
    assert "SECRET-REPLY-TEXT" not in caplog.text


def test_logs_never_contain_reply_or_command_text(caplog):
    _, ctx = boot("observe")
    with caplog.at_level(logging.DEBUG):
        turn(ctx, "t1", "run the tests TOKEN-USER-123")
        tool(ctx, "t1", "deploy --token TOKEN-CMD-456", 1, "TOKEN-OUT-789")
        reply(ctx, "t1", "Deployed to staging. TOKEN-REPLY-000. All tests pass.")
        turn(ctx, "t2")
    for secret in ("TOKEN-USER-123", "TOKEN-CMD-456", "TOKEN-OUT-789", "TOKEN-REPLY-000"):
        assert secret not in caplog.text
    assert "say_do_gate" in caplog.text  # findings ARE logged, by kind/severity only


@pytest.mark.parametrize("payload", [
    {}, {"response_text": None}, {"response_text": 42, "turn_id": None, "session_id": None},
    {"response_text": "All tests pass.", "turn_id": "", "session_id": ""},
])
def test_odd_transform_payloads(payload):
    _, ctx = boot("annotate")
    out = ctx.hooks["transform_llm_output"](**payload)
    assert out is None or isinstance(out, str)


def test_fork_turn_not_backed_by_parent():
    _, ctx = boot("annotate")
    turn(ctx, "t1")
    tool(ctx, "t1", "pytest -q", 0, "3 passed")
    assert reply(ctx, "t1:side", "All tests pass.").endswith("Ledger: 0 test commands run.")
