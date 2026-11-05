"""End-to-end through REAL upstream Hermes code (Story 7). Orchestrator-owned: do not edit.

Each case runs in a subprocess with a fresh HERMES_HOME: real plugin discovery, real lifecycle.invoke_hook for
post_tool_call / pre_llm_call (the same entry points model_tools.py and turn_context.py use), and the real
agent.turn_finalizer.apply_llm_output_transform held/unheld path with the agent attributes conversation_loop sets.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = Path((Path(os.environ["SAYDO_UPSTREAM"]) if "SAYDO_UPSTREAM" in os.environ else PLUGIN_ROOT.parent / "upstream"))

DRIVER = textwrap.dedent(r'''
    import json, sys
    from types import SimpleNamespace
    from hermes_cli.plugins import discover_plugins
    from hermes_cli.plugins_dispatch import held_stream_transforms
    from hermes_cli.lifecycle import invoke_hook
    from agent.turn_finalizer import apply_llm_output_transform, HELD_TRANSFORM_FAILED_REPLY
    discover_plugins(force=True)
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())  # file, not argv: one arg caps at 128 KB
    out = []
    for step in spec["steps"]:
        held = held_stream_transforms()
        agent = SimpleNamespace(session_id=spec["session"], model="m", platform="cli",
                                _held_transforms=held, _hold_stream=bool(held), _llm_output_transform=None)
        tid = step["turn_id"]
        invoke_hook("pre_llm_call", session_id=spec["session"], task_id="", turn_id=tid, user_message=step["user"],
                    conversation_history=[], is_first_turn=False, model="m", platform="cli",
                    parent_session_id="", sender_id="")
        for t in step.get("tools", []):
            invoke_hook("post_tool_call", tool_name=t["name"], args=t["args"], result=t["result"],
                        task_id="", session_id=spec["session"], tool_call_id="c1", turn_id=tid, api_request_id="",
                        duration_ms=3, status=None, error_type=None, error_message=None, middleware_trace=[])
        final, transformed, _ = apply_llm_output_transform(agent, step["reply"], turn_id=tid)
        out.append({"final": final, "transformed": transformed, "held": agent._hold_stream,
                    "withheld": final == HELD_TRANSFORM_FAILED_REPLY})
    print("E2E:" + json.dumps(out))
''')


def run(tmp_path, mode, steps, session="e2e"):
    home = tmp_path / "home"
    dest = home / "plugins" / "say-do-gate"
    dest.mkdir(parents=True)
    for f in ["plugin.yaml", *[p.name for p in PLUGIN_ROOT.glob("*.py")]]:
        shutil.copy2(PLUGIN_ROOT / f, dest / f)
    cfg = "plugins:\n  enabled:\n    - say-do-gate\n"
    if mode:
        cfg += f"  entries:\n    say-do-gate:\n      settings:\n        mode: {mode}\n"
    (home / "config.yaml").write_text(cfg)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["HERMES_HOME"] = str(home)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"session": session, "steps": steps}), encoding="utf-8")
    p = subprocess.run([sys.executable, "-c", DRIVER, str(spec)],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120)
    line = next((ln for ln in p.stdout.splitlines() if ln.startswith("E2E:")), None)
    assert line, f"driver failed rc={p.returncode}\nSTDERR:\n{p.stderr[-3000:]}"
    return json.loads(line[4:])


def term(cmd, code=0, out=""):
    return {"name": "terminal", "args": {"command": cmd},
            "result": json.dumps({"output": out, "exit_code": code, "error": None})}


def test_annotate_flags_unbacked_claim_through_real_finalizer(tmp_path):
    (r,) = run(tmp_path, "annotate", [{"turn_id": "t1", "user": "run the tests", "reply": "All tests pass."}])
    assert r["held"] is True and r["transformed"] is True and not r["withheld"]
    assert r["final"] == "All tests pass.\n\n[say-do] Claimed: tests pass. Ledger: 0 test commands run."


def test_annotate_backed_claim_passes_through_unchanged(tmp_path):
    (r,) = run(tmp_path, "annotate", [{"turn_id": "t1", "user": "run the tests", "reply": "All 9 tests pass.",
                                        "tools": [term("pytest -q", 0, "9 passed in 1.0s")]}])
    assert r["final"] == "All 9 tests pass." and r["transformed"] is False and not r["withheld"]


def test_annotate_contradiction_and_lookback_through_real_hooks(tmp_path):
    rs = run(tmp_path, "annotate", [
        {"turn_id": "t1", "user": "run the tests", "reply": "Ran them.", "tools": [term("pytest -q", 1, "2 failed")]},
        {"turn_id": "t2", "user": "and now?", "reply": "All tests pass."},
    ])
    assert rs[1]["final"].endswith("[say-do] Claimed: tests pass. Ledger: `pytest -q` exited 1 in turn -1.")


def test_observe_keeps_stream_and_reply(tmp_path):
    (r,) = run(tmp_path, None, [{"turn_id": "t1", "user": "deploy it", "reply": "Deployed to staging."}])
    assert r["held"] is False and r["transformed"] is False and r["final"] == "Deployed to staging."


@pytest.mark.parametrize("ending", [
    "All tests pass.",                                        # normal completion
    "All tests pass. I was partway through the next step",  # output-limit truncation mid-sentence
    "I reached the iteration limit. Tests pass so far.",    # iteration budget exhausted
    "Error: provider returned 529 overloaded",              # provider failure surfaced as text
    "[interrupted]",                                        # interruption marker
    "x" * 200_000,                                          # pathological size
])
def test_turn_endings_never_withhold_the_reply(tmp_path, ending):
    (r,) = run(tmp_path, "annotate", [{"turn_id": "t1", "user": "run the tests", "reply": ending}])
    assert not r["withheld"]
    assert r["final"].startswith(ending[:1000])
